from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SAFE_JSON_KEYS = {
    "summary",
    "title",
    "description",
    "desc",
    "note",
    "notes",
    "scope",
    "outcome",
    "lesson",
    "lessons",
    "evidence",
    "keywords",
    "task",
    "objective",
    "status",
}

SENSITIVE_JSON_KEYS = {
    "prompt",
    "completion",
    "content",
    "message",
    "messages",
    "input",
    "output",
    "raw",
    "body",
}

PERSONALITY_THEMES = {
    "Precision Over Speed": {
        "inference": "Prefers verified, complete work over fast-but-fragile execution.",
        "keywords": (
            "verify",
            "verification",
            "test",
            "tests",
            "compile",
            "typecheck",
            "full suite",
            "full unittest",
            "passing",
            "before claiming",
            "green",
        ),
    },
    "Intent-First Communication": {
        "inference": "Expects agents to preserve the real user intent and literal contracts.",
        "keywords": (
            "intent",
            "literal",
            "contract",
            "user said",
            "corrected",
            "preserve",
            "scope",
            "meaning",
        ),
    },
    "Blast-Radius Awareness": {
        "inference": "Treats shared systems, irreversible actions, and external blockers carefully.",
        "keywords": (
            "blocker",
            "blocked",
            "production",
            "shared",
            "permission",
            "non-mutating",
            "dry-run",
            "force",
            "backup",
            "safe",
            "reversible",
        ),
    },
    "Systems Thinking": {
        "inference": "Builds durable tools, platforms, and reusable workflows instead of one-off fixes.",
        "keywords": (
            "tool",
            "script",
            "cli",
            "installer",
            "workflow",
            "platform",
            "automation",
            "surface",
            "state contract",
        ),
    },
    "Structured Record-Keeping": {
        "inference": "Turns work into durable artifacts, logs, evidence, and reusable rules.",
        "keywords": (
            "closeout",
            "task-log",
            "review-log",
            "evidence",
            "artifact",
            "documented",
            "encoded",
            "agents.md",
            "memory",
            "lesson",
        ),
    },
}

THINKING_THEMES = {
    "Scope Before Solve": {
        "inference": "Frames the problem and rejects weak approaches before implementation.",
        "keywords": (
            "brief",
            "plan",
            "planning",
            "before implementation",
            "before edits",
            "scope",
            "inventory",
            "prd",
            "rejected",
            "approach",
        ),
    },
    "Measure To Decide": {
        "inference": "Uses measured behavior and live evidence to choose or validate the path.",
        "keywords": (
            "measure",
            "measured",
            "benchmark",
            "state json",
            "evidence",
            "latency",
            "cpu",
            "top",
            "windowserver",
            "data",
        ),
    },
    "Blast Radius Check": {
        "inference": "Evaluates safety, reversibility, and ownership before taking action.",
        "keywords": (
            "blast",
            "blocked",
            "blocker",
            "production",
            "external",
            "safe",
            "non-mutating",
            "dry-run",
            "backup",
            "reversible",
        ),
    },
    "Parallel Evaluation, Sequential Commitment": {
        "inference": "Uses parallel exploration only when streams are independent, then converges on one path.",
        "keywords": (
            "parallel",
            "independent",
            "workstream",
            "collision",
            "ownership",
            "fan out",
            "subagent",
            "converge",
        ),
    },
    "Encode And Propagate": {
        "inference": "Turns lessons into repo rules, memories, skills, docs, and checks.",
        "keywords": (
            "encoded",
            "encode",
            "lesson",
            "agents.md",
            "memory",
            "skill",
            "rule",
            "guideline",
            "documented",
            "propagate",
        ),
    },
}


@dataclass(frozen=True)
class EvidenceItem:
    theme: str
    source_path: str
    line_number: int
    text: str


@dataclass(frozen=True)
class IdentityDrafts:
    personality_markdown: str
    thinking_markdown: str
    source_files: int
    evidence_items: int
    personality_theme_count: int
    thinking_theme_count: int


def build_identity_drafts(
    sources: Iterable[Path],
    *,
    max_items_per_theme: int = 8,
    max_files: int = 400,
    generated_at: str | None = None,
) -> IdentityDrafts:
    files = list(_iter_source_files(sources, max_files=max_files))
    personality = _classify(files, PERSONALITY_THEMES, max_items_per_theme=max_items_per_theme)
    thinking = _classify(files, THINKING_THEMES, max_items_per_theme=max_items_per_theme)
    generated_at = generated_at or _utc_now()
    return IdentityDrafts(
        personality_markdown=_render_document(
            title="Peter Lyoo - Working Personality Profile Draft",
            description="Generated draft from reviewed Claude/Codex session summaries.",
            generated_at=generated_at,
            themes=PERSONALITY_THEMES,
            evidence_by_theme=personality,
        ),
        thinking_markdown=_render_document(
            title="Peter Lyoo - Thinking Path Draft",
            description="Generated draft from reviewed Claude/Codex session summaries.",
            generated_at=generated_at,
            themes=THINKING_THEMES,
            evidence_by_theme=thinking,
        ),
        source_files=len(files),
        evidence_items=sum(len(items) for items in personality.values()) + sum(len(items) for items in thinking.values()),
        personality_theme_count=sum(1 for items in personality.values() if items),
        thinking_theme_count=sum(1 for items in thinking.values() if items),
    )


