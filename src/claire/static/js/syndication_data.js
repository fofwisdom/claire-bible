/* Claire Bible — Syndication Prototype Seed Data & Shared Preferences Store */
window.CLAIRE_SYND_STORAGE = {
  PREFS_KEY: "claireSyndicationPrefs_v2",
  CUSTOM_KEY: "claireSyndicationCustomState_v2",
  /* 목록 스타일 키 (영문 i18n 명칭): list=목록 · compact=조밀하게 · card=일반 · roomy=넓게 */
  LIST_STYLES: ["list", "compact", "card", "roomy"],
  LEGACY_LIST_STYLES: { flat: "list", cards: "card" },
  DEFAULT_PREFS: {
    appTab: "client",
    layoutMode: "three-panel",
    listStyle: "list",
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
      apDefaultMode: "boost",
      atDefaultMode: "boost",
      includeCanonicalShareLink: true,
      quoteTemplate: "📌 {{title}} — {{url}} {{hashtags}}"
    }
  }
};

/*
 * ActivityPub · AT Protocol 은 Claire 가 직접 연합(Actor/Inbox/서명)하지 않는다.
 * 각 테마는 기존 서버의 계정(Mastodon Client API · AT Protocol PDS XRPC)에 연결되며,
 * 수집·발행·타임라인 열람은 모두 그 연결 계정의 서버 API를 경유한다.
 * ActivityPub 클라이언트 기능은 Mastodon 5.0 이상이 제공하는 클라이언트 기능 전체를 기준으로 갖춘다 (CLAIRE_SYND_AP_FEATURES).
 * Mastodon API 를 제공하는 서버라면 연결할 수 있으며, 서버가 제공하지 않는 기능(missingFeatures)만 비활성으로 표시한다.
 */
window.CLAIRE_SYND_ACCOUNT_TEMPLATES = {
  ap: {
    software: "Mastodon",
    featureBaseline: "Mastodon 5.0",
    auth: "OAuth 2.0 (Authorization Code + PKCE)",
    scopes: ["read", "write:statuses", "write:media", "write:favourites", "write:bookmarks", "write:follows", "write:lists", "write:mutes", "write:blocks", "write:filters", "write:reports", "write:notifications", "write:conversations", "write:accounts"],
    charLimit: 500,
    missingFeatures: [],
    lists: [], filters: [], mutes: [], blocks: [], domainBlocks: [], followedTags: [], notifRequests: [],
    notifPolicy: { forNotFollowing: "accept", forNotFollowers: "accept", forNewAccounts: "accept", forPrivateMentions: "filter", forLimitedAccounts: "filter" },
    trends: { statuses: [], tags: [], links: [] },
    timelines: [
      { id: "home", icon: "🏠", label: "홈", api: "GET /api/v1/timelines/home", stream: "user", collectable: true },
      { id: "notifications", icon: "🔔", label: "알림", api: "GET /api/v2/notifications", stream: "user:notification", kind: "notifications" },
      { id: "conversations", icon: "✉️", label: "개인 메시지", api: "GET /api/v1/conversations", stream: "direct" },
      { id: "explore", icon: "🧭", label: "탐색", api: "GET /api/v1/trends/statuses · /trends/tags · /trends/links", kind: "explore", feature: "trends" },
      { id: "local", icon: "🏘️", label: "로컬", api: "GET /api/v1/timelines/public?local=true", stream: "public:local" },
      { id: "federated", icon: "🌐", label: "연합", api: "GET /api/v1/timelines/public", stream: "public" },
      { id: "favourites", icon: "★", label: "좋아요", api: "GET /api/v1/favourites", kind: "favourites" },
      { id: "bookmarks", icon: "🔖", label: "북마크", api: "GET /api/v1/bookmarks", kind: "bookmarks" },
      { id: "scheduled", icon: "🗓️", label: "예약 게시물", api: "GET /api/v1/scheduled_statuses", kind: "scheduled", feature: "scheduled" },
      { id: "manage", icon: "🛡️", label: "필터 · 뮤트 · 차단", api: "GET /api/v2/filters · /api/v1/mutes · /api/v1/blocks · /api/v1/domain_blocks", kind: "manage" }
    ]
  },
  at: {
    software: "AT Protocol PDS",
    auth: "AT OAuth (PAR + DPoP)",
    scopes: ["atproto", "transition:generic"],
    charLimit: 300,
    timelines: [
      { id: "following", icon: "🏠", label: "Following", api: "app.bsky.feed.getTimeline", collectable: true },
      { id: "notifications", icon: "🔔", label: "알림", api: "app.bsky.notification.listNotifications" }
    ]
  }
};

/*
 * ActivityPub 클라이언트 기능 기준표 — Mastodon 5.0 이상이 제공하는 클라이언트 기능.
 * feature 키가 있는 항목은 연결 서버가 제공하지 않으면(missingFeatures) 비활성으로 표시한다.
 * demo: 프로토타입에서 해당 기능을 보여주는 위치 (timeline · post · profile · compose · search).
 */
