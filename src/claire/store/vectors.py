"""벡터 저장/검색.

advisor 조언: sqlite-vec 패키징(WSL2)이 불안할 수 있으니 auto 모드로 시도하고, 실패하면 임베딩을 BLOB 로 저장 + Python brute-force cosine 으로 폴백한다. 수백~수천 노드 규모에선 brute-force 로 충분하다.
"""

from __future__ import annotations

import sqlite3
import struct
import time
from typing import Any, NamedTuple

# --- 테마별 임베딩 공간 고정 (설계 §10.4) ---

# 모델을 기록하지 않던 시절의 라벨. 이 라벨(또는 NULL·빈 값)의 행은 어느 모델인지 모른다.
LEGACY_EMBED_MODELS: tuple[str, ...] = ("claire",)
# MockProvider 의 sha256 의사 임베딩. 실제 모델 공간과 섞이지 않도록 별도 이름을 쓴다.
MOCK_EMBED_MODEL = "mock-sha256"
DEFAULT_EMBED_DIM = 3072
DEFAULT_EMBED_EOS_DATES: dict[str, str] = {"gemini-embedding-001": "2028-05-14"}
RETIRED_EMBED_MODELS: frozenset[str] = frozenset({"gemini-embedding-2-preview", "embedding-2-preview", "text-embedding-004", "embedding-001", "embedding-gecko-001", "gemini-embedding-exp", "gemini-embedding-exp-03-07"})


def canonical_provider_slug(provider: str | None) -> str:
    """Normalize provider names into canonical space_id prefix."""
    clean = str(provider or "").strip().lower()
    if clean in ("gemini", "google"):
        return "google"
    if not clean:
        return "legacy"
    return clean


def infer_prompt_format_version(model: str | None) -> str:
    """Infer prompt format version ('v2_prompt' for Gemini Embedding 2, else 'v1_param')."""
    clean = str(model or "").strip().lower()
    if "embedding-2" in clean:
        return "v2_prompt"
    return "v1_param"


def canonical_space_id(provider: str | None, model: str | None, dim: int | None = None, prompt_format_version: str | None = None) -> str:
    """Build canonical space_id '{provider}:{model}:{dim}:{prompt_format_version}'."""
    clean_model = str(model or "").strip()
    if not clean_model or is_legacy_model(clean_model):
        return ""
    d_val = int(dim) if dim and int(dim) > 0 else DEFAULT_EMBED_DIM
    pfv = str(prompt_format_version or "").strip() or infer_prompt_format_version(clean_model)
    return f"{canonical_provider_slug(provider)}:{clean_model}:{d_val}:{pfv}"


def parse_space_id(space_id: str) -> tuple[str, str, int, str]:
    """Parse canonical space_id into (provider, model, dim, prompt_format_version)."""
    parts = [p.strip() for p in str(space_id or "").split(":")]
    if len(parts) != 4 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid embedding space_id {space_id!r}; expected 'provider:model:dim:prompt_format_version'")
    try:
        dim = int(parts[2])
    except ValueError as exc:
        raise ValueError(f"Invalid dimension in embedding space_id {space_id!r}") from exc
    if dim <= 0:
        raise ValueError(f"Dimension must be positive in embedding space_id {space_id!r}")
    return parts[0], parts[1], dim, parts[3]


class EmbedPin(NamedTuple):
    provider: str
    model: str

    @property
    def space_id(self) -> str:
        return canonical_space_id(self.provider, self.model)

    def space_id_for(self, dim: int | None = None) -> str:
        return canonical_space_id(self.provider, self.model, dim=dim)


class EmbeddingSpaceInfo(NamedTuple):
    space_id: str
    provider: str
    model: str
    dim: int
    prompt_format_version: str
    normalized: int
    eos_date: str | None
    status: str


class EmbeddingModelMismatch(ValueError):
    """테마에 고정된 임베딩 모델과 다른 모델의 벡터를 저장하려 했다."""

    def __init__(self, kb_id: int, pinned_model: str, model: str) -> None:
        super().__init__(
            f"theme {kb_id} embedding space is pinned to {pinned_model!r}; "
            f"refusing vector from {model!r} (run re-embed to switch models)"
        )
        self.kb_id = kb_id
        self.pinned_model = pinned_model
        self.model = model


def is_legacy_model(model: str | None) -> bool:
    return not model or model in LEGACY_EMBED_MODELS


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.OperationalError:
        return set()


def _runtime_provider_name(provider_slug: str) -> str:
    clean = str(provider_slug or "").strip().lower()
    if clean == "google":
        return "gemini"
    if clean == "legacy":
        return ""
    return clean