def default_sources(repo_root: Path) -> list[Path]:
    repo_root = Path(repo_root)
    candidates = [
        repo_root / "skill" / "public" / "two-phase-execution" / "common" / "state" / "task-log.md",
        repo_root / "skill" / "public" / "two-phase-execution" / "common" / "state" / "review-log.md",
        repo_root / "skill" / "task-log.md",
        repo_root / "skill" / "review-log.md",
        repo_root / "docs" / "plans",
        Path.home() / ".codex" / "memories" / "rollout_summaries",
    ]
    return [path for path in candidates if path.exists()]


def write_identity_drafts(
    drafts: IdentityDrafts,
    output_dir: Path,
    *,
    force: bool = False,
) -> dict[str, str]:
    output_dir = Path(output_dir)
    targets = {
        "personality": output_dir / "personality-draft.md",
        "thinking": output_dir / "thinkingpath-draft.md",
    }
    existing = [path for path in targets.values() if path.exists()]
    if existing and not force:
        names = ", ".join(str(path) for path in existing)
        raise FileExistsError(f"refusing to overwrite existing draft file(s): {names}; use --force")

    output_dir.mkdir(parents=True, exist_ok=True)
    targets["personality"].write_text(drafts.personality_markdown, encoding="utf-8")
    targets["thinking"].write_text(drafts.thinking_markdown, encoding="utf-8")
    return {name: str(path) for name, path in targets.items()}


def identity_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Draft identity/personality docs from local Claude/Codex session summaries.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--source", type=Path, action="append", help="Summary file or directory. Repeatable.")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--max-files", type=int, default=400)
    parser.add_argument("--max-items-per-theme", type=int, default=8)
    parser.add_argument("--check", action="store_true", help="Report whether enough evidence exists without rendering full drafts.")
    parser.add_argument("--dry-run", action="store_true", help="Render drafts to stdout without writing files.")
    parser.add_argument("--write", action="store_true", help="Write reviewable drafts under identity/drafts or --output-dir.")
    parser.add_argument("--force", action="store_true", help="Allow overwriting existing generated draft files.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable status for --check or --write.")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.expanduser().resolve()
    sources = args.source or default_sources(repo_root)
    output_dir = args.output_dir or repo_root / "identity" / "drafts"
    drafts = build_identity_drafts(
        sources,
        max_items_per_theme=args.max_items_per_theme,
        max_files=args.max_files,
    )
    status = _status_payload(drafts, sources)
    if drafts.evidence_items <= 0:
        status["status"] = "empty"

    if args.check:
        _print_status(status, json_output=args.json)
        return 0 if status["status"] == "ok" else 1

    if args.write:
        try:
            written = write_identity_drafts(drafts, output_dir, force=args.force)
        except FileExistsError as exc:
            print(f"identity-draft: {exc}", file=sys.stderr)
            return 1
        status["written"] = written
        _print_status(status, json_output=args.json)
        return 0

    # Default is intentionally non-mutating, same as --dry-run.
    if args.json:
        _print_status(status, json_output=True)
    else:
        print(drafts.personality_markdown)
        print("\n---\n")
        print(drafts.thinking_markdown)
    return 0 if status["status"] == "ok" else 1


def _iter_source_files(sources: Iterable[Path], *, max_files: int) -> Iterable[Path]:
    seen: set[Path] = set()
    count = 0
    for source in sources:
        path = Path(source).expanduser()
        candidates = _walk_source(path) if path.is_dir() else [path]
        for candidate in candidates:
            candidate = candidate.expanduser()
            if candidate in seen or not candidate.is_file() or candidate.suffix.lower() not in {".md", ".txt", ".jsonl", ".json"}:
                continue
            seen.add(candidate)
            yield candidate
            count += 1
            if count >= max_files:
                return


def _walk_source(path: Path) -> list[Path]:
    return sorted(
        candidate
        for candidate in path.rglob("*")
        if candidate.is_file() and ".git" not in candidate.parts and candidate.suffix.lower() in {".md", ".txt", ".jsonl", ".json"}
    )


