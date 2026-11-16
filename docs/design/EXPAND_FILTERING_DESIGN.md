# One-Hop Expansion: Depth, Relevance Filtering, and Same-Subject Merge

- **Status**: Partly implemented. Implemented: fixed depth of one, the heuristic link filter, LLM selection, the relevance and quality gate, and the same-subject merge. Current behavior: [`ONEHOP_MERGE.md`](../implementation/ONEHOP_MERGE.md). The pre-LLM content guard of the web fetcher: [`INGESTION.md`](../implementation/INGESTION.md). Planned, not implemented: the items in "Planned work" below.
- **Date**: 2026-08-19 (expansion filtering), 2026-07-13 (same-subject merge, owner decisions) · restructured 2026-11-16

## Goal / problem

Automatic expansion follows external links in an ingested document and adds related knowledge to the graph. Without control it causes four problems:

1. **Depth explosion.** Children follow their own links without end, and the graph and the system load grow fast.
2. **Topic drift and noise.** Homonyms, advertisements, terms pages, and social links add wrong nodes and make search and synthesis worse.
3. **Document bloat.** An introduction article and the primary source of the same subject (for example a GeekNews post and the GitHub repository of the project) become separate documents. The owner saw the document list grow two to four times with the same content.
4. **LLM cost on blocked pages.** Bot challenges, 404 and 403 pages, and login gates cause model calls that give nothing.

## Decisions in force

1. **Depth is always one.** A child stored as a new document cannot start a new expansion, and a merged child makes no new document or queue item. Reason: depth explosion is the largest risk, and one hop gives most of the value.
2. **The filter is a funnel, from cheap to expensive.** Deterministic host and path rules and the "already ingested" check come first, then the LLM selection, then the fetch and the content guard, then the LLM gate. Reason: each step removes noise before the next, more expensive step.
3. **The LLM decides both "follow" and "store".** Heuristics only remove obvious noise. A missing selection method follows nothing (conservative).
4. **The gate is fail-closed, and relevance is stricter than quality.** A failed judgement scores zero. Reason: pollution is worse than thin content, because a wrong homonym node misleads all later search and synthesis. The contextual research flow uses the same gate.
5. **Merge at judgement time, not grouping in the list (owner decision, 2026-07-13, option B).** When the gate passes and the child is about the same subject as the parent, the child text joins the parent document as an extra source. Reason: the owner wants one richer document, not a grouped list of near copies.
6. **The merge applies only to the one-hop chain.** Documents about the same subject that arrive independently at different times are out of scope.
7. **No new model call for the decision.** The same-subject flag is one more field of the existing relevance and quality judgement. Reason: cost.
8. **A child about a different subject stays a separate document.** A parent can mention another project for comparison. A forced merge would lose information.
9. **Snapshot and restore instead of a transaction (owner decision, 2026-07-13).** The database functions commit at once, so a real transaction would need changes in all of them. The merge keeps a snapshot of the parent and writes it back on failure. Entities that a failed re-extraction committed can stay; refresh and ingest have the same risk.
10. **Store all, cut only the model input (owner decision, 2026-07-13).** The merged text is stored complete. The extraction budget of a merged document is two times the single budget, and the detail prompt asks for a longer text, with retries when the result is too short. Reason: a single budget cuts away the child, and no limit makes the token cost jump. The retry is a quality step, so it is fail-open.
11. **No extra guard against a wrong same-subject flag (owner decision, 2026-07-13).** Reasons: the merged text keeps each source in full (no summary loss), each source link stays separate and traceable, and the scope is already narrow (links from one parent, found at about the same time).

## Rejected alternatives

- **Group near copies in the document list (option A).** The owner chose the merge (decision 5).
- **Refuse the merge when the entity sets of parent and child have no graph connection.** Measured on 81 one-hop pairs of the owner's server: 61 pairs were fully connected, 10 had a ratio from 0.5 to 1.0, 4 from 0 to 0.5, and 6 had no connection. The fully connected pairs were mostly "tool announcement and its own repository", where the LLM flag is already right. The unconnected pairs included the most valuable merges, for example a short Korean GeekNews comment and the English original article it links: the entities differ in language and grain. The signal adds nothing to the easy case and rejects the valuable case, so it was not adopted.

## Planned work (not implemented)

### 1) 도메인/주제 스코프 필터링 (Topic Scope Guard)
- **현황**: 현재는 일반적인 SNS나 boilerplate 페이지만 블랙리스트로 거르고 있어, 지식베이스의 핵심 관심사(예: AI, 소프트웨어 엔지니어링, 인프라 등)와 무관한 일반 상업/뉴스 링크가 LLM 선별 단계까지 진입할 수 있음.
- **개선안**:
  - `[ ]` 사용자 관심사 키워드 또는 도메인 허용/우선순위 목록을 기반으로 한 0차 스코프 필터 도입 검토.

### 2) 앵커 텍스트 누락 보완 (Context Window Extraction)
- **현황**: HTML `<a>` 태그가 아닌 일반 텍스트/마크다운에서 정규식으로 추출된 URL은 앵커 텍스트가 비어 있어 LLM 선별 정확도가 떨어질 수 있음.
- **개선안**:
  - `[ ]` 정규식 URL 추출 시 링크 앞뒤 $N$글자(예: 100자)의 문맥을 캡처하여 `link_anchors`의 대체 텍스트로 공급.

### 3) 큐 부하 및 중요도 기반 적응형 상한 제어 (Adaptive Capping)
- **현황**: 고정값 `CLAIRE_EXPAND_MAX=5`를 사용하므로, 부모 문서 1건당 최대 15회 이상의 LLM 호출이 발생할 수 있음.
- **개선안**:
  - `[ ]` 부모 문서의 분량/품질 점수 또는 `expand_queue`의 대기열 크기에 따라 `expand_max`를 1~3개로 동적 조절하는 적응형 스케줄링.

### 4) 다단계(Multi-hop, 2홉 이상) 확장 고려 시 아키텍처 요건
- **요건**:
  - 만약 향후 2홉 이상 확장을 검토할 경우:
    1. `documents` 테이블에 `depth: int` 컬럼 추가 및 계층 추적
    2. 홉 깊이에 따른 지수 감쇠(Decay) 임계값 적용 (1홉 0.7 $\to$ 2홉 0.85)
    3. 트리 단위 글로벌 토큰/문서 상한 예산(Budgeting)
    4. 그래프 기반 URL 순환/사이클 감지(Cycle Detection)

### 5) Measure the same-subject flag

Not done: compare the flag of the real model with a small set of true same-subject pairs and true different-subject pairs (milestone M5 of the 2026-07-13 plan). It needs real model calls, so it waits for the owner.
