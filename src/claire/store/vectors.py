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
# 임베딩만 Gemini 에 위임하는 프로바이더. 고정 프로바이더는 임베딩을 실제로 만든 쪽으로 기록한다.
_EMBED_BACKEND_BY_PROVIDER = {"codex": "gemini", "antigravity": "gemini"}


class EmbedPin(NamedTuple):
    provider: str
    model: str


class EmbeddingModelMismatch(ValueError):
    """테마에 고정된 임베딩 모델과 다른 모델의 벡터를 저장하려 했다."""

    def __init__(self, theme_id: int, pinned_model: str, model: str) -> None:
        super().__init__(
            f"theme {theme_id} embedding space is pinned to {pinned_model!r}; "
            f"refusing vector from {model!r} (run re-embed to switch models)"
        )
        self.theme_id = theme_id
        self.pinned_model = pinned_model
        self.model = model


def is_legacy_model(model: str | None) -> bool:
    return not model or model in LEGACY_EMBED_MODELS


def _str_attr(obj: Any, name: str) -> str:
    val = getattr(obj, name, None) if obj is not None else None
    return val.strip() if isinstance(val, str) else ""


def resolve_embed_identity(provider: Any, settings: Any = None) -> EmbedPin:
    """프로바이더가 실제로 쓰는 (임베딩 프로바이더, 임베딩 모델).

    모델: ``provider.embed_model`` → ``provider.settings.gemini_embed_model`` → ``settings.gemini_embed_model`` → 전역 설정. 모르면 빈 문자열.
    """
    name = _str_attr(provider, "name")
    if name == "mock":
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
    embed_provider = _str_attr(provider, "embed_provider") or _EMBED_BACKEND_BY_PROVIDER.get(name, name)
    return EmbedPin(embed_provider, model)


