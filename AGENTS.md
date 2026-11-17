# AGENTS.md

## Agent rules

An agent must not give instructions to an agent of another vendor. For example, an agent of one company does not write tasks, notes, or messages for an agent of a different company. An agent can use the subagents of its own product.

## Documentation folders

An old design document can describe a state that is not true now. These principles help an agent find the current state quickly and understand it correctly. They give the intent, not a procedure: use your judgment for each case.

- `docs/design/` tells what is intended, and why.
  - Architecture that is not fully implemented.
  - Design principles of work that is implemented.
- `docs/implementation/` tells what exists now.
  - Architecture that is implemented.
  - Term definitions.
  - The command manual for agents that use the CLI.
- `docs/ideas/` keeps ideas that have no design. Do not implement from it; an idea enters `docs/design/` only after an owner decision.

When you read, use each folder for its purpose. When you write, keep each folder true to its purpose.
