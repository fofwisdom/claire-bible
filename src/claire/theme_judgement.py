"""Subject judgement and document match (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 8.2, 8.3, 8.5; unit CT-5).

A theme is a collection of knowledge bases with the same subject and the same knowledge type. The judgement uses only data that exists and makes no LLM call:

1. The signature of a knowledge base: its 256 entities with the highest weight (the count of visible source documents), with the keys ``(type, norm_name)`` of the entities and of their aliases.
2. The weight of a key across the server (idf), for each knowledge type.
3. The similarity of two knowledge bases: the weighted Jaccard of their tf-idf vectors, with the cosine of their entity embedding centroids as a second signal when the two use the same embedding model.
4. Average-linkage clustering of the shared knowledge bases of one type. Public personal knowledge bases attach to ``real`` themes; they never make a theme.
5. The document match: the theme whose signature has the largest idf-weighted part of the keys of a document.

Changes of the method of the design, from the checks of this unit (CT25). Each one is a proposal for the owner, because the development data has only one knowledge base with documents and cannot calibrate the numbers:
- The design gives ``idf = ln(N / df)``. A key that each signature has then gets 0; with two knowledge bases of one type, their common keys count for nothing and the similarity is always 0. This module uses ``idf = ln(1 + N / df)``, which is never 0 and keeps the order.
- The tf-idf vectors are normalized to the sum 1 before the weighted Jaccard. Without it, a large and a small knowledge base about one subject have a small similarity only because of their sizes.
- The thresholds are admin settings (``server_settings``). Their defaults here are not calibrated.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from .ontology.base import normalize_name
from .store import themes as themes_store
from .store.vectors import pack_vector, unpack_vector

Key = tuple[str, str]  # (entity type, normal form of a name)

SIGNATURE_ENTITIES = 256  # Section 8.2 rule 1
MIN_SIGNATURE_DOCUMENTS = 5  # a knowledge base with fewer counted documents has no signature
MIN_DOCUMENT_KEYS = 3  # Section 8.5 rule 6
IDENTITY_OVERLAP = 0.5  # Section 8.2 rule 7
ENTITY_WEIGHT, EMBEDDING_WEIGHT = 0.7, 0.3  # Section 8.2 rule 3
LABEL_ENTITIES, TOP_ENTITIES = 2, 10  # Section 8.2 rule 8
# A new run is due after this time, or when a knowledge base has this number of new visible documents (Section 8.2 rule 10).
RUN_INTERVAL_SECONDS = 24 * 60 * 60
RUN_NEW_DOCUMENTS = 20

# Admin settings in ``server_settings`` (CT25). The defaults are not calibrated: see the module text.
SETTINGS = {"theme_match_threshold": 0.25, "document_match_threshold": 0.5, "seed_match_threshold": 0.5}
LAST_RUN_KEY = "theme_judgement_last_run"


def read_settings(conn: sqlite3.Connection) -> dict[str, float]:
    """The thresholds of the judgement. A stored value that is not a number in [0, 1] is a defect of the data: ``RuntimeError``."""
    values = dict(SETTINGS)
    for key in SETTINGS:
        row = conn.execute("SELECT value FROM server_settings WHERE key=?", (key,)).fetchone()
        if row is None:
            continue
        try:
            value = float(row[0])
        except (TypeError, ValueError):
            raise RuntimeError(f"server_settings.{key} is not a number: {row[0]!r}") from None
        if not 0.0 <= value <= 1.0:
            raise RuntimeError(f"server_settings.{key} is out of range: {value!r}")
        values[key] = value
    return values


def write_setting(conn: sqlite3.Connection, key: str, value: float, *, updated_by: int | None) -> None:
    """Set one threshold. ``ValueError`` for an unknown key or a value that is not in [0, 1]. No commit."""
    if key not in SETTINGS:
        raise ValueError(f"unknown judgement setting: {key!r}")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{key} must be a number from 0 to 1")
    conn.execute(
        "INSERT INTO server_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (key, repr(float(value)), time.time(), updated_by),
    )


# --- signatures ------------------------------------------------------------------------------------------------------


@dataclass(slots=True)
class Signature:
    kb_id: int
    knowledge_type: str  # ``real`` or ``fictional``; a personal knowledge base counts as ``real`` (CT18)
    is_shared: bool
    weights: dict[Key, float]  # entity keys and alias keys → count of visible source documents
    names: dict[Key, str] = field(default_factory=dict)  # entity keys → the name to show
    embed_model: str = ""
    centroid: list[float] | None = None
    visible_documents: int = 0


def _loads(raw: Any, default: Any) -> Any:
    try:
        value = json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def compute_signature(conn: sqlite3.Connection, *, kb_id: int, knowledge_type: str, is_shared: bool, exclude_documents: set[str] = frozenset()) -> Signature | None:
    """The signature of one knowledge base from its visible documents, or None when fewer than ``MIN_SIGNATURE_DOCUMENTS`` documents count. ``exclude_documents``: the fiction-like documents of a personal knowledge base (Section 8.2 rule 1)."""
    visible = {str(r[0]) for r in conn.execute("SELECT id FROM documents WHERE kb_id=? AND COALESCE(hidden, 0)=0", (int(kb_id),))} - set(exclude_documents)
    if len(visible) < MIN_SIGNATURE_DOCUMENTS:
        return None
    entities = []
    for row in conn.execute("SELECT id, type, name, norm_name, aliases, sources FROM entities WHERE kb_id=?", (int(kb_id),)):
        weight = sum(1 for source in _loads(row["sources"], []) if source in visible)
        if weight:
            entities.append((weight, str(row["type"]), str(row["norm_name"]), str(row["name"]), _loads(row["aliases"], []), str(row["id"])))
    if not entities:
        return None
    entities.sort(key=lambda e: (-e[0], e[1], e[2]))
    top = entities[:SIGNATURE_ENTITIES]
    weights: dict[Key, float] = {}
    names: dict[Key, str] = {}
    for weight, etype, norm, name, aliases, _entity_id in top:
        key = (etype, norm)
        weights[key] = weights.get(key, 0.0) + weight
        names.setdefault(key, name)
        for alias in aliases:
            alias_key = (etype, normalize_name(str(alias)))
            if alias_key[1] and alias_key != key:
                weights[alias_key] = weights.get(alias_key, 0.0) + weight
    # The centroid of the embeddings of the signature entities, when they have one embedding model.
    marks = ",".join("?" for _ in top)
    rows = conn.execute(f"SELECT vector, model FROM embeddings WHERE kb_id=? AND owner_id IN ({marks})", (int(kb_id), *(e[5] for e in top))).fetchall()
    models = {str(r["model"] or "") for r in rows}
    centroid, embed_model = None, ""
    if rows and len(models) == 1:
        vectors = [unpack_vector(r["vector"]) for r in rows]
        dim = len(vectors[0])
        if dim and all(len(v) == dim for v in vectors):
            centroid = [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]
            embed_model = models.pop()
    return Signature(kb_id=int(kb_id), knowledge_type=knowledge_type, is_shared=is_shared, weights=weights, names=names, embed_model=embed_model, centroid=centroid, visible_documents=len(visible))


def _signature_json(signature: Signature) -> str:
    return json.dumps([[k[0], k[1], w, signature.names.get(k)] for k, w in sorted(signature.weights.items())], ensure_ascii=False)


def _signature_from_row(row: dict[str, Any], *, is_shared: bool) -> Signature:
    weights: dict[Key, float] = {}
    names: dict[Key, str] = {}
    for item in _loads(row["entities"], []):
        if isinstance(item, list) and len(item) >= 3:
            key = (str(item[0]), str(item[1]))
            weights[key] = float(item[2])
            if len(item) > 3 and item[3]:
                names[key] = str(item[3])
    centroid = unpack_vector(row["centroid"]) if row["centroid"] else None
    return Signature(kb_id=int(row["kb_id"]), knowledge_type=str(row["knowledge_type"]), is_shared=is_shared, weights=weights, names=names, embed_model=str(row["embed_model"] or ""), centroid=centroid, visible_documents=int(row["visible_documents"]))


# --- similarity ------------------------------------------------------------------------------------------------------


def idf_table(signatures: list[Signature]) -> dict[Key, float]:
    """The weight of each key across the given signatures: ``ln(1 + N / df)`` (see the module text for the change of the formula)."""
    count = len(signatures)
    df: dict[Key, int] = {}
    for signature in signatures:
        for key in signature.weights:
            df[key] = df.get(key, 0) + 1
    return {key: math.log(1.0 + count / n) for key, n in df.items()}


def tfidf(signature: Signature, idf: dict[Key, float]) -> dict[Key, float]:
    """The tf-idf vector of a signature, with the sum 1."""
    vector = {key: weight * idf.get(key, 0.0) for key, weight in signature.weights.items()}
    total = sum(vector.values())
    return {key: value / total for key, value in vector.items()} if total > 0 else {}


def weighted_jaccard(a: dict[Key, float], b: dict[Key, float]) -> float:
    """Sum of minimums divided by sum of maximums."""
    keys = a.keys() | b.keys()
    maximum = sum(max(a.get(k, 0.0), b.get(k, 0.0)) for k in keys)
    return sum(min(a.get(k, 0.0), b.get(k, 0.0)) for k in keys) / maximum if maximum > 0 else 0.0


def cosine(u: list[float], v: list[float]) -> float:
    dot = sum(x * y for x, y in zip(u, v))
    nu, nv = math.sqrt(sum(x * x for x in u)), math.sqrt(sum(y * y for y in v))
    return dot / (nu * nv) if nu > 0 and nv > 0 else 0.0


def similarity(a: Signature, b: Signature, idf: dict[Key, float]) -> float:
    """Section 8.2 rule 3: the entity similarity, and with one embedding model also the cosine of the centroids (``0.7 × entity + 0.3 × embedding``)."""
    entity = weighted_jaccard(tfidf(a, idf), tfidf(b, idf))
    if a.embed_model and a.embed_model == b.embed_model and a.centroid and b.centroid and len(a.centroid) == len(b.centroid):
        return ENTITY_WEIGHT * entity + EMBEDDING_WEIGHT * max(0.0, cosine(a.centroid, b.centroid))
    return entity


def cluster(signatures: list[Signature], idf: dict[Key, float], threshold: float) -> list[list[int]]:
    """Average-linkage agglomerative clustering (Section 8.2 rule 4). Stop when the next merge has a score below ``threshold``. Return the clusters as lists of knowledge base IDs; each knowledge base is in exactly one cluster."""
    by_id = {s.kb_id: s for s in signatures}
    ids_ = sorted(by_id)
    pair = {(a, b): similarity(by_id[a], by_id[b], idf) for i, a in enumerate(ids_) for b in ids_[i + 1:]}

    def score(x: list[int], y: list[int]) -> float:
        return sum(pair[(min(a, b), max(a, b))] for a in x for b in y) / (len(x) * len(y))

    clusters = [[i] for i in ids_]
    while len(clusters) > 1:
        best, best_pair = -1.0, None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                value = score(clusters[i], clusters[j])
                if value > best:
                    best, best_pair = value, (i, j)
        if best_pair is None or best < threshold:
            break
        i, j = best_pair
        clusters[i] = sorted(clusters[i] + clusters[j])
        del clusters[j]
    return clusters


# --- the run ---------------------------------------------------------------------------------------------------------


@dataclass(slots=True)
class RunReport:
    signatures: int = 0
    themes_active: int = 0
    themes_new: int = 0
    themes_empty: int = 0
    memberships: int = 0
    document_matches: int = 0


def _knowledge_bases(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The knowledge bases that take part: each shared one, and each public personal one (Section 8.2 rules 1 and 5). A row that is neither kind is skipped."""
    rows = conn.execute("SELECT id, is_personal, account_id, is_public, knowledge_type FROM knowledge_bases ORDER BY id").fetchall()
    out = []
    for row in rows:
        if row["is_personal"] and row["account_id"] is not None:
            out.append({"id": int(row["id"]), "is_shared": False, "knowledge_type": "real", "is_public": bool(row["is_public"]), "account_id": int(row["account_id"])})
        elif not row["is_personal"] and row["account_id"] is None and row["knowledge_type"] in themes_store.KNOWLEDGE_TYPES:
            out.append({"id": int(row["id"]), "is_shared": True, "knowledge_type": str(row["knowledge_type"]), "is_public": True})
    return out


