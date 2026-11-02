# Claire Bible 프로젝트 원칙

확인 기준 커밋: `3d3a696` (2026-11-01) · 바꿀 수 있는 사람: 저장소 소유자

이 문서는 구현이 바뀌어도 유지되는 규칙만 적는다. 어기면 버그다. 현재 코드가 무엇을 어떻게 하는지는 [`implementation/`](implementation/)에, 비전과 범위는 [`GOALS.md`](GOALS.md)에 있다.

각 원칙에는 다음을 붙인다.

- **집행**: 원칙을 지키게 하는 코드나 테스트. "없음"이면 리뷰로만 지켜진다.
- **현황**: 기준 커밋에서 확인한 적용 범위와 예외. 원칙 자체가 아니라 확인 결과이므로, 코드가 바뀌면 이 줄만 고친다.

---

## A. 지식 무결성

**A1. 원문과 출처를 보존한다.** LLM이 만든 요약·추출 결과는 원문(raw text)과 출처 메타데이터를 대체하거나 덮어쓰지 않는다.
- 이유: 모델 산출물은 다시 만들 수 있지만 원문은 다시 얻지 못할 수 있다.
- 집행: `tests/test_raw.py`, `tests/test_raw_truncation_recording.py`, `tests/test_document_raw_route.py`

**A2. 결정론 로직과 모델 추론을 분리한다.** 라우팅, 중복 판별, 와이어 프로토콜, 인증, 토큰 교환은 결정론적 단위 테스트로 검증한다. 모델 추론 품질(추출, 요약, 동일체 판정)은 단위 테스트가 아닌 별도 평가 표본으로 잰다.
- 이유: 모델 출력을 단위 테스트의 기대값으로 삼으면 테스트가 깨지거나 아무것도 검증하지 못한다.
- 집행: `tests/` 단위 테스트, `tests/eval_resolution_benchmark.py`

**A3. 스키마는 비파괴적으로 진화한다.** 마이그레이션은 멱등이며 몇 번을 실행해도 같은 결과를 낸다. 버전과 lineage는 [`SCHEMA_VERSIONING.md`](implementation/SCHEMA_VERSIONING.md)를 따른다.
- 집행: `tests/test_migrate.py`, `tests/test_schema_v13.py`, `tests/test_schema_v14.py`

**A4. 관측성 데이터는 정본 지식 DB와 물리적으로 분리한다.** 텔레메트리와 진단 기록은 정본 DB에 테이블을 만들지 않는다.
- 이유: 2026-09-11에 정본 DB에 넣었던 `ingest_attempts`, `fetch_attempts`를 이 원칙 위반으로 철회했다(v12 폐기).
- 집행: `tests/test_telemetry.py`, `tests/test_migrate.py`(v12 복구)

## B. 보안

**B1. 권한은 최소로, 경계는 여러 겹으로 둔다.** 읽기와 쓰기 권한을 분리하고, 네트워크 경계(Reverse Proxy, CORS)와 애플리케이션 인증을 모두 둔다. 경계 규칙은 [`EXTERNAL_ACCESS.md`](implementation/EXTERNAL_ACCESS.md)와 [`API_HTTP_CONTRACT.md`](implementation/API_HTTP_CONTRACT.md)가 정본이다.

**B2. 사설망 요청은 기본 차단한다.** 수집기는 명시적으로 허용(`CLAIRE_ALLOW_PRIVATE_NETWORKS`, `CLAIRE_PRIVATE_NETWORK_ALLOWLIST`)되지 않은 사설 대역에 접근하지 않는다(SSRF 방지).
- 집행: `tests/test_http_security.py`

**B3. 공유 토큰은 읽기 전용이다.** 문서 공유 토큰(16자, `doc_shares.token`)은 외부에 공개될 수 있으므로 비인증 경로에서 수정·삭제 권한으로 승격하지 않는다. 토큰 4종(공유, 주입, UI 세션, 임시 스테이징)은 이름과 용도를 섞지 않는다.

**B4. 비밀값과 운영 데이터는 저장소에 두지 않는다.** 자격증명은 환경변수나 `data/` 아래 자격증명 파일로만 주입한다. 운영 DB, Vault, 백업, 세션 토큰은 커밋하지 않는다. 이슈·문서·테스트 fixture에도 실제 사용자 데이터나 운영 DB 식별자를 넣지 않는다. `sample.md`·`sample.adoc`에는 공개 문서와 합성 메모만 쓴다. 문서의 IP·도메인·포트·토큰은 예시값이나 환경변수 플레이스홀더로만 쓴다.

