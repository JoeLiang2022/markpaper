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
import contextlib
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
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
# Address-space cap for xelatex/mmdc, in MiB. Sized to leave room for the web
# process inside a 512 MB instance. 0 disables the cap.
MEM_LIMIT_MB = int(os.environ.get("MEM_LIMIT_MB", "380"))
API_TOKEN = os.environ.get("API_TOKEN", "").strip()                  # optional bearer token

# Job queue knobs
# Finished PDFs are held in RAM, and the free tier only has 512 MB, so retain
# few results for a short time. Raise these if you run on a bigger instance.
JOB_TTL = int(os.environ.get("JOB_TTL", "300"))            # keep finished results (s)
MAX_QUEUE = int(os.environ.get("MAX_QUEUE", "50"))          # reject submissions beyond this
MAX_STORED_RESULTS = int(os.environ.get("MAX_STORED_RESULTS", "5"))   # retained PDFs
SYNC_WAIT_TIMEOUT = int(os.environ.get("SYNC_WAIT_TIMEOUT", "600"))   # /api/pdf max wait


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


def _run_capped(cmd: list[str], cwd: Path, env: dict, timeout: int) -> subprocess.CompletedProcess:
    """
    Run a memory-hungry child under an address-space limit.

    Without this, a runaway xelatex (or Chromium) pushes the container past its
    memory limit and the platform kills/restarts the *whole service* - which
    users experience as an HTTP 502 and a lost job. Capping the child means it
    is the child that dies, so the request fails with a real error message and
    the server keeps serving. Set MEM_LIMIT_MB=0 to disable.
    """
    if MEM_LIMIT_MB > 0 and os.name == "posix":
        inner = " ".join(shlex.quote(part) for part in cmd)
        wrapped = ["bash", "-c",
                   f"ulimit -v {MEM_LIMIT_MB * 1024} 2>/dev/null || true; exec {inner}"]
        return _run(wrapped, cwd, env, timeout)
    return _run(cmd, cwd, env, timeout)


def _tail(text: str, lines: int = 40) -> str:
    return "\n".join((text or "").splitlines()[-lines:])


def _scan_log(log: str) -> list[str]:
    """
    Pull problems out of a XeLaTeX log that did not stop the build.

    Because we run with -interaction=nonstopmode, LaTeX produces a PDF even
    after errors. The nastiest case is a missing xeCJK or CJK font: the PDF
    looks fine but every Chinese character is gone. Callers surface these so a
    silently broken document is visible instead of merely wrong.
    """
    warnings: list[str] = []

    missing_chars = re.findall(r"Missing character: There is no (\S+)", log)
    if missing_chars:
        sample = " ".join(sorted(set(missing_chars))[:12])
        warnings.append(
            f"{len(missing_chars)} character(s) had no glyph in the selected "
            f"font and were dropped from the PDF (e.g. {sample}). "
            "If this is Chinese/CJK text, the CJK font or xeCJK is unavailable."
        )

    for missing_file in sorted(set(re.findall(r"File `([^']+)' not found", log))):
        warnings.append(f"LaTeX could not find: {missing_file}")

    for bad_font in sorted(set(re.findall(r"The font \"([^\"]+)\" cannot be found", log))):
        warnings.append(f"Font not found: {bad_font}")

    if "Package xeCJK Error" in log:
        warnings.append("xeCJK reported an error; CJK text may be missing.")

    return warnings


