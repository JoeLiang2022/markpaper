FROM dalibo/pandocker:latest-full

# NOTE: do NOT set NODE_OPTIONS="--jitless" here. --jitless disables the JIT and
# therefore WebAssembly, which Node's bundled undici (used by npm/puppeteer during
# install) requires -> "ReferenceError: WebAssembly is not defined".

# Install jq and curl for translation scripts
# Install basic dependencies for Puppeteer/Chrome (required by mermaid-cli)
RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends \
        jq curl gnupg ca-certificates \
        fonts-liberation fonts-noto-color-emoji \
        fonts-noto-cjk \
        libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libgbm1 libpangocairo-1.0-0 libcairo-gobject2 \
        libgtk-3-0 libgdk-pixbuf2.0-0 && \
    fc-cache -f && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Sanity-check that a CJK font really is available. tools/detect-fonts.sh looks
# for "Noto Sans CJK TC" and otherwise falls back to a hardcoded name, so a
# missing font would silently produce PDFs with no Chinese glyphs.
#
# Write fc-list to a file rather than piping into grep -q/head: this RUN executes
# under "bash -o pipefail", and those consumers exit early, so the producer takes
# SIGPIPE and the pipeline reports failure even on a successful match.
RUN set -eu; \
    fc-list > /tmp/fonts.txt; \
    echo "Installed CJK fonts (sample):"; \
    { grep -i "CJK" /tmp/fonts.txt || true; } | head -5 || true; \
    grep -qiE "Noto (Sans|Serif) CJK" /tmp/fonts.txt \
        || { echo "ERROR: no CJK font installed"; exit 1; }; \
    rm -f /tmp/fonts.txt

# Install Node.js 22 LTS. The base image ships Node 18, which is too old for the
# current mermaid-cli / puppeteer packages (they use the RegExp `v` flag that
# only Node 20+ understands, so `puppeteer browsers install` crashes on Node 18).
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y -qq nodejs && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    node --version

# Install mermaid-cli globally
RUN npm install -g @mermaid-js/mermaid-cli

# Install Chrome headless shell that Puppeteer expects
# Install it in a system-wide location accessible to all users
RUN mkdir -p /opt/puppeteer-cache && \
    chmod 777 /opt/puppeteer-cache && \
    PUPPETEER_CACHE_DIR=/opt/puppeteer-cache npx --yes puppeteer browsers install chrome-headless-shell && \
    chmod -R 777 /opt/puppeteer-cache && \
    find /opt/puppeteer-cache -type f -exec chmod 755 {} \; && \
    find /opt/puppeteer-cache -type d -exec chmod 755 {} \;

# Set environment variables for Puppeteer
ENV PUPPETEER_CACHE_DIR=/opt/puppeteer-cache

# Resolve the installed Chrome executable path and write Puppeteer config
RUN CHROME_PATH=$(find /opt/puppeteer-cache -name "chrome-headless-shell" -type f | head -1) && \
    echo "{\"executablePath\": \"${CHROME_PATH}\", \"args\": [\"--no-sandbox\", \"--disable-setuid-sandbox\"]}" > /etc/puppeteer-config.json && \
    chmod 644 /etc/puppeteer-config.json
ENV PUPPETEER_CONFIG_FILE=/etc/puppeteer-config.json

# Install libasound2 for Chromium (Ubuntu 24.04 uses libasound2t64)
RUN apt-get update -qq && \
    (apt-get install -y -qq libasound2 2>/dev/null || \
     apt-get install -y -qq libasound2t64 2>/dev/null || true) && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install the LaTeX packages this workflow needs beyond the base image's
# (reduced) TeX Live scheme:
#   placeins - \FloatBarrier, used by paper.md's header-includes
#   xecjk    - provides xeCJK.sty, required for ANY Chinese/CJK typesetting
#
# xeCJK matters more than it looks: xelatex runs with -interaction=nonstopmode,
# so a missing xeCJK does not fail the build - LaTeX carries on and produces a
# perfectly valid PDF with every CJK glyph silently dropped. Assert it exists.
# Pin a known-good CTAN mirror to avoid transient mirror signature issues.
#
# The image ships a fixed TeX Live release, but CTAN's main "tlnet" tree tracks
# the newest release. Once CTAN rolls over, tlmgr refuses to talk to it
# ("Local TeX Live (N) is older than remote repository (N+1)"). So pin the
# frozen historic repository matching the installed release, and do NOT run
# "tlmgr update --self" (cross-release self-update is what fails).
RUN set -eu; \
    need=""; \
    kpsewhich placeins.sty >/dev/null 2>&1 || need="$need placeins"; \
    kpsewhich xeCJK.sty    >/dev/null 2>&1 || need="$need xecjk"; \
    if [ -z "$need" ]; then \
        echo "placeins + xeCJK already present"; \
    else \
        echo "Missing TeX packages:$need"; \
        TLYEAR="$(tlmgr --version 2>/dev/null | sed -n 's/.*version \([0-9]\{4\}\).*/\1/p' | tail -1)"; \
        [ -n "$TLYEAR" ] || TLYEAR=2025; \
        echo "Detected TeX Live ${TLYEAR}"; \
        for base in \
            "https://ftp.math.utah.edu/pub/tex/historic" \
            "https://ftp.tu-chemnitz.de/pub/tug/historic" ; do \
            repo="${base}/systems/texlive/${TLYEAR}/tlnet-final"; \
            echo "Trying TeX Live repository: ${repo}"; \
            tlmgr --repository "$repo" --verify-repo=none install $need || true; \
            if kpsewhich placeins.sty >/dev/null 2>&1 && kpsewhich xeCJK.sty >/dev/null 2>&1; then break; fi; \
        done; \
        if ! kpsewhich placeins.sty >/dev/null 2>&1; then \
            echo "Falling back to direct CTAN download for placeins"; \
            TEXMFLOCAL="$(kpsewhich -var-value=TEXMFLOCAL)"; \
            mkdir -p "${TEXMFLOCAL}/tex/latex/placeins"; \
            curl -fsSL -o "${TEXMFLOCAL}/tex/latex/placeins/placeins.sty" \
                https://mirrors.ctan.org/macros/latex/contrib/placeins/placeins.sty; \
            mktexlsr; \
        fi; \
    fi; \
    kpsewhich placeins.sty || { echo "ERROR: placeins.sty not installed"; exit 1; }; \
    kpsewhich xeCJK.sty    || { echo "ERROR: xeCJK.sty not installed (CJK would silently vanish)"; exit 1; }

# Keep the same entrypoint as base image
ENTRYPOINT [""]

