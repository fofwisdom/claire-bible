"""Unit and E2E tests for cross-lingual entity resolution, Unicode alias lookup, and graph rollback recovery."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

from claire.config import Settings
from claire.extract.decision import ResolutionDecision, attach_resolution_meta
from claire.extract.prompts import judge_same_entity_prompt
from claire.extract.provider import ExtractedEntity, ExtractedRelation, ExtractionResult, MergeCandidate, MockProvider
from claire.extract.resolver import resolve_or_create
from claire.ingest.service import IngestService
from claire.ontology.base import Document, Entity
from claire.store import db as dbm
from claire.store.vectors import VectorStore


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def test_find_entities_by_name_or_alias_unicode_and_legacy_escaped():
    """Verify SQLite I/O lookup for Korean aliases stored as UTF-8 JSON and legacy ASCII-escaped JSON."""
    conn = _memory_db()

    ent_utf8 = Entity(type="Person", name="Marcus", aliases=["마커스"], observations=["Investigator"], sources=["doc_seed"])
    dbm.upsert_entity(conn, ent_utf8)

    raw_row = conn.execute("SELECT aliases FROM entities WHERE id=?", (ent_utf8.id,)).fetchone()
    assert "마커스" in raw_row["aliases"]
    assert "\\u" not in raw_row["aliases"]

    matched_utf8 = dbm.find_entities_by_name_or_alias(conn, "마커스")
    assert [e.id for e in matched_utf8] == [ent_utf8.id]

    ent_legacy = Entity(type="Org", name="Laplace Scientific Computing Center", aliases=["라플라스"], observations=["Research center"], sources=["doc_seed"])
    dbm.upsert_entity(conn, ent_legacy)
    legacy_aliases_json = json.dumps(["라플라스", "Laplace"], ensure_ascii=True)
    assert "\\ub77c\\ud50c\\ub77c\\uc2a4" in legacy_aliases_json
    conn.execute("UPDATE entities SET aliases=? WHERE id=?", (legacy_aliases_json, ent_legacy.id))
    conn.commit()

    matched_legacy = dbm.find_entities_by_name_or_alias(conn, "라플라스")
    assert [e.id for e in matched_legacy] == [ent_legacy.id]


def test_resolver_passes_new_aliases_to_judge_and_prompt():
    """Verify module interface between resolve_or_create, MergeCandidate, judge_same_entity_prompt, and MockProvider."""
    conn = _memory_db()
    vstore = VectorStore(conn, "brute")

    cand = Entity(type="Org", name="Laplace Scientific Computing Center", aliases=[], observations=["Scientific computing center"], sources=["doc_1"])
    dbm.upsert_entity(conn, cand)
    vstore.put(cand.id, [1.0, 0.0, 0.0, 0.0], "test")

    captured_calls: list[tuple[str, str, list[str], str, list[str]]] = []
    mock_provider = MockProvider()

    def judge_fn(nm: str, et: str, obs: list[str], candidate: Entity, aliases: list[str] | None = None) -> bool:
        alias_list = list(aliases or [])
        captured_calls.append((nm, et, obs, candidate.name, alias_list))
        mc = MergeCandidate(new_name=nm, new_type=et, new_aliases=alias_list, new_observations=obs, cand_name=candidate.name, cand_type=candidate.type, cand_aliases=candidate.aliases, cand_observations=candidate.observations)
        prompt_text = judge_same_entity_prompt(mc)
        assert "A: name='라플라스' type='Org' aliases=['Laplace', '라플라스 연구 센터']" in prompt_text
        return any(a.lower() in candidate.name.lower() for a in alias_list)

    resolved_ent, created = resolve_or_create(conn, vstore, name="라플라스", etype="Org", aliases=["Laplace", "라플라스 연구 센터"], observations=["라플라스 계측 설비"], document_id="doc_2", embed_fn=lambda: [0.82, 0.57, 0.0, 0.0], judge_fn=judge_fn)

    assert not created
    assert resolved_ent.id == cand.id
    assert len(captured_calls) == 1
    assert captured_calls[0][4] == ["Laplace", "라플라스 연구 센터"]
    assert "라플라스" in resolved_ent.aliases
    assert "Laplace" in resolved_ent.aliases

    mc_alias_overlap = MergeCandidate(new_name="라플라스", new_type="Org", new_aliases=["Laplace"], cand_name="Laplace Scientific Computing Center", cand_type="Org", cand_aliases=["Laplace"])
    assert mock_provider.judge_same_entity(mc_alias_overlap) is True


class _StoryResolutionProvider(MockProvider):
    """Test provider that returns cross-lingual story entities and deterministic embeddings."""

    def extract(self, doc: Document, ontology_block: str = "", **kwargs) -> ExtractionResult:
        result = ExtractionResult(
            summary="야외 실험 기록 요약",
            key_claims=["울리히와 마커스가 라플라스 야외 실험에 참여했다."],
            entities=[
                ExtractedEntity(name="마커스", type="Person", aliases=["Marcus"], observations=["야외 실험 기록에 등장하는 인물"]),
                ExtractedEntity(name="라플라스", type="Org", aliases=["Laplace", "라플라스 연구 센터"], observations=["야외 실험을 주관하는 연구 기관"]),
                ExtractedEntity(name="울리히", type="Person", aliases=["Ulrich"], observations=["야외 실험을 지휘하는 연구원"]),
            ],
            relations=[
                ExtractedRelation(source="울리히", target="라플라스", type="affiliated_with", why="울리히는 라플라스 소속 연구원이다"),
            ],
            model="mock",
            prompt_version="mock-1",
        )
        result.raw_response = result.model_dump_json(exclude={"raw_response", "model", "prompt_version"})
        return result

    def embed(self, text: str, *, task_type: str | None = None, title: str | None = None, **kwargs) -> list[float]:
        if "라플라스" in text or "Laplace" in text:
            return [0.82, 0.57, 0.0, 0.0]
        if "마커스" in text or "Marcus" in text:
            return [0.0, 0.82, 0.57, 0.0]
        return [0.0, 0.0, 0.82, 0.57]


def test_e2e_uttu_story_entity_resolution_purge_reingest_and_regenerate_recovery(tmp_path: Path):
    """Verify E2E recovery for purge + re-ingest and graph regeneration on a polluted document."""
    db_file = tmp_path / "claire_e2e_resolution.db"
    vault_dir = tmp_path / "vault"
    data_dir = tmp_path / "data"
    vault_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)

    settings = Settings(db_path=str(db_file), vault_path=str(vault_dir), data_dir=str(data_dir), provider="mock", data_lifecycle="purgeable", allow_purge=True)

    story_url = "https://uttu.merui.net/story/other/psp/kr/?part=1"
    dialogues = "".join(f"<b>울리히:</b> 라플라스 제{i}구역 계측 결과를 마커스에게 전달하라.<br>" for i in range(15))
    mock_chapters_data = {
        "chapters": [{"number": "1", "textPartNumber": "1", "title": "야외 실험", "content": f'<div class="location-label">라플라스 실험장</div>\n{dialogues}'}],
        "frontmatter": {"title": "Outdoor Experiment", "localeTitle": "야외 실험"},
        "type": "other",
        "name": "psp",
    }
    mock_page_html = f'<!DOCTYPE html><html><head><title>Outdoor Experiment | Reverse: 1999</title></head><body><script type="text/plain" id="chaptersData">{json.dumps(mock_chapters_data)}</script></body></html>'

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = mock_page_html
    mock_resp.url = story_url

    conn = dbm.connect(str(db_file))
    dbm.init_db(conn)
    vstore = VectorStore(conn, "brute")

    seed_doc = Document(id="doc_seed", url="https://uttu.merui.net/story/seed", canonical_url="https://uttu.merui.net/story/seed", title="Seed Story", raw_text="Seed story text", source_type="web", content_hash="seed_hash")
    dbm.insert_document(conn, seed_doc)

    marcus = Entity(type="Person", name="Marcus", aliases=["마커스"], observations=["Foundation investigator"], sources=["doc_seed"])
    dbm.upsert_entity(conn, marcus)
    conn.execute("UPDATE entities SET aliases=? WHERE id=?", (json.dumps(["마커스"], ensure_ascii=True), marcus.id))
    vstore.put(marcus.id, [0.0, 1.0, 0.0, 0.0], "mock")

    laplace = Entity(type="Org", name="Laplace Scientific Computing Center", aliases=["Laplace"], observations=["Main scientific computing center"], sources=["doc_seed"])
    dbm.upsert_entity(conn, laplace)
    vstore.put(laplace.id, [1.0, 0.0, 0.0, 0.0], "mock")

    ulrich = Entity(type="Person", name="울리히", aliases=[], observations=["기존 울리히 설명"], sources=["doc_seed"])
    dbm.upsert_entity(conn, ulrich)
    vstore.put(ulrich.id, [0.0, 0.0, 1.0, 0.0], "mock")

    polluted_doc_id = "doc_polluted_psp"
    polluted_doc = Document(id=polluted_doc_id, url=story_url, canonical_url=story_url, title="Polluted Story", raw_text="Polluted story text", source_type="web", content_hash="polluted_hash")

    ulrich.observations.append("오염된 스토리 관찰")
    ulrich.aliases.append("오염별칭")
    ulrich.sources.append(polluted_doc_id)
    dbm.upsert_entity(conn, ulrich)

    merge_dec = ResolutionDecision(
        entity="울리히",
        stage="exact_match",
        decision="MERGE",
        candidate="울리히",
        score=1.0,
        reason="Exact match",
        target_entity_id=ulrich.id,
        rollback_payload={"added_aliases": ["오염별칭"], "added_observations": ["오염된 스토리 관찰"], "added_source": polluted_doc_id, "prev_type": "Person"},
    )
    attach_resolution_meta(polluted_doc, [merge_dec])
    dbm.insert_document(conn, polluted_doc)

    dup_marcus = Entity(type="Person", name="마커스", aliases=[], observations=["중복 생성된 마커스"], sources=[polluted_doc_id])
    dbm.upsert_entity(conn, dup_marcus)
    dup_laplace = Entity(type="Org", name="라플라스", aliases=[], observations=["중복 생성된 라플라스"], sources=[polluted_doc_id])
    dbm.upsert_entity(conn, dup_laplace)
    conn.commit()

    assert dbm.counts(conn)["entities"] == 5

    # Case A: Purge without tombstone and re-ingest the story URL
    purge_stats = dbm.purge_document_cascade(conn, data_dir, vault_dir, [polluted_doc_id], dry_run=False, tombstone=False)
    assert purge_stats["deleted_documents"] == 1
    assert purge_stats["graph_rollback"]["rolled_back_merges"] == 1

    ulrich_after_purge = dbm.get_entity(conn, ulrich.id)
    assert ulrich_after_purge is not None
    assert ulrich_after_purge.observations == ["기존 울리히 설명"]
    assert "오염별칭" not in ulrich_after_purge.aliases
    assert polluted_doc_id not in ulrich_after_purge.sources
    assert dbm.get_entity(conn, dup_marcus.id) is None
    assert dbm.get_entity(conn, dup_laplace.id) is None
    assert dbm.counts(conn)["entities"] == 3
    conn.close()

    with patch("claire.ingest.fetchers.http.SafeHttpClient.get", return_value=mock_resp):
        service = IngestService(settings=settings)
        service.provider = _StoryResolutionProvider()
        report = service.ingest(story_url, source="e2e-recovery-test")
        assert report.error is None
        assert report.document_id is not None
        new_doc_id = report.document_id

    conn = dbm.connect(str(db_file))
    assert dbm.counts(conn)["entities"] == 3
    marcus_reingested = dbm.get_entity(conn, marcus.id)
    laplace_reingested = dbm.get_entity(conn, laplace.id)
    ulrich_reingested = dbm.get_entity(conn, ulrich.id)
    assert marcus_reingested is not None and new_doc_id in marcus_reingested.sources
    assert laplace_reingested is not None and new_doc_id in laplace_reingested.sources
    assert "라플라스" in laplace_reingested.aliases
    assert ulrich_reingested is not None and new_doc_id in ulrich_reingested.sources
    assert "Ulrich" in ulrich_reingested.aliases

    # Case B: Pollute the graph with a stale duplicate entity on the active document and run regenerate_components(graph=True)
    stale_dup = Entity(type="Org", name="라플라스", aliases=[], observations=["재생성 전 오염된 중복 노드"], sources=[new_doc_id])
    dbm.upsert_entity(conn, stale_dup)
    conn.commit()
    assert dbm.counts(conn)["entities"] == 4

    regen_res = service.regenerate_components(target=new_doc_id, summary=False, detail=False, graph=True, force=True)
    conn.close()
    assert regen_res["count"] == 1
    assert regen_res["targets"][0]["entities_created"] == 0
    assert regen_res["targets"][0]["entities_linked"] == 3

    conn = dbm.connect(str(db_file))
    try:
        assert dbm.get_entity(conn, stale_dup.id) is None
        assert dbm.counts(conn)["entities"] == 3
        laplace_after_regen = dbm.get_entity(conn, laplace.id)
        assert laplace_after_regen is not None
        assert new_doc_id in laplace_after_regen.sources
        assert "라플라스" in laplace_after_regen.aliases
    finally:
        conn.close()