def build_pdf(markdown: str, bib: Optional[str] = None) -> tuple[bytes, list[str]]:
    """
    Compile `markdown` into a PDF using the MarkPaper pipeline.

    Returns the PDF bytes plus any non-fatal problems found in the XeLaTeX log
    (see _scan_log). Runs entirely inside a throwaway working directory so
    concurrent requests never clobber each other. Reuses the repo's tools/
    scripts and CSL file.
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
        # Only invoke the Mermaid step when the document actually contains a
        # mermaid block. mmdc launches headless Chromium, which alone can
        # exhaust a 512 MB instance and take the whole process down (OOM kill
        # surfaces as a 502), so never pay that cost speculatively.
        has_mermaid = "```mermaid" in markdown
        src_for_fonts = "paper.md"
        if ENABLE_MERMAID and has_mermaid:
            r = _run_capped(
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
        # paper.md configures CJK itself (YAML CJKmainfont + \usepackage{xeCJK}).
        # Arbitrary pasted Markdown does not, so Chinese would come out as
        # missing glyphs. Inject the detected CJK font when the document has no
        # CJK setup of its own; pandoc's template then loads xeCJK for us.
        # (Guarded by \ifXeTeX in the template, and we compile with xelatex.)
        source_text = (workdir / "paper.tmp.md").read_text(encoding="utf-8",
                                                           errors="replace")
        declares_cjk = any(marker in source_text for marker in
                           ("CJKmainfont", "xeCJK", "setCJKmainfont"))

        pandoc_cmd = ["pandoc", "paper.tmp.md", "--standalone",
                      "--filter", "pandoc-crossref", "--citeproc",
                      "--csl=chicago-author-date.csl",
                      "-M", "title=", "-M", "author=", "-M", "date="]
        if not declares_cjk:
            # Write the preamble ourselves rather than relying on the template's
            # optional CJKmainfont variable: --include-in-header is additive and
            # behaves the same across pandoc versions and templates.
            (workdir / "cjk-header.tex").write_text(
                "\\usepackage{xeCJK}\n"
                f"\\setCJKmainfont{{{cjk_font}}}\n"
                "\\xeCJKsetup{AutoFakeBold=true, AutoFakeSlant=true}\n",
                encoding="utf-8",
            )
            pandoc_cmd += ["--include-in-header=cjk-header.tex"]
        pandoc_cmd += ["-o", "paper.tex"]

        r = _run(pandoc_cmd, workdir, env, BUILD_TIMEOUT)
        if r.returncode != 0 or not (workdir / "paper.tex").exists():
            raise BuildError("Pandoc conversion failed.", _tail(r.stderr or r.stdout))

        # ---- 5. Patch CSL / CJK preamble -------------------------------- #
        _run(["bash", "tools/fix-latex-csl.sh", "paper.tex"], workdir, env, 60)

        # ---- 6. XeLaTeX (two passes for references/TOC) ----------------- #
        for _ in range(2):
            r = _run_capped(
                ["xelatex", "-interaction=nonstopmode", "-no-shell-escape", "paper.tex"],
                workdir, env, BUILD_TIMEOUT,
            )

        log_text = ""
        log_file = workdir / "paper.log"
        if log_file.exists():
            log_text = log_file.read_text(encoding="utf-8", errors="replace")

        pdf_path = workdir / "paper.pdf"
        if not pdf_path.exists():
            detail = _tail(log_text, 50) or _tail(r.stdout or r.stderr)
            # A child killed by the address-space cap (or the kernel) leaves no
            # useful LaTeX error, so say so rather than showing an empty log.
            killed = r.returncode is not None and r.returncode < 0
            if killed or not detail.strip():
                detail = (
                    (detail + "\n\n" if detail.strip() else "")
                    + f"xelatex exited abnormally (code {r.returncode}). This is "
                      f"usually the {MEM_LIMIT_MB} MiB memory cap being hit. Try a "
                      "smaller document, or raise MEM_LIMIT_MB on a larger instance."
                )
            raise BuildError("XeLaTeX failed to produce a PDF.", detail)

        # nonstopmode means LaTeX happily emits a PDF even after real errors
        # (a missing xeCJK, for instance, silently drops every CJK glyph), so
        # inspect the log and report anything that corrupts the output.
        return pdf_path.read_bytes(), _scan_log(log_text)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Job queue
# --------------------------------------------------------------------------- #
#
# Builds are slow (LaTeX runs twice) and memory-hungry, so at most
# MAX_CONCURRENCY run at once. Rather than making callers hold an HTTP
# connection open for the whole wait, submissions become jobs: the client gets
# an id immediately and polls for status, including its position in the queue.
#
# Everything lives in memory in a single process. That is deliberate for a
# single free-tier instance: no Redis, no database. It also means jobs do not
# survive a restart and would not be shared across multiple instances.

@dataclass
class Job:
    id: str
    markdown: str
    bib: Optional[str] = None
    status: str = "queued"          # queued | running | done | error | cancelled
    submitted_at: float = field(default_factory=time.monotonic)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    pdf: Optional[bytes] = None
    error: Optional[str] = None
    log: str = ""
    warnings: list[str] = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)


_jobs: dict[str, Job] = {}
_job_order: list[str] = []                      # submission order, for queue position
_queue: Optional[asyncio.Queue] = None          # created in the lifespan
_tasks: list[asyncio.Task] = []

ACTIVE_STATES = ("queued", "running")


def _queued_count() -> int:
    return sum(1 for jid in _job_order
               if (j := _jobs.get(jid)) is not None and j.status == "queued")


def _running_count() -> int:
    return sum(1 for jid in _job_order
               if (j := _jobs.get(jid)) is not None and j.status == "running")


def _queue_position(job_id: str) -> Optional[int]:
    """1-based position among still-queued jobs, or None if not queued."""
    position = 0
    for jid in _job_order:
        job = _jobs.get(jid)
        if job is None or job.status != "queued":
            continue
        position += 1
        if jid == job_id:
            return position
    return None


def _drop(job_id: str) -> None:
    _jobs.pop(job_id, None)
    with contextlib.suppress(ValueError):
        _job_order.remove(job_id)


def _evict_old_results() -> None:
    """Cap retained PDFs so a busy day cannot exhaust a 512 MB instance."""
    finished = [jid for jid in _job_order
                if (j := _jobs.get(jid)) is not None and j.pdf is not None]
    for jid in finished[:max(0, len(finished) - MAX_STORED_RESULTS)]:
        _drop(jid)


async def _worker() -> None:
    """Pull jobs off the queue and build them, one at a time per worker."""
    assert _queue is not None
    while True:
        job_id = await _queue.get()
        try:
            job = _jobs.get(job_id)
            if job is None or job.status == "cancelled":
                continue
            job.status = "running"
            job.started_at = time.monotonic()
            try:
                # build_pdf is blocking; run it off the event loop so status
                # polling and health checks stay responsive during a build.
                job.pdf, job.warnings = await asyncio.to_thread(
                    build_pdf, job.markdown, job.bib)
                job.status = "done"
            except BuildError as exc:
                job.status, job.error, job.log = "error", str(exc), exc.log
            except subprocess.TimeoutExpired:
                job.status, job.error = "error", "Build timed out."
            except Exception as exc:                      # noqa: BLE001
                job.status, job.error = "error", f"Unexpected error: {exc}"
            finally:
                job.finished_at = time.monotonic()
                job.markdown = ""        # release the source text
                job.done.set()
                _evict_old_results()
        finally:
            _queue.task_done()


async def _reaper() -> None:
    """Expire finished jobs so results (and their PDFs) do not leak memory."""
    while True:
        await asyncio.sleep(30)
        now = time.monotonic()
        for job_id in list(_job_order):
            job = _jobs.get(job_id)
            if job and job.finished_at and (now - job.finished_at) > JOB_TTL:
                _drop(job_id)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _queue
    _queue = asyncio.Queue()
    for _ in range(max(1, MAX_CONCURRENCY)):
        _tasks.append(asyncio.create_task(_worker()))
    _tasks.append(asyncio.create_task(_reaper()))
    try:
        yield
    finally:
        for task in _tasks:
            task.cancel()
        await asyncio.gather(*_tasks, return_exceptions=True)
        _tasks.clear()


def _submit(markdown: str, bib: Optional[str]) -> Job:
    """Create a job and enqueue it. Raises HTTPException if the queue is full."""
    if _queue is None:
        raise HTTPException(status_code=503, detail="Service is starting up.")
    if _queued_count() >= MAX_QUEUE:
        raise HTTPException(
            status_code=429,
            detail=f"Queue is full ({MAX_QUEUE} waiting). Try again shortly.",
        )
    job = Job(id=uuid.uuid4().hex, markdown=markdown, bib=bib)
    _jobs[job.id] = job
    _job_order.append(job.id)
    _queue.put_nowait(job.id)
    return job


def _job_state(job: Job) -> dict:
    """Serialisable status payload (never includes the PDF itself)."""
    now = time.monotonic()
    state: dict = {
        "job_id": job.id,
        "status": job.status,
        "queue_position": _queue_position(job.id),
        "queued_total": _queued_count(),
        "running_total": _running_count(),
        "waited_seconds": round((job.started_at or now) - job.submitted_at, 1),
    }
    if job.started_at:
        state["build_seconds"] = round((job.finished_at or now) - job.started_at, 1)
    if job.status == "done":
        state["pdf_url"] = f"/api/jobs/{job.id}/pdf"
        state["size_bytes"] = len(job.pdf or b"")
        state["warnings"] = job.warnings
    if job.status == "error":
        state["error"] = job.error
        state["log"] = job.log
    return state


# --------------------------------------------------------------------------- #
# HTTP layer
# --------------------------------------------------------------------------- #

app = FastAPI(title="MarkPaper PDF Service", version="2.0.0", lifespan=lifespan)


def _check_auth(request: Request) -> None:
    """Enforce a bearer token if API_TOKEN is configured. No-op otherwise."""
    if not API_TOKEN:
        return
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if token != API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing API token.")


async def _extract_input(
    request: Request,
    markdown: Optional[str],
    bib: Optional[str],
    file: Optional[UploadFile],
) -> tuple[str, Optional[str]]:
    """Accept Markdown from a form field, an upload, a JSON body, or raw text."""
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
        raise HTTPException(status_code=413,
                            detail=f"Input exceeds {MAX_INPUT_BYTES} bytes.")
    return content, bib


def _pdf_response(pdf: bytes, warnings: Optional[list[str]] = None) -> Response:
    headers = {"Content-Disposition": 'inline; filename="paper.pdf"'}
    if warnings:
        # Surface non-fatal problems (e.g. dropped CJK glyphs) to API callers,
        # who otherwise receive a valid-looking but wrong PDF. Newlines are not
        # allowed in header values, so join with " | ".
        headers["X-MarkPaper-Warnings"] = " | ".join(warnings).replace("\n", " ")
    return Response(content=pdf, media_type="application/pdf", headers=headers)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "queued": _queued_count(),
        "running": _running_count(),
        "workers": max(1, MAX_CONCURRENCY),
        # Which build is actually serving traffic. Render injects these; without
        # them it is easy to debug a version that was never deployed.
        "commit": os.environ.get("RENDER_GIT_COMMIT", "unknown")[:12],
        "branch": os.environ.get("RENDER_GIT_BRANCH", "unknown"),
        "app_version": app.version,
    })


@app.get("/api/diag")
async def api_diag(request: Request) -> JSONResponse:
    """
    Report what the container actually provides.

    Exists because CJK failures are silent: without a CJK font or xeCJK.sty the
    service still returns a valid PDF with the Chinese missing. Hit this to see
    whether the toolchain is really complete.
    """
    _check_auth(request)

    def probe() -> dict:
        out: dict = {
            "root": str(APP_ROOT),
            "enable_mermaid": ENABLE_MERMAID,
            "commit": os.environ.get("RENDER_GIT_COMMIT", "unknown")[:12],
            "app_version": app.version,
        }

        def first_line(cmd: list[str]) -> str:
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                return ((r.stdout or r.stderr).strip().splitlines() or [""])[0]
            except Exception as exc:                      # noqa: BLE001
                return f"unavailable: {exc}"

        out["pandoc"] = first_line(["pandoc", "--version"])
        out["xelatex"] = first_line(["xelatex", "--version"])

        # LaTeX packages that silently break output when absent.
        for sty in ("xeCJK.sty", "placeins.sty"):
            out[sty] = first_line(["kpsewhich", sty]) or "NOT FOUND"

        # What detect-fonts.sh will hand the pipeline.
        try:
            r = subprocess.run(["bash", str(TOOLS_DIR / "detect-fonts.sh")],
                               capture_output=True, text=True, timeout=60,
                               cwd=str(APP_ROOT))
            out["detect_fonts"] = (r.stdout or "").strip().splitlines()
        except Exception as exc:                          # noqa: BLE001
            out["detect_fonts"] = f"unavailable: {exc}"

        # Installed CJK families, straight from fontconfig.
        try:
            r = subprocess.run(["fc-list", ":lang=zh-tw", "family"],
                               capture_output=True, text=True, timeout=30)
            families = sorted({line.strip() for line in
                               (r.stdout or "").splitlines() if line.strip()})
            out["cjk_family_count"] = len(families)
            out["cjk_families"] = families[:25]
        except Exception as exc:                          # noqa: BLE001
            out["cjk_families"] = f"unavailable: {exc}"

        return out

    return JSONResponse(await asyncio.to_thread(probe))


@app.get("/api/example", response_class=PlainTextResponse)
async def api_example() -> PlainTextResponse:
    if EXAMPLE_PAPER.exists():
        return PlainTextResponse(EXAMPLE_PAPER.read_text(encoding="utf-8"))
    return PlainTextResponse(_STARTER_MARKDOWN)


# ---- Async job mode ------------------------------------------------------- #

@app.post("/api/jobs", status_code=202)
async def api_create_job(
    request: Request,
    markdown: Optional[str] = Form(default=None),
    bib: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = None,
) -> JSONResponse:
    """Queue a build and return immediately with a job id to poll."""
    _check_auth(request)
    content, bib = await _extract_input(request, markdown, bib, file)
    job = _submit(content, bib)
    return JSONResponse(_job_state(job), status_code=202)


@app.get("/api/jobs/{job_id}")
async def api_job_status(request: Request, job_id: str) -> JSONResponse:
    _check_auth(request)
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown or expired job.")
    return JSONResponse(_job_state(job))


@app.get("/api/jobs/{job_id}/pdf")
async def api_job_pdf(request: Request, job_id: str) -> Response:
    _check_auth(request)
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown or expired job.")
    if job.status != "done" or job.pdf is None:
        raise HTTPException(status_code=409,
                            detail=f"Job is '{job.status}', not ready.")
    return _pdf_response(job.pdf, job.warnings)


@app.delete("/api/jobs/{job_id}")
async def api_job_delete(request: Request, job_id: str) -> JSONResponse:
    """Cancel a queued job, or discard a finished one."""
    _check_auth(request)
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown or expired job.")
    if job.status == "queued":
        # The worker skips cancelled jobs when it reaches them.
        job.status = "cancelled"
        job.finished_at = time.monotonic()
        job.markdown = ""
        job.done.set()
        return JSONResponse({"job_id": job_id, "status": "cancelled"})
    if job.status == "running":
        # A running build cannot be interrupted safely; let it finish.
        raise HTTPException(status_code=409,
                            detail="Job is already building; cannot cancel.")
    _drop(job_id)
    return JSONResponse({"job_id": job_id, "status": "deleted"})


# ---- Synchronous mode (kept for CLI/API convenience) ---------------------- #

@app.post("/api/pdf")
async def api_pdf(
    request: Request,
    markdown: Optional[str] = Form(default=None),
    bib: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = None,
) -> Response:
    """
    Submit and wait for the PDF in one call.

    Convenient for curl/scripts, but the connection stays open for the whole
    queue wait plus build. Browsers and proxies may time out first; prefer
    /api/jobs for anything interactive.
    """
    _check_auth(request)
    content, bib = await _extract_input(request, markdown, bib, file)
    job = _submit(content, bib)

    try:
        await asyncio.wait_for(job.done.wait(), timeout=SYNC_WAIT_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail={"error": "Timed out waiting for the build.",
                    "job_id": job.id,
                    "hint": f"Poll /api/jobs/{job.id} instead."},
        )

    if job.status == "error":
        raise HTTPException(status_code=422,
                            detail={"error": job.error, "log": job.log})
    if job.status != "done" or job.pdf is None:
        raise HTTPException(status_code=500,
                            detail=f"Build ended as '{job.status}'.")

    return _pdf_response(job.pdf, job.warnings)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(_INDEX_HTML)


# --------------------------------------------------------------------------- #
# Static content
# --------------------------------------------------------------------------- #

_STARTER_MARKDOWN = """\
---
author: Anonymous
---

