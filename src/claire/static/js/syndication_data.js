/* Claire Bible — Syndication Prototype Seed Data & Shared Preferences Store */
window.CLAIRE_SYND_STORAGE = {
  PREFS_KEY: "claireSyndicationPrefs_v2",
  CUSTOM_KEY: "claireSyndicationCustomState_v2",
  DEFAULT_PREFS: {
    layoutMode: "three-panel",
    listStyle: "flat",
    showExcerpt: true,
    defaultContentTab: "auto",
    readerFont: "sans",
    readerFontSize: 15,
    autoMarkReadOnOpen: true,
    autoIngestThreshold: 85,
    tagPolicy: {
      autoMapSourceTagsToEntities: true,
      createProvisionalEntityForUnknownTag: true,
      attachEntityHashtagsOnPublish: true,
      ignoredNoiseTags: "uncategorized, general, news, sponsor"
    },
    outboundPolicy: {
      rssDefaultMode: "quote_boost",
      apDefaultMode: "announce",
      includeCanonicalShareLink: true,
      quoteTemplate: "📌 {{title}} — {{url}} {{hashtags}}"
    }
  }
};

window.CLAIRE_SYND_SEEDS = {
  "0": {
    id: 0, seq: 0, icon: "📚", label: "기본 지식베이스", fqdn: "", dbPath: "data/claire.db", isPublic: true,
    actorHandle: "@claire@claire.example.com", followers: 39, defaultFocus: "",
    existingEntities: [
      { name: "SQLite", type: "Tool", count: 19 },
      { name: "ActivityPub", type: "Concept", count: 12 },
      { name: "AT Protocol", type: "Concept", count: 7 },
      { name: "FTS5", type: "Tool", count: 9 },
      { name: "W3C", type: "Org", count: 6 }
    ],
    rules: [
      { id: "r0-1", name: "SQLite 릴리스 노트 우선 오거나이즈", cond: "수집 태그 일치: [[SQLite]]", action: "점수 +45 · 별표 부여", enabled: true },
      { id: "r0-2", name: "W3C 표준 권고안 자동 적재 전달", cond: "대상: @w3c@w3c.social + #ActivityPub", action: "프로젝트 적재 파이프라인 자동 전달", enabled: true }
    ],
    feeds: [
      { id: "f0-1", folder: "오픈소스 릴리스", proto: "rss", format: "ATOM 1.0", interval: "15m", title: "SQLite Release Log", url: "https://sqlite.org/news.atom", etag: "200 OK" },
      { id: "f0-2", folder: "Fediverse 표준 기구", proto: "ap", format: "ActivityPub", interval: "push", title: "W3C Social Web WG", handle: "@w3c@w3c.social", url: "https://w3c.social/users/w3c", etag: "AP 서명 검증됨" },
      { id: "f0-3", folder: "분산 소셜 프로토콜", proto: "at", format: "AT Protocol", interval: "15m", title: "AT Protocol Core Dev", handle: "at://did:plc:atproto-dev", url: "https://bsky.app/profile/atproto.com", etag: "Jetstream 동기화" }
    ],
    items: [
      {
        id: "i0-1", feedId: "f0-1", proto: "rss", format: "ATOM",
        title: "SQLite 3.51.0: Improved FTS5 Query Planner & WAL Concurrency",
        author: "D. Richard Hipp", url: "https://sqlite.org/releaselog/3_51_0.html",
        timeAgo: "08:42", timestamp: 1761634000, unread: true, starred: true, score: 92,
        sourceTags: ["SQLite", "FTS5", "WAL-Mode", "DatabaseEngine"],
        rawBody: "SQLite 3.51.0 optimizes FTS5 phrase lookups, introduces non-blocking WAL2 snapshot promotion, and reduces reader tail latency on knowledge-graph workloads.",
        rawHtml: `<p><strong>SQLite version 3.51.0</strong> introduces targeted enhancements to the Full-Text Search (<code>FTS5</code>) query optimizer and Write-Ahead Logging (WAL) checkpoint scheduler for read-heavy embedded servers.</p>
<h2>1. FTS5 Multi-Token Phrase Cost Estimation</h2>
<p>Previously, compound prefix queries combined with boolean <code>NEAR()</code> filters scanned posting lists in declaration order. The new planner inspects segment b-tree page counts first, cutting memory allocations by up to 34% on CJK trigram indexes.</p>
<figure><svg viewBox="0 0 520 110" width="100%" style="max-width:480px;height:auto"><rect x="8" y="18" width="145" height="72" rx="8" fill="#ddf4ff" stroke="#0969da"/><text x="80" y="50" text-anchor="middle" font-size="12" fill="#1f2328" font-weight="bold">FTS5 Query AST</text><text x="80" y="68" text-anchor="middle" font-size="11" fill="#656d76">Trigram + NEAR()</text><line x1="153" y1="54" x2="195" y2="54" stroke="#0969da" stroke-width="2"/><rect x="195" y="18" width="150" height="72" rx="8" fill="#dafbe1" stroke="#1a7f37"/><text x="270" y="50" text-anchor="middle" font-size="12" fill="#1f2328" font-weight="bold">Sparse Doclist</text><text x="270" y="68" text-anchor="middle" font-size="11" fill="#656d76">-34% Merge IO</text><line x1="345" y1="54" x2="385" y2="54" stroke="#1a7f37" stroke-width="2"/><rect x="385" y="18" width="125" height="72" rx="8" fill="#fff8c5" stroke="#9a6700"/><text x="447" y="50" text-anchor="middle" font-size="12" fill="#1f2328" font-weight="bold">WAL Snapshot</text><text x="447" y="68" text-anchor="middle" font-size="11" fill="#656d76">Zero-Stall Read</text></svg><figcaption>그림 1. SQLite 3.51.0 FTS5 희소 세그먼트 병합 및 WAL 스냅샷 경로</figcaption></figure>
<h2>2. Benchmark Summary</h2>
<table><thead><tr><th>워크로드</th><th>SQLite 3.50.x</th><th>SQLite 3.51.0</th><th>개선율</th></tr></thead><tbody><tr><td>한국어·영문 혼합 FTS5 구문 질의</td><td>14.8 ms</td><td>9.7 ms</td><td>-34.5%</td></tr><tr><td>동시 읽기 32 스레드 중 PASSIVE 체크포인트</td><td>6.2 ms p99</td><td>1.9 ms p99</td><td>-69.3%</td></tr></tbody></table>`,
        ingestState: "linked",
        linkedDoc: {
          docId: "doc_4e81b9", ingestedAt: "15분 전 (프로젝트 DB 적재 완료)", focusUsed: "(중립 기본 추출 + 피드 태그 엔티티 병합)",
          entityCount: 2, relationCount: 3, shareUrl: "/p?s=sh_991a0f",
          entities: [{ name: "SQLite", type: "Tool" }, { name: "FTS5", type: "Tool" }],
          summary: "SQLite 3.51.0은 FTS5 세그먼트 비용 기반 실행 계획과 비차단형 WAL 체크포인트 스케줄링을 도입해 다중 읽기 지식 그래프 워크로드의 p99 지연을 69% 단축하였다.",
          detailHtml: `<h2>핵심 구조 분석 (Claire 적재 정제본)</h2>
<p><code>[[SQLite]]</code> 3.51.0 릴리스는 클레어바이블이 채택한 단일 파일 온톨로지 저장소 구조에 직접적인 성능 이점을 제공한다.</p>
<ul><li><strong><code>[[FTS5]]</code> 실행 계획 최적화</strong>: 세그먼트 B-Tree 페이지 밀도를 사전 계측하여 한·영 혼합 구문 검색 시 불필요한 역색인 병합 단계를 제거하였다.</li>
<li><strong><code>[[WAL-Mode]]</code> 경합 완화</strong>: <code>PRAGMA wal_checkpoint(TRUNCATE)</code> 수행 시 활성 읽기 스냅샷의 대기열 지연을 6.2ms에서 1.9ms로 줄였다.</li></ul>
<h3>추출된 온톨로지 관계 (Triples)</h3>
<pre><code>[[SQLite]] --(optimizes)--&gt; [[FTS5]]
[[SQLite]] --(reduces_contention_in)--&gt; [[WAL-Mode]]
[[FTS5]]   --(accelerates)--&gt; [[DatabaseEngine]]</code></pre>`
        },
        publishState: "none", publishedInfo: null
      },
      {
        id: "i0-2", feedId: "f0-2", proto: "ap", format: "AP",
        title: "Social Web Working Group Charter Renewal: Portable Actors & Quote Posts (FEP-044f)",
        author: "@w3c@w3c.social", apObjectUri: "https://w3c.social/users/w3c/statuses/11542910021", url: "https://w3c.social/@w3c/11542910021",
        timeAgo: "07:55", timestamp: 1761631000, unread: true, starred: false, score: 83,
        sourceTags: ["ActivityPub", "W3C", "FEP044f", "Fediverse"],
        rawBody: "We have published the updated Social Web WG draft covering standardized Quote Posts (FEP-044f), Portable Actor Key Rotation, and HTTP Message Signatures.",
        rawHtml: `<p>The <strong>W3C Social Web Working Group</strong> has published its 2026–2028 charter renewal draft for public review across the Fediverse.</p>
<ul><li><strong>Standardized Quote Posts (FEP-044f)</strong>: Explicit <code>quoteUrl</code> &amp; cryptographic quote approval stamps so RSS/Web/ActivityPub citations preserve provenance.</li>
<li><strong>Actor Portability (LOLA)</strong>: Seamless migration of followers and authored collections without severing object URIs.</li></ul>
<p>Tags from ActivityPub payload: <code>#ActivityPub</code> <code>#W3C</code> <code>#FEP044f</code> <code>#Fediverse</code></p>`,
        ingestState: "unlinked", linkedDoc: null,
        publishState: "boosted", publishedInfo: { mode: "announce", at: "40분 전", note: "ActivityPub 순수 부스트(Announce) 완료" }
      },
      {
        id: "i0-3", feedId: "f0-3", proto: "at", format: "AT-URI",
        title: "Jetstream v2 Deterministic Cursor Replay & Lexicon Tag Facets for Knowledge Archiving",
        author: "@atproto.com", url: "https://bsky.app/profile/atproto.com/post/3l7x9k2",
        timeAgo: "어제", timestamp: 1761627000, unread: true, starred: false, score: 76,
        sourceTags: ["AT Protocol", "Jetstream", "Lexicon"],
        rawBody: "Jetstream JSON firehose relays now support compressed zstd cursor replay and lossless facet tag extraction for periodic archivers.",
        rawHtml: `<p><strong>AT Protocol Jetstream v2</strong> enables low-bandwidth periodic collectors to subscribe to specific DID collections and Lexicon schemas without consuming a full relay firehose.</p>
<pre><code>GET wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post&amp;cursor=1761627000000000</code></pre>
<p>Rich-text facets (<code>app.bsky.richtext.facet#tag</code>) expose structured topic tags directly, allowing knowledge bases like Claire Bible to map tags straight into ontology entities upon ingestion.</p>`,
        ingestState: "unlinked", linkedDoc: null, publishState: "none", publishedInfo: null
      }
    ]
  },
  "1": {
    id: 1, seq: 1, icon: "🤖", label: "AI 및 클라우드 시스템", fqdn: "ai.example.com", dbPath: "data/themes/1/claire.db", isPublic: true,
    actorHandle: "@ai@ai.example.com", followers: 148, defaultFocus: "시스템 아키텍처 및 추론 메모리 대역폭 최적화 중심",
    existingEntities: [
      { name: "vLLM", type: "Framework", count: 27 },
      { name: "KV Cache", type: "Concept", count: 31 },
      { name: "Speculative Decoding", type: "Concept", count: 16 },
      { name: "PagedAttention", type: "Paper", count: 18 },
      { name: "RDMA", type: "Tool", count: 8 }
    ],
    rules: [
      { id: "r1-1", name: "추론 최적화 논문 고득점 오거나이즈", cond: "수집 태그 일치 ≥ 2 (#vLLM, #KV_Cache)", action: "점수 +50 · 상단 배치", enabled: true }
    ],
    feeds: [
      { id: "f1-1", folder: "학술 프리프린트", proto: "rss", format: "ATOM 1.0", interval: "15m", title: "arXiv cs.LG & cs.DC", url: "https://rss.arxiv.org/atom/cs.LG+cs.DC", etag: "200 OK" },
      { id: "f1-3", folder: "엔지니어링 블로그", proto: "rss", format: "RSS 2.0", interval: "15m", title: "vLLM Engineering Blog", url: "https://blog.vllm.ai/feed.xml", etag: "200 OK" },
      { id: "f1-4", folder: "Fediverse 연구자", proto: "ap", format: "ActivityPub", interval: "push", title: "Georgi Gerganov", handle: "@ggerganov@mastodon.social", url: "https://mastodon.social/users/ggerganov", etag: "AP Push" }
    ],
    items: [
      {
        id: "i1-1", feedId: "f1-1", proto: "rss", format: "ATOM",
        title: "Disaggregated KV Cache Routing Across Heterogeneous GPU Clusters",
        author: "Lin et al. (arXiv:2610.14821)", url: "https://arxiv.org/abs/2610.14821",
        timeAgo: "09:14", timestamp: 1761635100, unread: true, starred: true, score: 95,
        sourceTags: ["KV Cache", "vLLM", "PagedAttention", "RDMA"],
        rawBody: "We present a disaggregated prefill-decode KV cache scheduler that routes prefix blocks across H100 and L40S tiers over RDMA without stalling decode token generation.",
        rawHtml: `<p><strong>Abstract.</strong> Serving long-context reasoning models on mixed GPU fleets suffers from severe prefill-decode interference. We propose <em>TieredKV</em>, a zero-copy prefix block router built atop <code>vLLM</code> and <code>PagedAttention</code>.</p>
<h2>Architecture Overview</h2>
<p>Compute-dense H100 nodes execute prompt prefill phases and asynchronously stream quantized FP8 KV blocks over RoCEv2 RDMA into memory-capacity-optimized L40S decode pools.</p>
<table><thead><tr><th>지표 (128K Context)</th><th>단일 풀 vLLM</th><th>TieredKV (분리 라우팅)</th></tr></thead><tbody><tr><td>TTFT (Time-To-First-Token)</td><td>1,840 ms</td><td>910 ms (-50.5%)</td></tr><tr><td>TPOT (Inter-Token Latency)</td><td>38.2 ms</td><td>19.4 ms (-49.2%)</td></tr></tbody></table>`,
        ingestState: "linked",
        linkedDoc: {
          docId: "doc_kv91a2", ingestedAt: "5분 전", focusUsed: "시스템 아키텍처 및 추론 메모리 대역폭 최적화 중심",
          entityCount: 4, relationCount: 4, shareUrl: "/p?s=sh_kv91a2",
          entities: [
            { name: "KV Cache", type: "Concept" },
            { name: "vLLM", type: "Framework" },
            { name: "PagedAttention", type: "Paper" },
            { name: "RDMA", type: "Tool" }
          ],
          summary: "이종 GPU 클러스터(H100 Prefill + L40S Decode) 간 RDMA 기반 비동기 FP8 KV 블록 전송을 통해 128K 문맥 추론의 TTFT와 TPOT를 모두 약 50% 절감하는 TieredKV 아키텍처이다.",
          detailHtml: `<h2>추론 파이프라인 분리 요약 (Claire 적재 본문)</h2>
<p><code>[[vLLM]]</code>의 <code>[[PagedAttention]]</code> 블록 테이블을 클러스터 전역 주소 공간으로 확장하고, <code>[[RDMA]]</code> 단방향 Write로 <code>[[KV Cache]]</code> 이관 지연을 은닉하였다.</p>
<ul><li><strong>Prefill 계층</strong>: H100 텐서 코어 포화 활용 후 FP8 KV 블록 즉시 방출</li><li><strong>Decode 계층</strong>: L40S 풀에서 prefix 적중률 87% 달성</li></ul>`
        },
        publishState: "none", publishedInfo: null
      },
      {
        id: "i1-2", feedId: "f1-3", proto: "rss", format: "RSS",
        title: "Zero-Overhead Speculative Decoding in vLLM v0.9 Architecture",
        author: "vLLM Core Team", url: "https://blog.vllm.ai/2026/10/speculative-v09.html",
        timeAgo: "08:05", timestamp: 1761632100, unread: true, starred: false, score: 89,
        sourceTags: ["vLLM", "Speculative Decoding", "Eagle-3"],
        rawBody: "By fusing draft-model token verification into the main scheduler step, vLLM v0.9 eliminates host-device synchronization bubbles.",
        rawHtml: `<p>In <strong>vLLM v0.9</strong>, draft token generation (EAGLE-3 / ngram) runs inside the unified persistent CUDA graph runner without Python-side roundtrips.</p><blockquote>Result: 2.3x lossless speedup on code generation workloads at batch size ≤ 32.</blockquote>`,
        ingestState: "unlinked", linkedDoc: null, publishState: "none", publishedInfo: null
      }
    ]
  },
  "2": {
    id: 2, seq: 2, icon: "📜", label: "문헌학 및 고전 연구", fqdn: "classics.example.com", dbPath: "data/themes/2/claire.db", isPublic: true,
    actorHandle: "@classics@classics.example.com", followers: 64, defaultFocus: "사본 계보 및 원문 비평 중심",
    existingEntities: [{ name: "70인역 (LXX)", type: "Paper", count: 15 }, { name: "바울 서신", type: "Concept", count: 11 }],
    rules: [{ id: "r2-1", name: "70인역 사본 연구 우선 오거나이즈", cond: "수집 태그 일치: #70인역", action: "점수 +40", enabled: true }],
    feeds: [{ id: "f2-1", folder: "성서학 저널", proto: "rss", format: "RSS 2.0", interval: "1h", title: "Biblical Archaeology Review", url: "https://www.biblicalarchaeology.org/feed/", etag: "200 OK" }],
    items: [
      {
        id: "i2-1", feedId: "f2-1", proto: "rss", format: "RSS",
        title: "Newly Digitized Papyrus Fragments of Pauline Epistles in Chester Beatty Collection",
        author: "Dr. Hannah M. Evans", url: "https://www.biblicalarchaeology.org/daily/pauline-papyrus-2026/",
        timeAgo: "어제", timestamp: 1761621000, unread: true, starred: true, score: 89,
        sourceTags: ["70인역 (LXX)", "바울 서신", "Chester Beatty P46"],
        rawBody: "Multispectral imaging of P46 folio margins reveals scribal corrections aligning Old Testament citations closer to early Alexandrian Septuagint witnesses.",
        rawHtml: `<p>High-resolution multispectral imaging of <strong>Papyrus 46 (P46)</strong> folios has uncovered previously illegible marginal glosses in Hebrews and Romans.</p><p>These readings corroborate early Alexandrian transmission lines of the Septuagint (<code>70인역 (LXX)</code>).</p>`,
        ingestState: "unlinked", linkedDoc: null, publishState: "none", publishedInfo: null
      }
    ]
  },
  "3": {
    id: 3, seq: 3, icon: "🔒", label: "사내 보안 인텔리전스 (비공개)", fqdn: "", dbPath: "data/themes/3/claire.db", isPublic: false,
    actorHandle: "(비공개 테마 — 외부 발행 차단)", followers: 0, defaultFocus: "영향 커널 버전 및 즉각 완화 조치 중심",
    existingEntities: [{ name: "Linux Kernel", type: "Tool", count: 29 }, { name: "CVE-2026", type: "Event", count: 41 }, { name: "eBPF", type: "Concept", count: 13 }],
    rules: [{ id: "r3-1", name: "커널 권한 상승 즉시 적재", cond: "수집 태그: #LinuxKernel + #CVE-2026", action: "비공개 DB 자동 적재", enabled: true }],
    feeds: [{ id: "f3-1", folder: "취약점 공지", proto: "rss", format: "ATOM 1.0", interval: "5m", title: "OSS-Security Advisories", url: "https://www.openwall.com/lists/oss-security/rss", etag: "200 OK" }],
    items: [
      {
        id: "i3-1", feedId: "f3-1", proto: "rss", format: "ATOM",
        title: "CVE-2026-39102: Linux Kernel eBPF Verifier Bounds Tracking Local Privilege Escalation",
        author: "oss-security", url: "https://www.openwall.com/lists/oss-security/2026/10/28/1",
        timeAgo: "09:02", timestamp: 1761634700, unread: true, starred: true, score: 96,
        sourceTags: ["Linux Kernel", "CVE-2026", "eBPF"],
        rawBody: "Improper scalar register bounds propagation in kernel/bpf/verifier.c affects Linux 6.10 through 6.13.4.",
        rawHtml: `<p>An out-of-bounds map write primitive was discovered in <code>kernel/bpf/verifier.c</code> affecting Linux 6.10–6.13.4 when unprivileged BPF is enabled.</p><pre><code>sysctl -w kernel.unprivileged_bpf_disabled=1</code></pre>`,
        ingestState: "linked",
        linkedDoc: {
          docId: "doc_sec881", ingestedAt: "10분 전", focusUsed: "영향 커널 버전 및 즉각 완화 조치 중심",
          entityCount: 3, relationCount: 2, shareUrl: "(비공개 테마 — 외부 공유 차단)",
          entities: [
            { name: "Linux Kernel", type: "Tool" },
            { name: "CVE-2026", type: "Event" },
            { name: "eBPF", type: "Concept" }
          ],
          summary: "Linux 6.10~6.13.4 eBPF 검증기 경계 추적 결함으로 인한 로컬 권한 상승 취약점이며 unprivileged_bpf 비활성화로 즉시 완화 가능하다.",
          detailHtml: `<h2>영향 범위 및 즉각 완화 조치</h2><ul><li>영향 버전: Linux <code>6.10</code> ~ <code>6.13.4</code></li><li>완화 명령: <code>sysctl -w kernel.unprivileged_bpf_disabled=1</code></li></ul>`
        },
        publishState: "none", publishedInfo: null
      }
    ]
  }
};
