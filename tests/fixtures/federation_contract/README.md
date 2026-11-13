# Internal federation API contract fixtures

Each JSON file is one call of the internal federation API (`/internal/federation/v1`). The keys are `request` (`method`, `path`, and `body` for a `POST`), `status`, and `response`.

The Python tests (`tests/test_federation_routes.py`) and the sidecar tests (`sidecar/fedify/`) read the same files. A change to one side without the other fails.

The `inbox_event_*.json` files have the same method and path. The body selects the call. The follower numbers in `followers.json` and `followers_next.json` are fixed test values, and the tests store them before the call.

`outbox_tasks.json` gives a lease on the tasks: the `lease_until` value is the fixed test clock plus the lease time. The object numbers, the share token, and the tasks are fixed test values that the tests store before the call.

The key pairs in `actor_keys.json` and `instance_actor_keys.json` are test keys. Do not use them in an instance.

`actor_gone.json` is the actor record of a purged actor: the tests store an actor tombstone with that number before the call.

Contract 1.3 (unit FP4-3) adds the client outbox and the follows of the instance: `client_tasks.json` and `client_task_ack.json` (the tasks `follow`, `undo_follow`, and `reject_follow` from `client.db`), `following.json` (the total of accepted follows of an actor), and `inbox_event_accept_follow.json` (an `Accept` that refers to the follow ID `{actor}#follows/{n}`). The tests store the follows (`1` pending with the request `@carol@remote.example`, `3` accepted to `dan`, `4` pending to `frank`) and the tasks before the call.
