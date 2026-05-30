"""벡터 저장/검색.

advisor 조언: sqlite-vec 패키징(WSL2)이 불안할 수 있으니 auto 모드로 시도하고,
실패하면 임베딩을 BLOB 로 저장 + Python brute-force cosine 으로 폴백한다.
수백~수천 노드 규모에선 brute-force 로 충분하다.
"""

from __future__ import annotations

import sqlite3
import struct
import time

# --- (de)serialization: float32 array <-> bytes ---

def pack_vector(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_vector(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


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

    sqlite-vec 백엔드는 probe 가 성공하면 이후 마일스톤에서 활성화 예정.
    인터페이스는 동일하게 유지한다.
    """

    def __init__(self, conn: sqlite3.Connection, backend: str = "brute"):
        self.conn = conn
        self.backend = backend  # 'brute' | 'vec'

    def put(self, owner_id: str, vector: list[float], model: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO embeddings(owner_id,dim,vector,model,updated_at) "
            "VALUES (?,?,?,?,?)",
            (owner_id, len(vector), pack_vector(vector), model, time.time()),
        )
        self.conn.commit()

    def get(self, owner_id: str) -> list[float] | None:
        row = self.conn.execute(
            "SELECT vector FROM embeddings WHERE owner_id=?", (owner_id,)
        ).fetchone()
        return unpack_vector(row["vector"]) if row else None

    def search(self, query_vec: list[float], limit: int = 10) -> list[tuple[str, float]]:
        """(owner_id, score) 리스트, score 내림차순."""
        rows = self.conn.execute(
            "SELECT owner_id, vector FROM embeddings"
        ).fetchall()
        scored = [
            (r["owner_id"], _cosine(query_vec, unpack_vector(r["vector"])))
            for r in rows
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]


def make_vector_store(conn: sqlite3.Connection, backend_pref: str = "auto") -> VectorStore:
    """설정에 따라 백엔드 선택. 현재는 brute 만 구현하므로 항상 brute 반환하되,
    auto/vec 요청 시 probe 결과를 로깅용으로 남긴다(추후 vec 백엔드 연결)."""
    if backend_pref in ("auto", "vec"):
        ok, _detail = probe_sqlite_vec()
        # vec 백엔드 구현 전까지는 brute 사용. ok 여부는 doctor 에서 보고.
        return VectorStore(conn, backend="brute")
    return VectorStore(conn, backend="brute")
