# LLM·STT·VLM 프로바이더 구현 정본

기준 커밋: `5cd0d23` (2026-11-02) · 대상 독자: 프로바이더를 고치거나 추가하는 에이전트와 운영자 · 원칙: [`PRINCIPLES.md`](../PRINCIPLES.md) C2 · 설정값 목록: [`ENVIRONMENT_VARIABLES.md`](ENVIRONMENT_VARIABLES.md)

이 문서는 **지금 코드가 어떤 프로바이더를 어떻게 고르고 호출하는지**를 적는다. 줄 번호 대신 파일 경로와 함수·상수 이름을 기준점으로 삼는다.

- **[코드]** 기준 커밋의 소스에서 직접 확인한 사실
- **[실측]** 실행해서 확인한 값(조건을 함께 적음)
- **[결정]** 저장소 소유자가 정한 방침
- **[미검증]** 확인하지 못한 것

---

## 0. 작업 전에 알아 둘 것

1. **[결정] 운영 환경에서는 mock으로 대체하지 않는다.** `CLAIRE_ENVIRONMENT=production`에서 설정한 프로바이더를 쓸 수 없으면 호출이 `ProviderUnavailableError`로 실패한다(§3). 개발 환경의 mock 대체는 허용한다.
2. **[코드] 프로바이더 설정의 실제 출처는 `data/providers.json`이다.** 이 파일의 값은 export된 환경변수보다도 우선한다(§2).
3. **[코드] `Settings`를 만들면 디스크가 바뀐다.** `_ProvidersJsonSettingsSource`가 `ProviderManager(auto_migrate=True)`를 만든다. 이때 다음 일이 일어난다.
   - `providers.json`이 없으면 환경변수로부터 새로 만든다.
   - 저장소 루트의 `.env`·`.env.dev`에서 `MIGRATED_ENV_VARS`에 속한 줄을 주석 처리한다. 파일이 이미 있어도 마찬가지다.
   - 테스트에서 `Settings()`를 만들 때도 같은 일이 일어난다.
4. **[코드] 테스트는 개발자의 실제 `.env`를 읽는다.** conftest가 없다. 로컬 `.env`가 `production`이면 테스트도 운영 동작을 본다. mock 대체를 검증하는 테스트는 `Settings(environment="development")`로 환경을 고정한다.

---

## 1. 파일 지도

| 파일 | 역할 | 주요 기호 |
| :--- | :--- | :--- |
| `src/claire/config.py` | 프로바이더 선택과 사용 가능 판정 | `Settings.effective_provider`, `effective_stt_provider`, `effective_vlm_provider`, `provider_unavailable_reason`, `is_production`, `ProviderUnavailableError`, `PROVIDER_UNAVAILABLE`, `find_agy_executable`, `find_codex_executable` |
| `src/claire/provider_manager.py` | `data/providers.json` 읽기·쓰기, 환경변수 마이그레이션, WebUI 연결 검증 | `ProviderManager`, `DEFAULT_PROVIDERS_CONFIG`, `MIGRATED_ENV_VARS`, `SUPPORTED_ACTIVE_PROVIDERS`, `get_settings_dict`, `migrate_from_env_if_needed`, `test_connection` |
| `src/claire/extract/provider.py` | LLM 프로토콜과 팩토리 | `Provider`(Protocol), `MockProvider`, `UnavailableProvider`, `get_provider` |
| `src/claire/extract/gemini_provider.py` | Gemini API | `GeminiProvider`, `_call`, `_is_retryable`, `_is_daily_quota`, `_CALL_LOCK`, `_LAST_CALL` |
| `src/claire/extract/antigravity_provider.py` | `agy` CLI 서브프로세스 | `AntigravityProvider`, `_run_cli`, `EMBED_DIM` |
| `src/claire/extract/codex_provider.py` | `codex` CLI 서브프로세스 | `CodexProvider`, `_shared_semaphore` |
| `src/claire/extract/classifier.py` | 저비용 분류용 프로바이더 선택 | `get_lowest_effort_provider`, `parse_effort_score` |
| `src/claire/extract/transcript/factory.py` | STT 팩토리 | `get_transcript_provider`, `UnavailableTranscriptProvider` |
| `src/claire/extract/vision/factory.py` | VLM 팩토리 | `get_vision_provider`, `UnavailableVisionProvider` |

