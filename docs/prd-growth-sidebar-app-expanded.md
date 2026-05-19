# Expanded PRD: AgentBoost

## Product Shape

The first version has a native Swift/AppKit menu bar app backed by local Python helper commands. It presents an animated `AI` status item in the macOS menu bar; clicking it opens a compact menu with progress, token activity, representative badge, badge inventory, missions, badges, refresh, and quit. On macOS, new badge notifications use `osascript` to call Notification Center. If the GUI or notification system is unavailable, CLI modes still work.

This keeps the implementation aligned with the current repo: Python scripts for state, a script-built `.app` bundle, local files, symlink installation, and no extra dependency manager.

## Information Architecture

### Header

- Product name: `AgentBoost`
- Level, XP, and workforce fitness score.
- Representative badge.
- Import window or empty-state hint.

### Progress

- Today, week, month, and lifetime token totals.
- Claude/Codex split where useful.
- A reminder that token-only XP is capped.

### Token Activity

The state includes `token_activity` derived from today's local token rollup:

- `today_tokens`
- `intensity`: `idle`, `active`, `high`, or `surge`
- `animation_interval_seconds`
- `rocket_speed`

The native app draws a small rocket in the menu bar and moves it across a track. It stays still when usage is idle, then flies faster as token usage rises.

### Badges

Badge cards show:

- badge name
- status: `earned`, `in_progress`, or `locked`
- progress percentage
- evidence progress when available
- endorsement text or human skill text

Earned badges sort first, then in-progress, then locked. The sidebar should emphasize a small curated set rather than every possible badge at once.

### Badge Inventory

Badge Inventory is a submenu inside the native menu bar app. It lists earned, in-progress, and locked badges. Choosing a badge persists that badge id as `representative_badge_id` in the local notification/settings ledger. The header displays the selected representative badge; if no selection exists, AgentBoost defaults to the highest-value earned milestone badge, then the first earned badge, then the first in-progress badge.

### Daily Missions

Daily missions are generated from state, not hardcoded only by date. The first implementation should include up to five missions:

- Run `agentboost-usage-collect` if no source events exist.
- Define one verification goal if no goals exist.
- Advance the first open goal.
- Run a two-agent workflow when only one agent appears in the current data.
- Convert high-token work without outcome into a recovery or reusable-workflow goal.
- Create a reusable automation or checklist when verification goals exist but reuse goals do not.

### Weekly Missions

Weekly missions encourage one meaningful AI usage loop per work week. They are generated from current-week events, local workday activity, and workflow artifacts:

- Collect the weekly usage ledger if this week has no events.
- Build a workday AI rhythm capped to the five normal workdays.
- Review current skills and prompts through the AgentBoost artifact writer.
- Finish an existing open goal before creating more work.
- Run one Claude + Codex loop when only one agent appears in this week's data.
- Ship one reusable AI workflow artifact when verified work exists but reuse work does not.
- Recover one expensive AI session into a reusable lesson.

Each mission has:

- `mission_id`
- title
- reason
- status: `todo`, `active`, or `done`
- command hint
- evidence hint

### Notifications

Notifications fire when a badge changes into `earned` and has not already been notified. The system also sends deduped daily and weekly mission prompts to encourage meaningful AI usage without repeating the same prompt in the same period.

Notification state is stored locally:

```text
data/ai-usage/sidebar-notifications.json
```

The file records:

- badge id
- badge name
- mission prompt id
- daily or weekly period
- first notified timestamp
- status at notification time

The app must not notify repeatedly for the same earned badge or mission period unless the notification state is manually removed.

## Command-Line Surface

```bash
agentboost-build-app --open
open ~/Applications/AgentBoost.app
agentboost
agentboost --repo-root ~/agentboost
agentboost --state-json
agentboost --notify-only
agentboost --select-representative-badge <badge-id>
agentboost --check
agentboost --debug-window --no-notify
agentboost --no-system-notify
```

Behavior:

- `agentboost-build-app --open` compiles and launches the native Swift/AppKit menu bar `.app` bundle.
- `open ~/Applications/AgentBoost.app` launches the Finder-visible app bundle.
- Default `agentboost` opens the native menu bar app, building it first if needed.
- `--state-json` prints the state and exits.
- `--notify-only` sends/records achievement and daily/weekly mission notifications and exits.
- `--select-representative-badge <badge-id>` persists the representative badge and exits.
- `--check` validates readable data files, writable notification state directory, and GUI import availability.
- `--debug-window` opens the legacy Python debug window instead of the menu bar app.
- `--no-notify` applies to the explicit debug window path.
- `--no-system-notify` records notifications without calling macOS Notification Center. This is primarily for tests and headless checks.

## Data Flow

1. Read events via `read_events(default_events_file(repo_root))`.
2. Read goals via `load_goals(default_goals_file(repo_root))`.
3. Reuse `badge_statuses`, `total_xp`, `level_for_xp`, `workforce_fitness_score`, `rollup_events`, and `next_best_challenge`.
4. Generate sidebar state, badge inventory, representative badge, and token activity.
5. Compare earned badges and mission prompts with the notification ledger.
6. Send macOS notifications and persist new notification records.
7. Render the state in the menu bar app, allow Badge Inventory selection, and animate the status item from token activity.

## Error Handling

- Missing event file means empty events, not failure.
- Missing goal file means empty goals, not failure.
- Invalid notification JSON is treated as unreadable state and replaced only by explicit notification writes.
- GUI launch failure should print a clear error with a suggestion to use `--state-json` or `--notify-only`.
- `osascript` failure should not fail state generation or app launch.

## Visual Direction

The sidebar should be practical and dense, closer to a utility panel than a marketing page.

- Light background with clear section boundaries.
- Compact badge rows.
- Missions as checkable rows with short command hints.
- No decorative gradients or gamified clutter.
- Use a native drawn moving rocket for the menu bar indicator; avoid custom image assets for this standard-library version.

## Verification Plan

- Unit tests for state generation.
- Unit tests for daily/weekly mission generation across empty, open-goal, earned-badge, and high-token scenarios.
- Unit tests for notification dedupe.
- CLI smoke test for `--state-json`, `--notify-only`, and `--check`.
- Compile check for Python modules.
- Installer test updated to cover the new CLI symlink.

## Future Versions

- Package as a real `.app` bundle.
- Menu bar companion.
- Richer badge artwork.
- LaunchAgent scheduling for collection and notification checks.
- A SwiftUI version if the app needs native distribution.
