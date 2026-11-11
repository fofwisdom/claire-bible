# Internal federation API contract fixtures

Each JSON file is one call of the internal federation API (`/internal/federation/v1`). The keys are `request` (`method`, `path`, and `body` for a `POST`), `status`, and `response`.

The Python tests (`tests/test_federation_routes.py`) and the sidecar tests (`sidecar/fedify/`) read the same files. A change to one side without the other fails.

The `inbox_event_*.json` files have the same method and path. The body selects the call. The follower numbers in `followers.json` and `followers_next.json` are fixed test values, and the tests store them before the call.

The key pairs in `actor_keys.json` and `instance_actor_keys.json` are test keys. Do not use them in an instance.
