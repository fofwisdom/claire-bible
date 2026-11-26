# Development network only (docs/design/CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md Sections 6.6 and 8.4): the toolbox of the development network.
# It runs the bootstrap of each server and the tests with the marker devnet on the network claire-devnet-edge, so that no test process on the host connects to a container address.
# It is the Claire image of the development network with the extra "dev" of uv.lock (pytest). It is not the full toolbox of phase DE1 (no Deno, no Node.js, no Playwright).
ARG CLAIRE_IMAGE=claire-bible:devnet
FROM ${CLAIRE_IMAGE}
# --inexact keeps the packages of the image that the lock file does not select (the project and the newer yt-dlp).
RUN uv sync --locked --inexact --no-install-project --extra stealth --extra audio --extra ko --extra dev \
    && uv cache clean && rm -rf /root/.cache
ENV PYTHONDONTWRITEBYTECODE=1
