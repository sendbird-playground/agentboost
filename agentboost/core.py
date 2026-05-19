from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)

MILESTONE_BADGES = {
    "Billion Club": {
        "badge_id": "a758dd50b1415e27",
        "threshold": 1_000_000_000,
        "endorsement_text": "Uses AI agents as daily working partners, not occasional search boxes.",
        "evidence_requirement": "10 completed evidence-backed goals",
    },
    "Agent Guild Builder": {
        "threshold": 10_000_000_000,
        "endorsement_text": "Builds repeatable AI workflows that other agents and humans can reuse.",
        "evidence_requirement": "25 reusable artifacts",
    },
    "AI-Native Workforce Architect": {
        "threshold": 100_000_000_000,
        "endorsement_text": "Operates a mature AI workforce loop: plan, delegate, verify, learn, and improve the system.",
        "evidence_requirement": "sustained verification, closeout discipline, and orchestration evidence",
    },
}

HUMAN_GROWTH_BADGES = {
    "Better Question": "Clarity and task framing",
    "Taste Upgrade": "Judgment and standards",
    "Delegation Rep": "Workstream ownership",
    "Review Muscle": "Review and critique",
    "Calm Debugger": "Root-cause discipline",
    "Craft Keeper": "Reusable workflow capture",
}

VALID_GOAL_TYPES = {
    "adoption",
    "collaboration",
    "verification",
    "reuse",
    "workforce",
    "reflection",
    "recovery",
    # Existing report and badge taxonomy values.
    "automation",
    "builder",
    "debugging",
    "documentation",
    "implementation",
    "judgment",
    "research",
    "review",
}

VALID_GOAL_STATUSES = {"planned", "in_progress", "completed", "abandoned"}

LEVEL_XP_REQUIREMENTS: tuple[tuple[int, int], ...] = (
    (1, 15),
    (2, 34),
    (3, 57),
    (4, 92),
    (5, 135),
    (6, 372),
    (7, 560),
    (8, 840),
    (9, 1_242),
    (10, 1_716),
    (11, 2_360),
    (12, 3_216),
    (13, 4_200),
    (14, 5_460),
    (15, 7_050),
    (16, 8_840),
    (17, 11_040),
    (18, 13_716),
    (19, 16_680),
    (20, 20_216),
    (21, 24_402),
    (22, 28_980),
    (23, 34_320),
    (24, 40_512),
    (25, 54_900),
    (26, 57_210),
    (27, 63_666),
    (28, 73_080),
    (29, 83_270),
    (30, 95_700),
    (31, 108_480),
    (32, 122_760),
    (33, 138_666),
    (34, 155_540),
    (35, 174_216),
    (36, 194_832),
    (37, 216_600),
    (38, 240_550),
    (39, 266_682),
    (40, 294_216),
    (41, 324_240),
    (42, 356_916),
    (43, 391_160),
    (44, 428_280),
    (45, 468_450),
    (46, 510_420),
    (47, 555_680),
    (48, 604_416),
    (49, 655_200),
    (50, 709_716),
)

EVIDENCE_PREFIX_TYPES = {
    "task-log": "task_log",
    "task": "task_log",
    "command": "command",
    "test": "command",
    "report": "report",
    "generated-report": "report",
    "git-diff": "diff",
    "diff": "diff",
    "pr": "pr",
    "docs": "doc",
    "doc": "doc",
    "file": "file",
    "skill": "skill",
    "plan": "plan",
    "review": "review",
    "agent": "collaboration",
    "circuit": "circuit_breaker",
    "circuit-breaker": "circuit_breaker",
    "recovery": "recovery",
    "deep-work-skip": "recovery",
    "deep_work_skip": "recovery",
}


def default_events_file(repo_root: Path) -> Path:
    return repo_root / "data" / "ai-usage" / "events.jsonl"


