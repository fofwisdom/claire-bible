# 대상 식별자(Target Identifiers), 토큰(Tokens) 및 CLI 명령어 설계 표준

이 문서는 Claire Bible 시스템 전반에서 **문서 대상 식별자(Target Identifiers)**, **토큰(Tokens)**, 그리고 **CLI 명령어(CLI Commands)**를 설계하고 구현할 때 준수해야 하는 공식 표준 규격과 엔지니어링 가이드라인을 정의합니다.

새로운 CLI 서브커맨드나 API 엔드포인트를 추가할 때 발생할 수 있는 식별자 파편화, 오동작, 권한 상승, 그리고 데이터 유실 사고를 원천 방지하는 것을 목적으로 합니다.

---

## 1. 시스템 내 '토큰(Token)'의 정의 및 엄격한 사용 구분

Claire Bible에는 4가지 서로 다른 목적의 토큰이 존재합니다. 개발 및 CLI 옵션 설계 시 이를 절대 혼용하거나 모호하게 노출해서는 안 됩니다.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             토큰 유형 및 격리 체계                            │
├───────────────────────────────┬──────────────┬──────────────────────────────┤
│ 유형                          │ 포맷/길이    │ 용도 및 권한 경계            │
├───────────────────────────────┼──────────────┼──────────────────────────────┤
│ 1. 문서 공유 토큰 (Share)     │ 16자리 영숫자 │ 비인증 사용자 대상 특정 문서  │
│    - doc_shares.token         │ (Base62)     │ 1개의 읽기 전용 핫링크 (/p?s)│
├───────────────────────────────┼──────────────┼──────────────────────────────┤
│ 2. 주입/인증 토큰 (Inject)    │ 32~128자리   │ 외부 서비스/API 호출 시      │
│    - CLAIRE_INJECT_TOKEN      │ (URL-safe)   │ 백엔드 데이터 주입/관리자 권한│
├───────────────────────────────┼──────────────┼──────────────────────────────┤
│ 3. UI 세션 토큰 (Session)     │ 32~128자리   │ 텔레그램 승인 기반 웹 UI     │
│    - auth_sessions            │              │ 관리자 세션 쿠키             │
├───────────────────────────────┼──────────────┼──────────────────────────────┤
│ 4. 임시 스테이징 토큰 (Temp)  │ 8자리 Hex    │ 백업/복원 파일 교체 시       │
│    - secrets.token_hex(8)     │              │ 내부 충돌 방지용 임시 파일명 │
└───────────────────────────────┴──────────────┴──────────────────────────────┘
```

### 규칙
- **CLI 옵션 네이밍 규칙**: `--token`은 오직 **1번 문서 공유 토큰(16자리)**에만 사용하거나, 가능한 한 단일 `target` 인자로 흡수합니다.
- **권한 격리 원칙**: 1번 문서 공유 토큰은 '읽기 전용'으로 외부에 공개 배포될 수 있으므로, 웹 API 등 비인증 경로에서 이 토큰을 파괴/수정 명령의 주체로 승격시켜서는 안 됩니다 (CLI 운영자 환경에서만 문서 탐색 힌트로 사용).

---

## 2. 4단계 스마트 타깃 해석 표준 (Target Resolution Standard)

사용자가 입력한 `target` 문자열(또는 옵션)을 해석할 때는 반드시 아래의 **4단계 우선순위**를 따르는 공통 리졸버(`dbm.resolve_document_targets`)를 사용해야 합니다.

```
[ 사용자 입력: target (문자열) ]
   │
   ├─ [1단계: 정확한 ID 일치 (Exact ID Match)]
   │   • documents.id == target 검사 (SHA256 해시 / UUID)
   │   • 매칭 시 즉시 해당 문서 반환
   │
   ├─ [2단계: 공유 링크 및 공유 토큰 (Share Link & Token)]
   │   • URL 쿼리 파라미터에서 '?s=' 추출 또는 16자리 토큰 검증(plausible_share_token)
   │   • doc_shares 테이블 조회하여 document_id 도출
   │   ※ 주의: canonicalize_url() 통과 전에 ?s= 파라미터를 먼저 추출해야 함
   │
   ├─ [3단계: 원본 URL 및 정규화 URL 일치 (URL & Canonical URL)]
   │   • 원본 target으로 url = ? OR canonical_url = ? 조회
   │   • http:// 또는 https:// 누락 시(도메인/경로 형태) 접두사 보정
   │   • canonicalize_url(target) 결과로 url = ? OR canonical_url = ? 조회
   │
   └─ [4단계: 키워드/제목 부분 검색 (Keyword Fallback)]
       • title LIKE %target% OR url LIKE %target% OR canonical_url LIKE %target%
       • 최대 50건의 후보군 반환 (단건 전용 명령에서는 다건 매칭 시 거부)
