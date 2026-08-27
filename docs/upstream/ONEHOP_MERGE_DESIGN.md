# 1홉 확장 중복 완화 — 판정 시 같은 주제 원천 병합

작성일: 2026-07-13 · 상태: **설계 초안(미구현)** · 기준: [GOALS.md](../GOALS.md) 트랙2(추출·연결 품질) /
관련: [SYNTHESIS_REDESIGN.md](SYNTHESIS_REDESIGN.md)(다중 노드 종합의 dedup 적대 문제와 근친)

> 큰 기능이라 구현 전 계획을 먼저 남긴다(GOALS 원칙). 구현 착수 시 이 문서를 갱신하며 진행.

---

## 1. 문제 (사용자 보고)

> "GeekNews 에 A 에 대한 내용이 올라옴 → 조사시킴 → A 의 github 을 발견함 → 자동조사추가 →
> 끝" 의 흐름에서 두 조사가 서로 비슷한 글을 만들고, 그 둘이 분리되어 들어가 글 목록이
> 2~4배 동일 내용으로 뻥튀기된다.

**사용자 결정(2026-07-13)**: 목록에서 묶어 보이기(A안)가 아니라 **판정 시 원천 자체를
병합**(B안)으로 간다. 적용 범위는 **onehop 체인(같은 조사에서 파생된 문서)만** — 시간차를
두고 독립적으로 재발견되는 동일주제 글(TODO 8번, "동일 내용 여러 번 작성")은 범위 밖.

---

## 2. 원인 (코드 근거)

1. 문서 적재 시 `expand_max>0` 이면 `expand_queue` 에 등록(`pipeline.py:203-205`) →
   `expand-loop` 데몬이 `IngestService.expand_document()` 를 돈다(`service.py:62`).
2. `select_followups()`(LLM, `gemini_provider.py:370`)가 부모 본문 링크 후보 중 "따라갈
   가치" 있는 것을 고른다.
3. 고른 링크를 fetch 후 `judge_research(parent_title, parent_context, child_report)`
   (`service.py:104-109`, `gemini_provider.py:328`)로 relevance/quality 를 채점.
4. 게이트(`RELEVANCE_MIN=0.7`, `QUALITY_MIN=0.6`, `follow.py:81-83`) 통과 시
   `self.ingest(url, source=f"onehop:{document_id}", expand_max=0, prefetched=child)`
   (`service.py:115`) 로 **완전히 독립된 새 Document** 를 생성 — 처음부터 다시
   추출→해소→관계→`render_detail` 전체 파이프라인.

dedup 은 세 단계뿐: ① `content_hash` 완전일치, ② `canonical_url` 일치(in-place 갱신),
③ MinHash 근사중복(임계 0.90, `db.py:354`) — 전부 **"같은 텍스트의 다른 입구"** 를 잡는
용도지 **"같은 주제를 다른 텍스트(다른 소스)로 말하는 것"** 은 설계 대상이 아니다.
GeekNews 소개글과 그 프로젝트의 github README 는 어휘가 완전히 달라 MinHash 0.90 을 절대
못 넘는다 → 엔티티 레벨은 병합되지만(`resolve_or_create` 가 이름 기준으로 묶음), **문서
레벨엔 병합 개념이 아예 없어** 목록에 별개 항목으로 쌓인다.

부모→자식 계보는 `raw_inbox.source`(`"onehop:<부모ID>"`)에 이미 남지만 로그 용도일 뿐,
문서·UI 어디에도 활용되지 않는다.

---

## 3. 설계

### 3.1 판정 확장 — `same_subject` (신규 LLM 호출 없이, 기존 judge_research 재사용)

`ResearchJudgement` 스키마(및 프롬프트, `gemini_provider.py:328-345`)에 필드 하나 추가:

```
same_subject: bool
```

프롬프트에 추가할 채점 기준:
> "same_subject: [조사 대상/보고서]가 [맥락]이 다루는 대상 **그 자체**(공식 저장소·공식
> 문서·공식 사이트 등 1차 출처)에 관한 내용이면 true. 맥락과 관련은 있으나 사실상 별개의
> 소재(다른 프로젝트, 다른 사건, 제3자의 파생 논의 등)면 false."

