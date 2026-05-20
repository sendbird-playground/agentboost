# AgentBoost

AgentBoost is a local macOS menu-bar app that makes Claude and Codex usage visible without copying prompt content. It tracks local token metadata, missions, badges, recent activity, and optional notifications from files on the user's machine.

This repository is the public app extraction from the private `ai-system` harness. Generated usage files, session artifacts, local caches, and build outputs are intentionally ignored.

## Quick Start

```bash
bin/agentboost --state-json
bin/agentboost --check
bin/agentboost-build-app --open
```

Portable bundle smoke:

```bash
(cd elixir/agentboost && mix test && mix release --overwrite)
bin/agentboost-build-app \
  --portable-profile \
  --ad-hoc-sign \
  --beam-release-path elixir/agentboost/_build/dev/rel/agentboost \
  --beam-elixir-version 1.19.5 \
  --beam-otp-release 28 \
  --app-path dist/AgentBoost-Portable.app
bin/agentboost-quality-check --app-path dist/AgentBoost-Portable.app
```

## Privacy Boundary

AgentBoost reads local Claude/Codex usage metadata and writes derived local app state under `data/ai-usage/`. It is designed not to store full prompt text. The repository must not include generated `data/ai-usage/*.json*`, turn artifacts, local session histories, credentials, or installed app bundles.

Before publishing or pushing changes, run:

```bash
scripts/privacy-scan.sh
```

## Commands

- `bin/agentboost` opens or inspects the macOS app state.
- `bin/agentboost-build-app` builds `AgentBoost.app` with a shell bundle script, Swift AppKit host, and optional bundled Elixir/BEAM release.
- `bin/agentboost-quality-check` runs product-readiness checks on a built bundle.
- `bin/agentboost-usage-collect` imports local usage metadata.
- `bin/agentboost-usage-goal` manages local evidence-backed goals.
- `bin/agentboost-usage-report` prints local usage and mission reports.

## License

No open-source license has been selected yet. Public visibility does not grant reuse rights until a license is added.
