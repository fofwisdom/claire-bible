# LLM·STT·VLM 프로바이더 구현 정본

기준 커밋: P4 프로바이더 정책과 BYOK 구현 (2026-11-04), §3.1의 분류와 `AE8` 삭제: `AE8` 커밋 (2026-11-21) · 대상 독자: 프로바이더를 고치거나 추가하는 에이전트와 운영자 · 원칙: [`PRINCIPLES.md`](../PRINCIPLES.md) C2 · 설정값 목록: [`ENVIRONMENT_VARIABLES.md`](ENVIRONMENT_VARIABLES.md) · 설계 배경: [MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md](../design/MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) §10, §11, §13.3 (Rev. 2.3) · 설계 원칙: [MULTI_PROVIDER_DESIGN.md](../design/MULTI_PROVIDER_DESIGN.md), [AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md](../design/AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md) · 텔레메트리: [TELEMETRY.md](TELEMETRY.md)

이 문서는 **지금 코드가 어떤 프로바이더를 어떻게 분류·선택·격리하고 호출하는지**를 적는다. 줄 번호 대신 파일 경로와 함수·상수 이름을 기준점으로 삼는다.

- **[코드]** 기준 작업 트리의 소스에서 직접 확인한 사실
- **[실측]** 실행해서 확인한 값(조건을 함께 적음)
- **[결정]** 저장소 소유자가 정한 방침
- **[미검증]** 확인하지 못한 것

---

## 0. 작업 전에 알아 둘 것

1. **[결정] 운영 환경에서는 mock으로 대체하지 않는다.** `CLAIRE_ENVIRONMENT=production`에서 설정한 프로바이더를 쓸 수 없으면 호출이 `ProviderUnavailableError`로 실패한다(§3). 개발 환경의 mock 대체는 허용한다.
2. **[코드] 프로바이더 전역 설정의 실제 출처는 `data/providers.json`이다.** 이 파일의 값은 export된 환경변수보다도 우선한다(§2).
3. **[코드/결정] 계정별 프로바이더는 `ProviderContext`가 결정한다.** 작업마다 테마 소유 계정에 맞춰 관리자(`admin`), 멤버 BYOK(`byok`), 서버 등급(`tier`), 미설정(`none`) 중 하나로 확정된다(§3.2). 현재 구현된 운영 서버 프로바이더는 `api-key` 분류의 `gemini`이며, `host-cli`(`AntigravityProvider`, `CodexProvider`) 및 ChatGPT·Codex·Claude는 2026-11-20 소유자 결정(`AED8`)으로 폐기되었고, 코드는 `AE8`에서 삭제되었다(2026-11-21). A server administrator account with a personal key also gets `byok` (§3.2). 적재 요청이 `extraction_mode = "client"`를 고른 경우(요청마다 고른다, AED4-rev, [`INGESTION.md`](INGESTION.md) §1 6단계, [`EXTRACTION.md`](EXTRACTION.md) §9) 서버 LLM 프로바이더를 호출하지 않고 원문을 `pending` 상태로 스테이징해 `agent_client` 추출 제출을 기다린다. 이 컨텍스트의 프로바이더가 `none`(키도 서버 등급도 없음)이면 계정의 선택지는 `client`뿐이다.
4. **[코드] 폴백은 없다 (C2).** 멤버 키가 거부되거나 예산이 소진되어도 서버 등급이나 다른 경로로 자동 전환하지 않고 fail-closed로 실패한다(§3.3).
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
| `src/claire/core/secret_scrub.py` | API 키 등 비밀 누출 방지 및 로그/예외 마스킹 | `register_secret`, `scrub_text`, `scrub_exception`, `SecretScrubFilter`, `install_log_scrubber` |
| `src/claire/store/vectors.py` | 테마별 임베딩 모델 고정 및 검색 필터링 | `EmbedPin`, `EmbeddingModelMismatch`, `read_embed_pin`, `replace_theme_embeddings`, `VectorStore.search` |
| `src/claire/store/knowledge_base.py` | 테마 메타데이터 및 임베딩 고정 필드 보존 | `KnowledgeBaseInfo`, `embed_provider`, `embed_model` |
| `src/claire/core/config.py` | 전역 프로바이더 설정 및 사용 가능 판정 | `Settings.effective_provider`, `effective_stt_provider`, `effective_vlm_provider`, `provider_unavailable_reason`, `is_production`, `ProviderUnavailableError`, `PROVIDER_UNAVAILABLE` |
| `src/claire/core/provider_manager.py` | data/providers.json 읽기·쓰기, 환경변수 마이그레이션, WebUI 연결 검증 | `ProviderManager`, `DEFAULT_PROVIDERS_CONFIG`, `MIGRATED_ENV_VARS`, `SUPPORTED_ACTIVE_PROVIDERS`, `get_settings_dict`, `migrate_from_env_if_needed`, `test_connection` |
| `src/claire/extract/provider.py` | LLM 프로토콜과 팩토리 | `Provider`(Protocol), `MockProvider`, `UnavailableProvider`, `get_provider` |
| `src/claire/extract/gemini_provider.py` | Gemini API, 자격 지문별 호출 락, 키 거부 판정 | `GeminiProvider`, `_call`, `_call_state`, `_CALL_LOCK`, `_LAST_CALL`, `_is_key_rejection`, `_is_retryable`, `_is_daily_quota` |
| `src/claire/extract/classifier.py` | 저비용 분류용 프로바이더 선택 | `get_lowest_effort_provider`, `parse_effort_score` |
| `src/claire/extract/transcript/factory.py` | STT 팩토리 | `get_transcript_provider`, `UnavailableTranscriptProvider` |
| `src/claire/extract/vision/factory.py` | VLM 팩토리 | `get_vision_provider`, `UnavailableVisionProvider` |