- **신규 LLM 호출을 추가하지 않는다** — 이미 `judge_research` 는 정확히 필요한 입력
  (`context`=부모 맥락, `report`=자식 본문 미리보기)으로 호출되고 있어(`service.py:106-107`),
  구조화 출력 필드 하나만 늘리면 된다(R4 비용 원칙 — SYNTHESIS_REDESIGN §9 와 동일 절제).
- `expand/research.py` 의 기존 호출부(맥락 확장 조사, 사람이 직접 트리거)는 이 필드를
  그냥 무시 — 그쪽은 원래도 매번 신규 문서로 적재하는 게 맞는 흐름이라 영향 없음
  (범위 밖 — §1 사용자 결정).
- mock provider(`extract/provider.py:268`)도 필드 추가 — 결정론 기본값 `True`(테스트가
  merge 경로를 기본으로 밟게) + 테스트에서 오버라이드 가능하게.

### 3.2 게이트 결과 3분기

`follow.py` 의 `passes_gate()` 를 그대로 두고, `service.py:expand_document()` 의 분기를
확장:

| relevance/quality | same_subject | 동작 |
|---|---|---|
| 게이트 미달 | — | 폐기(기존과 동일, `skipped`) |
| 게이트 통과 | `True` | **병합**(신규) — 아래 3.3 |
| 게이트 통과 | `False` | 기존과 동일 — 독립 `ingest()` (예: 부모 글이 언급한 *다른* 프로젝트/기사) |

`same_subject=False` 케이스를 남겨두는 이유: onehop 이 항상 "동일 주제의 부가 출처"만
발견하는 게 아니다 — 부모 글이 비교 대상으로 언급한 별개 프로젝트처럼, 독립 문서로 남는 게
맞는 경우도 실제로 있다(범위: 이런 경우까지 억지로 합치면 정보 손실).

### 3.3 병합 메커니즘 — 신규 함수 `merge_source_into_document()`

위치 제안: `pipeline.py` (dedup②의 in-place 갱신 경로와 형제 — `pipeline.py:132-159` 재사용
패턴).

```
parent.raw_text 뒤에 자식 본문을 별도 섹션으로 append
  (예: "\n\n---\n[추가 출처: {child.title}]\n{child.url}\n\n{child.raw_text}")
→ content_hash 재계산
→ dbm.update_document_content(conn, parent.id, ..., raw_text=merged_text)  # 기존 함수, 그대로 재사용
→ documents.meta['extra_sources'] 에 {url, canonical_url, title, source_type, added_at} append
   (신규 컬럼 아님 — set_document_images 와 동일 패턴의 새 헬퍼 set_document_extra_sources.
   documents.meta 는 이미 존재하는 JSON 컬럼이라 스키마 변경·마이그레이션 불필요)
→ extract_resolve_store(conn, provider, vstore, parent, report, vault_dir)  # doc.id 그대로 재실행
   → 엔티티는 resolve_or_create 가 병합된 본문 기준으로 다시 뽑아 기존 노드에 관찰 누적
   → render_detail 도 합쳐진 본문으로 재생성 → 실제로 "더 풍부한 글"이 됨(사용자 목표)
→ raw_inbox 는 신규 document_id 없이 status='merged', document_id=parent.id 로 기록
   (Layer-1 원문 보존 협약 — 자식 raw_text 도 artifact 로 별도 저장, 재추출 대비)
```

**이 경로는 새 Document 행도, 새 `expand_queue` 항목도 만들지 않는다** → 자식이 다시
onehop 을 타는 재귀·증폭 위험이 원천적으로 없음(기존 `expand_max=0` 정책과 별개로 이중
안전).

**리스크 재평가**: 최초엔 "스키마 변경 동반이라 위험 높음"으로 봤으나, `documents.meta`
JSON 컬럼을 재사용하면 **신규 컬럼·마이그레이션이 필요 없다** — `set_document_images`
(`db.py:1185`)와 완전히 같은 패턴이라 위험도가 낮아짐. 남는 리스크는 하나:
**재추출 시 엔티티 해석이 흔들릴 수 있음** — 그런데 이건 이미 `refresh_document()`(같은
`extract_resolve_store` on 동일 doc.id)가 라이브로 쓰고 있는 것과 동일한 리스크 범주라
신규 리스크가 아니라 기존에 이미 수용된 패턴의 재사용.

### 3.3a 실패 안전망 — 스냅샷 후 복원(트랜잭션 흉내, 사용자 결정 2026-07-13)

