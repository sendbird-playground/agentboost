# PRD: AgentBoost vNext

Status: Draft
Owner: Peter Lyoo
Created: 2026-05-06
Source request: AgentBoost sidebar app with live AI usage animation, achievement notifications, badge inventory, inactivity nudges, daily AI tips, caffeinate mode, settings toggles, Sunday weekly resets, and community tip sharing.

## 1. Summary

AgentBoost is a local macOS menu bar/sidebar companion that makes AI-agent usage visible and fun. It shows live Claude/Codex usage, animates a rocket based on recent token activity, celebrates achievements, lets the user pick a representative badge, and nudges the user back into AI-assisted work when they have been inactive.

The product should feel like a playful AI work cockpit, not a generic productivity nag. It should encourage useful AI usage, avoid shame, and keep local usage/private workflow data on the user machine. Cloud or server features should be limited to public AI tips and community-submitted tips.

## 2. Goals

- Show current AI-agent usage in a persistent macOS menu bar/sidebar surface.
- Make recent token activity obvious through a rocket animation and human-readable number.
- Notify the user when they earn achievements.
- Provide a Badge Inventory menu and representative badge selection.
- Encourage the user to restart AI-assisted work after 30 minutes of no usage.
- Fetch daily AI tips or newly released AI feature tips from a server-backed static file.
- Keep the Mac awake while meaningful AI usage is active.
- Let the user disable or tune notifications from a Settings menu.
- Reset weekly missions and weekly progress every Sunday in local time.
- Support a lightweight community tip system where 10+ upvoted tips can become daily tips.
- Add optional fun mechanics that boost AI usage without pushing wasteful token burn.

## 3. Non-Goals

- Do not collect prompt content.
- Do not require a full backend for the first server-tip version; a Git-hosted static JSON file is enough.
- Do not make raw token burning the only path to rewards.
- Do not send shame-based inactivity notifications.
- Do not prevent system sleep when there is no active AI usage.
- Do not build a public social network in the local-app milestone.

## 4. Target Experience

The user launches `AgentBoost.app`. A menu bar item appears as a compact AI cockpit. The status item shows a rocket and a recent token number such as `3.2K`, `845K`, or `1.4M`.

When no AI usage happened in the last minute, the rocket waits on the launch pad with no fire. When usage is moderate, the rocket moves steadily. When usage is high, the rocket flies faster and higher with a stronger flame. Clicking the item opens the sidebar/menu with progress, missions, Badge Inventory, representative badge, latest tip, Settings, and quit.

## 5. Core Requirements

### 5.1 Live AI Usage Sidebar And Rocket Animation

The app must compute token usage for the last 1 minute and expose it as `recent_token_activity`.

State fields:

- `last_1m_tokens`
- `display_tokens`, formatted as `0`, `980`, `1K`, `12.4K`, `1.2M`, `3.4B`
- `activity_level`: `idle`, `moderate`, or `high`
- `rocket_state`: `waiting`, `flying`, or `surging`
- `rocket_speed`
- `rocket_altitude`
- `has_flame`

Behavior:

- `idle`: no last-1-minute usage; rocket waits for launch; no flame.
- `moderate`: some usage under the high threshold; rocket moves normally.
- `high`: high last-1-minute usage; rocket moves faster and flies higher.
- Token number appears next to or inside the animation area.
- Existing daily/weekly/lifetime rollups remain available in the menu.

Thresholds should be configurable later. Initial defaults:

- `idle`: `0` tokens in last 1 minute.
- `moderate`: `1` to `49,999` tokens in last 1 minute.
- `high`: `50,000+` tokens in last 1 minute.

### 5.2 Achievement Notifications

When the user earns a new badge or meaningful achievement, AgentBoost must send a macOS notification once.

Notification examples:

- `Badge earned: Two-Agent Day`
- `Achievement unlocked: You made Claude and Codex work together today.`

Rules:

- Notify once per earned badge id unless local notification state is cleared.
- Keep notification records in the local notification/settings ledger.
- Achievement notifications respect the global notification toggle and quiet hours.

### 5.3 Badge Inventory And Representative Badge

The menu/sidebar must include `Badge Inventory`.

Inventory behavior:

- Show earned, in-progress, and locked badges.
- Show status, progress percent, and short endorsement text.
- Let the user select one badge as the representative badge/title.
- Persist the selected badge id locally.
- Display the selected representative badge in the sidebar header.
- If no badge is selected, default to the highest-value earned milestone badge, then first earned badge, then first in-progress badge.

### 5.4 Inactivity Nudges

If there is no AI usage for more than 30 minutes during active work hours, AgentBoost should send a funny nudge notification.

