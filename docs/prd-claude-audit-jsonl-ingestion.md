---
artifact_id: artifact-2026-05-21-agentboost-claude-audit-jsonl
parent_id: null
artifact_type: sdlc-prd
state: reflected
traceability_to: []
version: 1
authors: [peter.lyoo, claude]
last_updated: 2026-05-21
---

# PRD: Claude Desktop `audit.jsonl` ingestion in AgentBoost

> Reclaim ~493M Claude tokens that AgentBoost currently misses because they live in
> `audit.jsonl` files at the session root of Claude Desktop's local-agent-mode tree,
> not in the nested `.claude/projects/` jsonls the new multi-root discoverer already
> covers.

## 1. Purpose

Extend AgentBoost's Claude usage collector to also parse `audit.jsonl` files inside
`~/Library/Application Support/Claude/local-agent-mode-sessions/`, so the menu's
Lifetime / Month / Today rollups reflect the full Claude Desktop usage history
instead of dropping ~8% of lifetime tokens silently.

## 2. Problem statement

The 2026-05-21 multi-root commit (`sendbird-playground/agentboost@1e86a4d`) added
discovery of nested `.claude/projects/` trees under Claude Desktop's
local-agent-mode sessions. That recovered 281M tokens / 4,942 events. A direct
audit of the same Library tree shows another **4,921 unique assistant events
worth ~493M tokens** sitting in `audit.jsonl` files at the *session root* —
zero overlap with what we already capture.

Observable evidence on this host (2026-05-21 backfill):

- Canonical Claude lifetime: 5,960,603,796 tokens / 26,232 events.
- Library `audit.jsonl` adds (measured by direct Python scan): 4,921 events / 492,914,704 tokens.
- True Claude lifetime if audit is included: ~6,453,518,500 tokens (+8.3%).
- User feedback: "claude usage isn't being checked properly" — pointed at CodexBar's
  `CostUsageScanner+Claude.swift` for inspiration on multi-source discovery.

## 3. Goals

### 3.1 Functional

- F-AUDIT-1: Discover every `audit.jsonl` whose path is under
  `~/Library/Application Support/Claude/local-agent-mode-sessions/`.
- F-AUDIT-2: Parse assistant rows in `audit.jsonl` that carry a `message.usage`
  block. Map `_audit_timestamp` → `occurred_at`, `parent_tool_use_id` → request
  identifier for dedup keying.
- F-AUDIT-3: Emit events with the same shape as today's `claudeProjectUsageEvents`
  output (event_id, source_agent=claude, source_path, occurred_at, token fields,
  record_type) so downstream rollups (Today/Week/Month/Lifetime) pick them up
  with no further changes.
- F-AUDIT-4: Apply the same streaming dedup as nested `.claude/projects/`:
  per `(messageId, derived_request_id)` keep the row with the largest
  `total_tokens`. Cross-file dedup uses the existing `event_id` mechanism.
- F-AUDIT-5: Tag audit-sourced events distinctly enough to debug later — at
  minimum the `source_path` will contain `local-agent-mode-sessions/.../audit.jsonl`,
  which is grep-able. (Stretch: optional `record_type: "audit"` for explicit
  filtering. See open question Q1.)

### 3.2 Non-functional

- NF-AUDIT-1: No regression in existing rollups — Claude event count must stay
  ≥ 26,232 and Codex stays ≥ 83,675 in a backfill on this host.
- NF-AUDIT-2: **First-run lifetime backfill** wall time under 6 min on this host
  (one-time cost; was 80 s without audit, 301 s after — driven by reading
  ~378 multi-MB `audit.jsonl` files). **Incremental refreshes** continue to
  finish under 90 s because they apply the 2-hour mtime cutoff before
  reading. Revised after measurement; original 90 s target was based on the
  pre-audit jsonl footprint.
- NF-AUDIT-3: No new permissions, entitlements, or sandbox prompts required —
  the dev-fallback (`AgentBoostRepoRoot` Info.plist key) already grants read
  access to `~/Library/Application Support/Claude/`.
- NF-AUDIT-4: Pure additive change — disabling the audit branch (env var or
  build flag) brings behavior back to current state without code rollback.

## 4. Non-goals

- claude.ai web sessions or OAuth API token tracking.
- Anthropic admin API ingestion (rate limits / weekly spend) — that's a separate
  PRD modeled on CodexBar's `ClaudeUsageFetcher`.
