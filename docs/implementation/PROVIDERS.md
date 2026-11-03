# LLM·STT·VLM 프로바이더 구현 정본

기준 커밋: P4 프로바이더 정책과 BYOK 구현 (2026-11-04) · 대상 독자: 프로바이더를 고치거나 추가하는 에이전트와 운영자 · 원칙: [`PRINCIPLES.md`](../PRINCIPLES.md) C2 · 설정값 목록: [`ENVIRONMENT_VARIABLES.md`](ENVIRONMENT_VARIABLES.md) · 설계 배경: [MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md](../design/MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) §10, §11, §13.3 (Rev. 2.3)

이 문서는 **지금 코드가 어떤 프로바이더를 어떻게 분류·선택·격리하고 호출하는지**를 적는다. 줄 번호 대신 파일 경로와 함수·상수 이름을 기준점으로 삼는다.

- **[코드]** 기준 작업 트리의 소스에서 직접 확인한 사실
- **[실측]** 실행해서 확인한 값(조건을 함께 적음)
- **[결정]** 저장소 소유자가 정한 방침
- **[미검증]** 확인하지 못한 것

---

## 0. 작업 전에 알아 둘 것

1. **[결정] 운영 환경에서는 mock으로 대체하지 않는다.** `CLAIRE_ENVIRONMENT=production`에서 설정한 프로바이더를 쓸 수 없으면 호출이 `ProviderUnavailableError`로 실패한다(§3). 개발 환경의 mock 대체는 허용한다.
2. **[코드] 프로바이더 전역 설정의 실제 출처는 `data/providers.json`이다.** 이 파일의 값은 export된 환경변수보다도 우선한다(§2).
3. **[코드] 계정별 프로바이더는 `ProviderContext`가 결정한다.** 작업마다 테마 소유 계정에 맞춰 관리자(`admin`), 멤버 BYOK(`byok`), 서버 등급(`tier`), 미설정(`none`) 중 하나로 확정된다(§3.2). 멤버는 어떤 경로로도 `host-cli`에 닿지 않는다.
4. **[코드] 폴백은 없다 (C2).** 멤버 키가 거부되거나 예산이 소진되어도 서버 등급이나 관리자 CLI로 전환하지 않고 fail-closed로 실패한다(§3.3).
5. **[코드] `Settings`를 만들면 디스크가 바뀐다.** `_ProvidersJsonSettingsSource`가 `ProviderManager(auto_migrate=True)`를 만든다. 이때 `providers.json`이 없으면 환경변수로부터 새로 만들고, 저장소 루트의 `.env`·`.env.dev`에서 `MIGRATED_ENV_VARS`에 속한 줄을 주석 처리한다.
6. **[코드] 테스트는 개발자의 실제 `.env`를 읽는다.** conftest가 없다. 로컬 `.env`가 `production`이면 테스트도 운영 동작을 본다. mock 대체를 검증하는 테스트는 `Settings(environment="development")`로 환경을 고정한다.

---

## 1. 파일 지도

