import fcntl
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agentboost.core import (
    add_goal,
    badge_statuses,
    collect_usage,
    complete_goal,
    format_tokens,
    generate_markdown_report,
    generate_terminal_report,
    load_goals,
    read_events,
    update_goal,
)


class AiUsageSystemTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.claude = self.root / "claude"
        self.codex = self.root / "codex"
        self.repo = self.root / "repo"
        self.events_file = self.repo / "data" / "ai-usage" / "events.jsonl"
        self.goals_file = self.repo / "data" / "ai-usage" / "goals.json"
        self.monthly_file = self.repo / "docs" / "ai-usage" / "monthly" / "2026-05.md"
        (self.claude / "usage-data" / "session-meta").mkdir(parents=True)
        (self.claude / "usage-data" / "facets").mkdir(parents=True)
        (self.codex / "sessions" / "2026" / "05" / "06").mkdir(parents=True)
        self.repo.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write_json(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    def test_format_tokens_uses_compact_human_readable_units(self):
        self.assertEqual(format_tokens(999), "999")
        self.assertEqual(format_tokens(1_000), "1K")
        self.assertEqual(format_tokens(1_500), "1.5K")
        self.assertEqual(format_tokens(1_250_000), "1.3M")
        self.assertEqual(format_tokens(1_500_000_000), "1.5B")

    def test_collect_normalizes_claude_and_codex_without_prompt_content_or_double_counting(self):
        self.write_json(
            self.claude / "usage-data" / "session-meta" / "claude-session.json",
            {
                "session_id": "claude-session",
                "project_path": "/work/project",
                "start_time": "2026-05-06T01:00:00Z",
                "input_tokens": 10,
                "output_tokens": 20,
                "first_prompt": "secret prompt text must not be copied",
                "uses_task_agent": True,
                "uses_mcp": False,
                "files_modified": 2,
                "git_commits": 1,
                "git_pushes": 0,
            },
        )
        self.write_json(
            self.claude / "usage-data" / "facets" / "claude-session.json",
            {
                "session_id": "claude-session",
                "outcome": "fully_achieved",
                "primary_success": "correct_code_edits",
                "goal_categories": {"implementation": 1},
            },
        )
        codex_log = self.codex / "sessions" / "2026" / "05" / "06" / "rollout.jsonl"
        codex_log.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "timestamp": "2026-05-06T01:05:00Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "token_count",
                                "info": {
                                    "total_token_usage": {
                                        "input_tokens": 100,
                                        "cached_input_tokens": 40,
                                        "output_tokens": 30,
                                        "reasoning_output_tokens": 10,
                                        "total_tokens": 130,
                                    }
                                },
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "timestamp": "2026-05-06T01:06:00Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "token_count",
                                "info": {
                                    "total_token_usage": {
                                        "input_tokens": 150,
                                        "cached_input_tokens": 60,
                                        "output_tokens": 50,
                                        "reasoning_output_tokens": 12,
                                        "total_tokens": 200,
                                    }
                                },
                            },
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        summary = collect_usage(
            repo_root=self.repo,
            claude_dir=self.claude,
            codex_dir=self.codex,
            imported_at="2026-05-06T02:00:00Z",
        )

        events = read_events(self.events_file)
        self.assertEqual(summary["imported"], 3)
        self.assertEqual(len(events), 3)
        self.assertEqual({event["source_agent"] for event in events}, {"claude", "codex"})
        self.assertNotIn("secret prompt text", json.dumps(events))
        codex_totals = [event["total_tokens"] for event in events if event["source_agent"] == "codex"]
        self.assertEqual(codex_totals, [130, 70])

        second_summary = collect_usage(
            repo_root=self.repo,
            claude_dir=self.claude,
            codex_dir=self.codex,
            imported_at="2026-05-06T02:05:00Z",
        )
        self.assertEqual(second_summary["imported"], 0)
        self.assertEqual(len(read_events(self.events_file)), 3)

    def test_collect_updates_late_changed_claude_session_totals_without_duplicates(self):
        meta = self.claude / "usage-data" / "session-meta" / "claude-session.json"
        self.write_json(
            meta,
            {
                "session_id": "claude-session",
                "project_path": "/work/project",
                "start_time": "2026-05-06T01:00:00Z",
                "input_tokens": 10,
                "output_tokens": 20,
            },
        )
        first = collect_usage(
            repo_root=self.repo,
            claude_dir=self.claude,
            codex_dir=self.codex,
            imported_at="2026-05-06T02:00:00Z",
        )
        self.write_json(
            meta,
            {
                "session_id": "claude-session",
                "project_path": "/work/project",
                "start_time": "2026-05-06T01:00:00Z",
                "input_tokens": 15,
                "output_tokens": 25,
            },
        )
        second = collect_usage(
            repo_root=self.repo,
            claude_dir=self.claude,
            codex_dir=self.codex,
            imported_at="2026-05-06T02:05:00Z",
        )

        events = read_events(self.events_file)
        self.assertEqual(first["imported"], 1)
        self.assertEqual(second["imported"], 0)
        self.assertEqual(second["updated"], 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["total_tokens"], 40)
        self.assertEqual(events[0]["updated_at"], "2026-05-06T02:05:00Z")

    def test_collect_imports_claude_code_project_jsonl_like_ccusage_without_content(self):
        transcript = self.claude / "projects" / "-work-project" / "session-a" / "conversation.jsonl"
        transcript.parent.mkdir(parents=True)
        usage_row = {
            "type": "assistant",
            "sessionId": "claude-jsonl-session",
            "timestamp": "2026-05-06T03:00:00Z",
            "cwd": "/work/project",
            "requestId": "req-1",
            "message": {
                "id": "msg-1",
                "model": "claude-sonnet-4-20250514",
                "content": [{"text": "secret Claude prompt or answer must not be stored"}],
                "usage": {
                    "input_tokens": 100,
                    "cache_creation_input_tokens": 25,
                    "cache_read_input_tokens": 10,
                    "output_tokens": 35,
                },
            },
        }
        transcript.write_text(
            "\n".join(
                [
                    json.dumps({"type": "user", "message": {"content": "do not copy me"}}),
                    json.dumps(usage_row),
                    json.dumps(usage_row),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        summary = collect_usage(
            repo_root=self.repo,
            claude_dir=self.claude,
            codex_dir=self.codex,
            imported_at="2026-05-06T03:05:00Z",
        )

        events = read_events(self.events_file)
        self.assertEqual(summary["imported"], 1)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["source_agent"], "claude")
        self.assertEqual(event["source_session_id"], "claude-jsonl-session")
        self.assertEqual(event["occurred_at"], "2026-05-06T03:00:00Z")
        self.assertEqual(event["project_path"], "/work/project")
        self.assertEqual(event["input_tokens"], 100)
        self.assertEqual(event["cached_input_tokens"], 35)
        self.assertEqual(event["output_tokens"], 35)
        self.assertEqual(event["total_tokens"], 170)
        self.assertEqual(event["record_type"], "turn")
        self.assertNotIn("secret Claude prompt", json.dumps(events))
        self.assertNotIn("do not copy me", json.dumps(events))

    def test_collect_replaces_legacy_claude_session_meta_when_project_jsonl_exists(self):
        self.events_file.parent.mkdir(parents=True)
        self.events_file.write_text(
            json.dumps(
                {
                    "event_id": "claude:legacy-session",
                    "source_agent": "claude",
                    "source_path": str(self.claude / "usage-data" / "session-meta" / "legacy-session.json"),
                    "source_session_id": "legacy-session",
                    "occurred_at": "2026-05-06T02:00:00Z",
                    "project_path": "/work/project",
                    "input_tokens": 1000,
                    "cached_input_tokens": 0,
                    "output_tokens": 100,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 1100,
                    "record_type": "session",
                    "imported_at": "2026-05-06T02:10:00Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        transcript = self.claude / "projects" / "-work-project" / "legacy-session.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": "legacy-session",
                    "timestamp": "2026-05-06T02:00:00Z",
                    "cwd": "/work/project",
                    "requestId": "req-legacy",
                    "message": {
                        "id": "msg-legacy",
                        "model": "claude-sonnet-4-20250514",
                        "usage": {
                            "input_tokens": 100,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 0,
                            "output_tokens": 20,
                        },
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        summary = collect_usage(
            repo_root=self.repo,
            claude_dir=self.claude,
            codex_dir=self.codex,
            imported_at="2026-05-06T02:15:00Z",
        )

        events = read_events(self.events_file)
        self.assertEqual(summary.get("removed_legacy"), 1)
        self.assertEqual(summary["imported"], 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["record_type"], "turn")
        self.assertEqual(events[0]["total_tokens"], 120)

    def test_codex_events_follow_ccusage_delta_aliases_and_skip_zero_usage(self):
        codex_log = self.codex / "sessions" / "2026" / "05" / "06" / "codex-session.jsonl"
        codex_log.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "timestamp": "2026-05-06T04:00:00Z",
                            "type": "turn_context",
                            "payload": {"model": "gpt-5"},
                        }
                    ),
                    json.dumps(
                        {
                            "timestamp": "2026-05-06T04:01:00Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "token_count",
                                "info": {
                                    "total_token_usage": {
                                        "input_tokens": 1200,
                                        "cache_read_input_tokens": 200,
                                        "output_tokens": 500,
                                        "reasoning_output_tokens": 30,
                                    }
                                },
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "timestamp": "2026-05-06T04:02:00Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "token_count",
                                "info": {
                                    "total_token_usage": {
                                        "input_tokens": 2000,
                                        "cache_read_input_tokens": 300,
                                        "output_tokens": 800,
                                        "reasoning_output_tokens": 40,
                                    }
                                },
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "timestamp": "2026-05-06T04:03:00Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "token_count",
                                "info": {
                                    "total_token_usage": {
                                        "input_tokens": 2000,
                                        "cache_read_input_tokens": 300,
                                        "output_tokens": 800,
                                        "reasoning_output_tokens": 40,
                                    }
                                },
                            },
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        summary = collect_usage(
            repo_root=self.repo,
            claude_dir=self.claude,
            codex_dir=self.codex,
            imported_at="2026-05-06T04:05:00Z",
        )

        events = read_events(self.events_file)
        self.assertEqual(summary["imported"], 2)
        self.assertEqual([event["input_tokens"] for event in events], [1200, 800])
        self.assertEqual([event["cached_input_tokens"] for event in events], [200, 100])
        self.assertEqual([event["output_tokens"] for event in events], [500, 300])
        self.assertEqual([event["reasoning_output_tokens"] for event in events], [30, 10])
        self.assertEqual([event["total_tokens"] for event in events], [1700, 1100])

    def test_goal_progress_taxonomy_evidence_quality_and_xp(self):
        with self.assertRaises(ValueError):
            add_goal(
                goals_file=self.goals_file,
                title="Unknown goal",
                goal_type="misc",
                period="2026-05",
            )

        goal = add_goal(
            goals_file=self.goals_file,
            title="Adopt AI on one workflow",
            goal_type="adoption",
            period="2026-05",
            target=3,
            human_skill="delegation",
        )
        updated = update_goal(self.goals_file, goal["goal_id"], status="in_progress", progress=1)
        self.assertEqual(updated["status"], "in_progress")
        self.assertEqual(updated["progress"], 1)

        with self.assertRaises(ValueError):
            complete_goal(self.goals_file, goal["goal_id"], evidence=["not a link"], reflection="")

        completed = complete_goal(
            self.goals_file,
            goal["goal_id"],
            evidence=["task-log:adopted", "command:python3 -m unittest", "circuit-breaker:replanned"],
            reflection="I changed how I delegate small implementation tasks.",
        )
        self.assertEqual(completed["status"], "completed")
        self.assertGreaterEqual(completed["xp_awarded"], 225)
        self.assertIn("command", completed["evidence_types"])

    def test_level_progress_uses_level_1_to_50_xp_table(self):
        from agentboost import core as core_module

        table = getattr(core_module, "LEVEL_XP_REQUIREMENTS", ())
        level_progress_for_xp = getattr(core_module, "level_progress_for_xp", lambda _xp: {})

        self.assertEqual(table[0], (1, 15))
        self.assertEqual(table[34], (35, 174_216))
        self.assertEqual(table[-1], (50, 709_716))

        level_one = level_progress_for_xp(14)
        self.assertEqual(level_one.get("current_level"), 1)
        self.assertEqual(level_one.get("current_level_required_xp"), 15)
        self.assertEqual(level_one.get("xp_to_next_level"), 1)

        level_two = level_progress_for_xp(15)
        self.assertEqual(level_two.get("current_level"), 2)
        self.assertEqual(level_two.get("current_level_xp"), 0)
        self.assertEqual(level_two.get("current_level_required_xp"), 34)

    def test_goals_require_evidence_and_reports_include_gamification_and_human_nurture(self):
        self.events_file.parent.mkdir(parents=True)
        self.events_file.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "event_id": "claude-big",
                            "source_agent": "claude",
                            "source_path": "fixture",
                            "source_session_id": "claude-big",
                            "occurred_at": "2026-05-06T01:00:00Z",
                            "project_path": "/work/project",
                            "input_tokens": 600_000_000,
                            "cached_input_tokens": 0,
                            "output_tokens": 0,
                            "reasoning_output_tokens": 0,
                            "total_tokens": 600_000_000,
                            "record_type": "session",
                            "imported_at": "2026-05-06T02:00:00Z",
                        }
                    ),
                    json.dumps(
                        {
                            "event_id": "codex-big",
                            "source_agent": "codex",
                            "source_path": "fixture",
                            "source_session_id": "codex-big",
                            "occurred_at": "2026-05-06T02:00:00Z",
                            "project_path": "/work/project",
                            "input_tokens": 400_000_000,
                            "cached_input_tokens": 0,
                            "output_tokens": 0,
                            "reasoning_output_tokens": 0,
                            "total_tokens": 400_000_000,
                            "record_type": "turn",
                            "imported_at": "2026-05-06T02:00:00Z",
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        for index in range(10):
            goal = add_goal(
                goals_file=self.goals_file,
                title=f"Verified AI workflow {index}",
                goal_type="verification",
                period="2026-05",
                target=1,
                human_skill="verification",
            )
            if index == 0:
                with self.assertRaises(ValueError):
                    complete_goal(self.goals_file, goal["goal_id"], evidence=[], reflection="")
            complete_goal(
                self.goals_file,
                goal["goal_id"],
                evidence=[f"task-log:{index}"],
                reflection="I verified the agent output before relying on it.",
            )

        goals = load_goals(self.goals_file)
        badges = badge_statuses(read_events(self.events_file), goals)
        self.assertEqual(badges["Billion Club"]["status"], "earned")
        self.assertEqual(badges["Billion Club"]["badge_id"], "a758dd50b1415e27")
        self.assertGreater(badges["Better Question"]["progress"], 0)

        report = generate_terminal_report(
            events=read_events(self.events_file),
            goals=goals,
            now="2026-05-06T03:00:00Z",
        )
        self.assertIn("Today", report)
        self.assertIn("This Week", report)
        self.assertIn("This Month", report)
        self.assertIn("Lifetime", report)
        self.assertIn("Claude", report)
        self.assertIn("Codex", report)
        self.assertIn("Billion Club: earned", report)
        self.assertIn("XP", report)
        self.assertIn("Human Skill Practiced", report)
        self.assertIn("Next Best Challenge", report)
        self.assertIn("Keep:", report)
        self.assertIn("Improve:", report)
        self.assertIn("Stop:", report)
        self.assertIn("without shame", report)
        self.assertIn("Input tokens", report)
        self.assertIn("Output tokens", report)
        self.assertIn("Reasoning output", report)
        self.assertIn("Agent-assisted tasks", report)
        self.assertIn("Quality per billion", report)

        markdown = generate_markdown_report(
            events=read_events(self.events_file),
            goals=goals,
            now="2026-05-06T03:00:00Z",
        )
        self.assertIn("## Monthly Council", markdown)
        self.assertIn("Skill Gained", markdown)
        self.assertIn("Next Challenge", markdown)

        earned = badges["Billion Club"]
        self.assertIn("earned_at", earned)
        self.assertIn("evidence", earned)
        self.assertEqual(len(earned["evidence"]), 10)

    def test_cli_collect_goal_report_and_markdown_recap(self):
        self.write_json(
            self.claude / "usage-data" / "session-meta" / "s1.json",
            {
                "session_id": "s1",
                "project_path": "/work/project",
                "start_time": "2026-05-06T01:00:00Z",
                "input_tokens": 100,
                "output_tokens": 50,
            },
        )
        env = {**os.environ, "PYTHONPATH": str(Path.cwd())}

        collect = subprocess.run(
            [
                sys.executable,
                "bin/agentboost-usage-collect",
                "--repo-root",
                str(self.repo),
                "--claude-dir",
                str(self.claude),
                "--codex-dir",
                str(self.codex),
                "--now",
                "2026-05-06T02:00:00Z",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(collect.returncode, 0, collect.stderr)
        self.assertIn("imported=1", collect.stdout)

        add = subprocess.run(
            [
                sys.executable,
                "bin/agentboost-usage-goal",
                "--repo-root",
                str(self.repo),
                "add",
                "--title",
                "Use AI with verification",
                "--type",
                "verification",
                "--period",
                "2026-05",
                "--target",
                "1",
                "--human-skill",
                "verification",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(add.returncode, 0, add.stderr)
        goal_id = add.stdout.strip().split()[-1]

        rejected = subprocess.run(
            [
                sys.executable,
                "bin/agentboost-usage-goal",
                "--repo-root",
                str(self.repo),
                "complete",
                goal_id,
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("evidence", rejected.stderr)

        completed = subprocess.run(
            [
                sys.executable,
                "bin/agentboost-usage-goal",
                "--repo-root",
                str(self.repo),
                "complete",
                goal_id,
                "--evidence",
                "task-log:verified",
                "--reflection",
                "I checked the evidence before closing the task.",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        report = subprocess.run(
            [
                sys.executable,
                "bin/agentboost-usage-report",
                "--repo-root",
                str(self.repo),
                "--now",
                "2026-05-06T03:00:00Z",
                "--markdown",
                str(self.monthly_file),
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn("Today", report.stdout)
        self.assertIn("Badge Progress", report.stdout)
        self.assertIn("Source Counts", report.stdout)
        self.assertIn("Next Best Challenge", report.stdout)
        self.assertIn("Keep:", self.monthly_file.read_text(encoding="utf-8"))

    def test_cli_collect_skips_when_recent_background_collect_is_fresh(self):
        refresh_file = self.repo / "data" / "ai-usage" / "sidebar-usage-refresh.json"
        self.write_json(
            refresh_file,
            {
                "last_refreshed_at": "2026-05-06T02:00:00Z",
                "min_interval_seconds": 300,
                "summary": {},
            },
        )
        env = {**os.environ, "PYTHONPATH": str(Path.cwd())}

        collect = subprocess.run(
            [
                sys.executable,
                "bin/agentboost-usage-collect",
                "--repo-root",
                str(self.repo),
                "--claude-dir",
                str(self.claude),
                "--codex-dir",
                str(self.codex),
                "--now",
                "2026-05-06T02:01:00Z",
                "--min-interval-seconds",
                "300",
                "--skip-if-running",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(collect.returncode, 0, collect.stderr)
        self.assertIn("skipped=fresh", collect.stdout)
        self.assertFalse(self.events_file.exists())

    def test_cli_collect_skips_when_background_collect_is_already_running(self):
        lock_file = self.repo / "data" / "ai-usage" / "ai-usage-collect.lock"
        lock_file.parent.mkdir(parents=True)
        env = {**os.environ, "PYTHONPATH": str(Path.cwd())}

        with lock_file.open("w", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            collect = subprocess.run(
                [
                    sys.executable,
                    "bin/agentboost-usage-collect",
                    "--repo-root",
                    str(self.repo),
                    "--claude-dir",
                    str(self.claude),
                    "--codex-dir",
                    str(self.codex),
                    "--skip-if-running",
                ],
                cwd=Path.cwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(collect.returncode, 0, collect.stderr)
        self.assertIn("skipped=running", collect.stdout)
        self.assertFalse(self.events_file.exists())

    def test_cli_wrappers_work_without_pythonpath(self):
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)

        report = subprocess.run(
            [
                sys.executable,
                "bin/agentboost-usage-report",
                "--repo-root",
                str(self.repo),
                "--now",
                "2026-05-06T03:00:00Z",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn("AI Usage Report", report.stdout)

    def test_period_specific_report_flags_select_requested_period(self):
        self.events_file.parent.mkdir(parents=True)
        self.events_file.write_text(
            json.dumps(
                {
                    "event_id": "older",
                    "source_agent": "claude",
                    "source_path": "fixture",
                    "source_session_id": "older",
                    "occurred_at": "2026-04-01T01:00:00Z",
                    "project_path": "/work/project",
                    "input_tokens": 100,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 100,
                    "record_type": "session",
                    "imported_at": "2026-05-06T02:00:00Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        report = subprocess.run(
            [
                sys.executable,
                "bin/agentboost-usage-report",
                "--repo-root",
                str(self.repo),
                "--lifetime",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn("Lifetime", report.stdout)
        self.assertNotIn("Today\n", report.stdout)
        self.assertNotIn("This Week\n", report.stdout)
        self.assertNotIn("This Month\n", report.stdout)

    def test_markdown_recap_includes_workforce_quality_and_nurture_sections(self):
        self.events_file.parent.mkdir(parents=True)
        self.events_file.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "event_id": "claude-high",
                            "source_agent": "claude",
                            "source_path": "claude-meta",
                            "source_session_id": "claude-high",
                            "occurred_at": "2026-05-06T01:00:00Z",
                            "project_path": "/work/project",
                            "input_tokens": 5_000_000,
                            "cached_input_tokens": 0,
                            "output_tokens": 2_000_000,
                            "reasoning_output_tokens": 0,
                            "total_tokens": 7_000_000,
                            "record_type": "session",
                            "imported_at": "2026-05-06T02:00:00Z",
                        }
                    ),
                    json.dumps(
                        {
                            "event_id": "codex-helped",
                            "source_agent": "codex",
                            "source_path": "codex-log",
                            "source_session_id": "codex-helped",
                            "occurred_at": "2026-05-06T02:00:00Z",
                            "project_path": "/work/project",
                            "input_tokens": 100,
                            "cached_input_tokens": 10,
                            "output_tokens": 50,
                            "reasoning_output_tokens": 5,
                            "total_tokens": 150,
                            "record_type": "turn",
                            "imported_at": "2026-05-06T02:05:00Z",
                            "outcome": "fully_achieved",
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        goal = add_goal(
            goals_file=self.goals_file,
            title="Review an AI-assisted implementation",
            goal_type="review",
            period="2026-05",
            target=1,
            human_skill="review",
        )
        complete_goal(
            self.goals_file,
            goal["goal_id"],
            evidence=["task-log:reviewed"],
            reflection="I used review evidence to improve my judgment.",
        )

        report = generate_terminal_report(read_events(self.events_file), load_goals(self.goals_file), "2026-05-06T03:00:00Z")
        self.assertIn("Source Counts", report)
        self.assertIn("Import Window", report)
        self.assertIn("Quality Overlays", report)
        self.assertIn("high-token usage without linked outcome", report)
        self.assertIn("Streaks", report)
        self.assertIn("recoverable", report)

        markdown = generate_markdown_report(read_events(self.events_file), load_goals(self.goals_file), "2026-05-06T03:00:00Z")
        self.assertIn("## Agent Roster", markdown)
        self.assertIn("## Workforce Fitness", markdown)
        self.assertIn("## Guild Paths", markdown)
        self.assertIn("## Quality Overlays", markdown)
        self.assertIn("## Streaks", markdown)
        self.assertIn("## Source Counts", markdown)
        self.assertIn("what the AI workforce helped with", markdown)
        self.assertIn("where agents wasted time", markdown)
        self.assertIn("which collaboration pattern improved", markdown)