def default_goals_file(repo_root: Path) -> Path:
    return repo_root / "data" / "ai-usage" / "goals.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def local_time(value: str | None) -> datetime:
    return parse_time(value).astimezone()


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def write_events(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")


def replace_events(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")


def load_goals(goals_file: Path) -> list[dict[str, Any]]:
    if not goals_file.exists():
        return []
    data = read_json(goals_file)
    if data is None:
        return []
    if isinstance(data, list):
        return data
    goals = data.get("goals", [])
    return goals if isinstance(goals, list) else []


def save_goals(goals_file: Path, goals: list[dict[str, Any]]) -> None:
    goals_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "goals": goals}
    goals_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def stable_id(*parts: Any) -> str:
    raw = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def token_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return 0


def source_path(path: Path) -> str:
    return str(path.expanduser())


def normalize_goal_type(goal_type: str) -> str:
    normalized = str(goal_type or "").strip().lower().replace("_", "-")
    aliases = {
        "automator": "automation",
        "collab": "collaboration",
        "verify": "verification",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in VALID_GOAL_TYPES:
        allowed = ", ".join(sorted(VALID_GOAL_TYPES))
        raise ValueError(f"invalid goal type: {goal_type}; expected one of: {allowed}")
    return normalized


def evidence_types(evidence: list[str] | None) -> list[str]:
    found: list[str] = []
    for item in evidence or []:
        text = str(item).strip()
        if not text or ":" not in text:
            continue
        prefix, value = text.split(":", 1)
        evidence_type = EVIDENCE_PREFIX_TYPES.get(prefix.strip().lower())
        if evidence_type and value.strip() and evidence_type not in found:
            found.append(evidence_type)
    return found


def validate_evidence(evidence: list[str] | None) -> list[str]:
    cleaned = [str(item).strip() for item in (evidence or []) if str(item).strip()]
    if not cleaned:
        raise ValueError("goal completion requires at least one evidence link")
    invalid = [item for item in cleaned if not evidence_types([item])]
    if invalid:
        allowed = ", ".join(sorted(EVIDENCE_PREFIX_TYPES))
        raise ValueError(f"unsupported evidence link: {invalid[0]}; expected prefix:value using one of: {allowed}")
    return cleaned


def goal_xp_award(goal: dict[str, Any], evidence_type_names: list[str], reflection: str = "") -> int:
    goal_type = str(goal.get("type") or "")
    xp = 100 + (25 * len(goal.get("evidence", [])))
    if reflection:
        xp += 25
    if "command" in evidence_type_names or goal_type in {"verification", "review"}:
        xp += 25
    if goal_type in {"reuse", "automation"} or any(item in evidence_type_names for item in ("doc", "skill", "plan")):
        xp += 50
    if goal_type in {"collaboration", "workforce"} or "collaboration" in evidence_type_names:
        xp += 50
    if goal_type == "recovery" or "recovery" in evidence_type_names:
        xp += 50
    if "circuit_breaker" in evidence_type_names:
        xp += 50
    return xp


def load_claude_facets(claude_dir: Path) -> dict[str, dict[str, Any]]:
    facets: dict[str, dict[str, Any]] = {}
    for path in (claude_dir / "usage-data" / "facets").glob("*.json"):
        data = read_json(path)
        if not data:
            continue
        session_id = data.get("session_id")
        if isinstance(session_id, str):
            facets[session_id] = data
    return facets


def claude_project_events(claude_dir: Path, imported_at: str) -> list[dict[str, Any]]:
    projects_dir = claude_dir / "projects"
    if not projects_dir.exists():
        return []
    events: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for path in sorted(projects_dir.rglob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("type") != "assistant":
                continue
            message = row.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            input_tokens = token_int(usage.get("input_tokens"))
            cache_creation_tokens = token_int(usage.get("cache_creation_input_tokens"))
            cache_read_tokens = token_int(usage.get("cache_read_input_tokens"))
            cached_input_tokens = cache_creation_tokens + cache_read_tokens
            output_tokens = token_int(usage.get("output_tokens"))
            if input_tokens == 0 and cached_input_tokens == 0 and output_tokens == 0:
                continue
            message_id = message.get("id")
            request_id = row.get("requestId")
            if isinstance(message_id, str) and isinstance(request_id, str):
                event_id = f"claude:{stable_id(message_id, request_id)}"
            else:
                event_id = f"claude:{stable_id(path, line_number)}"
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)
            session_id = str(row.get("sessionId") or path.stem)
            model = message.get("model")
            event: dict[str, Any] = {
                "event_id": event_id,
                "source_agent": "claude",
                "source_path": source_path(path),
                "source_session_id": session_id,
                "occurred_at": str(row.get("timestamp") or imported_at),
                "project_path": str(row.get("cwd") or ""),
                "input_tokens": input_tokens,
                "cached_input_tokens": cached_input_tokens,
                "output_tokens": output_tokens,
                "reasoning_output_tokens": 0,
                "total_tokens": input_tokens + cached_input_tokens + output_tokens,
                "record_type": "turn",
                "imported_at": imported_at,
            }
            if isinstance(model, str) and model:
                event["model"] = model
            events.append(event)
    return events


def claude_session_meta_events(claude_dir: Path, imported_at: str) -> list[dict[str, Any]]:
    session_dir = claude_dir / "usage-data" / "session-meta"
    if not session_dir.exists():
        return []
    facets = load_claude_facets(claude_dir)
    events: list[dict[str, Any]] = []
    for path in sorted(session_dir.glob("*.json")):
        data = read_json(path)
        if not data:
            continue
        session_id = str(data.get("session_id") or path.stem)
        input_tokens = token_int(data.get("input_tokens"))
        output_tokens = token_int(data.get("output_tokens"))
        facet = facets.get(session_id, {})
        event: dict[str, Any] = {
            "event_id": f"claude:{session_id}",
            "source_agent": "claude",
            "source_path": source_path(path),
            "source_session_id": session_id,
            "occurred_at": str(data.get("start_time") or imported_at),
            "project_path": str(data.get("project_path") or ""),
            "input_tokens": input_tokens,
            "cached_input_tokens": 0,
            "output_tokens": output_tokens,
            "reasoning_output_tokens": 0,
            "total_tokens": input_tokens + output_tokens,
            "record_type": "session",
            "imported_at": imported_at,
            "uses_task_agent": bool(data.get("uses_task_agent", False)),
            "uses_mcp": bool(data.get("uses_mcp", False)),
            "tool_counts": data.get("tool_counts") if isinstance(data.get("tool_counts"), dict) else {},
            "files_modified": token_int(data.get("files_modified")),
            "git_commits": token_int(data.get("git_commits")),
            "git_pushes": token_int(data.get("git_pushes")),
        }
        for key in ("outcome", "primary_success", "goal_categories"):
            if key in facet:
                event[key] = facet[key]
        if "goal_categories" in event:
            event["human_skill_practiced"] = infer_human_skill(event)
        events.append(event)
    return events


def claude_events(claude_dir: Path, imported_at: str) -> list[dict[str, Any]]:
    project_events = claude_project_events(claude_dir, imported_at)
    if project_events:
        return project_events
    return claude_session_meta_events(claude_dir, imported_at)


def normalized_token_usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    input_tokens = token_int(value.get("input_tokens"))
    cached_input_tokens = token_int(value.get("cached_input_tokens", value.get("cache_read_input_tokens")))
    output_tokens = token_int(value.get("output_tokens"))
    reasoning_output_tokens = token_int(value.get("reasoning_output_tokens"))
    total_tokens = token_int(value.get("total_tokens"))
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": min(cached_input_tokens, input_tokens),
        "output_tokens": output_tokens,
        "reasoning_output_tokens": reasoning_output_tokens,
        "total_tokens": total_tokens,
    }


def token_usage_from_info(
    info: dict[str, Any],
    previous_total: dict[str, int] | None,
) -> tuple[dict[str, int] | None, dict[str, int] | None]:
    total = normalized_token_usage(info.get("total_token_usage"))
    last = normalized_token_usage(info.get("last_token_usage"))
    if last is not None:
        return last, total or previous_total

    if total is None:
        return None, previous_total
    if previous_total is None:
        return total, total
    delta = {field: max(0, total[field] - previous_total.get(field, 0)) for field in TOKEN_FIELDS}
    delta["cached_input_tokens"] = min(delta["cached_input_tokens"], delta["input_tokens"])
    return delta, total


def codex_model_from_payload(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    info = value.get("info")
    if isinstance(info, dict):
        for candidate in (info.get("model"), info.get("model_name")):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        metadata = info.get("metadata")
        if isinstance(metadata, dict):
            model = metadata.get("model")
            if isinstance(model, str) and model.strip():
                return model.strip()
    for candidate in (value.get("model"), value.get("model_name")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    metadata = value.get("metadata")
    if isinstance(metadata, dict):
        model = metadata.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
    return None


def codex_events(codex_dir: Path, imported_at: str) -> list[dict[str, Any]]:
    sessions_dir = codex_dir / "sessions"
    if not sessions_dir.exists():
        return []
    events: list[dict[str, Any]] = []
    for path in sorted(sessions_dir.rglob("*.jsonl")):
        previous_total: dict[str, int] | None = None
        current_model: str | None = None
        current_model_is_fallback = False
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("type") == "turn_context":
                payload = row.get("payload")
                model = codex_model_from_payload(payload)
                if model:
                    current_model = model
                    current_model_is_fallback = False
                continue
            if row.get("type") != "event_msg":
                continue
            payload = row.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "token_count":
                continue
            info = payload.get("info")
            if not isinstance(info, dict):
                continue
            usage, previous_total = token_usage_from_info(info, previous_total)
            if usage is None:
                continue
            if (
                usage["input_tokens"] == 0
                and usage["cached_input_tokens"] == 0
                and usage["output_tokens"] == 0
                and usage["reasoning_output_tokens"] == 0
            ):
                continue
            extracted_model = codex_model_from_payload({**payload, "info": info})
            if extracted_model:
                current_model = extracted_model
                current_model_is_fallback = False
            model = extracted_model or current_model
            model_is_fallback = False
            if not model:
                model = "gpt-5"
                current_model = model
                current_model_is_fallback = True
                model_is_fallback = True
            elif current_model_is_fallback and extracted_model is None:
                model_is_fallback = True
            session_id = path.stem
            event_id = f"codex:{stable_id(path, line_number)}"
            event = {
                "event_id": event_id,
                "source_agent": "codex",
                "source_path": source_path(path),
                "source_session_id": session_id,
                "occurred_at": str(row.get("timestamp") or imported_at),
                "project_path": "",
                "input_tokens": usage["input_tokens"],
                "cached_input_tokens": usage["cached_input_tokens"],
                "output_tokens": usage["output_tokens"],
                "reasoning_output_tokens": usage["reasoning_output_tokens"],
                "total_tokens": usage["total_tokens"],
                "record_type": "turn",
                "imported_at": imported_at,
                "model": model,
            }
            if model_is_fallback:
                event["model_is_fallback"] = True
            events.append(event)
    return events


def collect_usage(
    repo_root: Path,
    claude_dir: Path | None = None,
    codex_dir: Path | None = None,
    imported_at: str | None = None,
    events_file: Path | None = None,
) -> dict[str, Any]:
    repo_root = Path(repo_root)
    claude_dir = Path(claude_dir or Path.home() / ".claude")
    codex_dir = Path(codex_dir or Path.home() / ".codex")
    imported_at = imported_at or utc_now_iso()
    events_file = Path(events_file or default_events_file(repo_root))
    existing_events = read_events(events_file)
    candidates = claude_events(claude_dir, imported_at) + codex_events(codex_dir, imported_at)
    has_claude_project_events = any(
        event.get("source_agent") == "claude" and event.get("record_type") == "turn" for event in candidates
    )
    removed_legacy = 0
    if has_claude_project_events:
        filtered_events = [
            event
            for event in existing_events
            if not (event.get("source_agent") == "claude" and event.get("record_type") == "session")
        ]
        removed_legacy = len(existing_events) - len(filtered_events)
        existing_events = filtered_events
    existing_by_id = {event.get("event_id"): event for event in existing_events}
    new_events: list[dict[str, Any]] = []
    updated_by_id: dict[str, dict[str, Any]] = {}
    seen_candidate_ids: set[str] = set()
    for candidate in candidates:
        event_id = candidate["event_id"]
        if event_id in seen_candidate_ids:
            continue
        seen_candidate_ids.add(event_id)
        existing = existing_by_id.get(event_id)
        if existing is None:
            new_events.append(candidate)
            continue
        comparable_existing = {key: value for key, value in existing.items() if key not in {"imported_at", "updated_at"}}
        comparable_candidate = {key: value for key, value in candidate.items() if key not in {"imported_at", "updated_at"}}
        if comparable_existing != comparable_candidate:
            updated = dict(candidate)
            updated["imported_at"] = existing.get("imported_at", candidate["imported_at"])
            updated["updated_at"] = imported_at
            updated_by_id[event_id] = updated
    if updated_by_id or removed_legacy:
        merged = [updated_by_id.get(str(event.get("event_id")), event) for event in existing_events]
        replace_events(events_file, merged)
    write_events(events_file, new_events)
    return {
        "scanned": len(candidates),
        "imported": len(new_events),
        "updated": len(updated_by_id),
        "removed_legacy": removed_legacy,
        "skipped_existing": len(candidates) - len(new_events) - len(updated_by_id),
        "events_file": str(events_file),
    }


def add_goal(
    goals_file: Path,
    title: str,
    goal_type: str,
    period: str,
    target: int = 1,
    human_skill: str = "",
) -> dict[str, Any]:
    goals = load_goals(goals_file)
    normalized_type = normalize_goal_type(goal_type)
    goal = {
        "goal_id": f"goal-{stable_id(title, normalized_type, period, len(goals))}",
        "title": title,
        "type": normalized_type,
        "period": period,
        "status": "planned",
        "target": max(1, int(target)),
        "progress": 0,
        "created_at": utc_now_iso(),
        "completed_at": None,
        "evidence": [],
        "reflection": "",
        "human_skill": human_skill or goal_type,
        "xp_awarded": 0,
        "evidence_types": [],
    }
    goals.append(goal)
    save_goals(goals_file, goals)
    return goal


def update_goal(
    goals_file: Path,
    goal_id: str,
    *,
    status: str | None = None,
    progress: int | None = None,
) -> dict[str, Any]:
    goals = load_goals(goals_file)
    for goal in goals:
        if goal.get("goal_id") != goal_id:
            continue
        if status is not None:
            normalized_status = str(status).strip().lower()
            if normalized_status not in VALID_GOAL_STATUSES:
                allowed = ", ".join(sorted(VALID_GOAL_STATUSES))
                raise ValueError(f"invalid goal status: {status}; expected one of: {allowed}")
            if normalized_status == "completed" and not goal.get("evidence"):
                raise ValueError("use complete to mark a goal completed with evidence")
            goal["status"] = normalized_status
        if progress is not None:
            target = token_int(goal.get("target")) or 1
            goal["progress"] = max(0, min(int(progress), target))
        save_goals(goals_file, goals)
        return goal
    raise ValueError(f"goal not found: {goal_id}")


def complete_goal(
    goals_file: Path,
    goal_id: str,
    evidence: list[str] | None,
    reflection: str = "",
    completed_at: str | None = None,
) -> dict[str, Any]:
    evidence = validate_evidence(evidence)
    evidence_type_names = evidence_types(evidence)
    goals = load_goals(goals_file)
    for goal in goals:
        if goal.get("goal_id") == goal_id:
            goal["status"] = "completed"
            goal["progress"] = goal.get("target", 1)
            goal["completed_at"] = completed_at or utc_now_iso()
            goal["evidence"] = evidence
            goal["evidence_types"] = evidence_type_names
            goal["reflection"] = reflection
            goal["xp_awarded"] = goal_xp_award(goal, evidence_type_names, reflection)
            save_goals(goals_file, goals)
            return goal
    raise ValueError(f"goal not found: {goal_id}")


def infer_human_skill(record: dict[str, Any]) -> str:
    categories = record.get("goal_categories")
    if isinstance(categories, dict) and categories:
        first = sorted(categories, key=lambda key: (-token_int(categories[key]), key))[0]
        mapping = {
            "documentation": "craft",
            "implementation": "builder",
            "configuration_setup": "automation",
            "review": "review",
            "debugging": "debugging",
        }
        return mapping.get(first, first)
    return ""


def completed_goals(goals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [goal for goal in goals if goal.get("status") == "completed" and goal.get("evidence")]


def total_tokens(events: list[dict[str, Any]]) -> int:
    return sum(token_int(event.get("total_tokens")) for event in events)


def goal_evidence(goals: list[dict[str, Any]], limit: int | None = None) -> list[str]:
    evidence: list[str] = []
    for goal in goals:
        for item in goal.get("evidence", []):
            if item:
                evidence.append(str(item))
                if limit is not None and len(evidence) >= limit:
                    return evidence
    return evidence


def first_completed_at(goals: list[dict[str, Any]]) -> str | None:
    completed_at = sorted(str(goal.get("completed_at")) for goal in goals if goal.get("completed_at"))
    return completed_at[0] if completed_at else None


def verification_goal(goal: dict[str, Any]) -> bool:
    goal_type = str(goal.get("type") or "")
    evidence_blob = " ".join(str(item) for item in goal.get("evidence", [])).lower()
    return goal_type in {"verification", "review", "workforce"} or "command:" in evidence_blob or "test:" in evidence_blob


def verifier_streak_count(goals: list[dict[str, Any]]) -> int:
    ordered = sorted(completed_goals(goals), key=lambda goal: str(goal.get("completed_at") or ""))
    streak = 0
    best = 0
    for goal in ordered:
        if verification_goal(goal):
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return best


def completed_month_count(goals: list[dict[str, Any]]) -> int:
    months = {
        local_time(str(goal.get("completed_at"))).strftime("%Y-%m")
        for goal in completed_goals(goals)
        if goal.get("completed_at")
    }
    return len(months)


def badge_statuses(events: list[dict[str, Any]], goals: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    total = total_tokens(events)
    completed = completed_goals(goals)
    reusable = [goal for goal in completed if goal.get("type") in {"reuse", "automation", "workforce"}]
    verified = [goal for goal in completed if goal.get("type") in {"verification", "review", "workforce"}]
    workforce = [goal for goal in completed if goal.get("type") in {"workforce", "collaboration"}]
    badges: dict[str, dict[str, Any]] = {}
    for name, spec in MILESTONE_BADGES.items():
        threshold = spec["threshold"]
        status = "locked"
        evidence_progress = 0
        badge_goals: list[dict[str, Any]] = []
        if name == "Billion Club":
            evidence_progress = min(len(completed), 10)
            if total >= threshold and len(completed) >= 10:
                status = "earned"
                badge_goals = completed[:10]
            elif total > 0 or completed:
                status = "in_progress"
        elif name == "Agent Guild Builder":
            evidence_progress = min(len(reusable), 25)
            if total >= threshold and len(reusable) >= 25:
                status = "earned"
                badge_goals = reusable[:25]
            elif total > 0 or reusable:
                status = "in_progress"
        else:
            evidence_progress = min(len(verified), 50)
            mature_orchestration = completed_month_count(verified) >= 2 and bool(workforce)
            if total >= threshold and len(verified) >= 50 and mature_orchestration:
                status = "earned"
                badge_goals = verified[:50]
            elif total > 0 or verified:
                status = "in_progress"
        badges[name] = {
            "badge_id": spec.get("badge_id") or stable_id(name),
            "name": name,
            "type": "milestone",
            "status": status,
            "progress": min(1.0, total / threshold) if threshold else 0,
            "threshold": threshold,
            "endorsement_text": spec["endorsement_text"],
            "evidence_requirement": spec["evidence_requirement"],
            "evidence_progress": evidence_progress,
            "earned_at": first_completed_at(badge_goals) if status == "earned" else None,
            "evidence": goal_evidence(badge_goals),
        }

    agent_token_totals: Counter[str] = Counter()
    for event in events:
        source_agent = str(event.get("source_agent") or "").lower()
        if source_agent:
            agent_token_totals[source_agent] += token_int(event.get("total_tokens"))
    weekly_agent_token_totals: Counter[Any] = Counter()
    for event in events:
        source_agent = str(event.get("source_agent") or "").lower()
        if source_agent not in {"claude", "codex"}:
            continue
        event_date = local_time(event.get("occurred_at")).date()
        week_start = event_date - timedelta(days=(event_date.weekday() + 1) % 7)
        weekly_agent_token_totals[week_start] += token_int(event.get("total_tokens"))
    heavy_user_weekly_threshold = 10_000_000_000
    heavy_user_weekly_tokens = max(weekly_agent_token_totals.values(), default=0)
    heavy_user_earned = heavy_user_weekly_tokens >= heavy_user_weekly_threshold
    heavy_user_progress = min(1.0, heavy_user_weekly_tokens / heavy_user_weekly_threshold)
    evidence_blob = " ".join(item for goal in completed for item in goal_evidence([goal])).lower()
    two_key_agent_threshold = 1_000_000_000
    claude_tokens = agent_token_totals["claude"]
    codex_tokens = agent_token_totals["codex"]
    two_key_agent_earned = claude_tokens >= two_key_agent_threshold and codex_tokens >= two_key_agent_threshold
    two_key_agent_progress = min(1.0, min(claude_tokens, codex_tokens) / two_key_agent_threshold)
    behavior_specs = {
        "Two key agents": {
            "badge_id": "b0aec0de4cd56059",
            "earned": two_key_agent_earned,
            "status": "earned" if two_key_agent_earned else ("in_progress" if two_key_agent_progress > 0 else "locked"),
            "progress": two_key_agent_progress,
            "threshold": two_key_agent_threshold,
            "endorsement_text": "Token usage for Claude and Codex each reaches over 1B.",
            "human_skill": "two_key_agents",
        },
        "Heavy user": {
            "badge_id": "8f5a9291c21f44bf",
            "earned": heavy_user_earned,
            "status": "earned" if heavy_user_earned else ("in_progress" if heavy_user_progress > 0 else "locked"),
            "progress": heavy_user_progress,
            "threshold": heavy_user_weekly_threshold,
            "endorsement_text": "Weekly Claude and Codex token usage reaches 10B total.",
            "human_skill": "heavy_user",
        },
        "Verifier Streak": verifier_streak_count(completed) >= 5,
        "Rival Review": any(goal.get("type") in {"collaboration", "review"} for goal in completed)
        or ("claude" in evidence_blob and "codex" in evidence_blob),
        "Automation Dividend": any(goal.get("type") in {"reuse", "automation"} for goal in completed),
        "Circuit Breaker Save": any("circuit" in " ".join(goal.get("evidence", [])).lower() for goal in completed),
        "Low Waste Win": any(goal.get("type") == "verification" for goal in completed) and total < 1_000_000,
    }
    for name, spec in behavior_specs.items():
        if isinstance(spec, dict):
            earned = bool(spec.get("earned"))
            status = str(spec.get("status") or ("earned" if earned else "locked"))
            progress = float(spec.get("progress", 1.0 if earned else 0.0))
            threshold = spec.get("threshold", 1)
            endorsement_text = str(spec.get("endorsement_text") or "Healthy AI workflow behavior.")
            human_skill = str(spec.get("human_skill") or name.lower().replace(" ", "_"))
            badge_id = str(spec.get("badge_id") or stable_id(name))
        else:
            earned = bool(spec)
            status = "earned" if earned else "locked"
            progress = 1.0 if earned else 0.0
            threshold = 1
            endorsement_text = "Healthy AI workflow behavior."
            human_skill = name.lower().replace(" ", "_")
            badge_id = stable_id(name)
        badges[name] = {
            "badge_id": badge_id,
            "name": name,
            "type": "behavior",
            "status": status,
            "progress": progress,
            "threshold": threshold,
            "endorsement_text": endorsement_text,
            "human_skill": human_skill,
            "earned_at": first_completed_at(completed) if earned else None,
            "evidence": goal_evidence(completed, 5) if earned else [],
        }

    skill_counts = Counter(goal.get("human_skill") or goal.get("type") for goal in completed)
    reflection_count = sum(1 for goal in completed if goal.get("reflection"))
    human_badge_progress = {
        "Better Question": reflection_count,
        "Taste Upgrade": skill_counts.get("review", 0) + skill_counts.get("judgment", 0),
        "Delegation Rep": skill_counts.get("workforce", 0) + skill_counts.get("delegation", 0),
        "Review Muscle": skill_counts.get("verification", 0) + skill_counts.get("review", 0),
        "Calm Debugger": skill_counts.get("debugging", 0),
        "Craft Keeper": skill_counts.get("reuse", 0) + skill_counts.get("automation", 0) + skill_counts.get("craft", 0),
    }
    for name, skill in HUMAN_GROWTH_BADGES.items():
        progress = human_badge_progress.get(name, 0)
        badges[name] = {
            "badge_id": stable_id(name),
            "name": name,
            "type": "human_growth",
            "status": "earned" if progress > 0 else "locked",
            "progress": min(1.0, progress),
            "threshold": 1,
            "endorsement_text": skill,
            "human_skill": skill,
            "earned_at": first_completed_at(completed) if progress > 0 else None,
            "evidence": goal_evidence(completed, 5) if progress > 0 else [],
        }
    return badges


def rollup_events(events: list[dict[str, Any]], now: str | None = None) -> dict[str, dict[str, Any]]:
    now_dt = parse_time(now)
    today = now_dt.astimezone().date()
    iso_year, iso_week, _ = today.isocalendar()
    month = today.strftime("%Y-%m")
    periods = {
        "Today": [],
        "This Week": [],
        "This Month": [],
        "Lifetime": list(events),
    }
    for event in events:
        event_date = local_time(event.get("occurred_at")).date()
        event_year, event_week, _ = event_date.isocalendar()
        if event_date == today:
            periods["Today"].append(event)
        if (event_year, event_week) == (iso_year, iso_week):
            periods["This Week"].append(event)
        if event_date.strftime("%Y-%m") == month:
            periods["This Month"].append(event)
    return {name: summarize_events(period_events) for name, period_events in periods.items()}


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_agent: dict[str, int] = defaultdict(int)
    fields = {field: 0 for field in TOKEN_FIELDS}
    projects: Counter[str] = Counter()
    sessions: set[str] = set()
    agent_assisted_tasks = 0
    for event in events:
        by_agent[str(event.get("source_agent") or "unknown")] += token_int(event.get("total_tokens"))
        for field in TOKEN_FIELDS:
            fields[field] += token_int(event.get(field))
        if event.get("project_path"):
            projects[str(event["project_path"])] += token_int(event.get("total_tokens"))
        if event.get("source_session_id"):
            sessions.add(str(event["source_session_id"]))
        if event.get("uses_task_agent") or event.get("uses_mcp"):
            agent_assisted_tasks += 1
    return {
        **fields,
        "by_agent": dict(sorted(by_agent.items())),
        "active_sessions": len(sessions),
        "top_projects": projects.most_common(3),
        "agent_assisted_tasks": agent_assisted_tasks,
    }


def format_tokens(value: int) -> str:
    value = max(0, int(value or 0))
    for threshold, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if value >= threshold:
            tenths = (value * 10 + threshold // 2) // threshold
            whole, fraction = divmod(tenths, 10)
            if fraction == 0:
                return f"{whole}{suffix}"
            return f"{whole}.{fraction}{suffix}"
    return str(value)


def total_xp(events: list[dict[str, Any]], goals: list[dict[str, Any]]) -> int:
    token_xp = min(total_tokens(events) // 1_000_000, 500)
    goal_xp = sum(token_int(goal.get("xp_awarded")) for goal in completed_goals(goals))
    reuse_bonus = 50 * sum(1 for goal in completed_goals(goals) if goal.get("type") in {"reuse", "automation"})
    return int(token_xp + goal_xp + reuse_bonus)


def quality_per_billion(events: list[dict[str, Any]], goals: list[dict[str, Any]]) -> float:
    billion_units = max(total_tokens(events) / 1_000_000_000, 1.0)
    quality_points = (
        len(completed_goals(goals))
        + len([event for event in events if event.get("outcome") or event.get("primary_success")])
        + len([goal for goal in completed_goals(goals) if goal.get("reflection")])
    )
    return round(quality_points / billion_units, 2)


def source_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for event in events:
        counts[str(event.get("source_agent") or "unknown")] += 1
    return dict(sorted(counts.items()))


def import_window(events: list[dict[str, Any]]) -> str:
    imported = sorted(str(event.get("imported_at")) for event in events if event.get("imported_at"))
    if not imported:
        return "n/a"
    return imported[0] if imported[0] == imported[-1] else f"{imported[0]} to {imported[-1]}"


def high_token_without_outcome(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if token_int(event.get("total_tokens")) >= 1_000_000
        and not event.get("outcome")
        and not event.get("primary_success")
    ]


def workforce_fitness_score(events: list[dict[str, Any]], goals: list[dict[str, Any]]) -> int:
    completed = completed_goals(goals)
    agents = {event.get("source_agent") for event in events}
    score = 20 if events else 0
    if {"claude", "codex"}.issubset(agents):
        score += 15
    score += min(25, len(completed) * 5)
    score += min(20, sum(1 for goal in completed if goal.get("evidence")) * 4)
    score += min(10, sum(1 for goal in completed if goal.get("reflection")) * 2)
    score += min(10, sum(1 for goal in completed if goal.get("type") in {"reuse", "automation", "workforce"}) * 5)
    return min(100, score)


def guild_path_progress(goals: list[dict[str, Any]]) -> dict[str, int]:
    mapping = {
        "Reviewer": {"review", "verification", "judgment"},
        "Builder": {"implementation", "builder", "verification"},
        "Researcher": {"research", "documentation"},
        "Automator": {"reuse", "automation", "craft"},
        "Orchestrator": {"workforce", "delegation", "collaboration"},
    }
    completed = completed_goals(goals)
    progress: dict[str, int] = {}
    for path_name, keys in mapping.items():
        progress[path_name] = sum(
            1
            for goal in completed
            if str(goal.get("type")) in keys or str(goal.get("human_skill")) in keys
        )
    return progress


def streak_summary(goals: list[dict[str, Any]], now: str | None = None) -> dict[str, Any]:
    now_dt = parse_time(now).astimezone()
    current_year, current_week, _ = now_dt.date().isocalendar()
    completed = completed_goals(goals)
    current_week_completed = 0
    recovery_marks = 0
    deep_work_skips = 0
    for goal in completed:
        completed_at = goal.get("completed_at")
        if completed_at:
            goal_date = local_time(str(completed_at)).date()
            year, week, _ = goal_date.isocalendar()
            if (year, week) == (current_year, current_week):
                current_week_completed += 1
        evidence_blob = " ".join(str(item) for item in goal.get("evidence", []))
        if goal.get("type") == "recovery" or "recovery" in evidence_blob.lower():
            recovery_marks += 1
        if "deep_work_skip" in evidence_blob.lower() or "deep-work-skip" in evidence_blob.lower():
            deep_work_skips += 1
    if current_week_completed:
        status = "active"
    elif recovery_marks or deep_work_skips:
        status = "preserved"
    else:
        status = "idle"
    return {
        "status": status,
        "current_week_completed": current_week_completed,
        "recovery_marks": recovery_marks,
        "deep_work_skips": deep_work_skips,
        "recoverable": True,
    }


def agent_roster(events: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    roster: dict[str, dict[str, int]] = {}
    for agent in ("claude", "codex"):
        agent_events = [event for event in events if event.get("source_agent") == agent]
        roster[agent] = {
            "events": len(agent_events),
            "sessions": len({str(event.get("source_session_id")) for event in agent_events if event.get("source_session_id")}),
            "tokens": sum(token_int(event.get("total_tokens")) for event in agent_events),
        }
    return roster


def level_progress_for_xp(xp: int) -> dict[str, Any]:
    total = max(0, token_int(xp))
    remaining = total
    max_level = LEVEL_XP_REQUIREMENTS[-1][0]

    for index, (level, required_xp) in enumerate(LEVEL_XP_REQUIREMENTS):
        required_xp = max(1, int(required_xp))
        is_last = index == len(LEVEL_XP_REQUIREMENTS) - 1
        if remaining < required_xp or is_last:
            current_level_xp = min(remaining, required_xp)
            xp_to_next = 0 if is_last and remaining >= required_xp else max(0, required_xp - current_level_xp)
            return {
                "current_level": level,
                "level_label": f"Lv {level}",
                "current_level_xp": current_level_xp,
                "current_level_required_xp": required_xp,
                "xp_to_next_level": xp_to_next,
                "progress_percent": min(100, (current_level_xp * 100) // required_xp),
                "next_level": None if level >= max_level else level + 1,
                "max_level": max_level,
                "total_xp": total,
            }
        remaining -= required_xp

    return {
        "current_level": max_level,
        "level_label": f"Lv {max_level}",
        "current_level_xp": LEVEL_XP_REQUIREMENTS[-1][1],
        "current_level_required_xp": LEVEL_XP_REQUIREMENTS[-1][1],
        "xp_to_next_level": 0,
        "progress_percent": 100,
        "next_level": None,
        "max_level": max_level,
        "total_xp": total,
    }


def level_for_xp(xp: int) -> int:
    return int(level_progress_for_xp(xp)["current_level"])


def level_label_for_xp(xp: int) -> str:
    return str(level_progress_for_xp(xp)["level_label"])


def dominant_human_skill(goals: list[dict[str, Any]], events: list[dict[str, Any]]) -> str:
    skills = Counter()
    for goal in completed_goals(goals):
        if goal.get("human_skill"):
            skills[str(goal["human_skill"])] += 1
    for event in events:
        if event.get("human_skill_practiced"):
            skills[str(event["human_skill_practiced"])] += 1
    if not skills:
        return "verification"
    return skills.most_common(1)[0][0]


def next_best_challenge(goals: list[dict[str, Any]], events: list[dict[str, Any]]) -> str:
    open_goals = [goal for goal in goals if goal.get("status") != "completed"]
    if open_goals:
        return f"Advance goal: {open_goals[0].get('title')}"
    if not completed_goals(goals):
        return "Define one small verification goal and attach evidence when it is complete."
    agents = {event.get("source_agent") for event in events}
    if not {"claude", "codex"}.issubset(agents):
        return "Run one two-agent workflow: one agent drafts, the other reviews."
    return "Turn one repeated AI workflow into a reusable checklist or script."


def report_lines(
    events: list[dict[str, Any]],
    goals: list[dict[str, Any]],
    now: str | None = None,
    sections: list[str] | None = None,
) -> list[str]:
    rollups = rollup_events(events, now)
    badges = badge_statuses(events, goals)
    xp = total_xp(events, goals)
    skill = dominant_human_skill(goals, events)
    counts = source_counts(events)
    high_without_outcome = high_token_without_outcome(events)
    streak = streak_summary(goals, now)
    lines = ["AI Usage Report", ""]
    lines.append("Source Counts")
    if counts:
        for source, count in counts.items():
            lines.append(f"- {source}: {count}")
    else:
        lines.append("- No source events collected.")
    lines.append(f"- Import Window: {import_window(events)}")
    lines.append("")
    section_names = sections or ["Today", "This Week", "This Month", "Lifetime"]
    for name in section_names:
        summary = rollups[name]
        lines.append(f"{name}")
        lines.append(f"- Total tokens: {format_tokens(summary['total_tokens'])}")
        lines.append(f"- Input tokens: {format_tokens(summary['input_tokens'])}")
        lines.append(f"- Output tokens: {format_tokens(summary['output_tokens'])}")
        lines.append(f"- Reasoning output: {format_tokens(summary['reasoning_output_tokens'])}")
        lines.append(f"- Claude: {format_tokens(summary['by_agent'].get('claude', 0))}")
        lines.append(f"- Codex: {format_tokens(summary['by_agent'].get('codex', 0))}")
        lines.append(f"- Cached input: {format_tokens(summary['cached_input_tokens'])}")
        lines.append(f"- Active sessions: {summary['active_sessions']}")
        lines.append(f"- Agent-assisted tasks: {summary['agent_assisted_tasks']}")
        if summary["top_projects"]:
            projects = ", ".join(f"{path} ({format_tokens(tokens)})" for path, tokens in summary["top_projects"])
            lines.append(f"- Top projects: {projects}")
        lines.append("")
    lines.append("Current Goals")
    if goals:
        for goal in goals[:10]:
            evidence = len(goal.get("evidence", []))
            lines.append(f"- {goal.get('title')} [{goal.get('status')}] evidence={evidence}")
    else:
        lines.append("- No goals defined.")
    lines.append("")
    lines.append("Badge Progress")
    for name in (
        "Billion Club",
        "Agent Guild Builder",
        "AI-Native Workforce Architect",
        "Two key agents",
        "Verifier Streak",
        "Better Question",
        "Review Muscle",
    ):
        badge = badges[name]
        progress = f"{badge['progress']:.0%}" if isinstance(badge.get("progress"), float) else str(badge.get("progress"))
        lines.append(f"- {name}: {badge['status']} ({progress})")
    lines.append("")
    lines.append(f"XP: {format_tokens(xp)}")
    lines.append(f"Level: {level_label_for_xp(xp)}")
    lines.append("Token-only XP is capped so evidence-backed goals and reusable artifacts remain more valuable.")
    lines.append("")
    lines.append("Human Skill Practiced")
    lines.append(f"- Skill Gained: {skill}")
    lines.append("- Keep: Continue attaching evidence to AI-assisted work.")
    lines.append("- Improve: Convert high-friction sessions into one reusable rule or checklist.")
    lines.append("- Stop: Do not let raw token volume stand in for outcomes.")
    lines.append("")
    lines.append("Next Best Challenge")
    lines.append(f"- {next_best_challenge(goals, events)}")
    lines.append("")
    lines.append("Quality Overlays")
    if high_without_outcome:
        lines.append(f"- {len(high_without_outcome)} high-token usage without linked outcome; treat as a learning opportunity.")
    else:
        lines.append("- No high-token usage without linked outcome detected.")
    lines.append(f"- Workforce fitness score: {workforce_fitness_score(events, goals)}/100")
    lines.append(f"- Quality per billion: {quality_per_billion(events, goals)} evidence/outcome signals")
    lines.append("")
    lines.append("Streaks")
    lines.append(f"- Weekly rhythm: {streak['status']}")
    lines.append(f"- Current-week completed goals: {streak['current_week_completed']}")
    lines.append(f"- Recovery marks: {streak['recovery_marks']}")
    lines.append(f"- Deep-work skips: {streak['deep_work_skips']}")
    lines.append("- Streaks are recoverable; deliberate rest or useful recovery work should not be punished.")
    lines.append("")
    lines.append("Nurture Tone")
    lines.append("- Missed goals, low usage, and failed sessions are reported without shame.")
    return lines


def generate_terminal_report(
    events: list[dict[str, Any]],
    goals: list[dict[str, Any]],
    now: str | None = None,
    sections: list[str] | None = None,
) -> str:
    return "\n".join(report_lines(events, goals, now, sections)) + "\n"


def generate_markdown_report(events: list[dict[str, Any]], goals: list[dict[str, Any]], now: str | None = None) -> str:
    now_dt = parse_time(now)
    rollups = rollup_events(events, now)
    badges = badge_statuses(events, goals)
    xp = total_xp(events, goals)
    skill = dominant_human_skill(goals, events)
    counts = source_counts(events)
    roster = agent_roster(events)
    high_without_outcome = high_token_without_outcome(events)
    streak = streak_summary(goals, now)
    lines = [f"# AI Usage Monthly Report: {now_dt.strftime('%Y-%m')}", ""]
    lines.append("## Source Counts")
    if counts:
        for source, count in counts.items():
            lines.append(f"- {source}: {count}")
    else:
        lines.append("- No source events collected.")
    lines.append(f"- Import Window: {import_window(events)}")
    lines.append("")
    lines.append("## Usage")
    for name in ("This Month", "Lifetime"):
        summary = rollups[name]
        lines.append(f"- {name}: {format_tokens(summary['total_tokens'])} total tokens")
        lines.append(f"  - Claude: {format_tokens(summary['by_agent'].get('claude', 0))}")
        lines.append(f"  - Codex: {format_tokens(summary['by_agent'].get('codex', 0))}")
        lines.append(f"  - Input tokens: {format_tokens(summary['input_tokens'])}")
        lines.append(f"  - Output tokens: {format_tokens(summary['output_tokens'])}")
        lines.append(f"  - Reasoning output: {format_tokens(summary['reasoning_output_tokens'])}")
        lines.append(f"  - Agent-assisted tasks: {summary['agent_assisted_tasks']}")
    lines.append("")
    lines.append("## Goals")
    if goals:
        for goal in goals:
            lines.append(f"- {goal.get('title')} [{goal.get('status')}]")
    else:
        lines.append("- No goals defined.")
    lines.append("")
    lines.append("## Badges")
    for name in ("Billion Club", "Agent Guild Builder", "AI-Native Workforce Architect"):
        badge = badges[name]
        lines.append(f"- {name}: {badge['status']} - {badge['endorsement_text']}")
    lines.append("")
    lines.append("## Agent Roster")
    for agent, stats in roster.items():
        lines.append(
            f"- {agent.title()}: {stats['events']} events, {stats['sessions']} sessions, "
            f"{format_tokens(stats['tokens'])} tokens"
        )
    lines.append("")
    lines.append("## Workforce Fitness")
    lines.append(f"- Score: {workforce_fitness_score(events, goals)}/100")
    lines.append(f"- Quality per billion: {quality_per_billion(events, goals)} evidence/outcome signals")
    lines.append("- Signals: usage consistency, goal completion, evidence, reflection, reuse, and Claude/Codex balance.")
    lines.append("")
    lines.append("## Guild Paths")
    for path_name, count in guild_path_progress(goals).items():
        lines.append(f"- {path_name}: {count} completed evidence-backed goals")
    lines.append("")
    lines.append("## Quality Overlays")
    if high_without_outcome:
        lines.append(f"- {len(high_without_outcome)} high-token usage without linked outcome; turn one into a recovery goal.")
    else:
        lines.append("- No high-token usage without linked outcome detected.")
    lines.append("- Token-only XP is capped; evidence-backed goals and reusable artifacts carry more weight.")
    lines.append("")
    lines.append("## Streaks")
    lines.append(f"- Weekly rhythm: {streak['status']}")
    lines.append(f"- Current-week completed goals: {streak['current_week_completed']}")
    lines.append(f"- Recovery marks: {streak['recovery_marks']}")
    lines.append(f"- Deep-work skips: {streak['deep_work_skips']}")
    lines.append("- Streaks are recoverable; deliberate rest or useful recovery work should not be punished.")
    lines.append("")
    lines.append("## Monthly Council")
    lines.append("- what the AI workforce helped with: completed goals, verified outputs, reusable artifacts, and agent-assisted sessions.")
    lines.append("- where agents wasted time: high-token usage without linked outcome and goals missing evidence.")
    lines.append("- which collaboration pattern improved: use one agent to draft and another to review when both are active.")
    lines.append("- Keep: Continue linking AI work to evidence.")
    lines.append("- Improve: Practice one smaller, clearer delegation loop.")
    lines.append("- Stop: Do not treat token volume as proof of progress.")
    lines.append(f"- Skill Gained: {skill}")
    lines.append(f"- Next Challenge: {next_best_challenge(goals, events)}")
    lines.append(f"- XP: {format_tokens(xp)} ({level_label_for_xp(xp)})")
    lines.append("- Tone: missed goals and low usage are reported without shame.")
    return "\n".join(lines) + "\n"


def collect_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect Claude and Codex usage into a local AI usage ledger.")
    parser.add_argument("--repo-root", default=".", type=Path)
    parser.add_argument("--claude-dir", default=Path.home() / ".claude", type=Path)
    parser.add_argument("--codex-dir", default=Path.home() / ".codex", type=Path)
    parser.add_argument("--now", default=None)
    args = parser.parse_args(argv)
    summary = collect_usage(args.repo_root, args.claude_dir, args.codex_dir, args.now)
    print(
        f"scanned={summary['scanned']} imported={summary['imported']} "
        f"updated={summary['updated']} skipped_existing={summary['skipped_existing']} events={summary['events_file']}"
    )
    return 0


def goal_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage AI usage goals.")
    parser.add_argument("--repo-root", default=".", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("--title", required=True)
    add.add_argument("--type", required=True, dest="goal_type")
    add.add_argument("--period", required=True)
    add.add_argument("--target", type=int, default=1)
    add.add_argument("--human-skill", default="")
    complete = sub.add_parser("complete")
    complete.add_argument("goal_id")
    complete.add_argument("--evidence", action="append", default=[])
    complete.add_argument("--reflection", default="")
    update = sub.add_parser("update")
    update.add_argument("goal_id")
    update.add_argument("--status", choices=sorted(VALID_GOAL_STATUSES))
    update.add_argument("--progress", type=int)
    sub.add_parser("list")
    args = parser.parse_args(argv)
    goals_file = default_goals_file(args.repo_root)
    try:
        if args.command == "add":
            goal = add_goal(goals_file, args.title, args.goal_type, args.period, args.target, args.human_skill)
            print(f"added goal {goal['goal_id']}")
            return 0
        if args.command == "complete":
            goal = complete_goal(goals_file, args.goal_id, args.evidence, args.reflection)
            print(f"completed goal {goal['goal_id']}")
            return 0
        if args.command == "update":
            goal = update_goal(goals_file, args.goal_id, status=args.status, progress=args.progress)
            print(f"updated goal {goal['goal_id']} {goal.get('status')} progress={goal.get('progress')}")
            return 0
        for goal in load_goals(goals_file):
            print(f"{goal.get('goal_id')} {goal.get('status')} {goal.get('title')}")
        return 0
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2


def report_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report local AI usage, goals, badges, and human growth signals.")
    parser.add_argument("--repo-root", default=".", type=Path)
    parser.add_argument("--now", default=None)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--day")
    parser.add_argument("--week")
    parser.add_argument("--month")
    parser.add_argument("--lifetime", action="store_true")
    args = parser.parse_args(argv)
    now = args.now
    sections: list[str] | None = None
    if args.day:
        now = f"{args.day}T12:00:00Z"
        sections = ["Today"]
    elif args.week:
        try:
            year_text, week_text = args.week.split("-W", 1)
            week_day = datetime.fromisocalendar(int(year_text), int(week_text), 3)
            now = week_day.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        except (ValueError, TypeError):
            print(f"invalid --week value: {args.week}", file=sys.stderr)
            return 2
        sections = ["This Week"]
    elif args.month:
        now = f"{args.month}-15T12:00:00Z"
        sections = ["This Month"]
    elif args.lifetime:
        sections = ["Lifetime"]
    events = read_events(default_events_file(args.repo_root))
    goals = load_goals(default_goals_file(args.repo_root))
    report = generate_terminal_report(events, goals, now, sections)
    print(report, end="")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(generate_markdown_report(events, goals, now), encoding="utf-8")
    return 0