---

## 2. 설정과 컨텍스트가 결정되는 순서

### 2.1 전역 설정 (`providers.json`)

**[코드]** `Settings.settings_customise_sources`의 우선순위는 다음과 같다(앞이 이긴다).

1. 생성자 인자 (`Settings(provider="gemini")`)
2. `data/ingest_limits.json`
3. `data/providers.json` (`ProviderManager.get_settings_dict`)
4. 프로세스 환경변수
5. `.env` 파일

`providers.json`이 없으면 `migrate_from_env_if_needed`가 환경변수와 `.env`·`.env.dev`에서 `MIGRATED_ENV_VARS`를 모아 파일을 만든다. 그 뒤로는 파일 값이 이긴다. 기본값은 `DEFAULT_PROVIDERS_CONFIG` 값이 실제 기본값이다.

- 모델(`gemini.model`, `gemini.embed_model`, `jev.model`, `stt.model`)은 Web UI에서 정한다. Google AI Studio(`gemini`) 생성 모델은 사고 수준(effort)을 접미사로 인코딩한 `<model>-<effort>` 형태(예: `gemini-3.8-flash-medium`, `gemini-4-argon-high`)로 열거해 선택하며 별도의 UI 추론 수준 선택지를 두지 않는다. `gemini.embed_model`은 기본값이 `gemini-embedding-2`(`3072d`, `v2_prompt`)이며 서버 공용 설정(`ProviderManager.update_config(server_scope=True)`)에서 활성 Google AI Studio 임베딩 모델(`gemini-embedding-2`, `gemini-embedding-001`)만 선택할 수 있고 폐기 모델(`gemini-embedding-2-preview`, `text-embedding-004` 등)과 타 프로바이더는 거부한다. 모델을 정하는 환경 변수는 없다: `Settings`는 환경 변수와 `.env`에서 모델 필드를 읽지 않는다(`config.MODEL_FIELDS`). 이전 판의 `CLAIRE_*_MODEL` 변수는 `providers.json`이 없을 때의 1회 이관에서만 읽힌다. `Settings` 필드 기본값은 `DEFAULT_PROVIDERS_CONFIG` 기본값과 같다.
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

**[코드/결정]** `classify_provider`(`src/claire/accounts/provider_policy.py`)와 `2026-11-20` 소유자 결정(`AED6`, `AED8`). 현재 구현된 운영 프로바이더는 `api-key` 분류의 `gemini`이며 개발·테스트 환경에서는 `mock`을 허용한다.

| 분류 | 프로바이더 | 실행 방식 | 쓸 수 있는 계정 |
| :--- | :--- | :--- | :--- |
| `api-key` | `gemini` (현재 구현 기준선; §6 절차로 추가 가능) | HTTPS API와 키 | 서버 작업: `providers.json` / 관리자 계정: 개인 키, 없으면 `providers.json` / 멤버: 자기 키(BYOK) 또는 서버 등급 |
| `mock` | `mock` | 개발/테스트용 | 개발 환경 |
| `unregistered` | 분류표(`PROVIDER_CLASSES`)에 없는 이름 | 없음 | 아무도 쓸 수 없다 |

