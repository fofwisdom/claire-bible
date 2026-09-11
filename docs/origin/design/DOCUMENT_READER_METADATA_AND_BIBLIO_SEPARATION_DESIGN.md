# 문서 리더 UI 위계 및 원문 정보 / 적재 메타데이터(docmeta) 분리 설계 명세서

**문서 번호:** DESIGN-UI-20260911-01  
**작성일:** 2026-09-11  
**상태:** **설계 및 구현 확정 (Implemented)**  
**관련 모듈:** `src/claire/static/js/reader.js`, `src/claire/static/js/app.js`, `src/claire/templates/share.html`, `src/claire/static/css/reader.css`  
**상위 문서:** [`docs/origin/README.md`](../README.md), [`GRAPHVIEW_MODULARIZATION_AND_STATIC_ASSET_DESIGN.md`](GRAPHVIEW_MODULARIZATION_AND_STATIC_ASSET_DESIGN.md)

---

## 1. 배경 및 안티패턴 문제 정의

### 1.1 UI 요소 임의 증식 및 개념 왜곡 (Anti-patterns)
과거 기능 개발 및 권한 조정 과정에서 다음과 같은 심각한 설계 원칙 위반과 UI 오염이 발생했습니다:

1. **무분별한 단추 증식 (Random Buttons)**:
   - 특정 기능(예: 익명 사용자 문서 공유 링크 생성)의 권한을 확장할 때, 기존에 확립된 단일 액션 위치(리더 상단 헤더의 `🔗`)를 존중하지 않고 우측 상세 패널(`#panel`)이나 다른 영역에 임의로 중복 단추와 입력창을 마구잡이로 덧붙여 UI 위계를 파괴함.
2. **첫 번째 행의 개념적 혼동과 docmeta 훼손**:
   - 문서 제목 바로 아래의 첫 번째 행 전체를 안일하게 `docmeta`로 뭉뚱그려 부르고, 원문 리소스 접근 단추(`↗ 원문 열기`)와 시스템 가공 뱃지(`docmeta`), 그리고 원문 저작물 속성인 서지 정보(`✍️ 저자`)를 무분별하게 한데 뒤섞음.
   - 서지 위치를 분리하려는 과정에서 첫 행의 `docmeta` 컨테이너 자체를 증발시키거나 고유 스타일을 탈취하는 등의 파괴적 변경을 초래함.
3. **서지 정보의 2중/3중 중복 노출 (SSOT 위반)**:
   - 상세(`detail`) 본문 최상단에 텍스트로 `저자: ... | 발행: ...`를 적어두고, 동시에 상단 `docmeta` 뱃지 영역에도 `✍️ 저자 (발행일)` 배지를 꽂아 넣어 동일 정보가 화면 여러 곳에 지저분하게 중복 노출됨.

---

## 2. 정규화된 개념 모델 (Normalized Conceptual Model)

지식 문서 인터페이스에서 다루는 정보는 대상과 층위(Layer)에 따라 엄격하게 분리됩니다.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ [문서 제목] (Title)                                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│ [첫 번째 행]                                                                 │
│   (좌측: 원문 관련 단추)               (우측: 적재 문서 메타데이터 'docmeta')  │
│   ↗ 원문 열기                          🎯 초점   ✂️ 절단율   ⚠️ 파서 폴백      │
├─────────────────────────────────────────────────────────────────────────────┤
│ [요약 섹션] (Summary)                                                       │
│   ... 문서 핵심 요약 본문 ...                                               │
├─────────────────────────────────────────────────────────────────────────────┤
│ [상세 섹션] (Detail)                                                        │
│   ... 순수 지식 본문 내용 (서지 정보 행 일체 없음) ...                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.1 원문 액션 단추 (Original Source Actions)
- **정의**: 원본 저작물(Original Work)로 연결되는 사용자 액션 단추.
- **위치**: 첫 번째 행 **좌측**
- **항목**: `↗ 원문 열기` (`url`), `↗ Presentation PDF` (`presentation_pdf.public_url`), `↗ 전사 열기` (STT 전사 텍스트 뷰어)
- **주의**: 이는 원본 리소스로 연결되는 링크이며, 시스템 가공 메타데이터인 `docmeta`가 아님.

### 2.2 적재 문서 메타데이터 (`docmeta`)
- **정의**: Claire 시스템이 원문을 수집·가공·적재(Ingestion & Processing)할 때 발생한 **시스템 파이프라인의 이력 및 상태 메타데이터(Provenance)**.
- **위치**: 첫 번째 행 **우측** (`.docmeta .docmeta-tags`)
- **표현**: 고유한 시스템 뱃지 꾸밈(Badge Chips)을 유지.
- **항목**:
  - `🎯 초점`: 적재 시 지정한 프롬프트 지침/초점 (`directive`)
  - `✂️ 부록·참고문헌 제외` / `✂️ 원문 일부 절단`: 예산 상한에 따른 절단율 (`trunc-tag`)
  - `⚠️ Docling 폴백 (PyPDF)`: 파서 실행 및 폴백 이력 (`parser-fallback-tag`)
  - `🎙️ STT`: 음성 인식 전사 기반 적재 여부 (`stt-tag`)
  - `CC×PDF` / `STT×PDF`: 비디오 자막 및 원본 슬라이드 PDF 동시 번들 적재 상태