def read_embed_pin(conn: sqlite3.Connection, theme_id: int) -> EmbedPin | None:
    """테마의 고정 임베딩. 아직 고정되지 않았거나(레거시) 테마 행·열이 없으면 None."""
    try:
        row = conn.execute(
            "SELECT embed_provider, embed_model FROM themes WHERE id=?", (theme_id,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    model = str(row[1] or "").strip()
    if not model:
        return None
    return EmbedPin(str(row[0] or "").strip(), model)


def _adopt_legacy_rows(conn: sqlite3.Connection, theme_id: int, model: str, dim: int | None) -> int:
    """모델 미기록 행을 고정 모델로 다시 적는다. 차원이 다른 행은 다른 모델이 분명하므로 두고, 검색에서 빠진다."""
    marks = ",".join("?" for _ in LEGACY_EMBED_MODELS)
    sql = (
        "UPDATE embeddings SET model=? WHERE theme_id=? "
        f"AND (model IS NULL OR model='' OR model IN ({marks}))"
    )
    params: list[Any] = [model, theme_id, *LEGACY_EMBED_MODELS]
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

    def __init__(self, conn: sqlite3.Connection, backend: str = "brute", theme_id: int = 0):
        self.conn = conn
        self.backend = backend  # 'brute' | 'vec'
        self.theme_id = theme_id

    def put(
        self,
        owner_id: str,
        vector: list[float],
        model: str,
        theme_id: int | None = None,
        *,
        embed_provider: str | None = None,
    ) -> None:
        """벡터 저장. 테마가 아직 고정되지 않았으면 이 모델로 고정한다(원자적).

        고정된 모델과 다른 모델이면 저장하지 않고 ``EmbeddingModelMismatch``를 던진다. 모델을 바꾸려면 ``replace_theme_embeddings``(re-embed)를 쓴다.
        """
        tid = self.theme_id if theme_id is None else theme_id
        if tid is not None:
            pinned = self._pin_if_unset(tid, model, embed_provider, len(vector))
            if pinned is not None and pinned != model:
                raise EmbeddingModelMismatch(tid, pinned, model)
        self.conn.execute(
            "INSERT OR REPLACE INTO embeddings(owner_id,theme_id,dim,vector,model,updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (owner_id, tid, len(vector), pack_vector(vector), model, time.time()),
        )
        self.conn.commit()

    def _pin_if_unset(
        self, theme_id: int, model: str, embed_provider: str | None, dim: int
    ) -> str | None:
        """고정 모델을 돌려준다. 미고정이면 ``model``로 고정하고(레거시 라벨은 고정하지 않음) 같은 차원의 레거시 행을 넘겨받는다."""
        pin = read_embed_pin(self.conn, theme_id)
        if pin is not None:
            return pin.model
        if not is_legacy_model(model):
            try:
                cur = self.conn.execute(
                    "UPDATE themes SET embed_model=?, embed_provider=? "
                    "WHERE id=? AND (embed_model IS NULL OR embed_model='')",
                    (model, (embed_provider or "").strip() or None, theme_id),
                )
            except sqlite3.OperationalError:
                return None
            if cur.rowcount == 1:
                _adopt_legacy_rows(self.conn, theme_id, model, dim)
                return model
        pin = read_embed_pin(self.conn, theme_id)
        return pin.model if pin else None

    def replace_theme_embeddings(
        self,
        theme_id: int,
        *,
        embed_provider: str | None,
        model: str,
        items: list[tuple[str, list[float]]],
    ) -> int:
        """re-embed 전용: 고정을 ``model``로 바꾸고, 다른 모델 행을 지우고, ``items``를 넣는다. 한 트랜잭션. 넣은 행 수."""
        if is_legacy_model(model):
            raise ValueError(f"cannot pin theme {theme_id} to legacy embedding label {model!r}")
        now = time.time()
        try:
            self.conn.execute(
                "UPDATE themes SET embed_provider=?, embed_model=? WHERE id=?",
                ((embed_provider or "").strip() or None, model, theme_id),
            )
            self.conn.execute(
                "DELETE FROM embeddings WHERE theme_id=? AND (model IS NULL OR model != ?)",
                (theme_id, model),
            )
            self.conn.executemany(
                "INSERT OR REPLACE INTO embeddings(owner_id,theme_id,dim,vector,model,updated_at) "
                "VALUES (?,?,?,?,?,?)",
                [
                    (owner_id, theme_id, len(vec), pack_vector(vec), model, now)
                    for owner_id, vec in items
                ],
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return len(items)

    def get(self, owner_id: str, theme_id: int | None = None) -> list[float] | None:
        tid = self.theme_id if theme_id is None else theme_id
        if tid is not None:
            row = self.conn.execute(
                "SELECT vector FROM embeddings WHERE owner_id=? AND theme_id=?", (owner_id, tid)
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT vector FROM embeddings WHERE owner_id=?", (owner_id,)
            ).fetchone()
        return unpack_vector(row["vector"]) if row else None

    def count(self, theme_id: int | None = None) -> int:
        """저장된 임베딩 총 개수."""
        tid = self.theme_id if theme_id is None else theme_id
        if tid is not None:
            row = self.conn.execute("SELECT count(*) FROM embeddings WHERE theme_id=?", (tid,)).fetchone()
        else:
            row = self.conn.execute("SELECT count(*) FROM embeddings").fetchone()
        return row[0] if row else 0

    def search(
        self,
        query_vec: list[float],
        limit: int = 10,
        adaptive_center: bool = False,
        theme_id: int | None = None,
        query_model: str | None = None,
    ) -> list[tuple[str, float]]:
        """(owner_id, score) 리스트, score 내림차순.
        
        adaptive_center=True 면 임베딩들의 평균 벡터(공통 배경 노이즈)를 감산하여 변별력을 극대화한다 (수백 개 이상 고차원 임베딩의 Hubness 왜곡 완화).

        테마에 고정 모델이 있으면 그 모델의 행만 본다(§10.4). ``query_model``이 주어졌는데 고정과 다르면 공간이 달라 비교할 수 없으므로 빈 결과다. 미고정(레거시) 테마는 전과 같다.
        """
        tid = self.theme_id if theme_id is None else theme_id
        pin = read_embed_pin(self.conn, tid) if tid is not None else None
        if pin is not None:
            if query_model is not None and query_model != pin.model:
                return []
            rows = self.conn.execute(
                "SELECT owner_id, vector FROM embeddings WHERE theme_id = ? AND model = ?",
                (tid, pin.model),
            ).fetchall()
        elif tid is not None:
            rows = self.conn.execute(
                "SELECT owner_id, vector FROM embeddings WHERE theme_id = ?",
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


def make_vector_store(
    conn: sqlite3.Connection,
    backend_pref: str = "auto",
    theme_id: int = 0,
) -> VectorStore:
    """설정에 따라 백엔드 선택. 현재는 brute 만 구현하므로 항상 brute 반환하되, auto/vec 요청 시 probe 결과를 로깅용으로 남긴다(추후 vec 백엔드 연결)."""
    if backend_pref in ("auto", "vec"):
        ok, _detail = probe_sqlite_vec()
        # vec 백엔드 구현 전까지는 brute 사용. ok 여부는 doctor 에서 보고.
        return VectorStore(conn, backend="brute", theme_id=theme_id)
    return VectorStore(conn, backend="brute", theme_id=theme_id)