Examples:

- `Your AI agents are staring at an empty task board. Give them a job?`
- `The rocket is on the pad. Time to launch an agent?`
- `Claude and Codex are available for delegation. What can they take off your plate?`

Rules:

- Trigger only when last AI usage is older than 30 minutes.
- Deduplicate nudges, for example no more than once every 60 minutes.
- Respect notification settings, quiet hours, and Do Not Disturb where detectable.
- Do not nudge outside configured work hours unless the user opts in.

### 5.5 Daily AI Tips From Server Or Git Static File

AgentBoost should fetch the latest AI tip from a public, versioned source. A static Git-hosted JSON file is enough for the first version.

Tip source fields:

- `tip_id`
- `title`
- `body`
- `url`
- `date`
- `tags`
- `source`
- `min_client_version`
- `is_release_feature`

Behavior:

- Fetch tips periodically with caching.
- Send one daily tip notification when a tip is available for the local date.
- Tips can include newly released AI features, workflow ideas, or short examples.
- If fetching fails, use the last cached tip and do not show an error notification.
- Do not send the same `tip_id` more than once.

### 5.6 Caffeinate During AI Usage

AgentBoost should keep the Mac awake while meaningful AI usage is active.

Behavior:

- Start or refresh caffeinate mode when recent AI usage is detected.
- Stop caffeinate mode after usage has been inactive for a grace period, default 5 minutes.
- Expose a Settings toggle: `Keep Mac awake during AI usage`.
- Use macOS-native mechanisms, for example the `caffeinate` command or `IOPMAssertion`, without requiring admin privileges.
- Fail safely: if caffeinate cannot start, log locally and continue app operation.

### 5.7 Settings Menu And Notification Toggles

The sidebar/menu must include `Settings`.

Settings should include:

- Enable all notifications.
- Achievement notifications.
- Mission reminders.
- Inactivity nudges.
- Daily AI tips.
- Community tip announcements.
- Caffeinate during AI usage.
- Quiet hours.
- Workday start/end time.

The notification toggles must control whether alarms/notifications are sent. Disabling notifications must not disable local state collection or badge calculation.

### 5.8 Sunday Weekly Reset

Weekly missions, weekly token rollups, weekly tip leaderboards, and weekly usage summaries must reset every Sunday based on local time.

Rules:

- Week starts Sunday `00:00` local time.
- Week ends Saturday `23:59:59` local time.
- Lifetime badges and lifetime token counts never reset.
- Sunday reset should produce a fresh weekly mission set and optional weekly recap.

### 5.9 Community Tips And Upvotes

AgentBoost should support a lightweight community for sharing AI tips.

First implementation options:

- GitHub Discussions or Issues as the community surface.
- Static generated JSON file for app consumption.
- Manual or automated aggregation of tips and upvote counts.

Promotion rule:

- A tip with 10+ upvotes becomes eligible to become a daily tip.
- Eligible tips can be announced every workday morning at 9:00 AM local time.
- Workdays are Monday through Friday by default.

Moderation and quality rules:

- Tips must not include private prompts, secrets, or company-sensitive data.
- Tips should be short, actionable, and source-linked when possible.
- Duplicate or low-quality tips can be excluded even if upvoted.

### 5.10 Extra Fun Features

Additional fun mechanics should increase useful AI adoption without encouraging waste.

Recommended ideas:

- `Launch Combo`: bonus XP for using AI on multiple meaningful tasks in a day with evidence.
- `Agent Crew Board`: show Claude, Codex, and future agents as crew members with current job status.
- `Mission Roulette`: one optional suggested AI task, such as "ask Codex to review one diff" or "ask Claude to critique one plan."
- `Recovery Boost`: if a high-token session has no outcome, prompt the user to turn it into a reusable lesson.
- `Friday Launch Recap`: quick weekly recap on Friday afternoon with wins, badges, tips used, and next week mission.

## 6. Information Architecture

### Menu Bar Status Item

- Rocket animation.
- Human-readable last-1-minute token count.
- Optional representative badge icon/title if space allows.

### Sidebar/Menu Sections

- Header: AgentBoost, representative badge, level, XP.
- Live Activity: last-1-minute tokens, today tokens, rocket state.
- Daily Mission.
- Weekly Mission.
- Badge Inventory.
- Latest AI Tip.
- Settings.
- Refresh and Quit.

## 7. Data Model

Local files:

- `data/ai-usage/events.jsonl`: normalized local usage events.
- `data/ai-usage/goals.json`: goals and completion evidence.
- `data/ai-usage/sidebar-notifications.json`: notification ledger and representative badge id.
- `data/ai-usage/settings.json`: notification toggles, work hours, quiet hours, caffeinate setting.
- `data/ai-usage/tips-cache.json`: cached static tips and last announced tip ids.