| 파일 | 역할 | 주요 기호 |
| :--- | :--- | :--- |
| `src/claire/accounts/provider_policy.py` | 프로바이더 분류, ServerTier, ProviderContext, 기능 게이트, effort 제한 | `classify_provider`, `ProviderContext`, `ServerTier`, `UsageHooks`, `admin_context`, `resolve_provider_context`, `settings_for_context`, `context_of`, `require_provider_allowed`, `require_feature`, `clamp_effort`, `ProviderPolicyError` |
| `src/claire/accounts/server_tier.py` | 서버 등급 로드/저장/검증, 사용량 확인, 동시성 슬롯, 사용량 훅 | `load_server_tier`, `save_server_tier`, `check_ingest_allowed`, `check_tokens_available`, `check_theme_quota`, `check_document_quota`, `job_slot`, `usage_hooks_for`, `record_usage` |
| `src/claire/store/provider_keys.py` | 멤버 BYOK API 키 암호화 저장소, 시험 호출 검증 | `ProviderKeyMeta`, `ActiveKey`, `put_key`, `load_active_key`, `verify_key`, `delete_key` |
| `src/claire/store/usage_ledger.py` | auth.db의 account_usage UTC 일/월별 원장 집계 | `UsageSnapshot`, `utc_period`, `utc_day`, `get_usage`, `record_tokens`, `try_reserve_ingest`, `tokens_remaining` |
| `src/claire/secret_scrub.py` | API 키 등 비밀 누출 방지 및 로그/예외 마스킹 | `register_secret`, `scrub_text`, `scrub_exception`, `SecretScrubFilter`, `install_log_scrubber` |
| `src/claire/store/vectors.py` | 테마별 임베딩 모델 고정 및 검색 필터링 | `EmbedPin`, `EmbeddingModelMismatch`, `read_embed_pin`, `replace_theme_embeddings`, `VectorStore.search` |
| `src/claire/store/theme.py` | 테마 메타데이터 및 임베딩 고정 필드 보존 | `ThemeInfo`, `embed_provider`, `embed_model` |
| `src/claire/config.py` | 전역 프로바이더 설정 및 사용 가능 판정 | `Settings.effective_provider`, `effective_stt_provider`, `effective_vlm_provider`, `provider_unavailable_reason`, `is_production`, `ProviderUnavailableError`, `PROVIDER_UNAVAILABLE`, `find_agy_executable`, `find_codex_executable` |
| `src/claire/provider_manager.py` | data/providers.json 읽기·쓰기, 환경변수 마이그레이션, WebUI 연결 검증 | `ProviderManager`, `DEFAULT_PROVIDERS_CONFIG`, `MIGRATED_ENV_VARS`, `SUPPORTED_ACTIVE_PROVIDERS`, `get_settings_dict`, `migrate_from_env_if_needed`, `test_connection` |
| `src/claire/extract/provider.py` | LLM 프로토콜과 팩토리 | `Provider`(Protocol), `MockProvider`, `UnavailableProvider`, `get_provider` |
| `src/claire/extract/gemini_provider.py` | Gemini API, 자격 지문별 호출 락, 키 거부 판정 | `GeminiProvider`, `_call`, `_call_state`, `_CALL_LOCK`, `_LAST_CALL`, `_is_key_rejection`, `_is_retryable`, `_is_daily_quota` |
| `src/claire/extract/antigravity_provider.py` | agy CLI 서브프로세스 (관리자 전용) | `AntigravityProvider`, `_run_cli`, `EMBED_DIM` |
| `src/claire/extract/codex_provider.py` | codex CLI 서브프로세스 (관리자 전용) | `CodexProvider`, `_shared_semaphore` |
| `src/claire/extract/classifier.py` | 저비용 분류용 프로바이더 선택 | `get_lowest_effort_provider`, `parse_effort_score` |
| `src/claire/extract/transcript/factory.py` | STT 팩토리 | `get_transcript_provider`, `UnavailableTranscriptProvider` |
| `src/claire/extract/vision/factory.py` | VLM 팩토리 | `get_vision_provider`, `UnavailableVisionProvider` |

---

## 2. 설정과 컨텍스트가 결정되는 순서

### 2.1 전역 설정 (`providers.json`)

**[코드]** `Settings.settings_customise_sources`의 우선순위는 다음과 같다(앞이 이긴다).

1. 생성자 인자 (`Settings(provider="codex")`)
2. `data/ingest_limits.json`
3. `data/providers.json` (`ProviderManager.get_settings_dict`)
4. 프로세스 환경변수
5. `.env` 파일

`providers.json`이 없으면 `migrate_from_env_if_needed`가 환경변수와 `.env`·`.env.dev`에서 `MIGRATED_ENV_VARS`를 모아 파일을 만든다. 그 뒤로는 파일 값이 이긴다. 기본값은 `DEFAULT_PROVIDERS_CONFIG` 값이 실제 기본값이다.

- 차이가 있는 키는 하나다. `CLAIRE_GEMINI_EMBED_MODEL`의 `Settings` 기본값은 `text-embedding-004`이지만, 실제로는 `gemini-embedding-001`이 쓰인다.
- VLM 설정(`CLAIRE_ENABLE_VLM_TRANSCRIPTION`, `CLAIRE_VLM_PROVIDER`, `CLAIRE_VLM_MODEL`)은 `providers.json`에 없고 환경변수로만 정한다.

### 2.2 작업별 `ProviderContext`

