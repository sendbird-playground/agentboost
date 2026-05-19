from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

from agentboost.core import (
    badge_statuses,
    collect_usage,
    completed_goals,
    default_events_file,
    default_goals_file,
    format_tokens,
    high_token_without_outcome,
    import_window,
    level_for_xp,
    level_progress_for_xp,
    load_goals,
    local_time,
    next_best_challenge,
    parse_time,
    read_events,
    rollup_events,
    summarize_events,
    source_counts,
    streak_summary,
    total_xp,
    workforce_fitness_score,
)
from agentboost.identity_draft import (
    build_identity_drafts,
    default_sources as default_identity_sources,
    write_identity_drafts,
)


NotificationSender = Callable[[str, str], Any]


def default_notifications_file(repo_root: Path) -> Path:
    return Path(repo_root) / "data" / "ai-usage" / "sidebar-notifications.json"


def default_settings_file(repo_root: Path) -> Path:
    return Path(repo_root) / "data" / "ai-usage" / "settings.json"


def default_tips_cache_file(repo_root: Path) -> Path:
    return Path(repo_root) / "data" / "ai-usage" / "tips-cache.json"


def default_usage_refresh_file(repo_root: Path) -> Path:
    return Path(repo_root) / "data" / "ai-usage" / "sidebar-usage-refresh.json"


def default_network_activity_file(repo_root: Path) -> Path:
    return Path(repo_root) / "data" / "ai-usage" / "sidebar-network-activity.json"


def default_review_state_file(repo_root: Path) -> Path:
    return _workflow_state_file(Path(repo_root), "review-state.md")


def default_review_log_file(repo_root: Path) -> Path:
    return _workflow_state_file(Path(repo_root), "review-log.md")


def _workflow_state_file(repo_root: Path, filename: str) -> Path:
    canonical = Path(repo_root) / "skill" / "public" / "two-phase-execution" / "common" / "state" / filename
    legacy = Path(repo_root) / "skill" / filename
    if canonical.exists() or not legacy.exists():
        return canonical
    return legacy


def meta_review_state(repo_root: Path, now: str | None = None) -> dict[str, Any]:
    state_file = default_review_state_file(Path(repo_root))
    state = _read_review_state(state_file)
    latest_score = _int_value(state.get("Latest meta-review score"), 0)
    tasks = _int_value(state.get("Non-trivial tasks since last meta-review"), 0)
    circuit_breakers = _int_value(state.get("Circuit-breakers since last meta-review"), 0)
    repeats = _int_value(state.get("Repeated-assumption failures since last meta-review"), 0)
    last_review = str(state.get("Last meta-review") or "")
    days_since = _days_since_review(last_review, now)
    score_status = _score_status(latest_score)

    status = "ok"
    reason = "Meta-review is up to date."
    due = False
    if latest_score < 60:
        status = "blocked"
        reason = f"Score {latest_score} is below 60."
        due = True
    elif tasks >= 5:
        status = "due"
        reason = f"{tasks} non-trivial tasks since last meta-review."
        due = True
    elif days_since is not None and days_since >= 7:
        status = "due"
        reason = f"{days_since} days since last meta-review."
        due = True
    elif circuit_breakers >= 2:
        status = "due"
        reason = f"{circuit_breakers} circuit-breakers since last meta-review."
        due = True
    elif latest_score <= 74 and tasks >= 2:
        status = "due"
        reason = f"Score {latest_score} and {tasks} tasks since last meta-review."
        due = True
    elif latest_score <= 74 and days_since is not None and days_since >= 3:
        status = "due"
        reason = f"Score {latest_score} and {days_since} days since last meta-review."
        due = True

    return {
        "status": status,
        "due": due,
        "reason": reason,
        "last_review": last_review,
        "days_since_last_review": days_since,
        "tasks_since_last_review": tasks,
        "circuit_breakers_since_last_review": circuit_breakers,
        "repeated_assumption_failures": repeats,
        "latest_score": latest_score,
        "score_status": score_status,
        "state_file": str(state_file),
    }


def complete_meta_review_from_app(repo_root: Path, *, score: int | None = None, now: str | None = None) -> dict[str, Any]:
    return perform_meta_review_from_app(repo_root, score=score, now=now)


def perform_meta_review_from_app(repo_root: Path, *, score: int | None = None, now: str | None = None) -> dict[str, Any]:
    repo_root = Path(repo_root)
    state_file = default_review_state_file(repo_root)
    review_log = default_review_log_file(repo_root)
    before = meta_review_state(repo_root, now)
    resolved_score = int(score if score is not None else before.get("latest_score") or 0)
    status = _score_status(resolved_score)
    today = parse_time(now).astimezone().date().isoformat()
    artifact = _write_app_meta_review_artifact(
        repo_root,
        today=today,
        before=before,
        score=resolved_score,
        status=status,
        score_source="provided by app action" if score is not None else "previous latest score",
    )

    state = _read_review_state(state_file)
    state["Last meta-review"] = today
    state["Non-trivial tasks since last meta-review"] = "0"
    state["Circuit-breakers since last meta-review"] = "0"
    state["Repeated-assumption failures since last meta-review"] = "0"
    state["Latest meta-review score"] = str(resolved_score)
    state["Score status"] = status
    _write_review_state(state_file, state)
    _append_app_meta_review_log(review_log, today, before, resolved_score, status)
    clear_meta_review_notification_prompts(default_notifications_file(repo_root))
    after = meta_review_state(repo_root, now)
    after["completed_by"] = "ai-system app"
    after["review_artifact"] = str(artifact)
    return after


def perform_skill_prompt_review_from_app(repo_root: Path, *, now: str | None = None) -> dict[str, Any]:
    repo_root = Path(repo_root)
    today = parse_time(now).astimezone().date().isoformat()
    skill_paths, prompt_paths = _skill_prompt_review_inventory(repo_root)
    artifact = _write_skill_prompt_review_artifact(
        repo_root,
        today=today,
        skill_paths=skill_paths,
        prompt_paths=prompt_paths,
    )
    return {
        "status": "reviewed",
        "review_artifact": str(artifact),
        "skills_reviewed": len(skill_paths),
        "prompts_reviewed": len(prompt_paths),
    }


def perform_identity_update_from_app(repo_root: Path, *, now: str | None = None) -> dict[str, Any]:
    repo_root = Path(repo_root)
    today = parse_time(now).astimezone().date().isoformat()
    sources = default_identity_sources(repo_root)
    drafts = build_identity_drafts(sources)
    output_dir = _unique_identity_update_dir(repo_root, today)
    written = write_identity_drafts(drafts, output_dir)
    artifact = _write_identity_update_summary(
        repo_root,
        output_dir=output_dir,
        today=today,
        drafts=drafts,
        written=written,
        sources=sources,
    )
    return {
        "status": "reviewed",
        "review_artifact": str(artifact),
        "written": written,
        "source_file_count": drafts.source_files,
        "evidence_items": drafts.evidence_items,
        "personality_theme_count": drafts.personality_theme_count,
        "thinking_theme_count": drafts.thinking_theme_count,
    }


def identity_update_state(repo_root: Path, now: str | None = None) -> dict[str, Any]:
    latest = _latest_identity_update_summary(repo_root)
    progress = 1 if latest and _identity_update_summary_is_this_week(latest, now) else 0
    state: dict[str, Any] = {
        "status": "done" if progress else "active",
        "progress": progress,
        "goal": 1,
        "metric": "identity_update_this_week",
        "command_hint": "bin/agentboost --do-identity-update",
        "evidence_hint": "identity draft update artifact for the current week",
        "reason": "Identity drafts were updated this week." if progress else "No personality/thinking-path draft update this week.",
        "review_artifact": "",
        "source_file_count": 0,
        "evidence_items": 0,
        "personality_theme_count": 0,
        "thinking_theme_count": 0,
    }
    if latest:
        state.update(_read_identity_update_summary_metrics(latest))
        state["review_artifact"] = str(latest)
        latest_date = _identity_update_date_from_path(latest)
        state["updated_at"] = latest_date.isoformat() if latest_date else ""
    return state


def _skill_prompt_review_inventory(repo_root: Path) -> tuple[list[Path], list[Path]]:
    repo_root = Path(repo_root)
    skill_paths = sorted((repo_root / "skill" / "public").glob("**/SKILL.md"))
    prompt_candidates = [
        repo_root / "AGENTS.md",
        repo_root / "adapters" / "claude" / "CLAUDE.md",
        repo_root / "adapters" / "codex" / "instructions.md",
        repo_root / "identity" / "personality.md",
        repo_root / "identity" / "thinkingpath.md",
    ]
    prompt_paths = [path for path in prompt_candidates if path.exists()]
    return skill_paths, prompt_paths


def _write_skill_prompt_review_artifact(
    repo_root: Path,
    *,
    today: str,
    skill_paths: list[Path],
    prompt_paths: list[Path],
) -> Path:
    review_dir = _skill_prompt_review_dir(repo_root)
    review_dir.mkdir(parents=True, exist_ok=True)
    path = _unique_skill_prompt_review_artifact_path(review_dir, today)

    def rel(path: Path) -> str:
        try:
            return path.relative_to(repo_root).as_posix()
        except ValueError:
            return path.as_posix()

    skill_lines = [f"- `{rel(path)}`" for path in skill_paths] or ["- No public SKILL.md files found."]
    prompt_lines = [f"- `{rel(path)}`" for path in prompt_paths] or ["- No prompt or identity files found."]
    text = "\n".join(
        [
            "# AgentBoost Skill and Prompt Review",
            "",
            "## Review Window",
            "",
            f"- Completed by: ai-system app",
            f"- Review date: {today}",
            f"- Skills reviewed: {len(skill_paths)}",
            f"- Prompts reviewed: {len(prompt_paths)}",
            "",
            "## Skills",
            "",
            *skill_lines,
            "",
            "## Prompts",
            "",
            *prompt_lines,
            "",
            "## Review Checklist",
            "",
            "- Check whether any skill overlaps or contradicts the current workflow rules.",
            "- Check whether Claude and Codex prompts still point at the canonical ai-system contracts.",
            "- Check whether identity/personality guidance reflects recent reviewed closeouts.",
            "- Convert any repeated lesson into the nearest skill, prompt, or AGENTS.md rule.",
            "",
            "## Result",
            "",
            "- Weekly skills/prompts review artifact created from local files.",
            "",
        ]
    )
    path.write_text(text, encoding="utf-8")
    return path


def _unique_skill_prompt_review_artifact_path(review_dir: Path, today: str) -> Path:
    base = review_dir / f"skill-prompt-review-{today}-agentboost.md"
    if not base.exists():
        return base
    index = 2
    while True:
        candidate = review_dir / f"skill-prompt-review-{today}-agentboost-{index}.md"
        if not candidate.exists():
            return candidate
        index += 1


