"""멀티 테마 상주 큐 루프의 전역 batch·공정성·동적 registry 반영."""

from __future__ import annotations

from types import SimpleNamespace

from claire import cli
from claire import notify
from claire.store import theme as theme_module


class FakeService:
    def __init__(self, settings, queues, failures=None):
        self.settings = settings
        self.queues = queues
        self.failures = failures or set()

    def _take(self, name):
        if name in self.failures:
            raise RuntimeError(f"{name} failed")
        queue = self.queues.setdefault(name, [])
        return [queue.pop(0)] if queue else []

    def recover_failed(self, **_kwargs):
        return self._take("recover")

    def run_refresh_queue(self, **_kwargs):
        return self._take("refresh")

    def run_expand_queue(self, **_kwargs):
        return self._take("expand")

    def enqueue_due_watch(self, **_kwargs):
        return len(self._take("watch"))


def _install_registry(monkeypatch, active):
    class FakeThemeManager:
        def __init__(self, _settings, **_kwargs):
            pass

        def active_theme_settings(self, _settings):
            return list(active)

    monkeypatch.setattr(theme_module, "ThemeManager", FakeThemeManager)


def _entry(theme_id):
    return (
        SimpleNamespace(id=theme_id, label=f"theme-{theme_id}"),
        SimpleNamespace(db_file=f"/{theme_id}.db", vault_dir=f"/{theme_id}-vault"),
    )


def test_recover_cycle_has_global_batch_and_rotating_start(monkeypatch):
    active = [_entry(0), _entry(1), _entry(2)]
    _install_registry(monkeypatch, active)
    queues = {
        0: {"recover": [{"inbox_id": 1}, {"inbox_id": 2}]},
        1: {"recover": [{"inbox_id": 3}, {"inbox_id": 4}]},
        2: {"recover": [{"inbox_id": 5}, {"inbox_id": 6}]},
    }
    factory = lambda settings: FakeService(settings, queues[int(settings.db_file[1])])
    args = SimpleNamespace(batch=4, max_attempts=5, base_delay=1.0)
    state = cli._ThemeLoopState()

    first = cli._run_recover_cycle(SimpleNamespace(), args, state, factory)
    second = cli._run_recover_cycle(SimpleNamespace(), args, state, factory)

    assert [item["_theme_id"] for item in first["results"]] == [0, 1, 2, 0]
    assert len(first["results"]) == 4
    assert [item["_theme_id"] for item in second["results"]] == [1, 2]


def test_recover_cycle_isolates_one_theme_failure(monkeypatch):
    active = [_entry(0), _entry(1), _entry(2)]
    _install_registry(monkeypatch, active)

    def factory(settings):
        theme_id = int(settings.db_file[1])
        failures = {"recover"} if theme_id == 1 else set()
        return FakeService(settings, {"recover": [{"inbox_id": theme_id}]}, failures)

    cycle = cli._run_recover_cycle(
        SimpleNamespace(),
        SimpleNamespace(batch=3, max_attempts=5, base_delay=1.0),
        cli._ThemeLoopState(),
        factory,
    )

    assert [item["_theme_id"] for item in cycle["results"]] == [0, 2]
    assert cycle["errors"] == [
        {"theme_id": 1, "theme_label": "theme-1", "error": "recover failed"}
    ]


def test_service_cache_drops_deleted_theme_and_adds_new_theme(monkeypatch):
    active = [_entry(0), _entry(1)]
    _install_registry(monkeypatch, active)
    created = []

    class ClosableService:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    def factory(settings):
        service = ClosableService()
        created.append((settings.db_file, service))
        return service

    state = cli._ThemeLoopState()
    first = cli._theme_services_for_cycle(SimpleNamespace(), state, factory)
    theme_one_service = dict((theme.id, service) for theme, service in first)[1]
    active[:] = [_entry(1), _entry(2)]

    second = cli._theme_services_for_cycle(SimpleNamespace(), state, factory)

    assert set(state.services) == {1, 2}
    assert dict((theme.id, service) for theme, service in second)[1] is theme_one_service
    assert len(created) == 3
    assert created[0][1].closed is True
    assert theme_one_service.closed is False


def test_refresh_and_expand_cycles_use_global_batch(monkeypatch):
    active = [_entry(0), _entry(1)]
    _install_registry(monkeypatch, active)
    queues = {
        0: {
            "watch": [{"id": 1}, {"id": 2}],
            "refresh": [{"document_id": "r0"}, {"document_id": "r1"}],
            "expand": [{"document_id": "e0"}, {"document_id": "e1"}],
        },
        1: {
            "watch": [{"id": 3}, {"id": 4}],
            "refresh": [{"document_id": "r2"}, {"document_id": "r3"}],
            "expand": [{"document_id": "e2"}, {"document_id": "e3"}],
        },
    }
    factory = lambda settings: FakeService(settings, queues[int(settings.db_file[1])])
    args = SimpleNamespace(batch=3)

    refresh = cli._run_refresh_cycle(
        SimpleNamespace(), args, cli._ThemeLoopState(), factory
    )
    expand = cli._run_expand_cycle(
        SimpleNamespace(), args, cli._ThemeLoopState(), factory
    )

    assert refresh["enqueued"] == 3
    assert len(refresh["results"]) == 3
    assert [item["_theme_id"] for item in refresh["results"]] == [0, 1, 0]
    assert len(expand["results"]) == 3
    assert [item["_theme_id"] for item in expand["results"]] == [0, 1, 0]


def test_expansion_notification_groups_links_by_theme(monkeypatch):
    sent = []
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: SimpleNamespace(telegram_bot_token="token", notify_chat_id=1),
    )
    monkeypatch.setattr(
        notify,
        "notify_owner",
        lambda _token, _chat_id, message: sent.append(message) or True,
    )

    cli._notify_expansion(
        [
            {
                "stored": 1,
                "_theme_id": 0,
                "_theme_label": "기본",
                "followed": [{"stored": True, "title": "기본 링크"}],
            },
            {
                "stored": 1,
                "_theme_id": 2,
                "_theme_label": "연구",
                "followed": [{"stored": True, "title": "연구 링크"}],
            },
        ]
    )

    assert len(sent) == 1
    assert "theme#0 [기본]: 1건 적재\n• 기본 링크" in sent[0]
    assert "theme#2 [연구]: 1건 적재\n• 연구 링크" in sent[0]