## C. 추측 금지 (Zero Speculation)

**C1. 모호하면 추측하지 말고 실패한다.** 필수 인자나 문맥이 없거나 모호하면 "가장 가까운 값"으로 유추하지 않고 구체적 오류로 즉시 실패한다. 요청받지 않은 편의 기능, 임의 기본값, 데이터 보정·합성 코드를 넣지 않는다.

**C2. 숨은 폴백을 두지 않는다.** 외부 서비스 연동이 실패하면 빈 데이터나 mock 결과로 파이프라인을 채우지 않고 실패로 처리한다.
- 운영 환경(`CLAIRE_ENVIRONMENT=production`)에서는 설정한 LLM·STT·VLM 프로바이더를 쓸 수 없을 때 mock으로 대체하지 않는다. 키나 실행 파일이 없거나 값이 알 수 없는 경우가 여기에 해당한다. 서비스 기동과 상태 조회는 계속되고(상태 표시는 `unavailable`), 실제 호출은 `ProviderUnavailableError`로 실패한다. 개발 환경의 mock 대체는 허용한다. 명시적으로 `mock`을 지정한 것은 폴백이 아니다. (결정 2026-11-02)
- 집행: `Settings.provider_unavailable_reason`, `extract/provider.py`·`transcript/factory.py`·`vision/factory.py`의 `Unavailable*Provider`, `tests/test_provider_unavailable.py`

**C3. 에이전트용 CLI 계약을 지킨다.** `--json` 모드에서는 다음이 성립한다.
- stdout에는 검증된 `AgentOutput` JSON만 나가고, 사람용 안내와 로그는 stderr로 간다.
- 대화형 입력을 요구하면 기다리지 않고 종료 코드 4로 실패한다.
- 종료 코드: 0 성공, 2 잘못된 인자, 4 전제조건 실패, 5 프로바이더·내부 오류.
- 모든 명령은 `--yes`/`-y`로 무인 실행할 수 있어야 한다.
- 집행: `src/claire/api/agent.py`의 `with_agent_contract`(`cli.main`이 모든 서브커맨드에 적용), `tests/test_agent_cli.py`

**C4. 출력 스키마는 봉인한다.** 에이전트에게 돌려주는 Pydantic 모델은 `ConfigDict(extra="forbid", strict=True)`로 정의해 스키마 밖 필드가 생기지 않게 한다.
- 현황: 이 설정을 쓰는 모델은 `AgentOutput` 하나다. 다른 API 응답 모델에 적용할지는 정하지 않았다.

**C5. 실행 표면을 정확히 안내한다.** 배포 호스트의 PATH에는 `claire`가 없다. 사람에게 명령을 안내할 때 `claire <cmd>`를 단독으로 쓰지 않고, `./cb-manuscript app <cmd>`(앱 one-off) 또는 `./cb-manuscript <cmd>`(호스트 수명주기)로 쓴다. 존재하지 않는 명령(`repair`, `clean-inbox` 등)을 지어내지 않는다.
- 자주 쓰는 명령(기준 커밋에서 존재 확인): `app recover-run`, `app replay-failed`, `app doctor --heal`, `app audit --heal`, `app queue status`, `app queue list inbox`, `health`(컨테이너 생존), `app health`(앱 진단)
- 명령 목록의 정본: [`COMMANDS.md`](implementation/COMMANDS.md)

## D. CLI 설계

**D1. 문서를 가리키는 인자는 하나의 리졸버로 해석한다.** 특정 문서를 대상으로 하는 명령의 첫 positional 인자는 `target`이다. 자체 SQL로 찾지 않고 `store.db.resolve_document_targets`(ID → 공유 링크·토큰 → URL·정규화 URL → 키워드 순)를 쓴다. 한 문서만 바꾸는 명령은 `resolve_single_document_target`로 여러 건 일치를 거부한다.
- 이유: 공유 링크의 `?s=`는 `canonicalize_url()`이 추적 파라미터로 지우므로, 정규화 전에 먼저 꺼내야 한다. 명령마다 따로 구현하면 이 함정이 반복된다.
- 집행: `tests/test_cli_target_resolution.py`

