# Development test only. DrFed 0.1.0 has no official image; this image installs the npm package on Node.js 26 (the package needs Node.js 26 or later).
# DrFed has the license AGPL-3.0-only. It runs only as a separate test program in this container; no Claire Bible code links to it, and no Claire Bible image contains it.
FROM node:26-slim
ARG DRFED_VERSION=0.1.0
RUN npm install -g "@drfed/drfed@${DRFED_VERSION}" && npm cache clean --force
