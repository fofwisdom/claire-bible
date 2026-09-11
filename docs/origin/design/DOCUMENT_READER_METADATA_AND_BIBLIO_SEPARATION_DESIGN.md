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
│ [서지 정보 행] (상세 첫 줄 스타일의 담백한 텍스트 한 줄)                      │
│   저자: Vaswani et al. | 발행일: 2017-06-12 | 출처: NeurIPS 2017            │
├─────────────────────────────────────────────────────────────────────────────┤
│ [요약 섹션] (Summary)                                                       │
│   ... 문서 핵심 요약 본문 ...                                               │
├─────────────────────────────────────────────────────────────────────────────┤
│ [상세 섹션] (Detail)                                                        │
│   ... 순수 본문 내용 (최상단 중복 서지 행 없음) ...                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.1 원문 정보 (Original Source Work Information)
- **정의**: 시스템이 가공하기 전, 원 저작물(Original Work) 자체가 지닌 고유 속성 및 원본 리소스.
- **구성 및 배치**:
  1. **원문 관련 단추 (Original Source Actions)**:
     - **위치**: 첫 번째 행 **좌측**
     - **항목**: `↗ 원문 열기` (`url`), `↗ Presentation PDF` (`presentation_pdf.public_url`), `↗ 전사 열기` (STT 전사 텍스트 뷰어)
     - **주의**: 이는 원본 저작물로 연결되는 액션 단추이며, 시스템 가공 메타데이터인 `docmeta`가 아님.
  2. **서지 정보 텍스트 행 (Bibliographic Line)**:
     - **위치**: **`요약(Summary)` 섹션 바로 위**
     - **항목**: `저자: ... | 발행일: ... | 출처: ... | DOI: ...`
     - **스타일**: 화려한 뱃지나 불필요한 아이콘(`🏛️`, `📅`, `✍️` 등) 없이, 상세 본문 첫 줄처럼 **담백하고 차분한 단일 텍스트 행(`.docbiblio`)**으로 처리.

### 2.2 적재 문서 메타데이터 (`docmeta`)
- **정의**: Claire 시스템이 원문을 수집·가공·적재(Ingestion & Processing)할 때 발생한 시스템 파이프라인의 이력 및 상태 메타데이터(Provenance).
- **위치**: 첫 번째 행 **우측** (`.docmeta .docmeta-tags`)
- **표현**: 고유한 시스템 뱃지 꾸밈(Badge Chips)을 유지.
- **항목**:
  - `🎯 초점`: 적재 시 지정한 프롬프트 지침/초점 (`directive`)
  - `✂️ 부록·참고문헌 제외` / `✂️ 원문 일부 절단`: 예산 상한에 따른 절단율 (`trunc-tag`)
  - `⚠️ Docling 폴백 (PyPDF)`: 파서 실행 및 폴백 이력 (`parser-fallback-tag`)
  - `🎙️ STT`: 음성 인식 전사 기반 적재 여부 (`stt-tag`)
  - `CC×PDF` / `STT×PDF`: 비디오 자막 및 원본 슬라이드 PDF 동시 번들 적재 상태
- **절대 원칙**: 원 저작물 속성인 서지 정보(`저자`, `발행일` 등)는 `docmeta` 뱃지 영역에 혼입하지 않는다.

### 2.3 본문 계층 (요약 및 상세)
- **단일 출처(SSOT) 준수**: 서지 정보가 `요약` 바로 위에 담백한 텍스트 행으로 단일 정규화되므로, `상세(detail)` 본문 최상단에 중복 기재되던 서지 행은 노출되지 않도록 하여 본연의 지식 콘텐츠로 시작한다.

---

## 3. 구현 명세

### 3.1 JavaScript 렌더링 명세

#### 1) 첫 번째 행: `docMetaHtml(dc)`
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
  if(hasPresentation) tags.push('<span class="directive-tag">'+...+'</span>');
  if(directive) tags.push('<span class="directive-tag">🎯 '+esc(directive)+'</span>');
  if(isStt && !hasPresentation) tags.push('<span class="directive-tag stt-tag">🎙️ STT</span>');
  if(isTrunc) tags.push('<span class="trunc-tag">✂️ ...</span>');
  if(tags.length) h += '<span class="docmeta-tags">' + tags.join(' ') + '</span>';
  h += '</p>';
  return h;
}
```

#### 2) 요약 상단 서지 행: `docBiblioHtml(dc)`
```javascript
function docBiblioHtml(dc){
  if(!dc) return '';
  const author = (dc.author || (dc.meta && dc.meta.author) || (dc.biblio && dc.biblio.author) || (dc.meta && dc.meta.biblio && dc.meta.biblio.author) || '').trim();
  const pubAt = (dc.published_at || (dc.meta && dc.meta.published_at) || (dc.biblio && dc.biblio.published_at) || (dc.meta && dc.meta.biblio && dc.meta.biblio.published_at) || '').trim();
  const biblio = (dc.biblio || (dc.meta && dc.meta.biblio)) || {};
  const venue = (biblio.venue || '').trim();
  const doi = (biblio.doi || '').trim();

  const parts = [];
  if(author) parts.push('저자: ' + esc(author));
  if(pubAt) parts.push('발행일: ' + esc(pubAt));
  if(venue) parts.push('출처: ' + esc(venue));
  if(doi) parts.push('DOI: ' + esc(doi));
  if(!parts.length) return '';
  return '<p class="docbiblio">' + parts.join(' | ') + '</p>';
}
```

### 3.2 스타일 명세 (`reader.css`, `workspace.css`)
```css
/* --- Bibliographic Row (.docbiblio) --- */
.docbiblio {
  color: var(--muted);
  font-size: 12.5px;
  margin: .6em 0 .8em;
  line-height: 1.5;
}
```
- 배경색, 테두리, 과도한 패딩을 부여하지 않고 본문 서두 텍스트로서의 가독성과 담백함을 보장합니다.

---

## 4. 재발 방지를 위한 엔지니어링 가이드라인

> [!CAUTION]
> **금지 사항 (Forbidden Actions)**:
> 1. **임의의 단추 추가 금지**: 기능이나 권한이 확장될 때 화면 빈자리에 임의의 버튼이나 패널을 증식시키지 마십시오. 모든 사용자 액션은 사전에 합의된 단일 캐노니컬 위치에만 둡니다.
> 2. **첫 행의 파괴적 축소/삭제 금지**: 첫 행은 좌측 원문 단추와 우측 `docmeta` 뱃지의 균형을 위한 필수 컨테이너입니다. 특정 하위 항목을 이동할 때 첫 행의 구조적 무결성을 훼손해서는 안 됩니다.
> 3. **메타데이터와 원문 정보의 혼용 금지**: '원문 관련 단추'나 '서지 정보'를 적재 가공 이력인 `docmeta`에 뒤섞거나, `docmeta` 뱃지를 원문 정보 영역에 복제하지 마십시오.
> 4. **텍스트 정보의 과도한 뱃지화 지양**: 서지 정보는 본문의 맥락을 형성하는 읽기 정보이므로, 뱃지나 아이콘 남발 대신 상세 첫 줄처럼 담백한 텍스트로 처리합니다.