---

## 2. 설정이 결정되는 순서

**[코드]** `Settings.settings_customise_sources`의 우선순위는 다음과 같다(앞이 이긴다).

1. 생성자 인자 (`Settings(provider="codex")`)
2. `data/ingest_limits.json`
3. `data/providers.json` (`ProviderManager.get_settings_dict`)
4. 프로세스 환경변수
5. `.env` 파일

**[코드]** `providers.json`이 없으면 `migrate_from_env_if_needed`가 환경변수와 `.env`·`.env.dev`에서 `MIGRATED_ENV_VARS`를 모아 파일을 만든다. 그 뒤로는 파일 값이 이긴다. 다시 마이그레이션하려면 `providers migrate`를 실행한다(호스트에서는 `./cb-manuscript app providers migrate`). 이 명령은 `migrate_from_env_if_needed(force=True)`를 부른다.

**[실측]** 빈 `CB_DATA_DIR`에서 `CLAIRE_PROVIDER=gemini CLAIRE_AGY_TIMEOUT=99`로 `Settings()`를 만들었다. 결과는 `provider=gemini`, `agy_timeout=99.0`이었고 `providers.json`이 생성되었다.

**[코드] 기본값이 두 군데에 있다.** `providers.json`이 관리하는 키는 `DEFAULT_PROVIDERS_CONFIG` 값이 실제 기본값이다. `Settings` 필드 기본값은 쓰이지 않는다.
- 차이가 있는 키는 하나다. `CLAIRE_GEMINI_EMBED_MODEL`의 `Settings` 기본값은 `text-embedding-004`이지만, 실제로는 `gemini-embedding-001`이 쓰인다.
- `tests/test_docs_consistency.py`는 두 기본값을 합친 실제 기본값으로 환경변수 문서를 검사한다.

**[코드] VLM 설정은 `providers.json`에 없다.** `CLAIRE_ENABLE_VLM_TRANSCRIPTION`, `CLAIRE_VLM_PROVIDER`, `CLAIRE_VLM_MODEL`은 환경변수로만 정한다.

---

## 3. 프로바이더 선택과 "쓸 수 없음"

### 3.1 LLM (`effective_provider`)

| `CLAIRE_PROVIDER` | 쓸 수 있는 조건 | 결과 |
| :--- | :--- | :--- |
| `mock` | 항상 | `mock` |
| `gemini` | `GEMINI_API_KEY`가 있음 | `gemini` |
| `antigravity`, `agy` | `find_agy_executable(CLAIRE_AGY_BIN)`이 경로를 찾음 | `antigravity` |
| `codex`, `codex-cli` | `find_codex_executable(CLAIRE_CODEX_BIN)`이 경로를 찾음 | `codex` |
| `openai` | 없음. WebUI 선택지(`SUPPORTED_ACTIVE_PROVIDERS`)에만 있고 구현이 없다 | 쓸 수 없음 |
| 그 밖의 값 | 없음 | 쓸 수 없음 |

쓸 수 없으면 개발 환경은 `mock`, 운영 환경은 `unavailable`이 된다(`Settings._unavailable_or_mock`). 이유 문자열은 `provider_unavailable_reason("llm")`이 돌려준다. `agy`를 찾지 못하면 텔레메트리에 `env_discovery`/`DEGRADED`를 기록한다.

### 3.2 STT·VLM

`enable_video_transcription`·`enable_vlm_transcription`이 꺼져 있으면 `mock`이다. 호출 측(`ingest/fetchers/*`)이 같은 플래그로 먼저 막으므로 이 mock은 실제로 호출되지 않는다. 켜져 있으면 다음과 같다.
- `gemini`(`google`): 키가 있으면 `gemini`, 없으면 쓸 수 없음
- `mock`: `mock`
- 그 밖의 값: 쓸 수 없음. `antigravity` 같은 STT 미지원 프로바이더가 여기에 해당한다