작업마다 테마 → 계정 → `ProviderContext` 순서로 프로바이더를 정한다(`resolve_provider_context`).
1. **관리자 (`account_id=None` 또는 관리자 계정)**: `admin_context(settings)`를 반환하며 `providers.json`의 프로바이더와 키를 그대로 쓴다.
2. **멤버, 자기 키 있음**: `source="byok"`. 등록된 API 키의 프로바이더(`gemini`)를 쓴다.
3. **멤버, 자기 키 없음**: `source="tier"`. 관리자가 정의한 서버 등급(`load_server_tier`)의 설정을 쓴다.
4. **서버 등급이 `none`(D6 기본값)**: `source="none"`, `provider="none"`. LLM 단계가 `ProviderNotConfigured`로 실패한다.

`settings_for_context(settings, ctx)`는 기본 `Settings`의 사본에 컨텍스트(`provider`, `api_key`, `model`, `effort`, `stt_provider`, `vlm_provider`, 기능 플래그)를 입힌 새 설정을 반환하며, 기존 팩토리(`get_provider` 등)가 이 사본을 받아 동작한다.

---

## 3. 프로바이더 정책과 분류

### 3.1 프로바이더 분류 (Provider Classification)

**[코드]** `classify_provider`(`src/claire/accounts/provider_policy.py`)가 이름에 따라 3개 부류로 나눈다(설계 §10.1).

| 분류 | 프로바이더 | 실행 방식 | 쓸 수 있는 계정 |
| :--- | :--- | :--- | :--- |
| `host-cli` | `antigravity`(agy), `codex`(codex-cli) | 서버 호스트의 바이너리, 호스트 로그인 | 관리자만 |
| `api-key` | `gemini` (`openai`는 구현 후) | HTTPS API와 키 | 관리자: `providers.json` / 멤버: 자기 키(BYOK) 또는 서버 등급 |
| `mock` | `mock` | 개발/테스트용 | 개발 환경 |

- **미등록 프로바이더 fail-closed**: 분류표에 없는 이름이나 빈 문자열은 모두 `host-cli`로 간주한다(`classify_provider`).
- **멤버 차단**: 멤버 컨텍스트에서 `host-cli` 프로바이더를 생성하거나 호출하려 하면 `ProviderNotAllowed` 예외가 발생한다(`require_provider_allowed`).
- **저비용 분류**: `get_lowest_effort_provider`는 멤버 컨텍스트에서 `codex`·`antigravity` 후보를 아예 생성하지 않는다.

### 3.2 계정별 컨텍스트와 기능 게이트

- **계정 상태 검사**: 계정의 상태가 `active` 또는 `setup_required`가 아니면(정지 `suspended`, 삭제 대기 `deleting`, 소각 `purged` 등) `ProviderNotAllowed`로 모든 프로바이더 접근이 차단된다.
- **관리자 전용 기능 게이트**: `research`(`allow_tools=True`), Jev 평가(`jev`), 텔레그램 봇(`telegram`)은 관리자 전용 기능이며 멤버에게는 항상 거부된다(`require_feature`).
- **서버 등급 기능 게이트**: 서버 등급 컨텍스트(`source="tier"`)는 관리자가 `tier.features`에 명시한 기능(`expand`, `stt`, `vlm`, `presentation`, `synthesize`)만 허용된다. BYOK 멤버는 등급 기능 5종 전체를 쓸 수 있다.
- **effort 제한**: 서버 등급 컨텍스트의 effort는 `tier.max_effort`를 넘지 않도록 상한으로 낮춘다(`clamp_effort`). 관리자와 BYOK 컨텍스트는 effort를 낮추지 않는다.
- **동시성 제한**: BYOK 컨텍스트는 서버 등급의 `byok_caps.max_concurrency`로 계정별 동시 LLM 작업 수를 제한한다(`job_slot`).

### 3.3 폴백 없음 원칙 (No Fallbacks, Principle C2)

- 멤버의 API 키가 인증 실패(401/403) 등으로 거부되면 `ProviderKeyRejected`가 발생한다. **서버 등급이나 호스트 CLI로 자동 폴백하지 않는다.**
- 멤버의 일 적재 한도나 월 토큰 예산이 소진되면 `ProviderBudgetExceeded`가 발생하며 실패한다.
- 서버 등급이 `none`인 멤버가 LLM 작업을 시도하면 `ProviderNotConfigured`로 실패한다.
  - 원문 저장과 FTS 색인은 정상 동작하며(A1), `raw_inbox`에 오류로 남는다. 키를 등록한 뒤 `replay-failed`로 다시 적재할 수 있다.