- **절대 원칙**: 
  - **`docmeta`는 `sourcemeta`가 아니다.** 적재한 문서 자체의 파이프라인 메타데이터일 뿐이다.
  - 원 저작물 속성인 서지 정보(`저자`, `발행일`, `출처` 등)는 `docmeta` 뱃지 영역에 절대 혼입하지 않는다.
  - `doc.meta["biblio"]` 딕셔너리는 시스템에서 완전히 소각되었다.

### 2.3 서지 정보의 지식 그래프 환원 (Knowledge Graph SSOT)
- **절대 원칙**: **서지 정보는 지식 그래프의 전유물이다.**
  - 저자(Person: `이보미`), 소속/발행기관(Org: `한국금융연구원`), 간행물(Work: `금융브리프`) 등의 서지 정보는 오직 **온톨로지 지식 그래프(엔티티와 관계)**로만 모델링되고 탐색된다.
  - 문서(Document) 레벨에는 서지 정보를 일체 남기지 않는다.
    - 본문(`detail`) 최상단에 서지 정보를 비정형 텍스트로 적지 않는다 (프롬프트 규칙 13/7 폐지).
    - 문서 리더 UI에 서지 정보 행(`.docbiblio`)을 두지 않는다.
    - `doc.meta["biblio"]` 같은 임의의 서지 딕셔너리를 생성하지 않는다.

---

## 3. 구현 명세

### 3.1 JavaScript 렌더링 명세

#### 첫 번째 행: `docMetaHtml(dc)`
```javascript
function docMetaHtml(dc){
  if(!dc) return '';
  const hasUrl = !!dc.url;
  const isTrunc = !!(dc.raw_truncated || (dc.meta && dc.meta.raw_truncated));
  const directive = (dc.directive || (dc.meta && dc.meta.directive) || '').trim();
  const isParserFallback = !!(dc.pdf_parser_fallback || (dc.meta && dc.meta.pdf_parser_fallback));
  const presentation = dc.presentation_pdf || (dc.meta && dc.meta.presentation_pdf) || {};
  const hasPresentation = presentation.status === 'available' && !!presentation.public_url;
  const isStt = !!(dc.is_stt || (dc.meta && (dc.meta.is_stt || dc.meta.stt_applied || dc.meta.stt)));

  // 원문 단추나 docmeta 뱃지가 하나라도 존재하면 컨테이너 유지
  if(!hasUrl && !isTrunc && !directive && !isStt && !isParserFallback && !hasPresentation) return '';

  let h = '<p class=docmeta>';
  // 좌측: 원문 관련 단추
  if(hasUrl){
    h += '<a href="'+esc(dc.url)+'" target=_blank rel=noopener>↗ 원문 열기</a>';
    if(isStt) h += ' <a href="#" class="stt-link" onclick="openSttReader();return false;">↗ 전사 열기</a>';
    if(hasPresentation) h += ' <a href="'+esc(presentation.public_url)+'" target=_blank rel=noopener>↗ Presentation PDF</a>';
  } else if(isStt){
    h += '<a href="#" class="stt-link" onclick="openSttReader();return false;">↗ 전사 열기</a>';
  }

  // 우측: 적재 문서 메타데이터 (docmeta 뱃지)
  let tags = [];
  if(isParserFallback) tags.push('<span class="trunc-tag parser-fallback-tag">⚠️ Docling 폴백 (PyPDF)</span>');
  if(directive) tags.push('<span class="directive-tag">🎯 '+esc(directive)+'</span>');
  if(isStt && !hasPresentation) tags.push('<span class="directive-tag stt-tag">🎙️ STT</span>');
  if(isTrunc) tags.push('<span class="trunc-tag">✂️ ...</span>');
  if(tags.length) h += '<span class="docmeta-tags">' + tags.join(' ') + '</span>';
  h += '</p>';
  return h;
}
```

---

## 4. 재발 방지를 위한 엔지니어링 가이드라인

> [!CAUTION]
> **금지 사항 (Forbidden Actions)**:
> 1. **`docmeta`와 `sourcemeta` 혼동 금지**: `docmeta`는 파이프라인 적재 이력(초점, 절단율, 파서 상태 등)이다. 원문 서지 정보를 `docmeta`로 둔갑시키거나 `doc.meta["biblio"]`를 부활시키지 마십시오.
> 2. **문서 레벨 서지 정보 강제 금지**: 문서 본문(`detail`)이나 UI 뷰어에 저자·발행일·출처 문자열을 강제로 인라인 삽입하지 마십시오.
> 3. **서지 정보는 지식 그래프의 전유물**: 저자, 출처, 소속 기관은 온톨로지 지식 노드(Person, Org, Work 등)로만 표현되며, 그래프 탐색 및 지식 노드 목록을 통해 접근합니다.
> 4. **첫 행의 파괴적 축소/삭제 금지**: 첫 행은 좌측 원문 단추와 우측 `docmeta` 뱃지의 균형을 위한 필수 컨테이너입니다. 구조적 무결성을 훼손하지 마십시오.
