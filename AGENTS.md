## Agent Guide for This Repository

This project is a worked example of a Markdown → Pandoc → LaTeX academic writing workflow with an optional LLM-powered translation pipeline. The **primary conceptual and user-facing documentation is in `README.md`**; always read that file first to understand the workflow, tools, and operations.

### Project Intent

- **Main goal**: Demonstrate a sustainable, plain-text academic workflow using `paper.md`, Pandoc, LaTeX, Zotero/BBT, and a `devops.sh`/`devops.ps1`-driven build. The `main` branch's `paper.md` is itself a tutorial paper arguing for and illustrating this methodology, rather than a live thesis manuscript.
- **Secondary goal**: Show how to translate the manuscript and cover into other languages using LLM-based scripts, driven by a single INI config file, and rebuild PDFs from the translated sources.
- **Note**: The author's actual completed thesis, built with an earlier iteration of this tooling, is archived on the `M11326915` branch rather than kept on `main`.

### Key Entry Points

- **`README.md`**: High-level description of the workflow, toolchain, build operations (`./devops.sh printed`, `./devops.sh translate`), and the translation pipeline.
- **`paper.md`**: Primary English manuscript; contains YAML metadata that configures Pandoc, citations, cross-references, and typesetting options.
- **`devops.sh`** / **`devops.ps1`**: The single build entry point (replaces the old `Makefile` + `make-docker.*` wrapper pair). `devops.sh` runs on Linux/macOS/WSL and does all the Docker orchestration and build logic; `devops.ps1` is a thin Windows wrapper that delegates to `bash ./devops.sh`. Operations: `pdf`, `pdf_date`, `cover`, `printed`, `translate [step]`, `tags`, `ref-list`, `toc-list`, `clean`, `deps`, `env`.
- **`zh-tw.ini`**: Single INI config file at the repo root for the translation pipeline (`DIR`, `FROM`, `TO`, `MODEL`, optional `FIGURE_LABEL`/`TABLE_LABEL`). See the comments in that file for the full key list.
- **`webapp/` + `Dockerfile.web` + `render.yaml`**: Optional on-demand PDF web service. `webapp/app.py` (FastAPI) reproduces the `devops.sh pdf` pipeline directly inside the container (no Docker-in-Docker) and serves it over HTTP; `Dockerfile.web` is a self-contained deployable image; `render.yaml` is a Render Blueprint. See the "Optional: On-Demand PDF Web Service" section in `README.md`.
- **`tools/` scripts**: Linux-based helpers for font detection, translation, validation, post-processing, logo download, PDF merging, and dependency installation. All scripts run inside the Docker container.
  - **`validate-and-fix-translated-md.sh`**: AI-powered validation that reviews translated Markdown files for formatting errors (malformed tables, broken syntax, corrupted YAML) and automatically fixes them.
  - **`postprocess-translated-md.sh`**: Fixes CJK font references and (optionally) pandoc-crossref figure/table labels.

### Constraints and Conventions for Agents