def ensure_embedding_space(conn: sqlite3.Connection, *, provider: str, model: str, dim: int | None = None, space_id: str | None = None, status: str = "active", eos_date: str | None = None) -> str:
    """Register or update an entry in embedding_spaces and return its canonical space_id."""
    sid = str(space_id or canonical_space_id(provider, model, dim=dim)).strip()
    if not sid:
        return ""
    if not _table_exists(conn, "embedding_spaces"):
        return sid
    try:
        p_slug, m_name, d_val, pfv = parse_space_id(sid)
    except ValueError:
        p_slug = canonical_provider_slug(provider)
        m_name = str(model or "").strip()
        d_val = int(dim) if dim and int(dim) > 0 else DEFAULT_EMBED_DIM
        pfv = infer_prompt_format_version(m_name)
    eff_eos = eos_date if eos_date is not None else DEFAULT_EMBED_EOS_DATES.get(m_name)
    conn.execute(
        "INSERT INTO embedding_spaces(space_id, provider, model, dim, prompt_format_version, normalized, eos_date, status) VALUES (?, ?, ?, ?, ?, 1, ?, ?) ON CONFLICT(space_id) DO UPDATE SET status = CASE WHEN excluded.status != 'active' OR embedding_spaces.status = '' THEN excluded.status ELSE embedding_spaces.status END, eos_date = COALESCE(embedding_spaces.eos_date, excluded.eos_date)",
        (sid, p_slug, m_name, d_val, pfv, eff_eos, status),
    )
    return sid


def read_embedding_space(conn: sqlite3.Connection, space_id: str) -> EmbeddingSpaceInfo | None:
    if not space_id or not _table_exists(conn, "embedding_spaces"):
        return None
    row = conn.execute(
        "SELECT space_id, provider, model, dim, prompt_format_version, normalized, eos_date, status FROM embedding_spaces WHERE space_id=?",
        (space_id,),
    ).fetchone()
    if row is None:
        return None
    return EmbeddingSpaceInfo(str(row[0]), str(row[1]), str(row[2]), int(row[3]), str(row[4]), int(row[5]), str(row[6]) if row[6] is not None else None, str(row[7]))


def read_kb_embedding_spaces(conn: sqlite3.Connection, kb_id: int | None) -> tuple[str, str]:
    """Return (active_space_id, target_space_id) for the given knowledge base."""
    if kb_id is None:
        return "", ""
    cols = _table_columns(conn, "knowledge_bases")
    if not cols or "embed_model" not in cols:
        return "", ""
    if "active_space_id" in cols and "target_space_id" in cols:
        row = conn.execute(
            "SELECT active_space_id, target_space_id, embed_provider, embed_model FROM knowledge_bases WHERE id=?",
            (kb_id,),
        ).fetchone()
        if row is None:
            return "", ""
        active_sid = str(row[0] or "").strip()
        target_sid = str(row[1] or "").strip()
        if not active_sid and row[3]:
            active_sid = canonical_space_id(str(row[2] or ""), str(row[3] or ""))
        return active_sid, target_sid
    row = conn.execute("SELECT embed_provider, embed_model FROM knowledge_bases WHERE id=?", (kb_id,)).fetchone()
    if row is None or not row[1]:
        return "", ""
    return canonical_space_id(str(row[0] or ""), str(row[1] or "")), ""


def is_kb_active_space_eos(conn: sqlite3.Connection, kb_id: int | None) -> bool:
    """Return True if the KB's active embedding space or pinned model is EOS-retired."""
    if kb_id is None:
        return False
    pin = read_embed_pin(conn, kb_id)
    if pin is not None and pin.model.strip().lower() in RETIRED_EMBED_MODELS:
        return True
    active_sid, _ = read_kb_embedding_spaces(conn, kb_id)
    if active_sid and _table_exists(conn, "embedding_spaces"):
        info = read_embedding_space(conn, active_sid)
        if info is not None and info.status == "eos_retired":
            return True
    if pin is not None and pin.model and _table_exists(conn, "embedding_spaces"):
        row = conn.execute(
            "SELECT 1 FROM embedding_spaces WHERE model=? AND status='eos_retired' LIMIT 1",
            (pin.model,),
        ).fetchone()
        if row is not None:
            return True
    return False


def mark_space_eos_retired(conn: sqlite3.Connection, space_id_or_model: str) -> int:
    """Mark matching embedding_spaces rows as 'eos_retired' and return updated row count."""
    if not space_id_or_model or not _table_exists(conn, "embedding_spaces"):
        return 0
    cur = conn.execute(
        "UPDATE embedding_spaces SET status='eos_retired' WHERE space_id=? OR model=?",
        (space_id_or_model.strip(), space_id_or_model.strip()),
    )
    conn.commit()
    return cur.rowcount


def _str_attr(obj: Any, name: str) -> str:
    val = getattr(obj, name, None) if obj is not None else None
    return val.strip() if isinstance(val, str) else ""


