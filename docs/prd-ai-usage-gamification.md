# PRD: AI Usage Gamification

Source: `docs/plans/2026-05-06-ai-usage-gamification-brainstorm.md`
Status: Draft
Owner: Peter Lyoo
Created: 2026-05-06

## 1. Summary

AI Usage Gamification turns Claude and Codex usage into a local AI-native workforce fitness system. It tracks daily, weekly, monthly, and lifetime token usage across both agents, connects usage to concrete goals, and awards badges or endorsement medals when usage demonstrates meaningful AI adoption.

The product should make AI work feel visible, measurable, and motivating without encouraging wasteful token consumption. Tokens are the progress meter, but high-status rewards require evidence of completed goals, verified work, reusable artifacts, or improved agent collaboration.

The deeper purpose is to nurture the human operator. The system should help the user build better taste, judgment, delegation skill, verification discipline, and confidence working with AI agents. It should behave more like a coach and work journal than a game that pushes endless usage.

## 2. Problem

Peter uses Claude, Codex, and orchestrated agent workflows across many local repos, but usage currently disappears into separate tool histories. There is no single place to answer:

- How much Claude and Codex usage happened today, this week, this month, and lifetime?
- Which projects and workflows consumed that usage?
- Did the usage lead to completed goals, verified work, or reusable process improvements?
- How close is the user to major AI adoption milestones like 1B, 10B, and 100B tokens?
- Which habits would make the personal AI workforce more effective?

Without this visibility, token usage is hard to celebrate, optimize, or connect to behavior change.

## 3. Product Goals

- Track Claude and Codex token usage separately and together.
- Show daily, weekly, monthly, and lifetime rollups.
- Support AI-utilization goals with evidence-backed completion.
- Award milestone badges at 1B, 10B, and 100B total lifetime tokens.
- Add daily and weekly behavior badges that reward healthy agent workflows.
- Produce local reports that fit the current `ai-system` repo shape.
- Encourage an AI-friendly workforce mindset: agents as collaborators with specialties, outcomes, trust signals, and improvement loops.
- Nurture human growth through reflection prompts, skill paths, healthy challenge pacing, and evidence-backed progress.
- Make the next useful action obvious without making the user feel judged for low-usage days.

## 4. Non-Goals

- Do not build a full web app in the first release.
- Do not require cloud sync or team-wide leaderboards for the MVP.
- Do not treat raw token burn as sufficient for the best rewards.
- Do not infer confidential content or store full prompts in the normalized usage ledger.
- Do not replace existing Claude or Codex histories.
- Do not use addictive streak pressure, shame language, or dark-pattern mechanics.
- Do not optimize for maximum token spend when a smaller, clearer AI interaction would produce the same outcome.

## 5. Users And Personas

### Primary User: AI-Native Operator

Uses Claude and Codex every day for coding, product thinking, debugging, review, workflow design, and documentation. Wants clear usage totals, meaningful achievement tracking, and evidence that AI work is becoming more effective.

### Secondary User: Workflow Maintainer

Maintains `ai-system` as a durable operating system for human plus AI collaboration. Wants usage reporting to connect to task closeouts, protocol health, verification discipline, and reusable workflow artifacts.

### Future User: Team AI Enablement Lead

May want team visibility later, but the MVP should stay local and personal until data semantics, privacy, and anti-gaming rules are stable.

## 6. Product Principles

### 6.1 Human Nurture Principles

The product should grow the human, not just score the tools.

- Agency: the user chooses goals and can pause, skip, or reset without penalty.
- Competence: feedback explains what improved and what skill was practiced.
- Judgment: reports distinguish useful AI leverage from expensive thrashing.
- Reflection: important badges and monthly recaps ask what the user learned.
- Trust: progress is tied to evidence, verification, and honest outcome reporting.
- Sustainability: the product rewards steady practice and reusable assets, not unsustainable usage spikes.
- Psychological safety: the product should use neutral, factual language for missed goals, failed experiments, or low-usage periods.

### 6.2 Gamification Principles

Gamification should turn good AI-working habits into visible progress.

- Reward loops must point back to real work outcomes.
- Milestones should feel earned because they combine usage, goals, and evidence.
- Streaks should be recoverable and should not punish rest days.
- XP should reward behavior quality more than token volume.
- Badges should communicate identity: operator, reviewer, automator, orchestrator.
- The system should show "next best challenge" rather than a long list of chores.
- The product should celebrate learning moments, including useful failures and circuit breakers.