- **[결정 2026-11-20 (`AED6`, `AED8`)]**: `host-cli`(`AntigravityProvider`, `CodexProvider`)와 `ChatGPT`, `Codex`, `Claude`는 원칙·설계·구현에서 모두 폐기한다(`AED8`). 기존에 미구현 상태로 적혀 있던 비-Google(`openai`) 항목은 문서에서 삭제했다(`AED6`). 타 `api-key` 프로바이더를 새로 구현·추가하는 일반 확장 구조(§6)는 열려 있다.
- **[코드] `AE8` 삭제 (2026-11-21)**: `src/claire/extract/{antigravity,codex}_provider.py`, CLI 탐색 함수(`find_agy_executable`, `find_codex_executable`, `diagnose_agy_environment`), `CLAIRE_AGY_*`·`CLAIRE_CODEX_*` `Settings` 필드, `host-cli` 분류, `openai` 기본 블록, 컨테이너의 `~/.gemini`·`~/.codex` 마운트가 없다. 예전 `providers.json`의 `antigravity`·`codex`·`openai` 블록과 최상위 `personal_cli` 블록은 파일에 데이터로 남지만 읽는 코드가 없고, Web UI 응답(`get_sanitized_config`)에는 나가지 않는다. 갱신 요청에 들어온 알려지지 않은 프로바이더 블록은 무시한다.
- **미등록·폐기 프로바이더 fail-closed**: 분류표에 없는 이름(폐기된 `antigravity`·`codex`·`openai` 포함)이나 빈 문자열은 `unregistered`다. 멤버 컨텍스트는 `ProviderNotAllowed`로 거부되고(`require_provider_allowed`), 관리자 설정에서는 `Settings.effective_provider`가 개발 환경에서 `mock`, 운영 환경에서 `unavailable`이 된다(`C2`). 서버 시작은 멈추지 않는다.
- **멤버 제한**: 멤버 컨텍스트는 `MEMBER_KEY_PROVIDERS` / `TIER_PROVIDERS`에 등록된 `api-key` 프로바이더(현재 `gemini`)만 허용하며 `ProviderNotAllowed`로 비허용 프로바이더를 차단한다.
- **저비용 분류**: `get_lowest_effort_provider`는 사용 가능한 `api-key` 프로바이더(현재 `gemini`)를 평가한다.

### 3.2 계정별 컨텍스트와 기능 게이트

