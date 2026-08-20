"""
MarkPaper - on-demand PDF generation web service.

This service reproduces the same Markdown -> Pandoc -> LaTeX -> PDF pipeline that
`devops.sh pdf` runs *inside* the pandocker container. The key difference is that
here the container itself is the runtime (as on Render), so we invoke the
`tools/*.sh` helpers and `pandoc`/`xelatex` directly instead of orchestrating
Docker from the outside.

Pipeline per request (mirrors build_pdf() in devops.sh):
    1. tools/process-mermaid.sh   (render Mermaid code blocks -> PNG; optional)
    2. tools/detect-fonts.sh      (pick an available CJK font)
    3. tools/replace-fonts.sh     (swap macOS font names for container fonts)
    4. pandoc ... --filter pandoc-crossref --citeproc --csl=...  -> paper.tex
    5. tools/fix-latex-csl.sh     (patch CSL/CJK preamble)
    6. xelatex (twice)            -> paper.pdf

Security note: this endpoint compiles arbitrary user-supplied Markdown/LaTeX.
See SECURITY CONSIDERATIONS below and the README. Set API_TOKEN in production.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

# --------------------------------------------------------------------------- #
# Configuration (all overridable via environment variables)
# --------------------------------------------------------------------------- #

# Directory holding the repo resources (tools/, chicago-author-date.csl,
# references.bib, paper.md). In the Docker image this is where the repo is
# copied (WORKDIR). Falls back to the parent of this file for local runs.
APP_ROOT = Path(os.environ.get("MARKPAPER_ROOT", Path(__file__).resolve().parent.parent))

TOOLS_DIR = APP_ROOT / "tools"
CSL_FILE = APP_ROOT / "chicago-author-date.csl"
DEFAULT_BIB = APP_ROOT / "references.bib"
EXAMPLE_PAPER = APP_ROOT / "paper.md"

# Behaviour knobs
BUILD_TIMEOUT = int(os.environ.get("BUILD_TIMEOUT", "240"))          # seconds per build
MAX_INPUT_BYTES = int(os.environ.get("MAX_INPUT_BYTES", str(2 * 1024 * 1024)))  # 2 MiB
MAX_CONCURRENCY = int(os.environ.get("MAX_CONCURRENCY", "1"))         # simultaneous builds
ENABLE_MERMAID = os.environ.get("ENABLE_MERMAID", "true").lower() not in ("0", "false", "no")
API_TOKEN = os.environ.get("API_TOKEN", "").strip()                  # optional bearer token

# A semaphore protects small instances (LaTeX + Chromium are memory hungry).
_build_semaphore = asyncio.Semaphore(max(1, MAX_CONCURRENCY))

app = FastAPI(title="MarkPaper PDF Service", version="1.0.0")


# --------------------------------------------------------------------------- #
# Build pipeline
# --------------------------------------------------------------------------- #

class BuildError(RuntimeError):
    """Raised when the PDF build fails; carries a log tail for diagnostics."""

    def __init__(self, message: str, log: str = ""):
        super().__init__(message)
        self.log = log


def _run(cmd: list[str], cwd: Path, env: dict, timeout: int) -> subprocess.CompletedProcess:
    """Run a subprocess, capturing output. Never raises on non-zero exit."""
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _tail(text: str, lines: int = 40) -> str:
    return "\n".join((text or "").splitlines()[-lines:])


def build_pdf(markdown: str, bib: Optional[str] = None) -> bytes:
    """
    Compile `markdown` into PDF bytes using the MarkPaper pipeline.

    Runs entirely inside a throwaway working directory so concurrent requests
    never clobber each other. Reuses the repo's tools/ scripts and CSL file.
    """
    if not markdown.strip():
        raise BuildError("Empty Markdown input.")

    workdir = Path(tempfile.mkdtemp(prefix=f"markpaper-{uuid.uuid4().hex[:8]}-"))
    try:
        # ---- Stage the working directory --------------------------------- #
        (workdir / "paper.md").write_text(markdown, encoding="utf-8")
        (workdir / "images").mkdir(exist_ok=True)

        # tools/ scripts are referenced as `bash tools/...`; symlink (or copy) them in.
        try:
            (workdir / "tools").symlink_to(TOOLS_DIR)
        except OSError:
            shutil.copytree(TOOLS_DIR, workdir / "tools")

        if CSL_FILE.exists():
            shutil.copy2(CSL_FILE, workdir / CSL_FILE.name)

        # Bibliography: user-supplied wins, otherwise fall back to the repo's.
        if bib and bib.strip():
            (workdir / "references.bib").write_text(bib, encoding="utf-8")
        elif DEFAULT_BIB.exists():
            shutil.copy2(DEFAULT_BIB, workdir / "references.bib")

        # ---- Environment ------------------------------------------------- #
        env = os.environ.copy()
        # Harden xelatex: restrict file reads/writes to the working tree and
        # forbid shell-escape (\write18). Reduces the blast radius of hostile
        # LaTeX injected via a YAML header-includes block.
        env["openin_any"] = "p"    # paranoid: no absolute/parent-dir reads
        env["openout_any"] = "p"
        env["shell_escape"] = "f"
        env["TEXMFVAR"] = str(workdir / ".texmf-var")

        # ---- 1. Mermaid diagrams ---------------------------------------- #
        src_for_fonts = "paper.md"
        if ENABLE_MERMAID:
            r = _run(
                ["bash", "tools/process-mermaid.sh", "paper.md", "paper.mermaid.tmp.md", "images"],
                workdir, env, BUILD_TIMEOUT,
            )
            if (workdir / "paper.mermaid.tmp.md").exists():
                src_for_fonts = "paper.mermaid.tmp.md"
            else:
                # Mermaid step failed to produce output; continue with raw markdown.
                shutil.copy2(workdir / "paper.md", workdir / "paper.mermaid.tmp.md")
                src_for_fonts = "paper.mermaid.tmp.md"
        else:
            shutil.copy2(workdir / "paper.md", workdir / "paper.mermaid.tmp.md")
            src_for_fonts = "paper.mermaid.tmp.md"

        # ---- 2. Detect an available CJK font ---------------------------- #
        cjk_font = "AR PL UMing TW"
        r = _run(["bash", "tools/detect-fonts.sh"], workdir, env, 60)
        for line in (r.stdout or "").splitlines():
            if line.startswith("CJK_FONT_TC="):
                value = line.split("=", 1)[1].strip()
                if value:
                    cjk_font = value

        # ---- 3. Replace macOS font names -------------------------------- #
        r = _run(
            ["bash", "tools/replace-fonts.sh", src_for_fonts, "paper.tmp.md",
             "PingFang SC", cjk_font, "PingFang TC", cjk_font],
            workdir, env, 60,
        )
        if not (workdir / "paper.tmp.md").exists():
            raise BuildError("Font replacement step failed.", _tail(r.stderr))

        # ---- 4. Pandoc -> LaTeX ----------------------------------------- #
        r = _run(
            ["pandoc", "paper.tmp.md", "--standalone",
             "--filter", "pandoc-crossref", "--citeproc",
             "--csl=chicago-author-date.csl",
             "-M", "title=", "-M", "author=", "-M", "date=",
             "-o", "paper.tex"],
            workdir, env, BUILD_TIMEOUT,
        )
        if r.returncode != 0 or not (workdir / "paper.tex").exists():
            raise BuildError("Pandoc conversion failed.", _tail(r.stderr or r.stdout))

        # ---- 5. Patch CSL / CJK preamble -------------------------------- #
        _run(["bash", "tools/fix-latex-csl.sh", "paper.tex"], workdir, env, 60)

        # ---- 6. XeLaTeX (two passes for references/TOC) ----------------- #
        for _ in range(2):
            r = _run(
                ["xelatex", "-interaction=nonstopmode", "-no-shell-escape", "paper.tex"],
                workdir, env, BUILD_TIMEOUT,
            )

        pdf_path = workdir / "paper.pdf"
        if not pdf_path.exists():
            log = ""
            log_file = workdir / "paper.log"
            if log_file.exists():
                log = _tail(log_file.read_text(encoding="utf-8", errors="replace"), 50)
            else:
                log = _tail(r.stdout or r.stderr)
            raise BuildError("XeLaTeX failed to produce a PDF.", log)

        return pdf_path.read_bytes()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# HTTP layer
# --------------------------------------------------------------------------- #

def _check_auth(request: Request) -> None:
    """Enforce a bearer token if API_TOKEN is configured. No-op otherwise."""
    if not API_TOKEN:
        return
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if token != API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing API token.")


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/api/example", response_class=PlainTextResponse)
async def api_example() -> PlainTextResponse:
    if EXAMPLE_PAPER.exists():
        return PlainTextResponse(EXAMPLE_PAPER.read_text(encoding="utf-8"))
    return PlainTextResponse(_STARTER_MARKDOWN)


@app.post("/api/pdf")
async def api_pdf(
    request: Request,
    markdown: Optional[str] = Form(default=None),
    bib: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = None,
) -> Response:
    _check_auth(request)

    # Accept markdown from a form field, an uploaded file, or a raw JSON body.
    content = markdown
    if content is None and file is not None:
        content = (await file.read()).decode("utf-8", errors="replace")
    if content is None:
        ctype = request.headers.get("content-type", "")
        if "application/json" in ctype:
            payload = await request.json()
            content = payload.get("markdown")
            bib = bib or payload.get("bib")
        else:
            body = (await request.body()).decode("utf-8", errors="replace")
            content = body or None

    if not content or not content.strip():
        raise HTTPException(status_code=400, detail="No Markdown provided.")

    if len(content.encode("utf-8")) > MAX_INPUT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Input exceeds {MAX_INPUT_BYTES} bytes.",
        )

    async with _build_semaphore:
        try:
            pdf_bytes = await asyncio.to_thread(build_pdf, content, bib)
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="Build timed out.")
        except BuildError as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": str(exc), "log": exc.log},
            )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="paper.pdf"'},
    )


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(_INDEX_HTML)


# --------------------------------------------------------------------------- #
# Static content
# --------------------------------------------------------------------------- #

_STARTER_MARKDOWN = """\
---
title: Hello, MarkPaper
author: Anonymous
---