def _label_of(signatures: list[Signature]) -> tuple[str, list[str]]:
    """The computed label ("A · B") and the top entity names of a theme, from the summed weights of the entities of its shared members (Section 8.2 rule 8)."""
    totals: dict[Key, float] = {}
    names: dict[Key, str] = {}
    for signature in signatures:
        for key, name in signature.names.items():
            totals[key] = totals.get(key, 0.0) + signature.weights.get(key, 0.0)
            names.setdefault(key, name)
    ordered = [names[k] for k in sorted(totals, key=lambda k: (-totals[k], k))]
    return " · ".join(ordered[:LABEL_ENTITIES]), ordered[:TOP_ENTITIES]


def run_judgement(conn: sqlite3.Connection, *, now: float | None = None) -> RunReport:
    """One run of the judgement (Section 8.2). The themes and their members change in one transaction; the caller commits.

    Admin adjustments stay (Section 8.3): a locked label, a pinned membership, an excluded membership, and a merged theme.
    """
    ts = time.time() if now is None else now
    settings = read_settings(conn)
    report = RunReport()
    knowledge_bases = _knowledge_bases(conn)
    by_id = {kb["id"]: kb for kb in knowledge_bases}

    # 1. Signatures. A knowledge base without a signature keeps its memberships (rule 6).
    signatures: dict[int, Signature] = {}
    for kb in knowledge_bases:
        if not kb["is_public"]:
            # A private personal knowledge base does not take part: no signature and no computed membership.
            themes_store.delete_signature(conn, kb["id"])
            themes_store.delete_free_members_of_kb(conn, kb["id"])
            continue
        exclude = set() if kb["is_shared"] else themes_store.fiction_like_documents(conn, kb["id"])
        signature = compute_signature(conn, kb_id=kb["id"], knowledge_type=kb["knowledge_type"], is_shared=kb["is_shared"], exclude_documents=exclude)
        if signature is None:
            themes_store.delete_signature(conn, kb["id"])
            continue
        signatures[kb["id"]] = signature
        themes_store.save_signature(conn, kb_id=kb["id"], knowledge_type=kb["knowledge_type"], embed_model=signature.embed_model, entities=_signature_json(signature), centroid=pack_vector(signature.centroid) if signature.centroid else None, visible_documents=signature.visible_documents, now=ts)
    report.signatures = len(signatures)

    themes = {int(t["id"]): t for t in themes_store.list_themes(conn, include_merged=True)}
    members = themes_store.all_members(conn)
    excluded = {(int(m["theme_id"]), int(m["kb_id"])) for m in members if m["excluded"]}
    idf_by_type: dict[str, dict[Key, float]] = {}
    assigned: dict[int, int] = {}  # shared knowledge base with a signature → its computed theme

    for knowledge_type in themes_store.KNOWLEDGE_TYPES:
        of_type = [s for s in signatures.values() if s.knowledge_type == knowledge_type]
        idf = idf_by_type[knowledge_type] = idf_table(of_type)
        shared = [s for s in of_type if s.is_shared]
        clusters = cluster(shared, idf, settings["theme_match_threshold"])
        # 2. Stable identity (rule 7): a cluster takes the theme with the highest overlap of members, when the Jaccard is at least 0.5.
        current: dict[int, set[int]] = {}
        for m in members:
            theme = themes.get(int(m["theme_id"]))
            # The identity of a theme follows its computed members: a membership that the admin pinned or excluded does not count.
            if theme is not None and theme["knowledge_type"] == knowledge_type and theme["state"] != "merged" and not m["excluded"] and not m["pinned"] and by_id.get(int(m["kb_id"]), {}).get("is_shared"):
                current.setdefault(int(m["theme_id"]), set()).add(int(m["kb_id"]))
        candidates = []
        for index, kb_ids in enumerate(clusters):
            for theme_id, old in current.items():
                overlap = len(set(kb_ids) & old) / len(set(kb_ids) | old)
                if overlap >= IDENTITY_OVERLAP:
                    candidates.append((-overlap, theme_id, index))
        taken_themes: set[int] = set()
        theme_of_cluster: dict[int, int] = {}
        for _negative, theme_id, index in sorted(candidates):
            if theme_id not in taken_themes and index not in theme_of_cluster:
                theme_of_cluster[index] = theme_id
                taken_themes.add(theme_id)
        for index, kb_ids in enumerate(clusters):
            theme_id = theme_of_cluster.get(index)
            # A knowledge base that the admin excluded from this theme is not added to it: it goes to a theme of its own.
            keep = [k for k in kb_ids if theme_id is None or (theme_id, k) not in excluded]
            groups = [(theme_id, keep)] if keep else []
            groups += [(None, [k]) for k in kb_ids if k not in keep]
            for group_theme, group in groups:
                if group_theme is None:
                    label, _top = _label_of([signatures[k] for k in group])
                    created = themes_store.create_theme(conn, label=label or f"Theme of knowledge base {group[0]}", knowledge_type=knowledge_type, now=ts)
                    group_theme = int(created["id"])
                    themes[group_theme] = created
                    report.themes_new += 1
                for kb_id in group:
                    assigned[kb_id] = group_theme

    # 3. Memberships. Only the rows that the judgement can change are replaced: a pinned or excluded row stays, and a knowledge base without a signature keeps its rows.
    for kb_id in signatures:
        themes_store.delete_free_members_of_kb(conn, kb_id)
    for kb_id, theme_id in assigned.items():
        if themes_store.set_computed_member(conn, theme_id=theme_id, kb_id=kb_id, score=None):
            report.memberships += 1
    # 4. Public personal knowledge bases attach to each ``real`` theme where the average score with the shared members is at least the threshold (rule 5). The reputation ``low`` stops the attachment (Section 8.6).
    idf_real = idf_by_type.get("real", {})
    members_of: dict[int, list[int]] = {}
    for kb_id, theme_id in assigned.items():
        members_of.setdefault(theme_id, []).append(kb_id)
    from . import reputation

    owners = {int(kb["id"]): kb.get("account_id") for kb in _knowledge_bases(conn)}
    for signature in signatures.values():
        if signature.is_shared:
            continue
        owner = owners.get(signature.kb_id)
        if owner is not None and not reputation.attaches_to_themes(reputation.level(conn, int(owner), now=ts)):
            continue  # Section 8.6: with the reputation ``low`` the personal knowledge base has no theme attachment
        for theme_id, kb_ids in members_of.items():
            if themes[theme_id]["knowledge_type"] != "real" or (theme_id, signature.kb_id) in excluded:
                continue
            average = sum(similarity(signature, signatures[k], idf_real) for k in kb_ids) / len(kb_ids)
            if average >= settings["theme_match_threshold"] and themes_store.set_computed_member(conn, theme_id=theme_id, kb_id=signature.kb_id, score=average):
                report.memberships += 1

    # 5. State, label, top entities, signature, and last activity of each theme.
    members = themes_store.all_members(conn)
    for theme_id, theme in themes.items():
        if theme["state"] == "merged":
            continue
        rows = [m for m in members if int(m["theme_id"]) == theme_id and not m["excluded"]]
        shared_members = [int(m["kb_id"]) for m in rows if by_id.get(int(m["kb_id"]), {}).get("is_shared")]
        if not rows:
            themes_store.update_theme(conn, theme_id, now=ts, state="empty", computed_at=ts)
            report.themes_empty += 1
            continue
        changes: dict[str, Any] = {"state": "active", "computed_at": ts}
        member_signatures = [signatures[k] for k in shared_members if k in signatures]
        if member_signatures:
            label, top = _label_of(member_signatures)
            changes["top_entities"] = json.dumps(top, ensure_ascii=False)
            if not theme["label_locked"] and label:
                changes["label"] = label
                changes["description"] = ", ".join(top)
            # The theme signature: the union of the keys of the signatures of its shared members (Section 8.5 rule 3).
            keys = sorted({key for s in member_signatures for key in s.weights})
            changes["signature"] = json.dumps([[k[0], k[1]] for k in keys], ensure_ascii=False)
        if shared_members:
            marks = ",".join("?" for _ in shared_members)
            last = conn.execute(f"SELECT MAX(approved_at) FROM kb_contributions WHERE kb_id IN ({marks})", shared_members).fetchone()[0]
            if last is not None:
                changes["last_activity_at"] = last
        themes_store.update_theme(conn, theme_id, now=ts, **changes)
        report.themes_active += 1

    # 6. Document matches of the visible documents of personal knowledge bases (Section 8.5 rule 7).
    matcher = DocumentMatcher.load(conn)
    for kb in knowledge_bases:
        if kb["is_shared"]:
            continue
        themes_store.delete_document_matches_of_kb(conn, kb["id"])
        for document_id, keys in _document_keys(conn, kb["id"]).items():
            match = matcher.match(keys, threshold=settings["document_match_threshold"])
            themes_store.save_document_match(conn, document_id=document_id, kb_id=kb["id"], theme_id=match.theme_id, knowledge_type=match.knowledge_type, score=match.score, now=ts)
            report.document_matches += 1 if match.theme_id is not None else 0
    conn.execute(
        "INSERT INTO server_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, NULL) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (LAST_RUN_KEY, repr(ts), ts),
    )
    return report


