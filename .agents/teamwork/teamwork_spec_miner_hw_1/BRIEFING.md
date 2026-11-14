# BRIEFING — 2026-11-14T12:42:00Z

## Mission
Investigate and specify all detailed requirements and edge cases for HW-4 (Local Graph & Hover Connections) and HW-5 (Entity Marks in Reader Text & Server Aliases), and identify documentation updates.

## 🔒 My Identity
- Archetype: teamwork_spec_miner
- Roles: Specification Investigator (Design & Contracts)
- Working directory: /home/fow/Projects/claire-bible
- State folder: /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_spec_miner_hw_1
- Original parent: 21ab5432-86d2-4f40-befa-f9c2b7b06ba1
- Milestone: HW-4 & HW-5 specification mining

## 🔒 Key Constraints
- Read-only: Do not implement code changes.
- ASD-STE100 compliance for documentation and comments.
- Do not hard-wrap code lines.
- Follow 5-component handoff protocol in handoff.md.

## Current Parent
- Conversation ID: 21ab5432-86d2-4f40-befa-f9c2b7b06ba1
- Updated: 2026-11-14T12:42:00Z

## Task Summary
- **What to build**: Specification discovery for HW-4 (Local Graph and Hover Connections) and HW-5 (Entity Marks in Reader Text & Server Aliases).
- **Success criteria**: Exhaustive identification of features, edge cases, contracts, UI rules, algorithm constraints, and documentation delta.
- **Interface contracts**: docs/design/HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md, docs/implementation/API_HTTP_CONTRACT.md, docs/implementation/openapi.yaml.
- **Code layout**: .agents/teamwork/ holds only metadata.

## Key Decisions Made
- Prioritize authoritative design specifications in docs/design/HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md and commits HW-1, HW-2, HW-3.
- Map exact line grammar: solid for facts / non-provisional relations, dashed for provisional relations, thin gray for document-to-entity lines.
- Map exact mark rules: CSS Custom Highlight API, no DOM alteration, ASCII borders, case-sensitive 2-3 char acronyms, collision discard, longest first.
- Map documentation delta across IMPLEMENTATION_STATUS.md, GLOSSARY.md, API_HTTP_CONTRACT.md, and openapi.yaml.

## Artifact Index
- DISPATCH.md — Dispatch assignment and instructions
- progress.md — Liveness heartbeat and step tracking
- handoff.md — 5-component handoff report