**D2. 옵션 하나에는 책임 하나만 준다.**
- `--apply`: dry-run이 기본인 명령에서 실제 변경을 일으키는 유일한 트리거
- `--dry-run`: 실행이 기본인 명령에서 시뮬레이션을 명시
- `--force`/`-f`: 이미 결과가 있어도 덮어쓰기. 실행 트리거로 쓰지 않는다
- `--yes`/`-y`: 확인 프롬프트만 생략. 단독으로 변경을 일으키지 않는다
- 집행: `tests/test_cli_theme.py`(`theme delete`/`theme reset`의 dry-run 기본값)

**D3. 파괴적 명령은 기본이 dry-run이다.** 일괄·파괴적 명령은 계획만 보여 주고 `--apply`가 있어야 실행한다. `purge`는 데이터 수명주기 정책(`CLAIRE_DATA_LIFECYCLE`)을 먼저 확인한다. 여러 문서가 일치하면 목록을 보여 주고 확인을 받는다. 0건이면 입력값과 힌트를 함께 보여 준다.

## E. 운영

**E1. 업데이트는 명령 하나로 끝난다.** `./cb-manuscript update`(또는 `install`) 하나로 환경변수 백필, 스키마 마이그레이션, 컨테이너 교체가 끝나야 한다.

**E2. 사용자 설정은 덮어쓰지 않는다.** 백필은 템플릿(`.env.example` 등)에 있고 사용자 파일에 없는 키만 추가한다. 이미 있는 API 키, 토큰, 포트, 타임존은 그대로 둔다. 몇 번을 실행해도 결과가 같다.
- 집행: `tests/test_cb_manuscript.py`

**E3. 스키마 마이그레이션은 새 서비스 시작 전에 끝낸다.** 새 컨테이너가 요청을 받기 전에 별도 컨테이너에서 `claire migrate`를 먼저 실행한다.

**E4. 에이전트는 운영 서버에 접근하거나 배포하지 않는다.** 검증은 개발 모드와 그래프 번들로 한다. 운영 상태를 알아야 하면 소유자에게 묻는다.

## F. 문서

**F1. 현재 상태를 말하는 문장은 검증할 수 있는 문서에만 쓴다.** 현재 구현은 `implementation/` 문서에 쓰고 확인 기준 커밋을 적는다. 근거는 `[코드]`(소스 확인), `[실측]`(측정, 조건 포함), `[결정]`(소유자 방침), `[미검증]`으로 구분한다. 견본: [`GRAPH_ENGINE.md`](implementation/GRAPH_ENGINE.md)
- 집행: `tests/test_docs_consistency.py`(링크, 코드 경로, 환경변수 집합과 기본값)

**F2. `design/`은 과거 기록이다.** 설계 문서의 배경, 대안, 기각 사유는 남기지만 상태 필드를 현재에 맞춰 고치지 않는다. 구조를 바꾸는 커밋은 해당 구현 문서도 함께 고친다.

---

## 원칙 간 충돌과 알려진 위반 (소유자 결정 필요)

현재 없음. 새로 발견하면 여기에 적고 소유자에게 결정을 요청한다.

해결된 항목:
- 2026-11-02 mock 폴백과 C1·C2: 운영 환경에서는 실패하도록 고쳤다 (C2 참고).
- 2026-11-02 `theme delete`/`theme reset`이 기본으로 실행되던 문제: dry-run 기본, `--apply`로 실행하도록 고쳤다 (D2·D3).

---

## 출처

이 문서는 다음에 흩어져 있던 원칙을 모은 것이다. 원문은 이력으로 남기고 이후에는 이 문서를 고친다.

- [`GOALS.md`](GOALS.md) §3 품질 및 거버넌스 원칙, §5 공개 저장소 데이터 및 보안 정책
- [`design/AGENT_FIRST_CLI_AND_ZERO_SPECULATION.md`](design/AGENT_FIRST_CLI_AND_ZERO_SPECULATION.md)
- [`design/TARGET_IDENTIFIERS_AND_CLI_STANDARD.md`](design/TARGET_IDENTIFIERS_AND_CLI_STANDARD.md)
- [`design/OPERATIONAL_MIGRATION.md`](design/OPERATIONAL_MIGRATION.md) §1
- `.gemini/rules/agentic-cli.md`
- [`implementation/GRAPH_ENGINE.md`](implementation/GRAPH_ENGINE.md) §0의 `[결정]` 항목 중 운영 접근 금지