## 7. Source Data

### Claude Data

Claude Code usage is tracked from local project JSONL files under `~/.claude/projects/**/*.jsonl`, matching the ccusage source model.

Relevant fields:

- `sessionId`
- `timestamp`
- `cwd`
- `requestId`
- `message.id`
- `message.model`
- `message.usage.input_tokens`
- `message.usage.cache_creation_input_tokens`
- `message.usage.cache_read_input_tokens`
- `message.usage.output_tokens`

Legacy Claude session metadata may exist under `~/.claude/usage-data/session-meta/*.json`. The collector treats it as a fallback only when project JSONL usage is unavailable.

Claude outcome facets exist under `~/.claude/usage-data/facets/*.json`.

Relevant fields:

- `session_id`
- `underlying_goal`
- `goal_categories`
- `outcome`
- `primary_success`
- `claude_helpfulness`
- `friction_counts`
- `brief_summary`

### Codex Data

Codex session logs exist under `~/.codex/sessions/**/*.jsonl`.

Relevant event:

- `event_msg` with `payload.type = "token_count"`

Relevant token fields:

- `last_token_usage.input_tokens`
- `last_token_usage.cached_input_tokens`
- `last_token_usage.cache_read_input_tokens`
- `last_token_usage.output_tokens`
- `last_token_usage.reasoning_output_tokens`
- `last_token_usage.total_tokens`
- `total_token_usage.*`

The collector should use per-turn deltas when available, synthesize missing Codex `total_tokens` from input plus output, and avoid double counting cumulative totals or zero-delta events.

### AI-System Data

The current repo contains workflow evidence that can enrich rewards:

- `skill/task-log.md` for task closeouts.
- `skill/review-state.md` for workflow health.
- `skill/review-log.md` for periodic meta-review history.
- `docs/plans/*.md` for planning artifacts.
- Future generated monthly reports under `docs/ai-usage/monthly/`.

## 8. MVP Scope

The MVP is a local ledger and report system.

### 8.1 Usage Collector

Command: `bin/agentboost-usage-collect`

Responsibilities:

- Read Claude Code project JSONL usage, with legacy session metadata as a fallback.
- Read Codex session logs.
- Normalize usage into `data/ai-usage/events.jsonl`.
- Deduplicate previously imported records.
- Preserve enough metadata for reporting without storing full prompt content.
- Track source provenance for every record.

### 8.2 Usage Report

Command: `bin/agentboost-usage-report`

Responsibilities:

- Print daily, weekly, monthly, and lifetime rollups.
- Show Claude, Codex, and combined totals.
- Show input, output, cached input, reasoning output, and total tokens when available.
- Show active sessions, top projects, and agent-assisted task counts.
- Show progress toward 1B, 10B, and 100B lifetime milestones.
- Optionally generate Markdown reports.

### 8.3 Goal Ledger

File: `data/ai-usage/goals.json`

Responsibilities:

- Store user-defined AI utilization goals.
- Track status: `planned`, `in_progress`, `completed`, `abandoned`.
- Link completed goals to evidence such as task closeouts, generated reports, git diffs, PRs, or docs.
- Award XP or badges only when evidence is attached.
- Store an optional short reflection on what the user learned or changed.

### 8.4 Monthly Recap

Path: `docs/ai-usage/monthly/YYYY-MM.md`

Responsibilities:

- Summarize monthly usage.
- List earned badges and progress toward milestone badges.
- Identify top workflows and projects.
- Highlight useful agent collaboration patterns.
- Call out waste signals, friction, or missing evidence.
- Recommend the next month goal.
- Include a human growth section with skill progress, reflection prompts, and one suggested next challenge.

## 9. Functional Requirements

### Usage Tracking

- The system must track Claude token usage.
- The system must track Codex token usage.
- The system must compute combined usage.
- The system must support daily rollups.
- The system must support weekly rollups.
- The system must support monthly rollups.
- The system must support lifetime rollups.
- The system must expose Claude vs Codex split for every rollup period.
- The system must keep cached input tokens separate when available.
- The system must avoid double counting cumulative Codex totals.

### Goals

