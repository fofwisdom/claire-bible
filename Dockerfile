FROM python:3.11-slim

WORKDIR /app

# stdout/stderr 라인 버퍼링 해제 → print 로그가 docker logs 로 즉시 흘러나오게.
ENV PYTHONUNBUFFERED=1

# 빌드 도구 자체도 버전을 고정하고 설치 결과를 빌드 로그에서 확인한다.
RUN python -m pip install --no-cache-dir "uv==0.11.29" \
    && uv --version

# nodriver(CDP) 가 JS SPA 렌더링 최후수단으로 쓸 시스템 Chromium. Playwright 자체 브라우저
# 다운로드(+deps 별도설치)보다 가벼움 — apt 패키지 하나로 해결.
RUN apt-get update && apt-get install -y --no-install-recommends chromium \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
# stealth extra = scrapling[fetchers](curl-cffi/browserforge, 봇차단 403 우회) + nodriver
# (CDP 로 위 apt chromium 을 직접 제어, JS 렌더링 최후수단).
RUN uv sync --locked --no-dev --extra stealth

# 런타임에는 빌드된 가상환경만 사용하고 uv run 이 재동기화하거나 lock 을 갱신하지 않게 한다.
ENV UV_NO_SYNC=1

# 데이터/볼트는 볼륨 마운트(이미지 미포함). 기본 명령은 compose 에서 override.
CMD ["uv", "run", "claire", "bot"]
