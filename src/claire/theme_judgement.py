"""Subject judgement and document match (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 8.2, 8.3, 8.5; unit CT-5).

A theme is a collection of knowledge bases with the same subject and the same knowledge type. The judgement uses only data that exists and makes no LLM call:

1. The signature of a knowledge base: its 256 entities with the highest weight (the count of visible source documents), with the keys ``(type, norm_name)`` of the entities and of their aliases.
2. The weight of a key across the server (idf), for each knowledge type.
3. The similarity of two knowledge bases: the weighted Jaccard of their tf-idf vectors, with the cosine of their entity embedding centroids as a second signal when the two use the same embedding model.
4. Average-linkage clustering of the shared knowledge bases of one type. Public personal knowledge bases attach to ``real`` themes; they never make a theme.
5. The document match: the theme whose signature has the largest idf-weighted part of the keys of a document.
6. Remote shared knowledge bases (KNOWLEDGE_FEDERATION_DESIGN.md Section 8.2): the signatures of the store ``knowledge_federation.remote_kbs`` take part. A remote knowledge base attaches to the local theme of its knowledge type with the highest average score with the local shared members, when that score is at least the threshold. It never makes a theme, and it does not change the clusters of the local knowledge bases: the idf of the clustering comes from the local signatures only, so that the data of another server cannot move a local knowledge base to another theme. The idf of the attachment comes from the local and the remote signatures of the type. A remote signature has no embedding centroid, so its similarity is the entity similarity.

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


def signature_keys(row: dict[str, Any]) -> set[Key]:
    """The keys of a row of ``kb_signatures``."""
    return set(_signature_from_row(row, is_shared=True).weights)


def remote_signature(kb: Any) -> Signature | None:
    """The signature of a remote knowledge base (a record of ``knowledge_federation.remote_kbs``), or None when its server gave no signature. ``kb_id`` is the number of the record, which is not a number of ``knowledge_bases``: the caller keeps the two kinds apart."""
    from .knowledge_federation import remote_kbs

    rows = remote_kbs.signature_rows(kb)
    if not rows:
        return None
    return Signature(kb_id=int(kb.id), knowledge_type=str(kb.knowledge_type), is_shared=True, weights={(etype, norm): weight for etype, norm, weight in rows}, visible_documents=int(kb.document_count))


def wire_signature(row: dict[str, Any]) -> dict[str, Any] | None:
    """The signature of a local knowledge base for another server (the input of ``vocabulary.build_knowledge_base``), from a row of ``kb_signatures``: the ``vocabulary.MAX_SIGNATURE_ENTITIES`` keys with the highest weight, as rows ``[type, norm_name, weight]``. None when the row has no key. The names and the centroid do not go to other servers."""
    from .knowledge_federation import vocabulary

    signature = _signature_from_row(row, is_shared=True)
    keys = sorted(signature.weights, key=lambda k: (-signature.weights[k], k))[: vocabulary.MAX_SIGNATURE_ENTITIES]
    entities = [[k[0], k[1], signature.weights[k]] for k in keys if k[0] and k[1] and signature.weights[k] > 0]
    if not entities:
        return None
    return {"entities": entities, "visible_documents": signature.visible_documents, "computed_at": float(row["computed_at"])}


def subject_terms(*, label: str, seed_names: list[str]) -> set[str]:
    """The terms of ``subject_score``: the normal forms of the label and of each seed entity. Empty: the account typed no text."""
    return {term for term in (normalize_name(name) for name in [label, *seed_names]) if term}


def subject_score(*, label: str, seed_names: list[str], candidate_label: str, candidate_keys: set[Key]) -> float | None:
    """How near a shared knowledge base is to the subject of a knowledge base that an account wants to make (federation design Section 8.3 rule 4). The design gives no method; this one uses only the text that the account typed.

    The terms are the normal forms of the label and of each seed entity. The first value is the part of the terms that are a name in the signature of the candidate or the label of the candidate. The second value is the Jaccard of the words of the two labels. The score is the larger value, from 0 to 1. None: the account typed no term.
    """
    own_label = normalize_name(label)
    terms = subject_terms(label=label, seed_names=seed_names)
    if not terms:
        return None
    other_label = normalize_name(candidate_label)
    names = {key[1] for key in candidate_keys} | {other_label}
    found = sum(1 for term in terms if term in names) / len(terms)
    own_words, other_words = set(own_label.split()), set(other_label.split())
    words = len(own_words & other_words) / len(own_words | other_words) if own_words and other_words else 0.0
    return max(found, words)


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
    remote_memberships: int = 0
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


@dataclass(slots=True)
class _Target:
    """The knowledge bases that one run puts in one theme. ``theme_id`` None: the write part makes a new theme with ``label``."""

    theme_id: int | None
    knowledge_type: str
    kb_ids: list[int] = field(default_factory=list)
    label: str = ""


@dataclass(slots=True)
class _Plan:
    """The result of the read part of a run: all that the write part stores."""

    private: list[int] = field(default_factory=list)  # private personal knowledge bases: no signature and no computed membership
    visible_counts: dict[int, int] = field(default_factory=dict)  # each public knowledge base → the count of its visible documents
    signatures: dict[int, Signature] = field(default_factory=dict)
    targets: list[_Target] = field(default_factory=list)
    personal: list[tuple[int, int, float]] = field(default_factory=list)  # (index in ``targets``, knowledge base, score)
    remote: list[tuple[int, int, float]] = field(default_factory=list)  # (index in ``targets``, remote knowledge base, score)


def _visible_count(conn: sqlite3.Connection, kb_id: int) -> int:
    """The count of all visible documents of a knowledge base, the fiction-like ones included."""
    return int(conn.execute("SELECT COUNT(*) FROM documents WHERE kb_id=? AND COALESCE(hidden, 0)=0", (int(kb_id),)).fetchone()[0])


def _merge_target(themes: dict[int, dict[str, Any]], theme_id: int) -> int | None:
    """The theme that a merged theme points to at the end of its chain of ``merged_into_theme_id``. None: the chain has no end, or its end is not a theme."""
    seen: set[int] = set()
    while theme_id in themes and themes[theme_id]["state"] == "merged":
        following = themes[theme_id]["merged_into_theme_id"]
        if theme_id in seen or following is None:
            return None
        seen.add(theme_id)
        theme_id = int(following)
    return theme_id if theme_id in themes else None


def _plan(conn: sqlite3.Connection, knowledge_bases: list[dict[str, Any]], settings: dict[str, float], ts: float) -> _Plan:
    """The read part of a run: the signatures, the clusters, the theme of each cluster, and the attachments. It writes nothing."""
    plan = _Plan()
    by_id = {kb["id"]: kb for kb in knowledge_bases}

    # 1. Signatures. A knowledge base without a signature keeps its memberships (rule 6).
    for kb in knowledge_bases:
        if not kb["is_public"]:
            plan.private.append(kb["id"])
            continue
        plan.visible_counts[kb["id"]] = _visible_count(conn, kb["id"])
        exclude = set() if kb["is_shared"] else themes_store.fiction_like_documents(conn, kb["id"])
        signature = compute_signature(conn, kb_id=kb["id"], knowledge_type=kb["knowledge_type"], is_shared=kb["is_shared"], exclude_documents=exclude)
        if signature is not None:
            plan.signatures[kb["id"]] = signature
    signatures = plan.signatures

    themes = {int(t["id"]): t for t in themes_store.list_themes(conn, include_merged=True)}
    members = themes_store.all_members(conn)
    excluded = {(int(m["theme_id"]), int(m["kb_id"])) for m in members if m["excluded"]}
    merge_targets = {target for target in (_merge_target(themes, theme_id) for theme_id, theme in themes.items() if theme["state"] == "merged") if target is not None}
    target_of_theme: dict[int, int] = {}  # theme → index in ``plan.targets``
    idf_by_type: dict[str, dict[Key, float]] = {}

    def target_index(theme_id: int | None, knowledge_type: str, label: str) -> int:
        if theme_id is not None and theme_id in target_of_theme:
            return target_of_theme[theme_id]
        plan.targets.append(_Target(theme_id=theme_id, knowledge_type=knowledge_type, label=label))
        if theme_id is not None:
            target_of_theme[theme_id] = len(plan.targets) - 1
        return len(plan.targets) - 1

    for knowledge_type in themes_store.KNOWLEDGE_TYPES:
        of_type = [s for s in signatures.values() if s.knowledge_type == knowledge_type]
        idf = idf_by_type[knowledge_type] = idf_table(of_type)
        shared = [s for s in of_type if s.is_shared]
        clusters = cluster(shared, idf, settings["theme_match_threshold"])
        # 2. Stable identity (rule 7): a cluster takes the theme with the highest overlap of members, when the Jaccard is at least 0.5.
        current: dict[int, set[int]] = {}
        pinned: dict[int, set[int]] = {}  # theme that is the target of a merge → its pinned shared members
        for m in members:
            theme_id, kb_id = int(m["theme_id"]), int(m["kb_id"])
            theme = themes.get(theme_id)
            if theme is None or theme["knowledge_type"] != knowledge_type or theme["state"] == "merged" or m["excluded"] or not by_id.get(kb_id, {}).get("is_shared"):
                continue
            # The identity of a theme follows its computed members: a membership that the admin pinned or excluded does not count.
            if not m["pinned"]:
                current.setdefault(theme_id, set()).add(kb_id)
            elif theme_id in merge_targets:
                pinned.setdefault(theme_id, set()).add(kb_id)
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
        # A merge pins the members of the merged theme to the target (Section 8.3), and a pinned row gives no identity. Thus a cluster with no theme takes the target of a merge that has half or more of the cluster as pinned members; else each run makes a new theme for the cluster of a merged theme.
        for index, kb_ids in enumerate(clusters):
            if index in theme_of_cluster:
                continue
            shares = sorted((-len(set(kb_ids) & rows) / len(kb_ids), theme_id) for theme_id, rows in pinned.items())
            if shares and -shares[0][0] >= IDENTITY_OVERLAP:
                theme_of_cluster[index] = shares[0][1]
        for index, kb_ids in enumerate(clusters):
            theme_id = theme_of_cluster.get(index)
            # A knowledge base that the admin excluded from this theme is not added to it: it goes to a theme of its own.
            keep = [k for k in kb_ids if theme_id is None or (theme_id, k) not in excluded]
            groups = [(theme_id, keep)] if keep else []
            groups += [(None, [k]) for k in kb_ids if k not in keep]
            for group_theme, group in groups:
                if group_theme is None:
                    # The theme that has exactly this group as its computed members stays the theme of the group (rule 7). Without this, a knowledge base that the admin excluded from the theme of its cluster gets a new theme number in each run.
                    free = sorted(t for t, old in current.items() if old == set(group) and t not in taken_themes and not any((t, k) in excluded for k in group))
                    if free:
                        group_theme = free[0]
                        taken_themes.add(group_theme)
                label = "" if group_theme is not None else _label_of([signatures[k] for k in group])[0] or f"Theme of knowledge base {group[0]}"
                plan.targets[target_index(group_theme, knowledge_type, label)].kb_ids.extend(group)

    # 4. Public personal knowledge bases attach to each ``real`` theme where the average score with the shared members is at least the threshold (rule 5). The reputation ``low`` stops the attachment (Section 8.6).
    idf_real = idf_by_type.get("real", {})
    from . import reputation

    for signature in signatures.values():
        if signature.is_shared:
            continue
        owner = by_id[signature.kb_id].get("account_id")
        if owner is not None and not reputation.attaches_to_themes(reputation.level(conn, int(owner), now=ts)):
            continue  # Section 8.6: with the reputation ``low`` the personal knowledge base has no theme attachment
        for index, target in enumerate(plan.targets):
            if target.knowledge_type != "real" or (target.theme_id, signature.kb_id) in excluded:
                continue
            average = sum(similarity(signature, signatures[k], idf_real) for k in target.kb_ids) / len(target.kb_ids)
            if average >= settings["theme_match_threshold"]:
                plan.personal.append((index, signature.kb_id, average))

    # 4b. Remote shared knowledge bases (federation design Section 8.2 rule 1): each one attaches to the theme of its type with the highest average score with the local shared members, when the score is at least the threshold. ``list_all`` gives no record of a blocked domain.
    from .knowledge_federation import remote_kbs

    remote_excluded = {(int(m["theme_id"]), int(m["remote_kb_id"])) for m in themes_store.remote_members(conn, include_excluded=True) if m["excluded"]}
    remote_signatures = [signature for signature in (remote_signature(kb) for kb in remote_kbs.list_all(conn)) if signature is not None]
    # The themes that exist come first, in the sequence of their IDs; with the same score, the first theme stays.
    ordered = sorted(range(len(plan.targets)), key=lambda i: (plan.targets[i].theme_id is None, plan.targets[i].theme_id or 0, i))
    for knowledge_type in themes_store.KNOWLEDGE_TYPES:
        remote_of_type = [signature for signature in remote_signatures if signature.knowledge_type == knowledge_type]
        if not remote_of_type:
            continue
        idf_all = idf_table([s for s in signatures.values() if s.knowledge_type == knowledge_type] + remote_of_type)
        for signature in remote_of_type:
            best: tuple[float, int] | None = None
            for index in ordered:
                target = plan.targets[index]
                if target.knowledge_type != knowledge_type or (target.theme_id, signature.kb_id) in remote_excluded:
                    continue
                average = sum(similarity(signature, signatures[k], idf_all) for k in target.kb_ids) / len(target.kb_ids)
                if average >= settings["theme_match_threshold"] and (best is None or average > best[0]):
                    best = (average, index)
            if best is not None:
                plan.remote.append((best[1], signature.kb_id, best[0]))
    return plan


def _write_plan(conn: sqlite3.Connection, plan: _Plan, knowledge_bases: list[dict[str, Any]], report: RunReport, ts: float) -> None:
    """The write part of a run for the signatures, the themes, and their members. No commit: the caller makes these changes one transaction."""
    from .knowledge_federation import remote_kbs

    by_id = {kb["id"]: kb for kb in knowledge_bases}
    signatures = plan.signatures
    for kb_id in plan.private:
        themes_store.delete_signature(conn, kb_id)
        themes_store.delete_free_members_of_kb(conn, kb_id)
    for kb_id in plan.visible_counts:
        if kb_id not in signatures:
            themes_store.delete_signature(conn, kb_id)
    for kb_id, signature in signatures.items():
        themes_store.save_signature(conn, kb_id=kb_id, knowledge_type=signature.knowledge_type, embed_model=signature.embed_model, entities=_signature_json(signature), centroid=pack_vector(signature.centroid) if signature.centroid else None, visible_documents=signature.visible_documents, now=ts)
    themes_store.replace_judged_counts(conn, plan.visible_counts, now=ts)
    report.signatures = len(signatures)

    theme_ids: list[int] = []
    for target in plan.targets:
        if target.theme_id is None:
            theme_ids.append(int(themes_store.create_theme(conn, label=target.label, knowledge_type=target.knowledge_type, now=ts)["id"]))
            report.themes_new += 1
        else:
            theme_ids.append(target.theme_id)

    # 3. Memberships. Only the rows that the judgement can change are replaced: a pinned or excluded row stays, and a knowledge base without a signature keeps its rows.
    for kb_id in signatures:
        themes_store.delete_free_members_of_kb(conn, kb_id)
    for theme_id, target in zip(theme_ids, plan.targets):
        for kb_id in target.kb_ids:
            if themes_store.set_computed_member(conn, theme_id=theme_id, kb_id=kb_id, score=None):
                report.memberships += 1
    for index, kb_id, score in plan.personal:
        if themes_store.set_computed_member(conn, theme_id=theme_ids[index], kb_id=kb_id, score=score):
            report.memberships += 1
    # A remote row that the admin pinned or excluded stays; each other row is computed again.
    remote_kbs.purge_blocked(conn)
    themes_store.delete_free_remote_members(conn)
    for index, remote_kb_id, score in plan.remote:
        if themes_store.set_computed_remote_member(conn, theme_id=theme_ids[index], remote_kb_id=remote_kb_id, score=score):
            report.remote_memberships += 1

    # 5. State, label, top entities, signature, and last activity of each theme.
    members = themes_store.all_members(conn)
    themes_with_remote = {int(m["theme_id"]) for m in themes_store.remote_members(conn)}
    for theme in themes_store.list_themes(conn):
        theme_id = int(theme["id"])
        shared_members = [int(m["kb_id"]) for m in members if int(m["theme_id"]) == theme_id and not m["excluded"] and by_id.get(int(m["kb_id"]), {}).get("is_shared")]
        # A personal knowledge base never makes a theme (rule 5): a theme with no shared knowledge base, local or remote, has the state ``empty`` (rule 7). The rows of the personal knowledge bases stay (rule 6).
        if not shared_members and theme_id not in themes_with_remote:
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


def run_judgement(conn: sqlite3.Connection, *, now: float | None = None) -> RunReport:
    """One run of the judgement (Section 8.2). The run commits its changes; a commit of the caller after the run changes nothing. Call it with no open transaction: the first commit of the run also commits the changes that the caller made on ``conn``.

    The write lock is held only for short times, because other writers wait 5 seconds at most:
    1. The read part (``_plan``) computes the signatures, the clusters, and the attachments. It writes nothing.
    2. One transaction stores the signatures and changes the themes and their members.
    3. The document matches are computed for one personal knowledge base at a time, and one transaction for each knowledge base stores them.
    4. One transaction stores the time of the run. A run that stops before this step is due again.

    Admin adjustments stay (Section 8.3): a locked label, a pinned membership, an excluded membership, and a merged theme.
    """
    ts = time.time() if now is None else now
    settings = read_settings(conn)
    report = RunReport()
    knowledge_bases = _knowledge_bases(conn)
    plan = _plan(conn, knowledge_bases, settings, ts)
    try:
        _write_plan(conn, plan, knowledge_bases, report, ts)
        conn.commit()

        # 6. Document matches of the visible documents of personal knowledge bases (Section 8.5 rule 7).
        matcher = DocumentMatcher.load(conn)
        for kb in knowledge_bases:
            if kb["is_shared"]:
                continue
            matches = {document_id: matcher.match(keys, threshold=settings["document_match_threshold"]) for document_id, keys in _document_keys(conn, kb["id"]).items()}
            themes_store.delete_document_matches_of_kb(conn, kb["id"])
            for document_id, match in matches.items():
                themes_store.save_document_match(conn, document_id=document_id, kb_id=kb["id"], theme_id=match.theme_id, knowledge_type=match.knowledge_type, score=match.score, now=ts)
                report.document_matches += 1 if match.theme_id is not None else 0
            conn.commit()
        conn.execute(
            "INSERT INTO server_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, NULL) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (LAST_RUN_KEY, repr(ts), ts),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return report


def run_is_due(conn: sqlite3.Connection, *, now: float | None = None) -> bool:
    """Section 8.2 rule 10: a run is due once a day, and when a knowledge base has 20 or more new visible documents since the last run.

    The comparison uses the count of all visible documents that the last run stored for each public knowledge base, also for one that got no signature. A knowledge base with no stored count was in no run: all its visible documents are new.
    """
    ts = time.time() if now is None else now
    row = conn.execute("SELECT value FROM server_settings WHERE key=?", (LAST_RUN_KEY,)).fetchone()
    try:
        last = float(row[0]) if row is not None else None
    except (TypeError, ValueError):
        last = None
    if last is None or ts - last >= RUN_INTERVAL_SECONDS:
        return True
    counted = themes_store.load_judged_counts(conn)
    for kb in _knowledge_bases(conn):
        if not kb["is_public"]:
            continue
        before = counted[kb["id"]] if kb["id"] in counted else 0
        if _visible_count(conn, kb["id"]) - before >= RUN_NEW_DOCUMENTS:
            return True
    # A record of a remote knowledge base that is new or that changed after the last run (federation design Section 8.2 rule 1).
    newest = conn.execute("SELECT MAX(changed_at) FROM kf_remote_knowledge_bases").fetchone()[0]
    return newest is not None and float(newest) > last


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
    ceiling: float = field(init=False)  # the weight of a key that no signature has: the highest idf, computed one time

    def __post_init__(self) -> None:
        self.ceiling = max(self.idf.values(), default=1.0)

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
        ceiling = self.ceiling
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
        ceiling = self.ceiling
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
