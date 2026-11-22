"""The media of a remote post in a real Chromium (FEDERATION_REMOTE_READING_DESIGN.md Sections 8 and 9.1, unit FP4-7).

The header checks of ``test_e2e_federation_media`` do not show that a browser loads the media: the body is in a sandbox frame with an opaque origin, and the media comes from another origin. This test starts the app on three local ports (app, usercontent, media), opens the client view of ``/syndication``, and checks that the image of the body has pixels and that the browser made no request to the remote server. Without a Playwright Chromium the test is skipped.
"""

from __future__ import annotations

import base64
import os
import socket
import threading
import time
from types import SimpleNamespace

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

from claire.api import server  # noqa: E402
from claire.store import accounts as accounts_store  # noqa: E402
from claire.store import client_db  # noqa: E402
from tests.test_e2e_federation_following import _Sidecar  # noqa: E402
from tests.test_e2e_federation_timeline import CAROL, _event, _object  # noqa: E402
from tests.test_federation_console import _db, _isolate, _prepare, _session  # noqa: E402, F401  (_isolate is an autouse fixture)
from tests.test_media_proxy import MediaServer  # noqa: E402

REAL_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFElEQVR4nGP8z8Dwn4GBgYGJAQoAHxcCAr7cSDMAAAAASUVORK5CYII=")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def live(tmp_path):
    ports = [_free_port() for _ in range(3)]
    base = f"http://127.0.0.1:{ports[0]}"
    settings, alpha, _beta = _prepare(tmp_path, public_url=base, fqdn=f"127.0.0.1:{ports[0]}")
    settings.usercontent_fqdn = f"127.0.0.1:{ports[1]}"
    settings.media_fqdn = f"127.0.0.1:{ports[2]}"
    settings.allow_private_networks = True
    _db(settings, lambda conn: conn.execute("UPDATE accounts SET profile_public=1 WHERE id=?", (alpha,)))
    app = server.create_app(settings, SimpleNamespace(provider=SimpleNamespace(name="stub")))
    servers = [uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on" if index == 0 else "off")) for index, port in enumerate(ports)]
    threads = [threading.Thread(target=srv.run, daemon=True) for srv in servers]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 20
    while not all(srv.started for srv in servers):
        if time.monotonic() > deadline or not all(thread.is_alive() for thread in threads):
            raise RuntimeError("test server did not start")
        time.sleep(0.05)
    remote = MediaServer({"/cat.png": ("image/png", REAL_PNG)}).start()
    try:
        client = client_db.connect_client_existing(client_db.client_db_path(settings.db_file))
        try:
            client.execute("INSERT INTO federation_following (account_id, request, actor_iri, state, created_at, updated_at) VALUES (?, ?, ?, 'accepted', 1, 1)", (alpha, CAROL, CAROL))
            client.commit()
        finally:
            client.close()
        obj = _object(f"{CAROL}/notes/1", CAROL, content_html=f'<p id="text">A cat <img id="cat" src="{remote.base}/cat.png" alt="a cat"></p>')
        assert _Sidecar(settings).call("POST", "/timeline-events", _event("Create", CAROL, obj)).json()["decision"] == "accepted"
        yield SimpleNamespace(base=base, settings=settings, alpha=alpha, remote=remote, media_origin=f"http://127.0.0.1:{ports[2]}", usercontent_origin=f"http://127.0.0.1:{ports[1]}")
    finally:
        remote.stop()
        for srv in servers:
            srv.should_exit = True
        for thread in threads:
            thread.join(timeout=15)


@pytest.fixture
def browser():
    with sync_api.sync_playwright() as p:
        try:
            # PW_CHROMIUM_EXECUTABLE: a Chromium binary of another Playwright build, when the build of the Python package is not installed.
            executable = os.environ.get("PW_CHROMIUM_EXECUTABLE")
            b = p.chromium.launch(executable_path=executable) if executable else p.chromium.launch()
        except Exception as exc:  # noqa: BLE001  (no browser binary in this environment)
            pytest.skip(f"Playwright Chromium is not available: {exc}")
        try:
            yield b
        finally:
            b.close()


def test_browser_loads_the_image_of_a_remote_post_through_the_proxy(live, browser):
    context = browser.new_context()
    context.add_cookies([{"name": "claire_session", "value": _session(live.settings, live.alpha), "url": live.base}])
    page = context.new_page()
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.goto(f"{live.base}/syndication?stage=collect")
    page.locator(".synd-post").first.click()
    frame_el = page.locator("iframe.synd-frame")
    frame_el.wait_for()
    assert (frame_el.get_attribute("src") or "").startswith(f"{live.usercontent_origin}/render/remote/")
    image = page.frame_locator("iframe.synd-frame").locator("#text img, article img").first
    image.wait_for()
    assert (image.get_attribute("src") or "").startswith(f"{live.media_origin}/media/remote/")
    # The image has pixels: the sandbox frame loaded it from the media host.
    deadline = time.monotonic() + 10
    while image.evaluate("el => el.naturalWidth") == 0 and time.monotonic() < deadline:
        time.sleep(0.1)
    assert image.evaluate("el => el.naturalWidth") == 2
    # Only the proxy contacted the remote server, one time. The browser made no request to it (RS3).
    assert live.remote.hits == ["/cat.png"]
    assert not [url for url in requests if url.startswith(live.remote.base)]
    context.close()