# Introduction 簡介

This PDF was generated on demand from **Markdown** via Pandoc and XeLaTeX.

這份 PDF 是由 **Markdown** 即時產生的，中英文混排都支援。

- Plain text in, typeset PDF out. 純文字輸入，輸出排版完成的 PDF。
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
  header input { font: inherit; padding: 6px 10px; border-radius: 6px;
                 border: 1px solid #8886; background: transparent; color: inherit;
                 width: 160px; }
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
  .preview { position: relative; background: #6b72801a; display: flex;
             flex-direction: column; }
  iframe { width: 100%; flex: 1; border: 0; }
  .warn { background: #b4530933; border-bottom: 1px solid #b45309;
          padding: 8px 12px; font-size: 12px; line-height: 1.45; }
  .warn strong { display: block; margin-bottom: 2px; }
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
  <input id="token" type="password" placeholder="API token (if required)"
         title="Only needed if the server has API_TOKEN set" />
  <button class="secondary" id="loadExample" type="button">Load example paper</button>
  <button class="secondary" id="download" type="button" disabled>Download PDF</button>
  <button class="secondary" id="cancel" type="button" hidden>Cancel</button>
  <button id="generate" type="button">Generate PDF</button>
</header>
<main>
  <textarea id="src" spellcheck="false" placeholder="Paste Markdown here...">---