- **계정 상태 검사**: 계정의 상태가 `active` 또는 `setup_required`가 아니면(정지 `suspended`, 삭제 대기 `deleting`, 소각 `purged` 등) `ProviderNotAllowed`로 모든 프로바이더 접근이 차단된다.
- **관리자 전용 기능 게이트**: `research`(`allow_tools=True`), Jev 평가(`jev`), 텔레그램 봇(`telegram`)은 관리자 전용 기능이며 멤버에게는 항상 거부된다(`require_feature`).
- **서버 등급 기능 게이트**: 서버 등급 컨텍스트(`source="tier"`)는 관리자가 `tier.features`에 명시한 기능(`expand`, `stt`, `vlm`, `presentation`, `synthesize`)만 허용된다. BYOK 멤버는 등급 기능 5종 전체를 쓸 수 있다.
- **effort 제한**: 서버 등급 컨텍스트의 effort는 `tier.max_effort`를 넘지 않도록 상한으로 낮춘다(`clamp_effort`). 관리자와 BYOK 컨텍스트는 effort를 낮추지 않는다.
- **동시성 제한**: BYOK 컨텍스트는 서버 등급의 `byok_caps.max_concurrency`로 계정별 동시 LLM 작업 수를 제한한다(`job_slot`).
- **[decision] No administrator baseline for the model of a key (owner, 2026-11-19).** The old rule "at or above the administrator baseline" (a rank table of models) gave wrong results, thus it is removed with the rank table.
- **[code] Model preferences of a key.** `PUT /api/me/provider-keys` accepts an optional `model`, `effort`, and `feature_models` (`keys._validate_byok_prefs`); `PATCH /api/me/provider-keys` changes them without the key (`provider_keys.set_key_model_prefs`; 404 `key_not_found` without a key). The defaults that `GET /api/me/provider-keys?include_policy=1` gives (`model_policy.admin_defaults`) are the main model of the server providers for each slot: the server feature routing does not apply to a key (`byok_context`). It checks only that the model is a known Google AI Studio model (an embedding model for `embed`) and that the effort is known and agrees with an effort in the model name. `resolve_provider_context` uses the stored preferences as they are. A value that is not set takes the value of the server providers (`gemini_model`, `gemini_effort`, `gemini_embed_model`, `feature_routing`); these values are only defaults. `GET /api/me/provider-keys?include_policy=1` gives them as `model_policy.admin_defaults`.
- **[code] Personal key of the server administrator** (owner decision 2026-11-19, [ACCOUNTS.md](ACCOUNTS.md) §5.8). `resolve_provider_context` gives the administrator account:
  - With an active personal key: a context with `source="byok"`, `is_admin=False`, `server_admin=True`, the personal key, and `credential_key="byok:<fingerprint>"`. Thus the calls go through the same member key path as a member BYOK: only the context key and registered `api-key` provider path (currently `gemini`, `settings_for_context`). The Jev settings stay, thus the Jev shadow runs also in this context (owner decision 2026-11-20); a member copy has `enable_jev=False`. The model rule is the member rule (§3.2, model preferences of a key).
  - With `server_admin=True`: all features are allowed (`feature_allowed`, also `research`), and the server tier limits do not apply (`server_tier._limits_exempt`: daily ingests, documents, storage, concurrency). The ledger records the tokens (`usage_hooks_for`, `record_usage`).
  - With a registered key that is not active or cannot be decrypted: `ProviderKeyRejected`. The job does not change to `providers.json` (C2).
  - Without a registered key: `admin_context` (`providers.json`), as before.
  - Jobs without an account (`account_id=None`) always get `admin_context`.
  - In a worker loop process, a registered personal key gives `ProviderNotAllowed` (no change to `providers.json`). See [ACCOUNTS.md](ACCOUNTS.md) §5.8.

### 3.3 폴백 없음 원칙 (No Fallbacks, Principle C2)

- 멤버의 API 키가 인증 실패(401/403) 등으로 거부되면 `ProviderKeyRejected`가 발생한다. **서버 등급 등 다른 경로로 자동 폴백하지 않는다.**
- 멤버의 일 적재 한도나 월 토큰 예산이 소진되면 `ProviderBudgetExceeded`가 발생하며 실패한다.
- 서버 등급이 `none`인 멤버가 LLM 작업을 시도하면 `ProviderNotConfigured`로 실패한다.
  - 원문 저장과 FTS 색인은 정상 동작하며(A1), `raw_inbox`에 오류로 남는다. 키를 등록한 뒤 `replay-failed`로 다시 적재할 수 있다.
- 모든 정책 예외(`ProviderPolicyError`)는 기계 판독용 `code`와 사용자 화면용 복구 안내 문구인 `remedy`를 제공한다.

### 3.4 STT·VLM

`enable_video_transcription`·`enable_vlm_transcription`이 꺼져 있으면 `mock`이다. 호출 측(`ingest/fetchers/*`)이 같은 플래그로 먼저 막으므로 이 mock은 실제로 호출되지 않는다. 켜져 있으면 다음과 같다.
- `gemini`(`google`): 키가 있으면 `gemini`, 없으면 쓸 수 없음
- `mock`: `mock`
- 그 밖의 값: 쓸 수 없음. STT를 지원하지 않거나 설정되지 않은 프로바이더가 여기에 해당한다

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
- **[코드] 모델명 기반 사고 수준 인코딩 (`split_gemini_model_and_effort`)**: `<base>-<effort>` 모델명을 선택하면 `GeminiProvider`가 `interactions.create` 호출 시 기본 모델명만 전달하고 내장된 사고 수준을 `generation_config.thinking_level`(소문자)에 자동 적용한다. interactions API에는 `thinking_config`와 토큰 budget이 없으므로 Gemini 모델 목록에 `none` 수준이 없다. API 모델 목록(`list_gemini_models`, `discover_gemini_models`)은 각 모델이 지원하는 추론 수준 조합을 열거해 반환하며, 레거시 설정·API 호환을 위해 접미사가 없는 기본 모델명 입력도 허용한다.
- **[코드] 기능별 라우팅 슬롯 분리 (`FEATURE_SLOTS`)**: `summarize`(요약 및 프레젠테이션)와 `render_detail`(가독 상세 렌더링)은 별도 슬롯으로 분리되어 서로 다른 모델·추론 수준을 지정할 수 있다(`FeatureRoutedProvider.summarize_document` → `summarize`, `FeatureRoutedProvider.render_detail` → `render_detail`). 슬롯은 `extract_graph`, `summarize`, `render_detail`, `entity_judge`, `relation_judge`, `classify`, `research`, `embed`이다. STT·VLM은 슬롯이 아니다: 전사·시각 팩토리가 프로바이더를 고른다(STT는 Google AI Studio `gemini-3.5-transcribe` 고정). 서버 라우팅은 서버 작업과 개인 키 없는 관리자 작업에만 적용되고, 개인 키 작업은 키의 모델과 키의 기능별 모델을 쓴다.
- **[코드] 사용량 훅 (`UsageHooks`)**: 호출 직전 `before_call`로 예산 초과 여부를 점검하고, 호출 완료 후 `after_call`로 소비된 토큰(입력/출력)을 `account_usage` 원장에 가산한다.

