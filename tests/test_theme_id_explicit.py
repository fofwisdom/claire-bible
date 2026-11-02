"""요청 경로는 `theme_id`를 반드시 명시한다 (설계 S3, THEMES.md §0 항목 2).

`claire.store.db`·`claire.store.queries`의 함수 상당수는 `theme_id` 기본값이 0(테마 0)이거나 None(모든 테마)이다.
계정이 생기면 테마 0은 관리자 테마이므로, 인자를 빠뜨리면 계정 사이로 읽기·쓰기가 샌다.

이 테스트는 실행하지 않고 소스만 본다 (`ast` + `inspect.signature`).
- `theme_id` 매개변수가 있는 store 함수를 모은다.
- 요청 경로 모듈에서 그 함수 호출을 찾는다. 이름은 각 모듈의 import 별칭으로 푼다.
- `theme_id=` 키워드나 그 매개변수 위치의 위치 인자가 없으면 위반이다.
  `**kwargs`는 dict 리터럴·`dict(...)`에 `theme_id`가 보일 때만 인정한다.
- "모든 테마"가 의도라면 호출 지점에서 `theme_id=None`처럼 명시한다. 주석 기반 허용 목록은 없다.
- 예외 목록은 없다. `except TypeError:` 처리부 안의 재시도 호출도 똑같이 위반이다.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from dataclasses import dataclass
from pathlib import Path

from claire.store import db as store_db
from claire.store import queries as store_queries

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

STORE_MODULES = (store_db, store_queries)

# 요청 경로 모듈 (설계 §18.3 L0-b)
REQUEST_PATH_GLOBS = (
    "claire/api/server.py",
    "claire/api/mcp_tools.py",
    "claire/api/oauth.py",
    "claire/telegram_bot.py",
    "claire/ingest/service.py",
    "claire/ingest/pipeline.py",
    "claire/ingest/composite.py",
    "claire/ingest/active.py",
    "claire/expand/*.py",
    "claire/retrieval/*.py",
    "claire/presentation/*.py",
    "claire/graph_bundle.py",
)


@dataclass(frozen=True)
class Violation:
    path: str  # 저장소 기준 경로
    line: int
    scope: str  # 감싼 함수 이름 (create_app.document_seen_route._mark)
    function: str  # store 함수 이름
    in_typeerror_fallback: bool  # `except TypeError:` 처리부 안의 호출

    def __str__(self) -> str:
        return f"{self.path}:{self.line} {self.function}"


@dataclass(frozen=True)
class ThemeParam:
    # 위치 인자로 넘길 수 있으면 그 인덱스, 키워드 전용이면 None
    index: int | None


def _theme_functions() -> dict[str, ThemeParam]:
    """`theme_id` 매개변수가 있는 store 함수: 정규 이름 -> 매개변수 위치."""
    found: dict[str, ThemeParam] = {}
    for module in STORE_MODULES:
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if fn.__module__ != module.__name__:
                continue
            params = list(inspect.signature(fn).parameters.values())
            names = [p.name for p in params]
            if "theme_id" not in names:
                continue
            p = params[names.index("theme_id")]
            index: int | None = None
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
                index = names.index("theme_id")
            found[f"{module.__name__}.{name}"] = ThemeParam(index)
    return found


_REEXPORT_CACHE: dict[str, str | None] = {}


def _canonical(name: str, theme_fns: dict[str, ThemeParam]) -> str | None:
    """정규 이름을 store 함수 이름으로. 다른 모듈의 재수출(`from ..graphview import synthesis_context`)도 따라간다."""
    if name in theme_fns:
        return name
    if not name.startswith("claire.") or "." not in name:
        return None
    if name not in _REEXPORT_CACHE:
        found: str | None = None
        parts = name.split(".")
        for cut in range(len(parts) - 1, 0, -1):
            try:
                obj: object = importlib.import_module(".".join(parts[:cut]))
            except ImportError:
                continue
            for attr in parts[cut:]:
                obj = getattr(obj, attr, None)
            if inspect.isfunction(obj):
                qual = f"{obj.__module__}.{obj.__name__}"
                if qual in theme_fns:
                    found = qual
            break
        _REEXPORT_CACHE[name] = found
    return _REEXPORT_CACHE[name]


def _request_path_files() -> list[Path]:
    files: set[Path] = set()
    for pattern in REQUEST_PATH_GLOBS:
        matched = sorted(SRC.glob(pattern))
        assert matched, f"요청 경로 모듈을 찾지 못했다: {pattern}"
        files.update(matched)
    return sorted(files)


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_from(module: str, is_package: bool, node: ast.ImportFrom) -> str:
    """`from <...> import` 의 기준 모듈을 절대 이름으로 푼다."""
    if node.level == 0:
        return node.module or ""
    base = module.split(".")
    if not is_package:
        base = base[:-1]
    if node.level > 1:
        base = base[: len(base) - (node.level - 1)]
    if node.module:
        base = base + node.module.split(".")
    return ".".join(base)


def _scope_nodes(body: list[ast.stmt]):
    """한 스코프의 노드. 중첩 함수·클래스·람다 안으로는 들어가지 않는다."""
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


# 이름으로 store 함수를 넘겨받아 그대로 돌려주는 래퍼 (server.py: graphview monkeypatch 호환)
_PASSTHROUGH_WRAPPERS = {"_resolve_query_func"}


def _alias_source(value: ast.expr) -> ast.expr | None:
    """`x = dbm.fn` 또는 `x = _resolve_query_func("fn", fn)` 의 원본 함수 식."""
    if isinstance(value, (ast.Name, ast.Attribute)):
        return value
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id in _PASSTHROUGH_WRAPPERS
        and len(value.args) == 2
    ):
        return value.args[1]
    return None


def _scope_bindings(
    body: list[ast.stmt], module: str, is_package: bool, outer: list[dict[str, str]]
) -> dict[str, str]:
    """한 스코프의 별칭: 지역 이름 -> 정규 이름. import와 store 함수를 가리키는 단순 대입을 본다."""
    bindings: dict[str, str] = {}
    assigns: list[ast.Assign] = []
    for node in _scope_nodes(body):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bindings[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    bindings[top] = top
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from(module, is_package, node)
            for alias in node.names:
                bindings[alias.asname or alias.name] = f"{base}.{alias.name}"
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            assigns.append(node)
    for node in sorted(assigns, key=lambda n: (n.lineno, n.col_offset)):
        source = _alias_source(node.value)
        if source is None:
            continue
        resolved = _resolve(source, [*outer, bindings])
        if resolved is not None:
            bindings[node.targets[0].id] = resolved
    return bindings


def _resolve(expr: ast.expr, scopes: list[dict[str, str]]) -> str | None:
    if isinstance(expr, ast.Name):
        for scope in reversed(scopes):
            if expr.id in scope:
                return scope[expr.id]
        return None
    if isinstance(expr, ast.Attribute):
        base = _resolve(expr.value, scopes)
        return f"{base}.{expr.attr}" if base else None
    return None


def _kwargs_has_theme_id(value: ast.expr) -> bool:
    if isinstance(value, ast.Dict):
        return any(isinstance(k, ast.Constant) and k.value == "theme_id" for k in value.keys)
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "dict":
        return any(kw.arg == "theme_id" for kw in value.keywords)
    return False


def _passes_theme_id(call: ast.Call, param: ThemeParam) -> bool:
    for kw in call.keywords:
        if kw.arg == "theme_id":
            return True
        if kw.arg is None and _kwargs_has_theme_id(kw.value):
            return True
    if param.index is None:
        return False
    positional = call.args[: param.index + 1]
    if any(isinstance(a, ast.Starred) for a in positional):
        # *args 로 넘기면 위치를 확인할 수 없다
        return False
    return len(call.args) > param.index


def _is_typeerror_handler(node: ast.ExceptHandler) -> bool:
    types = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
    return any(isinstance(t, ast.Name) and t.id == "TypeError" for t in types)


def _violations_in(
    path: Path, theme_fns: dict[str, ThemeParam], source: str | None = None
) -> list[Violation]:
    module = _module_name(path)
    is_package = path.name == "__init__.py"
    if source is None:
        source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    rel = path.relative_to(ROOT).as_posix()
    out: list[Violation] = []

    def visit(node: ast.AST, scopes: list[dict[str, str]], names: tuple[str, ...], fallback: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # 데코레이터·기본값·기반 클래스는 바깥 스코프에서 평가된다
            outer: list[ast.AST] = [*node.decorator_list]
            if isinstance(node, ast.ClassDef):
                outer += [*node.bases, *node.keywords]
            else:
                outer += [d for d in (*node.args.defaults, *node.args.kw_defaults) if d is not None]
            for expr in outer:
                visit(expr, scopes, names, fallback)
            inner = scopes + [_scope_bindings(node.body, module, is_package, scopes)]
            for stmt in node.body:
                visit(stmt, inner, (*names, node.name), fallback)
            return
        if isinstance(node, ast.ExceptHandler) and _is_typeerror_handler(node):
            fallback = True
        check(node, scopes, names, fallback)
        for child in ast.iter_child_nodes(node):
            visit(child, scopes, names, fallback)

    def check(node: ast.AST, scopes: list[dict[str, str]], names: tuple[str, ...], fallback: bool) -> None:
        if not isinstance(node, ast.Call):
            return
        resolved = _resolve(node.func, scopes)
        name = _canonical(resolved, theme_fns) if resolved else None
        if name is None:
            return
        if not _passes_theme_id(node, theme_fns[name]):
            out.append(Violation(rel, node.lineno, ".".join(names), name.rsplit(".", 1)[-1], fallback))

    module_scope = _scope_bindings(tree.body, module, is_package, [])
    for stmt in tree.body:
        visit(stmt, [module_scope], (), False)
    return out


def _all_violations() -> list[Violation]:
    fns = _theme_functions()
    found: list[Violation] = []
    for path in _request_path_files():
        found.extend(_violations_in(path, fns))
    return found


def test_store_theme_functions_are_found() -> None:
    """수집이 비면 검사도 비게 되므로 대표 함수가 잡히는지 확인한다."""
    fns = _theme_functions()
    assert "claire.store.db.get_document" in fns
    assert "claire.store.db.insert_document" in fns
    assert "claire.store.queries.theme_summary" in fns
    assert fns["claire.store.db.add_edge"].index is None  # 키워드 전용


def test_detector_resolves_import_aliases() -> None:
    """별칭 해석과 판정 규칙 자체를 확인한다."""
    fns = _theme_functions()
    src = (
        "from ..store import db as dbm\n"
        "from ..store.queries import theme_summary as ts\n"
        "def f(conn, t, kw):\n"
        "    from ..store.db import get_document\n"
        "    dbm.get_document(conn, 1)\n"  # 위반
        "    dbm.get_document(conn, 1, t)\n"
        "    get_document(conn, 1, theme_id=None)\n"
        "    ts(conn, None)\n"  # 위반: 위치 2가 theme_id
        "    ts(conn, None, t)\n"
        "    dbm.add_edge(conn, 1, 2, 'r', 'd', 1.0, None, None, t)\n"  # 위반: 키워드 전용
        "    dbm.get_document(conn, 1, **kw)\n"  # 위반
        "    dbm.get_document(conn, 1, **{'theme_id': t})\n"
        "    from ..store.queries import node_detail as _detail\n"
        "    _detail = _resolve_query_func('node_detail', _detail)\n"
        "    _detail(conn, 'e1')\n"  # 위반: 래퍼로 받은 별칭
        "    fn = dbm.get_entity\n"
        "    fn(conn, 'e1', t)\n"
        "    from ..graphview import synthesis_context\n"
        "    synthesis_context(conn, ['e1'])\n"  # 위반: 재수출을 거친 호출
    )
    # 모듈 이름 계산(상대 import 해석)을 위해 요청 경로 안의 가상 경로를 쓴다
    found = _violations_in(SRC / "claire" / "api" / "_fake_for_test.py", fns, source=src)
    assert sorted(v.line for v in found) == [5, 8, 10, 11, 15, 19], [str(v) for v in found]
    assert all(v.scope == "f" and not v.in_typeerror_fallback for v in found)

    fallback_src = (
        "from ..store import db as dbm\n"
        "def g(conn, t):\n"
        "    try:\n"
        "        dbm.get_document(conn, 1, theme_id=t)\n"
        "    except TypeError:\n"
        "        dbm.get_document(conn, 1)\n"
    )
    found = _violations_in(SRC / "claire" / "api" / "_fake_for_test.py", fns, source=fallback_src)
    assert [(v.line, v.in_typeerror_fallback) for v in found] == [(6, True)]


def test_request_paths_pass_theme_id_explicitly() -> None:
    violations = [str(v) for v in _all_violations()]
    assert not violations, (
        "theme_id를 명시하지 않은 store 호출 (기본값이 테마 0이거나 모든 테마다):\n  "
        + "\n  ".join(violations)
    )
