"""First-class run model for ai-system.

A "run" is a coherent unit of work that spans multiple turns and may produce
multiple artifacts. It starts when a two-phase brief is written and ends
when the closeout is appended (or the user calls `ai-run end <id>`).

Storage:
- `data/runs/index.jsonl` — one line per run, append-only event stream
  (`start`, `update`, `end`). Latest line wins.
- `data/runs/<run_id>/` — per-run scratch directory: brief.md, closeout.md,
  events.jsonl (turn events from the Stop hook), artifacts/ symlinks.

The model is intentionally local-first and grep-friendly. SQLite is overkill
for a single-user volume.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_DEFAULT = Path(__file__).resolve().parents[1]
RUNS_SUBDIR = "data/runs"
INDEX_FILE = "index.jsonl"
VALID_STATUSES = ("planning", "in_progress", "blocked", "done", "abandoned")


@dataclass
class Run:
    run_id: str
    title: str
    started_at: str
    ended_at: str = ""
    status: str = "planning"
    brief_path: str = ""
    closeout_path: str = ""
    artifact_paths: list[str] = field(default_factory=list)
    agents_used: list[str] = field(default_factory=list)
    skills_fired: list[str] = field(default_factory=list)
    tokens_used: int = 0


def _utc_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_run_id(title: str, now: datetime | None = None) -> str:
    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:24] or "run"
    return f"{timestamp}-{slug}"


def _index_path(repo_root: Path) -> Path:
    return Path(repo_root) / RUNS_SUBDIR / INDEX_FILE


def _run_dir(repo_root: Path, run_id: str) -> Path:
    return Path(repo_root) / RUNS_SUBDIR / run_id


def _append_event(repo_root: Path, event: dict[str, Any]) -> None:
    path = _index_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")


def _read_events(repo_root: Path) -> list[dict[str, Any]]:
    path = _index_path(repo_root)
    if not path.is_file():
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


def _coalesce(events: list[dict[str, Any]]) -> dict[str, Run]:
    runs: dict[str, Run] = {}
    for event in events:
        run_id = event.get("run_id")
        if not isinstance(run_id, str):
            continue
        action = event.get("action") or ""
        if action == "start":
            runs[run_id] = Run(
                run_id=run_id,
                title=str(event.get("title") or ""),
                started_at=str(event.get("at") or ""),
                status=str(event.get("status") or "planning"),
                brief_path=str(event.get("brief_path") or ""),
            )
        elif action == "update":
            current = runs.get(run_id)
            if current is None:
                continue
            for key in ("title", "status", "brief_path", "closeout_path"):
                value = event.get(key)
                if isinstance(value, str) and value:
                    setattr(current, key, value)
            for key in ("artifact_paths", "agents_used", "skills_fired"):
                value = event.get(key)
                if isinstance(value, list):
                    merged = list(dict.fromkeys(getattr(current, key) + [str(v) for v in value]))
                    setattr(current, key, merged)
            tokens = event.get("tokens_used")
            if isinstance(tokens, (int, float)) and not isinstance(tokens, bool):
                current.tokens_used = max(current.tokens_used, int(tokens))
        elif action == "end":
            current = runs.get(run_id)
            if current is None:
                continue
            current.ended_at = str(event.get("at") or "")
            status = event.get("status")
            if isinstance(status, str) and status in VALID_STATUSES:
                current.status = status
            closeout = event.get("closeout_path")
            if isinstance(closeout, str) and closeout:
                current.closeout_path = closeout
    return runs


def list_runs(repo_root: Path) -> list[Run]:
    runs = _coalesce(_read_events(repo_root))
    return sorted(runs.values(), key=lambda r: r.started_at, reverse=True)


def get_run(repo_root: Path, run_id: str) -> Run | None:
    return _coalesce(_read_events(repo_root)).get(run_id)


def start_run(
    repo_root: Path,
    title: str,
    *,
    brief_path: str = "",
    now: datetime | None = None,
) -> Run:
    title = title.strip() or "untitled-run"
    timestamp = now or datetime.now(timezone.utc)
    run_id = _new_run_id(title, timestamp)
    run_dir = _run_dir(repo_root, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "artifacts").mkdir(exist_ok=True)
    event = {
        "action": "start",
        "run_id": run_id,
        "at": _utc_iso(timestamp),
        "title": title,
        "status": "planning",
        "brief_path": brief_path,
    }
    _append_event(repo_root, event)
    return Run(run_id=run_id, title=title, started_at=event["at"], brief_path=brief_path)


def update_run(
    repo_root: Path,
    run_id: str,
    *,
    status: str | None = None,
    title: str | None = None,
    brief_path: str | None = None,
    closeout_path: str | None = None,
    add_artifact: str | None = None,
    add_agent: str | None = None,
    add_skill: str | None = None,
    tokens_used: int | None = None,
    now: datetime | None = None,
) -> Run:
    existing = get_run(repo_root, run_id)
    if existing is None:
        raise ValueError(f"unknown run_id: {run_id}")
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}; expected one of {VALID_STATUSES}")
    event: dict[str, Any] = {
        "action": "update",
        "run_id": run_id,
        "at": _utc_iso(now),
    }
    if status:
        event["status"] = status
    if title:
        event["title"] = title
    if brief_path:
        event["brief_path"] = brief_path
    if closeout_path:
        event["closeout_path"] = closeout_path
    if add_artifact:
        event["artifact_paths"] = [add_artifact]
    if add_agent:
        event["agents_used"] = [add_agent]
    if add_skill:
        event["skills_fired"] = [add_skill]
    if tokens_used is not None:
        event["tokens_used"] = int(tokens_used)
    _append_event(repo_root, event)
    return get_run(repo_root, run_id) or existing


def end_run(
    repo_root: Path,
    run_id: str,
    *,
    status: str = "done",
    closeout_path: str = "",
    now: datetime | None = None,
) -> Run:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}; expected one of {VALID_STATUSES}")
    existing = get_run(repo_root, run_id)
    if existing is None:
        raise ValueError(f"unknown run_id: {run_id}")
    event: dict[str, Any] = {
        "action": "end",
        "run_id": run_id,
        "at": _utc_iso(now),
        "status": status,
    }
    if closeout_path:
        event["closeout_path"] = closeout_path
    _append_event(repo_root, event)
    return get_run(repo_root, run_id) or existing


def active_run(repo_root: Path) -> Run | None:
    """Most recently started run that hasn't ended."""
    for run in list_runs(repo_root):
        if not run.ended_at:
            return run
    return None


