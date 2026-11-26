# Full contributor toolbox image (docs/design/CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md Sections 6.1 and 14 DE1).
# Build context: repository root. Supports linux/amd64 and linux/arm64 with no amd64-only URLs.

FROM denoland/deno:bin-2.9.8 AS deno-bin
FROM python:3.15.2-slim

ENV PYTHONUNBUFFERED=1 \
    CLAIRE_IGNORE_DOTENV=1 \
    TMPDIR=/tmp \
    UV_PROJECT_ENVIRONMENT=/opt/claire-venv \
    VIRTUAL_ENV=/opt/claire-venv \
    CLAIRE_E2E_PYTHON=/opt/claire-venv/bin/python \
    CLAIRE_E2E_NODE_MODULES=/opt/claire-e2e/node_modules \
    NODE_PATH=/opt/claire-e2e/node_modules \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    DENO_DIR=/opt/deno-cache \
    PYTHONPATH=/src:/src/src \
    PATH="/opt/claire-venv/bin:/opt/claire-e2e/node_modules/.bin:$PATH"

# Base system tools, Node.js, ffmpeg, Ruby/Asciidoctor, and Docker CLI with the Compose plugin (multi-arch via dpkg architecture).
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        ffmpeg \
        git \
        gnupg \
        nodejs \
        npm \
        procps \
        ruby \
        tzdata \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && . /etc/os-release \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && gem install --no-document 'asciidoctor:~> 2.0.26' 'asciidoctor-revealjs:~> 5.2.0' \
    && rm -rf /var/lib/apt/lists/* /root/.gem /var/lib/gems/*/cache/*

RUN pip install --no-cache-dir uv

COPY --from=deno-bin /deno /usr/local/bin/deno

# Layer 1 (lockfiles): Python dependencies (all extras and dev group) into /opt/claire-venv.
WORKDIR /tmp/claire-deps
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-install-project --all-extras \
    && uv cache clean && rm -rf /root/.cache /tmp/claire-deps \
    && chmod -R a+rX /opt/claire-venv

# Layer 2 (lockfiles): Playwright Node.js packages into /opt/claire-e2e and Chromium into /opt/ms-playwright.
WORKDIR /opt/claire-e2e
COPY e2e/package.json e2e/package-lock.json ./
RUN npm ci --no-audit --no-fund \
    && /opt/claire-e2e/node_modules/.bin/playwright install --with-deps chromium \
    && npm cache clean --force && rm -rf /root/.npm \
    && chmod -R a+rX /opt/claire-e2e /opt/ms-playwright

# Layer 3 (lockfiles): Pre-cache Deno dependencies for the Fedify sidecar into DENO_DIR=/opt/deno-cache.
WORKDIR /tmp/deno-deps
COPY sidecar/fedify/deno.json sidecar/fedify/deno.lock ./
RUN deno install --frozen
COPY sidecar/fedify/src ./src
COPY sidecar/fedify/tests ./tests
COPY sidecar/fedify/e2e ./e2e
RUN deno cache --frozen src/main.ts tests/*.ts e2e/*.ts \
    && rm -rf /tmp/deno-deps \
    && chmod -R a+rX /opt/deno-cache

WORKDIR /src
CMD ["bash"]