def run_is_due(conn: sqlite3.Connection, *, now: float | None = None) -> bool:
    """Section 8.2 rule 10: a run is due once a day, and when a knowledge base has 20 or more new visible documents since its last signature."""
    ts = time.time() if now is None else now
    row = conn.execute("SELECT value FROM server_settings WHERE key=?", (LAST_RUN_KEY,)).fetchone()
    try:
        last = float(row[0]) if row is not None else None
    except (TypeError, ValueError):
        last = None
    if last is None or ts - last >= RUN_INTERVAL_SECONDS:
        return True
    counted = {int(r["kb_id"]): int(r["visible_documents"]) for r in themes_store.load_signatures(conn)}
    for kb in _knowledge_bases(conn):
        if not kb["is_public"]:
            continue
        visible = conn.execute("SELECT COUNT(*) FROM documents WHERE kb_id=? AND COALESCE(hidden, 0)=0", (kb["id"],)).fetchone()[0]
        if visible - counted.get(kb["id"], 0) >= RUN_NEW_DOCUMENTS:
            return True
    return False


# --- document match (Section 8.5) ------------------------------------------------------------------------------------


def _document_keys(conn: sqlite3.Connection, kb_id: int) -> dict[str, set[Key]]:
    """The keys of each visible document of a knowledge base: the ``(type, norm_name)`` of the entities that have the document as a source."""
    visible = {str(r[0]) for r in conn.execute("SELECT id FROM documents WHERE kb_id=? AND COALESCE(hidden, 0)=0", (int(kb_id),))}
    keys: dict[str, set[Key]] = {document_id: set() for document_id in visible}
    for row in conn.execute("SELECT type, norm_name, sources FROM entities WHERE kb_id=?", (int(kb_id),)):
        for source in _loads(row["sources"], []):
            if source in keys:
                keys[source].add((str(row["type"]), str(row["norm_name"])))
    return keys