author: Anonymous
---

# Introduction 簡介

This PDF was generated on demand from **Markdown** via Pandoc and XeLaTeX.

這份 PDF 是由 **Markdown** 即時產生的，中英文混排都支援。

- Plain text in, typeset PDF out. 純文字輸入，輸出排版完成的 PDF。
- Edit the Markdown on the left, then click *Generate PDF*.
</textarea>
  <div class="preview">
    <div class="warn" id="warn" hidden></div>
    <iframe id="frame" title="PDF preview"></iframe>
    <div class="status" id="status">Click <strong>&nbsp;Generate PDF&nbsp;</strong> to render.</div>
  </div>
</main>
<script>
  const $ = (id) => document.getElementById(id);
  const src = $("src"), frame = $("frame"), status = $("status");
  const btnGen = $("generate"), btnEx = $("loadExample"), btnDl = $("download");
  const btnCancel = $("cancel");
  let lastUrl = null, currentJob = null, polling = false;

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

  function showWarnings(list) {
    const warn = $("warn");
    if (!list || !list.length) { warn.hidden = true; warn.textContent = ""; return; }
    warn.innerHTML = "<strong>The PDF was produced, but with problems:</strong>";
    const ul = document.createElement("ul");
    ul.style.margin = "4px 0 0 18px";
    list.forEach((w) => {
      const li = document.createElement("li");
      li.textContent = w;
      ul.appendChild(li);
    });
    warn.appendChild(ul);
    warn.hidden = false;
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

  function authHeaders() {
    const tok = $("token").value.trim();
    return tok ? { "Authorization": "Bearer " + tok } : {};
  }

  async function errorText(r) {
    let msg = "HTTP " + r.status;
    try {
      const j = await r.json();
      if (j.detail && j.detail.log) msg = j.detail.error + "\\n\\n" + j.detail.log;
      else if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch (_) {}
    return msg;
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function describe(s) {
    if (s.status === "queued") {
      const pos = s.queue_position;
      const ahead = pos && pos > 1 ? (pos - 1) + " ahead of you" : "next in line";
      return "Queued &middot; position <strong>" + (pos || "?") + "</strong> (" + ahead + ")"
           + "<br><small>" + s.queued_total + " waiting, " + s.running_total + " building"
           + " &middot; waited " + s.waited_seconds + "s</small>";
    }
    if (s.status === "running") {
      return "Building&hellip; <small>(" + (s.build_seconds || 0) + "s)</small>"
           + "<br><small>LaTeX runs twice, so this takes a moment.</small>";
    }
    return "Working&hellip;";
  }

  function finish() {
    polling = false; currentJob = null;
    btnGen.disabled = false; btnCancel.hidden = true;
  }

  btnCancel.addEventListener("click", async () => {
    if (!currentJob) return;
    const id = currentJob;
    try {
      const r = await fetch("/api/jobs/" + id, { method: "DELETE", headers: authHeaders() });
      if (r.ok) { setStatus("Cancelled."); finish(); }
      else { setStatus(await errorText(r), true); }
    } catch (e) { setStatus(String(e), true); }
  });

  btnGen.addEventListener("click", async () => {
    btnGen.disabled = true; btnDl.disabled = true;
    showWarnings([]);
    setStatus("Submitting&hellip;");
    try {
      const form = new FormData();
      form.append("markdown", src.value);
      const r = await fetch("/api/jobs", { method: "POST", body: form, headers: authHeaders() });
      if (!r.ok) { setStatus(await errorText(r), true); finish(); return; }

      const job = await r.json();
      currentJob = job.job_id; polling = true;
      btnCancel.hidden = false;
      setStatus(describe(job));

      // Poll until the build finishes. 1s is frequent enough to feel live
      // without hammering a single-instance server.
      while (polling) {
        await sleep(1000);
        if (!polling) return;
        const sr = await fetch("/api/jobs/" + currentJob, { headers: authHeaders() });
        if (!sr.ok) { setStatus(await errorText(sr), true); finish(); return; }
        const s = await sr.json();

        if (s.status === "done") {
          const pr = await fetch(s.pdf_url, { headers: authHeaders() });
          if (!pr.ok) { setStatus(await errorText(pr), true); finish(); return; }
          showPdf(URL.createObjectURL(await pr.blob()));
          showWarnings(s.warnings);
          finish();
          return;
        }
        if (s.status === "error") {
          setStatus((s.error || "Build failed") + "\\n\\n" + (s.log || ""), true);
          finish(); return;
        }
        if (s.status === "cancelled") { setStatus("Cancelled."); finish(); return; }
        setStatus(describe(s));
      }
    } catch (e) {
      setStatus(String(e), true);
      finish();
    }
  });
</script>
</body>
</html>
"""