```

---

## 3. URL 정규화와 `?s=` 쿼리 파라미터 보존 규칙

`src/claire/ingest/normalize.py`의 `_TRACKING` 파라미터 목록에는 `"s"`(일반 검색/추적 파라미터)가 포함되어 있습니다.

> **주의 (WARNING)**: 공유 링크(`https://domain.com/p?s=abcdef1234567890`)를 그대로 `canonicalize_url()`에 넘기면 `?s=...`가 **트래킹 파라미터로 오인되어 삭제**되고 `https://domain.com/p`로 잘려 버립니다.

### 올바른 구현 패턴:
```python
# 1. 정규화 전에 쿼리 파라미터에서 ?s= 및 공유 토큰을 먼저 분리
if "?" in target:
    parsed = urlsplit(target)
    qs = parse_qs(parsed.query)
    if "s" in qs and qs["s"]:
        token = qs["s"][0]
        if plausible_share_token(token):
            # doc_shares 에서 document_id 조회
            ...

# 2. 공유 링크가 아닌 일반 웹 URL일 때만 canonicalize_url 적용
c_url = canonicalize_url(target)
```

---

## 4. 명령어 성격별 안전 가이드라인 (Command Safety Matrix)

명령어의 위험도에 따라 스마트 타깃 리졸버 결과를 다르게 취급해야 합니다.

### A. 파괴적 명령 (Destructive / Purge) — `claire purge`
- **수명주기 정책 검증**: `settings.is_purge_allowed` (`CLAIRE_DATA_LIFECYCLE=purgeable`) 필수 확인.
- **공유 링크 경고**: 공유 URL/토큰으로 식별된 경우, **"단순 공유 링크 비활성화가 아닌 원본 문서 및 지식그래프 전체 소각"**임을 Dry-Run에 명시적 경고.
- **다건 매칭 가드**: 키워드 부분 매칭 등으로 여러 문서가 잡힌 경우, 삭제 대상 목록 테이블을 출력하고 `--force` 플래그 확인 필수.
- **0건 매칭 시 피드백**: 단순히 0건 종료하지 않고, 입력된 검색어와 함께 최근 수집된 문서 목록(5건) 및 힌트 제공.

### B. 단건 메타데이터 갱신 명령 (Single-Doc Update) — `claire watch`, `claire doc-title`
- **단건 원칙**: 정확히 1개의 문서만 대상이어야 함.
- **모호성 거부**: 스마트 리졸버 결과가 2건 이상인 경우, 작업을 중단하고 `"여러 문서가 일치합니다. 정확한 ID나 URL을 입력하세요."` 에러 출력.

### C. 멱등 재생성 명령 (Idempotent Regeneration) — `claire regenerate`, `claire summary-regenerate`
- **자유로운 타깃 입력**: ID, 16자리 공유 토큰, 공유 URL(`/p?s=token`), 일반 원본 URL(`https://...`), 도메인 형태 모두 지원.
- **Dry-run 기본값**: `--force` 플래그가 없을 때는 진단 보고서만 출력.

### D. 읽기 전용 감사 명령 (Read-Only Audit) — `claire audit`
- **통합 검색**: ID, URL, 키워드, 토큰을 종합하여 L1 인박스, L2 디스크 파일, 8개 DB 테이블, 툼스톤의 잔재를 전수 검사.

---

## 5. 신규 CLI 서브커맨드 개발 시 체크리스트

새로운 CLI 명령어를 추가할 때는 반드시 다음 사항을 확인하십시오:

- [ ] **인자 통일**: 특정 문서를 대상으로 하는 명령어의 첫 번째 positional 인자는 `target`으로 명명했는가?
- [ ] **공통 리졸버 사용**: 자체 SQL `SELECT ... WHERE id=?`를 작성하지 않고 `dbm.resolve_document_targets()`를 호출했는가?
- [ ] **성격별 가드**:
  - [ ] 파괴적 명령인가? ➔ Dry-Run 기본 적용, `--force` 요구, 정책 게이트 확인.
  - [ ] 단건 전용 갱신인가? ➔ `resolve_single_document_target()`를 사용하여 다건 매칭 시 거부 처리.
- [ ] **하위 호환성**: 과거 개별 옵션(`--doc-id`, `--token`, `--url`)이 있었다면 deprecated 처리하고 `target` 리졸버로 흡수했는가?
- [ ] **피드백 및 0건 처리**: 대상을 찾지 못했을 때 입력값 명시 및 검색 힌트를 제공하는가?
- [ ] **문서화**: `docs/origin/implementation/COMMANDS.md`에 명령어 사용법 및 스마트 타깃 예시를 추가했는가?