def document_keys(conn: sqlite3.Connection, kb_id: int, document_id: str) -> set[Key]:
    """The keys of one document."""
    keys: set[Key] = set()
    for row in conn.execute("SELECT type, norm_name, sources FROM entities WHERE kb_id=?", (int(kb_id),)):
        if document_id in _loads(row["sources"], []):
            keys.add((str(row["type"]), str(row["norm_name"])))
    return keys


@dataclass(frozen=True, slots=True)
class Match:
    theme_id: int | None
    knowledge_type: str | None
    score: float | None


@dataclass(slots=True)
class DocumentMatcher:
    """The stored data that the document match needs: the idf over the signatures of all knowledge bases of the two types (rule 2), and the signature of each active theme (rule 3)."""

    idf: dict[Key, float]
    themes: list[dict[str, Any]]  # id, knowledge_type, keys, shared (count of shared members)

    @classmethod
    def load(cls, conn: sqlite3.Connection) -> "DocumentMatcher":
        shared_ids = {int(r[0]) for r in conn.execute("SELECT id FROM knowledge_bases WHERE COALESCE(is_personal, 0)=0")}
        signatures = [_signature_from_row(row, is_shared=int(row["kb_id"]) in shared_ids) for row in themes_store.load_signatures(conn)]
        counts: dict[int, int] = {}
        for member in themes_store.all_members(conn):
            if not member["excluded"] and int(member["kb_id"]) in shared_ids:
                counts[int(member["theme_id"])] = counts.get(int(member["theme_id"]), 0) + 1
        themes = []
        for theme in themes_store.list_themes(conn):
            if theme["state"] != "active":
                continue
            keys = {(str(k[0]), str(k[1])) for k in _loads(theme["signature"], []) if isinstance(k, list) and len(k) == 2}
            if keys:
                themes.append({"id": int(theme["id"]), "knowledge_type": str(theme["knowledge_type"]), "keys": keys, "shared": counts.get(int(theme["id"]), 0)})
        return cls(idf=idf_table(signatures), themes=themes)

    def score(self, keys: set[Key], signature: set[Key]) -> float:
        """Rule 4: the idf-weighted part of the keys of the document that are in the signature. A key that no signature has gets the weight of a key of one signature (the highest)."""
        ceiling = max(self.idf.values(), default=1.0)
        total = sum(self.idf.get(k, ceiling) for k in keys)
        return sum(self.idf.get(k, ceiling) for k in keys & signature) / total if total > 0 else 0.0

    def match(self, keys: set[Key], *, threshold: float) -> Match:
        """Rule 6: the theme with the highest score, when the score is at least the threshold and the document has at least 3 keys. With the same score, the theme with more shared knowledge bases."""
        if len(keys) < MIN_DOCUMENT_KEYS:
            return Match(None, None, None)
        best = None
        for theme in self.themes:
            value = self.score(keys, theme["keys"])
            if value >= threshold and (best is None or (value, theme["shared"], -theme["id"]) > (best[0], best[1]["shared"], -best[1]["id"])):
                best = (value, theme)
        return Match(None, None, None) if best is None else Match(best[1]["id"], best[1]["knowledge_type"], best[0])

    def seed_score(self, keys: set[Key], seed_names: list[str] | tuple[str, ...]) -> float:
        """Rule 5: the idf-weighted part of the seed keys that the document has. A seed is a name; its keys are the keys with that normal form, in each entity type."""
        norms = {normalize_name(name) for name in seed_names if normalize_name(name)}
        if not norms:
            return 0.0
        ceiling = max(self.idf.values(), default=1.0)
        by_norm: dict[str, list[Key]] = {}
        for key in set(self.idf) | keys:
            if key[1] in norms:
                by_norm.setdefault(key[1], []).append(key)
        total = found = 0.0
        for norm in norms:
            candidates = by_norm.get(norm, [])
            weight = max((self.idf.get(k, ceiling) for k in candidates), default=ceiling)
            total += weight
            if any(k in keys for k in candidates):
                found += weight
        return found / total if total > 0 else 0.0


def match_document(conn: sqlite3.Connection, *, kb_id: int, document_id: str, now: float | None = None) -> Match:
    """Compute and store the theme match of one document of a personal knowledge base (Section 8.5 rule 7: after the extraction of a new document). No commit."""
    settings = read_settings(conn)
    match = DocumentMatcher.load(conn).match(document_keys(conn, kb_id, document_id), threshold=settings["document_match_threshold"])
    themes_store.save_document_match(conn, document_id=document_id, kb_id=kb_id, theme_id=match.theme_id, knowledge_type=match.knowledge_type, score=match.score, now=now)
    return match