- 모든 정책 예외(`ProviderPolicyError`)는 기계 판독용 `code`와 사용자 화면용 복구 안내 문구인 `remedy`를 제공한다.

### 3.4 STT·VLM

`enable_video_transcription`·`enable_vlm_transcription`이 꺼져 있으면 `mock`이다. 호출 측(`ingest/fetchers/*`)이 같은 플래그로 먼저 막으므로 이 mock은 실제로 호출되지 않는다. 켜져 있으면 다음과 같다.
- `gemini`(`google`): 키가 있으면 `gemini`, 없으면 쓸 수 없음
- `mock`: `mock`
- 그 밖의 값: 쓸 수 없음. `antigravity` 같은 STT 미지원 프로바이더가 여기에 해당한다

---

## 4. 프로바이더별 동작

### 4.1 Gemini (`GeminiProvider`)

- **[코드] 자격 지문별 호출 락 분리**: 모든 호출은 `_call()`을 거친다. 프로세스 전역 잠금 대신 자격 지문(`credential_key`: `"admin"`, `"byok:<fingerprint>"`, `"tier"`) 단위로 `_CallState`를 분리하여 격리한다(`_call_state(credential_key)`).
  - 한 계정의 BYOK 호출이나 429 레이트 리밋 백오프가 다른 계정이나 관리자의 호출을 블로킹하지 않는다.
  - 하위 호환을 위해 `_CALL_LOCK`, `_LAST_CALL`은 관리자(`"admin"`)의 throttle 상태를 가리킨다.
- **[코드] 재시도와 키 거부**:
  - `_is_key_rejection`(401, 403, 유효하지 않은 키)이면 재시도하지 않고 바로 `ProviderKeyRejected`로 실패한다.
  - `_is_retryable`(429, 500, 503, `RESOURCE_EXHAUSTED`, `UNAVAILABLE`)이면 최대 `CLAIRE_GEMINI_MAX_RETRIES`회 지수 백오프로 재시도한다.
  - `_is_daily_quota`(일일 quota·결제 소진)이면 재시도하지 않고 바로 실패한다.
- **[코드] 사용량 훅 (`UsageHooks`)**: 호출 직전 `before_call`로 예산 초과 여부를 점검하고, 호출 완료 후 `after_call`로 소비된 토큰(입력/출력)을 `account_usage` 원장에 가산한다.

### 4.2 Antigravity CLI (`AntigravityProvider`)

- **[코드] 관리자 전용**: 호스트 바이너리(`CLAIRE_AGY_BIN`)를 호스트 로그인 상태로 실행한다. 멤버 컨텍스트에서는 생성이 거부된다.
- **[코드] 동시성**: 인스턴스마다 `threading.Semaphore(CLAIRE_AGY_MAX_CONCURRENCY)`를 둔다.
- **[코드] 격리**: `allow_tools=False`(기본)이면 "도구 금지" 지시문을 붙이고 `tempfile.gettempdir()/claire_agy_clean`을 작업 디렉터리로 쓴다. `research`만 `allow_tools=True`로 호출한다.
- **[코드] 임베딩**: `GEMINI_API_KEY`가 있으면 Gemini 임베딩을 직접 호출한다. 키가 없으면 개발 환경은 결정론적 의사 벡터(`EMBED_DIM`)를 돌려주고 운영 환경은 `ProviderUnavailableError`를 던진다.

### 4.3 Codex CLI (`CodexProvider`)

- **[코드] 관리자 전용**: 호스트 바이너리(`CLAIRE_CODEX_BIN`)를 호스트 로그인 상태로 실행한다. 멤버 컨텍스트에서는 생성이 거부된다.
- **[코드] 동시성**: `(바이너리, 한도)` 단위의 프로세스 공유 `BoundedSemaphore`(`_shared_semaphore`)로 제한한다.
- **[코드] 격리**: 호출마다 임시 디렉터리(`claire-codex-`)를 작업 디렉터리로 쓰고 프롬프트는 stdin/임시 파일로 전달한다.
- **[코드] 임베딩**: Codex에는 임베딩 기능이 없다. `GEMINI_API_KEY`가 있으면 `GeminiProvider`에 위임하고 없으면 `RuntimeError`를 던진다.

### 4.4 테마별 임베딩 모델 고정 (Theme Embedding Pinning)

**[코드]** `src/claire/store/vectors.py`, `src/claire/store/theme.py`. 설계 §10.4 (P4 L4-a3).

