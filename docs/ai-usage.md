# AI Usage Gamification

This directory is the local output area for AI usage reports.

The implementation lives in:

- `agentboost/core.py`
- `agentboost/growth_sidebar.py`
- `bin/agentboost-build-app`
- `bin/agentboost`
- `bin/agentboost-usage-collect`
- `bin/agentboost-usage-goal`
- `bin/agentboost-usage-report`

Generated data:

- `data/ai-usage/events.jsonl`
- `data/ai-usage/goals.json`
- `data/ai-usage/sidebar-notifications.json`
- `docs/ai-usage/monthly/YYYY-MM.md`

These generated files are local/private by default and ignored by git. The collector stores usage metadata and token counts, but not full prompt content.

Menu bar app commands:

```bash
agentboost-build-app --open
agentboost
open ~/Applications/AgentBoost.app
agentboost --state-json
agentboost --notify-only
agentboost --check
```

The default `agentboost` command opens the native `AgentBoost.app` menu bar item instead of a Python window. The state JSON includes daily missions, weekly missions, badges, badge inventory, representative badge, newly earned achievements, token activity, memory monitor state at the 80% alert threshold, XP, level, and workforce fitness. `--select-representative-badge <badge-id>` persists the badge shown as representative. `--notify-only` sends deduped achievement notifications and daily/weekly mission encouragement prompts. `--memory-check` sends a once-per-day local alert when system memory is at or above the threshold.

Product specs:

- `docs/prd-growth-sidebar-app.md` covers the implemented MVP.
- `docs/prd-agentboost-vnext.md` covers the requested vNext features: last-1-minute rocket activity, inactivity nudges, tips, caffeinate, settings toggles, Sunday weekly reset, and community tips.