- Pulling audit data from anywhere other than the local-agent-mode Library tree.
- Cost computation. Tokens only; pricing is out of scope.
- Codex audit-equivalent. Codex side is already complete.
- A new CLI/menu surface to view audit-source events separately.

## 5. Success metrics

- **Coverage:** Claude lifetime on this host climbs from 5.96B → ≥ 6.40B after
  next backfill (target: +400M+ tokens, 95% of measured gap).
- **Event count:** Claude event count climbs from 26,232 → ≥ 31,000 (≥ 95% of
  measured 4,921 new events).
- **No regression:** existing tests stay green (`mix test` 15/15) and no
  previously-captured event_ids disappear from the imported set.
- **Backfill time:** first-run lifetime backfill under 6 min, incremental
  refreshes continue under 90 s. (Measured: 301 s for first-run on this host
  with ~378 audit.jsonl files; pre-existing incremental path untouched.)
- **Verification window:** measured within 1 hour of merge against current
  `events.jsonl` to avoid drift.

## 6. Constraints

- Local-only, no network.
- Read-only on Claude Desktop's data tree.
- Must work both with a security-scoped bookmark and with the dev-fallback path
  (currently the only mode actually in use on this host).
- Must coexist with the existing `claudeProjectUsageEvents` dedup pipeline —
  no separate persistence layer.
- Swift 5 / macOS 13+ deployment target.

## 7. Risks

| Risk | Severity | Likelihood | Mitigation |
|------|---------|-----------|------------|
| `audit.jsonl` rows occasionally lack `message.usage` or use a partial schema | M | H | Reuse the existing `usage` block guard (skip when input+cache+output == 0); defensive `text()` / `tokenInt()` accessors. |
| Schema evolves in a future Claude Desktop release | L | M | Failure mode is graceful skip, not crash; existing tests cover the happy path. |
| Same logical turn appears in both `audit.jsonl` and nested `.claude/projects/` | M | L | Empirically zero overlap on this host (measured: `audit ∩ proj = 0`). Cross-file `event_id` dedup catches any future overlap. |
| `parent_tool_use_id` is missing on some audit rows | M | M | Fall back to `claude:stableID(path, lineIndex)` event-id (same path the existing scanner uses for rows without `requestId`). |
| Adds non-trivial scan time for users without Claude Desktop | L | L | The discovery enumerator is a no-op when the Library directory is absent (already guarded). |

## 8. Decisions

- **D1: Add the audit pass inside `claudeProjectUsageEvents`** as a second pass
  over the same projects-roots discovery loop, not a new top-level function.
  Rejected: a sibling `claudeAuditUsageEvents` function, because it would
  duplicate the streaming-dedup + event-emission code we just refactored.
- **D2: Treat `parent_tool_use_id` as the requestId surrogate** for dedup keying.
  Rejected: synthesizing a request id from message body hash, because empirical
  zero-overlap means a missing key for the small minority of audit rows is
  acceptable (handled by the path+line fallback).
- **D3: Map `_audit_timestamp` to `occurred_at`** at parse time. Rejected:
  carrying a separate `audit_timestamp` field through the rollup, because
  downstream rollups already key on `occurred_at` for day/week/month buckets.
- **D4: Source discovery reuses `discoverClaudeProjectsRoots` enumeration**, but
  filters the same Library tree for `audit.jsonl` files (filename match) at the
  enumerator level. Rejected: a separate enumerator that re-walks the same
  directory tree, since it doubles the IO with no benefit.

## 9. Open questions

- **Q1: Should audit events use `record_type: "audit"`** so the rest of the
  system can filter them out optionally, or merge silently as `record_type: "turn"`
  to keep one canonical event shape? — Owner: Peter — Target: before merge.
  Default if unanswered: merge silently as `turn` (matches user's stated goal of
  recovering lifetime tokens, not adding a new view).
- **Q2: Do we also want to apply CodexBar's sidechain-wins cross-file dedup
  rule?** Right now we keep the max-tokens row. CodexBar prefers sidechain /
  subagent roles. Out of scope for this PRD; flagging only because it surfaced
  while reading their scanner. Default: no — empirical overlap is zero, so the
  rule wouldn't change any current event.

## 10. Children (Milestones)

- (Single-milestone project. No fan-out planned — the work is a contained
  Swift change in `AgentBoostApp.swift` plus a regression test on the
  collector. Anti-bloat exit per the sdlc-templates rule applies; we'll
  proceed straight to an SDD-equivalent design note inside the implementation
  commit instead of a separate Milestone artifact.)
