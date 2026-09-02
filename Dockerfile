FROM python:3.11-slim

WORKDIR /app

# stdout/stderr 라인 버퍼링 해제 → print 로그가 docker logs 로 즉시 흘러나오게.
ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir uv

# nodriver(CDP) 가 JS SPA 렌더링 최후수단으로 쓸 시스템 Chromium + 오디오 스트림 추출용 ffmpeg.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates chromium ffmpeg tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
# stealth extra = scrapling[fetchers] + nodriver, audio extra = yt-dlp[curl-cffi]
# 최신 비디오 플랫폼 시그니처 대응을 위해 yt-dlp는 빌드 시 항상 최신 릴리스로 업그레이드
RUN uv sync --no-dev --extra stealth --extra audio \
    && uv pip install --no-cache -U "yt-dlp[curl-cffi]"

# Runtime processes use the environment built above directly.  uv remains a
# build/development tool rather than an extra process wrapper for every service.
# /host-bin allows optional host CLI tools (like Antigravity agy) to be invoked seamlessly.
ENV PATH="/app/.venv/bin:/host-bin:$PATH" \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_DIR=/etc/ssl/certs \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# 데이터/볼트는 볼륨 마운트(이미지 미포함). 기본 명령은 compose 에서 override.
CMD ["claire", "bot"]