Possible remote/static files:

- `tips/daily.json`: daily tip feed.
- `tips/community.json`: eligible community tips.
- `tips/upvotes.json`: generated summary of community votes.

## 8. Scheduling

AgentBoost should avoid requiring a daemon for the MVP, but scheduled features need a reliable path.

Options:

1. App-resident timers while AgentBoost is running.
2. LaunchAgent for periodic collection, inactivity checks, and daily 9:00 AM tip notifications.
3. Manual CLI commands for testing and fallback.

Recommended path:

- Use app-resident timers for live animation and settings.
- Add a LaunchAgent later for daily tips and inactivity nudges if always-on behavior is required.
- Keep all scheduled actions callable from CLI for testability.

## 9. Privacy And Safety

- Do not collect or upload prompt content.
- Do not upload local usage events.
- Fetch only public tips from the server/static source.
- Community tips must be user-submitted public content.
- Keep notification and setting state local.
- Do not show sensitive project names in notifications unless the user opts in.

## 10. Command-Line Surface

Existing commands remain:

```bash
agentboost
agentboost --state-json
agentboost --notify-only
agentboost --select-representative-badge <badge-id>
agentboost --check
agentboost-build-app --open
```

Proposed vNext commands:

```bash
agentboost --settings-json
agentboost --set-notification-enabled false
agentboost --set-notification-category inactivity:false
agentboost --fetch-tips
agentboost --tip-notify-only
agentboost --inactivity-check
agentboost --caffeinate-check
```

## 11. Acceptance Criteria

### Live Usage And Animation

- State JSON includes `recent_token_activity`.
- `last_1m_tokens=0` produces `rocket_state=waiting`, `has_flame=false`, and display token `0`.
- Moderate last-1-minute usage produces normal rocket speed.
- High last-1-minute usage produces faster speed and higher altitude.
- Token display formats `1,000` as `1K`.

### Achievements

- Earning a new badge writes one notification record and sends one notification.
- Running notification check again does not repeat the same achievement notification.
- Disabled achievement notifications suppress delivery but do not remove achievement state.

### Badge Inventory

- Sidebar/menu includes Badge Inventory.
- User can select a representative badge.
- Representative badge persists after app refresh/relaunch.

### Inactivity Nudges

- No AI usage for more than 30 minutes during work hours triggers one funny nudge.
- A second check inside the cooldown does not send another nudge.
- Disabling inactivity notifications suppresses nudges.

### Tips

- App can read a static tips JSON file.
- A new daily tip sends one notification.
- Already-announced tips are deduped.
- Community tips with 10+ upvotes are eligible for workday 9:00 AM announcement.

### Caffeinate

- Recent AI usage starts or refreshes caffeinate mode.
- Inactivity for the grace period stops caffeinate mode.
- The setting can disable caffeinate behavior.

### Weekly Reset

- Weekly state uses Sunday-start local weeks.
- Sunday local midnight starts a fresh weekly mission period.
- Lifetime counters and lifetime badges remain unchanged.

## 12. Implementation Plan

### Phase 1: Local State And Settings

- Add `settings.json`.
- Add notification category toggles.
- Add Sunday-start weekly helpers.
- Add `recent_token_activity` based on the last 1 minute of events.
- Add human-readable token formatting.

### Phase 2: Native Sidebar/Menu UX

- Add Settings submenu.
- Show token number next to rocket animation.
- Add representative badge to header/title area.
- Keep Badge Inventory selection.

### Phase 3: Notification Engines

- Add inactivity check.
- Add daily tip notification.
- Add category-specific notification suppression.
- Add CLI check modes for every notification path.

### Phase 4: Tips And Community

- Define static tip JSON schema.
- Fetch/cache static Git-hosted tip file.
- Add community tip ingestion from GitHub Discussions/Issues or generated JSON.
- Promote `10+` upvote tips into daily tip candidates.

### Phase 5: Caffeinate

- Add caffeinate setting.
- Add active-usage detector.
- Start/stop caffeinate safely.
- Add tests with command execution mocked at the boundary.

## 13. Open Questions

- Should inactivity nudges be work-hours only by default, and what are the default work hours?
- Should daily tips announce only Monday through Friday, or also on weekends when AI usage is active?
- What remote Git/static file should host official tips?
- Should community upvotes come from GitHub reactions, a small web app, or a static generated file?
- What exact thresholds should separate moderate vs high last-1-minute token usage?
