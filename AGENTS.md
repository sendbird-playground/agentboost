# AgentBoost

AgentBoost is a public-candidate macOS menu-bar app extracted from the private `ai-system` harness.

## Boundaries

- Keep generated usage and settings files under `data/ai-usage/` ignored.
- Never commit prompt/session content, local usage ledgers, credentials, installed `.app` bundles, BEAM `_build/`, or local agent state folders.
- Run `scripts/privacy-scan.sh` before any public push.
- Keep app behavior testable through `--state-json`, `--check`, and product-quality checks before relying on manual menu-bar inspection.
- Do not add or upgrade third-party packages without a package-age/cooldown check.
- For menu-bar CPU/performance work, verify the rebuilt and restarted installed `AgentBoost.app` with live `ps`/`sample` evidence. Keep full lifetime backfills, recursive session scans, BEAM state rebuilds, and usage collectors off status-item clicks, timers, and active-agent startup paths; cache status glyphs, file scans, and process scans when they feed animation.

## Verification

Use focused checks before publication:

```bash
python3 -m unittest tests.test_ai_usage tests.test_growth_sidebar tests.test_macos_app tests.test_elixir_runtime_contract
(cd elixir/agentboost && mix test)
scripts/privacy-scan.sh
```