def refresh_usage_if_stale(
    repo_root: Path,
    *,
    claude_dir: Path | None = None,
    codex_dir: Path | None = None,
    now: str | None = None,
    refresh_file: Path | None = None,
    min_interval_seconds: int = 5,
) -> dict[str, Any]:
    repo_root = Path(repo_root)
    refresh_file = Path(refresh_file or default_usage_refresh_file(repo_root))
    current = parse_time(now).replace(microsecond=0)
    current_iso = current.isoformat().replace("+00:00", "Z")
    previous = _read_json_object(refresh_file)
    last_refreshed = str(previous.get("last_refreshed_at") or "")
    if last_refreshed:
        elapsed = (current - parse_time(last_refreshed)).total_seconds()
        if 0 <= elapsed < min_interval_seconds:
            return {
                "skipped": True,
                "reason": "fresh",
                "last_refreshed_at": last_refreshed,
                "min_interval_seconds": min_interval_seconds,
                "refresh_file": str(refresh_file),
            }

    summary = collect_usage(repo_root, claude_dir=claude_dir, codex_dir=codex_dir, imported_at=current_iso)
    refresh_file.parent.mkdir(parents=True, exist_ok=True)
    refresh_file.write_text(
        json.dumps(
            {
                "last_refreshed_at": current_iso,
                "min_interval_seconds": min_interval_seconds,
                "summary": summary,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    result = dict(summary)
    result["skipped"] = False
    result["last_refreshed_at"] = current_iso
    result["min_interval_seconds"] = min_interval_seconds
    result["refresh_file"] = str(refresh_file)
    return result


def memory_monitor_state(
    *,
    total_bytes: int,
    used_bytes: int,
    threshold_percent: int = 80,
) -> dict[str, Any]:
    total = max(0, int(total_bytes or 0))
    used = min(total, max(0, int(used_bytes or 0))) if total else max(0, int(used_bytes or 0))
    threshold = max(1, min(100, int(threshold_percent or 80)))
    if total <= 0:
        return {
            "used_bytes": used,
            "total_bytes": total,
            "available_bytes": 0,
            "used_percent": 0,
            "threshold_percent": threshold,
            "alert": False,
            "status": "unavailable",
            "message": "System memory usage is unavailable.",
        }

    used_percent = int((used * 100) / total)
    alert = used_percent >= threshold
    return {
        "used_bytes": used,
        "total_bytes": total,
        "available_bytes": max(0, total - used),
        "used_percent": used_percent,
        "threshold_percent": threshold,
        "alert": alert,
        "status": "alert" if alert else "ok",
        "message": (
            f"System memory is {used_percent}% used, at or above the {threshold}% alert threshold."
            if alert
            else f"System memory is {used_percent}% used, below the {threshold}% alert threshold."
        ),
    }


def system_memory_monitor(*, threshold_percent: int = 80) -> dict[str, Any]:
    total, used = _system_memory_bytes()
    return memory_monitor_state(total_bytes=total, used_bytes=used, threshold_percent=threshold_percent)


def network_activity_state(
    *,
    outbound_bytes_per_second: int,
    sample_available: bool = True,
) -> dict[str, Any]:
    outbound = max(0, int(outbound_bytes_per_second or 0))
    if outbound <= 0:
        activity_level = "idle"
        interval = 1.5
        rocket_speed = 0.0
    elif outbound < 128_000:
        activity_level = "active"
        interval = 0.9
        rocket_speed = 0.45
    elif outbound < 384_000:
        activity_level = "high"
        interval = 0.45
        rocket_speed = 0.9
    else:
        activity_level = "surge"
        interval = 0.2
        rocket_speed = 1.8

    return {
        "outbound_bytes_per_second": outbound,
        "activity_level": activity_level,
        "animation_interval_seconds": interval,
        "rocket_speed": rocket_speed,
        "has_flame": rocket_speed > 0,
        "sample_available": bool(sample_available),
        "speed_source": "network",
    }


def current_network_activity(
    repo_root: Path,
    *,
    now: str | None = None,
    sample_file: Path | None = None,
) -> dict[str, Any]:
    repo_root = Path(repo_root)
    sample_file = Path(sample_file or default_network_activity_file(repo_root))
    sampled_at = parse_time(now).timestamp()
    outbound_bytes = _outbound_network_bytes()
    previous = _read_json_object(sample_file)
    previous_bytes = _int_value(previous.get("outbound_bytes"), -1)
    previous_sampled_at = _float_value(previous.get("sampled_at"))
    sample_available = (
        outbound_bytes > 0
        and previous_bytes >= 0
        and outbound_bytes >= previous_bytes
        and sampled_at > previous_sampled_at
    )
    outbound_per_second = 0
    if sample_available:
        elapsed = max(0.001, sampled_at - previous_sampled_at)
        outbound_per_second = int((outbound_bytes - previous_bytes) / elapsed)

    sample_file.parent.mkdir(parents=True, exist_ok=True)
    sample_file.write_text(
        json.dumps(
            {
                "outbound_bytes": outbound_bytes,
                "sampled_at": sampled_at,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    state = network_activity_state(
        outbound_bytes_per_second=outbound_per_second,
        sample_available=sample_available,
    )
    state["outbound_bytes"] = outbound_bytes
    state["sample_file"] = str(sample_file)
    return state


def _apply_relative_speed(rockets: list[dict[str, Any]]) -> None:
    """Scale each rocket's speed by its share of peak tokens.

    Absolute `_usage_animation_speed` saturates at 2.4 above ~1M tokens, so
    two agents both running heavy look identical on screen. This pass keeps
    the leader at its full absolute speed and scales the rest by their
    token share — with a 0.4 floor so a tiny-but-active rocket is still
    visibly moving, not frozen.
    """
    peak = 0
    for rocket in rockets:
        peak = max(peak, _token_int(rocket.get("tokens")))
    if peak <= 0:
        return
    for rocket in rockets:
        tokens = _token_int(rocket.get("tokens"))
        if tokens <= 0:
            continue
        ratio = max(0.4, min(1.0, tokens / peak))
        base = float(rocket.get("speed") or 0.0)
        rocket["speed"] = round(base * ratio, 3)
        rocket["animation_interval_seconds"] = _animation_interval_for_speed(rocket["speed"])


def _artifact_files(repo_root: Path | None) -> list[Path]:
    if repo_root is None:
        return []
    artifact_dir = Path(repo_root) / "data" / "turn-artifacts"
    if not artifact_dir.is_dir():
        return []
    return sorted(artifact_dir.glob("*.md"))


def _artifacts_today(repo_root: Path | None, now: str | None) -> int:
    today = parse_time(now).astimezone().date()
    count = 0
    for path in _artifact_files(repo_root):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        except OSError:
            continue
        if mtime.date() == today:
            count += 1
    return count


def _artifacts_this_week(repo_root: Path | None, now: str | None) -> int:
    target = parse_time(now).astimezone().date()
    target_year, target_week, _ = target.isocalendar()
    count = 0
    for path in _artifact_files(repo_root):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        except OSError:
            continue
        year, week, _ = mtime.date().isocalendar()
        if (year, week) == (target_year, target_week):
            count += 1
    return count


def _skill_invocations_this_week(repo_root: Path | None, skill_name: str, now: str | None) -> int:
    target = parse_time(now).astimezone().date()
    target_year, target_week, _ = target.isocalendar()
    needle = f"skills_fired:"
    count = 0
    for path in _artifact_files(repo_root):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        except OSError:
            continue
        year, week, _ = mtime.date().isocalendar()
        if (year, week) != (target_year, target_week):
            continue
        try:
            head = path.read_text(encoding="utf-8")[:800]
        except OSError:
            continue
        for line in head.splitlines():
            if line.startswith(needle) and skill_name in line:
                count += 1
                break
    return count


def _runs_started_today(repo_root: Path | None, now: str | None) -> int:
    if repo_root is None:
        return 0
    try:
        from agentboost.runs import list_runs
        runs = list_runs(repo_root)
    except Exception:
        return 0
    today = parse_time(now).astimezone().date()
    count = 0
    for run in runs:
        try:
            started = local_time(run.started_at).date()
        except Exception:
            continue
        if started == today:
            count += 1
    return count


def _runs_completed_this_week(repo_root: Path | None, now: str | None) -> int:
    if repo_root is None:
        return 0
    try:
        from agentboost.runs import list_runs
        runs = list_runs(repo_root)
    except Exception:
        return 0
    target = parse_time(now).astimezone().date()
    target_year, target_week, _ = target.isocalendar()
    count = 0
    for run in runs:
        if run.status != "done" or not run.ended_at:
            continue
        try:
            ended = local_time(run.ended_at).date()
        except Exception:
            continue
        year, week, _ = ended.isocalendar()
        if (year, week) == (target_year, target_week):
            count += 1
    return count


def _meta_reviews_this_week(repo_root: Path | None, now: str | None) -> int:
    if repo_root is None:
        return 0
    meta_dir = Path(repo_root) / "skill" / "public" / "two-phase-execution" / "common" / "meta-reviews"
    if not meta_dir.is_dir():
        return 0
    target = parse_time(now).astimezone().date()
    target_year, target_week, _ = target.isocalendar()
    count = 0
    for path in meta_dir.glob("meta-review-*.md"):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        except OSError:
            continue
        year, week, _ = mtime.date().isocalendar()
        if (year, week) == (target_year, target_week):
            count += 1
    return count


def _skill_prompt_review_dir(repo_root: Path) -> Path:
    return Path(repo_root) / "skill" / "public" / "two-phase-execution" / "common" / "skill-prompt-reviews"


def _skill_prompt_reviews_this_week(repo_root: Path | None, now: str | None) -> int:
    if repo_root is None:
        return 0
    review_dir = _skill_prompt_review_dir(Path(repo_root))
    if not review_dir.is_dir():
        return 0
    week_start = _sunday_week_start(parse_time(now).astimezone().date())
    week_end = week_start + timedelta(days=7)
    count = 0
    for path in review_dir.glob("skill-prompt-review-*.md"):
        review_date = _date_from_skill_prompt_review_name(path.name)
        if review_date is None:
            try:
                review_date = datetime.fromtimestamp(path.stat().st_mtime).astimezone().date()
            except OSError:
                continue
        if week_start <= review_date < week_end:
            count += 1
    return count


def _identity_update_dir(repo_root: Path) -> Path:
    return Path(repo_root) / "identity" / "drafts"


def _unique_identity_update_dir(repo_root: Path, today: str) -> Path:
    root = _identity_update_dir(repo_root)
    root.mkdir(parents=True, exist_ok=True)
    base = root / f"identity-update-{today}-agentboost"
    if not base.exists():
        return base
    index = 2
    while True:
        candidate = root / f"identity-update-{today}-agentboost-{index}"
        if not candidate.exists():
            return candidate
        index += 1


def _write_identity_update_summary(
    repo_root: Path,
    *,
    output_dir: Path,
    today: str,
    drafts: Any,
    written: dict[str, str],
    sources: list[Path],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = output_dir / "summary.md"

    def rel(path: str | Path) -> str:
        candidate = Path(path)
        try:
            return candidate.relative_to(repo_root).as_posix()
        except ValueError:
            return candidate.as_posix()

    source_lines = [f"- `{rel(source)}`" for source in sources] or ["- No reviewed evidence sources found."]
    text = "\n".join(
        [
            "# AgentBoost Identity Update",
            "",
            "## Review Window",
            "",
            "- Completed by: ai-system app",
            f"- Review date: {today}",
            f"- Evidence items: {drafts.evidence_items}",
            f"- Source files: {drafts.source_files}",
            f"- Personality themes: {drafts.personality_theme_count}",
            f"- Thinking themes: {drafts.thinking_theme_count}",
            f"- Personality draft: `{rel(written['personality'])}`",
            f"- Thinking path draft: `{rel(written['thinking'])}`",
            "",
            "## Sources",
            "",
            *source_lines,
            "",
            "## Result",
            "",
            "- Created reviewable personality and thinking-path drafts.",
            "- Canonical identity files were not overwritten.",
            "",
        ]
    )
    summary.write_text(text, encoding="utf-8")
    return summary


def _identity_update_summaries(repo_root: Path | None) -> list[Path]:
    if repo_root is None:
        return []
    root = _identity_update_dir(Path(repo_root))
    if not root.is_dir():
        return []
    return sorted(root.glob("identity-update-*-agentboost*/summary.md"))


def _latest_identity_update_summary(repo_root: Path | None) -> Path | None:
    summaries = _identity_update_summaries(repo_root)
    if not summaries:
        return None
    return max(
        summaries,
        key=lambda path: (
            _identity_update_date_from_path(path) or date.min,
            path.stat().st_mtime if path.exists() else 0,
        ),
    )


def _identity_updates_this_week(repo_root: Path | None, now: str | None) -> int:
    return sum(
        1
        for path in _identity_update_summaries(repo_root)
        if _identity_update_summary_is_this_week(path, now)
    )


def _identity_update_summary_is_this_week(path: Path, now: str | None) -> bool:
    update_date = _identity_update_date_from_path(path)
    if update_date is None:
        try:
            update_date = datetime.fromtimestamp(path.stat().st_mtime).astimezone().date()
        except OSError:
            return False
    week_start = _sunday_week_start(parse_time(now).astimezone().date())
    return week_start <= update_date < week_start + timedelta(days=7)


def _identity_update_date_from_path(path: Path) -> date | None:
    match = re.search(r"identity-update-(\d{4}-\d{2}-\d{2})-agentboost", path.as_posix())
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _read_identity_update_summary_metrics(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    def metric(label: str) -> int:
        match = re.search(rf"- {re.escape(label)}:\s*(\d+)", raw)
        return int(match.group(1)) if match else 0

    def path_metric(label: str) -> str:
        match = re.search(rf"- {re.escape(label)}:\s*`([^`]+)`", raw)
        if not match:
            return ""
        value = match.group(1)
        candidate = Path(value)
        if candidate.is_absolute():
            return candidate.as_posix()
        root = path.parents[3] if len(path.parents) >= 4 else Path.cwd()
        return (root / candidate).as_posix()

    return {
        "source_file_count": metric("Source files"),
        "evidence_items": metric("Evidence items"),
        "personality_theme_count": metric("Personality themes"),
        "thinking_theme_count": metric("Thinking themes"),
        "personality_draft": path_metric("Personality draft"),
        "thinkingpath_draft": path_metric("Thinking path draft"),
    }


def _date_from_skill_prompt_review_name(name: str) -> date | None:
    match = re.search(r"skill-prompt-review-(\d{4}-\d{2}-\d{2})", name)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def daily_missions(
    events: list[dict[str, Any]],
    goals: list[dict[str, Any]],
    now: str | None = None,
    *,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    missions: list[dict[str, Any]] = []
    completed = completed_goals(goals)
    open_goals = [goal for goal in goals if goal.get("status") != "completed"]
    agents = {event.get("source_agent") for event in events}
    today_events = _events_today(events, now)
    today_agents = {event.get("source_agent") for event in today_events}
    today_completed = _goals_completed_today(completed, now)
    daily_target = _self_adjusted_daily_target(events, now)
    daily_target_text = _count_text(daily_target)
    daily_turn_word = "turn" if daily_target == 1 else "turns"
    daily_event_word = "event" if daily_target == 1 else "events"
    high_without_outcome = high_token_without_outcome(events)

    if not events:
        missions.append(
            _mission(
                "collect_usage",
                "Collect today's AI usage",
                "The sidebar has no source events yet.",
                "active",
                "agentboost-usage-collect",
                "data/ai-usage/events.jsonl",
                cadence="daily",
                frequency="1/day",
                progress=0,
                goal=1,
                metric="local_usage_event",
                xp=5,
            )
        )
    else:
        daily_turn_progress = min(daily_target, len(today_events))
        missions.append(
            _mission(
                "daily_ai_turn",
                f"Use {daily_target_text} AI agent {daily_turn_word} today",
                "AgentBoost adjusts the daily target from your recent active-day pace.",
                _mission_status(daily_turn_progress, daily_target),
                f"Run {daily_target_text} Claude or Codex {daily_turn_word} on real tasks today",
                f"{daily_target_text} local Claude/Codex {daily_event_word} today",
                cadence="daily",
                frequency=f"{daily_target}/day",
                progress=daily_turn_progress,
                goal=daily_target,
                metric="local_usage_event",
                xp=5,
                adaptive=True,
                target_source="recent_active_day_average",
                target_window_days=14,
            )
        )

    if not goals:
        missions.append(
            _mission(
                "define_verification_goal",
                "Define one verification goal",
                "Growth badges need evidence-backed goals, not token volume alone.",
                "active",
                'agentboost-usage-goal add --title "Verify one AI-assisted task" --type verification --period YYYY-MM',
                "goal in data/ai-usage/goals.json",
                cadence="daily",
                frequency="1/day",
                progress=0,
                goal=1,
                metric="goal_defined",
                check_cost="loaded_goals_only",
                xp=5,
            )
        )

    if open_goals:
        first = open_goals[0]
        title = str(first.get("title") or "Open AI goal")
        missions.append(
            _mission(
                "advance_open_goal",
                f"Move goal forward: {title}",
                "A daily goal nudge should create momentum, not force same-day proof of completion.",
                "active",
                "agentboost-usage-goal list",
                f"next step or note against {first.get('goal_id')}",
                cadence="daily",
                frequency="1/day",
                progress=min(1, len(today_completed)),
                goal=1,
                metric="goal_completed_today",
                check_cost="loaded_goals_only",
                xp=10,
            )
        )

    if repo_root is not None:
        runs_today = _runs_started_today(repo_root, now)
        missions.append(
            _mission(
                "daily_open_run",
                "Open one run today",
                "Runs anchor a coherent unit of work — brief, artifacts, closeout — to a single id so AgentBoost can show progress instead of guessing from token rows.",
                _mission_status(min(1, runs_today), 1),
                "bin/ai-run start --title \"<what you're about to do>\"",
                "entry in data/runs/index.jsonl dated today",
                cadence="daily",
                frequency="1/day",
                progress=min(1, runs_today),
                goal=1,
                metric="runs_started_today",
                xp=10,
            )
        )

    if repo_root is not None:
        artifacts_today = _artifacts_today(repo_root, now)
        missions.append(
            _mission(
                "daily_artifact_capture",
                "Capture one durable turn artifact today",
                "Every non-trivial Claude turn should leave a markdown artifact under data/turn-artifacts/ so lessons compound.",
                _mission_status(min(1, artifacts_today), 1),
                "Let Claude finish a non-trivial task (file change, build, PR) — the Stop hook writes the artifact",
                "file under data/turn-artifacts/ dated today",
                cadence="daily",
                frequency="1/day",
                progress=min(1, artifacts_today),
                goal=1,
                metric="turn_artifact_today",
                xp=10,
            )
        )

    if events and not {"claude", "codex"}.issubset(agents):
        agent_progress = min(2, len({agent for agent in today_agents if agent in {"claude", "codex"}}))
        missions.append(
            _mission(
                "two_agent_workflow",
                "Run one two-agent workflow",
                "Use one agent to draft or explore and another to verify.",
                _mission_status(agent_progress, 2),
                "Use Claude and Codex on the same concrete task",
                "task closeout naming both agents",
                cadence="daily",
                frequency="2 agents/day",
                progress=agent_progress,
                goal=2,
                metric="active_agents_today",
                xp=10,
            )
        )

    if high_without_outcome:
        missions.append(
            _mission(
                "recover_high_token_work",
                "Recover one high-token session",
                "Convert expensive exploration into a reusable lesson.",
                "todo",
                'agentboost-usage-goal add --title "Recover a high-token session" --type recovery --period YYYY-MM',
                "recovery note or workflow rule",
                cadence="daily",
                frequency="when flagged",
                progress=0,
                goal=1,
                metric="recovery_note",
                check_cost="loaded_events_only",
                xp=10,
            )
        )

    has_verified = any(goal.get("type") in {"verification", "review", "workforce"} for goal in completed)
    has_reuse = any(goal.get("type") in {"reuse", "automation"} for goal in completed)
    if has_verified and not has_reuse:
        missions.append(
            _mission(
                "create_reusable_artifact",
                "Turn one verified win into a reusable artifact",
                "Reusable scripts, checklists, and docs make the badge system feel earned.",
                "todo",
                'agentboost-usage-goal add --title "Create one reusable AI workflow" --type reuse --period YYYY-MM',
                "script, checklist, docs page, or skill update",
                cadence="daily",
                frequency="1/day",
                progress=0,
                goal=1,
                metric="reusable_artifact",
                check_cost="loaded_goals_only",
                xp=15,
            )
        )

    if not missions:
        missions.append(
            _mission(
                "next_best_challenge",
                next_best_challenge(goals, events),
                "The sidebar found no urgent gaps, so take the best next growth step.",
                "todo",
                "agentboost-usage-report",
                "task closeout or completed goal",
                cadence="daily",
                frequency="1/day",
                progress=0,
                goal=1,
                metric="next_growth_step",
                xp=5,
            )
        )

    return missions[:7]


def weekly_missions(
    events: list[dict[str, Any]],
    goals: list[dict[str, Any]],
    now: str | None = None,
    *,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    missions: list[dict[str, Any]] = []
    completed = completed_goals(goals)
    open_goals = [goal for goal in goals if goal.get("status") != "completed"]
    weekly_events = _events_this_week(events, now)
    weekly_completed = _goals_completed_this_week(completed, now)
    weekly_agents = {event.get("source_agent") for event in weekly_events}
    active_workdays = _active_workdays_this_week(events, now)
    weekly_target = _self_adjusted_weekly_target(events, now)
    high_without_outcome = high_token_without_outcome(weekly_events or events)

    missions.append(
        _mission(
            "weekly_ai_streak",
            f"Build a {weekly_target}-workday AI rhythm",
            "AgentBoost caps weekly pacing to the five normal workdays.",
            _mission_status(active_workdays, weekly_target),
            f"Use Claude or Codex on {weekly_target} workdays this week",
            f"{weekly_target} local AI-usage workdays in the current week",
            cadence="weekly",
            frequency=f"{weekly_target}/week",
            progress=min(weekly_target, active_workdays),
            goal=weekly_target,
            metric="active_workdays",
            xp=25,
            adaptive=True,
            target_source="recent_weekly_workdays",
            target_window_days=28,
        )
    )

    if not weekly_events:
        missions.append(
            _mission(
                "weekly_collect_usage",
                "Collect this week's AI usage",
                "Weekly progress starts with a current local ledger.",
                "active",
                "agentboost-usage-collect",
                "data/ai-usage/events.jsonl",
                cadence="weekly",
                frequency="1/week",
                progress=0,
                goal=1,
                metric="local_usage_event",
                xp=10,
            )
        )

    skill_prompt_reviews = _skill_prompt_reviews_this_week(repo_root, now)
    review_progress = min(1, skill_prompt_reviews)
    missions.append(
        _mission(
            "weekly_skill_prompt_review",
            "Review current skills and prompts",
            "Keep the harness sharp by reviewing skills, agent prompts, and identity guidance once per work week.",
            _mission_status(review_progress, 1),
            "bin/agentboost --do-skill-prompt-review",
            "skill/prompt review artifact for the current week",
            cadence="weekly",
            frequency="1/week",
            progress=review_progress,
            goal=1,
            metric="skill_prompt_review_this_week",
            check_cost="local_artifact_scan",
            xp=25,
        )
    )

    identity_updates = _identity_updates_this_week(repo_root, now)
    identity_progress = min(1, identity_updates)
    missions.append(
        _mission(
            "weekly_identity_update",
            "Update personality and thinking path",
            "Generate reviewable identity drafts from recent reviewed work once per week.",
            _mission_status(identity_progress, 1),
            "bin/agentboost --do-identity-update",
            "identity draft update artifact for the current week",
            cadence="weekly",
            frequency="1/week",
            progress=identity_progress,
            goal=1,
            metric="identity_update_this_week",
            check_cost="local_artifact_scan",
            xp=25,
        )
    )

    if open_goals:
        first = open_goals[0]
        title = str(first.get("title") or "Open AI goal")
        completed_progress = min(1, len(weekly_completed))
        missions.append(
            _mission(
                "weekly_finish_open_goal",
                f"Finish weekly goal: {title}",
                "Closing an existing goal is higher leverage than starting another one.",
                _mission_status(completed_progress, 1),
                f"agentboost-usage-goal complete {first.get('goal_id')} --evidence task-log:<entry>",
                "completed goal with reflection",
                cadence="weekly",
                frequency="1/week",
                progress=completed_progress,
                goal=1,
                metric="goal_completed_this_week",
                check_cost="loaded_goals_only",
                xp=20,
            )
        )

    if repo_root is not None:
        runs_completed = _runs_completed_this_week(repo_root, now)
        missions.append(
            _mission(
                "weekly_close_run",
                "Close one run this week",
                "Closing a run with a closeout proves the loop actually finishes, not just starts.",
                _mission_status(min(1, runs_completed), 1),
                "bin/ai-run end <run_id> --status done --closeout <path>",
                "run with status=done in data/runs/index.jsonl this week",
                cadence="weekly",
                frequency="1/week",
                progress=min(1, runs_completed),
                goal=1,
                metric="runs_completed_this_week",
                xp=25,
            )
        )

    if repo_root is not None:
        meta_count = _meta_reviews_this_week(repo_root, now)
        weekly_artifacts = _artifacts_this_week(repo_root, now)
        missions.append(
            _mission(
                "weekly_meta_review",
                "Apply one meta-review this week",
                "The workflow improves at the class-of-problem level only when a meta-review actually lands.",
                _mission_status(min(1, meta_count), 1),
                "Run the configured meta-review aggregate command, fill the template, then reset the review counter with the configured workflow helper.",
                "file under skill/two-phase-execution/common/meta-reviews/ dated this week",
                cadence="weekly",
                frequency="1/week",
                progress=min(1, meta_count),
                goal=1,
                metric="meta_review_applied",
                xp=30,
            )
        )
        review_invocations = _skill_invocations_this_week(repo_root, "code-review", now)
        missions.append(
            _mission(
                "weekly_review_skill_invocations",
                "Trigger the code-review skill 3 times this week",
                "Author/reviewer self-discipline only compounds when invoked on real PR work.",
                _mission_status(min(3, review_invocations), 3),
                "Run gh pr create / push to a feature branch — the PreToolUse hook reminds, the skill runs",
                "turn artifacts with `code-review` in skills_fired frontmatter this week",
                cadence="weekly",
                frequency="3/week",
                progress=min(3, review_invocations),
                goal=3,
                metric="review_skill_invocation",
                xp=20,
            )
        )

    if weekly_events and not {"claude", "codex"}.issubset(weekly_agents):
        weekly_agent_progress = min(2, len({agent for agent in weekly_agents if agent in {"claude", "codex"}}))
        missions.append(
            _mission(
                "weekly_two_agent_loop",
                "Run one Claude + Codex loop this week",
                "Use one model to draft or critique and the other to verify.",
                _mission_status(weekly_agent_progress, 2),
                "Use both Claude and Codex on one concrete task",
                "closeout naming both agents",
                cadence="weekly",
                frequency="2 agents/week",
                progress=weekly_agent_progress,
                goal=2,
                metric="active_agents_this_week",
                xp=15,
            )
        )

    has_reuse = any(goal.get("type") in {"reuse", "automation"} for goal in completed)
    if completed and not has_reuse:
        missions.append(
            _mission(
                "ship_reusable_workflow",
                "Ship one reusable AI workflow artifact",
                "A reusable script, checklist, or doc turns one good run into compounding progress.",
                "todo",
                'agentboost-usage-goal add --title "Ship one reusable AI workflow" --type reuse --period YYYY-Www',
                "script, checklist, docs page, or skill update",
                cadence="weekly",
                frequency="1/week",
                progress=0,
                goal=1,
                metric="reusable_artifact",
                check_cost="loaded_goals_only",
                xp=25,
            )
        )

    if high_without_outcome:
        missions.append(
            _mission(
                "weekly_recovery",
                "Recover one expensive AI session",
                "High-token work becomes useful when it leaves a rule, report, or reusable path.",
                "todo",
                'agentboost-usage-goal add --title "Recover one expensive AI session" --type recovery --period YYYY-Www',
                "recovery note or reusable workflow",
                cadence="weekly",
                frequency="when flagged",
                progress=0,
                goal=1,
                metric="recovery_note",
                xp=15,
            )
        )

    if not missions:
        missions.append(
            _mission(
                "weekly_next_challenge",
                "Pick one higher-leverage AI workflow for the week",
                "The system found no urgent gaps, so raise the bar deliberately.",
                "todo",
                "agentboost-usage-report --week YYYY-Www",
                "weekly goal or reusable artifact",
                cadence="weekly",
                frequency="1/week",
                progress=0,
                goal=1,
                metric="next_growth_step",
                xp=15,
            )
        )

    return missions[:7]


def build_sidebar_state(
    repo_root: Path,
    *,
    now: str | None = None,
    notification_file: Path | None = None,
) -> dict[str, Any]:
    repo_root = Path(repo_root)
    notification_file = Path(notification_file or default_notifications_file(repo_root))
    events = read_events(default_events_file(repo_root))
    goals = load_goals(default_goals_file(repo_root))
    badges = sidebar_badges(events, goals)
    representative_badges = representative_badge_list(badges, notification_file)
    representative = representative_badges[0] if representative_badges else None
    notified = _notified_badge_ids(notification_file)
    new_achievements = [
        badge for badge in badges if badge.get("status") == "earned" and str(badge.get("badge_id")) not in notified
    ]
    earned_badges = [badge for badge in badges if badge.get("status") == "earned"]
    rollups = agentboost_rollups(events, now)
    daily = daily_missions(events, goals, now, repo_root=repo_root)
    weekly = weekly_missions(events, goals, now, repo_root=repo_root)
    identity_update = identity_update_state(repo_root, now)
    base_xp = total_xp(events, goals)
    mission_xp = earned_mission_xp(daily + weekly)
    xp = base_xp + mission_xp
    level_progress = level_progress_for_xp(xp)
    today_activity = token_activity(rollups)
    settings = load_settings(default_settings_file(repo_root))
    split_io = bool(settings.get("display", {}).get("split_io_rockets", False))
    recent_activity = recent_token_activity(events, now, split_io=split_io)
    network_activity = current_network_activity(repo_root, now=now)
    running_activity = running_agent_activity()
    rotating_status_views = status_views(events, rollups, now)
    return {
        "app": "AgentBoost",
        "repo_root": str(repo_root),
        "generated_at": now,
        "events_count": len(events),
        "goals_count": len(goals),
        "source_counts": source_counts(events),
        "import_window": import_window(events),
        "xp": xp,
        "level": level_for_xp(xp),
        "level_label": level_progress["level_label"],
        "level_progress": level_progress,
        "xp_breakdown": {
            "base_xp": base_xp,
            "mission_xp": mission_xp,
        },
        "workforce_fitness_score": workforce_fitness_score(events, goals),
        "rollups": rollups,
        "token_activity": today_activity,
        "recent_token_activity": recent_activity,
        "status_views": rotating_status_views,
        "agentboost_daily_7d": daily_usage_buckets_7d(events, now),
        "network_activity": network_activity,
        "running_agent_activity": running_activity,
        "status_animation_activity": status_animation_activity(
            recent_activity,
            today_activity,
            network_activity,
            running_activity,
        ),
        "memory_monitor": system_memory_monitor(),
        "badges": badges,
        "badge_inventory": badge_inventory(badges, representative_badges),
        "earned_badges": earned_badges,
        "representative_badge": representative,
        "representative_badges": representative_badges,
        "meta_review": meta_review_state(repo_root, now),
        "identity_update": identity_update,
        "new_achievements": new_achievements,
        "daily_missions": daily,
        "weekly_missions": weekly,
        "streak": streak_summary(goals, now),
        "active_run": _active_run_snapshot(repo_root),
        "recent_runs": _recent_runs_snapshot(repo_root, limit=5),
        "notification_file": str(notification_file),
    }


def earned_mission_xp(missions: list[dict[str, Any]]) -> int:
    return sum(_int_value(mission.get("xp")) for mission in missions if mission.get("status") == "done")


def _active_run_snapshot(repo_root: Path) -> dict[str, Any] | None:
    try:
        from agentboost.runs import active_run
        run = active_run(repo_root)
    except Exception:
        return None
    if run is None:
        return None
    from dataclasses import asdict
    return asdict(run)


def _recent_runs_snapshot(repo_root: Path, limit: int = 5) -> list[dict[str, Any]]:
    try:
        from agentboost.runs import list_runs
        runs = list_runs(repo_root)
    except Exception:
        return []
    from dataclasses import asdict
    return [asdict(run) for run in runs[:limit]]


def sidebar_badges(events: list[dict[str, Any]], goals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {"earned": 0, "in_progress": 1, "locked": 2}
    badges = []
    for badge in badge_statuses(events, goals).values():
        item = dict(badge)
        progress = item.get("progress")
        if isinstance(progress, float):
            item["progress_percent"] = int(round(progress * 100))
        else:
            item["progress_percent"] = int(progress or 0)
        badges.append(item)
    badges.sort(key=lambda badge: (order.get(str(badge.get("status")), 9), str(badge.get("name"))))
    return badges


def token_activity(rollups: dict[str, dict[str, Any]]) -> dict[str, Any]:
    today_tokens = 0
    today = rollups.get("Today")
    if isinstance(today, dict):
        today_tokens = int(today.get("total_tokens") or 0)
    by_agent = today.get("by_agent") if isinstance(today, dict) else {}
    active_agents = [
        agent
        for agent in ("claude", "codex")
        if isinstance(by_agent, dict) and _token_int(by_agent.get(agent)) > 0
    ]

    if today_tokens <= 0:
        intensity = "idle"
        interval = 1.5
        emoji = ""
        rocket_speed = 0.0
    elif today_tokens < 10_000_000:
        intensity = "active"
        interval = 0.9
        emoji = "⚡"
        rocket_speed = 0.45
    elif today_tokens < 100_000_000:
        intensity = "high"
        interval = 0.45
        emoji = "🚀"
        rocket_speed = 0.9
    else:
        intensity = "surge"
        interval = 0.2
        emoji = "🔥"
        rocket_speed = 1.8

    return {
        "today_tokens": today_tokens,
        "intensity": intensity,
        "animation_interval_seconds": interval,
        "emoji": emoji,
        "rocket_speed": rocket_speed,
        "active_agents": active_agents,
        "rocket_count": 2 if {"claude", "codex"}.issubset(active_agents) else 1,
    }


def agentboost_rollups(events: list[dict[str, Any]], now: str | None = None) -> dict[str, dict[str, Any]]:
    rollups = rollup_events(events, now)
    rollups["This Week"] = summarize_events(_events_this_week(events, now))
    return rollups


def recent_token_activity(
    events: list[dict[str, Any]],
    now: str | None = None,
    *,
    split_io: bool = False,
) -> dict[str, Any]:
    now_local = parse_time(now).astimezone()
    cutoff = now_local - timedelta(minutes=1)
    last_1m_tokens = 0
    last_1m_by_agent: dict[str, int] = {"claude": 0, "codex": 0}
    input_by_agent: dict[str, int] = {"claude": 0, "codex": 0}
    output_by_agent: dict[str, int] = {"claude": 0, "codex": 0}
    active_agents: set[str] = set()
    for event in events:
        occurred = local_time(event.get("occurred_at"))
        if cutoff <= occurred <= now_local:
            tokens = _token_int(event.get("total_tokens"))
            last_1m_tokens += tokens
            source_agent = str(event.get("source_agent") or "").strip().lower()
            if source_agent in {"claude", "codex"}:
                active_agents.add(source_agent)
                last_1m_by_agent[source_agent] += tokens
                input_by_agent[source_agent] += _token_int(event.get("input_tokens")) + _token_int(
                    event.get("cached_input_tokens")
                )
                output_by_agent[source_agent] += _token_int(event.get("output_tokens")) + _token_int(
                    event.get("reasoning_output_tokens")
                )

    if last_1m_tokens <= 0:
        activity_level = "idle"
        rocket_state = "waiting"
        has_flame = False
    elif last_1m_tokens < 50_000:
        activity_level = "moderate"
        rocket_state = "flying"
        has_flame = True
    else:
        activity_level = "high"
        rocket_state = "surging"
        has_flame = True
    rocket_speed = _usage_animation_speed(last_1m_tokens)
    rocket_altitude = _usage_animation_altitude(last_1m_tokens)
    ordered_active = [agent for agent in ("claude", "codex") if agent in active_agents]

    payload: dict[str, Any] = {
        "last_1m_tokens": last_1m_tokens,
        "display_tokens": compact_token_count(last_1m_tokens),
        "activity_level": activity_level,
        "rocket_state": rocket_state,
        "rocket_speed": rocket_speed,
        "rocket_altitude": rocket_altitude,
        "animation_interval_seconds": _animation_interval_for_speed(rocket_speed),
        "has_flame": has_flame,
        "active_agents": ordered_active,
        "rocket_count": 2 if {"claude", "codex"}.issubset(active_agents) else 1,
        "agent_usage": {
            agent: {
                "last_1m_tokens": last_1m_by_agent[agent],
                "display_tokens": compact_token_count(last_1m_by_agent[agent]),
                "input_tokens": input_by_agent[agent],
                "output_tokens": output_by_agent[agent],
            }
            for agent in ("claude", "codex")
        },
        "split_io_enabled": bool(split_io),
    }

    if split_io:
        rockets: list[dict[str, Any]] = []
        for agent in ("claude", "codex"):
            for channel, channel_tokens in (
                ("input", input_by_agent[agent]),
                ("output", output_by_agent[agent]),
            ):
                rockets.append(
                    {
                        "agent": agent,
                        "channel": channel,
                        "tokens": channel_tokens,
                        "display_tokens": compact_token_count(channel_tokens),
                        "speed": _usage_animation_speed(channel_tokens),
                        "altitude": _usage_animation_altitude(channel_tokens),
                        "animation_interval_seconds": _animation_interval_for_speed(
                            _usage_animation_speed(channel_tokens)
                        ),
                        "has_flame": channel_tokens > 0,
                    }
                )
        _apply_relative_speed(rockets)
        payload["rockets"] = rockets
        payload["rocket_count"] = len(rockets)

    return payload


def status_views(
    events: list[dict[str, Any]],
    rollups: dict[str, dict[str, Any]],
    now: str | None = None,
) -> list[dict[str, Any]]:
    lifetime_tokens = _rollup_token_total(rollups, "Lifetime")
    now_local = parse_time(now).astimezone()
    seven_day_start = now_local - timedelta(days=7)
    minute_start = now_local - timedelta(minutes=1)
    seven_day_tokens = _tokens_between(events, seven_day_start, now_local, include_end=True)
    minute_tokens = _tokens_between(events, minute_start, now_local, include_end=True)
    minute_by_agent = _tokens_between_by_agent(events, minute_start, now_local, include_end=True)
    seven_day_by_agent = _tokens_between_by_agent(events, seven_day_start, now_local, include_end=True)
    lifetime_by_agent = _rollup_by_agent(rollups, "Lifetime")
    active_minute_agents = [agent for agent in ("claude", "codex") if minute_by_agent.get(agent, 0) > 0]
    if set(active_minute_agents) == {"claude", "codex"}:
        views: list[dict[str, Any]] = []
        for agent in ("claude", "codex"):
            views.extend(
                [
                    _status_view(
                        view_id=f"{agent}_token_per_minute",
                        label="1m",
                        tokens=minute_by_agent.get(agent, 0),
                        prefix=f"{_agent_label(agent)} 1m",
                        per_minute=True,
                        scope="agent",
                        agent=agent,
                    ),
                    _status_view(
                        view_id=f"{agent}_last_7d_cumulative",
                        label="7d",
                        tokens=seven_day_by_agent.get(agent, 0),
                        prefix=f"{_agent_label(agent)} 7d",
                        scope="agent",
                        agent=agent,
                    ),
                    _status_view(
                        view_id=f"{agent}_total_cumulative",
                        label="Total",
                        tokens=lifetime_by_agent.get(agent, 0),
                        prefix=f"{_agent_label(agent)} Total",
                        scope="agent",
                        agent=agent,
                    ),
                ]
            )
        views.extend(
            [
                _status_view(
                    view_id="combined_token_per_minute",
                    label="1m",
                    tokens=minute_tokens,
                    prefix="All 1m",
                    per_minute=True,
                    scope="combined",
                ),
                _status_view(
                    view_id="combined_last_7d_cumulative",
                    label="7d",
                    tokens=seven_day_tokens,
                    prefix="All 7d",
                    scope="combined",
                ),
                _status_view(
                    view_id="combined_total_cumulative",
                    label="Total",
                    tokens=lifetime_tokens,
                    prefix="All Total",
                    scope="combined",
                ),
            ]
        )
        return views

    return [
        {
            "view_id": "last_7d_cumulative",
            "label": "7d",
            "tokens": seven_day_tokens,
            "display_tokens": compact_token_count(seven_day_tokens),
            "display_text": f"7d {compact_token_count(seven_day_tokens)}",
            "trend": "flat",
            "trend_symbol": "flat",
        },
        {
            "view_id": "total_cumulative",
            "label": "Total",
            "tokens": lifetime_tokens,
            "display_tokens": compact_token_count(lifetime_tokens),
            "display_text": f"Total {compact_token_count(lifetime_tokens)}",
            "trend": "flat",
            "trend_symbol": "flat",
        },
        {
            "view_id": "token_per_minute",
            "label": "Token/min",
            "tokens": minute_tokens,
            "display_tokens": f"{compact_token_count(minute_tokens)}/min",
            "display_text": f"{compact_token_count(minute_tokens)}/min",
            "trend": "flat",
            "trend_symbol": "flat",
        },
    ]


def _status_view(
    *,
    view_id: str,
    label: str,
    tokens: int,
    prefix: str,
    per_minute: bool = False,
    scope: str,
    agent: str | None = None,
) -> dict[str, Any]:
    display_tokens = f"{compact_token_count(tokens)}/min" if per_minute else compact_token_count(tokens)
    view: dict[str, Any] = {
        "view_id": view_id,
        "label": label,
        "tokens": tokens,
        "display_tokens": display_tokens,
        "display_text": f"{prefix} {display_tokens}",
        "trend": "flat",
        "trend_symbol": "flat",
        "scope": scope,
    }
    if agent:
        view["agent"] = agent
        view["agent_label"] = _agent_label(agent)
    return view


def daily_usage_buckets_7d(events: list[dict[str, Any]], now: str | None = None) -> list[dict[str, Any]]:
    today = parse_time(now).astimezone().date()
    days = [today - timedelta(days=offset) for offset in range(6, -1, -1)]
    buckets: dict[date, dict[str, Any]] = {
        day: {"day": day.strftime("%a"), "date": day.isoformat(), "claude": 0, "codex": 0} for day in days
    }
    for event in events:
        occurred_day = local_time(event.get("occurred_at")).astimezone().date()
        source_agent = str(event.get("source_agent") or "").strip().lower()
        if occurred_day not in buckets or source_agent not in {"claude", "codex"}:
            continue
        buckets[occurred_day][source_agent] += _token_int(event.get("total_tokens"))
    return [buckets[day] for day in days]


def _agent_label(agent: str) -> str:
    return {"claude": "Claude", "codex": "Codex"}.get(agent, agent.title())


def _rollup_by_agent(rollups: dict[str, dict[str, Any]], name: str) -> dict[str, int]:
    rollup = rollups.get(name)
    if not isinstance(rollup, dict):
        return {}
    by_agent = rollup.get("by_agent")
    if not isinstance(by_agent, dict):
        return {}
    return {str(agent).lower(): _token_int(tokens) for agent, tokens in by_agent.items()}


def _tokens_between_by_agent(
    events: list[dict[str, Any]],
    start,
    end,
    *,
    include_end: bool,
) -> dict[str, int]:
    totals = {"claude": 0, "codex": 0}
    for event in events:
        agent = str(event.get("source_agent") or "").strip().lower()
        if agent not in totals:
            continue
        occurred = local_time(event.get("occurred_at"))
        in_window = start <= occurred <= end if include_end else start <= occurred < end
        if in_window:
            totals[agent] += _token_int(event.get("total_tokens"))
    return totals


def token_window_trend(events: list[dict[str, Any]], now: str | None = None, *, minutes: int = 5) -> dict[str, Any]:
    now_local = parse_time(now).astimezone()
    current_start = now_local - timedelta(minutes=minutes)
    previous_start = now_local - timedelta(minutes=minutes * 2)
    current_tokens = _tokens_between(events, current_start, now_local, include_end=True)
    previous_tokens = _tokens_between(events, previous_start, current_start, include_end=False)
    delta_tokens = current_tokens - previous_tokens

    if delta_tokens > 0:
        trend = "up"
    elif delta_tokens < 0:
        trend = "down"
    else:
        trend = "flat"

    return {
        "tokens": current_tokens,
        "previous_tokens": previous_tokens,
        "delta_tokens": delta_tokens,
        "display_tokens": compact_token_count(current_tokens),
        "trend": trend,
        "trend_symbol": trend,
    }


def status_animation_activity(
    recent: dict[str, Any],
    today: dict[str, Any],
    network: dict[str, Any] | None = None,
    running: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if _float_value(recent.get("rocket_speed")) > 0:
        activity = dict(recent)
        visible_agents = _merged_active_agents(recent, running or {})
        if not visible_agents:
            visible_agents = _merged_active_agents(recent, today)
        if visible_agents:
            activity["active_agents"] = visible_agents
            if not activity.get("split_io_enabled"):
                activity["rocket_count"] = 2 if {"claude", "codex"}.issubset(visible_agents) else 1
        activity["source"] = "recent"
        return _apply_network_animation_speed(activity, network)

    today_tokens = _token_int(today.get("today_tokens"))
    split_io_enabled = bool(recent.get("split_io_enabled"))
    if today_tokens > 0:
        intensity = str(today.get("intensity") or "active")
        active_agents = _merged_active_agents(today, running or {})
        rocket_count = 2 if {"claude", "codex"}.issubset(active_agents) else min(2, max(1, _token_int(today.get("rocket_count"))))
        activity = {
            "last_1m_tokens": _token_int(recent.get("last_1m_tokens")),
            "display_tokens": compact_token_count(today_tokens),
            "activity_level": intensity,
            "rocket_state": "waiting",
            "rocket_speed": 0.0,
            "rocket_altitude": 0,
            "animation_interval_seconds": 1.5,
            "has_flame": False,
            "active_agents": active_agents,
            "rocket_count": rocket_count,
            "source": "today",
        }
        if split_io_enabled:
            activity["split_io_enabled"] = True
            if isinstance(recent.get("rockets"), list):
                activity["rockets"] = recent["rockets"]
                activity["rocket_count"] = len(recent["rockets"]) or rocket_count
            if isinstance(recent.get("agent_usage"), dict):
                activity["agent_usage"] = recent["agent_usage"]
        return _apply_network_animation_speed(activity, network)

    activity = dict(recent)
    running_agents = _agent_list((running or {}).get("active_agents"))
    if running_agents:
        activity["active_agents"] = running_agents
        if not split_io_enabled:
            activity["rocket_count"] = 2 if {"claude", "codex"}.issubset(running_agents) else 1
        activity["source"] = "running"
    else:
        activity["source"] = "recent"
    return _apply_network_animation_speed(activity, network)


def _apply_network_animation_speed(activity: dict[str, Any], network: dict[str, Any] | None) -> dict[str, Any]:
    result = dict(activity)
    usage_speed = _float_value(activity.get("rocket_speed"))
    if not isinstance(network, dict):
        result.setdefault("speed_source", "token_usage")
        return result

    network_speed = _float_value(network.get("rocket_speed"))
    result["outbound_bytes_per_second"] = _token_int(network.get("outbound_bytes_per_second"))
    if usage_speed <= 0:
        result["speed_source"] = "token_usage"
        result["rocket_speed"] = 0.0
        result["rocket_altitude"] = 0
        result["animation_interval_seconds"] = _float_value(activity.get("animation_interval_seconds")) or 1.5
        result["has_flame"] = False
        return result

    if network_speed > usage_speed:
        result["speed_source"] = "network"
        result["rocket_speed"] = network_speed
        result["animation_interval_seconds"] = _float_value(network.get("animation_interval_seconds"))
        result["source"] = "network"
    else:
        result["speed_source"] = "token_usage"
        result["rocket_speed"] = usage_speed
        result["animation_interval_seconds"] = _float_value(activity.get("animation_interval_seconds")) or _animation_interval_for_speed(usage_speed)
    result["has_flame"] = True
    return result


def _usage_animation_speed(tokens: int) -> float:
    tokens = max(0, int(tokens or 0))
    if tokens <= 0:
        return 0.0
    if tokens < 50_000:
        return round(0.35 + (tokens / 50_000) * 0.45, 3)
    return round(min(2.4, 0.8 + ((tokens - 50_000) / 950_000) * 1.6), 3)


def _usage_animation_altitude(tokens: int) -> int:
    tokens = max(0, int(tokens or 0))
    if tokens <= 0:
        return 0
    if tokens < 50_000:
        return min(50, max(1, tokens // 1_000))
    return min(250, 50 + (tokens - 50_000) // 5_000)


def _animation_interval_for_speed(speed: float) -> float:
    speed = max(0.0, float(speed or 0.0))
    if speed <= 0:
        return 1.5
    return round(max(0.12, 1.2 - speed * 0.35), 3)


def compact_token_count(value: int) -> str:
    return format_tokens(value)


def _agent_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    agents = {str(agent).strip().lower() for agent in value}
    return [agent for agent in ("claude", "codex") if agent in agents]


def _merged_active_agents(*states: dict[str, Any]) -> list[str]:
    agents: set[str] = set()
    for state in states:
        if not isinstance(state, dict):
            continue
        agents.update(_agent_list(state.get("active_agents")))
    return [agent for agent in ("claude", "codex") if agent in agents]


def running_agent_activity(process_lines: Iterable[str] | None = None) -> dict[str, Any]:
    if process_lines is None:
        process_lines = _running_process_lines()
    found: set[str] = set()
    for line in process_lines:
        normalized = str(line).lower()
        if "claude" in normalized:
            found.add("claude")
        if "codex" in normalized:
            found.add("codex")
    active_agents = [agent for agent in ("claude", "codex") if agent in found]
    return {
        "active_agents": active_agents,
        "rocket_count": 2 if {"claude", "codex"}.issubset(active_agents) else 1,
    }


def _running_process_lines() -> list[str]:
    try:
        run = subprocess.run(
            ["pgrep", "-fl", "claude|codex"],
            text=True,
            capture_output=True,
            check=False,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if run.returncode not in (0, 1):
        return []
    return [line for line in run.stdout.splitlines() if line.strip()]


def _rollup_token_total(rollups: dict[str, dict[str, Any]], name: str) -> int:
    rollup = rollups.get(name)
    if not isinstance(rollup, dict):
        return 0
    return _token_int(rollup.get("total_tokens"))


def _tokens_between(
    events: list[dict[str, Any]],
    start: Any,
    end: Any,
    *,
    include_end: bool,
) -> int:
    total = 0
    for event in events:
        occurred = local_time(event.get("occurred_at"))
        if occurred < start:
            continue
        if include_end:
            if occurred > end:
                continue
        elif occurred >= end:
            continue
        total += _token_int(event.get("total_tokens"))
    return total


REPRESENTATIVE_BADGE_LIMIT = 1


def representative_badge_list(badges: list[dict[str, Any]], notification_file: Path) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for selected_id in _representative_badge_ids(notification_file):
        badge = _badge_by_id(badges, selected_id)
        if badge and badge.get("status") == "earned":
            selected.append(dict(badge))
    if selected:
        return selected[:REPRESENTATIVE_BADGE_LIMIT]

    for badge in badges:
        if badge.get("status") == "earned" and badge.get("type") == "milestone":
            return [dict(badge)]
    for badge in badges:
        if badge.get("status") == "earned":
            return [dict(badge)]
    for badge in badges:
        if badge.get("status") == "in_progress":
            return [dict(badge)]
    return [dict(badges[0])] if badges else []


def representative_badge(badges: list[dict[str, Any]], notification_file: Path) -> dict[str, Any] | None:
    selected = representative_badge_list(badges, notification_file)
    return selected[0] if selected else None


def badge_inventory(
    badges: list[dict[str, Any]],
    representative: dict[str, Any] | list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    representatives = representative if isinstance(representative, list) else ([representative] if representative else [])
    representative_ranks = {
        str(badge.get("badge_id")): index + 1
        for index, badge in enumerate(representatives[:REPRESENTATIVE_BADGE_LIMIT])
    }
    inventory = []
    for badge in badges:
        item = dict(badge)
        rank = representative_ranks.get(str(item.get("badge_id")), 0)
        item["is_representative"] = rank > 0
        item["representative_rank"] = rank
        item["can_select"] = item.get("status") == "earned"
        inventory.append(item)
    return inventory


def select_representative_badges(
    notification_file: Path,
    badges: list[dict[str, Any]],
    badge_ids: list[str],
) -> bool:
    earned = {
        str(badge.get("badge_id")): badge
        for badge in badges
        if badge.get("status") == "earned"
    }
    selected: list[str] = []
    for badge_id in badge_ids:
        normalized = str(badge_id)
        if normalized in earned and normalized not in selected:
            selected.append(normalized)
        if len(selected) >= REPRESENTATIVE_BADGE_LIMIT:
            break
    if not selected:
        return False
    notification_file = Path(notification_file)
    ledger = _read_notification_ledger(notification_file)
    ledger["representative_badge_id"] = selected[0]
    ledger["representative_badge_ids"] = selected
    _write_notification_ledger(notification_file, ledger)
    return True


def select_representative_badge(notification_file: Path, badges: list[dict[str, Any]], badge_id: str) -> bool:
    return select_representative_badges(notification_file, badges, [badge_id])


def notify_new_badges(
    badges: list[dict[str, Any]],
    notification_file: Path,
    *,
    now: str | None = None,
    sender: NotificationSender | None = None,
    settings_file: Path | None = None,
) -> list[dict[str, Any]]:
    if not _notification_category_enabled(settings_file, "achievements"):
        return []
    notification_file = Path(notification_file)
    ledger = _read_notification_ledger(notification_file)
    existing = {str(row.get("badge_id")) for row in ledger.get("badges", []) if row.get("badge_id")}
    newly_earned = [
        badge for badge in badges if badge.get("status") == "earned" and str(badge.get("badge_id")) not in existing
    ]
    if not newly_earned:
        return []

    sender = sender or send_macos_notification
    for badge in newly_earned:
        title = f"Badge earned: {badge.get('name')}"
        message = str(badge.get("endorsement_text") or badge.get("human_skill") or "New agentboost achievement unlocked.")
        try:
            sender(title, message)
        except Exception:
            pass
        ledger.setdefault("badges", []).append(
            {
                "badge_id": badge.get("badge_id"),
                "name": badge.get("name"),
                "notified_at": now,
                "status": badge.get("status"),
            }
        )
    notification_file.parent.mkdir(parents=True, exist_ok=True)
    notification_file.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return newly_earned


def notify_growth_updates(
    state: dict[str, Any],
    notification_file: Path,
    *,
    now: str | None = None,
    sender: NotificationSender | None = None,
    settings_file: Path | None = None,
) -> dict[str, int]:
    sender = sender or send_macos_notification
    badges = notify_new_badges(
        list(state.get("badges", [])),
        notification_file,
        now=now,
        sender=sender,
        settings_file=settings_file,
    )
    meta_review_count = notify_meta_review_due(
        state,
        notification_file,
        now=now,
        sender=sender,
        settings_file=settings_file,
    )
    mission_count = 0
    if not _notification_category_enabled(settings_file, "missions"):
        return {"badges": len(badges), "missions": 0, "meta_review": meta_review_count, "total": len(badges) + meta_review_count}
    ledger = _read_notification_ledger(Path(notification_file))
    mission_prompts = ledger.setdefault("mission_prompts", [])
    existing = {str(row.get("key")) for row in mission_prompts if isinstance(row, dict)}

    for cadence, period, missions in (
        ("daily", _day_period(now), state.get("daily_missions", [])),
        ("weekly", _week_period(now), state.get("weekly_missions", [])),
    ):
        mission = _first_actionable_mission(missions)
        if not mission:
            continue
        key = f"{cadence}:{period}:{mission.get('mission_id')}"
        if key in existing:
            continue
        title = f"AgentBoost {cadence} mission"
        message = str(mission.get("title") or "Use AI on one meaningful task.")
        try:
            sender(title, message)
        except Exception:
            pass
        mission_prompts.append(
            {
                "key": key,
                "cadence": cadence,
                "period": period,
                "mission_id": mission.get("mission_id"),
                "title": mission.get("title"),
                "notified_at": now,
            }
        )
        existing.add(key)
        mission_count += 1

    _write_notification_ledger(Path(notification_file), ledger)
    return {
        "badges": len(badges),
        "missions": mission_count,
        "meta_review": meta_review_count,
        "total": len(badges) + mission_count + meta_review_count,
    }


def notify_meta_review_due(
    state: dict[str, Any],
    notification_file: Path,
    *,
    now: str | None = None,
    sender: NotificationSender | None = None,
    settings_file: Path | None = None,
) -> int:
    if not _notification_category_enabled(settings_file, "workflow"):
        return 0
    meta = state.get("meta_review")
    if not isinstance(meta, dict) or not meta.get("due"):
        return 0
    state_file_raw = str(meta.get("state_file") or "")
    if state_file_raw and not Path(state_file_raw).exists():
        return 0
    status = str(meta.get("status") or "due")
    last_review = str(meta.get("last_review") or state_file_raw or "unknown")
    key = f"meta-review-due:{last_review}:{status}"
    reason = str(meta.get("reason") or "Run a workflow meta-review before more throughput.")
    return _notify_once(
        Path(notification_file),
        "meta_review_prompts",
        key,
        "AgentBoost meta-review due",
        reason,
        sender=sender,
        now=now,
    )


def clear_meta_review_notification_prompts(notification_file: Path) -> None:
    notification_file = Path(notification_file)
    ledger = _read_notification_ledger(notification_file)
    if not ledger.get("meta_review_prompts"):
        return
    ledger["meta_review_prompts"] = []
    _write_notification_ledger(notification_file, ledger)


def notify_memory_pressure(
    monitor: dict[str, Any],
    notification_file: Path,
    *,
    settings_file: Path | None = None,
    sender: NotificationSender | None = None,
    now: str | None = None,
) -> int:
    if not _notification_category_enabled(settings_file, "memory"):
        return 0
    if not isinstance(monitor, dict) or not monitor.get("alert"):
        return 0

    used_percent = _int_value(monitor.get("used_percent"), 0)
    threshold_percent = _int_value(monitor.get("threshold_percent"), 80)
    key = f"memory:{_day_period(now)}:{threshold_percent}"
    ledger = _read_notification_ledger(Path(notification_file))
    alerts = ledger.setdefault("memory_alerts", [])
    if any(isinstance(row, dict) and row.get("key") == key for row in alerts):
        return 0

    title = "AgentBoost memory alert"
    message = (
        f"System memory is {used_percent}% used. "
        "Consider closing idle AI agent sessions before spawning more subagents."
    )
    sender = sender or send_macos_notification
    try:
        sender(title, message)
    except Exception:
        pass
    alerts.append(
        {
            "key": key,
            "used_percent": used_percent,
            "threshold_percent": threshold_percent,
            "notified_at": now,
        }
    )
    _write_notification_ledger(Path(notification_file), ledger)
    return 1


def send_macos_notification(title: str, message: str) -> None:
    script = f"display notification {json.dumps(message)} with title {json.dumps(title)}"
    subprocess.run(["osascript", "-e", script], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def preferred_gui_backend(
    platform_name: str | None = None,
    *,
    appkit_available: bool | None = None,
    tk_available: bool | None = None,
) -> str:
    platform_name = platform_name or sys.platform
    if appkit_available is None:
        appkit_available = _module_available("AppKit")
    if tk_available is None:
        tk_available = _module_available("tkinter")
    if platform_name == "darwin" and appkit_available:
        return "appkit"
    if tk_available:
        return "tk"
    if appkit_available:
        return "appkit"
    return "none"


def launch_sidebar(repo_root: Path, *, now: str | None = None, no_notify: bool = False) -> int:
    backend = preferred_gui_backend()
    if backend == "appkit":
        return launch_appkit_sidebar(repo_root, now=now, no_notify=no_notify)
    if backend == "tk":
        return launch_tk_sidebar(repo_root, now=now, no_notify=no_notify)
    print("agentboost: no supported GUI backend found", file=sys.stderr)
    return 2


def open_menu_bar_app(repo_root: Path, *, app_path: Path | None = None, rebuild: bool = False) -> int:
    try:
        from agentboost.macos_app import APP_NAME, build_agentboost_app, default_agentboost_app_path
    except Exception as exc:
        print(f"agentboost: cannot load macOS app builder: {exc}", file=sys.stderr)
        return 2

    repo_root = Path(repo_root).expanduser().resolve()
    target = Path(app_path or default_agentboost_app_path()).expanduser()
    executable = target / "Contents" / "MacOS" / APP_NAME

    try:
        if rebuild or not executable.exists():
            build_agentboost_app(repo_root, target)
        opened = subprocess.run(["open", str(target)], check=False)
    except (FileNotFoundError, subprocess.CalledProcessError, OSError) as exc:
        print(f"agentboost: cannot open menu bar app: {exc}", file=sys.stderr)
        return 2

    if opened.returncode != 0:
        print(f"agentboost: open failed for {target}", file=sys.stderr)
        return opened.returncode

    print(f"opened {target}")
    return 0


def launch_appkit_sidebar(repo_root: Path, *, now: str | None = None, no_notify: bool = False) -> int:
    try:
        from AppKit import (
            NSApp,
            NSApplication,
            NSApplicationActivationPolicyRegular,
            NSBackingStoreBuffered,
            NSColor,
            NSFloatingWindowLevel,
            NSFont,
            NSMakeRect,
            NSScrollView,
            NSScreen,
            NSTextField,
            NSView,
            NSWindow,
            NSWindowStyleMaskClosable,
            NSWindowStyleMaskResizable,
            NSWindowStyleMaskTitled,
        )
    except Exception as exc:
        print(f"agentboost: cannot import AppKit: {exc}", file=sys.stderr)
        return 2

    repo_root = Path(repo_root)
    notification_file = default_notifications_file(repo_root)
    state = build_sidebar_state(repo_root, now=now, notification_file=notification_file)
    if not no_notify:
        notify_new_badges(state["badges"], notification_file, now=now)

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)

    screen = NSScreen.mainScreen()
    visible = screen.visibleFrame() if screen else NSMakeRect(0, 0, 1440, 900)
    width = 390
    height = min(880, max(640, visible.size.height - 80))
    x = visible.origin.x + visible.size.width - width - 24
    y = visible.origin.y + 40
    style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(x, y, width, height),
        style,
        NSBackingStoreBuffered,
        False,
    )
    window.setTitle_("AgentBoost")
    window.setLevel_(NSFloatingWindowLevel)
    window.setReleasedWhenClosed_(False)

    scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
    scroll.setHasVerticalScroller_(True)
    document_height = 1260
    document = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, width, document_height))
    window.setBackgroundColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.96, 0.98, 0.95, 1))
    scroll.setDocumentView_(document)
    window.setContentView_(scroll)

    y_cursor = document_height - 36

    def add_label(text: str, size: int = 12, bold: bool = False, muted: bool = False, gap: int = 24) -> None:
        nonlocal y_cursor
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(16, y_cursor, width - 40, gap))
        field.setStringValue_(text)
        field.setBezeled_(False)
        field.setBordered_(False)
        field.setDrawsBackground_(False)
        field.setEditable_(False)
        field.setSelectable_(False)
        field.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
        field.setTextColor_(NSColor.secondaryLabelColor() if muted else NSColor.labelColor())
        document.addSubview_(field)
        y_cursor -= gap

    add_label("AgentBoost", 22, True, gap=32)
    add_label(
        f"{state['level']} · {format_tokens(state['xp'])} XP · Fitness {state['workforce_fitness_score']}/100",
        13,
        True,
        gap=28,
    )
    month = state["rollups"]["This Month"]
    lifetime = state["rollups"]["Lifetime"]
    add_label(f"Month {format_tokens(month['total_tokens'])} tokens", 12, gap=22)
    add_label(f"Lifetime {format_tokens(lifetime['total_tokens'])} tokens", 12, gap=22)
    representative = state.get("representative_badge") or {}
    if isinstance(representative, dict) and representative:
        add_label(f"Representative badge: {representative.get('name')}", 12, True, gap=26)

    add_label("Daily Missions", 14, True, gap=30)
    for mission in state["daily_missions"]:
        add_label(f"{_status_symbol(mission['status'])} {mission['title']}", 12, True, gap=24)
        add_label(str(mission["command_hint"]), 10, muted=True, gap=26)

    add_label("Weekly Missions", 14, True, gap=30)
    for mission in state["weekly_missions"]:
        add_label(f"{_status_symbol(mission['status'])} {mission['title']}", 12, True, gap=24)
        add_label(str(mission["command_hint"]), 10, muted=True, gap=26)

    add_label("Badges", 14, True, gap=30)
    for badge in state["badges"][:9]:
        status = str(badge.get("status"))
        add_label(
            f"{_badge_symbol(status)} {badge.get('name')} · {status} · {badge.get('progress_percent', 0)}%",
            12,
            True,
            gap=24,
        )
        detail = str(badge.get("endorsement_text") or badge.get("human_skill") or "")
        add_label(detail[:88], 10, muted=True, gap=24)

    window.makeKeyAndOrderFront_(None)
    NSApp.activateIgnoringOtherApps_(True)
    app.run()
    return 0


def launch_tk_sidebar(repo_root: Path, *, now: str | None = None, no_notify: bool = False) -> int:
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception as exc:
        print(f"agentboost: cannot import tkinter: {exc}", file=sys.stderr)
        return 2

    repo_root = Path(repo_root)
    notification_file = default_notifications_file(repo_root)
    state = build_sidebar_state(repo_root, now=now, notification_file=notification_file)
    if not no_notify:
        notify_new_badges(state["badges"], notification_file, now=now)

    root = tk.Tk()
    root.title("AgentBoost")
    width = 390
    height = min(880, max(640, root.winfo_screenheight() - 120))
    x = max(0, root.winfo_screenwidth() - width - 24)
    y = 60
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.attributes("-topmost", True)
    root.configure(bg="#f5f7f4")

    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("Title.TLabel", font=("Helvetica", 18, "bold"), background="#f5f7f4", foreground="#1f2a24")
    style.configure("Metric.TLabel", font=("Helvetica", 12, "bold"), background="#f5f7f4", foreground="#1f2a24")
    style.configure("Body.TLabel", font=("Helvetica", 11), background="#f5f7f4", foreground="#26312b")
    style.configure("Muted.TLabel", font=("Helvetica", 10), background="#f5f7f4", foreground="#65706a")
    style.configure("Card.TFrame", background="#ffffff", relief="solid", borderwidth=1)

    container = ttk.Frame(root, padding=14)
    container.pack(fill="both", expand=True)
    container.configure(style="Card.TFrame")

    def render() -> None:
        for child in container.winfo_children():
            child.destroy()
        fresh = build_sidebar_state(repo_root, now=now, notification_file=notification_file)
        ttk.Label(container, text="AgentBoost", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            container,
            text=f"{fresh['level']} · {format_tokens(fresh['xp'])} XP · Fitness {fresh['workforce_fitness_score']}/100",
            style="Metric.TLabel",
        ).pack(anchor="w", pady=(4, 8))

        month = fresh["rollups"]["This Month"]
        lifetime = fresh["rollups"]["Lifetime"]
        ttk.Label(
            container,
            text=f"Month {format_tokens(month['total_tokens'])} tokens · Lifetime {format_tokens(lifetime['total_tokens'])}",
            style="Body.TLabel",
        ).pack(anchor="w")
        representative = fresh.get("representative_badge") or {}
        if isinstance(representative, dict) and representative:
            ttk.Label(
                container,
                text=f"Representative badge: {representative.get('name')}",
                style="Metric.TLabel",
            ).pack(anchor="w", pady=(8, 0))

        _section(container, "Daily Missions")
        for mission in fresh["daily_missions"]:
            _row(container, f"{_status_symbol(mission['status'])} {mission['title']}", mission["command_hint"])

        _section(container, "Weekly Missions")
        for mission in fresh["weekly_missions"]:
            _row(container, f"{_status_symbol(mission['status'])} {mission['title']}", mission["command_hint"])

        _section(container, "Badges")
        for badge in fresh["badges"][:8]:
            status = str(badge.get("status"))
            text = f"{_badge_symbol(status)} {badge.get('name')} · {status} · {badge.get('progress_percent', 0)}%"
            detail = str(badge.get("endorsement_text") or badge.get("human_skill") or "")
            _row(container, text, detail)

        ttk.Button(container, text="Refresh", command=render).pack(anchor="e", pady=(12, 0))

    render()
    root.mainloop()
    return 0


def _module_available(module_name: str) -> bool:
    try:
        __import__(module_name)
        return True
    except Exception:
        return False


def sidebar_main(argv: Iterable[str] | None = None, *, default_repo_root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open or inspect the local AgentBoost macOS menu bar app.")
    parser.add_argument("--repo-root", default=default_repo_root or Path("."), type=Path)
    parser.add_argument("--now", default=None)
    parser.add_argument("--notification-file", type=Path)
    parser.add_argument("--state-json", action="store_true")
    parser.add_argument("--notify-only", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--no-system-notify", action="store_true")
    parser.add_argument("--select-representative-badge")
    parser.add_argument("--meta-review-json", action="store_true")
    parser.add_argument("--meta-review-done", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--do-meta-review", action="store_true")
    parser.add_argument("--meta-review-score", type=int)
    parser.add_argument("--do-skill-prompt-review", action="store_true")
    parser.add_argument("--do-identity-update", action="store_true")
    parser.add_argument("--app-path", type=Path, help="Override the native app bundle path for the default launcher.")
    parser.add_argument("--rebuild-app", action="store_true", help="Rebuild the native app bundle before opening it.")
    parser.add_argument("--debug-window", action="store_true", help="Open the legacy Python debug window.")
    parser.add_argument("--settings-json", action="store_true")
    parser.add_argument("--set-notification-enabled", choices=("true", "false"))
    parser.add_argument("--set-notification-category", action="append", default=[])
    parser.add_argument("--set-split-io", choices=("on", "off"))
    parser.add_argument("--set-floating-overlay", choices=("on", "off"))
    parser.add_argument("--inactivity-check", action="store_true")
    parser.add_argument("--tip-notify-only", action="store_true")
    parser.add_argument("--fetch-tips", action="store_true")
    parser.add_argument("--tips-source")
    parser.add_argument("--caffeinate-check", action="store_true")
    parser.add_argument("--memory-check", action="store_true")
    parser.add_argument("--refresh-usage", action="store_true")
    parser.add_argument("--claude-dir", type=Path)
    parser.add_argument("--codex-dir", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)

    repo_root = args.repo_root.expanduser().resolve()
    notification_file = args.notification_file or default_notifications_file(repo_root)
    settings_file = default_settings_file(repo_root)
    tips_cache_file = default_tips_cache_file(repo_root)

    if args.set_split_io is not None:
        settings = load_settings(settings_file)
        settings.setdefault("display", {})["split_io_rockets"] = args.set_split_io == "on"
        save_settings(settings_file, settings)
        print(
            f"split_io_rockets={settings['display']['split_io_rockets']} settings_file={settings_file}"
        )
        return 0

    if args.set_floating_overlay is not None:
        settings = load_settings(settings_file)
        settings.setdefault("display", {})["floating_overlay_enabled"] = args.set_floating_overlay == "on"
        save_settings(settings_file, settings)
        print(
            f"floating_overlay_enabled={settings['display']['floating_overlay_enabled']} settings_file={settings_file}"
        )
        return 0

    if args.set_notification_enabled is not None or args.set_notification_category:
        settings = load_settings(settings_file)
        if args.set_notification_enabled is not None:
            settings.setdefault("notifications", {})["enabled"] = args.set_notification_enabled == "true"
        for spec in args.set_notification_category:
            try:
                category, raw_enabled = spec.split(":", 1)
            except ValueError:
                print(f"invalid notification category setting: {spec}", file=sys.stderr)
                return 2
            category = category.strip()
            if category not in _notification_categories():
                print(f"unknown notification category: {category}", file=sys.stderr)
                return 2
            if raw_enabled not in {"true", "false"}:
                print(f"invalid notification category value: {spec}", file=sys.stderr)
                return 2
            settings.setdefault("notifications", {}).setdefault("categories", {})[category] = raw_enabled == "true"
        save_settings(settings_file, settings)
        print(f"settings_file={settings_file}")
        return 0

    if args.settings_json:
        print(json.dumps(load_settings(settings_file), indent=2, sort_keys=True))
        return 0

    if args.fetch_tips:
        tips = fetch_tips(args.tips_source, tips_cache_file)
        print(f"tips_cached={len(tips)} tips_cache_file={tips_cache_file}")
        return 0

    if args.tip_notify_only:
        sender = (lambda title, message: None) if args.no_system_notify else None
        count = notify_tips(tips_cache_file, notification_file, settings_file=settings_file, sender=sender, now=args.now)
        print(f"tips_notified={count} tips_cache_file={tips_cache_file}")
        return 0

    if args.inactivity_check:
        sender = (lambda title, message: None) if args.no_system_notify else None
        count = notify_inactivity(repo_root, notification_file, settings_file=settings_file, sender=sender, now=args.now)
        print(f"inactivity_notified={count} notification_file={notification_file}")
        return 0

    if args.caffeinate_check:
        sender = (lambda title, message: None) if args.no_system_notify else None
        count = notify_caffeinate(repo_root, notification_file, settings_file=settings_file, sender=sender, now=args.now)
        print(f"caffeinate_notified={count} notification_file={notification_file}")
        return 0

    if args.memory_check:
        sender = (lambda title, message: None) if args.no_system_notify else None
        monitor = system_memory_monitor()
        count = notify_memory_pressure(
            monitor,
            notification_file,
            settings_file=settings_file,
            sender=sender,
            now=args.now,
        )
        print(
            f"memory_alert_notified={count} used_percent={monitor['used_percent']} "
            f"threshold_percent={monitor['threshold_percent']} notification_file={notification_file}"
        )
        return 0

    if args.meta_review_json:
        print(json.dumps(meta_review_state(repo_root, args.now), indent=2, sort_keys=True))
        return 0

    if args.do_meta_review or args.meta_review_done:
        result = perform_meta_review_from_app(repo_root, score=args.meta_review_score, now=args.now)
        print(
            f"meta_review=reviewed score={result['latest_score']} "
            f"status={result['score_status']} state_file={result['state_file']} "
            f"review_artifact={result['review_artifact']}"
        )
        return 0

    if args.do_skill_prompt_review:
        result = perform_skill_prompt_review_from_app(repo_root, now=args.now)
        print(
            f"skill_prompt_review=reviewed skills={result['skills_reviewed']} "
            f"prompts={result['prompts_reviewed']} review_artifact={result['review_artifact']}"
        )
        return 0

    if args.do_identity_update:
        result = perform_identity_update_from_app(repo_root, now=args.now)
        print(
            f"identity_update=reviewed evidence_items={result['evidence_items']} "
            f"source_file_count={result['source_file_count']} review_artifact={result['review_artifact']}"
        )
        return 0

    if args.check:
        return _check(repo_root, notification_file)

    usage_refresh = None
    if args.refresh_usage:
        usage_refresh = refresh_usage_if_stale(
            repo_root,
            claude_dir=args.claude_dir,
            codex_dir=args.codex_dir,
            now=args.now,
        )

    state = build_sidebar_state(repo_root, now=args.now, notification_file=notification_file)
    if usage_refresh is not None:
        state["usage_refresh"] = usage_refresh
    if args.select_representative_badge:
        selected = select_representative_badge(notification_file, state["badges"], args.select_representative_badge)
        if not selected:
            print(f"badge not found: {args.select_representative_badge}", file=sys.stderr)
            return 2
        updated = build_sidebar_state(repo_root, now=args.now, notification_file=notification_file)
        badge = updated.get("representative_badge") or {}
        print(f"representative_badge={badge.get('name')} badge_id={badge.get('badge_id')}")
        return 0

    if args.state_json:
        print(json.dumps(state, indent=2, sort_keys=True))
        return 0

    if args.notify_only:
        sender = (lambda title, message: None) if args.no_system_notify else None
        notified = notify_growth_updates(state, notification_file, now=args.now, sender=sender, settings_file=settings_file)
        print(
            f"notified={notified['total']} badges={notified['badges']} "
            f"missions={notified['missions']} meta_review={notified['meta_review']} "
            f"notification_file={notification_file}"
        )
        return 0

    if args.debug_window:
        return launch_sidebar(repo_root, now=args.now, no_notify=args.no_notify)
    return open_menu_bar_app(repo_root, app_path=args.app_path, rebuild=args.rebuild_app)


def _mission(
    mission_id: str,
    title: str,
    reason: str,
    status: str,
    command_hint: str,
    evidence_hint: str,
    *,
    cadence: str,
    frequency: str,
    progress: int,
    goal: int,
    metric: str,
    xp: int,
    auto_check: bool = True,
    check_cost: str = "loaded_events_only",
    adaptive: bool = False,
    target_source: str | None = None,
    target_window_days: int | None = None,
) -> dict[str, Any]:
    mission = {
        "mission_id": mission_id,
        "title": title,
        "reason": reason,
        "status": status,
        "command_hint": command_hint,
        "evidence_hint": evidence_hint,
        "cadence": cadence,
        "frequency": frequency,
        "progress": max(0, min(int(progress), int(goal))) if goal > 0 else max(0, int(progress)),
        "goal": max(0, int(goal)),
        "metric": metric,
        "xp": max(0, int(xp)),
        "auto_check": auto_check,
        "check_cost": check_cost,
    }
    if adaptive:
        mission["adaptive"] = True
    if target_source:
        mission["target_source"] = target_source
    if target_window_days is not None:
        mission["target_window_days"] = max(0, int(target_window_days))
    return mission


def _mission_status(progress: int, goal: int) -> str:
    if goal > 0 and progress >= goal:
        return "done"
    return "active"


def _count_text(count: int) -> str:
    return "one" if count == 1 else str(count)


def _read_notification_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "badges": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": 1, "badges": []}
    if not isinstance(data, dict):
        return {"version": 1, "badges": []}
    badges = data.get("badges")
    if not isinstance(badges, list):
        data["badges"] = []
    data.setdefault("version", 1)
    return data


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_notification_ledger(path: Path, ledger: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_settings(path: Path) -> dict[str, Any]:
    defaults = {
        "version": 1,
        "notifications": {
            "enabled": True,
            "categories": {category: True for category in _notification_categories()},
        },
        "caffeinate": {"enabled": True},
        "work_hours": {"start": "09:00", "end": "18:00", "workdays": [0, 1, 2, 3, 4]},
        "quiet_hours": {"enabled": False, "start": "22:00", "end": "07:00"},
        "display": {"split_io_rockets": False, "floating_overlay_enabled": False},
    }
    if not path.exists():
        return defaults
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    if not isinstance(data, dict):
        return defaults
    notifications = data.get("notifications")
    if not isinstance(notifications, dict):
        notifications = {}
    categories = notifications.get("categories")
    if not isinstance(categories, dict):
        categories = {}
    display = data.get("display")
    if not isinstance(display, dict):
        display = {}
    merged = {
        "version": data.get("version", 1),
        "notifications": {
            "enabled": bool(notifications.get("enabled", True)),
            "categories": {category: bool(categories.get(category, True)) for category in _notification_categories()},
        },
        "caffeinate": {"enabled": _setting_enabled(data.get("caffeinate"), default=True)},
        "work_hours": _merge_time_window(defaults["work_hours"], data.get("work_hours")),
        "quiet_hours": _merge_time_window(defaults["quiet_hours"], data.get("quiet_hours")),
        "display": {
            "split_io_rockets": bool(display.get("split_io_rockets", False)),
            "floating_overlay_enabled": bool(display.get("floating_overlay_enabled", False)),
        },
    }
    return merged


def save_settings(path: Path, settings: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def fetch_tips(source: str | None, cache_file: Path) -> list[dict[str, Any]]:
    tips: list[dict[str, Any]] = []
    if source:
        try:
            if source.startswith(("http://", "https://")):
                with urllib.request.urlopen(source, timeout=3) as response:
                    raw = response.read().decode("utf-8")
            else:
                raw = Path(source).expanduser().read_text(encoding="utf-8")
            payload = json.loads(raw)
            if isinstance(payload, dict) and isinstance(payload.get("tips"), list):
                tips = [_normalize_tip(tip) for tip in payload["tips"] if isinstance(tip, dict)]
            elif isinstance(payload, list):
                tips = [_normalize_tip(tip) for tip in payload if isinstance(tip, dict)]
            tips = [tip for tip in tips if tip and _tip_is_eligible(tip)]
        except (OSError, ValueError, TimeoutError):
            tips = []
    if tips:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"version": 1, "tips": tips}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return tips
    return _read_cached_tips(cache_file)


def notify_tips(
    tips_cache_file: Path,
    notification_file: Path,
    *,
    settings_file: Path | None = None,
    sender: NotificationSender | None = None,
    now: str | None = None,
) -> int:
    if not _notification_category_enabled(settings_file, "tips"):
        return 0
    tips = _read_cached_tips(tips_cache_file)
    if not tips:
        return 0
    ledger = _read_notification_ledger(notification_file)
    tip_rows = ledger.setdefault("tips", [])
    existing = {str(row.get("tip_id")) for row in tip_rows if isinstance(row, dict)}
    period = _day_period(now)
    if any(isinstance(row, dict) and row.get("period") == period for row in tip_rows):
        return 0
    sender = sender or send_macos_notification
    count = 0
    for tip in tips:
        tip_id = str(tip.get("tip_id") or tip.get("id") or "")
        if not tip_id or tip_id in existing:
            continue
        title = str(tip.get("title") or "AgentBoost tip")
        message = str(tip.get("message") or tip.get("body") or "Try one small AI workflow improvement.")
        try:
            sender(title, message)
        except Exception:
            pass
        tip_rows.append({"tip_id": tip_id, "title": title, "period": period, "notified_at": now})
        existing.add(tip_id)
        count += 1
        break
    _write_notification_ledger(notification_file, ledger)
    return count


def notify_inactivity(
    repo_root: Path,
    notification_file: Path,
    *,
    settings_file: Path | None = None,
    sender: NotificationSender | None = None,
    now: str | None = None,
) -> int:
    if not _notification_category_enabled(settings_file, "inactivity"):
        return 0
    if not _inside_work_hours(settings_file, now):
        return 0
    events = read_events(default_events_file(repo_root))
    recent = recent_token_activity(events, now)
    if recent["last_1m_tokens"] > 0:
        return 0
    last_usage = _last_usage_time(events)
    if last_usage is not None:
        now_dt = parse_time(now).astimezone()
        if now_dt - last_usage < timedelta(minutes=30):
            return 0
    key = f"inactivity:{_day_period(now)}"
    return _notify_once(
        notification_file,
        "inactivity_prompts",
        key,
        "AgentBoost inactivity",
        "No AI token activity in the last minute.",
        sender=sender,
        now=now,
    )


def notify_caffeinate(
    repo_root: Path,
    notification_file: Path,
    *,
    settings_file: Path | None = None,
    sender: NotificationSender | None = None,
    now: str | None = None,
) -> int:
    if not _caffeinate_enabled(settings_file):
        return 0
    if not _notification_category_enabled(settings_file, "caffeinate"):
        return 0
    events = read_events(default_events_file(repo_root))
    recent = recent_token_activity(events, now)
    if recent["last_1m_tokens"] <= 0:
        return 0
    key = f"caffeinate:{_day_period(now)}"
    return _notify_once(
        notification_file,
        "caffeinate_prompts",
        key,
        "AgentBoost caffeinate",
        "Keep the local agentboost loop awake when you are actively working.",
        sender=sender,
        now=now,
    )


def _system_memory_bytes() -> tuple[int, int]:
    if sys.platform == "darwin":
        total, used = _darwin_memory_bytes()
        if total > 0:
            return total, used
    total, used = _proc_meminfo_memory_bytes(Path("/proc/meminfo"))
    if total > 0:
        return total, used
    return _fallback_memory_bytes()


def _darwin_memory_bytes() -> tuple[int, int]:
    try:
        total = int(
            subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            ).stdout.strip()
        )
    except (OSError, ValueError):
        total = 0
    try:
        vm_stat = subprocess.run(
            ["vm_stat"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        ).stdout
    except OSError:
        vm_stat = ""
    used = _parse_darwin_vm_stat_used_bytes(vm_stat)
    if total <= 0 or used <= 0:
        return 0, 0
    return total, min(total, used)


def _parse_darwin_vm_stat_used_bytes(output: str) -> int:
    page_size = 4096
    first_line = output.splitlines()[0] if output.splitlines() else ""
    match = re.search(r"page size of (\d+) bytes", first_line)
    if match:
        page_size = int(match.group(1))
    counts: dict[str, int] = {}
    for line in output.splitlines()[1:]:
        if ":" not in line:
            continue
        name, raw_value = line.split(":", 1)
        value = raw_value.strip().rstrip(".").replace(".", "")
        try:
            counts[name.strip()] = int(value)
        except ValueError:
            continue
    used_pages = (
        counts.get("Pages active", 0)
        + counts.get("Pages wired down", 0)
        + counts.get("Pages occupied by compressor", 0)
    )
    return used_pages * page_size


def _proc_meminfo_memory_bytes(path: Path) -> tuple[int, int]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0, 0
    values: dict[str, int] = {}
    for line in lines:
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        parts = raw_value.strip().split()
        if not parts:
            continue
        try:
            values[key] = int(parts[0]) * 1024
        except ValueError:
            continue
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    if total <= 0 or available < 0:
        return 0, 0
    return total, max(0, total - available)


def _fallback_memory_bytes() -> tuple[int, int]:
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        total_pages = os.sysconf("SC_PHYS_PAGES")
        available_pages = os.sysconf("SC_AVPHYS_PAGES")
    except (OSError, ValueError, AttributeError):
        return 0, 0
    total = int(page_size) * int(total_pages)
    available = int(page_size) * int(available_pages)
    if total <= 0:
        return 0, 0
    return total, max(0, total - available)


def _outbound_network_bytes() -> int:
    if sys.platform == "darwin":
        value = _darwin_outbound_network_bytes()
        if value > 0:
            return value
    value = _linux_outbound_network_bytes(Path("/proc/net/dev"))
    return value


def _darwin_outbound_network_bytes() -> int:
    try:
        output = subprocess.run(
            ["netstat", "-ibn"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        ).stdout
    except OSError:
        return 0
    return _parse_netstat_outbound_bytes(output)


def _parse_netstat_outbound_bytes(output: str) -> int:
    headers: list[str] = []
    per_interface: dict[str, int] = {}
    for line in output.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "Name":
            headers = parts
            continue
        if not headers or len(parts) < len(headers):
            continue
        if parts[0].startswith(("lo", "gif", "stf", "utun", "awdl", "llw")):
            continue
        try:
            obytes_index = headers.index("Obytes")
            obytes = int(parts[obytes_index])
        except (ValueError, IndexError):
            continue
        per_interface[parts[0]] = max(per_interface.get(parts[0], 0), obytes)
    return sum(per_interface.values())


def _linux_outbound_network_bytes(path: Path) -> int:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    total = 0
    for line in lines:
        if ":" not in line:
            continue
        name, values = line.split(":", 1)
        interface = name.strip()
        if interface == "lo":
            continue
        fields = values.split()
        if len(fields) < 16:
            continue
        try:
            total += int(fields[8])
        except ValueError:
            continue
    return total


def _read_review_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    state: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in lines:
        if not line.startswith("- ") or ":" not in line:
            continue
        label, value = line[2:].split(":", 1)
        state[label.strip()] = value.strip()
    return state


def _write_review_state(path: Path, state: dict[str, str]) -> None:
    labels = (
        "Last meta-review",
        "Non-trivial tasks since last meta-review",
        "Circuit-breakers since last meta-review",
        "Repeated-assumption failures since last meta-review",
        "Latest meta-review score",
        "Rolling 3-review average",
        "Score status",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else ["# Workflow Review State", ""]
    seen: set[str] = set()
    output: list[str] = []
    for line in existing:
        if line.startswith("- ") and ":" in line:
            label = line[2:].split(":", 1)[0].strip()
            if label in state:
                output.append(f"- {label}: {state[label]}")
                seen.add(label)
                continue
        output.append(line)
    if not output:
        output = ["# Workflow Review State", ""]
    if output[-1] != "":
        output.append("")
    for label in labels:
        if label in state and label not in seen:
            output.append(f"- {label}: {state[label]}")
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


def _write_app_meta_review_artifact(
    repo_root: Path,
    *,
    today: str,
    before: dict[str, Any],
    score: int,
    status: str,
    score_source: str,
) -> Path:
    skill_dir = Path(repo_root) / "skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = _unique_meta_review_artifact_path(skill_dir, today)
    text = "\n".join(
        [
            "# Workflow Meta-Review",
            "",
            "## Review Window",
            "",
            f"- Completed by: ai-system app",
            f"- Review date: {today}",
            f"- Previous status: {before.get('status')} ({before.get('reason')})",
            "",
            "## Signals Reviewed",
            "",
            f"- Previous counters: tasks={before.get('tasks_since_last_review')} cbs={before.get('circuit_breakers_since_last_review')} repeats={before.get('repeated_assumption_failures')}",
            f"- Last review: {before.get('last_review')}",
            f"- State file: {before.get('state_file')}",
            "",
            "## Scorecard",
            "",
            f"- Latest meta-review score: {score}",
            f"- Score status: {status}",
            f"- Score source: {score_source}",
            "",
            "## Result",
            "",
            "- Completed a meta-review from the local ai-system app surface.",
            "- Reset non-trivial task, circuit-breaker, and repeated-assumption counters after writing this artifact.",
            "",
        ]
    )
    path.write_text(text, encoding="utf-8")
    return path


def _unique_meta_review_artifact_path(skill_dir: Path, today: str) -> Path:
    base = skill_dir / f"meta-review-{today}-ai-system-app.md"
    if not base.exists():
        return base
    index = 2
    while True:
        candidate = skill_dir / f"meta-review-{today}-ai-system-app-{index}.md"
        if not candidate.exists():
            return candidate
        index += 1


def _append_app_meta_review_log(
    path: Path,
    today: str,
    before: dict[str, Any],
    score: int,
    status: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8").rstrip() if path.exists() else "# Workflow Review Log"
    entry = "\n".join(
        [
            f"## {today} ai-system App Meta-Review",
            "",
            "- Completed a meta-review from the local ai-system app surface.",
            f"- Previous status: {before.get('status')} ({before.get('reason')})",
            f"- Previous counters: tasks={before.get('tasks_since_last_review')} cbs={before.get('circuit_breakers_since_last_review')} repeats={before.get('repeated_assumption_failures')}",
            f"- Score: {score} (status {status})",
        ]
    )
    path.write_text(f"{current}\n\n{entry}\n", encoding="utf-8")


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _score_status(score: int) -> str:
    if score >= 90:
        return "green"
    if score >= 75:
        return "yellow"
    if score >= 60:
        return "orange"
    return "red"


def _days_since_review(last_review: str, now: str | None = None) -> int | None:
    if not last_review:
        return None
    try:
        last_date = date.fromisoformat(last_review[:10])
    except ValueError:
        return None
    today = parse_time(now).astimezone().date()
    return max(0, (today - last_date).days)


def _notified_badge_ids(path: Path) -> set[str]:
    ledger = _read_notification_ledger(path)
    return {str(row.get("badge_id")) for row in ledger.get("badges", []) if isinstance(row, dict) and row.get("badge_id")}


def _representative_badge_id(path: Path) -> str:
    value = _read_notification_ledger(path).get("representative_badge_id")
    return str(value) if value else ""


def _representative_badge_ids(path: Path) -> list[str]:
    ledger = _read_notification_ledger(path)
    raw_ids = ledger.get("representative_badge_ids")
    values = raw_ids if isinstance(raw_ids, list) else [ledger.get("representative_badge_id")]
    selected: list[str] = []
    for value in values:
        badge_id = str(value or "").strip()
        if badge_id and badge_id not in selected:
            selected.append(badge_id)
        if len(selected) >= REPRESENTATIVE_BADGE_LIMIT:
            break
    return selected


def _badge_by_id(badges: list[dict[str, Any]], badge_id: str | None) -> dict[str, Any] | None:
    if not badge_id:
        return None
    for badge in badges:
        if str(badge.get("badge_id")) == str(badge_id):
            return badge
    return None


def _events_this_week(events: list[dict[str, Any]], now: str | None = None) -> list[dict[str, Any]]:
    week_start = _sunday_week_start(parse_time(now).astimezone().date())
    weekly = []
    for event in events:
        event_date = local_time(event.get("occurred_at")).date()
        if _sunday_week_start(event_date) == week_start:
            weekly.append(event)
    return weekly


def _goals_completed_this_week(goals: list[dict[str, Any]], now: str | None = None) -> list[dict[str, Any]]:
    week_start = _sunday_week_start(parse_time(now).astimezone().date())
    weekly = []
    for goal in goals:
        completed_at = goal.get("completed_at")
        if not completed_at:
            continue
        completed_date = local_time(str(completed_at)).date()
        if _sunday_week_start(completed_date) == week_start:
            weekly.append(goal)
    return weekly


def _self_adjusted_daily_target(events: list[dict[str, Any]], now: str | None = None) -> int:
    average, active_days = _recent_active_day_average(events, now, days=14)
    if active_days >= 5 and average >= 6:
        return 3
    if active_days >= 4 and average >= 2.5:
        return 2
    return 1


def _recent_active_day_average(
    events: list[dict[str, Any]], now: str | None = None, *, days: int
) -> tuple[float, int]:
    today = parse_time(now).astimezone().date()
    start_day = today - timedelta(days=max(1, int(days)))
    counts: dict[date, int] = {}
    for event in events:
        occurred_at = event.get("occurred_at")
        if not occurred_at:
            continue
        event_date = local_time(occurred_at).date()
        if start_day <= event_date < today:
            counts[event_date] = counts.get(event_date, 0) + 1
    if not counts:
        return 0.0, 0
    return sum(counts.values()) / len(counts), len(counts)


def _self_adjusted_weekly_target(events: list[dict[str, Any]], now: str | None = None) -> int:
    average, active_weeks = _recent_weekly_workday_average(events, now, days=28)
    if active_weeks and average >= 5:
        return 5
    return 4


def _recent_weekly_workday_average(
    events: list[dict[str, Any]], now: str | None = None, *, days: int
) -> tuple[float, int]:
    current_week_start = _sunday_week_start(parse_time(now).astimezone().date())
    window_start = current_week_start - timedelta(days=max(7, int(days)))
    active_workdays_by_week: dict[date, set[date]] = {}
    for event in events:
        occurred_at = event.get("occurred_at")
        if not occurred_at:
            continue
        event_date = local_time(occurred_at).date()
        if not (window_start <= event_date < current_week_start) or not _is_workday(event_date):
            continue
        week_start = _sunday_week_start(event_date)
        active_workdays_by_week.setdefault(week_start, set()).add(event_date)
    if not active_workdays_by_week:
        return 0.0, 0
    counts = [len(days_in_week) for days_in_week in active_workdays_by_week.values()]
    return sum(counts) / len(counts), len(counts)


def _events_today(events: list[dict[str, Any]], now: str | None = None) -> list[dict[str, Any]]:
    today = parse_time(now).astimezone().date()
    today_events = []
    for event in events:
        occurred_at = event.get("occurred_at")
        if not occurred_at:
            continue
        if local_time(occurred_at).date() == today:
            today_events.append(event)
    return today_events


def _goals_completed_today(goals: list[dict[str, Any]], now: str | None = None) -> list[dict[str, Any]]:
    today = parse_time(now).astimezone().date()
    completed_today = []
    for goal in goals:
        completed_at = goal.get("completed_at")
        if not completed_at:
            continue
        if local_time(str(completed_at)).date() == today:
            completed_today.append(goal)
    return completed_today


def _is_workday(day: date) -> bool:
    return day.weekday() < 5


def _active_workdays_this_week(events: list[dict[str, Any]], now: str | None = None) -> int:
    week_start = _sunday_week_start(parse_time(now).astimezone().date())
    active_workdays: set[date] = set()
    for event in events:
        occurred_at = event.get("occurred_at")
        if not occurred_at:
            continue
        event_date = local_time(occurred_at).date()
        if _sunday_week_start(event_date) == week_start and _is_workday(event_date):
            active_workdays.add(event_date)
    return len(active_workdays)


def _first_actionable_mission(missions: Any) -> dict[str, Any] | None:
    if not isinstance(missions, list):
        return None
    for mission in missions:
        if isinstance(mission, dict) and mission.get("status") in {"active", "todo"}:
            return mission
    return None


def _day_period(now: str | None = None) -> str:
    return parse_time(now).astimezone().date().isoformat()


def _week_period(now: str | None = None) -> str:
    date = parse_time(now).astimezone().date()
    return _sunday_week_start(date).isoformat()


def _notification_categories() -> tuple[str, ...]:
    return ("achievements", "missions", "workflow", "inactivity", "tips", "community", "caffeinate", "memory")


def _notification_category_enabled(settings_file: Path | None, category: str) -> bool:
    if settings_file is None:
        return True
    settings = load_settings(Path(settings_file))
    notifications = settings.get("notifications", {})
    if not isinstance(notifications, dict) or not notifications.get("enabled", True):
        return False
    categories = notifications.get("categories", {})
    return bool(categories.get(category, True)) if isinstance(categories, dict) else True


def _caffeinate_enabled(settings_file: Path | None) -> bool:
    if settings_file is None:
        return True
    settings = load_settings(Path(settings_file))
    return _setting_enabled(settings.get("caffeinate"), default=True)


def _setting_enabled(value: Any, *, default: bool) -> bool:
    if isinstance(value, dict):
        return bool(value.get("enabled", default))
    if value is None:
        return default
    return bool(value)


def _read_cached_tips(cache_file: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, dict) and isinstance(payload.get("tips"), list):
        return [tip for tip in payload["tips"] if isinstance(tip, dict) and (tip.get("tip_id") or tip.get("id"))]
    if isinstance(payload, list):
        return [tip for tip in payload if isinstance(tip, dict) and (tip.get("tip_id") or tip.get("id"))]
    return []


def _notify_once(
    notification_file: Path,
    ledger_key: str,
    key: str,
    title: str,
    message: str,
    *,
    sender: NotificationSender | None = None,
    now: str | None = None,
) -> int:
    ledger = _read_notification_ledger(notification_file)
    rows = ledger.setdefault(ledger_key, [])
    if any(isinstance(row, dict) and row.get("key") == key for row in rows):
        return 0
    sender = sender or send_macos_notification
    try:
        sender(title, message)
    except Exception:
        pass
    rows.append({"key": key, "notified_at": now})
    _write_notification_ledger(notification_file, ledger)
    return 1


def _sunday_week_start(date: Any) -> Any:
    return date - timedelta(days=(date.weekday() + 1) % 7)


def _token_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return 0


def _float_value(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    try:
        return max(0.0, float(str(value)))
    except (TypeError, ValueError):
        return 0.0


def _normalize_tip(tip: dict[str, Any]) -> dict[str, Any]:
    tip_id = tip.get("tip_id") or tip.get("id")
    if not tip_id:
        return {}
    normalized = dict(tip)
    normalized["tip_id"] = str(tip_id)
    normalized.setdefault("id", str(tip_id))
    return normalized


def _tip_is_eligible(tip: dict[str, Any]) -> bool:
    source = str(tip.get("source") or "").lower()
    if source == "community":
        return _token_int(tip.get("upvotes")) >= 10
    return True


def _merge_time_window(defaults: dict[str, Any], value: Any) -> dict[str, Any]:
    merged = dict(defaults)
    if isinstance(value, dict):
        for key in merged:
            if key in value:
                merged[key] = value[key]
    return merged


def _last_usage_time(events: list[dict[str, Any]]) -> Any | None:
    if not events:
        return None
    times = [local_time(event.get("occurred_at")) for event in events if event.get("occurred_at")]
    return max(times) if times else None


def _inside_work_hours(settings_file: Path | None, now: str | None) -> bool:
    settings = load_settings(Path(settings_file)) if settings_file else load_settings(Path("/nonexistent"))
    now_dt = parse_time(now).astimezone()
    quiet = settings.get("quiet_hours", {})
    if isinstance(quiet, dict) and quiet.get("enabled") and _time_in_window(
        now_dt,
        str(quiet.get("start") or "22:00"),
        str(quiet.get("end") or "07:00"),
    ):
        return False
    work_hours = settings.get("work_hours", {})
    if not isinstance(work_hours, dict):
        return True
    workdays = work_hours.get("workdays", [0, 1, 2, 3, 4])
    if isinstance(workdays, list) and now_dt.weekday() not in {_token_int(day) for day in workdays}:
        return False
    return _time_in_window(now_dt, str(work_hours.get("start") or "09:00"), str(work_hours.get("end") or "18:00"))


def _time_in_window(now_dt: Any, start_text: str, end_text: str) -> bool:
    try:
        start_hour, start_minute = [int(part) for part in start_text.split(":", 1)]
        end_hour, end_minute = [int(part) for part in end_text.split(":", 1)]
    except ValueError:
        return True
    current = now_dt.hour * 60 + now_dt.minute
    start = start_hour * 60 + start_minute
    end = end_hour * 60 + end_minute
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def _check(repo_root: Path, notification_file: Path) -> int:
    if not repo_root.exists():
        print(f"missing repo root: {repo_root}", file=sys.stderr)
        return 2
    notification_file.parent.mkdir(parents=True, exist_ok=True)
    events = read_events(default_events_file(repo_root))
    goals = load_goals(default_goals_file(repo_root))
    backend = preferred_gui_backend()
    gui = "available" if backend != "none" else "unavailable"
    print(f"OK events={len(events)} goals={len(goals)} gui={gui} backend={backend} notification_file={notification_file}")
    return 0


def _section(parent: Any, title: str) -> None:
    import tkinter as tk
    from tkinter import ttk

    ttk.Label(parent, text=title, style="Metric.TLabel").pack(anchor="w", pady=(14, 4))
    tk.Frame(parent, height=1, bg="#d9dfd8").pack(fill="x", pady=(0, 6))


def _row(parent: Any, title: str, detail: str) -> None:
    from tkinter import ttk

    frame = ttk.Frame(parent, padding=(0, 4))
    frame.pack(fill="x", anchor="w")
    ttk.Label(frame, text=title, style="Body.TLabel", wraplength=340).pack(anchor="w")
    if detail:
        ttk.Label(frame, text=detail, style="Muted.TLabel", wraplength=340).pack(anchor="w")


def _status_symbol(status: str) -> str:
    if status == "done":
        return "[x]"
    if status == "active":
        return "[>]"
    return "[ ]"


def _badge_symbol(status: str) -> str:
    if status == "earned":
        return "*"
    if status == "in_progress":
        return ">"
    return "-"
