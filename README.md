# Mark Paper

English | [繁體中文](README.zh-TW.md)

---

This repository is a self-contained, reproducible example of a modern plain‑text academic workflow built around **Markdown**, **Pandoc**, and **LaTeX**. The core idea is to separate content from presentation: you write the manuscript as plain text in `paper.md`, while formatting, typesetting, and output details are handled automatically by Pandoc, LaTeX, and a small set of configuration files.

The `main` branch itself is that example: `paper.md` is a tutorial/methodology paper that explains **why** and **how** to author an academic paper or thesis this way — plain text, Pandoc, LaTeX, and Git — instead of a word processor like Microsoft Word or Google Docs. It demonstrates the full pipeline end to end (citations, bibliography, tables, cross‑references, Mermaid diagrams, multilingual typesetting, custom page layout, standalone cover pages, and AI-assisted workflows) using itself as the worked example.

**Note**: The author's actual completed thesis, built with an earlier iteration of this tooling, is archived on the `M11326915` branch for reference rather than kept on `main`.

### Key Ideas

- **Sustainability and durability**: Plain‑text Markdown files are future‑proof compared to proprietary word‑processor formats. They remain readable and diff‑friendly, and integrate naturally with Git.
- **Separation of concerns**: The manuscript (`paper.md`) contains only semantic content and structure; the visual appearance is delegated to LaTeX templates and Pandoc settings defined in the YAML metadata block.
- **Reproducibility**: The entire pipeline—from Markdown and bibliography data to final PDF—is scripted and repeatable. Anyone with the same toolchain can regenerate the exact same output.
- **Transparency and debuggability**: Every stage (Markdown → Pandoc → LaTeX → PDF) is inspectable. Intermediate artifacts like the generated `.tex` file can be examined to debug typesetting or filter issues.
- **Git‑friendly writing**: Because everything is plain text (manuscript, metadata, bibliography), the full research and writing history can be tracked, branched, and merged with standard version control practices.
- **Flattening the toolchain learning curve**: AI assistants and autonomous coding tools serve as compiler copilots, translating high-level writing and layout intents into precise Pandoc YAML and LaTeX macros without requiring authors to master arcane TeX syntax.

### Components Demonstrated in `paper.md`

- **Pandoc**: The central document converter, transforming `paper.md` into LaTeX and then to PDF.
- **LaTeX distribution (TeX Live / XeLaTeX)**: Provides the Unicode-aware typesetting engine and packages needed for advanced layouts, micro-typography, and multilingual text.
- **Plain‑text editor**: Any modern editor (VS Code, Zettlr, etc.) used for authoring the Markdown source.
- **Reference managers (Zotero + Better BibTeX)**: Manages bibliographic data and exports it automatically as `references.bib` for Pandoc to consume (optional; authors can also create and maintain bibliography files manually).
- **CSL styles**: Citation Style Language definitions (e.g., `chicago-author-date.csl`) governing in-text citations and the bibliography.
- **Pandoc filters**:
  - `--citeproc` for automated citation processing and bibliography generation.
  - `pandoc-crossref` for numbering and cross‑referencing figures, tables, and equations.
- **Mermaid.js & Puppeteer**: Automated headless rendering of programmatic diagrams into high-resolution images (`images/mermaid-*.png`).
- **Standalone LaTeX cover page (`cover_page.tex`)**: A dedicated cover page template featuring the official `images/scholarship_logo.jpg` emblem and bilingual metadata.

### Features Illustrated

- **YAML metadata block as control panel**: At the top of `paper.md`, a rich YAML header configures:
  - Document metadata (title, author, abstract).
  - Bibliography file (`references.bib`) and CSL style (`chicago-author-date.csl`).
  - PDF engine (`xelatex`) and LaTeX header includes (`header-includes`).
  - Cross‑reference prefixes (`figPrefix`, `tblPrefix`, `eqnPrefix`) and formatting conventions.
  - Section numbering (`numbersections: true`), table of contents (`toc`), and page numbering behavior.
  - Complete bibliography inclusion (`nocite: '@*'`).