### 4.2 테마별 임베딩 모델 고정·다중 공간 EOS 전환 (Theme Embedding Pinning & EOS Lifecycle)

**[코드]** `src/claire/extract/gemini_provider.py`, `src/claire/store/vectors.py`, `src/claire/store/knowledge_base.py`, `src/claire/extract/resolver.py`. 설계 §10.4 (P4 L4-a3), `AED6`, `AED7`, `AED10`, `AED11` (`AE6a`–`AE6d` 구현 2026-11-21).

- **듀얼 버전 어댑터 및 3072차원 기준(`AE6a`, `AED10`)**: `GeminiProvider.embed`는 `classify_gemini_embed_model`로 모델을 라우팅한다. `gemini-embedding-2`(`v2_prompt`)는 `EmbedContentConfig(output_dimensionality=3072)`만 전달하고 `task_type`/`title`을 프롬프트 접두사(`format_v2_embed_prompt`)로 인코딩한다. `gemini-embedding-001`(`v1_param`, 공식 EOS 2028-05-14)는 SDK `task_type`/`title`을 유지하며 비-3072 차원 시 L2 정규화를 수행한다. 폐기 모델(`gemini-embedding-2-preview`, `text-embedding-004` 등)은 `ValueError`로 fail-closed 차단한다(`C2`). `embed_many`(`types.Content` 래핑)와 `embed_batch_job`(`client.batches.create_embeddings`)으로 배치 적재를 지원한다.
- **v39 다중 공간 스키마 및 검색 격리(`AE6b`)**: `embedding_spaces` 레지스트리와 `knowledge_bases.active_space_id`·`target_space_id`, `embeddings(owner_id, kb_id, space_id)` 복합 키를 사용한다. `VectorStore.search`와 `VectorStore.count`는 `active_space_id` 행만 조회해 그림자 전환 중 공간 혼합을 차단한다.
- **무중단 그림자 전환·Dual-Embed·EOS fail-closed 퇴역(`AE6d`, `AED7`)**: `claire re-embed --target-space <space_id|model> --mode batch|sync`가 `target_space_id`에 그림자 벡터를 백필하는 동안 실시간 엔티티 생성·병합은 `sync_entity_space_embeddings`로 활성/목표 공간에 동시 임베딩(Dual-Embed)된다. `--calibrate`로 4계층 임계값(`0.93 / 0.72 / 0.70 / 0.55`) 분포를 검증한 뒤 `--cutover`로 `active_space_id`를 원자 전환하고 구 벡터를 정리한다. 활성 공간이 `eos_retired`이면 `VectorStore.search`와 `retrieval.query.search`는 퇴역 모델을 호출하지 않고 FTS 전용(`via == ["fts"]`)으로 강등된다(`C2`).
- **Mock 모델 격리 및 초기화**: `MockProvider`의 의사 임베딩 모델명은 `mock-sha256`으로 명시하여 실제 모델 공간과 격리하며, `reset_kb` 시 `embed_provider`, `embed_model`, `active_space_id`, `target_space_id`를 모두 초기화한다.
- **공개 테마 검색 격리**: 네임스페이스 소유자가 아닌 타 계정 또는 익명 사용자의 검색은 임베딩 비용 전가를 막기 위해 FTS 전용으로 동작한다.