- The system must allow AI-utilization goals to be defined.
- The system must allow goals to be completed.
- The system must require evidence links for XP-bearing completions.
- The system must distinguish adoption, collaboration, verification, reuse, and workforce goals.
- The system should show current goal progress in reports.
- The system should allow a completed goal to include a short human reflection.
- The system should recommend one next goal based on recent behavior, not a generic checklist.

### Badges And Endorsements

- The system must support lifetime milestone badges at 1B, 10B, and 100B total tokens.
- The system must support behavior badges for daily and weekly agent workflows.
- The system must store endorsement text for high-status medals.
- The system must prevent high-status medals from being awarded by raw token volume alone.
- The system should distinguish earned, in-progress, and locked badges.
- The system must explain why a badge matters in terms of a human skill or work habit.
- The system should support recoverable streaks or streak freezes so the user is not punished for deliberate rest or deep non-AI work.

### Nurture Mechanics

- The system must surface at least one "human skill practiced" signal in monthly reports.
- The system should classify goals and badges into growth paths such as Reviewer, Builder, Automator, and Orchestrator.
- The system should identify one behavior to keep, one behavior to improve, and one behavior to stop in each monthly recap.
- The system should flag high-token, low-outcome periods as learning opportunities rather than failures.
- The system should show a "next best challenge" that is small enough to act on in the next week.

### Game Economy

- The system must support XP as a derived value from usage, goal completion, evidence quality, and reflection.
- The system must cap token-only XP so raw usage cannot dominate the economy.
- The system should award bonus XP for reusable artifacts, verified completions, cross-agent collaboration, and circuit-breaker discipline.
- The system should keep medal unlock rules transparent.

### Reports

- The system must print a concise terminal report.
- The system should generate monthly Markdown reports.
- Reports must include usage totals, agent split, milestone progress, goals, badges, and quality overlays.
- Reports should highlight sessions or periods with high token usage and no linked outcome.

### Local-First Operation

- The system must work without cloud services.
- The system must read local Claude and Codex data paths.
- The system must keep generated state under repo-local `data/ai-usage/` and `docs/ai-usage/`.
- The system must tolerate missing Claude or Codex data.

## 10. Core Progression Loop

The game loop should be simple enough to run from local reports.

1. Use Claude, Codex, or both on meaningful work.
2. Collect usage and workflow evidence.
3. Attach work to a goal or leave it as passive usage.
4. Complete goals with evidence and optional reflection.
5. Earn XP, behavior badges, and milestone progress.
6. Review the monthly council recap.
7. Choose one next challenge for the next cycle.

The loop should produce momentum without demanding daily interaction. A useful week may have fewer tokens and stronger evidence than a busy week with no outcome.

## 11. XP And Level Model

XP is a motivational layer, not the source of truth. The source of truth remains usage, goals, evidence, and outcomes.

### XP Inputs

- Base usage XP: capped contribution from Claude and Codex token usage.
- Goal XP: awarded when a goal is completed with evidence.
- Verification XP: awarded for command-backed verification, review, or test evidence.
- Reuse XP: awarded when work creates a reusable script, doc, skill, checklist, or protocol.
- Collaboration XP: awarded when Claude and Codex play distinct roles in the same goal.
- Reflection XP: awarded for concise notes on what the human learned or will change.
- Recovery XP: awarded when a high-friction or high-token session leads to a concrete workflow improvement.
- Mission XP: awarded when generated daily or weekly missions are auto-checked as `done`.

### Level Progression

The AgentBoost state contract exposes numeric levels from 1 to 50. Each level has a required XP cost to clear that level; current XP is applied from level 1 upward. The native header should show `LV n · current/required XP`, and state JSON should expose `level`, `level_label`, `level_progress`, and `xp_breakdown`.

The canonical level-up table starts with:

- Level 1 requires 15 XP.
- Level 2 requires 34 XP.
- Level 3 requires 57 XP.
- Level 4 requires 92 XP.
- Level 5 requires 135 XP.

The table continues through the image-provided cap:

- Level 35 requires 174,216 XP.
- Level 50 requires 709,716 XP.

### Legacy Level Names

- Level 1: `Prompt Apprentice`
- Level 2: `Daily Operator`
- Level 3: `Verified Builder`
- Level 4: `Agent Delegator`
- Level 5: `Workflow Automator`
- Level 6: `AI Workforce Lead`

Levels should unlock richer report sections or goal templates, not artificial feature locks that make the tool less useful.

