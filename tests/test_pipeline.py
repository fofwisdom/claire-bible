"""M1/M2 파이프라인 테스트 — 네트워크/토큰 없이 mock provider + 주입 fetch 로 검증.

핵심 검증: dedup, 엔티티 적재, **기존 그래프와의 연결(머지)**, 관계 검증/적재,
provisional/proposal, vault export.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from claire.ontology.base import Document
from claire.ingest.pipeline import ingest
from claire.ingest.normalize import canonicalize_url, content_hash
from claire.ingest.router import classify
from claire.extract.provider import (
    MockProvider, ExtractionResult, ExtractedEntity, ExtractedRelation,
)
from claire.store import db as dbm
from claire.store.vectors import VectorStore


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


# --- normalize / router ---

def test_canonicalize_url():
    a = canonicalize_url("https://www.Example.com/Path/?utm_source=x&id=2#frag")
    assert a == "https://example.com/Path?id=2"
    assert canonicalize_url("https://x.com/a/") == "https://x.com/a"


def test_canonicalize_converges_equivalent_forms():
    """같은 자료에 도달하는 여러 URL 형태가 하나의 canonical 로 수렴(중복 방지 핵심)."""
    canon = "https://example.com/article"
    # 모바일/amp prefix, 기본포트, http/https, www, index 파일, 끝슬래시, 추적파라미터
    variants = [
        "https://www.example.com/article",
        "https://m.example.com/article/",
        "https://amp.example.com/article",
        "http://example.com:80/article" if False else "https://example.com:443/article",
        "https://example.com/article/index.html",
        "https://example.com/article?fbclid=abc&gclid=xyz",
        "https://example.com/article?utm_id=1&mc_cid=2",
    ]
    for v in variants:
        assert canonicalize_url(v) == canon, v
    # http 는 scheme 이 보존되므로 https 와 다른 키(서버 redirect 가 effective_url 로 수렴시킴)
    assert canonicalize_url("http://example.com/article") == "http://example.com/article"
    # 서로 다른 글은 합쳐지지 않는다(false-positive 방지).
    assert canonicalize_url("https://example.com/other") != canon


def test_canonicalize_arxiv_versions():
    """arxiv 버전/형식 변형 → 정본 /abs/<id> 로 수렴(중복 적재 방지)."""
    base = "https://arxiv.org/abs/2606.17551"
    for v in [
        "https://arxiv.org/abs/2606.17551v1",
        "https://arxiv.org/abs/2606.17551v3",
        "https://arxiv.org/pdf/2606.17551",
        "https://arxiv.org/pdf/2606.17551v2",
        "https://arxiv.org/pdf/2606.17551v2.pdf",
        "https://www.arxiv.org/abs/2606.17551v1",
    ]:
        assert canonicalize_url(v) == base, v
    # 구형 식별자(archive/number)도 버전만 제거.
    assert canonicalize_url("https://arxiv.org/abs/hep-th/9901001v2") == \
        "https://arxiv.org/abs/hep-th/9901001"
    # 다른 논문은 그대로 구분.
    assert canonicalize_url("https://arxiv.org/abs/2606.99999") != base


def test_content_hash_stable():
    assert content_hash("a  b") == content_hash(" a b ")
    assert content_hash("a") != content_hash("b")


def test_router_classify():
    assert classify("https://youtu.be/abc") == "youtube"
    assert classify("https://x.com/u/status/1") == "xcom"
    assert classify("https://share.google/zzz") == "redirect"
    assert classify("https://geeknews.io/post") == "web"
    assert classify("그냥 키워드") == "text"


# --- pipeline with mock provider + injected fetch ---

def _fetch_doc(doc: Document):
    return lambda payload: doc


def test_ingest_emits_progress_events(tmp_path: Path):
    """웹 적재(ingest-stream)용 진행 메시지가 provider 진행 콜백으로 흐른다(fetch→추출 단계).

    콜백 미설정 시 emit_progress 는 no-op(CLI/텔레그램 경로 무영향). 여기선 콜백을 걸어
    파이프라인이 단계 메시지를 흘리는지(스트림 계약) 확인한다."""
    from claire.extract.provider import set_progress_callback

    conn = _db()
    vstore = VectorStore(conn, "brute")
    doc = Document(url="https://example.com/post", title="Post",
                   raw_text="A knowledge graph from a corpus. " * 10,
                   source_type="web", content_hash="h-prog")
    msgs: list[str] = []
    set_progress_callback(msgs.append)
    try:
        rep = ingest("x", conn=conn, provider=MockProvider(), vstore=vstore,
                     source="web", fetch_fn=_fetch_doc(doc))
    finally:
        set_progress_callback(None)
    assert rep.document_id and rep.error is None
    assert any("원문 가져오는 중" in m for m in msgs), msgs
    assert any("구조화 추출" in m for m in msgs), msgs
    # prefetched 경로(1홉 확장)는 재fetch 안 하므로 "원문 가져오는 중" 을 내지 않는다.
    msgs2: list[str] = []
    conn2 = _db()
    set_progress_callback(msgs2.append)
    try:
        ingest("x", conn=conn2, provider=MockProvider(), vstore=VectorStore(conn2, "brute"),
               source="onehop:d1", fetch_fn=_fetch_doc(doc), prefetched=doc)
    finally:
        set_progress_callback(None)
    assert not any("원문 가져오는 중" in m for m in msgs2), msgs2


def test_ingest_text_creates_entities_and_vault(tmp_path: Path):
    conn = _db()
    vstore = VectorStore(conn, "brute")
    doc = Document(
        url="https://github.com/safishamsi/graphify",
        title="Graphify",
        raw_text="A knowledge graph generator from any corpus.",
        source_type="web",
        content_hash="h-graphify",
    )
    rep = ingest("x", conn=conn, provider=MockProvider(), vstore=vstore,
                 vault_dir=tmp_path, fetch_fn=_fetch_doc(doc))
    assert rep.error is None
    assert rep.entities_created >= 1
    assert rep.relations_added >= 1  # repo -authored_by-> org
    # vault 파일이 생성되고 generated 배너 + wikilink 포함
    files = list(tmp_path.glob("*.md"))
    assert files
    txt = "\n".join(f.read_text() for f in files)
    assert "generated by claire_bible" in txt
    assert "[[" in txt


def test_dedup_second_ingest_is_skipped():
    conn = _db()
    vstore = VectorStore(conn, "brute")
    doc = Document(title="Dup", raw_text="same", source_type="text", content_hash="dup1")
    p = MockProvider()
    r1 = ingest("x", conn=conn, provider=p, vstore=vstore, fetch_fn=_fetch_doc(doc))
    r2 = ingest("x", conn=conn, provider=p, vstore=vstore, fetch_fn=_fetch_doc(doc))
    assert not r1.duplicate and r2.duplicate
    assert dbm.counts(conn)["documents"] == 1


def test_same_canonical_url_updates_in_place(tmp_path: Path):
    """같은 canonical_url + 다른 content_hash → 중복 노드도 skip도 아닌 in-place 갱신."""
    conn = _db()
    vstore = VectorStore(conn, "brute")
    d1 = Document(url="https://github.com/a/b", canonical_url="https://github.com/a/b",
                  title="v1", raw_text="first version about Foo",
                  source_type="web", content_hash="h1")
    r1 = ingest("x", conn=conn, provider=MockProvider(), vstore=vstore,
                vault_dir=tmp_path, fetch_fn=_fetch_doc(d1))
    assert r1.error is None and not r1.duplicate and not r1.updated
    doc_id = r1.document_id

    d2 = Document(url="https://github.com/a/b", canonical_url="https://github.com/a/b",
                  title="v2", raw_text="updated version about Foo and Bar",
                  source_type="web", content_hash="h2")
    r2 = ingest("x", conn=conn, provider=MockProvider(), vstore=vstore,
                vault_dir=tmp_path, fetch_fn=_fetch_doc(d2))
    assert r2.updated is True and not r2.duplicate
    assert r2.document_id == doc_id                  # 같은 문서 in-place
    assert dbm.counts(conn)["documents"] == 1         # 중복 노드 안 생김
    row = dbm.get_document_row(conn, doc_id)
    assert row["content_hash"] == "h2" and row["title"] == "v2"


def _long(seed: str, n: int = 120) -> str:
    """min_len(500) 을 넘는 충분히 긴 본문 — 근사중복 게이트 대상이 되게."""
    return " ".join(f"{seed}{i}" for i in range(n))


def test_near_duplicate_is_skipped():
    """다른 canonical·다른 content_hash 인데 본문이 ~동일하면 근사중복으로 skip(노드 안 생김).

    실측 패턴 재현: arxiv `/abs/x` 와 `/abs/xv1` 처럼 URL 만 다르고 본문이 거의 같은 경우.
    """
    conn = _db()
    vstore = VectorStore(conn, "brute")
    body = "deep learning model trains on tokens " + _long("w")
    d1 = Document(url="https://arxiv.org/abs/2606.17551",
                  canonical_url="https://arxiv.org/abs/2606.17551",
                  title="Reversal Q-Learning", raw_text=body,
                  source_type="web", content_hash="ha")
    # v1: URL 도 다르고 본문도 한 단어 추가(content_hash 다름) → ①② 게이트 비껴감.
    d2 = Document(url="https://arxiv.org/abs/2606.17551v1",
                  canonical_url="https://arxiv.org/abs/2606.17551v1",
                  title="Reversal Q-Learning", raw_text=body + " extra",
                  source_type="web", content_hash="hb")
    r1 = ingest("x", conn=conn, provider=MockProvider(), vstore=vstore, fetch_fn=_fetch_doc(d1))
    r2 = ingest("x", conn=conn, provider=MockProvider(), vstore=vstore, fetch_fn=_fetch_doc(d2))
    assert not r1.duplicate and r2.duplicate          # 두번째는 근사중복
    assert r2.document_id == r1.document_id
    assert dbm.counts(conn)["documents"] == 1          # 중복 노드 안 생김


def test_near_dup_gate_ignores_short_and_partial():
    """짧은 글·partial(x.com 트윗 등)은 근사중복 게이트 대상에서 제외 → 오병합 방지."""
    conn = _db()
    vstore = VectorStore(conn, "brute")
    # 짧은 두 트윗: 공통 토큰이 많아도 합쳐지면 안 됨.
    t1 = Document(url="https://x.com/a/1", canonical_url="https://x.com/a/1",
                  title="x.com post", raw_text="https://x.com/a/status/1 great AI thread",
                  source_type="xcom", content_hash="t1", partial=True)
    t2 = Document(url="https://x.com/b/2", canonical_url="https://x.com/b/2",
                  title="x.com post", raw_text="https://x.com/b/status/2 great AI thread",
                  source_type="xcom", content_hash="t2", partial=True)
    ingest("x", conn=conn, provider=MockProvider(), vstore=vstore, fetch_fn=_fetch_doc(t1))
    r2 = ingest("x", conn=conn, provider=MockProvider(), vstore=vstore, fetch_fn=_fetch_doc(t2))
    assert not r2.duplicate
    assert dbm.counts(conn)["documents"] == 2


def test_distinct_long_docs_not_merged():
    """충분히 길지만 내용이 다른 두 글은 근사중복으로 합쳐지지 않는다(false-positive 방지)."""
    conn = _db()
    vstore = VectorStore(conn, "brute")
    d1 = Document(url="https://s/1", canonical_url="https://s/1", title="A",
                  raw_text="apple banana " + _long("alpha"),
                  source_type="web", content_hash="ca")
    d2 = Document(url="https://s/2", canonical_url="https://s/2", title="B",
                  raw_text="quantum chromodynamics " + _long("beta"),
                  source_type="web", content_hash="cb")
    ingest("x", conn=conn, provider=MockProvider(), vstore=vstore, fetch_fn=_fetch_doc(d1))
    r2 = ingest("x", conn=conn, provider=MockProvider(), vstore=vstore, fetch_fn=_fetch_doc(d2))
    assert not r2.duplicate
    assert dbm.counts(conn)["documents"] == 2


def test_merge_documents_repoints_and_deletes():
    """근사중복 병합: loser 를 가리키는 모든 참조를 keeper 로 옮기고 loser 문서 삭제."""
    from claire.ontology.base import Entity, Relation

    conn = _db()
    A = Document(id="doc_keep", url="https://arxiv.org/abs/1", title="P",
                 raw_text="x" * 600, source_type="web", content_hash="hA")
    B = Document(id="doc_lose", url="https://arxiv.org/abs/1v1", title="P",
                 raw_text="x" * 600, source_type="web", content_hash="hB")
    dbm.insert_document(conn, A)
    dbm.insert_document(conn, B)
    # 엔티티 sources 가 양쪽을 가리킴(중복) → keeper 로 합쳐 dedupe 되어야.
    e = Entity(id="ent_1", type="Article", name="Paper", sources=["doc_keep", "doc_lose"])
    dbm.upsert_entity(conn, e)
    e2 = Entity(id="ent_2", type="Org", name="OnlyB", sources=["doc_lose"])
    dbm.upsert_entity(conn, e2)
    dbm.upsert_relation(conn, Relation(id="rel_1", type="authored_by",
                                       source_id="ent_1", target_id="ent_2",
                                       sources=["doc_lose"]))
    ib = dbm.log_inbox(conn, source="t", payload="p", kind="url")
    dbm.update_inbox(conn, ib, status="done", document_id="doc_lose")

    res = dbm.merge_documents(conn, "doc_keep", ["doc_lose"])
    assert res["deleted"] == 1
    assert dbm.get_document_row(conn, "doc_lose") is None
    assert dbm.get_document_row(conn, "doc_keep") is not None
    assert dbm.get_entity(conn, "ent_1").sources == ["doc_keep"]   # dedupe
    assert dbm.get_entity(conn, "ent_2").sources == ["doc_keep"]   # repoint
    rel = conn.execute("SELECT sources FROM relations WHERE id='rel_1'").fetchone()
    assert "doc_keep" in rel["sources"] and "doc_lose" not in rel["sources"]
    row = conn.execute("SELECT document_id FROM raw_inbox WHERE id=?", (ib,)).fetchone()
    assert row["document_id"] == "doc_keep"


def test_minhash_estimate_and_signature():
    from claire.ingest.normalize import minhash_estimate, minhash_signature

    a = minhash_signature("the quick brown fox jumps over the lazy dog repeatedly today")
    b = minhash_signature("the quick brown fox jumps over the lazy dog repeatedly today now")
    c = minhash_signature("completely unrelated text about cooking pasta and tomato sauce here")
    assert a and b and c
    assert minhash_estimate(a, b) >= 0.8     # 거의 같은 글
    assert minhash_estimate(a, c) < 0.3      # 다른 글
    assert minhash_signature("a") is not None  # 짧아도 토큰셋 폴백
    assert minhash_signature("   ") is None    # 토큰 없으면 None


def test_different_canonical_url_creates_new():
    conn = _db()
    vstore = VectorStore(conn, "brute")
    d1 = Document(url="https://x/1", canonical_url="https://x/1", title="A",
                  raw_text="aaa", source_type="web", content_hash="ha")
    d2 = Document(url="https://x/2", canonical_url="https://x/2", title="B",
                  raw_text="bbb", source_type="web", content_hash="hb")
    ingest("x", conn=conn, provider=MockProvider(), vstore=vstore, fetch_fn=_fetch_doc(d1))
    r2 = ingest("x", conn=conn, provider=MockProvider(), vstore=vstore,
                fetch_fn=_fetch_doc(d2))
    assert not r2.updated and not r2.duplicate
    assert dbm.counts(conn)["documents"] == 2


def test_existing_node_gets_linked_across_documents():
    """핵심 가치: 같은 엔티티가 두 자료에 나오면 하나로 수렴(머지)된다."""
    conn = _db()
    vstore = VectorStore(conn, "brute")

    class P(MockProvider):
        def __init__(self, ents, rels=None):
            self._ents = ents
            self._rels = rels or []

        def extract(self, doc, ontology_block=None):
            return ExtractionResult(
                summary="s",
                entities=[ExtractedEntity(**e) for e in self._ents],
                relations=[ExtractedRelation(**r) for r in self._rels],
            )

    d1 = Document(title="Doc1", raw_text="t1", source_type="text", content_hash="c1")
    d2 = Document(title="Doc2", raw_text="t2", source_type="text", content_hash="c2")

    p1 = P([{"name": "Claude Code", "type": "Tool", "observations": ["from doc1"]}])
    r1 = ingest("x", conn=conn, provider=p1, vstore=vstore, fetch_fn=_fetch_doc(d1))
    assert r1.entities_created == 1

    # 두번째 문서에서 같은 이름(대소문자 다름) → 기존 노드에 연결
    p2 = P([{"name": "claude code", "type": "Tool", "observations": ["from doc2"]}])
    r2 = ingest("x", conn=conn, provider=p2, vstore=vstore, fetch_fn=_fetch_doc(d2))
    assert r2.entities_linked == 1
    assert r2.entities_created == 0
    assert dbm.counts(conn)["entities"] == 1  # 수렴
    ent = dbm.all_entities(conn)[0]
    assert "from doc1" in ent.observations and "from doc2" in ent.observations
    assert len(ent.sources) == 2


def test_relation_domain_range_rejection_and_proposal():
    conn = _db()
    vstore = VectorStore(conn, "brute")

    class P(MockProvider):
        def extract(self, doc, ontology_block=None):
            return ExtractionResult(
                summary="s",
                entities=[
                    ExtractedEntity(name="RepoX", type="Repo"),
                    ExtractedEntity(name="ToolY", type="Tool"),
                    ExtractedEntity(name="ThingZ", type="Gadget",
                                    proposed_type="gadget"),
                ],
                relations=[
                    # authored_by 의 range 는 Person/Org. Tool 타겟 → 거부
                    ExtractedRelation(source="RepoX", target="ToolY", type="authored_by"),
                    # provisional 관계 타입
                    ExtractedRelation(source="RepoX", target="ToolY",
                                      type="forks_from", proposed_type="forks_from"),
                ],
            )

    doc = Document(title="D", raw_text="t", source_type="text", content_hash="ck")
    rep = ingest("x", conn=conn, provider=P(), vstore=vstore, fetch_fn=_fetch_doc(doc))
    assert rep.relations_rejected >= 1   # authored_by 거부
    assert rep.relations_added >= 1      # forks_from(provisional) 적재
    assert rep.proposals >= 1            # gadget + forks_from 제안 기록
    # provisional 엔티티 플래그
    gz = [e for e in dbm.all_entities(conn) if e.name == "ThingZ"][0]
    assert gz.provisional


def test_fetch_error_reported():
    conn = _db()
    vstore = VectorStore(conn, "brute")

    def boom(_payload):
        raise RuntimeError("network down")

    rep = ingest("x", conn=conn, provider=MockProvider(), vstore=vstore, fetch_fn=boom)
    assert rep.error and "network down" in rep.error
    assert dbm.counts(conn)["documents"] == 0