def _classify(
    files: list[Path],
    themes: dict[str, dict[str, Any]],
    *,
    max_items_per_theme: int,
) -> dict[str, list[EvidenceItem]]:
    evidence_by_theme = {theme: [] for theme in themes}
    seen: set[tuple[str, str]] = set()
    for path in files:
        for line_number, text in _extract_source_lines(path):
            normalized = _normalize_text(text)
            if len(normalized) < 30:
                continue
            lower = normalized.lower()
            for theme, config in themes.items():
                if len(evidence_by_theme[theme]) >= max_items_per_theme:
                    continue
                if not _matches(lower, config["keywords"]):
                    continue
                key = (theme, normalized)
                if key in seen:
                    continue
                seen.add(key)
                evidence_by_theme[theme].append(
                    EvidenceItem(
                        theme=theme,
                        source_path=str(path),
                        line_number=line_number,
                        text=_truncate(normalized),
                    )
                )
    return evidence_by_theme


def _extract_source_lines(path: Path) -> Iterable[tuple[int, str]]:
    suffix = path.suffix.lower()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return []
    if suffix == ".jsonl":
        return _extract_jsonl_lines(lines)
    if suffix == ".json":
        try:
            data = json.loads("\n".join(lines))
        except json.JSONDecodeError:
            return []
        return [(1, text) for text in _safe_json_strings(data)]
    return [
        (line_number, line)
        for line_number, line in enumerate(lines, start=1)
        if _markdown_line_is_evidence(line)
    ]


def _extract_jsonl_lines(lines: list[str]) -> list[tuple[int, str]]:
    extracted: list[tuple[int, str]] = []
    for line_number, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for text in _safe_json_strings(data):
            extracted.append((line_number, text))
    return extracted


def _safe_json_strings(value: Any, *, key: str = "") -> list[str]:
    if key.lower() in SENSITIVE_JSON_KEYS:
        return []
    if isinstance(value, dict):
        found: list[str] = []
        for child_key, child in value.items():
            lower_key = str(child_key).lower()
            if lower_key in SENSITIVE_JSON_KEYS:
                continue
            if lower_key in SAFE_JSON_KEYS:
                found.extend(_string_values(child))
            else:
                found.extend(_safe_json_strings(child, key=lower_key))
        return found
    if isinstance(value, list):
        found: list[str] = []
        for item in value:
            found.extend(_safe_json_strings(item, key=key))
        return found
    return []


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        found: list[str] = []
        for item in value:
            found.extend(_string_values(item))
        return found
    if isinstance(value, dict):
        found: list[str] = []
        for key, child in value.items():
            if str(key).lower() in SENSITIVE_JSON_KEYS:
                continue
            found.extend(_string_values(child))
        return found
    return []


def _markdown_line_is_evidence(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped in {"---", "```"}:
        return False
    if stripped.startswith("#"):
        return False
    return bool(re.search(r"[A-Za-z]", stripped))


def _normalize_text(text: str) -> str:
    text = re.sub(r"^\s*[-*]\s+", "", text.strip())
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _matches(lower_text: str, keywords: Iterable[str]) -> bool:
    return any(keyword.lower() in lower_text for keyword in keywords)


def _truncate(text: str, limit: int = 240) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _render_document(
    *,
    title: str,
    description: str,
    generated_at: str,
    themes: dict[str, dict[str, Any]],
    evidence_by_theme: dict[str, list[EvidenceItem]],
) -> str:
    lines = [
        "---",
        f"name: {title}",
        f"description: {description}",
        "type: user",
        "generated_by: identity-draft",
        f"generated_at: {generated_at}",
        "---",
        "",
        f"# {title}",
        "",
        "Generated draft from reviewed Claude/Codex session summaries. Review and edit before copying into canonical identity files.",
        "",
    ]
    for theme, config in themes.items():
        lines.extend(
            [
                f"## {theme}",
                "",
                f"Inference: {config['inference']}",
                "",
            ]
        )
        items = evidence_by_theme.get(theme, [])
        if not items:
            lines.extend(["Evidence: none found in the selected source window.", ""])
            continue
        lines.append("Evidence:")
        for item in items:
            lines.append(f"- `{item.source_path}:{item.line_number}` - {item.text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _status_payload(drafts: IdentityDrafts, sources: Iterable[Path]) -> dict[str, Any]:
    return {
        "status": "ok",
        "sources": [str(Path(source).expanduser()) for source in sources],
        "source_files": drafts.source_files,
        "evidence_items": drafts.evidence_items,
        "personality_theme_count": drafts.personality_theme_count,
        "thinking_theme_count": drafts.thinking_theme_count,
    }


def _print_status(payload: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(f"identity-draft: {payload['status']}")
    print(f"source_files={payload['source_files']} evidence_items={payload['evidence_items']}")
    if "written" in payload:
        for name, path in payload["written"].items():
            print(f"{name}: {path}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(identity_main())
