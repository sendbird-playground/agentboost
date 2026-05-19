# PRD: AgentBoost

## Summary

AgentBoost is a local macOS menu bar app for the existing AI Usage Gamification system. It turns the dry CLI report into a visible daily growth surface: progress, badge inventory, representative badge selection, token-activity animation, new-achievement notifications, and daily missions.

The app should feel like a lightweight command center for the human operator, not a social leaderboard or token-consumption game. It runs locally, reads `data/ai-usage/events.jsonl` and `data/ai-usage/goals.json`, and never stores prompt text.

## Problem

The current gamification system works as a report, but it does not feel fun. The user has to run commands, read a text report, and infer what to do next. Badge progress is easy to miss, and there is no moment of reward when a new achievement becomes available.

## Goals

- Show growth progress in a persistent macOS menu bar app.
- Show earned, in-progress, and locked badges.
- Let the user pick one representative badge from Badge Inventory.
- Animate the menu bar indicator faster when current token usage is high.
- Notify when a badge becomes newly earned.
- Show daily and weekly missions that encourage healthy AI work habits.
- Keep data local and private.
- Reuse the existing `agentboost` ledger, goals, XP, badge, and report logic.

## Non-Goals

- No social leaderboard.
- No cloud sync.
- No prompt-content collection.
- No addictive daily streak pressure.
- No full external distribution packaging in the first implementation.

## Target Experience

The user opens:

```bash
agentboost
```

An `AI` item appears in the macOS menu bar. Its menu shows:

- Current level and XP.
- Workforce fitness score.
- Lifetime/month/week token progress.
- Animated token activity indicator for today's usage.
- Badge cards with status and progress.
- Badge Inventory menu for selecting the representative badge.
- Daily and weekly missions with evidence hints.
- A notification when a badge is newly earned.

The user can also run:

```bash
agentboost --state-json
agentboost --notify-only
agentboost --check
```

## Functional Requirements

1. The app must read local usage events and goals from the repo.
2. The app must show current XP, level, and workforce fitness score.
3. The app must show badge status and progress.
4. The app must persist which earned badges have already notified.
5. The app must expose badge inventory and persist a selected representative badge.
6. The app must expose current token activity and animate the menu bar indicator faster as usage rises.
7. The app must trigger macOS notifications for newly earned badges.
8. The app must show daily missions generated from current state.
9. The app must show weekly missions generated from current state.
10. The app must be runnable from `~/.local/bin` after `ai-system-install`.
11. The app must expose a JSON state mode for tests and automation.
12. The app must degrade gracefully if no usage data or no GUI display is available.

## Acceptance Criteria

- `agentboost --check` exits 0 and prints a concise health summary.
- `agentboost --state-json` emits level, XP, score, badges, badge inventory, representative badge, token activity, daily missions, weekly missions, and new achievements.
- `agentboost --notify-only` records newly earned badge and mission notifications without launching the menu bar app.
- Unit tests cover mission generation, badge notification dedupe, state JSON, and check mode.
- The installer links `bin/agentboost` into `~/.local/bin`.