window.CLAIRE_SYND_AP_FEATURES = [
  {
    group: "타임라인 · 탐색",
    items: [
      { label: "홈 타임라인 · 실시간 스트리밍", api: "GET /api/v1/timelines/home · /api/v1/streaming (user)", demo: { timeline: "home" } },
      { label: "로컬 · 연합 타임라인", api: "GET /api/v1/timelines/public[?local=true]", demo: { timeline: "federated" } },
      { label: "해시태그 타임라인 · 해시태그 팔로우", api: "GET /api/v1/timelines/tag/:tag · POST /api/v1/tags/:name/follow", demo: { timeline: "tag:ActivityPub" } },
      { label: "리스트", api: "GET /api/v1/timelines/list/:id · POST /api/v1/lists/:id/accounts", demo: { timeline: "list:standards" } },
      { label: "탐색 (트렌드 게시물 · 해시태그 · 링크)", api: "GET /api/v1/trends/statuses · /tags · /links", feature: "trends", demo: { timeline: "explore" } },
      { label: "통합 검색 (계정 · 해시태그 · 게시물, 원격 확인)", api: "GET /api/v2/search?resolve=true", demo: { search: "ActivityPub" } },
      { label: "개인 메시지 (대화)", api: "GET /api/v1/conversations", demo: { timeline: "conversations" } },
      { label: "좋아요 · 북마크 모아 보기", api: "GET /api/v1/favourites · /api/v1/bookmarks", demo: { timeline: "bookmarks" } }
    ]
  },
  {
    group: "알림",
    items: [
      { label: "묶음 알림 (좋아요 · 부스트 · 팔로우 · 인용 · 멘션)", api: "GET /api/v2/notifications", demo: { timeline: "notifications" } },
      { label: "멘션만 보기", api: "GET /api/v2/notifications?types[]=mention", demo: { timeline: "notifications" } },
      { label: "알림 요청 · 알림 정책", api: "GET /api/v1/notifications/requests · PATCH /api/v2/notifications/policy", feature: "notifRequests", demo: { timeline: "notifications" } }
    ]
  },
  {
    group: "게시물 표시",
    items: [
      { label: "부스트한 사람 · 공개 범위 · 언어 표시", api: "reblog · visibility · language", demo: { post: "p0-11" } },
      { label: "열람 주의 (CW)", api: "spoiler_text", demo: { post: "p0-12" } },
      { label: "미디어 · 대체 텍스트 · 민감한 미디어 가림", api: "media_attachments[].description · sensitive", demo: { post: "p0-16" } },
      { label: "투표 참여 · 결과", api: "POST /api/v1/polls/:id/votes", demo: { post: "p0-13" } },
      { label: "인용 게시물 · 인용 상태", api: "quote · quoted_status_id", feature: "quote", demo: { post: "p0-14" } },
      { label: "수정됨 표시 · 수정 이력", api: "GET /api/v1/statuses/:id/history", demo: { post: "p0-15" } },
      { label: "번역", api: "POST /api/v1/statuses/:id/translate", feature: "translate", demo: { post: "p0-11" } },
      { label: "키워드 필터 (경고 · 숨김)", api: "GET /api/v2/filters · filtered[]", demo: { post: "p0-17" } }
    ]
  },
  {
    group: "게시물 동작",
    items: [
      { label: "답글 · 부스트 · 좋아요 · 북마크", api: "POST /api/v1/statuses/:id/reblog · favourite · bookmark", demo: { post: "p0-4" } },
      { label: "인용하기 · 인용 허용 범위", api: "quoted_status_id · quote_approval_policy", feature: "quote", demo: { compose: "quote" } },
      { label: "내 글 고정 · 수정 · 삭제 후 다시 쓰기", api: "POST /pin · PUT /api/v1/statuses/:id · DELETE /api/v1/statuses/:id", demo: { post: "p0-21" } },
      { label: "대화 뮤트 · 링크 복사 · 원격 서버에서 열기", api: "POST /api/v1/statuses/:id/mute", demo: { post: "p0-1" } },
      { label: "신고", api: "POST /api/v1/reports", demo: { post: "p0-17" } }
    ]
  },
  {
    group: "작성",
    items: [
      { label: "공개 범위 4종 · 언어 · 열람 주의", api: "visibility · language · spoiler_text", demo: { compose: "new" } },
      { label: "투표 만들기", api: "poll[options][] · poll[expires_in] · poll[multiple]", demo: { compose: "new" } },
      { label: "미디어 첨부 · 대체 텍스트 · 민감함 표시", api: "POST /api/v2/media · media_ids · sensitive", demo: { compose: "new" } },
      { label: "예약 게시", api: "scheduled_at · GET /api/v1/scheduled_statuses", feature: "scheduled", demo: { timeline: "scheduled" } }
    ]
  },
  {
    group: "계정 · 관계 · 안전",
    items: [
      { label: "프로필 (소개 · 필드 · 고정 게시물 · 추천 해시태그)", api: "GET /api/v1/accounts/:id · /statuses?pinned=true · /featured_tags", demo: { profile: "@w3c@w3c.social" } },
      { label: "팔로우 · 새 글 알림 · 부스트 표시", api: "POST /api/v1/accounts/:id/follow (notify, reblogs)", demo: { profile: "@w3c@w3c.social" } },
      { label: "개인 메모 · 리스트에 추가", api: "POST /api/v1/accounts/:id/note · /api/v1/lists/:id/accounts", demo: { profile: "@mina@hachyderm.example" } },
      { label: "뮤트 · 차단 · 도메인 차단 · 필터 관리", api: "/api/v1/mutes · /blocks · /domain_blocks · /api/v2/filters", demo: { timeline: "manage" } }
    ]
  }
];

