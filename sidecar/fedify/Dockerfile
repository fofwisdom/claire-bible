# Fedify sidecar image (design Section 4.2). The Python image of Claire has no JavaScript runtime: this image is separate.
# Build context: sidecar/fedify/
FROM denoland/deno:2.9.8

WORKDIR /app
COPY deno.json deno.lock THIRD_PARTY_LICENSES.md ./
COPY src ./src
# The frozen lock file fixes each dependency version (design Section 13, FS11).
# The image keeps the license files of the npm dependencies in /app/licenses (design Section 4.2). The JSR packages have no license file: THIRD_PARTY_LICENSES.md records their licenses.
# Compose starts the process with the uid and gid of the Claire services (CB_UID, CB_GID), not with the user `deno`. Thus all users must be able to read the module cache and the application files. No user other than root can write them.
RUN deno cache --frozen src/main.ts \
    && mkdir -p /app/licenses \
    && cd "$DENO_DIR/npm/registry.npmjs.org" \
    && find . -mindepth 3 -maxdepth 4 -type f \( -iname 'licen[sc]e*' -o -iname 'notice*' -o -iname 'copying*' \) -exec cp --parents {} /app/licenses/ \; \
    && chmod -R a+rX "$DENO_DIR" /app

USER deno
EXPOSE 8790
# The process can use the network, the data directory, and the socket directory only.
CMD ["run", "--frozen", "--cached-only", "--allow-net", "--allow-read=/data,/app", "--allow-write=/data/fedify,/data/run/federation.sock", "--allow-env=CLAIRE_FEDIFY_SOCKET,CLAIRE_FEDIFY_SERVICE_KEY_FILE,CLAIRE_FEDIFY_DB,CLAIRE_FEDIFY_HOST,CLAIRE_FEDIFY_PORT", "src/main.ts"]