- **테마별 공간 고정**: `themes` 테이블에 `embed_provider`, `embed_model` 열을 두어 테마 단위로 임베딩 모델 공간을 고정한다.
- **모델 혼합 거부**: 테마에 고정된 모델과 다른 모델로 생성된 벡터를 저장하려 하면 `EmbeddingModelMismatch` 예외를 내며 저장을 거부한다.
- **검색 필터링**: `VectorStore.search`는 테마의 고정 모델(`themes.embed_model`)과 일치하는 `embeddings.model` 행만 조회한다.
  - 검색 질의 모델(`query_model`)이 테마의 고정 모델과 다르면 벡터 공간이 달라 유사도 비교가 불가능하므로 빈 결과를 반환한다.
- **Mock 모델 격리**: `MockProvider`의 의사 임베딩 모델명은 `mock-sha256`으로 명시하여 실제 모델 공간과 격리한다.
- **재임베딩 (re-embed)**: 프로바이더나 키 변경으로 모델이 바뀌면 `claire reembed -t <theme>`(`replace_theme_embeddings`)으로 해당 테마의 벡터를 새로운 고정 모델로 일괄 재구축해야 한다. `reset_theme` 시에는 테마 임베딩 고정이 해제된다.
- **공개 테마 검색 격리**: 네임스페이스 소유자가 아닌 타 계정 또는 익명 사용자의 검색은 임베딩 비용 전가를 막기 위해 FTS 전용으로 동작한다.

### 4.5 저비용 분류 (`get_lowest_effort_provider`)

**[코드]** `classify_paper` 같은 1차 분류는 effort 점수가 가장 낮은 프로바이더를 고른다.
- 후보: Codex(운영 프로바이더로 명시되고 바이너리가 있을 때), Antigravity(바이너리가 있거나 명시됐을 때), Gemini(키가 있거나 명시됐을 때).
- **멤버 컨텍스트 제한**: 멤버 컨텍스트에서는 `codex`·`antigravity`를 후보에 올리지 않고 멤버의 사용 가능 프로바이더(`gemini`)만 평가한다.
- 동점 우선순위: codex → antigravity → gemini.

---

## 5. 서버 등급 (ServerTier) 및 사용량 원장

**[코드]** `src/claire/accounts/server_tier.py`, `src/claire/store/usage_ledger.py`. 설계 §10.3 (P4 L4-c).

### 5.1 서버 등급 (`ServerTier`)

- **저장 위치**: `claire.db`의 `instance_settings` 테이블 (`key='server_tier'`). JSON 형식으로 직렬화된다.
- **기본값 (D6 `none`)**:
  - `provider`: `"none"` (기본값) 또는 `"gemini"` (`TIER_PROVIDERS`)
  - `model`: 프로바이더 모델명 (기본값 `""`)
  - `monthly_tokens`: 월간 LLM 토큰 상한 (기본값 `0`)
  - `daily_ingests`: 일일 적재 상한 (기본값 `20`)
  - `max_effort`: 허용 최대 effort (기본값 `"low"`)
  - `features`: 허용 기능 집합 (기본값 빈 집합. `expand`, `stt`, `vlm`, `presentation`, `synthesize`)
  - `storage_mb`: 계정별 저장 용량 상한 MB (기본값 `2048`)
  - `max_themes`: 계정별 최대 테마 수 (기본값 `5`)
  - `max_documents`: 계정별 최대 문서 수 (기본값 `5000`)
  - `byok_caps`: BYOK 계정의 서버 자원 상한 (`{"max_concurrency": 1}`)
- **자원 한도 공통 적용**: 서버 자원 한도(`storage_mb`, `max_themes`, `max_documents`, `daily_ingests`)는 본인 키가 있는 BYOK 멤버에게도 동일하게 적용된다.
- **유효성 검증 및 보호**:
  - `ServerTier.from_dict`가 필드와 범위를 엄격히 검증하며, 잘못된 JSON이나 미지원 프로바이더는 `provider="none"`으로 fail-closed 처리한다.
- **관리 경로**: 관리자 세션에서 `GET /api/admin/server-tier`, `PUT /api/admin/server-tier`를 통해 설정 및 조회할 수 있으며, `/admin/instance` 화면에 관리 패널이 제공된다.

### 5.2 계정별 UTC 사용량 원장 (`account_usage`)

