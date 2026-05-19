# AgentBoost Public Boundary

## Product Code Included

- `agentboost/`: local usage collection, mission/badge state, macOS bundle builder, and product-quality checks.
- `macos/agentboost/AgentBoostApp.swift`: native AppKit menu-bar host.
- `elixir/agentboost/`: bundled BEAM runtime candidate for portable state generation.
- `bin/agentboost*`: local development and app helper commands.
- `tests/`: focused contract and product-readiness tests.
- `docs/`: product PRDs and privacy notes.

## Private Surfaces Excluded

- `data/ai-usage/*.json*`
- `data/turn-artifacts/`
- `identity/drafts/`
- `.codex/`, `.claude/`, `.agents/`
- installed `.app` bundles
- `dist/`, `build/`, Elixir `_build/`, Elixir `deps/`
- private `ai-system` skills, adapters, workflow state, and memory artifacts

## Publication Gate

The repo can be made public only after:

- no generated/private data files are present,
- `scripts/privacy-scan.sh` passes,
- tests and build smoke checks pass or documented blockers are explicit,
- `git status --short --ignored` shows ignored private paths are not tracked,
- the target GitHub repository name and visibility are confirmed.