진짜 SQL 트랜잭션으로 감싸는 건 무리다 — `update_document_content`/`resolve_or_create`/
`upsert_relation` 등 관련 db.py 함수들이 각자 내부에서 즉시 `conn.commit()` 하는 구조라
(원자적 BEGIN/ROLLBACK 을 감싸려면 그 함수들을 전부 고쳐야 함) 이번 범위에서 손대지 않는다.
대신 **가볍게 스냅샷→복원**으로 흉내낸다(사용자 지시 "현황 상 편하고 좋은 걸로"):

```python
def merge_source_into_document(conn, provider, vstore, parent, child, *, vault_dir=None, data_dir=None) -> dict:
    original = dbm.get_document_row(conn, parent.id)          # 병합 전 스냅샷
    original_meta = json.loads(original["meta"] or "{}")
    try:
        merged_text, extra_sources = _build_merged_text(parent, child, original_meta)
        new_meta = {**original_meta, "extra_sources": extra_sources}
        dbm.update_document_content(conn, parent.id, title=parent.title,
            raw_text=merged_text, content_hash=content_hash(merged_text),
            fetched_at=time.time(), meta=new_meta)
        parent.raw_text = merged_text
        report = IngestReport(document_id=parent.id, title=parent.title, updated=True)
        ok, err = extract_resolve_store(conn, provider, vstore, parent, report, vault_dir=vault_dir)
        if not ok:
            raise RuntimeError(err)
        return {"merged": True, "report": report}
    except Exception as e:  # noqa: BLE001  — 실패 시 부모를 병합 이전 상태로 복원
        dbm.update_document_content(conn, parent.id, title=original["title"],
            raw_text=original["raw_text"], content_hash=original["content_hash"],
            fetched_at=original["fetched_at"], meta=original_meta)
        return {"merged": False, "error": str(e)}
```

- 가장 흔한 실패 모드(`provider.extract()` 의 rate-limit/quota 429 — TODO 1번에서 실제 관측된
  사례)는 `extract_resolve_store` 의 최상단에서 `(False, err)` 로 깔끔히 반환되므로, 엔티티
  루프가 시작되기 전 실패 → 위 복원으로 부모 문서는 병합 시도 흔적 없이 원상 복귀.
- **알려진 한계**: `extract_resolve_store` 의 엔티티/관계 루프 자체는 `provider.extract()` 만큼
  방어적으로 감싸여 있지 않다(예외가 나면 위로 전파) — 루프 도중 예외가 나면 이미 일부
  엔티티가 그래프에 커밋된 채로 위 except 가 잡아 raw_text/meta 는 복원되지만 **그 부분
  엔티티는 그대로 남는다**(고아 관찰). 이건 병합이 새로 만드는 위험이 아니라 `ingest()`/
  `refresh_document()` 도 이미 안고 있는 기존 리스크와 동급이라 이번 범위에서 별도로
  고치지 않는다(문서화만).

### 3.3b 병합 입력 상한 + 목표 분량(사용자 결정 2026-07-13)

**입력 쪽(저장 vs 프롬프트 투입은 분리)**: 저장(`documents.raw_text`)은 원문 보존 협약대로
**자르지 않는다** — 부모+자식 원문 전체를 그대로 이어붙여 DB 에 남긴다(나중에 재추출/감사
가능). 잘라야 하는 건 **LLM 호출에 투입하는 양**뿐이다. 현재 `_doc_to_prompt()`
(`gemini_provider.py:476-483`)가 `raw_text[:12000]` 로 하드코딩돼 있는데, 이걸 `limit`
매개변수로 빼고 **병합 문서는 2배(24000자)** 를 넘긴다(사용자 지시 "상한을 두배로") — 단일
출처 문서의 예산을 그대로 쓰면 자식 내용이 통째로 잘려나가 병합의 의미가 없고, 무제한으로
주면 토큰 비용이 튐. 2배가 "두 출처를 다 담되 과하지 않은" 절충.