def resolve_embed_identity(provider: Any, settings: Any = None) -> EmbedPin:
    """프로바이더가 실제로 쓰는 (임베딩 프로바이더, 임베딩 모델).

    모델: ``provider.embed_model`` → ``provider.settings.gemini_embed_model`` → ``settings.gemini_embed_model`` → 전역 설정. 모르면 빈 문자열.
    """
    name = _str_attr(provider, "name")
    explicit_ep = _str_attr(provider, "embed_provider")
    if name == "mock" and (not explicit_ep or explicit_ep == "mock"):
        return EmbedPin("mock", MOCK_EMBED_MODEL)
    model = _str_attr(provider, "embed_model") or _str_attr(
        getattr(provider, "settings", None), "gemini_embed_model"
    )
    if not model and settings is not None:
        model = _str_attr(settings, "gemini_embed_model")
    if not model:
        try:
            from ..config import get_settings

            model = _str_attr(get_settings(), "gemini_embed_model")
        except Exception:  # noqa: BLE001
            model = ""
    embed_provider = _str_attr(provider, "embed_provider") or name
    return EmbedPin(embed_provider, model)


def read_embed_pin(conn: sqlite3.Connection, kb_id: int) -> EmbedPin | None:
    """테마의 고정 임베딩. 아직 고정되지 않았거나(레거시) 테마 행·열이 없으면 None."""
    try:
        row = conn.execute(
            "SELECT embed_provider, embed_model FROM knowledge_bases WHERE id=?", (kb_id,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    model = str(row[1] or "").strip()
    if not model:
        return None
    return EmbedPin(str(row[0] or "").strip(), model)


def _adopt_legacy_rows(conn: sqlite3.Connection, kb_id: int, model: str, dim: int | None, *, space_id: str = "") -> int:
    """모델 미기록 행을 고정 모델로 다시 적는다. 차원이 다른 행은 다른 모델이 분명하므로 두고, 검색에서 빠진다."""
    marks = ",".join("?" for _ in LEGACY_EMBED_MODELS)
    has_space_col = "space_id" in _table_columns(conn, "embeddings")
    clean_space = str(space_id or "").strip() if has_space_col else ""
    if has_space_col and clean_space:
        del_sql = f"DELETE FROM embeddings WHERE kb_id=? AND space_id='' AND (model IS NULL OR model='' OR model IN ({marks}))"
        del_params: list[Any] = [kb_id, *LEGACY_EMBED_MODELS]
        if dim is not None:
            del_sql += " AND dim=?"
            del_params.append(dim)
        del_sql += " AND EXISTS (SELECT 1 FROM embeddings e2 WHERE e2.owner_id=embeddings.owner_id AND e2.kb_id=embeddings.kb_id AND e2.space_id=?)"
        del_params.append(clean_space)
        conn.execute(del_sql, del_params)
        sql = f"UPDATE embeddings SET model=?, space_id=CASE WHEN space_id='' THEN ? ELSE space_id END WHERE kb_id=? AND (model IS NULL OR model='' OR model IN ({marks}))"
        params: list[Any] = [model, clean_space, kb_id, *LEGACY_EMBED_MODELS]
    else:
        sql = f"UPDATE embeddings SET model=? WHERE kb_id=? AND (model IS NULL OR model='' OR model IN ({marks}))"
        params = [model, kb_id, *LEGACY_EMBED_MODELS]
    if dim is not None:
        sql += " AND dim=?"
        params.append(dim)
    return conn.execute(sql, params).rowcount

# --- (de)serialization: float32 array <-> bytes ---

def pack_vector(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_vector(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def looks_like_hash_fallback(vec: list[float] | None) -> bool:
    """프로바이더의 sha256 결정론적 의사 임베딩(embed 실패 폴백)인지 판별.

    폴백 벡터는 32바이트 digest 를 주기 32로 반복하고 각 값이 b/127.5 - 1 (b ∈ 0..255) 격자 위에 놓인다. 실제 임베딩이 이 세 조건을 동시에 만족할 가능성은 사실상 없다.
    """
    if not vec or len(vec) < 64:
        return False
    if any(vec[i] != vec[i - 32] for i in range(32, len(vec))):
        return False
    for v in vec[:32]:
        scaled = (v + 1.0) * 127.5
        if abs(scaled - round(scaled)) > 1e-3 or not 0 <= round(scaled) <= 255:
            return False
    return True


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def compute_mean_vector(vectors: list[list[float]]) -> list[float] | None:
    """임베딩 집합의 평균 벡터 mu 산출 (Adaptive Centering 용)."""
    if not vectors or not vectors[0]:
        return None
    dim = len(vectors[0])
    mean = [0.0] * dim
    valid_count = 0
    for v in vectors:
        if len(v) == dim:
            for i in range(dim):
                mean[i] += v[i]
            valid_count += 1
    if valid_count == 0:
        return None
    return [x / valid_count for x in mean]


def center_and_normalize(vec: list[float], mean: list[float]) -> list[float]:
    """평균 벡터를 차감하고 단위 벡터로 재정규화."""
    if not vec or not mean or len(vec) != len(mean):
        return vec
    centered = [x - m for x, m in zip(vec, mean)]
    norm = sum(x * x for x in centered) ** 0.5
    if norm == 0:
        return centered
    return [x / norm for x in centered]


def reciprocal_rank_fusion(
    ranked_lists: list[list[tuple[str, float]]],
    weights: list[float] | None = None,
    k: int = 60,
) -> list[tuple[str, float]]:
    """RRF (Reciprocal Rank Fusion) 하이브리드 순위 결합 (Vector + FTS)."""
    scores: dict[str, float] = {}
    if weights is None:
        weights = [1.0] * len(ranked_lists)

    for r_list, weight in zip(ranked_lists, weights):
        for rank, (item_id, _) in enumerate(r_list):
            scores[item_id] = scores.get(item_id, 0.0) + weight * (1.0 / (k + rank + 1))

    sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_items


def probe_sqlite_vec() -> tuple[bool, str]:
    """sqlite-vec 로드 가능 여부 10초 스파이크. (ok, detail)."""
    try:
        import sqlite_vec  # type: ignore
    except Exception as e:  # noqa: BLE001
        return False, f"sqlite-vec not importable: {e}"
    try:
        conn = sqlite3.connect(":memory:")
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        (ver,) = conn.execute("SELECT vec_version()").fetchone()
        conn.close()
        return True, f"sqlite-vec OK (vec_version={ver})"
    except Exception as e:  # noqa: BLE001
        return False, f"sqlite-vec load failed: {e}"


class VectorStore:
    """현재(M0)는 brute-force 백엔드만 구현. embeddings 테이블에 BLOB 저장.

    sqlite-vec 백엔드는 probe 가 성공하면 이후 마일스톤에서 활성화 예정. 인터페이스는 동일하게 유지한다.
    """

    def __init__(self, conn: sqlite3.Connection, backend: str = "brute", kb_id: int = 0):
        self.conn = conn
        self.backend = backend  # 'brute' | 'vec'
        self.kb_id = kb_id

    def put(
        self,
        owner_id: str,
        vector: list[float],
        model: str,
        kb_id: int | None = None,
        *,
        embed_provider: str | None = None,
        space_id: str | None = None,
    ) -> None:
        """Store vector into the KB active or shadow target space with fail-closed space/dimension pinning."""
        tid = self.kb_id if kb_id is None else kb_id
        clean_model = str(model or "").strip()
        clean_provider = str(embed_provider or "").strip()
        dim = len(vector)
        has_space_col = "space_id" in _table_columns(self.conn, "embeddings")
        resolved_space_id = ""
        pin: EmbedPin | None = None
        is_shadow_write = False
        if tid is not None:
            pin = read_embed_pin(self.conn, tid)
            active_sid, target_sid = read_kb_embedding_spaces(self.conn, tid)
            if space_id is not None:
                resolved_space_id = str(space_id).strip()
            else:
                eff_provider = pin.provider if (pin is not None and pin.model == clean_model and pin.provider) else clean_provider
                resolved_space_id = canonical_space_id(eff_provider, clean_model, dim=dim)
            is_shadow_write = bool(target_sid and resolved_space_id and resolved_space_id == target_sid and resolved_space_id != active_sid)
            if is_shadow_write:
                target_info = read_embedding_space(self.conn, target_sid)
                if target_info is not None:
                    if clean_model and clean_model != target_info.model:
                        raise EmbeddingModelMismatch(tid, target_info.model, clean_model)
                    if target_info.dim > 0 and dim != target_info.dim:
                        raise EmbeddingModelMismatch(tid, f"{target_info.model}@{target_info.dim}d", f"{clean_model}@{dim}d")
            else:
                pinned = self._pin_if_unset(tid, clean_model, clean_provider, dim, space_id=resolved_space_id)
                if pinned is not None and pinned != clean_model:
                    raise EmbeddingModelMismatch(tid, pinned, clean_model)
                if pin is not None and has_space_col:
                    active_dim_row = self.conn.execute(
                        "SELECT dim FROM embeddings WHERE kb_id=? AND (space_id=? OR (space_id='' AND model=?)) LIMIT 1",
                        (tid, active_sid, pin.model),
                    ).fetchone()
                    if active_dim_row is not None and active_dim_row[0] is not None and int(active_dim_row[0]) != dim:
                        raise EmbeddingModelMismatch(tid, f"{pin.model}@{int(active_dim_row[0])}d", f"{clean_model}@{dim}d")
                    if space_id is not None and active_sid and resolved_space_id != active_sid and active_dim_row is not None:
                        raise EmbeddingModelMismatch(tid, active_sid, resolved_space_id)
        else:
            resolved_space_id = str(space_id or canonical_space_id(clean_provider, clean_model, dim=dim)).strip()

        if resolved_space_id:
            status = "shadow_building" if is_shadow_write else "active"
            eff_space_prov = clean_provider or (pin.provider if pin is not None else "")
            ensure_embedding_space(self.conn, provider=eff_space_prov, model=clean_model, dim=dim, space_id=resolved_space_id, status=status)

        now = time.time()
        if has_space_col:
            if resolved_space_id and tid is not None:
                marks = ",".join("?" for _ in LEGACY_EMBED_MODELS)
                self.conn.execute(
                    f"DELETE FROM embeddings WHERE owner_id=? AND kb_id=? AND space_id='' AND (model=? OR model IS NULL OR model='' OR model IN ({marks}))",
                    (owner_id, tid, clean_model, *LEGACY_EMBED_MODELS),
                )
            self.conn.execute(
                "INSERT OR REPLACE INTO embeddings(owner_id,kb_id,space_id,dim,vector,model,updated_at) VALUES (?,?,?,?,?,?,?)",
                (owner_id, tid, resolved_space_id, dim, pack_vector(vector), clean_model, now),
            )
        else:
            self.conn.execute(
                "INSERT OR REPLACE INTO embeddings(owner_id,kb_id,dim,vector,model,updated_at) VALUES (?,?,?,?,?,?)",
                (owner_id, tid, dim, pack_vector(vector), clean_model, now),
            )
        self.conn.commit()

    def _pin_if_unset(
        self, kb_id: int, model: str, embed_provider: str | None, dim: int, *, space_id: str | None = None
    ) -> str | None:
        """고정 모델을 돌려준다. 미고정이면 ``model``로 고정하고(레거시 라벨은 고정하지 않음) 같은 차원의 레거시 행을 넘겨받는다."""
        pin = read_embed_pin(self.conn, kb_id)
        if pin is not None:
            return pin.model
        if not is_legacy_model(model):
            sid = str(space_id or canonical_space_id(embed_provider, model, dim=dim)).strip()
            ensure_embedding_space(self.conn, provider=embed_provider or "", model=model, dim=dim, space_id=sid, status="active")
            kb_cols = _table_columns(self.conn, "knowledge_bases")
            try:
                if "active_space_id" in kb_cols:
                    cur = self.conn.execute(
                        "UPDATE knowledge_bases SET embed_model=?, embed_provider=?, active_space_id=? WHERE id=? AND (embed_model IS NULL OR embed_model='')",
                        (model, (embed_provider or "").strip() or None, sid or None, kb_id),
                    )
                else:
                    cur = self.conn.execute(
                        "UPDATE knowledge_bases SET embed_model=?, embed_provider=? WHERE id=? AND (embed_model IS NULL OR embed_model='')",
                        (model, (embed_provider or "").strip() or None, kb_id),
                    )
            except sqlite3.OperationalError:
                return None
            if cur.rowcount == 1:
                _adopt_legacy_rows(self.conn, kb_id, model, dim, space_id=sid)
                return model
        pin = read_embed_pin(self.conn, kb_id)
        return pin.model if pin else None

    def replace_theme_embeddings(
        self,
        kb_id: int,
        *,
        embed_provider: str | None,
        model: str,
        items: list[tuple[str, list[float]]],
    ) -> int:
        """re-embed 전용: 고정을 ``model``로 바꾸고, 다른 모델 행을 지우고, ``items``를 넣는다. 한 트랜잭션. 넣은 행 수."""
        if is_legacy_model(model):
            raise ValueError(f"cannot pin theme {kb_id} to legacy embedding label {model!r}")
        now = time.time()
        clean_provider = (embed_provider or "").strip() or None
        dim = len(items[0][1]) if items else DEFAULT_EMBED_DIM
        sid = canonical_space_id(clean_provider, model, dim=dim)
        has_space_col = "space_id" in _table_columns(self.conn, "embeddings")
        kb_cols = _table_columns(self.conn, "knowledge_bases")
        try:
            ensure_embedding_space(self.conn, provider=clean_provider or "", model=model, dim=dim, space_id=sid, status="active")
            if "active_space_id" in kb_cols and "target_space_id" in kb_cols:
                self.conn.execute(
                    "UPDATE knowledge_bases SET embed_provider=?, embed_model=?, active_space_id=?, target_space_id=NULL WHERE id=?",
                    (clean_provider, model, sid or None, kb_id),
                )
            else:
                self.conn.execute(
                    "UPDATE knowledge_bases SET embed_provider=?, embed_model=? WHERE id=?",
                    (clean_provider, model, kb_id),
                )
            self.conn.execute("DELETE FROM embeddings WHERE kb_id=?", (kb_id,))
            if has_space_col:
                self.conn.executemany(
                    "INSERT OR REPLACE INTO embeddings(owner_id,kb_id,space_id,dim,vector,model,updated_at) VALUES (?,?,?,?,?,?,?)",
                    [(owner_id, kb_id, sid, len(vec), pack_vector(vec), model, now) for owner_id, vec in items],
                )
            else:
                self.conn.executemany(
                    "INSERT OR REPLACE INTO embeddings(owner_id,kb_id,dim,vector,model,updated_at) VALUES (?,?,?,?,?,?)",
                    [(owner_id, kb_id, len(vec), pack_vector(vec), model, now) for owner_id, vec in items],
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return len(items)

    def start_shadow_backfill(
        self,
        kb_id: int,
        *,
        target_model: str,
        embed_provider: str = "gemini",
        dim: int = DEFAULT_EMBED_DIM,
        target_space_id: str | None = None,
    ) -> str:
        """Register target embedding space and set knowledge_bases.target_space_id for non-blocking backfill."""
        if is_legacy_model(target_model):
            raise ValueError(f"cannot shadow-backfill theme {kb_id} to legacy label {target_model!r}")
        sid = ensure_embedding_space(self.conn, provider=embed_provider, model=target_model, dim=dim, space_id=target_space_id, status="shadow_building")
        self.conn.execute("UPDATE knowledge_bases SET target_space_id=? WHERE id=?", (sid, kb_id))
        self.conn.commit()
        return sid

    def cutover_target_space(self, kb_id: int, *, delete_old_vectors: bool = True) -> str:
        """Atomically promote target_space_id to active_space_id and clear stale vectors."""
        active_sid, target_sid = read_kb_embedding_spaces(self.conn, kb_id)
        if not target_sid:
            raise ValueError(f"theme {kb_id} has no target_space_id staged for cutover")
        info = read_embedding_space(self.conn, target_sid)
        if info is not None:
            p_slug, target_model, target_dim = info.provider, info.model, info.dim
        else:
            p_slug, target_model, target_dim, _ = parse_space_id(target_sid)
        target_count = self.count(kb_id, space_id=target_sid)
        active_count = self.count(kb_id) if active_sid else 0
        if target_count == 0 and active_count > 0:
            raise ValueError(f"refusing cutover for theme {kb_id}: target space {target_sid!r} has 0 vectors while active space has {active_count}")
        runtime_prov = _runtime_provider_name(p_slug) or None
        try:
            ensure_embedding_space(self.conn, provider=p_slug, model=target_model, dim=target_dim, space_id=target_sid, status="active")
            self.conn.execute("UPDATE embedding_spaces SET status='active' WHERE space_id=?", (target_sid,))
            if active_sid and active_sid != target_sid:
                in_use_elsewhere = self.conn.execute(
                    "SELECT 1 FROM knowledge_bases WHERE id != ? AND active_space_id=? LIMIT 1",
                    (kb_id, active_sid),
                ).fetchone()
                if in_use_elsewhere is None:
                    self.conn.execute(
                        "UPDATE embedding_spaces SET status = CASE WHEN status='eos_retired' THEN 'eos_retired' ELSE 'deprecated' END WHERE space_id=?",
                        (active_sid,),
                    )
            self.conn.execute(
                "UPDATE knowledge_bases SET active_space_id=?, target_space_id=NULL, embed_provider=?, embed_model=? WHERE id=?",
                (target_sid, runtime_prov, target_model, kb_id),
            )
            if delete_old_vectors:
                self.conn.execute("DELETE FROM embeddings WHERE kb_id=? AND space_id != ?", (kb_id, target_sid))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return target_sid

    def get(self, owner_id: str, kb_id: int | None = None, *, space_id: str | None = None) -> list[float] | None:
        tid = self.kb_id if kb_id is None else kb_id
        has_space_col = "space_id" in _table_columns(self.conn, "embeddings")
        if tid is not None:
            if space_id is not None and has_space_col:
                row = self.conn.execute(
                    "SELECT vector FROM embeddings WHERE owner_id=? AND kb_id=? AND space_id=?",
                    (owner_id, tid, space_id),
                ).fetchone()
            elif has_space_col:
                pin = read_embed_pin(self.conn, tid)
                active_sid, _ = read_kb_embedding_spaces(self.conn, tid)
                if pin is not None and active_sid:
                    row = self.conn.execute(
                        "SELECT vector FROM embeddings WHERE owner_id=? AND kb_id=? AND (space_id=? OR (space_id='' AND model=?)) LIMIT 1",
                        (owner_id, tid, active_sid, pin.model),
                    ).fetchone()
                else:
                    row = self.conn.execute(
                        "SELECT vector FROM embeddings WHERE owner_id=? AND kb_id=? LIMIT 1",
                        (owner_id, tid),
                    ).fetchone()
            else:
                row = self.conn.execute("SELECT vector FROM embeddings WHERE owner_id=? AND kb_id=?", (owner_id, tid)).fetchone()
        else:
            row = self.conn.execute("SELECT vector FROM embeddings WHERE owner_id=?", (owner_id,)).fetchone()
        return unpack_vector(row["vector"]) if row else None

    def count(self, kb_id: int | None = None, *, space_id: str | None = None) -> int:
        """저장된 임베딩 총 개수."""
        tid = self.kb_id if kb_id is None else kb_id
        has_space_col = "space_id" in _table_columns(self.conn, "embeddings")
        if tid is not None:
            if space_id is not None and has_space_col:
                row = self.conn.execute("SELECT count(*) FROM embeddings WHERE kb_id=? AND space_id=?", (tid, space_id)).fetchone()
            elif has_space_col:
                pin = read_embed_pin(self.conn, tid)
                active_sid, _ = read_kb_embedding_spaces(self.conn, tid)
                if pin is not None and active_sid:
                    row = self.conn.execute(
                        "SELECT count(*) FROM embeddings WHERE kb_id=? AND (space_id=? OR (space_id='' AND model=?))",
                        (tid, active_sid, pin.model),
                    ).fetchone()
                elif pin is not None:
                    row = self.conn.execute("SELECT count(*) FROM embeddings WHERE kb_id=? AND model=?", (tid, pin.model)).fetchone()
                else:
                    row = self.conn.execute("SELECT count(*) FROM embeddings WHERE kb_id=?", (tid,)).fetchone()
            else:
                row = self.conn.execute("SELECT count(*) FROM embeddings WHERE kb_id=?", (tid,)).fetchone()
        else:
            row = self.conn.execute("SELECT count(*) FROM embeddings").fetchone()
        return row[0] if row else 0

    def search(
        self,
        query_vec: list[float],
        limit: int = 10,
        adaptive_center: bool = False,
        kb_id: int | None = None,
        query_model: str | None = None,
        *,
        space_id: str | None = None,
    ) -> list[tuple[str, float]]:
        """(owner_id, score) 리스트, score 내림차순.
        
        adaptive_center=True 면 임베딩들의 평균 벡터(공통 배경 노이즈)를 감산하여 변별력을 극대화한다 (수백 개 이상 고차원 임베딩의 Hubness 왜곡 완화).

        테마에 고정 모델이 있으면 그 모델의 행만 본다(§10.4). ``query_model``이 주어졌는데 고정과 다르면 공간이 달라 비교할 수 없으므로 빈 결과다. 미고정(레거시) 테마는 전과 같다.
        """
        tid = self.kb_id if kb_id is None else kb_id
        has_space_col = "space_id" in _table_columns(self.conn, "embeddings")
        pin = read_embed_pin(self.conn, tid) if tid is not None else None
        if space_id is None and tid is not None and is_kb_active_space_eos(self.conn, tid):
            return []
        if space_id is not None and has_space_col:
            rows = self.conn.execute(
                "SELECT owner_id, vector FROM embeddings WHERE kb_id = ? AND space_id = ?",
                (tid, space_id),
            ).fetchall()
        elif pin is not None:
            if query_model is not None and query_model != pin.model:
                return []
            active_sid, _ = read_kb_embedding_spaces(self.conn, tid)
            if has_space_col and active_sid:
                rows = self.conn.execute(
                    "SELECT owner_id, vector FROM embeddings WHERE kb_id = ? AND (space_id = ? OR (space_id = '' AND model = ?))",
                    (tid, active_sid, pin.model),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT owner_id, vector FROM embeddings WHERE kb_id = ? AND model = ?",
                    (tid, pin.model),
                ).fetchall()
        elif tid is not None:
            rows = self.conn.execute(
                "SELECT owner_id, vector FROM embeddings WHERE kb_id = ?",
                (tid,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT owner_id, vector FROM embeddings"
            ).fetchall()
        if not rows:
            return []

        unpacked = [(r["owner_id"], unpack_vector(r["vector"])) for r in rows]

        if adaptive_center and len(unpacked) >= 2:
            vectors_only = [v for _, v in unpacked]
            mean_v = compute_mean_vector(vectors_only)
            if mean_v and len(query_vec) == len(mean_v):
                q_centered = center_and_normalize(query_vec, mean_v)
                scored = [
                    (owner_id, _cosine(q_centered, center_and_normalize(v, mean_v)))
                    for owner_id, v in unpacked
                ]
            else:
                scored = [
                    (owner_id, _cosine(query_vec, v))
                    for owner_id, v in unpacked
                ]
        else:
            scored = [
                (owner_id, _cosine(query_vec, v))
                for owner_id, v in unpacked
            ]

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def calibrate_theme_embeddings(self, kb_id: int | None = None, *, target_space_id: str | None = None, sample_limit: int = 50) -> dict[str, Any]:
        """Measure adaptive-centered cosine distribution across 4 resolution tiers for active vs shadow space."""
        tid = self.kb_id if kb_id is None else kb_id
        if tid is None:
            raise ValueError("kb_id is required for embedding space calibration")
        active_sid, kb_target_sid = read_kb_embedding_spaces(self.conn, tid)
        eff_target_sid = str(target_space_id or kb_target_sid or "").strip()
        try:
            from ..config import get_settings

            settings = get_settings()
            t1 = float(getattr(settings, "sim_tier_auto_merge", 0.93))
            t2 = float(getattr(settings, "sim_tier_verify", 0.72))
            t3 = float(getattr(settings, "sim_tier_related", 0.70))
            t4 = float(getattr(settings, "sim_tier_distinct", 0.55))
        except Exception:
            t1, t2, t3, t4 = 0.93, 0.72, 0.70, 0.55

        has_space_col = "space_id" in _table_columns(self.conn, "embeddings")

        def _space_stats(sid: str, fallback_model: str = "") -> dict[str, Any]:
            if has_space_col and sid:
                rows = self.conn.execute(
                    "SELECT owner_id, dim, vector FROM embeddings WHERE kb_id=? AND (space_id=? OR (space_id='' AND model=?)) ORDER BY owner_id LIMIT ?",
                    (tid, sid, fallback_model, int(sample_limit)),
                ).fetchall()
            elif sid:
                rows = self.conn.execute(
                    "SELECT owner_id, dim, vector FROM embeddings WHERE kb_id=? ORDER BY owner_id LIMIT ?",
                    (tid, int(sample_limit)),
                ).fetchall()
            else:
                rows = []
            vectors = [unpack_vector(r["vector"]) for r in rows if r["vector"]]
            total_count = self.count(tid, space_id=sid if (sid and has_space_col) else None) if sid else 0
            tiers = {"auto_merge": 0, "verify": 0, "related": 0, "distinct": 0}
            if len(vectors) < 2:
                return {"space_id": sid, "vector_count": total_count, "sampled": len(vectors), "dim": int(rows[0]["dim"]) if rows else 0, "pair_count": 0, "mean_similarity": 0.0, "p50_similarity": 0.0, "p90_similarity": 0.0, "max_similarity": 0.0, "tiers": tiers}
            mean_v = compute_mean_vector(vectors)
            centered = [center_and_normalize(v, mean_v) if mean_v else v for v in vectors]
            sims: list[float] = []
            for i in range(len(centered)):
                for j in range(i + 1, len(centered)):
                    sim = _cosine(centered[i], centered[j])
                    sims.append(sim)
                    if sim >= t1:
                        tiers["auto_merge"] += 1
                    elif sim >= t2:
                        tiers["verify"] += 1
                    elif sim >= t3:
                        tiers["related"] += 1
                    else:
                        tiers["distinct"] += 1
            sims.sort()
            n_pairs = len(sims)
            return {
                "space_id": sid,
                "vector_count": total_count,
                "sampled": len(vectors),
                "dim": int(rows[0]["dim"]) if rows else 0,
                "pair_count": n_pairs,
                "mean_similarity": round(sum(sims) / n_pairs, 4),
                "p50_similarity": round(sims[n_pairs // 2], 4),
                "p90_similarity": round(sims[min(n_pairs - 1, int(n_pairs * 0.9))], 4),
                "max_similarity": round(sims[-1], 4),
                "tiers": tiers,
            }

        pin = read_embed_pin(self.conn, tid)
        active_stats = _space_stats(active_sid, fallback_model=pin.model if pin else "")
        target_stats = _space_stats(eff_target_sid) if eff_target_sid else None
        return {
            "kb_id": tid,
            "thresholds": {"auto_merge": t1, "verify": t2, "related": t3, "distinct": t4},
            "active": active_stats,
            "target": target_stats,
        }


def make_vector_store(
    conn: sqlite3.Connection,
    backend_pref: str = "auto",
    kb_id: int = 0,
) -> VectorStore:
    """설정에 따라 백엔드 선택. 현재는 brute 만 구현하므로 항상 brute 반환하되, auto/vec 요청 시 probe 결과를 로깅용으로 남긴다(추후 vec 백엔드 연결)."""
    if backend_pref in ("auto", "vec"):
        ok, _detail = probe_sqlite_vec()
        # vec 백엔드 구현 전까지는 brute 사용. ok 여부는 doctor 에서 보고.
        return VectorStore(conn, backend="brute", kb_id=kb_id)
    return VectorStore(conn, backend="brute", kb_id=kb_id)
