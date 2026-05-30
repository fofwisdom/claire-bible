# claire_bible

텔레그램으로 던진 링크/문서/키워드를 **스크랩 → Gemini로 구조화 → 팔란티어식 타입 온톨로지 그래프**로 적재하고,
새 자료를 **기존에 쌓인 그래프와 연결**하며, 나중에 키워드로 **검색 → LLM 정리**해 보여주는 개인 지식베이스.

설계 상세는 [PLAN.md](PLAN.md), 조사 근거는 [research/RESEARCH_NOTES.md](research/RESEARCH_NOTES.md) 참고.

## 상태

M0 스캐폴드 진행 중. Gemini 키 도착 전까지 **mock provider**로 동작.

## 빠른 시작

```bash
uv sync                      # 의존성 설치
cp .env.example .env         # 토큰/키 채우기 (없으면 mock으로 동작)
uv run claire doctor         # 환경/벡터백엔드 점검
uv run claire ingest "https://example.com/article"   # 단건 적재 (CLI)
uv run claire bot            # 텔레그램 봇 실행 (long-polling)
```

## 구조

```
src/claire/
  config.py        설정(.env)
  cli.py           CLI 진입점 (doctor / ingest / bot / search)
  telegram_bot.py  텔레그램 진입점
  ingest/          fetcher 라우터 + normalize + dedup
  ontology/        타입 온톨로지 (코드 인터페이스) + registry(domain/range)
  extract/         Gemini structured 추출 + provider 어댑터(mock/gemini) + resolver
  store/           SQLite(graph+FTS+vec) + vault(.md) export
  expand/          1홉 자동 확장
  retrieval/       하이브리드 검색 + LLM 정리 (후순위)
```