**출력 쪽(render_detail 목표 분량 + 재시도)**: 현재 프롬프트의 "대략 A4 1~2장 분량"
(`gemini_provider.py:230`)을 병합 문서는 "대략 A4 2~4장 분량, 각 출처의 핵심을 빠짐없이
통합해 서술"로 올려 요청(사용자 지시 "프롬프트로 넉넉히 마진을 두고 희망 글자수 요청"). 결과가
너무 짧으면(예: 1000자 미만 — 두 출처를 담기엔 명백히 부족한 하한선) **목표 분량을 2배로
올려 재시도, 최대 n=2회**(즉 1x→2x→4x 로 최대 3회 시도, 마지막 결과를 그대로 채택 —
`ensure_document_detail` 의 기존 "실패는 조용히 스킵" 관용과 동일하게 fail-open. 품질
향상용이지 정합성 문제가 아니므로 이 이상 정교화하지 않는다). 정확한 하한선·배수는 M5
실측(§6)에서 실 데이터로 조정.

### 3.4 onehop 후보 필터 갱신 — 병합된 URL 은 재제안 금지

`onehop.py:_already_ingested()` 는 `documents.canonical_url` 만 본다. 병합 경로는 새
Document 를 안 만들므로 이 체크를 통과 못 한다 — 같은 자식 URL 이 나중에 다른 부모의
후보로 다시 뜰 수 있음. `_already_ingested` 를 확장해 `documents.meta` 의
`extra_sources[].canonical_url` 도 함께 조회(신규 `dbm.find_document_by_extra_source()`).

### 3.5 UI — 병합된 소스 노출

문서 상세(`/document`, `graphview.py` 노드/문서 패널)에 "출처 N개" 표기 + 원문 링크 나열
(사용자 요구 "원문 링크도 머지"). `extra_sources` 를 읽어 원 URL(부모 자신의 url/canonical_url)
+ 병합된 자식들을 함께 리스트업. 목록(`documents_list_route`)은 변경 불필요 — 애초에 별도
행이 생기지 않으므로 자동으로 뻥튀기가 없어짐.

---

## 4. 마일스톤

| 단계 | 내용 | 검증 | 상태 |
|---|---|---|---|
| **M1** | `ResearchJudgement` + 두 provider(gemini/mock) 에 `same_subject` 필드 추가 | 단위 테스트(mock 판정값 검증) | ✅ 완료(2026-07-13) |
| **M2** | `merge_source_into_document()`(스냅샷/복원 포함, §3.3a) + `set_document_extra_sources`/`find_document_by_extra_source`(db.py) + `_doc_to_prompt` limit 매개변수화(§3.3b) | 단위 테스트(merge 성공 시 raw_text·meta·엔티티 관찰 수 / **extract 실패 mock 주입 시 원본으로 정확히 복원되는지**) | ✅ 완료(2026-07-13) |
| **M3** | `service.py:expand_document()` 3분기 배선 + `onehop.py` 후보 필터 갱신 | 단위 + mock provider e2e(부모+자식 후보 → 병합 1건, 목록엔 1개만) | ✅ 완료(2026-07-13) |
| **M4** | UI 출처 노출(`/document` 패널·크게읽기·공유 페이지 3곳) | JS 문법(`node --check`, GRAPH_HTML/_SHARED_HTML 평가 후) | ✅ 완료(2026-07-13, 실브라우저 Playwright 는 미실행) |
| **M5** | 실 Gemini 로 `same_subject` 판정 정확도 확인(진짜 같은 주제 vs 진짜 별개 소재 표본 몇 건) + 원격 배포 | 실 API 호출, before/after 비교 | ⏳ 미착수 — 배포·실비용 호출이라 사용자 확인 후 |

각 단계 검증까지 끝낸 뒤 다음으로(빅뱅 금지, GOALS 원칙). 로컬 `scripts/ci.sh`(uv.lock 동기 + pytest 188개, 신규 `tests/test_onehop_merge.py` 5개 포함) 전부 통과.

---

## 5. 결정 사항 (2026-07-13, 사용자 확인 완료)

1. **병합 실패 시 정책 — 스냅샷 후 복원**(§3.3a). 진짜 트랜잭션은 db.py 전반의 즉시-commit
   구조 때문에 이번 범위에서 무리라 채택 안 함 — 대신 병합 전 원본을 스냅샷해두고 실패 시
   그대로 되돌리는 가벼운 방식으로 결정("현황 상 편하고 좋은 걸로").
2. **병합 분량 상한 — 저장은 무제한, 프롬프트 투입만 2배 + 목표 분량 상향/재시도**(§3.3b).
   `_doc_to_prompt` 예산을 12000→24000자(병합 문서 한정)로, `render_detail` 목표 분량을
   "A4 1~2장"→"A4 2~4장"으로 올리고 결과가 너무 짧으면(<1000자) 목표를 2배씩 최대 2회
   재시도.
