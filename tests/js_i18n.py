"""JavaScript prelude for tests that run a function of a bundled page in Node.

The page functions read their text from the catalog (``tr`` in app.js, ``rtr`` in reader.js and in share.html). The prelude gives the two names with the catalog of one locale, like the page runtime does.
"""

from __future__ import annotations

import json

from claire.i18n import load_catalog


# The functions under test read only these key domains. A smaller catalog keeps the ``node -e`` argument below the size limit of one argument.
PRELUDE_KEY_PREFIXES = ("reader.", "share.", "ws.", "home.", "common.", "context.")


def js_i18n_prelude(locale: str = "en") -> str:
    catalog = json.dumps({key: value for key, value in load_catalog(locale).items() if key.startswith(PRELUDE_KEY_PREFIXES)}, ensure_ascii=False)
    return (
        f"const __CLAIRE_CATALOG = {catalog};\n"
        "function rtr(key, params) {\n"
        "  const template = Object.prototype.hasOwnProperty.call(__CLAIRE_CATALOG, key) ? __CLAIRE_CATALOG[key] : key;\n"
        "  return params ? String(template).replace(/\\{([a-zA-Z0-9_]+)\\}/g, (m, name) => (params[name] != null ? String(params[name]) : m)) : template;\n"
        "}\n"
        "const tr = rtr;\n"
    )
