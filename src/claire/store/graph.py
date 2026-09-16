"""지식 그래프 저장소 — 전역 엔티티 및 관계(엣지) 조작 추상화 (Phase 2).

SQLite 백엔드와 연결되어 횡단형 엣지(Edge)의 검증, 생성, 중복 제거, 갱신을
안전하고 일관성 있게 제공한다.
"""

from __future__ import annotations

import json
import sqlite3

from ..ontology.base import Entity, Relation
from ..ontology.registry import validate_relation
from . import db as dbm


class GraphStore:
    """지식 그래프 저장소 및 엣지 관리자."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        rel_type: str,
        *,
        confidence: float = 1.0,
        props: dict | None = None,
        sources: list[str] | None = None,
        provisional: bool = False,
    ) -> Relation | None:
        """두 엔티티 사이에 엣지를 안전하게 추가하거나 갱신한다.

        - 자기 자신으로의 루프(source_id == target_id)는 거부한다.
        - 존재하지 않는 엔티티 ID는 거부한다.
        - 온톨로지 도메인/레인지 제약을 검증한다.
        - 이미 존재하는 동일 (type, source, target) 엣지는 sources와 confidence를 병합한다.
        """
        if not source_id or not target_id or source_id == target_id:
            return None

        src = dbm.get_entity(self.conn, source_id)
        tgt = dbm.get_entity(self.conn, target_id)
        if src is None or tgt is None:
            return None

        vr = validate_relation(rel_type, src.type, tgt.type)
        if not vr.ok and not provisional:
            return None

        actual_provisional = vr.provisional or provisional

        # 기존 동일 관계 확인
        existing = self.get_edge(source_id, target_id, rel_type)
        if existing is not None:
            changed = False
            new_sources = list(existing.sources)
            if sources:
                for s in sources:
                    if s and s not in new_sources:
                        new_sources.append(s)
                        changed = True
            new_conf = max(existing.confidence, confidence)
            if new_conf != existing.confidence:
                changed = True
            merged_props = dict(existing.props)
            if props:
                for k, v in props.items():
                    if merged_props.get(k) != v:
                        merged_props[k] = v
                        changed = True

            if changed:
                existing.sources = new_sources
                existing.confidence = new_conf
                existing.props = merged_props
                self.conn.execute(
                    "UPDATE relations SET confidence=?, sources=?, props=? WHERE id=?",
                    (
                        existing.confidence,
                        json.dumps(existing.sources),
                        json.dumps(existing.props),
                        existing.id,
                    ),
                )
                self.conn.commit()
            return existing

        # 신규 생성
        rel = Relation(
            type=rel_type,
            source_id=source_id,
            target_id=target_id,
            props=props or {},
            sources=sources or [],
            confidence=confidence,
            provisional=actual_provisional,
        )
        dbm.upsert_relation(self.conn, rel)
        return rel

    def has_edge(
        self,
        source_id: str,
        target_id: str,
        rel_type: str | None = None,
        bidirectional: bool = True,
    ) -> bool:
        """두 노드 사이에 엣지가 이미 존재하는지 여부 확인."""
        if not source_id or not target_id:
            return False

        if bidirectional:
            if rel_type:
                row = self.conn.execute(
                    """SELECT 1 FROM relations
                    WHERE type=? AND ((source_id=? AND target_id=?) OR (source_id=? AND target_id=?))
                    LIMIT 1""",
                    (rel_type, source_id, target_id, target_id, source_id),
                ).fetchone()
            else:
                row = self.conn.execute(
                    """SELECT 1 FROM relations
                    WHERE (source_id=? AND target_id=?) OR (source_id=? AND target_id=?)
                    LIMIT 1""",
                    (source_id, target_id, target_id, source_id),
                ).fetchone()
        else:
            if rel_type:
                row = self.conn.execute(
                    """SELECT 1 FROM relations
                    WHERE type=? AND source_id=? AND target_id=?
                    LIMIT 1""",
                    (rel_type, source_id, target_id),
                ).fetchone()
            else:
                row = self.conn.execute(
                    """SELECT 1 FROM relations
                    WHERE source_id=? AND target_id=?
                    LIMIT 1""",
                    (source_id, target_id),
                ).fetchone()

        return row is not None

    def get_edge(
        self,
        source_id: str,
        target_id: str,
        rel_type: str | None = None,
    ) -> Relation | None:
        """특정 방향과 타입의 엣지를 조회."""
        if rel_type:
            row = self.conn.execute(
                "SELECT * FROM relations WHERE type=? AND source_id=? AND target_id=? LIMIT 1",
                (rel_type, source_id, target_id),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM relations WHERE source_id=? AND target_id=? LIMIT 1",
                (source_id, target_id),
            ).fetchone()

        if row is None:
            return None
        return dbm._row_to_relation(row)

    def neighbors(self, entity_id: str) -> list[Relation]:
        """특정 노드와 연결된 모든 인접 엣지 조회."""
        return dbm.neighbors(self.conn, entity_id)

    def all_edges(self) -> list[Relation]:
        """전체 엣지 목록 조회."""
        return dbm.all_relations(self.conn)

    def all_nodes(self) -> list[Entity]:
        """전체 노드 목록 조회."""
        return dbm.all_entities(self.conn)