**[코드]** `extract/transcript/antigravity_stt.py`가 있지만 어디서도 import하지 않는다.

### 3.3 운영 환경에서 실패하는 방식

- 팩토리(`get_provider`, `get_transcript_provider`, `get_vision_provider`)는 `unavailable`일 때 예외를 던지지 않는다. 대신 자리표시 객체(`Unavailable*Provider`)를 돌려준다. 그래서 `IngestService` 생성, `claire status`, `app health`, `/api` 설정 조회는 계속 동작하고 `unavailable`로 표시된다.
- 자리표시 객체의 메서드는 호출되면 `ProviderUnavailableError(RuntimeError)`를 던진다.
- **[실측]** 운영 설정에서 `GEMINI_API_KEY` 없이 `IngestService.ingest`를 실행했다. 문서 행은 저장되었고(A1), 엔티티는 0건이었다. `raw_inbox`는 `error` 상태가 되었고 메시지는 `extract failed: LLM 프로바이더를 쓸 수 없다: CLAIRE_PROVIDER=gemini: GEMINI_API_KEY가 비어 있다`였다. 키를 넣은 뒤 `replay-failed`로 다시 적재할 수 있다.
- **[코드]** 수집기는 이 예외를 삼키지 않는다. `web.py`의 VLM 에스컬레이션, `youtube.py`·`video.py`·`naver_clip.py`의 STT 블록이 `except ProviderUnavailableError: raise`를 먼저 둔다. 덕분에 캐시된 오디오가 "손상"으로 오인되어 지워지지 않는다.
- **[코드]** CLI의 `with_agent_contract`는 `RuntimeError`를 종료 코드 4(전제조건 실패)로 바꾼다.

---

## 4. 프로바이더별 동작

### 4.1 Gemini (`GeminiProvider`)

- **[코드]** 모든 호출은 `_call()`을 거친다. 프로세스 전역 잠금(`_CALL_LOCK`, `_LAST_CALL`)으로 호출 간격을 `CLAIRE_GEMINI_MIN_INTERVAL`초 이상으로 유지한다.
- **[코드]** `_is_retryable`(429·5xx·`RESOURCE_EXHAUSTED`·`UNAVAILABLE`·`rate limit`)이면 최대 `CLAIRE_GEMINI_MAX_RETRIES`회 지수 백오프로 재시도한다.
- **[코드]** `_is_daily_quota`(일일 quota·결제 소진)이면 재시도하지 않고 바로 실패한다. 회복은 `recover-loop`의 지수 백오프가 맡는다.

### 4.2 Antigravity CLI (`AntigravityProvider`)

- **[코드]** 인스턴스마다 `threading.Semaphore(CLAIRE_AGY_MAX_CONCURRENCY)`를 둔다. 인스턴스가 여럿이면 전체 동시 실행 수가 이 값을 넘을 수 있다.
- **[코드]** `allow_tools=False`(기본)이면 두 가지를 한다.
  - 프롬프트 앞에 "도구 금지" 지시문을 붙인다.
  - `tempfile.gettempdir()/claire_agy_clean`을 작업 디렉터리로 쓴다. 저장소를 탐색하느라 생기는 지연을 막기 위해서다.
  - `research`만 `allow_tools=True`로 호출한다.
- **[코드]** 상세 생성은 `CLAIRE_AGY_DETAIL_TIMEOUT`, 그 밖의 호출은 `CLAIRE_AGY_TIMEOUT`을 쓴다.
- **[코드] 임베딩**: `GEMINI_API_KEY`가 있으면 Gemini 임베딩을 직접 호출한다. 키가 없으면 개발 환경은 SHA-256 기반 결정론적 의사 벡터(`EMBED_DIM`)를 돌려준다. 운영 환경은 `ProviderUnavailableError`를 던진다. Gemini 호출이 실패했을 때도 같은 규칙을 따른다.