### 4.3 저비용 분류 (`get_lowest_effort_provider`)

**[코드/개정]** `classify_paper` 같은 1차 분류는 등록된 `api-key` 프로바이더(현재 `gemini`) 중 가장 낮은 effort 구성을 우선 선택한다(`AED8`: 폐기된 `host-cli` 후보는 없으며, 그 분기는 `AE8`에서 삭제되었다).

---

## 5. 서버 등급 (ServerTier) 및 사용량 원장

**[코드]** `src/claire/accounts/server_tier.py`, `src/claire/store/usage_ledger.py`. 설계 §10.3 (P4 L4-c).

### 5.1 서버 등급 (`ServerTier`)

- **저장 위치**: `claire.db`의 `server_settings` 테이블 (`key='server_tier'`). JSON 형식으로 직렬화된다.
- **기본값 (D6 `none`)**:
  - `provider`: `"none"` (기본값) 또는 `"gemini"` (`TIER_PROVIDERS`)
  - `model`: 프로바이더 모델명 (기본값 `""`)
  - `monthly_tokens`: 월간 LLM 토큰 상한 (기본값 `0`)
  - `daily_ingests`: 일일 적재 상한 (기본값 `20`)
  - `max_effort`: 허용 최대 effort (기본값 `"low"`)
  - `features`: 허용 기능 집합 (기본값 빈 집합. `expand`, `stt`, `vlm`, `presentation`, `synthesize`)
  - `storage_mb`: 계정별 저장 용량 상한 MB (기본값 `2048`)
  - `max_documents`: 계정별 최대 문서 수 (기본값 `5000`)
  - `byok_caps`: BYOK 계정의 서버 자원 상한 (`{"max_concurrency": 1}`)
- **자원 한도 공통 적용**: 서버 자원 한도(`storage_mb`, `max_documents`, `daily_ingests`)는 본인 키가 있는 BYOK 멤버에게도 동일하게 적용된다.
- **유효성 검증 및 보호**:
  - `ServerTier.from_dict`가 필드와 범위를 엄격히 검증하며, 잘못된 JSON이나 미지원 프로바이더는 `provider="none"`으로 fail-closed 처리한다.
- **관리 경로**: 관리자 세션에서 `GET /api/admin/server-tier`, `PUT /api/admin/server-tier`를 통해 설정 및 조회할 수 있으며, `/admin/server` 화면에 관리 패널이 제공된다.

### 5.2 계정별 UTC 사용량 원장 (`account_usage`)

- **저장 위치**: `data/auth/auth.db`의 `account_usage` 테이블 (v2 스키마).
- **컬럼 구성**: `account_id`, `period` (UTC 'YYYY-MM'), `tokens_in`, `tokens_out`, `ingests`, `daily_day` (UTC 'YYYY-MM-DD'), `daily_ingests`, `updated_at`.
- **UTC 기준 집계**: 모든 월간 및 일간 집계는 서버 로컬 시각이 아닌 **UTC 기준**으로 처리된다. 날짜가 바뀌면 `daily_ingests`는 0부터 다시 계산된다.
- **원자적 갱신**: 사용량 기록(`record_tokens`)과 일 적재 예약(`try_reserve_ingest`)은 단일 SQL `UPDATE` 문을 통해 원자적으로 수행되어 경쟁 상태를 방지한다.
- **적재 예약 규칙**:
  - 신규 문서 적재 시 `IngestService`가 `try_reserve_ingest`를 호출하여 일 적재 한도를 선확인 및 차감한다.
  - 재생성(`regenerate`), 기존 문서 재적재(`reingest`), recover 재시도는 `daily_ingests` 카운트에 포함하지 않는다(월 토큰만 기록).
- **BYOK 토큰 계측**: BYOK 계정이 소비한 토큰도 원장에 누적 기록되나, 월 토큰 예산 초과 차단은 서버 등급 컨텍스트(`source="tier"`)에만 적용된다.
- **[code] Server credentials are not recorded.** The ledger has no row for `admin_context` (the server providers). The tokens of the personal key of the server administrator are recorded on the administrator account.

---

## 5A. Management surface

Basis: commit d15ae8e0 (2026-11-16).

