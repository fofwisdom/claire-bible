/* Proposal 4 (Gemini Neon) interactive 5-stage optical pipeline shared across /about and /support/pipeline. */
(function (root) {
  "use strict";

  var mountCounter = 0;

  var ICON_PATHS = {
    bolt: '<path d="M9 2 4 9h3.5L7 14l5-7H8.5z"/>',
    bot: '<path d="M3.5 6h9v6.5h-9zM8 6V3.5M6.2 9v.6M9.8 9v.6"/><circle cx="8" cy="3" r=".6"/>',
    pause: '<path d="M5 3.5v9M11 3.5v9"/>',
    play: '<path d="M5 3.5v9l7-4.5z"/>',
    prev: '<path d="m10 3.5-4.5 4.5 4.5 4.5"/>',
    next: '<path d="m6 3.5 4.5 4.5-4.5 4.5"/>',
    spark: '<path d="M8 2v3.5M8 10.5V14M2 8h3.5M10.5 8H14M4 4l2.2 2.2M9.8 9.8 12 12M12 4 9.8 6.2M6.2 9.8 4 12"/>',
    layers: '<path d="M8 2.5 13.5 5.5 8 8.5 2.5 5.5zM2.5 8.5 8 11.5l5.5-3M2.5 11.5 8 14.5l5.5-3"/>',
    eye: '<path d="M1.5 8c1.6-2.8 3.8-4.2 6.5-4.2s4.9 1.4 6.5 4.2c-1.6 2.8-3.8 4.2-6.5 4.2S3.1 10.8 1.5 8z"/><circle cx="8" cy="8" r="2"/>',
    chevron: '<path d="m4.5 6 3.5 4 3.5-4"/>'
  };

  function iconSvg(name, cls) {
    if (root.ClaireIcons && typeof root.ClaireIcons.svg === "function" && root.ClaireIcons.has && root.ClaireIcons.has(name)) {
      return root.ClaireIcons.svg(name, cls);
    }
    var body = ICON_PATHS[name] || "";
    return '<svg class="lineicon' + (cls ? " " + cls : "") + '" viewBox="0 0 16 16" aria-hidden="true" focusable="false">' + body + "</svg>";
  }

  function esc(val) {
    return String(val == null ? "" : val).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function interpolate(tmpl, vars) {
    if (!vars) return String(tmpl || "");
    return String(tmpl || "").replace(/\{([a-zA-Z0-9_]+)\}/g, function (m, k) {
      return Object.prototype.hasOwnProperty.call(vars, k) && vars[k] != null ? String(vars[k]) : m;
    });
  }

  function makeTranslator(data) {
    var dict = data && data.i18n && typeof data.i18n === "object" ? data.i18n : {};
    return function tr(key, fallback, vars) {
      var shortKey = key.indexOf("pipeline.neon.") === 0 ? key.slice("pipeline.neon.".length) : key;
      var fullKey = key.indexOf("pipeline.neon.") === 0 || key.indexOf("rendition.") === 0 ? key : "pipeline.neon." + key;
      if (Object.prototype.hasOwnProperty.call(dict, fullKey) && typeof dict[fullKey] === "string") {
        return interpolate(dict[fullKey], vars);
      }
      if (Object.prototype.hasOwnProperty.call(dict, shortKey) && typeof dict[shortKey] === "string") {
        return interpolate(dict[shortKey], vars);
      }
      if (root.ClaireI18n && typeof root.ClaireI18n.t === "function") {
        var resolved = root.ClaireI18n.t(fullKey, vars);
        if (resolved && resolved !== fullKey) return resolved;
      }
      return interpolate(fallback || key, vars);
    };
  }

  function renderArcaneEmblem(uid) {
    var flareId = "p4-flare-" + uid;
    return '<svg class="p4-emblem" viewBox="0 0 64 64" aria-hidden="true" focusable="false">' +
      '<defs><radialGradient id="' + flareId + '" cx="50%" cy="50%" r="50%"><stop offset="0%" stop-color="#ffffff"/><stop offset="45%" stop-color="#58d5ff" stop-opacity="0.7"/><stop offset="100%" stop-color="#0080ff" stop-opacity="0"/></radialGradient></defs>' +
      '<circle cx="32" cy="32" r="28" fill="rgba(0,245,184,0.08)" stroke="#00ffaa" stroke-width="1.4" stroke-opacity="0.72"/>' +
      '<ellipse cx="32" cy="32" rx="28" ry="11" fill="none" stroke="#26ffaa" stroke-opacity="0.4" stroke-width="1.1"/>' +
      '<ellipse cx="32" cy="32" rx="11" ry="28" fill="none" stroke="#38b6ff" stroke-opacity="0.4" stroke-width="1.1"/>' +
      '<polygon points="32,12 49,42 15,42" fill="none" stroke="#26ffaa" stroke-width="1.35" stroke-opacity="0.88"/>' +
      '<polygon points="32,52 49,22 15,22" fill="none" stroke="#a6ffe0" stroke-width="1.1" stroke-opacity="0.72"/>' +
      '<circle cx="32" cy="32" r="11" fill="url(#' + flareId + ')"/>' +
      '<circle cx="32" cy="32" r="3.4" fill="#ffffff"/>' +
      "</svg>";
  }

  function buildBeats(tr, langName) {
    return [
      {
        id: "ingest_seal",
        num: "01",
        kind: "rule",
        shortTitle: tr("pipeline.neon.beat1.short", "수집 · 원문 봉인"),
        title: tr("pipeline.neon.beat1.title", "01. 수집 · 전처리 · 원문 봉인 (A1)"),
        kindLabel: tr("pipeline.neon.kind.rule_full", "결정론적 규칙 (실선)"),
        pillText: tr("pipeline.neon.beat1.pill", "원문 불변 · raw_text"),
        input: tr("pipeline.neon.beat1.input", "URL, PDF/ODT/DOCX/TXT, 자유 메모, 첨부+링크 복합 입력"),
        apiAction: tr("pipeline.neon.beat1.api", "어디서 들어오든 사설망(SSRF) 차단 후 PDFium → 일반 어댑터 → Scrapling → CDP 순으로 본문을 얻습니다. 영상은 제작자 자막 → 자동 자막 → STT(gemini-3.5-transcribe) 우선순위이며, PDF/ODT는 classify 슬롯으로 서지를 보정해 raw_text에 불변 보관합니다."),
        clientAction: tr("pipeline.neon.beat1.client", "stage_raw(full_content=True)가 서버 STT·VLM·논문 분류를 끈 채 원문 전체와 SHA-256 해시를 수집해 pending 상태로 격리 저장합니다(검색·연합 미노출, agent_source_staged 알림 생략)."),
        output: tr("pipeline.neon.beat1.output", "raw_inbox 상태 행 · 불변 raw_text · content_hash"),
        failClosed: tr("pipeline.neon.beat1.fail", "사설망 IP, 빈 PDF, 선호 자막 내려받기 실패, 300자 미만 차단 페이지는 오류로 기록하고 수집을 중단합니다."),
        accSummary: tr("pipeline.neon.beat1.acc_title", "세부 참조: 입력 경로 통합 원칙 · 동영상 자막→STT 우선순위"),
        accLines: [
          tr("pipeline.neon.beat1.acc_1", "입력 경로는 달라도 관문은 하나: 웹 UI, REST API, CLI, 텔레그램 봇(단일·복합 2배 예산), 피드·연합(/syndication), MCP 스테이징이 모두 동일한 사설망 가드와 raw_text 불변 관문을 통과합니다."),
          tr("pipeline.neon.beat1.acc_2", "동영상 4단계 우선순위: (1) 제작자 자막 → (2) 자동 생성 자막 → (3) 자막 없을 때만 gemini-3.5-transcribe → (4) 모두 없으면 제목·설명·챕터만 남기고 3일간 캐시합니다.")
        ]
      },
      {
        id: "guard_budget",
        num: "02",
        kind: "rule",
        shortTitle: tr("pipeline.neon.beat2.short", "정규화 · 가드 · 예산"),
        title: tr("pipeline.neon.beat2.title", "02. 중복 판별 · 내용 가드 · 예산 결정"),
        kindLabel: tr("pipeline.neon.kind.rule_full", "결정론적 규칙 (실선)"),
        pillText: tr("pipeline.neon.beat2.pill", "가드 통과 · 노력 고정"),
        input: tr("pipeline.neon.beat2.input", "정규화된 URL, raw_text, content_hash, paper_classification"),
        apiAction: tr("pipeline.neon.beat2.api", "툼스톤(purged_tombstones), canonical URL(기존 문서 갱신), 해시·MinHash 중복과 저품질 가드(validate_extractable_content)를 통과한 뒤, 표(table-exemption)를 보존한 채 예산과 논문 PDF 적응형 노력(high)을 계산합니다."),
        clientAction: tr("pipeline.neon.beat2.client", "중복·가드 확인 후 full_content=True 원문 전체를 자르지 않고 보존하며, 이후 에이전트가 claim_token 임대와 nonce 청크 읽기를 시작할 수 있게 잠금 해시를 준비합니다."),
        output: tr("pipeline.neon.beat2.output", "통과한 문서 레코드 · 표 보존 프롬프트 예산 · applied_effort"),
        failClosed: tr("pipeline.neon.beat2.fail", "툼스톤 차단(force=True만 해제), 저품질·차단 페이지(insufficient_content), 미지원 --effort 값(400)은 추출 전에 즉시 거절됩니다."),
        accSummary: tr("pipeline.neon.beat2.acc_title", "세부 참조: 원문 표 보존과 적응형 추론 노력 규칙"),
        accLines: [
          tr("pipeline.neon.beat2.acc_1", "표 면제 규칙: 원문 길이가 예산을 초과할 때도 마크다운·AsciiDoc 표 블록을 우선 보존해 구조화 정보 손실을 막습니다."),
          tr("pipeline.neon.beat2.acc_2", "논문 적응형 노력: 15,000자 이상 논문 PDF는 high 추론 노력을 선택하되, 고정 노력 접미사가 붙은 모델이나 명시적 --effort 지정은 덮어쓰지 않습니다.")
        ]
      },
      {
        id: "extract_rendition",
        num: "03",
        kind: "ai",
        shortTitle: tr("pipeline.neon.beat3.short", "추출 · 정리본"),
        title: tr("pipeline.neon.beat3.title", "03. 구조화 추출 · AsciiDoc 정리본 렌더링"),
        kindLabel: tr("pipeline.neon.kind.ai_client", "AI 추론 / 에이전트 경계 (점선)"),
        pillText: tr("pipeline.neon.beat3.pill", "초록 + 3종 정리본 / AED9"),
        input: tr("pipeline.neon.beat3.input", "예산 적용 원문 + 닫힌 온톨로지(11 엔티티 · 11 관계) + 템플릿 규칙"),
        apiAction: tr("pipeline.neon.beat3.api", "extract_graph가 단일 호출로 {lang} 초록·핵심 주장·엔티티·관계를 추출합니다(summarize 슬롯이 다를 때만 초록을 재정제). 독립 병렬 분기인 render_detail이 대표 정리본(standard)과 선택적 concise·academic 정리본을 AOT HTML로 컴파일합니다.", { lang: langName }),
        clientAction: tr("pipeline.neon.beat3.client", "격리 서브에이전트(claire-extractor)가 <untrusted_source_document nonce=...> 경계로 청크 읽기 후 자체 LLM으로 초록·정리본·엔티티·관계를 생성하고, extract_submit이 스키마(extra=forbid)·AsciiDoc·근거 스팬·온톨로지 4계층 검증을 통과한 뒤 임베딩을 선계산합니다."),
        output: tr("pipeline.neon.beat3.output", "초록(summary) · 엔티티/관계 후보 · document_renditions (AOT HTML)"),
        failClosed: tr("pipeline.neon.beat3.fail", "에이전트 경로에서 마크다운 혼입, 가짜 닫힘 태그, 해시 불일치, 비허용 모델 문자열이면 DB 쓰기 전에 거부됩니다. 정리본 렌더링 실패는 그래프 저장을 막지 않습니다."),
        accSummary: tr("pipeline.neon.beat3.acc_title", "세부 참조: 정리본(Rendition) 3종 정책 · AED9 4계층 방어"),
        accLines: [
          tr("pipeline.neon.beat3.acc_1", "정리본 3종: standard(기본 · 원문 표 미사용 · 대표 수식만), concise(핵심 쟁점 · 고정 절 순서 · 짧은 범위), academic(원문 표 · 수식 유도 전체 보존)."),
          tr("pipeline.neon.beat3.acc_2", "에이전트 격리(AED9): (1) 셸/쓰기 도구 없는 서브에이전트, (2) 난수 nonce 경계, (3) 엄격 스키마 4계층 검증, (4) 선계산 임베딩 후 단일 트랜잭션 커밋.")
        ]
      },
      {
        id: "resolve_gates",
        num: "04",
        kind: "ai",
        shortTitle: tr("pipeline.neon.beat4.short", "엔티티 5관문 해소"),
        title: tr("pipeline.neon.beat4.title", "04. 엔티티 해소 5관문 · 하드 불변식 · 관계 검증"),
        kindLabel: tr("pipeline.neon.kind.gates", "규칙 + 벡터 + 조건부 판정"),
        pillText: tr("pipeline.neon.beat4.pill", "Gate 1~5 · 0.93 / 0.72"),
        input: tr("pipeline.neon.beat4.input", "후보 엔티티 프레임(이름·타입·별칭·관찰 최대 5개) + KB 엔티티/벡터"),
        apiAction: tr("pipeline.neon.beat4.api", "Gate 1(정규화·별칭·3자↑ 약어) 적중 시 임베딩을 건너뜁니다. 미스 시 3072d 코사인+FTS5로 후보를 찾고, Gate 2(버전 v1↔v2·2글자 대문자·타입) 불변식 검사 후 Gate 3(≥0.93 자동 병합) 또는 Gate 4(0.72~0.93 entity_judge 최대 3회)를 거쳐 횡단 관계(relation_judge)와 히트맵을 기록합니다."),
        clientAction: tr("pipeline.neon.beat4.client", "extract_submit은 DB 쓰기 전에 선계산한 3072d 임베딩으로 Gate 1(규칙), Gate 2(하드 불변식), Gate 3(≥0.93 자동 병합), Gate 5(신규 생성)까지만 실행합니다. Gate 4 LLM 판정, 히트맵, 횡단 relation_judge, Jev 그림자 평가는 실행하지 않습니다."),
        output: tr("pipeline.neon.beat4.output", "병합 또는 신규 entity_id · 검증된 relations 엣지 · resolution_log"),
        failClosed: tr("pipeline.neon.beat4.fail", "자기 루프 관계(A→A)와 도메인/범위 불일치(예: authored_by → Person|Org 외) 엣지는 적재 전에 차단됩니다. 서버/에이전트 모두 임계값 미만 저신뢰 병합을 허용하지 않습니다."),
        accSummary: tr("pipeline.neon.beat4.acc_title", "세부 참조: 엔티티 해소 5관문 임계값 · 온톨로지 도메인 검증"),
        accLines: [
          tr("pipeline.neon.beat4.acc_1", "고정 임계값: sim_tier_auto_merge = 0.93, sim_tier_borderline = 0.72, MAX_JUDGE = 3, relational_candidates ≥ 0.70."),
          tr("pipeline.neon.beat4.acc_2", "임베딩 입력 범위: 초록·상세 본문이 아닌 엔티티 프레임(이름·타입·별칭·관찰 최대 5개)만 3072차원 RETRIEVAL_DOCUMENT 벡터로 저장합니다.")
        ]
      },
      {
        id: "commit_follow",
        num: "05",
        kind: "rule",
        shortTitle: tr("pipeline.neon.beat5.short", "정본 커밋 · 발행 · 후속"),
        title: tr("pipeline.neon.beat5.title", "05. SQLite 정본 커밋 · Silvercord 발행 · 비동기 후속"),
        kindLabel: tr("pipeline.neon.kind.commit", "원자 트랜잭션 + 격리 후속"),
        pillText: tr("pipeline.neon.beat5.pill", "claire.db · Silvercord"),
        input: tr("pipeline.neon.beat5.input", "문서 본문, 정리본 HTML, 해소된 엔티티/관계, 단계별 model_lineage"),
        apiAction: tr("pipeline.neon.beat5.api", "정본 SQLite(claire.db, kb_id 격리)에 FTS5 전문 검색 색인(언어별 형태소 지원)·3072d 벡터·model_lineage를 저장하고 /silvercord/{token} 열람 경로를 엽니다. 신규 1차 적재이고 개인 KB(CT10)면 백그라운드 classify_watch·expand-loop(깊이 1, expand_max=0)가 이어집니다."),
        clientAction: tr("pipeline.neon.beat5.client", "_AtomicConnection 단일 트랜잭션으로 origin=agent_client 상태를 동기 커밋하고 /silvercord/{token}과 정본 색인을 즉시 완성합니다. classify_watch와 expand-loop 자동 확장은 실행하지 않습니다."),
        output: tr("pipeline.neon.beat5.output", "검색·그래프 UI·/silvercord/{token}·MCP·ActivityPub 발행 가능 정본"),
        failClosed: tr("pipeline.neon.beat5.fail", "원문(raw_text)과 출처 메타는 절대 덮어쓰지 않는 불변 원칙(A1)을 지키며 텔레메트리는 정본 DB와 물리 분리(A4)됩니다. 공유 KB는 소유 계정 과금 방지를 위해 expand·refresh·recover 루프가 돌지 않습니다(CT10)."),
        accSummary: tr("pipeline.neon.beat5.acc_title", "세부 참조: 개인 KB 3종 루프(CT10) · 재처리 안전장치(before_store)"),
        accLines: [
          tr("pipeline.neon.beat5.acc_1", "개인 KB 전용 3종 워커: expand-loop(깊이 1 고정), refresh-loop(동일 doc.id로 갱신해 엔티티 출처 보존), recover-loop(원래 extraction_mode 유지 · staged 건너뜀)."),
          tr("pipeline.neon.beat5.acc_2", "재생성 보호: 기존 문서를 다시 추출(regenerate)할 때는 extract_graph가 성공해 before_store가 호출되기 전까지 기존 그래프를 삭제하지 않습니다.")
        ]
      }
    ];
  }

  function buildRenditions(tr) {
    return [
      {
        id: "standard",
        label: tr("rendition.template.standard.label", "기본"),
        tables: tr("pipeline.neon.rend.standard.tables", "원문 표 미사용"),
        formulas: tr("pipeline.neon.rend.standard.formulas", "대표 공식만 유지"),
        preview: tr("pipeline.neon.rend.standard.preview", "= vLLM: PagedAttention 기반 LLM 서빙 엔진 (대표 정리본)\n\n== 구조와 동작\n가상 메모리 페이징을 KV 캐시에 적용합니다. 대표 공식 1종만 남기고 원문 표 대신 구조화 해설로 정리합니다.")
      },
      {
        id: "concise",
        label: tr("rendition.template.concise.label", "간결"),
        tables: tr("pipeline.neon.rend.concise.tables", "원문 표 미사용"),
        formulas: tr("pipeline.neon.rend.concise.formulas", "대표 공식만 유지"),
        preview: tr("pipeline.neon.rend.concise.preview", "= vLLM 핵심 쟁점 (간결 정리본)\n\n== 1. 핵심 문제\n연속 KV 캐시 예약으로 60% 이상 내부 단편화가 발생합니다.\n== 2. 해법\n비연속 물리 블록 매핑으로 처리량을 높입니다.")
      },
      {
        id: "academic",
        label: tr("rendition.template.academic.label", "학술"),
        tables: tr("pipeline.neon.rend.academic.tables", "원문 표 전체 보존"),
        formulas: tr("pipeline.neon.rend.academic.formulas", "수식·유도 전체 보존"),
        preview: tr("pipeline.neon.rend.academic.preview", "= PagedAttention과 vLLM: 원문 표 · 수식 유도 포함\n\n해석: 메모리 단편화율과 KV 블록 테이블 수식 유도를 생략 없이 보존하고 원문 성능 표 전체를 AsciiDoc으로 유지합니다.")
      }
    ];
  }

  function buildGates(tr, autoMerge, borderline) {
    return [
      { id: 1, title: tr("pipeline.neon.gate.1", "Gate 1 · 규칙 일치 (임베딩 없음)") },
      { id: 2, title: tr("pipeline.neon.gate.2", "Gate 2 · 하드 불변식 검사") },
      { id: 3, title: tr("pipeline.neon.gate.3", "Gate 3 · 고신뢰 벡터 자동 병합 (≥ {auto})", { auto: autoMerge }) },
      { id: 4, title: tr("pipeline.neon.gate.4", "Gate 4 · 경계 구간 LLM 판정 ({border} ~ {auto})", { border: borderline, auto: autoMerge }) },
      { id: 5, title: tr("pipeline.neon.gate.5", "Gate 5 · 신규 생성 및 관계 후보 수집") }
    ];
  }

  function mount(host, rawData) {
    if (!host) return;
    var uid = ++mountCounter;
    var data = rawData && typeof rawData === "object" ? rawData : {};
    var tr = makeTranslator(data);
    var models = data.models && typeof data.models === "object" ? data.models : {};
    var autoMergeNum = parseFloat(data.autoMerge || "0.93");
    if (!Number.isFinite(autoMergeNum)) autoMergeNum = 0.93;
    var borderlineNum = parseFloat(data.borderline || "0.72");
    if (!Number.isFinite(borderlineNum)) borderlineNum = 0.72;
    var autoMergeStr = autoMergeNum.toFixed(2);
    var borderlineStr = borderlineNum.toFixed(2);
    var langName = String(data.primaryLangName || "한국어");
    var showJev = Boolean(data.showJev);
    var showSimulators = data.showSimulators !== false;
    var beats = buildBeats(tr, langName);
    var renditions = buildRenditions(tr);
    var gates = buildGates(tr, autoMergeStr, borderlineStr);

    var reducedMotion = Boolean(root.matchMedia && root.matchMedia("(prefers-reduced-motion: reduce)").matches);
    var st = {
      stage: 0,
      mode: "provider",
      motionOn: !reducedMotion,
      gateScore: 0.84,
      gateExact: false,
      gateConflict: false,
      rendition: "standard",
      accOpen: false
    };

    function modelFor(key) {
      return String((models && models[key]) || "-");
    }

    function stageModelText(beatId, isClient) {
      if (beatId === "ingest_seal") {
        return isClient
          ? tr("pipeline.neon.model.beat1_client", "서버 STT · VLM · 분류 OFF (stage_raw)")
          : tr("pipeline.neon.model.beat1_api", "자막 우선 → {stt} / {classify}", { stt: modelFor("stt"), classify: modelFor("classify") });
      }
      if (beatId === "guard_budget") {
        return tr("pipeline.neon.model.beat2", "규칙 기반 (AI 호출 없음 · 해시 · 가드 · 예산)");
      }
      if (beatId === "extract_rendition") {
        return isClient
          ? modelFor("client_llm") + " + " + modelFor("embed")
          : modelFor("extract_graph") + " · " + modelFor("render_detail") + " · " + modelFor("summarize");
      }
      if (beatId === "resolve_gates") {
        return isClient
          ? tr("pipeline.neon.model.beat4_client", "규칙(G1~G2) + 선계산 {embed} (Gate 4 생략)", { embed: modelFor("embed") })
          : modelFor("embed") + " + " + modelFor("entity_judge") + " + " + modelFor("relation_judge");
      }
      return isClient
        ? tr("pipeline.neon.model.beat5_client", "원자 커밋 완료 (비동기 워커 생략)")
        : "claire.db FTS5 · " + modelFor("classify") + " · " + modelFor("research");
    }

    function renderSpecimen(beat, isClient) {
      if (beat.id === "ingest_seal") {
        return '<div class="p4-specimen-box">' +
          '<div class="p4-specimen-head"><strong class="p4-specimen-title">' + esc(tr("pipeline.neon.spec1.title", "입력 표본: vLLM 논문 PDF (24,300자)")) + '</strong>' +
          '<span class="p4-pill p4-pill-mint">' + esc(tr("pipeline.neon.spec1.badge", "A1 원문 불변")) + "</span></div>" +
          '<div class="p4-specimen-note">' + esc(tr("pipeline.neon.spec1.note", "어디서 들어오든 동일한 수집·사설망 가드 관문을 거칩니다 (단순 참고 출처):")) + "</div>" +
          '<div class="p4-chip-cloud">' +
          '<span class="p4-pill p4-pill-quiet">' + esc(tr("pipeline.neon.spec1.origin_web", "웹 UI · REST API · CLI")) + "</span>" +
          '<span class="p4-pill p4-pill-quiet">' + esc(tr("pipeline.neon.spec1.origin_tg", "텔레그램 DM (단일·복합)")) + "</span>" +
          '<span class="p4-pill p4-pill-quiet">' + esc(tr("pipeline.neon.spec1.origin_feed", "피드 · ActivityPub")) + "</span>" +
          '<span class="p4-pill p4-pill-quiet">' + esc(tr("pipeline.neon.spec1.origin_mcp", "MCP (OAuth 2.1)")) + "</span>" +
          "</div>" +
          '<div class="p4-specimen-meta">' +
          "<div><code>source_url</code> + <code>check_ip_safety</code> " + esc(tr("pipeline.neon.spec1.guard_ok", "사설망 차단 통과")) + "</div>" +
          "<div><code>sha256</code> → " + (isClient
            ? '<span class="p4-pill p4-pill-mint">' + esc(tr("pipeline.neon.spec1.client_pill", "full_content=True · pending 격리")) + "</span>"
            : '<span class="p4-pill p4-pill-azure">' + esc(tr("pipeline.neon.spec1.api_pill", "PDFium 추출 · 논문 분류 완료")) + "</span>") + "</div>" +
          "</div></div>";
      }

      if (beat.id === "guard_budget") {
        return '<div class="p4-specimen-box">' +
          '<div class="p4-specimen-head"><strong class="p4-specimen-title">' + esc(tr("pipeline.neon.spec2.title", "정규화 · 가드 판정 결과")) + '</strong>' +
          '<span class="p4-pill p4-pill-mint">' + esc(tr("pipeline.neon.spec2.badge", "결정론적 통과")) + "</span></div>" +
          '<div class="p4-gate-bar">' +
          '<div class="p4-gate-row" data-hit="true"><span>' + esc(tr("pipeline.neon.spec2.row1", "1. purged_tombstones / canonical URL 검사")) + "</span><span>" + esc(tr("pipeline.neon.spec2.row1_val", "통과 (신규)")) + "</span></div>" +
          '<div class="p4-gate-row" data-hit="true"><span>' + esc(tr("pipeline.neon.spec2.row2", "2. SHA-256 · MinHash 유사 중복 검사")) + "</span><span>" + esc(tr("pipeline.neon.spec2.row2_val", "중복 0건")) + "</span></div>" +
          '<div class="p4-gate-row" data-hit="true"><span>' + esc(tr("pipeline.neon.spec2.row3", "3. validate_extractable_content")) + "</span><span>" + esc(tr("pipeline.neon.spec2.row3_val", "24.3k자 PASS")) + "</span></div>" +
          '<div class="p4-gate-row" data-hit="' + (isClient ? "true" : "ai") + '"><span>' + esc(tr("pipeline.neon.spec2.row4", "4. 표 면제 예산 · 추론 노력 결정")) + "</span><span>" + esc(isClient ? tr("pipeline.neon.spec2.row4_client", "원문 전체 보존") : tr("pipeline.neon.spec2.row4_api", "표 보존 · effort: high")) + "</span></div>" +
          "</div></div>";
      }

      if (beat.id === "extract_rendition") {
        if (isClient) {
          return '<div class="p4-specimen-box">' +
            '<div class="p4-specimen-head"><strong class="p4-specimen-title">' + esc(tr("pipeline.neon.spec3_client.title", "에이전트 클라이언트 격리 추출 · 4계층 검증 (AED9)")) + '</strong>' +
            '<span class="p4-pill p4-pill-mint">' + esc(tr("pipeline.neon.spec3_client.badge", "extract_submit 검증")) + "</span></div>" +
            '<div class="p4-nonce-frame">' +
            '<code>&lt;untrusted_source_document nonce="7f3a9c..." sha256="..."&gt;</code>' +
            '<div class="p4-specimen-note">' + esc(tr("pipeline.neon.spec3_client.nonce_desc", "격리 서브에이전트(claire-extractor)가 셸·쓰기 도구 없이 청크 읽기 후 초록·정리본·엔티티·관계를 생성합니다.")) + "</div>" +
            "<code>&lt;/untrusted_source_document&gt;</code>" +
            "</div>" +
            '<div class="p4-chip-cloud">' +
            '<span class="p4-pill p4-pill-mint">' + esc(tr("pipeline.neon.spec3_client.l1", '1. extra="forbid" 스키마')) + "</span>" +
            '<span class="p4-pill p4-pill-mint">' + esc(tr("pipeline.neon.spec3_client.l2", "2. AsciiDoc 규칙 검사")) + "</span>" +
            '<span class="p4-pill p4-pill-mint">' + esc(tr("pipeline.neon.spec3_client.l3", "3. 근거 스팬 대조")) + "</span>" +
            '<span class="p4-pill p4-pill-mint">' + esc(tr("pipeline.neon.spec3_client.l4", "4. 온톨로지 · 임베딩 선계산")) + "</span>" +
            "</div></div>";
        }
        var rInfo = renditions.find(function (r) { return r.id === st.rendition; }) || renditions[0];
        return '<div class="p4-specimen-box">' +
          '<div class="p4-specimen-head"><strong class="p4-specimen-title">' + esc(tr("pipeline.neon.spec3_api.title", "초록 추출 + 정리본 템플릿 미리보기")) + '</strong>' +
          '<span class="p4-pill p4-pill-azure">' + esc(tr("pipeline.neon.spec3_api.badge", "독립 병렬 AOT HTML")) + "</span></div>" +
          '<div class="p4-gate-presets">' +
          renditions.map(function (r) {
            return '<button type="button" class="p4-btn p4-mode-btn" data-p4-rend="' + esc(r.id) + '" aria-pressed="' + (r.id === rInfo.id ? "true" : "false") + '">' + esc(r.label) + "</button>";
          }).join("") +
          "</div>" +
          '<pre class="p4-pre">' + esc(rInfo.preview) + "</pre>" +
          '<div class="p4-chip-cloud">' +
          '<span class="p4-pill p4-pill-quiet">' + esc(rInfo.tables) + "</span>" +
          '<span class="p4-pill p4-pill-quiet">' + esc(rInfo.formulas) + "</span>" +
          "</div></div>";
      }

      if (beat.id === "resolve_gates") {
        if (!showSimulators) {
          return '<div class="p4-specimen-box">' +
            '<div class="p4-specimen-head"><strong class="p4-specimen-title">' + esc(tr("pipeline.neon.spec4.title", "엔티티 해소 5관문 광학 게이지")) + "</strong></div>" +
            '<div class="p4-gate-bar">' + gates.map(function (g) {
              return '<div class="p4-gate-row"><span>' + esc(g.title) + "</span><span>-</span></div>";
            }).join("") + "</div></div>";
        }
        var gHit = 5;
        var gDesc = "";
        var scoreStr = st.gateScore.toFixed(2);
        if (st.gateExact) {
          gHit = 1;
          gDesc = tr("pipeline.neon.gate_desc.1", "Gate 1 통과: 정규화/별칭/약어 일치로 임베딩과 LLM 판정 없이 즉시 병합합니다.");
        } else if (st.gateConflict) {
          gHit = 2;
          gDesc = tr("pipeline.neon.gate_desc.2", "Gate 2 차단: 버전 번호 충돌(예: v1 ↔ v2)로 후보에서 제거되어 신규 엔티티로 분리됩니다.");
        } else if (st.gateScore >= autoMergeNum) {
          gHit = 3;
          gDesc = tr("pipeline.neon.gate_desc.3", "Gate 3 통과({score} ≥ {auto}): 고신뢰 3072d 코사인 유사도로 자동 병합합니다.", { score: scoreStr, auto: autoMergeStr });
        } else if (st.gateScore >= borderlineNum) {
          gHit = isClient ? 5 : 4;
          gDesc = isClient
            ? tr("pipeline.neon.gate_desc.4_client", "경계 구간({score})이지만 에이전트 클라이언트 모드에서는 Gate 4 LLM 판정을 생략하므로 Gate 5 신규 생성(+관계 후보)으로 진행합니다.", { score: scoreStr })
            : tr("pipeline.neon.gate_desc.4_api", "Gate 4 발동({border} ≤ {score} < {auto}): entity_judge(최대 3회)가 SAME/DIFFERENT를 판정합니다.", { score: scoreStr, border: borderlineStr, auto: autoMergeStr });
        } else {
          gHit = 5;
          gDesc = st.gateScore >= 0.70
            ? tr("pipeline.neon.gate_desc.5_rel", "Gate 5 신규 생성({score} ≥ 0.70: 관계 후보 등록)", { score: scoreStr })
            : tr("pipeline.neon.gate_desc.5_new", "Gate 5 신규 생성({score} < 0.70: 새 엔티티 저장)", { score: scoreStr });
        }
        var sliderId = "p4-gate-score-" + uid;
        var exactId = "p4-gate-exact-" + uid;
        var conflictId = "p4-gate-conflict-" + uid;
        return '<div class="p4-specimen-box">' +
          '<div class="p4-specimen-head"><strong class="p4-specimen-title">' + esc(tr("pipeline.neon.spec4.title", "엔티티 해소 5관문 광학 게이지")) + '</strong>' +
          '<span class="p4-pill ' + (isClient ? "p4-pill-mint" : "p4-pill-azure") + '">' + esc(isClient ? tr("pipeline.neon.spec4.mode_client", "G1~G3 + G5 (G4 생략)") : tr("pipeline.neon.spec4.mode_api", "G1~G5 전체 경로")) + "</span></div>" +
          '<div class="p4-gate-presets">' +
          '<button type="button" class="p4-btn" data-p4-gate-preset="exact">' + esc(tr("pipeline.neon.preset.exact", "규칙 일치")) + "</button>" +
          '<button type="button" class="p4-btn" data-p4-gate-preset="conflict">' + esc(tr("pipeline.neon.preset.conflict", "버전 충돌")) + "</button>" +
          '<button type="button" class="p4-btn" data-p4-gate-preset="auto">' + esc(tr("pipeline.neon.preset.auto", "0.95 자동")) + "</button>" +
          '<button type="button" class="p4-btn" data-p4-gate-preset="border">' + esc(tr("pipeline.neon.preset.border", "0.84 경계")) + "</button>" +
          '<button type="button" class="p4-btn" data-p4-gate-preset="new">' + esc(tr("pipeline.neon.preset.new", "0.52 신규")) + "</button>" +
          "</div>" +
          '<label class="p4-gate-slider-label" for="' + sliderId + '">' + esc(tr("pipeline.neon.spec4.score_label", "코사인 유사도 점수:")) + ' <strong style="color:#7bffcf;" data-p4-score-val>' + esc(scoreStr) + "</strong>" +
          '<input type="range" id="' + sliderId + '" class="p4-gate-slider" data-p4-gate-score min="0.40" max="1.00" step="0.01" value="' + esc(scoreStr) + '"/></label>' +
          '<div class="p4-gate-toggles">' +
          '<label for="' + exactId + '"><input type="checkbox" id="' + exactId + '" data-p4-gate-exact' + (st.gateExact ? " checked" : "") + "/> " + esc(tr("pipeline.neon.spec4.exact_check", "Gate 1 이름/별칭 일치")) + "</label>" +
          '<label for="' + conflictId + '"><input type="checkbox" id="' + conflictId + '" data-p4-gate-conflict' + (st.gateConflict ? " checked" : "") + "/> " + esc(tr("pipeline.neon.spec4.conflict_check", "Gate 2 버전/타입 충돌")) + "</label>" +
          "</div>" +
          '<div class="p4-gate-bar" data-p4-gate-rows>' +
          gates.map(function (g) {
            var hitState = "";
            if (g.id === gHit) hitState = g.id === 2 ? "blocked" : (g.id === 4 ? "ai" : "true");
            var badge = g.id === 4 && isClient
              ? tr("pipeline.neon.gate_state.skipped", "이 모드 생략")
              : (g.id === gHit ? (g.id === 2 ? tr("pipeline.neon.gate_state.blocked", "차단") : tr("pipeline.neon.gate_state.hit", "통과")) : tr("pipeline.neon.gate_state.wait", "대기"));
            return '<div class="p4-gate-row" data-gate-id="' + g.id + '"' + (hitState ? ' data-hit="' + hitState + '"' : "") + ">" +
              "<span>" + esc(g.title) + "</span><span>" + esc(badge) + "</span></div>";
          }).join("") +
          "</div>" +
          '<div class="p4-gate-result" data-p4-gate-result role="status" aria-live="polite">' + esc(gDesc) + "</div>" +
          (showJev && !isClient ? '<div><span class="p4-pill p4-pill-iris" data-jev-pill>' + iconSvg("eye") + " " + esc(tr("pipeline.neon.jev_admin", "관리자 관측: {jev}", { jev: modelFor("jev") })) + "</span></div>" : "") +
          "</div>";
      }

      var cTitleId = "p4-const-title-" + uid;
      var cDescId = "p4-const-desc-" + uid;
      var constSvg = '<svg viewBox="0 0 360 124" style="width:100%;height:auto;display:block;" role="img" aria-labelledby="' + cTitleId + " " + cDescId + '">' +
        '<title id="' + cTitleId + '">' + esc(tr("pipeline.neon.constellation.title", "저장된 지식 그래프 별자리 예시")) + "</title>" +
        '<desc id="' + cDescId + '">' + esc(tr("pipeline.neon.constellation.desc", "vLLM, PagedAttention, FlashAttention 엔티티와 uses, cites 관계가 정본 그래프에 저장된 모습입니다.")) + "</desc>" +
        '<line x1="72" y1="62" x2="180" y2="34" stroke="#26ffaa" stroke-width="2"/>' +
        '<line x1="180" y1="34" x2="292" y2="74" stroke="#38b6ff" stroke-width="1.8" stroke-dasharray="5 3"/>' +
        '<text x="114" y="38" fill="#8dffda" font-size="10" font-weight="700">uses</text>' +
        '<text x="234" y="45" fill="#90d5ff" font-size="10" font-weight="700">cites</text>' +
        '<circle cx="72" cy="62" r="14" fill="rgba(0,245,184,0.18)" stroke="#26ffaa" stroke-width="1.8"/>' +
        '<text x="72" y="66" text-anchor="middle" fill="#ffffff" font-size="10" font-weight="700">vLLM</text>' +
        '<circle cx="180" cy="34" r="15" fill="rgba(56,182,255,0.2)" stroke="#58d5ff" stroke-width="1.8"/>' +
        '<text x="180" y="38" text-anchor="middle" fill="#ffffff" font-size="9.5" font-weight="700">PagedAttn</text>' +
        '<circle cx="292" cy="74" r="14" fill="rgba(167,139,250,0.2)" stroke="#c084fc" stroke-width="1.6"/>' +
        '<text x="292" y="78" text-anchor="middle" fill="#ffffff" font-size="9.5" font-weight="700">FlashAttn</text>' +
        '<rect x="18" y="92" width="324" height="22" rx="6" fill="rgba(15,23,42,0.86)" stroke="rgba(148,163,184,0.28)"/>' +
        '<text x="30" y="106" fill="#cbd8ee" font-size="10">claire.db · FTS5 · 3072d · /silvercord/{token}</text>' +
        "</svg>";
      return '<div class="p4-specimen-box">' +
        '<div class="p4-specimen-head"><strong class="p4-specimen-title">' + esc(tr("pipeline.neon.spec5.title", "정본 저장 · 지식 별자리 · 후속 루프")) + '</strong>' +
        '<span class="p4-pill p4-pill-mint">/silvercord/{token}</span></div>' +
        constSvg +
        '<div class="p4-chip-cloud">' +
        (isClient
          ? '<span class="p4-pill p4-pill-mint">' + esc(tr("pipeline.neon.spec5.client_1", "단일 _AtomicConnection 커밋")) + '</span><span class="p4-pill p4-pill-quiet">origin: agent_client</span><span class="p4-pill p4-pill-quiet">' + esc(tr("pipeline.neon.spec5.client_2", "classify_watch · 자동확장 미실행")) + "</span>"
          : '<span class="p4-pill p4-pill-mint">claire.db · FTS5 · 3072d</span><span class="p4-pill p4-pill-azure">' + esc(tr("pipeline.neon.spec5.api_1", "classify_watch (개인 KB · 신규만)")) + '</span><span class="p4-pill p4-pill-azure">' + esc(tr("pipeline.neon.spec5.api_2", "expand-loop 깊이 1")) + "</span>") +
        "</div></div>";
    }

    function render() {
      var idx = Math.max(0, Math.min(beats.length - 1, st.stage));
      var beat = beats[idx];
      var isClient = st.mode === "client";
      var modelText = stageModelText(beat.id, isClient);
      var actionText = isClient ? beat.clientAction : beat.apiAction;
      var spineHtml = '<div class="p4-spine" role="list" aria-label="' + esc(tr("pipeline.neon.rail_aria", "문서 처리 5단계 신호 레일")) + '">' +
        beats.map(function (b, i) {
          var isCur = i === idx;
          var isLit = i <= idx;
          var edge = i > 0 ? '<div class="p4-conduit' + (isLit ? " is-lit" : "") + '" aria-hidden="true"><span class="p4-photon"></span></div>' : "";
          return edge + '<button type="button" class="p4-node" data-p4-stage="' + i + '" data-kind="' + esc(b.kind) + '"' + (isCur ? ' aria-current="step"' : "") + ">" +
            '<div class="p4-node-num"><span class="p4-node-index">' + esc(b.num) + "</span><span>" + esc(b.kind === "rule" ? tr("pipeline.neon.node.rule", "실선 규칙") : tr("pipeline.neon.node.ai", "점선 AI")) + "</span></div>" +
            '<div class="p4-node-title">' + esc(b.shortTitle) + "</div>" +
            '<div class="p4-node-tag">' + esc(b.pillText) + "</div>" +
            "</button>";
        }).join("") +
        "</div>";

      var accHtml = '<details class="p4-acc"' + (st.accOpen ? " open" : "") + "><summary>" +
        iconSvg("chevron", "p4-chev") +
        "<span>" + esc(beat.accSummary) + "</span></summary>" +
        '<div class="p4-acc-body">' +
        beat.accLines.map(function (line) { return "<div>" + esc(line) + "</div>"; }).join("") +
        "</div></details>";

      var motionLabel = st.motionOn ? tr("pipeline.neon.motion.pause", "흐름 정지") : tr("pipeline.neon.motion.resume", "흐름 재생");
      var motionIcon = st.motionOn ? "pause" : "play";

      host.innerHTML = '<section class="p4-theatre' + (st.motionOn ? "" : " p4-paused") + '" aria-label="' + esc(tr("pipeline.neon.theatre_aria", "문서 처리 5단계 광학 흐름도")) + '">' +
        '<div class="p4-head">' +
        '<div class="p4-brand">' + renderArcaneEmblem(uid) +
        '<div class="p4-title-wrap"><h3>' + esc(tr("pipeline.neon.title", "문서 한 편이 지식이 되는 5단계")) + "</h3>" +
        "<p>" + esc(tr("pipeline.neon.subtitle", "어디서 들어오든 수집·검증을 거쳐 추출 방식(서버 API vs 에이전트)에 따라 3~5단계만 다르게 동작합니다.")) + "</p></div></div>" +
        '<div class="p4-controls">' +
        '<div class="p4-mode-group" role="group" aria-label="' + esc(tr("pipeline.neon.mode_aria", "추출 방식 전환")) + '">' +
        '<button type="button" class="p4-btn p4-mode-btn" data-p4-mode="provider" aria-pressed="' + (!isClient ? "true" : "false") + '">' + iconSvg("bolt") + " " + esc(tr("pipeline.neon.mode.api", "서버 API")) + "</button>" +
        '<button type="button" class="p4-btn p4-mode-btn" data-p4-mode="client" aria-pressed="' + (isClient ? "true" : "false") + '">' + iconSvg("bot") + " " + esc(tr("pipeline.neon.mode.client", "에이전트 클라이언트")) + "</button>" +
        "</div>" +
        '<button type="button" class="p4-btn" id="p4-motion-btn" data-p4-motion>' + iconSvg(motionIcon) + "<span>" + esc(motionLabel) + "</span></button>" +
        '<button type="button" class="p4-btn" data-p4-step="prev"' + (idx <= 0 ? " disabled" : "") + ' aria-label="' + esc(tr("pipeline.neon.prev_aria", "이전 단계")) + '">' + iconSvg("prev") + " " + esc(tr("pipeline.neon.prev", "이전")) + "</button>" +
        '<span class="p4-pill p4-pill-quiet" aria-live="polite">' + (idx + 1) + " / " + beats.length + "</span>" +
        '<button type="button" class="p4-btn" data-p4-step="next"' + (idx >= beats.length - 1 ? " disabled" : "") + ' aria-label="' + esc(tr("pipeline.neon.next_aria", "다음 단계")) + '">' + esc(tr("pipeline.neon.next", "다음")) + " " + iconSvg("next") + "</button>" +
        "</div></div>" +
        '<div class="p4-spine-wrap">' + spineHtml + "</div>" +
        '<div class="p4-deck">' +
        '<div class="p4-glass"><div class="p4-glass-head"><h4>' + iconSvg("spark") + " " + esc(tr("pipeline.neon.specimen_heading", "지금 이 단계의 문서 상태")) + '</h4><span class="p4-pill ' + (isClient ? "p4-pill-mint" : "p4-pill-azure") + '">' + esc(isClient ? tr("pipeline.neon.path.client", "에이전트 클라이언트 경로") : tr("pipeline.neon.path.api", "서버 API 경로")) + "</span></div>" + renderSpecimen(beat, isClient) + "</div>" +
        '<div class="p4-glass"><div class="p4-glass-head"><h4>' + iconSvg("layers") + " " + esc(beat.title) + '</h4><span class="p4-pill ' + (beat.kind === "rule" ? "p4-pill-mint" : "p4-pill-azure") + '">' + esc(beat.kindLabel) + "</span></div>" +
        '<dl class="p4-kv">' +
        "<dt>" + esc(tr("pipeline.neon.kv.model", "맥락 · 모델")) + "</dt><dd><code>" + esc(modelText) + '</code> <span class="p4-pill p4-pill-quiet">' + esc(data.sourceLabel || "") + "</span></dd>" +
        "<dt>" + esc(tr("pipeline.neon.kv.input", "입력")) + "</dt><dd>" + esc(beat.input) + "</dd>" +
        "<dt>" + esc(tr("pipeline.neon.kv.action", "핵심 동작")) + "</dt><dd>" + esc(actionText) + "</dd>" +
        "<dt>" + esc(tr("pipeline.neon.kv.output", "산출물")) + "</dt><dd>" + esc(beat.output) + "</dd>" +
        "<dt>" + esc(tr("pipeline.neon.kv.fail", "실패 차단")) + "</dt><dd>" + esc(beat.failClosed) + "</dd>" +
        "</dl>" +
        accHtml +
        "</div></div>" +
        '<div class="p4-legend">' +
        '<div class="p4-chip-cloud"><span class="p4-pill p4-pill-mint">' + esc(tr("pipeline.neon.legend.rule", "실선(민트): 결정론적 규칙 · 검증 경계")) + '</span><span class="p4-pill p4-pill-azure">' + esc(tr("pipeline.neon.legend.ai", "점선(애저): AI 추론 · 조건부 분기")) + "</span>" +
        (showJev ? '<span class="p4-pill p4-pill-iris" data-jev-legend>' + esc(tr("pipeline.neon.legend.jev", "점선(바이올렛): Jev 그림자 관측(관리자)")) + "</span>" : "") + "</div>" +
        "<span><code>/about</code> · <code>/support/pipeline</code> " + esc(tr("pipeline.neon.legend.shared", "공통 컴포넌트")) + "</span>" +
        "</div></section>";
    }

    host.addEventListener("click", function (ev) {
      var btn = ev.target && ev.target.closest ? ev.target.closest("button") : null;
      if (!btn || !host.contains(btn)) return;
      if (btn.hasAttribute("data-p4-stage")) {
        st.stage = parseInt(btn.getAttribute("data-p4-stage"), 10) || 0;
        st.accOpen = false;
        render();
        return;
      }
      if (btn.hasAttribute("data-p4-mode")) {
        st.mode = btn.getAttribute("data-p4-mode") === "client" ? "client" : "provider";
        render();
        return;
      }
      if (btn.hasAttribute("data-p4-motion")) {
        st.motionOn = !st.motionOn;
        render();
        return;
      }
      if (btn.hasAttribute("data-p4-rend")) {
        st.rendition = btn.getAttribute("data-p4-rend") || "standard";
        render();
        return;
      }
      if (btn.hasAttribute("data-p4-step")) {
        var dir = btn.getAttribute("data-p4-step");
        st.stage = dir === "next" ? Math.min(beats.length - 1, st.stage + 1) : Math.max(0, st.stage - 1);
        st.accOpen = false;
        render();
        return;
      }
      if (btn.hasAttribute("data-p4-gate-preset")) {
        var gp = btn.getAttribute("data-p4-gate-preset");
        st.gateExact = gp === "exact";
        st.gateConflict = gp === "conflict";
        if (gp === "auto") st.gateScore = 0.95;
        else if (gp === "border") st.gateScore = 0.84;
        else if (gp === "new") st.gateScore = 0.52;
        else if (gp === "conflict") st.gateScore = 0.88;
        render();
      }
    });

    host.addEventListener("toggle", function (ev) {
      var det = ev.target;
      if (det && det.matches && det.matches("details.p4-acc")) {
        st.accOpen = det.open;
      }
    }, true);

    host.addEventListener("change", function (ev) {
      var el = ev.target;
      if (!el) return;
      if (el.hasAttribute("data-p4-gate-exact")) {
        st.gateExact = Boolean(el.checked);
        render();
      } else if (el.hasAttribute("data-p4-gate-conflict")) {
        st.gateConflict = Boolean(el.checked);
        render();
      }
    });

    host.addEventListener("input", function (ev) {
      var el = ev.target;
      if (!el || !el.hasAttribute("data-p4-gate-score")) return;
      st.gateScore = parseFloat(el.value);
      render();
      var slider = host.querySelector("[data-p4-gate-score]");
      if (slider) slider.focus();
    });

    host.addEventListener("keydown", function (ev) {
      var node = ev.target && ev.target.closest ? ev.target.closest(".p4-node") : null;
      if (!node || !host.contains(node)) return;
      if (ev.key !== "ArrowRight" && ev.key !== "ArrowDown" && ev.key !== "ArrowLeft" && ev.key !== "ArrowUp" && ev.key !== "Home" && ev.key !== "End") return;
      ev.preventDefault();
      var cur = parseInt(node.getAttribute("data-p4-stage"), 10) || 0;
      var nxt = cur;
      if (ev.key === "ArrowRight" || ev.key === "ArrowDown") nxt = Math.min(beats.length - 1, cur + 1);
      else if (ev.key === "ArrowLeft" || ev.key === "ArrowUp") nxt = Math.max(0, cur - 1);
      else if (ev.key === "Home") nxt = 0;
      else if (ev.key === "End") nxt = beats.length - 1;
      st.stage = nxt;
      st.accOpen = false;
      render();
      var targetBtn = host.querySelector('.p4-node[data-p4-stage="' + nxt + '"]');
      if (targetBtn) targetBtn.focus();
    });

    host.dataset.pipelineMounted = "1";
    render();
  }

  function mountAll(scope) {
    var rootEl = scope || document;
    var mounts = rootEl.querySelectorAll ? rootEl.querySelectorAll("[data-pipeline-mount]") : [];
    Array.prototype.forEach.call(mounts, function (el) {
      if (el.dataset.pipelineMounted === "1") return;
      var jsonId = el.getAttribute("data-pipeline-json-id") || "claire-pipeline-data";
      var dataEl = jsonId ? document.getElementById(jsonId) : null;
      var parsed = {};
      if (dataEl) {
        try { parsed = JSON.parse(dataEl.textContent || "{}"); } catch (_) { parsed = {}; }
      }
      mount(el, parsed);
    });
  }

  root.ClairePipelineFlow = { mount: mount, mountAll: mountAll };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { mountAll(document); });
  } else {
    mountAll(document);
  }
})(window);