- **Default build target**: When the user requests a build or compilation without specifying a target operation, always default to the `pdf` target for `devops.sh` or `devops.ps1` (`./devops.sh pdf` or `./devops.ps1 pdf`).
- **Do not change the overall structure** of `paper.md`’s YAML metadata or its role as the single source of truth for document configuration, unless explicitly asked.
- **Preserve operation names and roles** in `devops.sh`/`devops.ps1` (`pdf`, `pdf_date`, `cover`, `printed`, `translate`, `set-api-key`, `get-api-key`, `delete-api-key`, `tags`, `ref-list`, `toc-list`, `clean`, `deps`, `env`) to avoid breaking existing workflows or documentation.
- **Keep `README.md` and `AGENTS.md` consistent** with any changes to:
  - Build commands and primary operations (use `./devops.sh`/`./devops.ps1` for Docker-based builds).
  - Translation pipeline behavior (`zh-tw.ini`'s `DIR`/`FROM`/`TO`/`MODEL`, OS credential manager / `GEMINI_API_KEY` usage).
  - Docker container usage and requirements.
- As a rule of thumb: **if you add or change `devops.sh` operations, translation scripts/profiles, API key usage, or primary documentation**, update this file accordingly.
- **Be cautious with translation scripts**:
  - Secure credential handling: API keys are securely managed via native OS credential stores (macOS Keychain, Windows Credential Manager, Linux Secret Service via `./devops.sh set-api-key`) or `GEMINI_API_KEY`; never hardcode or log credentials.
  - Translation caching & cost saving: `./devops.sh translate` automatically skips LLM translation if target source files already exist, avoiding token waste and preserving manual edits; use `--force` / `-f` to force re-translation.
  - Keep language directions and font/label assumptions (e.g., CJK fonts, `FIGURE_LABEL`/`TABLE_LABEL`) driven by `zh-tw.ini` — don't hardcode a specific target language back into `devops.sh` or the `tools/` scripts.
  - All scripts run inside the Docker container; ensure they use Linux-compatible commands (bash, standard Unix utilities).
  - The translation pipeline includes automatic validation: after initial translation, the system uses AI to detect and fix formatting errors in the translated content while preserving the translation itself.
 - **Sync plan progress to Markdown plan files**: When using plan-style workflows or multi-step tasks, always include a final step to sync the plan’s current state into the relevant Markdown plan file (e.g., under a `plans/` directory), so that progress is persistently recorded outside the transient agent context.

### Web PDF Service (`webapp/`, `Dockerfile.web`, `render.yaml`)

- **Role**: An optional HTTP service that turns submitted Markdown into a PDF on demand. It is a thin wrapper around the same pipeline as `./devops.sh pdf`; it does **not** replace `devops.sh` for local/CLI builds.
- **Async job model**: Submissions are queued (`POST /api/jobs` → poll `GET /api/jobs/{id}` → fetch `/api/jobs/{id}/pdf`). An in-memory `asyncio.Queue` with `MAX_CONCURRENCY` workers serialises builds; the UI polls and shows queue position. `POST /api/pdf` is the synchronous convenience path and goes through the *same* queue — keep both paths sharing one concurrency control rather than adding a second mechanism. State is intentionally in-memory (single instance, no Redis/DB); don't add external infrastructure without being asked.
- **Per-job isolation is a hard requirement**: each build gets its own temp dir and `TEXMFVAR`, removed in a `finally`. Never introduce shared mutable working directories.
- **No Docker-in-Docker**: Unlike `devops.sh` (which orchestrates `docker` from the host), the web service runs *inside* the container and invokes `tools/*.sh`, `pandoc`, and `xelatex` directly. Keep this distinction intact — do not make `webapp/` shell out to `docker`.
- **Keep the pipeline in sync**: `webapp/app.py`'s `build_pdf()` mirrors `build_pdf()` in `devops.sh` (Mermaid → font detection → font replace → Pandoc+crossref+citeproc → `fix-latex-csl.sh` → LaTeX ×2, LuaLaTeX primary with XeLaTeX as fallback). If you change the build steps in `devops.sh`, mirror them here (and vice versa).
- **Keep the two Dockerfiles in sync**: `Dockerfile.web` duplicates the toolchain layers of `Dockerfile` and adds a Python web layer. When you change the toolchain in `Dockerfile`, update `Dockerfile.web` too. Two divergences are deliberate and commented in both files: the web image's low-memory Chromium flags, and its second (restricted-policy) CJK smoke test.
- **CJK requires BOTH a font and `xeCJK.sty`**, and both fail silently. The base image has a reduced TeX Live scheme (that's why `placeins` needs installing), so `xecjk` and `fonts-noto-cjk` are installed explicitly in both Dockerfiles (`tools/deps.sh` is local-only and never runs for Docker). Because `xelatex` uses `-interaction=nonstopmode`, a missing package or font still yields a valid PDF with all CJK glyphs dropped — both Dockerfiles assert `xeCJK.sty` and a CJK font exist at build time. **Don't remove those assertions.**
- **CJK auto-configuration**: `paper.md` declares its own `CJKmainfont`/`xeCJK`, but arbitrary pasted Markdown does not. `webapp/app.py` passes pandoc `-V mainfont=<detected CJK font>` only when the document declares no font setup of its own; preserve that "don't override the user" behaviour. `mainfont` is used deliberately instead of loading `xeCJK`: Noto CJK covers Latin too, so one font handles a mixed document, and `mainfont` is a documented pandoc variable rather than injected raw LaTeX.
- **Don't let LaTeX fail silently**: `_scan_log()` turns non-fatal LaTeX log entries (dropped glyphs, missing files/fonts) into warnings that reach the job result, the `X-MarkPaper-Warnings` header and the UI. Keep this whenever you touch the LaTeX step; `GET /api/diag` exists for the same reason. Skipped/failed Mermaid rendering is reported the same way — never drop a diagram silently.
- **`openin_any` must stay `r`, not `p`**: paranoid mode rejects absolute-path reads, and LuaTeX applies that policy to the Lua `io` library, so luaotfload's bootstrap dies on `io.open(kpse.find_file"ScriptExtensions.txt")` and *no* build can succeed. This shipped once and looked like an image problem because the build-time test ran without the service's env vars. `_latex_env()` in `app.py` owns this; keep `openout_any=p` and `shell_escape=f`.
- **Headless Chromium cannot be contained by rlimits**: `mmdc` forks renderer processes, so `MEM_LIMIT_MB` does not bound it and a 512 MB instance gets OOM-killed as a whole (a 502 with the job lost). Hence `ENABLE_MERMAID` defaults to `false` in `render.yaml` and `Dockerfile.web` passes `--single-process --no-zygote`. Don't flip the default back on without also changing the plan.
- **`MEM_LIMIT_MB` is a runaway guard, not a memory budget**: `ulimit -v` bounds *virtual address space*, and LuaTeX reserves far more of that than it uses. Sizing it to the instance (it was `380` for a 512 MB plan) killed every CJK build, because luaotfload parses the ~20 MB Noto CJK collection into Lua tables; Latin-only documents stayed under the limit, so it looked like a font bug. Keep it generous and use `BUILD_TIMEOUT`/`MAX_CONCURRENCY` as the real controls.
- **Build-time tests must reproduce runtime conditions**: `Dockerfile.web` runs the pandoc-driven CJK smoke test twice — once unrestricted (which also warms luaotfload's font cache as root) and once with `openin_any`/`openout_any`/`shell_escape` set exactly as the service sets them. Both must pass or the image build fails. Keep the second pass; a test that skips it has already let a broken image through.
- **Security**: The service compiles arbitrary user-supplied Markdown/LaTeX. Preserve the hardening in `app.py` (LaTeX run without shell-escape, `openout_any=p`, secret-looking env vars stripped from the LaTeX child via `_SECRET_ENV_RE`, per-request temp dirs, size/time/concurrency limits) and the optional `API_TOKEN` bearer gate. Never enable `-shell-escape`.
- **Measure, don't guess, when CJK builds fail**: `GET /api/probe` compiles a one-line document and reports per engine the exit code, PDF size, **peak child RSS**, whether luaotfload hit its font cache, and the log head/tail. It takes `font`, `engine`, `mem_limit` and `text` query parameters so alternatives can be compared on a live instance without rebuilding the image. Font/memory failures all present as a LaTeX log full of font messages, which is indistinguishable from a genuinely missing font — this endpoint is what tells them apart. Keep it.
- **Font size is a memory decision**: `fonts-noto-cjk` is a ~120 MB pan-CJK collection and luaotfload parses fonts in Lua, so it does not fit a 512 MB instance (measured: ~437 MB peak RSS for a one-line document even with a ~10 MB font). `Dockerfile.web` installs smaller `fonts-arphic-*`/`fonts-wqy-zenhei` families and sets `CJK_FONT` via a build ARG. Don't hardcode a font into `tools/detect-fonts.sh` (it is shared with the CLI workflow, which has the memory for Noto) — override with `CJK_FONT` instead.
- **`CJK_FONT` lives in `Dockerfile.web`, not `render.yaml`**: the image build warms luaotfload's font cache for that exact family, and requests cannot write the cache themselves because they run with `openout_any=p`. Setting `CJK_FONT` from the environment still works but forfeits the warmed cache, so every build re-parses the font in memory. Keep the warm-up compiling with `$CJK_FONT` rather than a hardcoded family.
- **Engines sign off differently**: pdfTeX/XeTeX write "Here is how much of TeX's memory you used", LuaTeX writes a "PDF statistics" block. `_log_finished()` checks for both — it is what distinguishes "LaTeX complained" from "the process was killed", and testing only for the XeTeX marker reported every LuaLaTeX run as killed.
- **Config via env vars** (not code): `PORT`, `API_TOKEN`, `BUILD_TIMEOUT`, `MAX_INPUT_BYTES`, `MAX_CONCURRENCY`, `MAX_QUEUE`, `JOB_TTL`, `MAX_STORED_RESULTS`, `SYNC_WAIT_TIMEOUT`, `ENABLE_MERMAID`, `MEM_LIMIT_MB`, `PDF_ENGINE`, `FALLBACK_ENGINE`, `CJK_FONT`, `MARKPAPER_ROOT`. Document changes to these in `README.md` and here.
- **Memory discipline**: finished PDFs are held in RAM, so `JOB_TTL` expiry and `MAX_STORED_RESULTS` eviction must stay in place — the free tier only has 512 MB.

### Commit Message Conventions for Agents

- **Use nested lists in commit messages**: When generating commit messages, structure the body as nested lists (e.g., top-level bullets for major changes, indented sub-bullets for details or rationale) to keep the "what" and "why" clear and scannable.

### How to Help Users

- For **build questions**, point users to `./devops.sh printed` (or `./devops.ps1 printed` on Windows) for the English workflow and `./devops.sh translate` for the Traditional Chinese workflow, and reference the relevant sections in `README.md`. All builds run inside the Docker container.
- For **workflow changes**, favor solutions that:
  - Maintain plain-text, Git-friendly files.
  - Keep configuration in YAML and `zh-tw.ini` rather than ad-hoc shell commands.
- For **dependency and version questions**, explain that:
  - All toolchains (Pandoc, LaTeX, etc.) are provided by the `dalibo/pandocker` Docker container.
  - A derived image (`pandocker-with-tools:latest`) is automatically built from `Dockerfile` on first use, adding `jq` and `curl` for translation scripts.
  - No local installation is required; Docker handles all dependencies.
  - The `./devops.sh deps` operation is only for local development/testing and is not needed when using Docker.
  - The container images include all necessary tools pre-configured and ready to use.
- For **new languages or targets**, edit `zh-tw.ini`'s values (or replace the file, keeping the same key names) rather than editing `devops.sh` itself, and update both `README.md` and this `AGENTS.md` accordingly if the interface changes.