- **[code] HTTP routes**: `GET`/`HEAD /providers`, `PATCH`/`POST /providers`, `POST /providers/test`, and `POST /providers/check` in `src/claire/api/server.py`. The route table in `src/claire/api/security.py` allows them only for `admin`, and `POST /providers/test` and `POST /providers/check` additionally require an admin browser cookie session and same-origin request (Bearer API tokens are rejected with 403).
- **[code] Secret masking**: `ProviderManager.get_settings_dict` returns an API key as `••••••••` when a key is set. `ProviderManager.update_config` keeps the stored key when the request sends the mask or an empty value. It clears the key only for an empty value with `clear_api_key`.
- **[code] Connection test**: `ProviderManager.test_connection` checks an API key and model before or after a save. For Gemini, a masked key in the request means the stored key.
- **[decision] Retirement of host-CLI providers and ChatGPT/Codex/Claude (owner, 2026-11-19 seal -> 2026-11-20 `AED8` retirement).** Previously, the administrator's personal CLI routes (`/api/me/personal-cli`, `/api/me/personal-cli/test`) were sealed with 404. On 2026-11-20 (`AED8`), the owner formally retired server-side `host-cli` providers (`AntigravityProvider`, `CodexProvider`) and `ChatGPT`, `Codex`, `Claude` across principles, design, and implementation (`AED6` also removed unimplemented `openai` placeholders). Implementation unit `AE8` deleted the server CLI modules and `Settings` keys on 2026-11-21 ([`AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md`](../design/AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md)). `POST /providers/check` answers 400 for an active provider that is not `mock` or `gemini`.
- **[code] Screen `/admin/providers`** (design [`PROVIDER_SETTINGS_SIMPLIFICATION_DESIGN.md`](../design/PROVIDER_SETTINGS_SIMPLIFICATION_DESIGN.md), `renderProviderManager` in `src/bible/static/js/manage_panels.js`): the card "What applies now" (`#prov-effective-card`) from the field `effective` of `GET`/`PATCH /providers` (`describe_server_providers` in `src/claire/accounts/provider_view.py`: the main and effective provider, the model and the override of each text slot, the embedding model of new knowledge bases, STT and VLM with on/off and readiness, Jev, the problems `no_api_key`, `mock_provider`, `stt_not_ready`; the response reads the settings with the saved `providers.json`), then one form for each part: the key (`#prov-key-form`: save, check the connection with `POST /providers/test`, delete), the models (`#prov-model-form`: main model, embedding model of new knowledge bases, the seven text slots in a closed `<details>`), STT (`#prov-stt-form`: the order for a video `#prov-stt-order` from `effective.stt.order`, the switch, and the transcription language), and the advanced settings in a closed `<details>` (`#prov-advanced-form`: call interval and retries, Jev, the test provider switch on a development server only). Each form sends only its part with `PATCH /providers` and shows the result in its `[data-prov-status]` (`role="status"`) next to its buttons; a failure keeps the typed values. The page has no lineage diagram (it is on `/support/pipeline`, see the next item and the link `[data-prov-pipeline-link]` of the status card), no routing rows for STT and VLM, and no server tier card. [code]
- **[code] Page `/support/pipeline`** (`src/claire/api/accounts_routes/pipeline.py`, sidebar group `support`, rule `_system("owner")`): the corrected model lineage diagram for each account. The card `#pipeline-flow` shows the 11 steps of `extract_resolve_store` in their order (EXTRACTION.md §1) as a `console_flow` list (`[data-flow-step]`), with the model of each step from the provider view (`describe_server_providers` for the administrator, `describe_account_providers` for a member) and the marks of an ingest with the agent client (`[data-client-role]`: `client` for steps 4–6, `api_only` for the effort step, the LLM judge, the relations across documents, and the model lineage). Entity resolution shows its five gates; the embedding is in gate 3 and the judge in gate 4; Jev is a side note (`[data-jev-state]`, administrator only), never a step between them. The card `#pipeline-stt-order` shows the order of a video (`media_language_order`). The card `#pipeline-fixed` has the fixed values (entity frame as embedding text, `RETRIEVAL_DOCUMENT` with the prompt prefix of `gemini-embedding-2` or the `task_type` of `gemini-embedding-001`, `RETRIEVAL_QUERY`, 3072, the embedding model of the knowledge base, `gemini-3.5-transcribe`). Only the administrator gets `#pipeline-recent` (the model lineage of the 20 latest documents of all accounts, from `ProviderManager.get_lineage_summary`) and `#pipeline-fallbacks` (`REMOVED_FALLBACK_CATALOG`, column `behavior`). [code]
- **[code] `get_lineage_summary`** reads `documents.meta` and `documents.fetched_at` (the old reads of `meta_json`, `created_at`, `resolution_decisions`, and `review_shadow_pairs` named no column or table of `claire.db`, thus the old list was always empty). The keys `architecture_specs`, `decision_gates`, and the Jev counters are removed; `jev` has `enabled`, `role`, and `model`. [code]
- **[code] Rules of `PATCH /providers`**: a production server refuses `active_provider = "mock"` (400 `mock_not_allowed_in_production`); an STT block keeps only `enabled` and `language` and stores `provider = "gemini"`; a `server_tier` field is ignored (the tier is edited on `/admin/server` with `PUT /api/admin/server-tier`, step-up, and admin audit); only a request that sets `active_provider` checks that the main provider can be selected (a save of another part with Google AI Studio and no key is stored, and the status view shows `no_api_key`). `FEATURE_SLOTS` has no `stt` and no `vlm` (a routing row for them had no reader): such a slot is an unknown slot (400). [code]
- **[code] Storage**: `data/providers.json`, written by `ProviderManager.save_config`. The env migration (`migrate_from_env_if_needed`) and its effect on `.env` are in §0 and §2.1.
- **[code] CLI**: `claire providers` (`list`, `migrate`). See [`COMMANDS.md`](COMMANDS.md) §3.12.