def run_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage ai-system runs.")
    parser.add_argument("--repo-root", default=str(REPO_DEFAULT), type=Path)
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Start a new run.")
    start.add_argument("--title", required=True)
    start.add_argument("--brief", default="", help="Path to the execution brief (optional).")

    end = sub.add_parser("end", help="End an existing run.")
    end.add_argument("run_id")
    end.add_argument("--status", default="done", choices=VALID_STATUSES)
    end.add_argument("--closeout", default="")

    update = sub.add_parser("update", help="Update run fields.")
    update.add_argument("run_id")
    update.add_argument("--status", choices=VALID_STATUSES)
    update.add_argument("--title")
    update.add_argument("--brief")
    update.add_argument("--closeout")
    update.add_argument("--add-artifact")
    update.add_argument("--add-agent")
    update.add_argument("--add-skill")
    update.add_argument("--tokens-used", type=int)

    sub.add_parser("list", help="List all runs (most recent first).")

    show = sub.add_parser("show", help="Show one run by id.")
    show.add_argument("run_id")

    sub.add_parser("active", help="Show the currently active run, if any.")

    args = parser.parse_args(argv)
    repo = Path(args.repo_root).expanduser().resolve()

    try:
        if args.command == "start":
            run = start_run(repo, args.title, brief_path=args.brief)
            print(f"started {run.run_id}")
            return 0
        if args.command == "end":
            run = end_run(repo, args.run_id, status=args.status, closeout_path=args.closeout)
            print(f"ended {run.run_id} ({run.status})")
            return 0
        if args.command == "update":
            run = update_run(
                repo,
                args.run_id,
                status=args.status,
                title=args.title,
                brief_path=args.brief,
                closeout_path=args.closeout,
                add_artifact=args.add_artifact,
                add_agent=args.add_agent,
                add_skill=args.add_skill,
                tokens_used=args.tokens_used,
            )
            print(f"updated {run.run_id} status={run.status}")
            return 0
        if args.command == "list":
            for run in list_runs(repo):
                ended = run.ended_at or "—"
                print(f"{run.run_id}  {run.status:12}  {run.started_at} → {ended}  {run.title}")
            return 0
        if args.command == "show":
            run = get_run(repo, args.run_id)
            if run is None:
                print(f"unknown run_id: {args.run_id}", file=sys.stderr)
                return 2
            print(json.dumps(asdict(run), indent=2, sort_keys=True))
            return 0
        if args.command == "active":
            run = active_run(repo)
            if run is None:
                print("no active run")
                return 0
            print(f"{run.run_id}  {run.status:12}  started {run.started_at}  {run.title}")
            return 0
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(run_main())
