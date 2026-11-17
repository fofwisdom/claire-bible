# Wire contract fixtures of the federation of shared knowledge

These files are the wire contract of unit KF0 (`docs/implementation/KNOWLEDGE_FEDERATION.md`).

- `objects/`: one file for each object kind. Each file is a document that goes alone, with `@context`.
- `activities/`: one file for each activity kind.
- `internal/`: the calls of the internal routes between the sidecar and Claire (units KF1 and KF2), with the keys `request`, `status`, and `response`. `tests/test_knowledge_federation_internal_contract.py` makes the calls on a real internal app and writes the files; `sidecar/fedify/tests/knowledge_test.ts` reads them.
- `invalid/`: the important refusals. The keys are `validator` (`object` or `activity`), `error` (the error code), `path` (the field), and `value`.

Server A is `a.example` (the account actor and the server actor). Server B is `b.example` (the shared knowledge base). The addresses and the numbers are test values.

`tests/test_knowledge_federation_vocabulary.py` reads the files. The builders of `src/claire/knowledge_federation/vocabulary.py` make the same values: when the contract changes, `write_fixtures()` of the test module writes the files again. The sidecar tests can read the same files.
