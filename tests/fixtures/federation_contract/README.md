# Internal federation API contract fixtures

Each JSON file is one call of the internal federation API (`/internal/federation/v1`). The keys are `request` (`method`, `path`, and `body` for a `POST`), `status`, and `response`.

The Python tests (`tests/test_federation_routes.py`) and the sidecar tests (`sidecar/fedify/`) read the same files. A change to one side without the other fails.

The `inbox_event_*.json` files have the same method and path. The body selects the call. The follower numbers in `followers.json` and `followers_next.json` are fixed test values, and the tests store them before the call.

`outbox_tasks.json` gives a lease on the tasks: the `lease_until` value is the fixed test clock plus the lease time. The object numbers, the share token, and the tasks are fixed test values that the tests store before the call.

The key pairs in `actor_keys.json` and `instance_actor_keys.json` are test keys. Do not use them in an instance.

`actor_gone.json` is the actor record of a purged actor: the tests store an actor tombstone with that number before the call.

Contract 1.3 (unit FP4-3) adds the client outbox and the follows of the instance: `client_tasks.json` and `client_task_ack.json` (the tasks `follow`, `undo_follow`, and `reject_follow` from `client.db`), `following.json` (the total of accepted follows of an actor), and `inbox_event_accept_follow.json` (an `Accept` that refers to the follow ID `{actor}#follows/{n}`). The tests store the follows (`1` pending with the request `@carol@remote.example`, `3` accepted to `dan`, `4` pending to `frank`) and the tasks before the call.

Unit FP4-4 adds the timeline calls (the version stays 1.3: no shape that existed changed): `timeline_interest.json` (an account follows `dan`, with the limits of the parent chain) and `timeline_event_*.json` (a public `Create` and an `Announce` from `dan`, each one item for `alice`; a `Create` that is not public; a `Delete` of an object that no account has). The `timeline_event_*.json` files have the same method and path. The body selects the call.

Contract 1.4 (unit FP4-8) adds the inbound interactions with an account. Each timeline event has `actor_type`, `mentioned`, and `in_reply_to`, and each timeline decision has `notices`. `timeline_event_mention.json` is a public reply of `bob` (no account follows `bob`) to the published object of `alice` with a mention of `alice`: one item. `timeline_event_private_notice.json` is a message of `bob` to `alice` that is not public: no item, and one automatic notice in the locale of the account. `inbox_event_like.json` and `inbox_event_undo_like.json` are a `Like` of the published object of `alice` and its `Undo`; `inbox_event_no_object.json` is a `Like` of an object path that has no object. `inbox_event_ignored.json` is now a `Flag`, a type outside the set.

Contract 1.5 (unit FP4-6) adds the thread fetch: `context_tasks.json` (the `fetch_context` task `4` of `alice` for the object `notes/40`, with the limits of the reading settings) and `context_objects.json` (one public reply of `erin` in the thread: one new item). The Python test stores the item `notes/40` and the task before the calls.
Contract 1.6 (unit FP4-9) adds the outbound interactions. `note.json` is an own note of `alice`: a reply to a note of `carol` with a content warning, a hashtag, a mention with an actor (`carol`), and a mention that the sidecar must resolve (`dan`). `note_gone.json` is a deleted note (410). `interaction_tasks.json` gives a lease on four tasks: the `note` task of that note, the `delete_note` task of the deleted note, and a `like` and an `announce` of the note of `carol`. `interaction_task_ack.json` is the acknowledgement of the `note` task with the resolved actor of `dan`. The tests of these fixtures store the notes, one item, and the tasks before the call (`tests/test_federation_note_routes.py`); the other fixtures do not have these rows.
