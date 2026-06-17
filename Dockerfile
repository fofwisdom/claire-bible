FROM python:3.11-slim

WORKDIR /app

# stdout/stderr 라인 버퍼링 해제 → print 로그가 docker logs 로 즉시 흘러나오게.
ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
# stealth extra = scrapling[fetchers] (curl-cffi/browserforge). Fetcher 헤더위장으로
# 봇차단(openai.com 403 등) 우회 — 브라우저 바이너리는 미설치(StealthyFetcher만 필요).
RUN uv sync --frozen --no-dev --extra stealth 2>/dev/null || uv sync --no-dev --extra stealth

# 데이터/볼트는 볼륨 마운트(이미지 미포함). 기본 명령은 compose 에서 override.
CMD ["uv", "run", "claire", "bot"]