window.CLAIRE_SYND_SEEDS = {
  "0": {
    id: 0, seq: 0, icon: "📚", label: "기본 지식베이스", fqdn: "", dbPath: "data/claire.db", isPublic: true, defaultFocus: "",
    accounts: [
      {
        id: "acc0-ap", proto: "ap", software: "Mastodon", serverVersion: "5.0.0", server: "social.example.com",
        handle: "@claire@social.example.com", displayName: "Claire Bible 📚",
        auth: "OAuth 2.0 (Authorization Code + PKCE)",
        scopes: ["read", "write:statuses", "write:media", "write:favourites", "write:bookmarks", "write:follows", "write:lists", "write:mutes", "write:blocks", "write:filters", "write:reports", "write:notifications", "write:conversations", "write:accounts"],
        canPublish: true, status: "ok", statusNote: "스트리밍 연결됨 (user)", charLimit: 500, missingFeatures: [],
        timelines: [
          { id: "home", icon: "🏠", label: "홈", api: "GET /api/v1/timelines/home", stream: "user", collectable: true },
          { id: "notifications", icon: "🔔", label: "알림", api: "GET /api/v2/notifications", stream: "user:notification", kind: "notifications" },
          { id: "conversations", icon: "✉️", label: "개인 메시지", api: "GET /api/v1/conversations", stream: "direct" },
          { id: "explore", icon: "🧭", label: "탐색", api: "GET /api/v1/trends/statuses · /trends/tags · /trends/links", kind: "explore", feature: "trends" },
          { id: "local", icon: "🏘️", label: "로컬", api: "GET /api/v1/timelines/public?local=true", stream: "public:local" },
          { id: "federated", icon: "🌐", label: "연합", api: "GET /api/v1/timelines/public", stream: "public" },
          { id: "tag:ActivityPub", icon: "#", label: "#ActivityPub", api: "GET /api/v1/timelines/tag/ActivityPub", stream: "hashtag", collectable: true },
          { id: "list:standards", icon: "📋", label: "리스트 · 표준 기구", api: "GET /api/v1/timelines/list/12", stream: "list", collectable: true },
          { id: "favourites", icon: "★", label: "좋아요", api: "GET /api/v1/favourites", kind: "favourites" },
          { id: "bookmarks", icon: "🔖", label: "북마크", api: "GET /api/v1/bookmarks", kind: "bookmarks" },
          { id: "scheduled", icon: "🗓️", label: "예약 게시물", api: "GET /api/v1/scheduled_statuses", kind: "scheduled", feature: "scheduled" },
          { id: "manage", icon: "🛡️", label: "필터 · 뮤트 · 차단", api: "GET /api/v2/filters · /api/v1/mutes · /api/v1/blocks · /api/v1/domain_blocks", kind: "manage" }
        ],
        lists: [{ id: "standards", title: "표준 기구" }, { id: "neighbors", title: "가까운 이웃" }],
        filters: [{ id: "flt-1", title: "크립토 광고", keywords: ["크립토", "에어드롭"], context: ["home", "public"], action: "warn" }],
        mutes: [{ handle: "@spammy@spam.example", until: "무기한" }],
        blocks: [],
        domainBlocks: ["spam.example"],
        followedTags: ["ActivityPub", "SQLite"],
        notifRequests: [
          { id: "nr-1", handle: "@newbie@social.example.com", count: 1, preview: "@claire 안녕하세요, 적재 기능을 연합우주에서도 쓸 수 있는지 궁금합니다.", reason: "새 계정" },
          { id: "nr-2", handle: "@deals@promo.example", count: 3, preview: "@claire 한정 특가! 지금 바로 확인하세요", reason: "팔로우하지 않는 계정" }
        ],
        notifPolicy: { forNotFollowing: "filter", forNotFollowers: "accept", forNewAccounts: "filter", forPrivateMentions: "filter", forLimitedAccounts: "filter" },
        trends: {
          statuses: ["p0-4", "p0-13", "p0-1"],
          tags: [
            { name: "FEP044f", history: [8, 19, 46], accounts: 52 },
            { name: "SQLite", history: [5, 7, 63], accounts: 71 },
            { name: "ActivityPub", history: [30, 34, 41], accounts: 88 }
          ],
          links: [
            { title: "Social Web Working Group Charter (Draft 2026–2028)", description: "Quote post interoperability with approval stamps, actor portability, RFC 9421 HTTP Message Signatures.", url: "https://www.w3.org/2026/socialweb-charter-draft", provider: "w3.org", author: "W3C", shares: 120 },
            { title: "SQLite Release 3.51.0", description: "FTS5 multi-token phrase cost estimation and non-blocking WAL checkpoints.", url: "https://sqlite.org/releaselog/3_51_0.html", provider: "sqlite.org", author: "", shares: 95 },
            { title: "Designing alt text for technical diagrams", description: "Practical guidance for describing sequence diagrams and charts for screen reader users.", url: "https://a11y.example.org/alt-text-diagrams", provider: "a11y.example.org", author: "Mina", shares: 41 }
          ]
        }
      },
      {
        id: "acc0-at", proto: "at", software: "Bluesky PDS", server: "bsky.social",
        handle: "@claire.example.com", did: "did:plc:claire4kb2x7q", displayName: "Claire Bible 📚",
        auth: "AT OAuth (PAR + DPoP)", scopes: ["atproto", "transition:generic"],
        canPublish: true, status: "ok", statusNote: "세션 갱신 4분 전", charLimit: 300,
        timelines: [
          { id: "following", icon: "🏠", label: "Following", api: "app.bsky.feed.getTimeline", collectable: true },
          { id: "notifications", icon: "🔔", label: "알림", api: "app.bsky.notification.listNotifications" },
          { id: "feed:atproto-dev", icon: "🧭", label: "커스텀 피드 · ATproto Dev", api: "app.bsky.feed.getFeed (at://did:plc:feedgen-dev/app.bsky.feed.generator/atproto-dev)", collectable: true },
          { id: "list:researchers", icon: "📋", label: "리스트 · 프로토콜 연구자", api: "app.bsky.feed.getListFeed (at://did:plc:claire4kb2x7q/app.bsky.graph.list/3kresearch)", collectable: true }
        ]
      }
    ],
    posts: [
      {
        id: "p0-1", accountId: "acc0-ap", timelines: ["home", "tag:ActivityPub", "list:standards"], unread: true,
        author: "W3C Social Web WG", handle: "@w3c@w3c.social", time: "07:55", timestamp: 1761631000,
        text: "Social Web WG 2026–2028 charter renewal draft is open for public review: standardized quote posts (FEP-044f), portable actors (LOLA) and HTTP Message Signatures. Comments welcome until Nov 30. #ActivityPub #W3C #FEP044f",
        hashtags: ["ActivityPub", "W3C", "FEP044f"],
        card: { title: "Social Web Working Group Charter (Draft 2026–2028)", description: "Proposed scope covers quote post interoperability with approval stamps, actor portability across servers without breaking object URIs, and migration to RFC 9421 HTTP Message Signatures.", url: "https://www.w3.org/2026/socialweb-charter-draft" },
        url: "https://w3c.social/@w3c/11542910021", uri: "https://w3c.social/users/w3c/statuses/11542910021", remoteId: "115429100210443008",
        counts: { replies: 2, boosts: 31, favs: 58 }, boosted: true, favourited: false, bookmarked: true,
        kbItemId: "i0-2"
      },
      {
        id: "p0-2", accountId: "acc0-ap", timelines: [], replyTo: "p0-1", unread: false,
        author: "Evan", handle: "@evan@cosocial.example", time: "08:03", timestamp: 1761631480,
        text: "Quote approval stamps are the part I'm most excited about — finally real provenance for quotes across servers. #FEP044f",
        hashtags: ["FEP044f"], card: null,
        url: "https://cosocial.example/@evan/11542913377", uri: "https://cosocial.example/users/evan/statuses/11542913377", remoteId: "115429133770012001",
        counts: { replies: 1, boosts: 4, favs: 19 }, boosted: false, favourited: true, bookmarked: false
      },
      {
        id: "p0-3", accountId: "acc0-ap", timelines: [], replyTo: "p0-2", unread: false,
        author: "W3C Social Web WG", handle: "@w3c@w3c.social", time: "08:10", timestamp: 1761631900,
        text: "@evan Agreed. The draft keeps quote approval optional per server, so clients can fall back to a plain link when the target server does not support it.",
        hashtags: [], card: null,
        url: "https://w3c.social/@w3c/11542918810", uri: "https://w3c.social/users/w3c/statuses/11542918810", remoteId: "115429188100550004",
        counts: { replies: 0, boosts: 2, favs: 11 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p0-4", accountId: "acc0-ap", timelines: ["home"], unread: true,
        author: "SQLite News", handle: "@sqlite_news@fosstodon.example", time: "08:44", timestamp: 1761634100,
        text: "SQLite 3.51.0 is out — FTS5 query planner now estimates multi-token phrase cost from segment page counts, and WAL checkpoints no longer stall concurrent readers. #SQLite #FTS5",
        hashtags: ["SQLite", "FTS5"],
        card: { title: "SQLite Release 3.51.0", description: "Release log: FTS5 multi-token phrase cost estimation, non-blocking WAL2 snapshot promotion, reduced reader tail latency.", url: "https://sqlite.org/releaselog/3_51_0.html" },
        url: "https://fosstodon.example/@sqlite_news/11542960001", uri: "https://fosstodon.example/users/sqlite_news/statuses/11542960001", remoteId: "115429600010000771",
        counts: { replies: 6, boosts: 88, favs: 203 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p0-5", accountId: "acc0-ap", timelines: ["notifications"], notif: { kind: "mention", by: "@mina@hachyderm.example" }, unread: true,
        author: "Mina", handle: "@mina@hachyderm.example", time: "09:01", timestamp: 1761635000,
        text: "@claire 혹시 AT Protocol facet 태그를 엔티티로 바꾸는 규칙이 문서로 공개되어 있나요? 적재 전에는 그냥 태그로 남는다고 들었는데 맞는지 궁금합니다. #ATProtocol",
        hashtags: ["ATProtocol"], card: null,
        url: "https://hachyderm.example/@mina/11542970042", uri: "https://hachyderm.example/users/mina/statuses/11542970042", remoteId: "115429700420003310",
        counts: { replies: 0, boosts: 0, favs: 1 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p0-6", accountId: "acc0-ap", timelines: ["local"], unread: false,
        author: "social.example.com 관리자", handle: "@admin@social.example.com", time: "어제", timestamp: 1761598000,
        text: "서버 점검 안내: 11/2(일) 03:00–03:30 KST, PostgreSQL 메이저 업그레이드. 점검 중에는 타임라인 API와 스트리밍이 일시 중단됩니다.",
        hashtags: [], card: null,
        url: "https://social.example.com/@admin/11542800001", uri: "https://social.example.com/users/admin/statuses/11542800001", remoteId: "115428000010000001",
        counts: { replies: 3, boosts: 12, favs: 40 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p0-7", accountId: "acc0-ap", timelines: ["tag:ActivityPub"], unread: true,
        author: "Darius", handle: "@darius@friend.example", time: "06:20", timestamp: 1761625200,
        text: "W3C 소셜 웹 명세의 변경분(diff)을 매일 게시하는 작은 봇을 만들었습니다. 초안 리뷰 기간 동안만 돌릴 예정이에요. #ActivityPub #SpecDiff",
        hashtags: ["ActivityPub", "SpecDiff"], card: null,
        url: "https://friend.example/@darius/11542940077", uri: "https://friend.example/users/darius/statuses/11542940077", remoteId: "115429400770002210",
        counts: { replies: 1, boosts: 9, favs: 27 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p0-8", accountId: "acc0-at", timelines: ["following", "feed:atproto-dev"], unread: true,
        author: "AT Protocol", handle: "@atproto.com", time: "어제", timestamp: 1761627000,
        text: "Jetstream v2: deterministic cursor replay with zstd compression and lossless facet tag extraction for periodic archivers. #atproto #Jetstream",
        hashtags: ["atproto", "Jetstream"],
        card: { title: "Jetstream v2 release notes", description: "Subscribe to specific DID collections and Lexicon schemas without consuming a full relay firehose; tag facets are preserved verbatim.", url: "https://docs.bsky.app/blog/jetstream-v2" },
        url: "https://bsky.app/profile/atproto.com/post/3l7x9k2", uri: "at://did:plc:atproto-dev/app.bsky.feed.post/3l7x9k2", cid: "bafyreib2rxk3rh6kzwq7jetstreamv2",
        counts: { replies: 14, boosts: 120, favs: 410 }, boosted: false, favourited: true, bookmarked: false,
        kbItemId: "i0-3"
      },
      {
        id: "p0-9", accountId: "acc0-at", timelines: ["following", "feed:atproto-dev", "list:researchers"], unread: true,
        author: "Lexicon Notes", handle: "@lexicon-notes.example.net", time: "05:48", timestamp: 1761623300,
        text: "Lexicon 팁: app.bsky.richtext.facet#tag 의 byteStart/byteEnd 는 UTF-8 바이트 오프셋입니다. 한국어 해시태그를 다룰 때 문자 인덱스로 계산하면 어긋나요. #atproto #Lexicon",
        hashtags: ["atproto", "Lexicon"], card: null,
        url: "https://bsky.app/profile/lexicon-notes.example.net/post/3l7w2ab", uri: "at://did:plc:lexnotes/app.bsky.feed.post/3l7w2ab", cid: "bafyreic7lexiconfacetbyteoffsets",
        counts: { replies: 5, boosts: 33, favs: 96 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p0-10", accountId: "acc0-at", timelines: ["notifications"], notif: { kind: "reblog", by: "@kb-curator.example.org" }, unread: true,
        author: "Claire Bible 📚", handle: "@claire.example.com", time: "어제", timestamp: 1761612000, own: true,
        text: "📡 적재 발행 · SQLite 3.51.0 은 FTS5 세그먼트 비용 기반 실행 계획과 비차단형 WAL 체크포인트로 p99 지연을 69% 단축했다. #SQLite #FTS5",
        hashtags: ["SQLite", "FTS5"],
        card: { title: "SQLite 3.51.0 — Claire 적재 요약", description: "Claire Bible 지식 그래프 공유 문서", url: "https://claire.example.com/p?s=sh_991a0f" },
        url: "https://bsky.app/profile/claire.example.com/post/3l7v0zz", uri: "at://did:plc:claire4kb2x7q/app.bsky.feed.post/3l7v0zz", cid: "bafyreid0clairesqlite3510summary",
        counts: { replies: 0, boosts: 7, favs: 22 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p0-11", accountId: "acc0-ap", timelines: ["home", "tag:ActivityPub"], unread: true, visibility: "public", lang: "en",
        rebloggedBy: { name: "Mina", handle: "@mina@hachyderm.example" },
        author: "Fediverse Dev Notes", handle: "@devnotes@fosstodon.example", time: "07:40", timestamp: 1761630000,
        text: "How a quote post travels between servers: Create(Note with quote) → QuoteRequest → Accept with an approval stamp. Diagram with alt text below. #ActivityPub #FEP044f",
        translation: "인용 게시물이 서버 사이를 이동하는 과정: 인용이 담긴 Create(Note) → QuoteRequest → 승인 스탬프가 담긴 Accept. 아래 도식에 대체 텍스트가 있습니다. #ActivityPub #FEP044f",
        hashtags: ["ActivityPub", "FEP044f"], card: null,
        media: [{ kind: "image", label: "quote-flow.png", alt: "시퀀스 도식: 인용하는 서버가 QuoteRequest 를 보내고, 원 작성자 서버가 승인 스탬프를 담은 Accept 로 응답한 뒤 두 서버 모두에 인용이 표시된다." }],
        url: "https://fosstodon.example/@devnotes/11542905000", uri: "https://fosstodon.example/users/devnotes/statuses/11542905000", remoteId: "115429050000000101",
        counts: { replies: 3, boosts: 45, favs: 120 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p0-12", accountId: "acc0-ap", timelines: ["home"], unread: true, visibility: "unlisted", lang: "ko",
        author: "Mina", handle: "@mina@hachyderm.example", time: "07:20", timestamp: 1761629000,
        cw: "W3C 회의 결과 (스포일러)",
        text: "결국 인용 승인은 서버 선택 사항으로 남기로 했습니다. 다음 초안에서 표기 방식만 정리한다고 해요. #W3C",
        hashtags: ["W3C"], card: null,
        url: "https://hachyderm.example/@mina/11542900120", uri: "https://hachyderm.example/users/mina/statuses/11542900120", remoteId: "115429001200000044",
        counts: { replies: 1, boosts: 2, favs: 14 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p0-13", accountId: "acc0-ap", timelines: ["home", "local"], unread: true, visibility: "public", lang: "ko",
        author: "social.example.com 관리자", handle: "@admin@social.example.com", time: "06:55", timestamp: 1761627500,
        text: "서버 기본값 투표: 새 게시물의 인용 허용 범위 기본값을 무엇으로 할까요? (개인 설정에서 언제든 바꿀 수 있습니다) #서버운영",
        hashtags: ["서버운영"], card: null,
        poll: { id: "poll-881", multiple: false, expires: "2일 남음", expired: false, voted: false, ownVotes: [], options: [{ title: "누구나", votes: 41 }, { title: "팔로워만", votes: 27 }, { title: "나만 (인용 불가)", votes: 9 }] },
        url: "https://social.example.com/@admin/11542890880", uri: "https://social.example.com/users/admin/statuses/11542890880", remoteId: "115428908800000002",
        counts: { replies: 6, boosts: 12, favs: 30 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p0-14", accountId: "acc0-ap", timelines: ["home"], unread: true, visibility: "public", lang: "ko",
        author: "Evan", handle: "@evan@cosocial.example", time: "08:15", timestamp: 1761632100,
        text: "꼭 읽어볼 초안입니다. 인용 승인 스탬프 부분이 핵심이에요.",
        hashtags: [], card: null, quoteOf: "p0-1", quoteState: "accepted",
        url: "https://cosocial.example/@evan/11542921500", uri: "https://cosocial.example/users/evan/statuses/11542921500", remoteId: "115429215000000310",
        counts: { replies: 0, boosts: 5, favs: 22 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p0-15", accountId: "acc0-ap", timelines: ["home"], unread: false, visibility: "public", lang: "ko",
        author: "SQLite News", handle: "@sqlite_news@fosstodon.example", time: "08:50", timestamp: 1761634500,
        text: "SQLite 3.51.0 정오표: FTS5 메모리 할당 감소 수치는 '34%'가 아니라 'CJK trigram 색인에서 최대 34%'입니다. #SQLite",
        hashtags: ["SQLite"], card: null, edited: true, editedAt: "08:58",
        history: [{ at: "08:50", text: "SQLite 3.51.0 정오표: FTS5 메모리 할당 감소 수치는 34%입니다. #SQLite" }],
        url: "https://fosstodon.example/@sqlite_news/11542965002", uri: "https://fosstodon.example/users/sqlite_news/statuses/11542965002", remoteId: "115429650020000880",
        counts: { replies: 2, boosts: 18, favs: 51 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p0-16", accountId: "acc0-ap", timelines: ["federated"], unread: true, visibility: "public", lang: "ja",
        author: "Kenji", handle: "@kenji@misskey.example", time: "05:30", timestamp: 1761622200,
        text: "サーバー移行のログ画像です。アカウント移行の進捗が見えます。#Fediverse",
        translation: "서버 이전 로그 이미지입니다. 계정 이전 진행률이 보입니다. #Fediverse",
        hashtags: ["Fediverse"], card: null, sensitive: true,
        media: [{ kind: "image", label: "migration-log.png", alt: "서버 이전 중 터미널 로그 화면 캡처 — 팔로워 이전 단계 진행률 82%가 표시됨" }],
        url: "https://misskey.example/notes/9x2k4m", uri: "https://misskey.example/notes/9x2k4m", remoteId: "115429220000000555",
        counts: { replies: 0, boosts: 4, favs: 19 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p0-17", accountId: "acc0-ap", timelines: ["home", "federated"], unread: true, visibility: "public", lang: "ko",
        author: "Airdrop Bot", handle: "@promo@promo.example", time: "04:12", timestamp: 1761617500,
        text: "🚀 크립토 에어드롭 이벤트! 지금 지갑을 연결하면 보상을 드립니다 #crypto",
        hashtags: ["crypto"], card: null,
        url: "https://promo.example/@promo/1", uri: "https://promo.example/users/promo/statuses/1", remoteId: "115429170000000999",
        counts: { replies: 0, boosts: 1, favs: 0 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p0-18", accountId: "acc0-ap", timelines: ["federated"], unread: false, visibility: "public", lang: "ko",
        author: "Lee", handle: "@lee@pixelfed.example", time: "어제", timestamp: 1761590000,
        text: "새 사진 연작: 오래된 도서관의 서가들 #Photography",
        hashtags: ["Photography"], card: null,
        media: [
          { kind: "image", label: "library-01.jpg", alt: "높은 나무 서가와 이동식 사다리가 있는 오래된 도서관 내부" },
          { kind: "image", label: "library-02.jpg", alt: "창가 열람석 위에 펼쳐진 고서 두 권" }
        ],
        url: "https://pixelfed.example/p/lee/7781", uri: "https://pixelfed.example/p/lee/7781", remoteId: "115428800000000777",
        counts: { replies: 2, boosts: 9, favs: 64 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p0-19", accountId: "acc0-ap", timelines: ["conversations"], unread: true, visibility: "direct", lang: "ko",
        author: "Mina", handle: "@mina@hachyderm.example", time: "09:10", timestamp: 1761635300,
        text: "@claire 적재 문서 공유 링크(/p?s=…)를 팀 위키에 걸어도 되는지 확인 부탁드려요.",
        hashtags: [], card: null,
        url: "https://hachyderm.example/@mina/11542975555", uri: "https://hachyderm.example/users/mina/statuses/11542975555", remoteId: "115429755550000012",
        counts: { replies: 1, boosts: 0, favs: 0 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p0-20", accountId: "acc0-ap", timelines: [], replyTo: "p0-19", unread: false, visibility: "direct", lang: "ko", own: true,
        author: "Claire Bible 📚", handle: "@claire@social.example.com", time: "09:14", timestamp: 1761635500,
        text: "@mina 네, 공개 테마 문서라 공유 링크는 그대로 쓰셔도 됩니다.",
        hashtags: [], card: null,
        url: "https://social.example.com/@claire/11542976601", uri: "https://social.example.com/users/claire/statuses/11542976601", remoteId: "115429766010000003",
        counts: { replies: 0, boosts: 0, favs: 1 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p0-21", accountId: "acc0-ap", timelines: ["home"], unread: false, visibility: "public", lang: "ko", own: true, pinned: true, quotePolicy: "public",
        author: "Claire Bible 📚", handle: "@claire@social.example.com", time: "08:20", timestamp: 1761632400,
        text: "📡 적재 발행 · W3C Social Web WG 헌장 갱신 초안 요약: 인용 승인 스탬프, 계정 이전, HTTP 메시지 서명. #ActivityPub #W3C",
        hashtags: ["ActivityPub", "W3C"],
        card: { title: "Social Web WG 헌장 초안 — Claire 적재 요약", description: "Claire Bible 지식 그래프 공유 문서", url: "https://claire.example.com/p?s=sh_w3c26" },
        url: "https://social.example.com/@claire/11542923300", uri: "https://social.example.com/users/claire/statuses/11542923300", remoteId: "115429233000000001",
        counts: { replies: 1, boosts: 3, favs: 7 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p0-22", accountId: "acc0-ap", timelines: ["notifications"], notifOnly: true, unread: true, time: "09:05", timestamp: 1761635100,
        notif: { kind: "favourite", by: ["@mina@hachyderm.example", "@evan@cosocial.example", "@darius@friend.example"], total: 7, target: "p0-21" },
        author: "", handle: "", text: "", hashtags: [], card: null, counts: { replies: 0, boosts: 0, favs: 0 }
      },
      {
        id: "p0-23", accountId: "acc0-ap", timelines: ["notifications"], notifOnly: true, unread: true, time: "08:58", timestamp: 1761634700,
        notif: { kind: "reblog", by: ["@evan@cosocial.example", "@kenji@misskey.example"], total: 3, target: "p0-21" },
        author: "", handle: "", text: "", hashtags: [], card: null, counts: { replies: 0, boosts: 0, favs: 0 }
      },
      {
        id: "p0-24", accountId: "acc0-ap", timelines: ["notifications"], notifOnly: true, unread: false, time: "어제", timestamp: 1761601000,
        notif: { kind: "follow", by: ["@kenji@misskey.example", "@lee@pixelfed.example"], total: 2 },
        author: "", handle: "", text: "", hashtags: [], card: null, counts: { replies: 0, boosts: 0, favs: 0 }
      },
      {
        id: "p0-25", accountId: "acc0-ap", timelines: ["notifications"], unread: true, visibility: "public", lang: "ko",
        notif: { kind: "quote", by: ["@darius@friend.example"] },
        author: "Darius", handle: "@darius@friend.example", time: "08:40", timestamp: 1761633600,
        text: "Claire 쪽 요약이 제일 깔끔하네요. 원문 링크까지 같이 붙어 있어서 좋습니다.",
        hashtags: [], card: null, quoteOf: "p0-21", quoteState: "accepted",
        url: "https://friend.example/@darius/11542936000", uri: "https://friend.example/users/darius/statuses/11542936000", remoteId: "115429360000000470",
        counts: { replies: 0, boosts: 1, favs: 3 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p0-26", accountId: "acc0-ap", timelines: ["scheduled"], unread: false, visibility: "public", lang: "ko", own: true,
        scheduled: { id: "sch-3301", at: "11/02 (일) 09:00" },
        author: "Claire Bible 📚", handle: "@claire@social.example.com", time: "예약", timestamp: 1761900000,
        text: "📡 주간 정리: 이번 주 지식베이스에 적재된 연합우주 표준 문서 5건 #ActivityPub",
        hashtags: ["ActivityPub"], card: null,
        url: "https://social.example.com/@claire", uri: "", remoteId: "",
        counts: { replies: 0, boosts: 0, favs: 0 }, boosted: false, favourited: false, bookmarked: false
      }
    ],
    profiles: {
      "@w3c@w3c.social": {
        name: "W3C Social Web WG", bio: "W3C Social Web Working Group 공식 계정. ActivityPub 및 관련 명세의 표준화를 다룹니다.",
        fields: [{ name: "Website", value: "https://www.w3.org/groups/wg/socialweb", verified: true }, { name: "Charter", value: "2026–2028 초안 검토 중" }],
        followers: 18240, following: 12, statuses: 941, joined: "2023년 2월", bot: false,
        featuredTags: ["ActivityPub", "W3C"], pinned: ["p0-1"],
        rel: { following: true, notify: true, showReblogs: true, muted: false, blocked: false, note: "표준 문서 원문 출처", lists: ["standards"] }
      },
      "@mina@hachyderm.example": {
        name: "Mina", bio: "연합우주 도구를 만드는 개발자. 지식 관리, SQLite, 접근성(대체 텍스트)에 관심이 많습니다.",
        fields: [{ name: "Blog", value: "https://mina.example.dev", verified: true }, { name: "Pronouns", value: "공개 안 함" }],
        followers: 1203, following: 388, statuses: 5520, joined: "2022년 11월", bot: false,
        featuredTags: ["SQLite", "a11y"], pinned: [],
        rel: { following: true, notify: false, showReblogs: true, muted: false, blocked: false, note: "", lists: ["neighbors"] }
      },
      "@evan@cosocial.example": {
        name: "Evan", bio: "Fediverse 명세 리뷰어. FEP 토론에 자주 등장합니다.",
        fields: [{ name: "FEP", value: "https://codeberg.org/fediverse/fep" }],
        followers: 3410, following: 902, statuses: 12880, joined: "2017년 4월", bot: false,
        featuredTags: ["FEP044f"], pinned: [],
        rel: { following: true, notify: false, showReblogs: false, muted: false, blocked: false, note: "", lists: [] }
      },
      "@promo@promo.example": {
        name: "Airdrop Bot", bio: "프로모션 자동 게시 계정", fields: [], followers: 12, following: 4810, statuses: 22041, joined: "이번 주", bot: true,
        featuredTags: [], pinned: [], rel: { following: false, notify: false, showReblogs: true, muted: false, blocked: false, note: "", lists: [] }
      }
    },
    existingEntities: [
      { name: "SQLite", type: "Tool", count: 19 },
      { name: "ActivityPub", type: "Concept", count: 12 },
      { name: "AT Protocol", type: "Concept", count: 7 },
      { name: "FTS5", type: "Tool", count: 9 },
      { name: "W3C", type: "Org", count: 6 }
    ],
    rules: [
      { id: "r0-1", name: "SQLite 릴리스 노트 우선 오거나이즈", cond: "수집 태그 일치: [[SQLite]]", action: "점수 +45 · 별표 부여", enabled: true },
      { id: "r0-2", name: "W3C 표준 권고안 자동 적재 전달", cond: "대상: @w3c@w3c.social (연결 계정 경유) + #ActivityPub", action: "프로젝트 적재 파이프라인 자동 전달", enabled: true }
    ],
    feeds: [
      { id: "f0-1", folder: "오픈소스 릴리스", proto: "rss", format: "ATOM 1.0", interval: "15m", title: "SQLite Release Log", url: "https://sqlite.org/news.atom", etag: "200 OK" },
      { id: "f0-2", folder: "Fediverse 표준 기구", proto: "ap", format: "ActivityPub", interval: "stream", title: "W3C Social Web WG", handle: "@w3c@w3c.social", url: "https://w3c.social/@w3c", via: "acc0-ap", source: "계정 게시물 · GET /api/v1/accounts/:id/statuses", etag: "연결 계정 경유 · 스트리밍" },
      { id: "f0-3", folder: "분산 소셜 프로토콜", proto: "at", format: "AT Protocol", interval: "15m", title: "AT Protocol Core Dev", handle: "@atproto.com", url: "https://bsky.app/profile/atproto.com", via: "acc0-at", source: "작성자 피드 · app.bsky.feed.getAuthorFeed", etag: "PDS 커서 동기화" }
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
        publishState: "published_create", publishedInfo: { mode: "ingested_create", at: "어제", accounts: ["acc0-at"], note: "AT 연결 계정으로 적재 발행 완료" }
      },
      {
        id: "i0-2", feedId: "f0-2", proto: "ap", format: "AP", viaAccount: "acc0-ap",
        title: "Social Web Working Group Charter Renewal: Portable Actors & Quote Posts (FEP-044f)",
        author: "@w3c@w3c.social", apObjectUri: "https://w3c.social/users/w3c/statuses/11542910021", remoteId: "115429100210443008", url: "https://w3c.social/@w3c/11542910021",
        timeAgo: "07:55", timestamp: 1761631000, unread: true, starred: false, score: 83,
        sourceTags: ["ActivityPub", "W3C", "FEP044f", "Fediverse"],
        rawBody: "We have published the updated Social Web WG draft covering standardized Quote Posts (FEP-044f), Portable Actor Key Rotation, and HTTP Message Signatures.",
        rawHtml: `<p>The <strong>W3C Social Web Working Group</strong> has published its 2026–2028 charter renewal draft for public review across the Fediverse.</p>
<ul><li><strong>Standardized Quote Posts (FEP-044f)</strong>: Explicit <code>quoteUrl</code> &amp; cryptographic quote approval stamps so RSS/Web/ActivityPub citations preserve provenance.</li>
<li><strong>Actor Portability (LOLA)</strong>: Seamless migration of followers and authored collections without severing object URIs.</li></ul>
<p>Tags from ActivityPub payload: <code>#ActivityPub</code> <code>#W3C</code> <code>#FEP044f</code> <code>#Fediverse</code></p>`,
        ingestState: "unlinked", linkedDoc: null,
        publishState: "boosted", publishedInfo: { mode: "boost", at: "40분 전", accounts: ["acc0-ap"], note: "연결 계정 부스트(reblog) 완료" }
      },
      {
        id: "i0-3", feedId: "f0-3", proto: "at", format: "AT-URI", viaAccount: "acc0-at",
        title: "Jetstream v2 Deterministic Cursor Replay & Lexicon Tag Facets for Knowledge Archiving",
        author: "@atproto.com", url: "https://bsky.app/profile/atproto.com/post/3l7x9k2",
        atUri: "at://did:plc:atproto-dev/app.bsky.feed.post/3l7x9k2", atCid: "bafyreib2rxk3rh6kzwq7jetstreamv2",
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
    defaultFocus: "시스템 아키텍처 및 추론 메모리 대역폭 최적화 중심",
    accounts: [
      {
        id: "acc1-ap", proto: "ap", software: "Mastodon API 호환 서버", server: "fedi.ai.example.com",
        handle: "@ai@fedi.ai.example.com", displayName: "Claire · AI Systems 🤖",
        auth: "OAuth 2.0 (Authorization Code + PKCE)", scopes: ["read", "write:statuses", "write:media", "write:favourites", "write:bookmarks", "write:follows", "write:lists", "write:mutes", "write:blocks", "write:filters", "write:reports", "write:notifications", "write:conversations", "write:accounts"],
        canPublish: true, status: "warn", statusNote: "Mastodon 5.0 기준 기능 중 일부를 서버가 제공하지 않음 — 해당 기능 비활성", charLimit: 500,
        missingFeatures: ["trends", "quote", "translate", "notifRequests", "scheduled"],
        timelines: [
          { id: "home", icon: "🏠", label: "홈", api: "GET /api/v1/timelines/home", stream: "user", collectable: true },
          { id: "notifications", icon: "🔔", label: "알림", api: "GET /api/v2/notifications", stream: "user:notification", kind: "notifications" },
          { id: "conversations", icon: "✉️", label: "개인 메시지", api: "GET /api/v1/conversations", stream: "direct" },
          { id: "explore", icon: "🧭", label: "탐색", api: "GET /api/v1/trends/statuses · /trends/tags · /trends/links", kind: "explore", feature: "trends" },
          { id: "local", icon: "🏘️", label: "로컬", api: "GET /api/v1/timelines/public?local=true", stream: "public:local" },
          { id: "federated", icon: "🌐", label: "연합", api: "GET /api/v1/timelines/public", stream: "public" },
          { id: "tag:KVCache", icon: "#", label: "#KVCache", api: "GET /api/v1/timelines/tag/KVCache", stream: "hashtag", collectable: true },
          { id: "list:ml-researchers", icon: "📋", label: "리스트 · ML 시스템 연구자", api: "GET /api/v1/timelines/list/3", stream: "list", collectable: true },
          { id: "favourites", icon: "★", label: "좋아요", api: "GET /api/v1/favourites", kind: "favourites" },
          { id: "bookmarks", icon: "🔖", label: "북마크", api: "GET /api/v1/bookmarks", kind: "bookmarks" },
          { id: "scheduled", icon: "🗓️", label: "예약 게시물", api: "GET /api/v1/scheduled_statuses", kind: "scheduled", feature: "scheduled" },
          { id: "manage", icon: "🛡️", label: "필터 · 뮤트 · 차단", api: "GET /api/v2/filters · /api/v1/mutes · /api/v1/blocks · /api/v1/domain_blocks", kind: "manage" }
        ],
        lists: [{ id: "ml-researchers", title: "ML 시스템 연구자" }],
        filters: [], mutes: [], blocks: [], domainBlocks: [], followedTags: ["KVCache"], notifRequests: [],
        notifPolicy: { forNotFollowing: "accept", forNotFollowers: "accept", forNewAccounts: "accept", forPrivateMentions: "accept", forLimitedAccounts: "filter" },
        trends: { statuses: [], tags: [], links: [] }
      },
      {
        id: "acc1-at", proto: "at", software: "자체 호스팅 PDS", server: "pds.ai.example.com",
        handle: "@ai.example.com", did: "did:web:ai.example.com", displayName: "Claire · AI Systems 🤖",
        auth: "앱 비밀번호 (com.atproto.server.createSession)", scopes: ["app-password"],
        canPublish: true, status: "warn", statusNote: "앱 비밀번호 사용 중 — OAuth 전환 권장", charLimit: 300,
        timelines: [
          { id: "following", icon: "🏠", label: "Following", api: "app.bsky.feed.getTimeline", collectable: true },
          { id: "notifications", icon: "🔔", label: "알림", api: "app.bsky.notification.listNotifications" },
          { id: "feed:ml-systems", icon: "🧭", label: "커스텀 피드 · ML Systems", api: "app.bsky.feed.getFeed (at://did:plc:mlsys-feed/app.bsky.feed.generator/ml-systems)", collectable: true }
        ]
      }
    ],
    posts: [
      {
        id: "p1-1", accountId: "acc1-ap", timelines: ["home", "tag:KVCache"], unread: true,
        author: "KV Cache Research", handle: "@kvcache@sigmoid.example", time: "09:20", timestamp: 1761635400,
        text: "New preprint: disaggregated KV cache routing across H100 prefill / L40S decode pools over RoCEv2 RDMA. TTFT −50%, TPOT −49% at 128K context. #KVCache #RDMA #vLLM",
        hashtags: ["KVCache", "RDMA", "vLLM"],
        card: { title: "Disaggregated KV Cache Routing Across Heterogeneous GPU Clusters", description: "TieredKV: a zero-copy prefix block router built atop vLLM and PagedAttention that streams FP8 KV blocks between tiers.", url: "https://arxiv.org/abs/2610.14821" },
        url: "https://sigmoid.example/@kvcache/11543000101", uri: "https://sigmoid.example/users/kvcache/statuses/11543000101", remoteId: "115430001010008801",
        counts: { replies: 9, boosts: 64, favs: 151 }, boosted: false, favourited: true, bookmarked: false
      },
      {
        id: "p1-2", accountId: "acc1-ap", timelines: ["home", "list:ml-researchers"], unread: true,
        author: "Georgi Gerganov", handle: "@ggerganov@mastodon.social", time: "08:31", timestamp: 1761632900,
        text: "llama.cpp: EAGLE-3 draft heads for speculative decoding are merged. On M-series laptops we see ~1.8x on code completion with no quality loss. #llamacpp #SpeculativeDecoding",
        hashtags: ["llamacpp", "SpeculativeDecoding"], card: null,
        url: "https://mastodon.social/@ggerganov/11543000555", uri: "https://mastodon.social/users/ggerganov/statuses/11543000555", remoteId: "115430005550001402",
        counts: { replies: 22, boosts: 140, favs: 512 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p1-3", accountId: "acc1-ap", timelines: ["notifications"], notif: { kind: "mention", by: "@infra-sre@hachyderm.example" }, unread: true,
        author: "Infra SRE", handle: "@infra-sre@hachyderm.example", time: "07:12", timestamp: 1761628300,
        text: "@ai 지난주 적재 발행하신 PagedAttention 요약, 사내 위키로 인용해도 될까요? 원문 arXiv 링크와 같이 걸겠습니다.",
        hashtags: [], card: null,
        url: "https://hachyderm.example/@infra-sre/11543000999", uri: "https://hachyderm.example/users/infra-sre/statuses/11543000999", remoteId: "115430009990000117",
        counts: { replies: 0, boosts: 0, favs: 0 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p1-4", accountId: "acc1-at", timelines: ["following", "feed:ml-systems"], unread: true,
        author: "vLLM Project", handle: "@vllm-project.example.org", time: "08:06", timestamp: 1761632160,
        text: "vLLM v0.9: zero-overhead speculative decoding — draft verification is fused into the scheduler step, no host-device sync bubbles. #vLLM #SpeculativeDecoding",
        hashtags: ["vLLM", "SpeculativeDecoding"],
        card: { title: "Zero-Overhead Speculative Decoding in vLLM v0.9", description: "EAGLE-3 / ngram draft tokens run inside the unified persistent CUDA graph runner without Python-side round trips.", url: "https://blog.vllm.ai/2026/10/speculative-v09.html" },
        url: "https://bsky.app/profile/vllm-project.example.org/post/3l7y1vv", uri: "at://did:plc:vllmproject/app.bsky.feed.post/3l7y1vv", cid: "bafyreif5vllmv09specdecodefused",
        counts: { replies: 11, boosts: 97, favs: 330 }, boosted: false, favourited: false, bookmarked: false
      },
      {
        id: "p1-5", accountId: "acc1-at", timelines: ["feed:ml-systems"], unread: false,
        author: "Paged Thoughts", handle: "@paged-thoughts.example.net", time: "어제", timestamp: 1761600000,
        text: "PagedAttention 블록 테이블을 클러스터 전역 주소 공간으로 확장하면 prefix 캐시 적중률이 87%까지 올라갑니다. RDMA 단방향 write 로 이관 지연을 숨기는 게 핵심. #PagedAttention #KVCache",
        hashtags: ["PagedAttention", "KVCache"], card: null,
        url: "https://bsky.app/profile/paged-thoughts.example.net/post/3l7u8pp", uri: "at://did:plc:pagedthoughts/app.bsky.feed.post/3l7u8pp", cid: "bafyreia9pagedattentionglobal",
        counts: { replies: 3, boosts: 18, favs: 74 }, boosted: false, favourited: false, bookmarked: false
      }
    ],
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
      { id: "f1-4", folder: "Fediverse 연구자", proto: "ap", format: "ActivityPub", interval: "5m", title: "Georgi Gerganov", handle: "@ggerganov@mastodon.social", url: "https://mastodon.social/@ggerganov", via: "acc1-ap", source: "계정 게시물 · GET /api/v1/accounts/:id/statuses", etag: "연결 계정 경유 · 폴링" }
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
    defaultFocus: "사본 계보 및 원문 비평 중심",
    accounts: [
      {
        id: "acc2-ap", proto: "ap", software: "Mastodon", serverVersion: "5.0.0", server: "scholar.example",
        handle: "@classics@scholar.example", displayName: "Claire · 고전 연구 📜",
        auth: "OAuth 2.0 (Authorization Code + PKCE)", scopes: ["read", "write:statuses", "write:media", "write:favourites", "write:bookmarks", "write:follows", "write:lists", "write:mutes", "write:blocks", "write:filters", "write:reports", "write:notifications", "write:conversations", "write:accounts"],
        canPublish: true, status: "ok", statusNote: "스트리밍 연결됨 (user)", charLimit: 500,
        timelines: [
          { id: "home", icon: "🏠", label: "홈", api: "GET /api/v1/timelines/home", stream: "user", collectable: true },
          { id: "notifications", icon: "🔔", label: "알림", api: "GET /api/v2/notifications", stream: "user:notification", kind: "notifications" },
          { id: "conversations", icon: "✉️", label: "개인 메시지", api: "GET /api/v1/conversations", stream: "direct" },
          { id: "explore", icon: "🧭", label: "탐색", api: "GET /api/v1/trends/statuses · /trends/tags · /trends/links", kind: "explore", feature: "trends" },
          { id: "local", icon: "🏘️", label: "로컬", api: "GET /api/v1/timelines/public?local=true", stream: "public:local" },
          { id: "federated", icon: "🌐", label: "연합", api: "GET /api/v1/timelines/public", stream: "public" },
          { id: "tag:Septuagint", icon: "#", label: "#Septuagint", api: "GET /api/v1/timelines/tag/Septuagint", stream: "hashtag", collectable: true },
          { id: "favourites", icon: "★", label: "좋아요", api: "GET /api/v1/favourites", kind: "favourites" },
          { id: "bookmarks", icon: "🔖", label: "북마크", api: "GET /api/v1/bookmarks", kind: "bookmarks" },
          { id: "scheduled", icon: "🗓️", label: "예약 게시물", api: "GET /api/v1/scheduled_statuses", kind: "scheduled", feature: "scheduled" },
          { id: "manage", icon: "🛡️", label: "필터 · 뮤트 · 차단", api: "GET /api/v2/filters · /api/v1/mutes · /api/v1/blocks · /api/v1/domain_blocks", kind: "manage" }
        ],
        lists: [], filters: [], mutes: [], blocks: [], domainBlocks: [], followedTags: ["Septuagint"], notifRequests: [],
        notifPolicy: { forNotFollowing: "accept", forNotFollowers: "accept", forNewAccounts: "accept", forPrivateMentions: "filter", forLimitedAccounts: "filter" },
        trends: { statuses: ["p2-1"], tags: [{ name: "Septuagint", history: [2, 3, 9], accounts: 12 }, { name: "P46", history: [0, 1, 6], accounts: 7 }], links: [] }
      }
    ],
    posts: [
      {
        id: "p2-1", accountId: "acc2-ap", timelines: ["home", "tag:Septuagint"], unread: true,
        author: "Patristics Colloquium", handle: "@patristics@scholar.example", time: "어제", timestamp: 1761619000,
        text: "P46 다중분광 촬영으로 히브리서 여백 주석이 새로 판독되었습니다. 구약 인용이 초기 알렉산드리아계 70인역 증언과 더 가깝게 정렬됩니다. #Septuagint #P46",
        hashtags: ["Septuagint", "P46"],
        card: { title: "Newly Digitized Papyrus Fragments of Pauline Epistles", description: "Multispectral imaging of P46 folio margins reveals scribal corrections aligning Old Testament citations with Alexandrian Septuagint witnesses.", url: "https://www.biblicalarchaeology.org/daily/pauline-papyrus-2026/" },
        url: "https://scholar.example/@patristics/11542700300", uri: "https://scholar.example/users/patristics/statuses/11542700300", remoteId: "115427003000001200",
        counts: { replies: 4, boosts: 21, favs: 66 }, boosted: false, favourited: false, bookmarked: false
      }
    ],
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
    defaultFocus: "영향 커널 버전 및 즉각 완화 조치 중심",
    accounts: [
      {
        id: "acc3-ap", proto: "ap", software: "Mastodon", serverVersion: "5.0.0", server: "infosec.example",
        handle: "@secwatch@infosec.example", displayName: "보안 모니터링 (읽기 전용)",
        auth: "OAuth 2.0 (Authorization Code + PKCE)", scopes: ["read"],
        canPublish: false, status: "ok", statusNote: "읽기 전용 scope — 발행 불가", charLimit: 500,
        timelines: [
          { id: "home", icon: "🏠", label: "홈", api: "GET /api/v1/timelines/home", stream: "user", collectable: true },
          { id: "notifications", icon: "🔔", label: "알림", api: "GET /api/v2/notifications", stream: "user:notification", kind: "notifications" },
          { id: "explore", icon: "🧭", label: "탐색", api: "GET /api/v1/trends/statuses · /trends/tags · /trends/links", kind: "explore", feature: "trends" },
          { id: "local", icon: "🏘️", label: "로컬", api: "GET /api/v1/timelines/public?local=true", stream: "public:local" },
          { id: "federated", icon: "🌐", label: "연합", api: "GET /api/v1/timelines/public", stream: "public" },
          { id: "tag:CVE", icon: "#", label: "#CVE", api: "GET /api/v1/timelines/tag/CVE", stream: "hashtag", collectable: true },
          { id: "favourites", icon: "★", label: "좋아요", api: "GET /api/v1/favourites", kind: "favourites" },
          { id: "bookmarks", icon: "🔖", label: "북마크", api: "GET /api/v1/bookmarks", kind: "bookmarks" },
          { id: "manage", icon: "🛡️", label: "필터 · 뮤트 · 차단", api: "GET /api/v2/filters · /api/v1/mutes · /api/v1/blocks · /api/v1/domain_blocks", kind: "manage" }
        ],
        lists: [], filters: [], mutes: [], blocks: [], domainBlocks: [], followedTags: ["CVE"], notifRequests: [],
        notifPolicy: { forNotFollowing: "accept", forNotFollowers: "accept", forNewAccounts: "accept", forPrivateMentions: "accept", forLimitedAccounts: "filter" },
        trends: { statuses: ["p3-1"], tags: [{ name: "CVE", history: [40, 52, 77], accounts: 140 }, { name: "LinuxKernel", history: [6, 9, 31], accounts: 48 }], links: [{ title: "oss-security: CVE-2026-39102", description: "Linux eBPF verifier scalar bounds tracking LPE.", url: "https://www.openwall.com/lists/oss-security/2026/10/28/1", provider: "openwall.com", author: "", shares: 210 }] }
      }
    ],
    posts: [
      {
        id: "p3-1", accountId: "acc3-ap", timelines: ["home", "tag:CVE"], unread: true,
        author: "oss-security relay", handle: "@oss_security@infosec.example", time: "09:05", timestamp: 1761634900,
        text: "CVE-2026-39102: Linux eBPF verifier scalar bounds tracking → local privilege escalation, 6.10–6.13.4. Mitigation: kernel.unprivileged_bpf_disabled=1 #CVE #LinuxKernel #eBPF",
        hashtags: ["CVE", "LinuxKernel", "eBPF"],
        card: { title: "oss-security: CVE-2026-39102 Linux Kernel eBPF Verifier", description: "Improper scalar register bounds propagation in kernel/bpf/verifier.c.", url: "https://www.openwall.com/lists/oss-security/2026/10/28/1" },
        url: "https://infosec.example/@oss_security/11542999001", uri: "https://infosec.example/users/oss_security/statuses/11542999001", remoteId: "115429990010000420",
        counts: { replies: 12, boosts: 230, favs: 310 }, boosted: false, favourited: false, bookmarked: false
      }
    ],
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