## 12. Badge Model

### Lifetime Token Medals

#### 1B Total Tokens: `Billion-Token Operator`

Endorsement: "Uses AI agents as daily working partners, not occasional search boxes."

Unlock conditions:

- Lifetime total tokens >= 1,000,000,000.
- At least 10 logged agent-assisted goals.

#### 10B Total Tokens: `Agent Guild Builder`

Endorsement: "Builds repeatable AI workflows that other agents and humans can reuse."

Unlock conditions:

- Lifetime total tokens >= 10,000,000,000.
- At least 25 reusable artifacts, such as plans, scripts, docs, prompts, or review checklists.

#### 100B Total Tokens: `AI-Native Workforce Architect`

Endorsement: "Operates a mature AI workforce loop: plan, delegate, verify, learn, and improve the system."

Unlock conditions:

- Lifetime total tokens >= 100,000,000,000.
- Sustained verification and closeout discipline.
- Evidence of mature agent orchestration or workforce practice over multiple months.

### Behavior Badges

- `Two-Agent Day`: Claude and Codex were both used on the same local date.
- `Verifier Streak`: 5 consecutive completed tasks include explicit verification evidence.
- `Rival Review`: one agent drafted and another agent critiqued, reviewed, or tested the result.
- `Automation Dividend`: repeated manual work was converted into a script, skill, doc, or protocol.
- `Circuit Breaker Save`: a broken assumption was detected and the work was re-planned.
- `Low Waste Win`: a meaningful goal was completed with lower-than-usual token usage.

### Human Growth Badges

- `Better Question`: the user rewrote a vague request into a clearer task or brief.
- `Taste Upgrade`: the user rejected a plausible AI answer and improved the standard with evidence.
- `Delegation Rep`: the user split a task into clean agent-owned workstreams.
- `Review Muscle`: the user used one agent to review another agent's output.
- `Calm Debugger`: the user paused on a broken assumption and investigated root cause before patching.
- `Craft Keeper`: the user converted a one-off lesson into a durable doc, skill, or rule.

### Streak Design

Streaks should reinforce rhythm without becoming coercive.

- Weekly streaks are preferred over daily streaks.
- A "deep work skip" can preserve a streak when the user deliberately did non-AI work.
- A "recovery mark" can preserve a streak when a failed session creates a useful lesson.
- Streaks should reset without shame language.
- Reports should prioritize skill progress over streak length.

## 13. Goal Taxonomy

### Adoption Goals

Example: "Use an agent on 5 meaningful tasks this week."

Purpose: make AI assistance habitual.

### Collaboration Goals

Example: "Use Claude for product critique and Codex for implementation on 3 tasks."

Purpose: use each agent where it is strongest.

### Verification Goals

Example: "Every completed coding task has command-backed verification."

Purpose: preserve trust in AI-assisted work.

### Reuse Goals

Example: "Create 2 reusable scripts, skills, or docs from repeated work."

Purpose: convert recurring effort into durable workflow assets.

### Workforce Goals

Example: "Delegate one independent workstream and integrate the result."

Purpose: practice AI workforce management, not just single-agent prompting.

### Reflection Goals

Example: "Write one short note about what changed in your judgment after an AI-assisted task."

Purpose: make learning explicit so the human improves alongside the tooling.

### Recovery Goals

Example: "Turn one high-friction AI session into a checklist, prompt, or guardrail."

Purpose: convert frustration into system improvement.

## 14. AI-Friendly Workforce Layer

The product should make the AI workforce visible through a few simple concepts.

### Agent Roster

Shows Claude, Codex, and future orchestrated agents as collaborators with:

- specialties
- recent contribution counts
- linked goals
- trust signals
- friction signals

### Workforce Fitness Score

A composite score for personal AI workforce quality.

Inputs:

- usage consistency
- completed goal rate
- verification integrity
- reusable artifacts created
- low-rework sessions
- balanced Claude/Codex collaboration

The score should be advisory and trend-based, not a punitive grade.

### Guild Paths

Longer-term progress paths:

- Reviewer
- Builder
- Researcher
- Automator
- Orchestrator

Each path can have its own goals, badges, and report sections.

Each path should describe the human capability being developed:

- Reviewer: sharper critique, risk detection, and taste.
- Builder: clearer task framing and implementation follow-through.
- Researcher: better source judgment and synthesis.
- Automator: recognizing repeated work and creating leverage.
- Orchestrator: assigning roles, integrating outputs, and preserving accountability.