- **저장 위치**: `data/auth/auth.db`의 `account_usage` 테이블 (v2 스키마).
- **컬럼 구성**: `account_id`, `period` (UTC 'YYYY-MM'), `tokens_in`, `tokens_out`, `ingests`, `daily_day` (UTC 'YYYY-MM-DD'), `daily_ingests`, `updated_at`.
- **UTC 기준 집계**: 모든 월간 및 일간 집계는 서버 로컬 시각이 아닌 **UTC 기준**으로 처리된다. 날짜가 바뀌면 `daily_ingests`는 0부터 다시 계산된다.
- **원자적 갱신**: 사용량 기록(`record_tokens`)과 일 적재 예약(`try_reserve_ingest`)은 단일 SQL `UPDATE` 문을 통해 원자적으로 수행되어 경쟁 상태를 방지한다.
- **적재 예약 규칙**:
  - 신규 문서 적재 시 `IngestService`가 `try_reserve_ingest`를 호출하여 일 적재 한도를 선확인 및 차감한다.
  - 재생성(`regenerate`), 기존 문서 재적재(`reingest`), recover 재시도는 `daily_ingests` 카운트에 포함하지 않는다(월 토큰만 기록).
- **BYOK 토큰 계측**: BYOK 계정이 소비한 토큰도 원장에 누적 기록되나, 월 토큰 예산 초과 차단은 서버 등급 컨텍스트(`source="tier"`)에만 적용된다.

---

## 6. 프로바이더를 추가할 때

1. `extract/<name>_provider.py`에 `Provider` 프로토콜의 메서드를 모두 구현한다.
2. `src/claire/accounts/provider_policy.py`의 `PROVIDER_CLASSES`에 분류(`host-cli` 또는 `api-key`)를 등록한다. 미등록 시 `host-cli`로 fail-closed 된다.
3. 멤버 키로 지원하려면 `MEMBER_KEY_PROVIDERS`에 추가하고, 서버 등급으로 지원하려면 `TIER_PROVIDERS`에 추가한다.
4. `get_provider` 및 `resolve_provider_context`에 분기를 추가하고, `Settings._llm_unavailable_reason`에 사용 가능 조건을 추가한다.
5. WebUI에서 고를 수 있게 하려면 `SUPPORTED_ACTIVE_PROVIDERS`, `DEFAULT_PROVIDERS_CONFIG`, `get_settings_dict`, `migrate_from_env_if_needed`를 함께 고친다.
6. 새 `Settings` 필드는 [`ENVIRONMENT_VARIABLES.md`](ENVIRONMENT_VARIABLES.md)에 행을 추가한다. 빠뜨리면 `tests/test_docs_consistency.py`가 실패한다.
7. 운영 환경 실패 동작 및 정책 검속 동작을 `tests/test_provider_policy.py`에 추가한다.

---

## 7. 알려진 한계와 미검증

- **[코드] `openai`**: WebUI 설정, 연결 검증(`ProviderManager.test_connection`), 마이그레이션 및 `api-key` 분류 매핑은 있지만 LLM 프로바이더 구현이 없다. 따라서 멤버 키 등록 목록(`MEMBER_KEY_PROVIDERS`) 및 서버 등급 선택지에서 제외되어 있다.
- **[코드] 임베딩 모델 기본값 불일치**: §2 참고. 실제 기본값(`gemini-embedding-001`)과 `Settings` 필드 기본값(`text-embedding-004`)이 다르다. 모델을 바꾸면 기존 벡터와 공간이 달라지므로 `re-embed`가 필요하다.
- **[코드] BYOK 멤버 모델/effort 설정**: 현재 BYOK 멤버의 LLM 모델과 effort는 관리자 기본값을 상속받으며, 멤버별 세부 설정 UI는 후속 과제다.
- **[코드] 서버 등급 `none` 멤버 테마의 recover 루프**: 서버 등급이 `none`인 멤버의 테마에 적재 실패 문서가 남아 있을 경우, recover 재시도가 최대 시도 횟수까지 소모된 뒤 영구 실패로 마킹된다.
- **[미검증]** 텔레그램 봇과 백그라운드 루프가 운영 환경에서 `unavailable`일 때 사용자에게 어떤 메시지를 보여 주는지는 확인하지 않았다. `raw_inbox` 오류 기록까지만 확인했다.