3. **`same_subject` 오판 위험 — 임계값 상향 등 별도 방어 불필요, 현행 게이트로 충분**.
   사용자 판단(2026-07-13) 근거 3가지, 전부 채택:
   - 병합해도 **각 출처의 내용이 요약이 아니라 병합된 본문 그대로** 들어간다(§3.3) — 오판이어도
     정보 손실이 아니라 "무관한 내용이 한 문서에 같이 실린" 정도.
   - **원문 링크는 각각 개별로 보존**된다(`extra_sources`, §3.3) — 어느 출처가 무엇이었는지
     추적 가능, 나중에 사람이 봐도 문제를 알아챌 수 있음.
   - onehop 은 **같은 조사(부모 문서)에서, 근접한 시간에** 발견된 링크만 대상이다(범위가
     이미 좁음, §1) — 시간·경로상 완전히 동떨어진 것끼리 합쳐질 여지 자체가 없음.

## 5a. 검토했으나 채택 안 함 — 그래프 관계성 기반 "병합 포기 조건"

**아이디어**: 부모·자식 각각의 엔티티가 추출된 뒤, 두 엔티티 집합이 그래프에서 전혀
연결되지 않으면(직접 겹침도, 관계 엣지도 없으면) `same_subject` LLM 판정을 불신하고 병합을
포기하는 조건을 추가하면 안전망이 되지 않을까 — 사용자 제안으로 **원격 운영 DB의 실제 onehop
쌍 81건**(claire_api 컨테이너, `raw_inbox.source LIKE 'onehop:%' AND status='done'`)을
대상으로 실측.

**측정 결과**: 부모 엔티티집합 P, 자식 엔티티집합 C 에 대해 "C 중 P 와 직접 겹치거나 관계
엣지로 연결된 비율"을 계산.

| 구간 | 건수 (81건 중) |
|---|---|
| 1.0 (완전 연결) | 61 (75%) |
| 0.5 ~ 1.0 | 10 |
| 0 ~ 0.5 | 4 |
| 0 (완전 무관) | 6 |

- **완전 무관(0) 6건 중 실제 내용을 확인하니, 가장 값어치 있는 병합 후보가 바로 여기 있었다**:
  예) "미국 소비자 60%, 브랜드 메시지의 AI에 거부감 | GeekNews" → 그 원문
  "Sixty percent of US consumers say 'AI' in brand messaging is a turnoff | Hacker News".
  **이건 GeekNews 큐레이션 글과 그 자신이 링크한 원문 기사** — same_subject 가 명백히
  참이어야 하는 사례인데, 짧은 한국어 코멘트와 영어 원문이 뽑는 엔티티의 언어·알갱이가
  달라 그래프 연결이 0으로 나왔다(3건은 부모 문서가 나중에 삭제돼 측정 불가라 제외).
- **완전 연결(1.0) 61건은 대부분 "툴/프로젝트 발표 글 → 그 자체의 github 레포"** 패턴 —
  제품명 엔티티가 양쪽에서 동일하게 뽑혀 자연스럽게 연결됨. 이 경우는 `same_subject` LLM
  판정만으로도 이미 잘 맞을 사례라 그래프 신호가 추가 정보를 주지 않는다(중복 확인).

**결론**: 이 신호는 **가장 흔하고 이미 잘 판정되는 케이스엔 군더더기**, **가장 값진데
드문 케이스(GeekNews↔원문)엔 오히려 오탐(false-reject)**을 유발하는 방향이라 도입하지
않는다. §5-3 의 사용자 논거(오판 피해가 원래 작음)와도 맞물려, 이 안전망 없이 진행하는 게
합리적.

---

## 6. 재사용 자산 요약

| 필요 | 기존 자산 | 위치 |
|---|---|---|
| in-place 본문 갱신 | `update_document_content` | `store/db.py:1154` |
| meta JSON 부분 갱신 패턴 | `set_document_images` | `store/db.py:1185` |
| 동일 doc.id 재추출 | `extract_resolve_store` | `ingest/pipeline.py:231` |
| 판정 구조화 출력 | `ResearchJudgement`/`judge_research` | `extract/gemini_provider.py:328` |
| Layer-1 원문 보존 | `raw_inbox` + `save_artifact` | `ingest/pipeline.py:176-183` |