### Monthly Council

A generated monthly section that summarizes:

- what the AI workforce helped with
- where agents wasted time
- which collaboration pattern improved
- which habit should be improved next
- which human skill grew
- which next challenge is small, concrete, and timely

## 15. Human Nurture UX

The nurturing layer should appear in reports without becoming verbose.

### Daily Tone

Daily reports should answer:

- What did I use?
- What did it help with?
- Is there one small action worth taking next?

Low-usage days should be reported neutrally. Example: "No AI goals completed today. Current weekly goal remains open."

### Weekly Coaching

Weekly reports should answer:

- What habit improved?
- Which agent role worked best?
- Which task had the best outcome per token?
- Which task created avoidable churn?
- What is the next challenge?

### Monthly Reflection

Monthly reports should include:

- `Keep`: one behavior to continue.
- `Improve`: one behavior to practice.
- `Stop`: one waste pattern to reduce.
- `Skill Gained`: one human capability that improved.
- `Next Challenge`: one concrete goal for the next month.

The wording should be direct and practical. Avoid inflated praise, shame, or fake certainty.

## 16. Data Model

### Usage Event

Stored in `data/ai-usage/events.jsonl`.

Required fields:

- `event_id`
- `source_agent`: `claude` or `codex`
- `source_path`
- `source_session_id`
- `occurred_at`
- `project_path`
- `input_tokens`
- `cached_input_tokens`
- `output_tokens`
- `reasoning_output_tokens`
- `total_tokens`
- `record_type`: `session`, `turn`, or `rollup_delta`
- `imported_at`

Optional fields:

- `uses_task_agent`
- `uses_mcp`
- `tool_counts`
- `files_modified`
- `git_commits`
- `git_pushes`
- `outcome`
- `primary_success`
- `goal_categories`
- `human_skill_practiced`
- `reflection_id`

### Goal

Stored in `data/ai-usage/goals.json`.

Fields:

- `goal_id`
- `title`
- `type`
- `period`
- `status`
- `target`
- `progress`
- `created_at`
- `completed_at`
- `evidence`
- `reflection`
- `human_skill`
- `xp_awarded`

### Badge

Computed from usage events and goals.

Fields:

- `badge_id`
- `name`
- `type`
- `status`
- `earned_at`
- `progress`
- `threshold`
- `endorsement_text`
- `evidence`
- `human_skill`

### Reflection

Stored inline in `goals.json` for MVP or as `data/ai-usage/reflections.jsonl` later.

Fields:

- `reflection_id`
- `source_goal_id`
- `created_at`
- `prompt`
- `response`
- `human_skill`
- `next_action`

## 17. UX Requirements

### Terminal Report

The default report should be fast and readable.

Required sections:

- Today
- This Week
- This Month
- Lifetime
- Claude vs Codex split
- Current goals
- Badge progress
- Human skill practiced
- Next best challenge
- Alerts or missing evidence

Example command:

```bash
bin/agentboost-usage-report
```

### Period-Specific Reports

Example commands:

```bash
bin/agentboost-usage-report --day 2026-05-06
bin/agentboost-usage-report --week 2026-W19
bin/agentboost-usage-report --month 2026-05
bin/agentboost-usage-report --lifetime
```

### Markdown Recap

Example command:

```bash
bin/agentboost-usage-report --month 2026-05 --markdown docs/ai-usage/monthly/2026-05.md
```

## 18. Success Metrics

### Product Adoption

- Daily report can be generated in under 2 seconds on local data.
- Weekly and monthly rollups correctly include both Claude and Codex.
- User can see milestone progress without manually reading tool histories.

### Behavior Change

- At least one AI-utilization goal can be completed with evidence.
- Reports show whether usage produced verified work or reusable artifacts.
- Monthly recap identifies one improvement habit.
- Monthly recap identifies one human skill that improved.
- At least one high-friction session can be converted into a recovery goal or reusable workflow improvement.
- Token-only progress does not dominate XP or high-status badges.

### Data Quality

- No duplicate Codex cumulative usage is counted.
- Missing Claude or Codex data does not fail the report.
- Cached input tokens are visible separately when available.

### Trust

- Generated reports include source counts and import timestamps.
- High-status medals require both token thresholds and evidence.
- No full prompt content is stored in the normalized ledger.
- Missed goals and low-usage periods are described without shame language.

