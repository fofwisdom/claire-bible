# Fedify sidecar image (design Section 4.2). The Python image of Claire has no JavaScript runtime: this image is separate.
# Build context: sidecar/fedify/
FROM denoland/deno:2.9.8

WORKDIR /app
COPY deno.json deno.lock ./
COPY src ./src
# The frozen lock file fixes each dependency version (design Section 13, FS11).
RUN deno cache --frozen src/main.ts

USER deno
EXPOSE 8790
# The process can use the network, the data directory, and the socket directory only.
CMD ["run", "--frozen", "--cached-only", "--allow-net", "--allow-read=/data,/app", "--allow-write=/data/fedify,/data/run/federation.sock", "--allow-env=CLAIRE_FEDIFY_SOCKET,CLAIRE_FEDIFY_SERVICE_KEY_FILE,CLAIRE_FEDIFY_DB,CLAIRE_FEDIFY_HOST,CLAIRE_FEDIFY_PORT", "src/main.ts"]