### 4.3 Codex CLI (`CodexProvider`)

- **[코드]** 동시 실행 수는 `(바이너리, 한도)` 단위로 프로세스 전체가 공유하는 `BoundedSemaphore`(`_shared_semaphore`)로 제한한다.
- **[코드]** 호출마다 `tempfile.TemporaryDirectory(prefix="claire-codex-")`를 작업 디렉터리로 쓴다. 프롬프트는 stdin과 임시 파일로 넘긴다(`tests/test_codex_provider.py`의 `test_run_cli_uses_stdin_isolated_temp_files_and_sanitized_environment`).
- **[코드] 임베딩**: Codex에는 임베딩 기능이 없다. `GEMINI_API_KEY`가 있으면 `GeminiProvider`에 위임하고, 없으면 `RuntimeError`를 던진다(환경과 무관).

### 4.4 임베딩 실패를 다루는 방식

**[코드]** 적재 파이프라인(`ingest/pipeline.py`의 `_embed`)은 임베딩 예외를 `None`으로 바꾸고 벡터 없이 진행한다. 이 경우 검색은 FTS만 쓴다. 의사 벡터가 저장되는 경우는 개발 환경의 Antigravity와 `MockProvider`뿐이다.

### 4.5 저비용 분류 (`get_lowest_effort_provider`)

**[코드]** `classify_paper` 같은 1차 분류는 `CLAIRE_PROVIDER`와 다른 프로바이더로 갈 수 있다. 후보를 모아 effort 점수가 가장 낮은 것을 고른다.
- 후보: Codex(운영 프로바이더로 명시되고 바이너리가 있을 때), Antigravity(바이너리가 있거나 명시됐을 때), Gemini(키가 있거나 명시됐을 때)
- 동점 우선순위: codex → antigravity → gemini
- `provider`가 `mock`·`test`면 `MockProvider`다. 후보가 없으면 `get_provider`를 따른다(§3).

---

## 5. 프로바이더를 추가할 때

1. `extract/<name>_provider.py`에 `Provider` 프로토콜의 메서드를 모두 구현한다.
2. `get_provider`에 분기를 추가하고, `Settings._llm_unavailable_reason`에 사용 가능 조건을 추가한다. 조건을 빠뜨리면 운영 환경에서 `unavailable`이 된다.
3. WebUI에서 고를 수 있게 하려면 `SUPPORTED_ACTIVE_PROVIDERS`, `DEFAULT_PROVIDERS_CONFIG`, `get_settings_dict`, `migrate_from_env_if_needed`를 함께 고친다.
4. 새 `Settings` 필드는 [`ENVIRONMENT_VARIABLES.md`](ENVIRONMENT_VARIABLES.md)에 행을 추가한다. 빠뜨리면 `tests/test_docs_consistency.py`가 실패한다.
5. 운영 환경 실패 동작을 `tests/test_provider_unavailable.py`에 추가한다.

---

## 6. 알려진 한계와 미검증

- **[코드] `openai`**: WebUI 설정, 연결 검증(`ProviderManager.test_connection`), 마이그레이션은 있지만 LLM 프로바이더 구현이 없다. 기준 커밋 이전에는 이 값을 고르면 조용히 mock으로 동작했다.
- **[코드] 임베딩 모델 기본값 불일치**: §2 참고. 실제 기본값(`gemini-embedding-001`)과 `Settings` 필드 기본값(`text-embedding-004`)이 다르다. 모델을 바꾸면 기존 벡터와 공간이 달라지므로 `re-embed`가 필요하다.
- **[코드] `Settings()` 생성 시 `.env` 수정**: §0의 3번 참고.
- **[미검증]** 텔레그램 봇과 백그라운드 루프가 운영 환경에서 `unavailable`일 때 사용자에게 어떤 메시지를 보여 주는지는 확인하지 않았다. `raw_inbox` 오류 기록까지만 확인했다.
