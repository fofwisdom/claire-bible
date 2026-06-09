FROM python:3.11-slim

WORKDIR /app

# stdout/stderr 라인 버퍼링 해제 → print 로그가 docker logs 로 즉시 흘러나오게.
ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev 2>/dev/null || uv sync --no-dev

# 데이터/볼트는 볼륨 마운트(이미지 미포함). 기본 명령은 compose 에서 override.
CMD ["uv", "run", "claire", "bot"]