## 6. 프로바이더를 추가할 때

1. `extract/<name>_provider.py`에 `Provider` 프로토콜의 메서드를 모두 구현한다.
2. `src/claire/accounts/provider_policy.py`의 `PROVIDER_CLASSES`에 `api-key` 분류를 등록한다(미등록 이름은 fail-closed).
3. 멤버 키로 지원하려면 `MEMBER_KEY_PROVIDERS`에 추가하고, 서버 등급으로 지원하려면 `TIER_PROVIDERS`에 추가한다.
4. `get_provider` 및 `resolve_provider_context`에 분기를 추가하고, `Settings._llm_unavailable_reason`에 사용 가능 조건을 추가한다.
5. WebUI에서 고를 수 있게 하려면 `SUPPORTED_ACTIVE_PROVIDERS`, `ALLOWED_ROUTE_PROVIDERS`, `DEFAULT_PROVIDERS_CONFIG`, `get_settings_dict`, `migrate_from_env_if_needed`를 함께 고친다.
6. 새 `Settings` 필드는 [`ENVIRONMENT_VARIABLES.md`](ENVIRONMENT_VARIABLES.md)에 행을 추가한다. 빠뜨리면 `tests/test_docs_consistency.py`가 실패한다.
7. 운영 환경 실패 동작 및 정책 검속 동작을 `tests/test_provider_policy.py`에 추가한다.

---

## 7. 알려진 한계와 미검증

- **[코드] `gemini-embedding-2` Stage 2 멀티모달 임베딩**: Track B (`AE6a`–`AE6d`)로 `gemini-embedding-2`(`3072d`, `v2_prompt`) 텍스트 임베딩, `embedding_spaces` v39 스키마, Batch API Shadow Backfill 및 `AED7` Dual-Embed가 구현되었다. 이미지·PDF·오디오·영상 네이티브 멀티모달 임베딩(Stage 2)은 후속 범위다(`AED11`).
- **[코드] BYOK 멤버 모델/effort 설정**: 현재 BYOK 멤버의 LLM 모델과 effort는 관리자 기본값을 상속받으며, 멤버별 세부 설정 UI는 후속 과제다.
- **[코드] 서버 등급 `none` 멤버 테마의 recover 루프**: 서버 등급이 `none`인 멤버의 테마에 적재 실패 문서가 남아 있을 경우, recover 재시도가 최대 시도 횟수까지 소모된 뒤 영구 실패로 마킹된다.
- **[미검증]** 텔레그램 봇과 백그라운드 루프가 운영 환경에서 `unavailable`일 때 사용자에게 어떤 메시지를 보여 주는지는 확인하지 않았다. `raw_inbox` 오류 기록까지만 확인했다.