- **Automated citations & bibliography formatting**: In‑text citations use Pandoc's syntax (e.g., `[@key]`, `@key`, `[-@key]`) and resolve to a clean, bulleted reference list at the end of the manuscript with full author names.
- **Tables and cross‑references**: Semantic labels (e.g., `{#tbl:workbench}`, `{#fig:my-plot}`, `{#eq:relativity}`) plus `pandoc-crossref` enable automatic numbering and internal references like `@tbl:workbench`.
- **Programmatic diagramming with Mermaid**: Flowcharts written directly in `paper.md` are automatically rendered into crisp 3x resolution images and substituted before Pandoc compilation.
- **Multilingual typesetting**: Using XeLaTeX and CJK font settings (`Noto Sans CJK TC` / `PingFang SC`) enables high‑quality Traditional Chinese text alongside English.
- **Custom appearance and templates**: Hooking Pandoc into LaTeX templates (e.g., Eisvogel) allows deep layout customization entirely from YAML.
- **Dedicated cover page & document packaging**: `cover_page.tex` provides a dedicated cover page that is compiled with XeLaTeX and merged with the main manuscript into `printed.pdf` via `./devops.sh printed`.
- **Dynamic date injection**: Dates are automatically injected at build time rather than hardcoded in source files:
  - Paper PDFs use the current date in `YYYY-MM-DD` format (injected via Pandoc's `-V date` flag).
  - Cover PDFs use the current date in `Month DD, YYYY` format (injected via `tools/inject-date.sh`).
- **AI-assisted writing and automated validation**: Integrating LLMs for draft refinement, citation auditing, and structure-aware automated translation (`./devops.sh translate`).

### Repository Structure

```
├── paper.md                    # Primary manuscript (with embedded examples)
├── cover_page.tex              # Standalone LaTeX cover page template
├── references.bib              # Bibliographic database in BibTeX format
├── chicago-author-date.csl     # CSL style definition (Chicago author-date)
├── zh-tw.ini                   # Single configuration file for translation pipeline
├── devops.sh                   # Main build & Docker orchestration script (macOS/Linux/WSL)
├── devops.ps1                  # Windows PowerShell wrapper for devops.sh
├── Dockerfile                  # Extends pandocker with jq, curl, and tools
├── images/
│   └── scholarship_logo.jpg    # High-resolution vector emblem for cover page
└── tools/                      # Build, font detection, and translation helper scripts
    ├── detect-fonts.sh         # Detects available host/container CJK fonts
    ├── inject-date.sh          # Injects current date into cover_page.tex
    ├── merge-pdfs.sh           # Merges cover, administrative forms, and paper PDF
    ├── process-mermaid.sh      # Extracts and renders Mermaid code blocks via Puppeteer
    ├── translate.sh            # LLM translation engine (structure-aware)
    └── validate-and-fix-translated-md.sh # AI syntax validation and repair script
```

### Toolchain Requirements

This project uses **Docker** to provide a consistent, reproducible build environment. All toolchains run inside the container, which includes:

- **Pandoc** (with built‑in `--citeproc`) and **pandoc-crossref** filter
- **LaTeX distribution** (TeX Live) with XeLaTeX and standard packages
- **Mermaid CLI (`mmdc`)** with headless Chromium / Puppeteer for diagram compilation
- All necessary CJK fonts and dependencies

**Prerequisites:**

- **Docker** installed and running on your system
- **bash** available on `PATH` (Git for Windows / WSL on Windows) — `devops.ps1` shells out to `devops.sh`
- A **plain‑text editor** and **Git** for version control

### Quick Start

Writing your thesis with this framework is straightforward:

1. **Write the manuscript body**: Open `paper.md`, locate the content body beneath the YAML front matter, and write your paper using standard Markdown syntax. Run `./devops.sh pdf` (or `./devops.ps1 pdf` on Windows) to compile and review the generated PDF for any issues.
2. **Insert and cross-reference figures**: Place image files in `images/` (or write Mermaid diagram blocks directly), then insert and cross-reference them by checking the syntax examples demonstrated in `paper.md` itself (such as `![Caption](images/foo.png){#fig:foo}` and `@fig:foo`).
3. **Add citations**: Add your bibliographic entries to `references.bib` (manually or via Zotero + BBT), and cite them in the text using `[@key]` syntax following the examples in `paper.md`.
4. **Let the framework handle typesetting**: Once you are familiar with these core writing steps, you don't need to worry about complex layout details. The framework automatically handles heading numbering, cross-references, footnotes, page numbers, table of contents, and bibliography formatting.

### Basic Usage: Build the Example PDF

All build, translation, and utility operations are driven by a single **Development Operations Center** script:

- **Linux/macOS/WSL**: `./devops.sh <operation>`
- **Windows PowerShell**: `./devops.ps1 <operation>` (delegates to `bash ./devops.sh` — requires Git Bash or WSL)

```bash
# Linux/macOS/WSL
./devops.sh pdf

# Windows PowerShell
./devops.ps1 pdf
```

Running an operation will:

1. Check for the base image `dalibo/pandocker:latest-full` and pull it if needed.
2. Build a derived image `pandocker-with-tools:latest` (with `jq` and `curl` pre-installed) from `Dockerfile`, if it doesn't exist yet.
3. Run the requested operation inside an ephemeral container, with the current directory mounted at `/workspace`.
4. Remove the container automatically after the operation completes.

**Note**: The first run will build the derived image, which may take a few minutes. Subsequent runs reuse the cached image.

Run `./devops.sh help` (or `./devops.ps1 help`) to see all available operations:

| Operation                     | Description                                            |
| ------------------------------ | -------------------------------------------------------- |
| `pdf`                         | Build the main paper PDF (`paper.pdf`)                  |
| `pdf_date`                    | Build the paper PDF with a `YYYYMMDD` date suffix        |
| `cover`                       | Build the standalone cover page PDF (`cover.pdf`)       |
| `printed`                     | Build the printed version (cover + paper merged)        |
| `translate [step] [-f]`      | Run translation pipeline (skips existing by default, `--force` to re-translate) |
| `set-api-key [key]`          | Save Gemini API key to OS credential manager            |
| `get-api-key`                | Check configured Gemini API key in OS credential store  |
| `delete-api-key`             | Remove Gemini API key from OS credential manager        |
| `tags`                        | Generate `.tags` from all Markdown files                |
| `ref-list`                    | Extract references from a PDF to clipboard              |
| `toc-list`                    | Extract table of contents from a PDF to clipboard       |
| `clean`                       | Remove all generated intermediate and PDF files         |
| `deps`                        | Show information about local (non-Docker) dependencies  |
| `env`                         | Check environment and guide toolchain/Docker setup      |

### Optional: Translate to Other Languages (`translate` target)

This project demonstrates how to leverage an LLM-backed translation pipeline, driven entirely from `devops.sh`, to produce translated versions of the paper and cover page for any target language.

- **Source**: The original English manuscript in `paper.md` and the cover page in `cover_page.tex`.
- **Config**: The translation target is defined in a single INI config file, `zh-tw.ini` at the repo root (source/target language names, output directory, LLM model, and optional pandoc-crossref label overrides).
- **LLM translation & caching**: `./devops.sh translate` calls `tools/translate.sh`, which invokes a large language model (default `gemini-2.5-flash`, configurable in `zh-tw.ini`) using an API key retrieved securely from the native OS credential manager (macOS Keychain, Windows Credential Manager, or Linux Secret Service via `./devops.sh set-api-key`) or the `GEMINI_API_KEY` environment variable. If translated source files (`paper.md`, `cover_page.tex`) already exist in the target directory, translation is automatically skipped to save API tokens and avoid overwriting manual edits (use `--force` / `-f` to force a full re-translation).
- **AI-powered validation**: After initial translation, `tools/validate-and-fix-translated-md.sh` automatically reviews the translated Markdown for formatting errors (malformed tables, broken syntax, corrupted YAML) and fixes them while preserving the translated content.
- **Post-processing and typesetting**: Additional scripts fix fonts and crossref labels, then Pandoc and XeLaTeX compile the translated sources into fully typeset PDFs with cover pages.

```bash
./devops.sh translate                # run pipeline (reuses existing translated markdown/cover to save tokens)
./devops.sh translate --force        # force full re-translation and rebuild from scratch
./devops.sh translate pdf            # rebuild only the translated paper PDF (re-run one step)
```

`step` may be `all` (default), `markdown`, `cover`, `pdf`, `cover_pdf`, or `printed`. Optional flag `--force` (or `-f`) forces full re-translation.

The resulting files are written under the configured `DIR` (e.g. `translated-zh-tw/`), mirroring the structure of the original English workflow.

### Optional: On-Demand PDF Web Service (`webapp/`)

In addition to the CLI workflow, the repository ships an optional **web service** that turns submitted Markdown into a PDF over HTTP — paste Markdown in a browser, click a button, get a typeset PDF back. It is a thin wrapper around the exact same pipeline as `./devops.sh pdf`.

Unlike `devops.sh` (which orchestrates Docker from your host), the web service runs *inside* the container and calls `pandoc`/`xelatex`/`tools/*.sh` directly. This makes it deployable to container hosts such as [Render](https://render.com) that run one long-lived service per container.

**Components:**

- **`webapp/app.py`** — a small [FastAPI](https://fastapi.tiangolo.com/) application.
- **`Dockerfile.web`** — a self-contained image: the full MarkPaper toolchain plus the Python web server.
- **`render.yaml`** — a Render Blueprint for one-click deployment.

**Endpoints:**

| Method & Path             | Description                                                        |
| ------------------------- | ------------------------------------------------------------------ |
| `GET /`                   | Browser UI: a Markdown editor with a live PDF preview pane         |
| `POST /api/jobs`          | Queue a build; returns `202` with a `job_id` immediately           |
| `GET /api/jobs/{id}`      | Job status, including `queue_position` and timings                 |
| `GET /api/jobs/{id}/pdf`  | Download the finished PDF (`409` if not ready)                     |
| `DELETE /api/jobs/{id}`   | Cancel a queued job, or discard a finished one                     |
| `POST /api/pdf`           | Submit **and wait**; returns `application/pdf` in one call         |
| `GET /api/example`        | Returns the bundled `paper.md` so you can try the full example     |
| `GET /api/diag`           | Toolchain report: pandoc/xelatex versions, `xeCJK.sty`, CJK fonts  |
| `GET /healthz`            | Health check (used by Render); also reports queue depth            |

**Multi-user behaviour.** Builds are slow and memory-hungry, so at most `MAX_CONCURRENCY` run at a time (default `1`) and the rest queue. Any number of people can use the service concurrently:

- **Queueing**: submissions are served first-come, first-served. The browser UI polls once a second and shows your **position in the queue** ("position 3, 2 ahead of you"), then switches to a build timer. Queued jobs can be cancelled; a build already running cannot.
- **Isolation**: every job builds in its own temporary directory (unique name, its own `TEXMFVAR`) which is deleted afterwards, whether it succeeded or failed. There is no database and nothing is persisted; the PDF is held in memory only until you download it or it expires (`JOB_TTL`).
- **Prefer `/api/jobs` for anything interactive.** `POST /api/pdf` holds the connection open for the entire queue wait plus build, so browsers or proxies may time out first. It exists for curl and scripts.

State lives in memory in a single process, which suits one free-tier instance. Jobs do not survive a restart and are not shared across multiple instances.

**Using it from the command line:**

```bash
BASE=https://your-service.onrender.com

# One-shot: submit and wait (simplest; may time out on a long queue)
curl -X POST "$BASE/api/pdf" -F "markdown=$(cat paper.md)" -o paper.pdf

# Job mode: submit, poll, download (no long-held connection)
ID=$(curl -sX POST "$BASE/api/jobs" -F "markdown=$(cat paper.md)" | jq -r .job_id)
until [ "$(curl -s "$BASE/api/jobs/$ID" | jq -r .status)" = "done" ]; do sleep 2; done
curl -s "$BASE/api/jobs/$ID/pdf" -o paper.pdf
```

Add `-H "Authorization: Bearer $API_TOKEN"` to each call if the service has a token set.

**Run locally with Docker:**

```bash
docker build -f Dockerfile.web -t markpaper-web .
docker run --rm -p 8000:8000 markpaper-web
# open http://localhost:8000
```

**Deploy to Render:**

1. Push this repository to GitHub/GitLab.
2. In the Render dashboard: **New +** → **Blueprint**, and select the repo. Render reads `render.yaml`.
3. Render builds `Dockerfile.web` and starts the service, injecting `$PORT` automatically.

**Configuration (environment variables):**

| Variable             | Default   | Purpose                                                        |
| -------------------- | --------- | -------------------------------------------------------------- |
| `PORT`               | `8000`    | Port the server binds to (Render sets this automatically)      |
| `API_TOKEN`          | *(unset)* | If set, the API requires `Authorization: Bearer <token>`       |
| `BUILD_TIMEOUT`      | `240`     | Max seconds for a single build                                 |
| `MAX_INPUT_BYTES`    | `2097152` | Max accepted Markdown payload (2 MiB)                          |
| `MAX_CONCURRENCY`    | `1`       | Simultaneous builds / worker count (budget ~1 GB each)         |
| `MAX_QUEUE`          | `50`      | Max jobs waiting before new submissions get `429`              |
| `JOB_TTL`            | `300`     | Seconds a finished result is retained                          |
| `MAX_STORED_RESULTS` | `5`       | Max finished PDFs kept in memory (oldest evicted first)        |
| `SYNC_WAIT_TIMEOUT`  | `600`     | Max seconds `POST /api/pdf` waits before returning `504`       |
| `ENABLE_MERMAID`     | `false`   | Mermaid rendering (needs headless Chromium — see below)        |
| `MEM_LIMIT_MB`       | `1024`    | Address-space cap per LaTeX child (runaway guard); `0` disables |
| `PDF_ENGINE`         | `lualatex`| Primary LaTeX engine                                           |
| `FALLBACK_ENGINE`    | `xelatex` | Engine retried once if the primary produces no PDF; `""` = off |

**Chinese / CJK output.** Two things are required and both are easy to get wrong silently:

1. **A CJK font** — the image installs `fonts-noto-cjk`.
2. **`xeCJK.sty`** — the base image ships a *reduced* TeX Live scheme, so `xecjk` is installed explicitly.

Neither failure is loud. The LaTeX engine runs with `-interaction=nonstopmode`, so if `xeCJK` or the font is missing it still emits a valid-looking PDF with **every Chinese character silently dropped**. Both Dockerfiles therefore assert these exist at build time, and the service scans the LaTeX log and reports dropped glyphs as warnings on the result instead of handing you a quietly wrong PDF. Use `GET /api/diag` to confirm what a running instance actually has.

Given those, the service auto-configures CJK: if your Markdown does not declare a font itself, it passes pandoc `-V mainfont=<detected CJK font>` (normally *Noto Sans CJK TC*, which also covers Latin), so plain Chinese Markdown with no YAML at all renders correctly. If you *do* want control, declare it yourself and the service leaves your choice alone:

```yaml
---
CJKmainfont: Noto Sans CJK TC
---
```

> **Security note**: This service compiles arbitrary user-supplied Markdown/LaTeX. LaTeX is run without shell-escape and with writes confined to the working directory (`openout_any=p`), each build runs in an isolated temporary directory, and size/time/concurrency limits apply. Reads use `openin_any=r` rather than `p`, because paranoid mode also blocks LuaTeX's own font machinery from reading `ScriptExtensions.txt` under `/opt/texlive` and no build can then succeed; to compensate, secret-looking environment variables (anything matching `TOKEN`/`SECRET`/`PASSWORD`/`KEY`) are stripped from the LaTeX child's environment, so `\input{/proc/self/environ}` yields nothing useful. Even so, treat a public instance as untrusted compute: **set `API_TOKEN`** for anything beyond a throwaway demo. The service is stateless and has no built-in authentication otherwise.

> **Resource note**: `render.yaml` defaults to the **free** plan (512 MB, $0), which is fine for plain Markdown. Mermaid rendering is a different story: `mmdc` launches headless Chromium, which forks renderer processes, so no per-process memory limit can contain it and 512 MB gets exhausted — the platform then kills the whole container and callers see a **502** with their job gone. `ENABLE_MERMAID` therefore defaults to `false`. Documents containing Mermaid still build; the diagram source appears as a code block and a warning is attached to the result. Set `ENABLE_MERMAID=true` once you are on `starter` or larger. Free instances also sleep after ~15 minutes of inactivity and cold-start slowly because the image is large.

### Conceptual Overview of the Workflow

- **Input layer**: `paper.md` (manuscript) + BibTeX bibliography file (`references.bib`) + CSL style + `cover_page.tex`.
- **Processing layer**:
  - `tools/process-mermaid.sh` renders Mermaid diagrams to high-resolution PNGs.
  - Pandoc parses the Markdown and YAML metadata.
  - `--citeproc` resolves citations and formats the bulleted bibliography.
  - `pandoc-crossref` resolves numbering and cross‑references.
  - Pandoc produces LaTeX, which is compiled by XeLaTeX.
  - `tools/merge-pdfs.sh` fuses the cover page and manuscript into `printed.pdf`.
- **Output layer**: A fully typeset PDF bundle suitable for institutional archiving and academic publication.