## 19. Privacy And Safety

- Do not copy full prompt text into `data/ai-usage/events.jsonl`.
- Store paths and summary metadata only.
- Treat local usage data as private by default.
- Keep team sharing out of MVP scope.
- Make generated reports easy to inspect before sharing.
- Avoid storing secrets from transcripts or command outputs.
- Keep reflections local by default because they may include sensitive self-assessment.
- Do not use manipulative streak mechanics that pressure the user into unnecessary AI usage.
- Do not label the user negatively based on low activity, failed goals, or high-friction sessions.

## 20. Edge Cases

- Claude data exists but Codex data does not.
- Codex data exists but Claude data does not.
- A Codex log has multiple cumulative `token_count` events.
- A session has zero or missing token fields.
- A source file is malformed JSON or JSONL.
- Local timezone differs from UTC timestamps in source data.
- The same source session is imported more than once.
- A goal is completed without evidence.
- A badge threshold is met but evidence requirements are not.
- A user deliberately avoids AI for deep work or rest.
- A reflection contains sensitive information.
- A high-token session produced learning but no shipped artifact.

## 21. Release Plan

### Phase 1: Ledger

- Implement `bin/agentboost-usage-collect`.
- Normalize Claude and Codex usage.
- Store events in `data/ai-usage/events.jsonl`.
- Add basic duplicate protection.

### Phase 2: Reports

- Implement `bin/agentboost-usage-report`.
- Add daily, weekly, monthly, and lifetime rollups.
- Show Claude vs Codex split and milestone progress.

### Phase 3: Goals

- Add `data/ai-usage/goals.json`.
- Support goal creation, progress, completion, and evidence links.
- Add goal sections to reports.
- Add optional reflection and human skill fields.

### Phase 4: Badges

- Add milestone and behavior badge calculation.
- Add endorsement text.
- Add anti-gaming evidence gates.
- Add human growth badges and recoverable weekly streaks.

### Phase 5: Monthly Workforce Recap

- Generate `docs/ai-usage/monthly/YYYY-MM.md`.
- Include agent roster, workforce fitness, guild paths, and monthly council.
- Include keep, improve, stop, skill gained, and next challenge sections.

### Phase 6: Optional UI

- Build a local dashboard only after the ledger and report semantics are stable.

## 22. Open Questions

- Should cached input tokens count equally toward lifetime milestone medals?
- Should milestone thresholds use total tokens or non-cached billable-equivalent tokens?
- Should Claude facets be considered authoritative outcome evidence or advisory context?
- Should goals be edited manually in JSON first, or should a CLI goal command be part of the first implementation?
- Should badges be generated as Markdown only, or also stored as structured JSON?
- Should ipagent runs be treated as a separate source agent later?
- Which human skills should be first-class taxonomy values: judgment, delegation, verification, automation, research, reflection, or others?
- Should reflections be private-only forever, or should the user be able to export selected reflections into monthly reports?
- How should the product recognize healthy non-AI work without creating manual logging burden?

## 23. Acceptance Criteria

- A user can run one command to collect local Claude and Codex usage.
- Claude collection reads `~/.claude/projects/**/*.jsonl` token metadata without storing message content, and only falls back to `usage-data/session-meta` when project JSONL usage is unavailable.
- Codex collection handles `last_token_usage`, cumulative `total_token_usage` deltas, `cache_read_input_tokens` aliases, missing `total_tokens`, and zero-delta events without double counting.
- A user can run one command to see daily, weekly, monthly, and lifetime usage.
- Reports show Claude, Codex, and combined totals.
- Reports show progress toward 1B, 10B, and 100B token milestones.
- A user can define an AI-utilization goal.
- A user can mark a goal complete only with evidence.
- The system awards `Billion-Token Operator`, `Agent Guild Builder`, and `AI-Native Workforce Architect` only when both token and evidence requirements are met.
- The system can generate a monthly Markdown recap.
- The system does not store full prompt content in the normalized ledger.
- Monthly reports include keep, improve, stop, skill gained, and next challenge sections.
- At least one human growth badge can be earned without increasing raw token volume.
- XP from raw token usage is capped so evidence-backed goals and reusable artifacts remain more valuable.
- Reports describe missed goals, low usage, and failed sessions without shame language.