# Introduction

This PDF was generated on demand from **Markdown** via Pandoc and XeLaTeX.

- Plain text in, typeset PDF out.
- Edit the Markdown on the left, then click *Generate PDF*.
"""

_INDEX_HTML = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>MarkPaper - Markdown to PDF</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
  header { padding: 12px 16px; border-bottom: 1px solid #8883; display: flex;
           align-items: center; gap: 12px; flex-wrap: wrap; }
  header h1 { font-size: 16px; margin: 0; font-weight: 600; }
  header .spacer { flex: 1; }
  button { font: inherit; padding: 7px 14px; border-radius: 6px; border: 1px solid #8886;
           background: #2563eb; color: #fff; cursor: pointer; }
  button.secondary { background: transparent; color: inherit; }
  button:disabled { opacity: .5; cursor: default; }
  main { display: grid; grid-template-columns: 1fr 1fr; gap: 0;
         height: calc(100vh - 54px); }
  textarea { width: 100%; height: 100%; border: 0; border-right: 1px solid #8883;
             padding: 14px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
             font-size: 13px; line-height: 1.5; resize: none; outline: none;
             background: transparent; color: inherit; }
  .preview { position: relative; background: #6b72801a; }
  iframe { width: 100%; height: 100%; border: 0; }
  .status { position: absolute; inset: 0; display: flex; align-items: center;
            justify-content: center; padding: 24px; text-align: center; }
  pre { white-space: pre-wrap; word-break: break-word; font-size: 12px;
        max-width: 100%; max-height: 100%; overflow: auto; text-align: left;
        color: #b91c1c; }
  @media (max-width: 800px) { main { grid-template-columns: 1fr; grid-template-rows: 1fr 1fr; } }
</style>
</head>
<body>
<header>
  <h1>MarkPaper &middot; Markdown &rarr; PDF</h1>
  <div class="spacer"></div>
  <button class="secondary" id="loadExample" type="button">Load example paper</button>
  <button class="secondary" id="download" type="button" disabled>Download PDF</button>
  <button id="generate" type="button">Generate PDF</button>
</header>
<main>
  <textarea id="src" spellcheck="false" placeholder="Paste Markdown here..."></textarea>
  <div class="preview">
    <iframe id="frame" title="PDF preview"></iframe>
    <div class="status" id="status">Click <strong>&nbsp;Generate PDF&nbsp;</strong> to render.</div>
  </div>
</main>
<script>
  const $ = (id) => document.getElementById(id);
  const src = $("src"), frame = $("frame"), status = $("status");
  const btnGen = $("generate"), btnEx = $("loadExample"), btnDl = $("download");
  let lastUrl = null;

  const starter = `---\\ntitle: Hello, MarkPaper\\nauthor: Anonymous\\n---\\n\\n# Introduction\\n\\nThis PDF was generated on demand from **Markdown**.\\n`;
  src.value = starter.replace(/\\\\n/g, "\\n");

  function setStatus(html, isError) {
    status.style.display = "flex";
    status.innerHTML = isError ? '<pre></pre>' : html;
    if (isError) status.querySelector("pre").textContent = html;
    frame.style.display = "none";
  }
  function showPdf(url) {
    if (lastUrl) URL.revokeObjectURL(lastUrl);
    lastUrl = url;
    frame.src = url;
    frame.style.display = "block";
    status.style.display = "none";
    btnDl.disabled = false;
  }

  btnEx.addEventListener("click", async () => {
    setStatus("Loading example&hellip;");
    try {
      const r = await fetch("/api/example");
      src.value = await r.text();
      setStatus("Example loaded. Click <strong>&nbsp;Generate PDF&nbsp;</strong>.");
    } catch (e) { setStatus(String(e), true); }
  });

  btnDl.addEventListener("click", () => {
    if (!lastUrl) return;
    const a = document.createElement("a");
    a.href = lastUrl; a.download = "paper.pdf"; a.click();
  });

  btnGen.addEventListener("click", async () => {
    btnGen.disabled = true; btnDl.disabled = true;
    setStatus("Rendering&hellip; (LaTeX builds can take a while)");
    try {
      const form = new FormData();
      form.append("markdown", src.value);
      const r = await fetch("/api/pdf", { method: "POST", body: form });
      if (!r.ok) {
        let msg = "HTTP " + r.status;
        try {
          const j = await r.json();
          if (j.detail && j.detail.log) msg = j.detail.error + "\\n\\n" + j.detail.log;
          else if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
        } catch (_) {}
        setStatus(msg, true);
        return;
      }
      showPdf(URL.createObjectURL(await r.blob()));
    } catch (e) {
      setStatus(String(e), true);
    } finally {
      btnGen.disabled = false;
    }
  });
</script>
</body>
</html>
"""
