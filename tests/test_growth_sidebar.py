import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentboost.core import add_goal, complete_goal
from agentboost.growth_sidebar import (
    build_sidebar_state,
    daily_missions,
    default_notifications_file,
    load_settings,
    memory_monitor_state,
    network_activity_state,
    perform_meta_review_from_app,
    perform_identity_update_from_app,
    perform_skill_prompt_review_from_app,
    notify_caffeinate,
    notify_growth_updates,
    notify_memory_pressure,
    notify_new_badges,
    preferred_gui_backend,
    select_representative_badge,
    select_representative_badges,
    sidebar_main,
    status_animation_activity,
    weekly_missions,
)


class GrowthSidebarTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.events_file = self.repo / "data" / "ai-usage" / "events.jsonl"
        self.goals_file = self.repo / "data" / "ai-usage" / "goals.json"
        self.notifications_file = self.repo / "data" / "ai-usage" / "sidebar-notifications.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write_events(self, events):
        self.events_file.parent.mkdir(parents=True, exist_ok=True)
        self.events_file.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

    def write_review_state(self, *, tasks=5, score=96, status="green", last_review="2026-05-01"):
        state = self.repo / "skill" / "review-state.md"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(
            "\n".join(
                [
                    "# Workflow Review State",
                    "",
                    f"- Last meta-review: {last_review}",
                    f"- Non-trivial tasks since last meta-review: {tasks}",
                    "- Circuit-breakers since last meta-review: 0",
                    "- Repeated-assumption failures since last meta-review: 0",
                    f"- Latest meta-review score: {score}",
                    "- Rolling 3-review average: 100",
                    f"- Score status: {status}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (self.repo / "skill" / "review-log.md").write_text("# Workflow Review Log\n", encoding="utf-8")
        return state

    def big_two_agent_events(self):
        return [
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
            },
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
            },
        ]

    def two_key_agent_events(self):
        events = self.big_two_agent_events()
        events[0]["input_tokens"] = 1_100_000_000
        events[0]["total_tokens"] = 1_100_000_000
        events[1]["input_tokens"] = 1_200_000_000
        events[1]["total_tokens"] = 1_200_000_000
        return events

    def heavy_user_week_events(self):
        events = self.big_two_agent_events()
        events[0]["input_tokens"] = 6_000_000_000
        events[0]["total_tokens"] = 6_000_000_000
        events[1]["input_tokens"] = 4_000_000_000
        events[1]["total_tokens"] = 4_000_000_000
        return events

    def complete_verification_goal(self, index):
        goal = add_goal(
            goals_file=self.goals_file,
            title=f"Verified workflow {index}",
            goal_type="verification",
            period="2026-05",
            target=1,
            human_skill="verification",
        )
        complete_goal(
            self.goals_file,
            goal["goal_id"],
            evidence=[f"task-log:{index}"],
            reflection="I checked the evidence before trusting the agent output.",
            completed_at=f"2026-05-0{min(index + 1, 6)}T03:00:00Z",
        )
        return goal

    def test_sidebar_state_exposes_progress_badges_missions_and_new_achievements(self):
        self.write_events(self.big_two_agent_events())
        for index in range(10):
            self.complete_verification_goal(index)

        state = build_sidebar_state(
            self.repo,
            now="2026-05-06T04:00:00Z",
            notification_file=self.notifications_file,
        )

        self.assertEqual(state["app"], "AgentBoost")
        self.assertEqual(state["level"], 9)
        self.assertEqual(state["level_label"], "Lv 9")
        self.assertEqual(state["xp"], 2_255)
        self.assertEqual(state["xp_breakdown"]["base_xp"], 2_250)
        self.assertEqual(state["xp_breakdown"]["mission_xp"], 5)
        self.assertEqual(state["level_progress"]["current_level"], 9)
        self.assertEqual(state["level_progress"]["current_level_xp"], 150)
        self.assertEqual(state["level_progress"]["current_level_required_xp"], 1_242)
        self.assertEqual(state["level_progress"]["xp_to_next_level"], 1_092)
        self.assertEqual(state["level_progress"]["progress_percent"], 12)
        self.assertEqual(state["level_progress"]["total_xp"], state["xp"])
        self.assertGreater(state["workforce_fitness_score"], 0)
        badge_names = [badge["name"] for badge in state["badges"]]
        self.assertIn("Billion Club", badge_names)
        self.assertIn("Two key agents", badge_names)
        self.assertIn("Heavy user", badge_names)
        earned = [badge["name"] for badge in state["badges"] if badge["status"] == "earned"]
        self.assertIn("Billion Club", earned)
        self.assertNotIn("Two key agents", earned)
        self.assertNotIn("Heavy user", earned)
        two_key = next(badge for badge in state["badges"] if badge["name"] == "Two key agents")
        self.assertEqual(two_key["badge_id"], "b0aec0de4cd56059")
        self.assertEqual(two_key["status"], "in_progress")
        self.assertEqual(two_key["endorsement_text"], "Token usage for Claude and Codex each reaches over 1B.")
        heavy = next(badge for badge in state["badges"] if badge["name"] == "Heavy user")
        self.assertEqual(heavy["badge_id"], "8f5a9291c21f44bf")
        self.assertEqual(heavy["status"], "in_progress")
        self.assertEqual(heavy["progress_percent"], 10)
        self.assertEqual(heavy["endorsement_text"], "Weekly Claude and Codex token usage reaches 10B total.")
        self.assertIn("daily_missions", state)
        self.assertIn("weekly_missions", state)
        self.assertIn("agentboost_daily_7d", state)
        self.assertEqual(len(state["agentboost_daily_7d"]), 7)
        self.assertGreater(sum(day["claude"] + day["codex"] for day in state["agentboost_daily_7d"]), 0)
        self.assertIn("token_activity", state)
        self.assertGreaterEqual(state["token_activity"]["today_tokens"], 1_000_000_000)
        self.assertEqual(state["token_activity"]["intensity"], "surge")
        self.assertLessEqual(state["token_activity"]["animation_interval_seconds"], 0.25)
        self.assertGreater(state["token_activity"]["rocket_speed"], 0)
        self.assertNotIn("frames", state["token_activity"])
        self.assertIn("badge_inventory", state)
        self.assertIn("earned_badges", state)
        earned_badge_names = [badge["name"] for badge in state["earned_badges"]]
        self.assertIn("Billion Club", earned_badge_names)
        self.assertNotIn("Two key agents", earned_badge_names)
        self.assertNotIn("Heavy user", earned_badge_names)
        inventory_by_name = {badge["name"]: badge for badge in state["badge_inventory"]}
        self.assertFalse(inventory_by_name["Heavy user"]["can_select"])
        self.assertFalse(inventory_by_name["Heavy user"]["is_representative"])
        self.assertIn("Verifier Streak", earned_badge_names)
        self.assertIn("representative_badge", state)
        self.assertEqual(state["representative_badge"]["name"], "Billion Club")
        self.assertEqual(state["representative_badge"]["badge_id"], "a758dd50b1415e27")
        self.assertEqual(state["representative_badge"]["endorsement_text"], "Uses AI agents as daily working partners, not occasional search boxes.")
        self.assertTrue(any(badge["is_representative"] for badge in state["badge_inventory"]))
        self.assertTrue(any(mission["mission_id"] == "create_reusable_artifact" for mission in state["daily_missions"]))
        self.assertTrue(any(mission["mission_id"] == "recover_high_token_work" for mission in state["daily_missions"]))
        self.assertTrue(any(mission["mission_id"] == "ship_reusable_workflow" for mission in state["weekly_missions"]))
        self.assertTrue(any(badge["name"] == "Billion Club" for badge in state["new_achievements"]))

    def test_heavy_user_badge_is_earned_by_weekly_ten_billion_claude_codex_usage(self):
        self.write_events(self.heavy_user_week_events())

        state = build_sidebar_state(
            self.repo,
            now="2026-05-06T04:00:00Z",
            notification_file=self.notifications_file,
        )

        heavy = next(badge for badge in state["badges"] if badge["name"] == "Heavy user")
        self.assertEqual(heavy["badge_id"], "8f5a9291c21f44bf")
        self.assertEqual(heavy["status"], "earned")
        self.assertEqual(heavy["progress_percent"], 100)
        self.assertEqual(heavy["threshold"], 10_000_000_000)
        self.assertIn("Heavy user", [badge["name"] for badge in state["earned_badges"]])

    def test_daily_missions_reflect_empty_and_open_goal_state(self):
        empty_missions = daily_missions([], [], now="2026-05-06T04:00:00Z")
        self.assertEqual(empty_missions[0]["mission_id"], "collect_usage")
        self.assertTrue(any(mission["mission_id"] == "define_verification_goal" for mission in empty_missions))

        open_goal = add_goal(
            goals_file=self.goals_file,
            title="Finish one verified implementation",
            goal_type="verification",
            period="2026-05",
            target=1,
            human_skill="verification",
        )
        missions = daily_missions(self.big_two_agent_events(), [open_goal], now="2026-05-06T04:00:00Z")
        self.assertTrue(any(mission["mission_id"] == "advance_open_goal" for mission in missions))
        self.assertTrue(any("Finish one verified implementation" in mission["title"] for mission in missions))
        self.assertFalse(any(mission["mission_id"] == "complete_goal_evidence" for mission in missions))
        self.assertFalse(any("Complete one goal with evidence" in mission["title"] for mission in missions))

    def test_weekly_missions_encourage_weekly_agentboost(self):
        missions = weekly_missions([], [], now="2026-05-06T04:00:00Z")
        self.assertTrue(any(mission["mission_id"] == "weekly_collect_usage" for mission in missions))
        self.assertTrue(any(mission["mission_id"] == "weekly_skill_prompt_review" for mission in missions))

        open_goal = add_goal(
            goals_file=self.goals_file,
            title="Review one AI-assisted diff",
            goal_type="review",
            period="2026-W19",
            target=1,
            human_skill="review",
        )
        missions = weekly_missions(self.big_two_agent_events(), [open_goal], now="2026-05-06T04:00:00Z")
        self.assertTrue(any(mission["mission_id"] == "weekly_finish_open_goal" for mission in missions))
        self.assertTrue(any("Review one AI-assisted diff" in mission["title"] for mission in missions))

    def test_weekly_missions_use_workday_cap_and_skill_prompt_review_artifact(self):
        for path, body in {
            "skill/public/code-review/codex/SKILL.md": "# Code Review\n",
            "skill/public/two-phase-execution/codex/SKILL.md": "# Two Phase\n",
            "adapters/codex/instructions.md": "Codex prompt\n",
            "adapters/claude/CLAUDE.md": "Claude prompt\n",
            "identity/personality.md": "Personality\n",
            "identity/thinkingpath.md": "Thinking path\n",
            "skill/public/two-phase-execution/common/state/task-log.md": "- Verification and evidence were encoded in AGENTS.md.\n",
            "skill/public/two-phase-execution/common/state/review-log.md": "- Scope was reviewed before solve.\n",
        }.items():
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")

        events = []
        for day in ["2026-05-03", "2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08", "2026-05-09"]:
            events.append(
                {
                    "source_agent": "codex",
                    "occurred_at": f"{day}T10:00:00+09:00",
                    "total_tokens": 100,
                }
            )
        for day in ["2026-04-20", "2026-04-21", "2026-04-22", "2026-04-23", "2026-04-24"]:
            events.append(
                {
                    "source_agent": "claude",
                    "occurred_at": f"{day}T10:00:00+09:00",
                    "total_tokens": 100,
                }
            )

        missions = weekly_missions(events, [], now="2026-05-09T12:00:00+09:00", repo_root=self.repo)
        weekly_streak = next(mission for mission in missions if mission["mission_id"] == "weekly_ai_streak")
        review = next(mission for mission in missions if mission["mission_id"] == "weekly_skill_prompt_review")
        identity = next(mission for mission in missions if mission["mission_id"] == "weekly_identity_update")

        self.assertEqual(weekly_streak["title"], "Build a 5-workday AI rhythm")
        self.assertEqual(weekly_streak["frequency"], "5/week")
        self.assertEqual(weekly_streak["metric"], "active_workdays")
        self.assertEqual(weekly_streak["goal"], 5)
        self.assertEqual(weekly_streak["progress"], 5)
        self.assertEqual(weekly_streak["status"], "done")
        self.assertEqual(weekly_streak["target_source"], "recent_weekly_workdays")

        self.assertEqual(review["title"], "Review current skills and prompts")
        self.assertEqual(review["status"], "active")
        self.assertEqual(review["command_hint"], "bin/agentboost --do-skill-prompt-review")
        self.assertEqual(review["evidence_hint"], "skill/prompt review artifact for the current week")
        self.assertEqual(review["check_cost"], "local_artifact_scan")

        self.assertEqual(identity["title"], "Update personality and thinking path")
        self.assertEqual(identity["status"], "active")
        self.assertEqual(identity["command_hint"], "bin/agentboost --do-identity-update")
        self.assertEqual(identity["evidence_hint"], "identity draft update artifact for the current week")
        self.assertEqual(identity["metric"], "identity_update_this_week")
        self.assertEqual(identity["check_cost"], "local_artifact_scan")

        result = perform_skill_prompt_review_from_app(self.repo, now="2026-05-09T12:00:00+09:00")
        artifact = Path(result["review_artifact"])
        self.assertTrue(artifact.exists())
        text = artifact.read_text(encoding="utf-8")
        self.assertIn("# AgentBoost Skill and Prompt Review", text)
        self.assertIn("skill/public/code-review/codex/SKILL.md", text)
        self.assertIn("adapters/codex/instructions.md", text)
        self.assertEqual(result["skills_reviewed"], 2)
        self.assertEqual(result["prompts_reviewed"], 4)

        done = weekly_missions(events, [], now="2026-05-09T12:00:00+09:00", repo_root=self.repo)
        done_review = next(mission for mission in done if mission["mission_id"] == "weekly_skill_prompt_review")
        self.assertEqual(done_review["status"], "done")
        self.assertEqual(done_review["progress"], 1)

        identity_result = perform_identity_update_from_app(self.repo, now="2026-05-09T12:01:00+09:00")
        summary = Path(identity_result["review_artifact"])
        self.assertTrue(summary.exists())
        self.assertTrue(Path(identity_result["written"]["personality"]).exists())
        self.assertTrue(Path(identity_result["written"]["thinking"]).exists())
        self.assertGreater(identity_result["evidence_items"], 0)
        summary_text = summary.read_text(encoding="utf-8")
        self.assertIn("# AgentBoost Identity Update", summary_text)
        self.assertIn("Evidence items:", summary_text)

        identity_done = weekly_missions(events, [], now="2026-05-09T12:02:00+09:00", repo_root=self.repo)
        done_identity = next(mission for mission in identity_done if mission["mission_id"] == "weekly_identity_update")
        self.assertEqual(done_identity["status"], "done")
        self.assertEqual(done_identity["progress"], 1)

        state = build_sidebar_state(self.repo, now="2026-05-09T12:03:00+09:00", notification_file=self.notifications_file)
        self.assertEqual(state["identity_update"]["status"], "done")
        self.assertEqual(state["identity_update"]["progress"], 1)
        self.assertEqual(state["identity_update"]["evidence_items"], identity_result["evidence_items"])
        self.assertEqual(state["identity_update"]["source_file_count"], identity_result["source_file_count"])
        self.assertEqual(state["identity_update"]["updated_at"], "2026-05-09")
        json.dumps(state)

    def test_missions_are_frequency_based_and_auto_checked_from_loaded_state(self):
        events = [
            {
                "source_agent": "codex",
                "occurred_at": "2026-05-03T10:00:00+09:00",
                "total_tokens": 100,
            },
            {
                "source_agent": "claude",
                "occurred_at": "2026-05-05T10:00:00+09:00",
                "total_tokens": 100,
            },
            {
                "source_agent": "codex",
                "occurred_at": "2026-05-06T10:00:00+09:00",
                "total_tokens": 100,
            },
        ]
        goals = [
            {
                "goal_id": "done-this-week",
                "title": "Review one diff",
                "type": "verification",
                "status": "completed",
                "completed_at": "2026-05-05T11:00:00+09:00",
                "evidence": ["task-log:review"],
            }
        ]

        daily = daily_missions(events, goals, now="2026-05-06T12:00:00+09:00")
        weekly = weekly_missions(events, goals, now="2026-05-06T12:00:00+09:00")

        daily_turn = next(mission for mission in daily if mission["mission_id"] == "daily_ai_turn")
        weekly_streak = weekly[0]
        weekly_review = next(mission for mission in weekly if mission["mission_id"] == "weekly_skill_prompt_review")

        self.assertEqual(daily_turn["cadence"], "daily")
        self.assertEqual(daily_turn["frequency"], "1/day")
        self.assertEqual(daily_turn["progress"], 1)
        self.assertEqual(daily_turn["goal"], 1)
        self.assertEqual(daily_turn["status"], "done")
        self.assertTrue(daily_turn["auto_check"])
        self.assertEqual(daily_turn["check_cost"], "loaded_events_only")

        self.assertEqual(weekly_streak["mission_id"], "weekly_ai_streak")
        self.assertEqual(weekly_streak["cadence"], "weekly")
        self.assertEqual(weekly_streak["frequency"], "4/week")
        self.assertEqual(weekly_streak["metric"], "active_workdays")
        self.assertEqual(weekly_streak["progress"], 2)
        self.assertEqual(weekly_streak["goal"], 4)
        self.assertEqual(weekly_streak["status"], "active")
        self.assertTrue(weekly_streak["auto_check"])
        self.assertEqual(weekly_streak["check_cost"], "loaded_events_only")

        self.assertEqual(weekly_review["progress"], 0)
        self.assertEqual(weekly_review["goal"], 1)
        self.assertEqual(weekly_review["status"], "active")
        self.assertFalse(any("difficulty" in mission for mission in daily + weekly))

    def test_mission_frequency_self_adjusts_from_recent_loaded_state(self):
        events = []
        for day in range(7, 14):
            for turn in range(3):
                events.append(
                    {
                        "source_agent": "codex" if turn % 2 else "claude",
                        "occurred_at": f"2026-05-{day:02d}T1{turn}:00:00+09:00",
                        "total_tokens": 100,
                    }
                )
        for turn in range(3):
            events.append(
                {
                    "source_agent": "codex",
                    "occurred_at": f"2026-05-14T1{turn}:00:00+09:00",
                    "total_tokens": 100,
                }
            )

        daily = daily_missions(events, [], now="2026-05-14T18:00:00+09:00")
        daily_turn = next(mission for mission in daily if mission["mission_id"] == "daily_ai_turn")

        self.assertEqual(daily_turn["frequency"], "2/day")
        self.assertEqual(daily_turn["goal"], 2)
        self.assertEqual(daily_turn["progress"], 2)
        self.assertEqual(daily_turn["status"], "done")
        self.assertTrue(daily_turn["adaptive"])
        self.assertEqual(daily_turn["target_source"], "recent_active_day_average")
        self.assertEqual(daily_turn["target_window_days"], 14)

        weekly_events = []
        for day in [
            "2026-04-20",
            "2026-04-21",
            "2026-04-22",
            "2026-04-23",
            "2026-04-24",
            "2026-04-27",
            "2026-04-28",
            "2026-04-29",
            "2026-04-30",
            "2026-05-01",
        ]:
            weekly_events.append(
                {
                    "source_agent": "claude",
                    "occurred_at": f"{day}T10:00:00+09:00",
                    "total_tokens": 100,
                }
            )
        for day in [3, 4, 5, 6, 7, 8]:
            weekly_events.append(
                {
                    "source_agent": "codex",
                    "occurred_at": f"2026-05-{day:02d}T10:00:00+09:00",
                    "total_tokens": 100,
                }
            )

        weekly = weekly_missions(weekly_events, [], now="2026-05-09T12:00:00+09:00")
        weekly_streak = weekly[0]

        self.assertEqual(weekly_streak["mission_id"], "weekly_ai_streak")
        self.assertEqual(weekly_streak["frequency"], "5/week")
        self.assertEqual(weekly_streak["goal"], 5)
        self.assertEqual(weekly_streak["progress"], 5)
        self.assertEqual(weekly_streak["status"], "done")
        self.assertTrue(weekly_streak["adaptive"])
        self.assertEqual(weekly_streak["target_source"], "recent_weekly_workdays")
        self.assertEqual(weekly_streak["target_window_days"], 28)

    def test_token_activity_speeds_up_with_current_usage(self):
        idle = build_sidebar_state(self.repo, now="2026-05-06T04:00:00Z", notification_file=self.notifications_file)[
            "token_activity"
        ]
        self.assertEqual(idle["today_tokens"], 0)
        self.assertEqual(idle["intensity"], "idle")
        self.assertEqual(idle["animation_interval_seconds"], 1.5)

        self.write_events(
            [
                {
                    "event_id": "codex-now",
                    "source_agent": "codex",
                    "source_path": "fixture",
                    "source_session_id": "codex-now",
                    "occurred_at": "2026-05-06T02:00:00Z",
                    "project_path": "/work/project",
                    "input_tokens": 50_000_000,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 50_000_000,
                    "record_type": "turn",
                    "imported_at": "2026-05-06T02:00:00Z",
                }
            ]
        )
        active = build_sidebar_state(self.repo, now="2026-05-06T04:00:00Z", notification_file=self.notifications_file)[
            "token_activity"
        ]
        self.assertEqual(active["intensity"], "high")
        self.assertLess(active["animation_interval_seconds"], idle["animation_interval_seconds"])

    def test_recent_token_activity_uses_last_local_minute(self):
        self.write_events(
            [
                {
                    "event_id": "recent",
                    "source_agent": "codex",
                    "source_path": "fixture",
                    "source_session_id": "recent",
                    "occurred_at": "2026-05-06T04:00:10+09:00",
                    "project_path": "/work/project",
                    "input_tokens": 1_250_000,
                    "cached_input_tokens": 0,
                    "output_tokens": 250_000,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 1_500_000,
                    "record_type": "turn",
                    "imported_at": "2026-05-06T04:00:10+09:00",
                },
                {
                    "event_id": "old",
                    "source_agent": "codex",
                    "source_path": "fixture",
                    "source_session_id": "old",
                    "occurred_at": "2026-05-06T03:58:59+09:00",
                    "project_path": "/work/project",
                    "input_tokens": 90_000_000,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 90_000_000,
                    "record_type": "turn",
                    "imported_at": "2026-05-06T03:58:59+09:00",
                },
            ]
        )

        state = build_sidebar_state(self.repo, now="2026-05-06T04:00:30+09:00", notification_file=self.notifications_file)

        recent = state["recent_token_activity"]
        self.assertEqual(recent["last_1m_tokens"], 1_500_000)
        self.assertEqual(recent["display_tokens"], "1.5M")
        self.assertEqual(recent["activity_level"], "high")
        self.assertEqual(recent["rocket_state"], "surging")
        self.assertGreater(recent["rocket_speed"], 0)
        self.assertGreater(recent["rocket_altitude"], 0)
        self.assertTrue(recent["has_flame"])
        self.assertIn("token_activity", state)

    def test_recent_token_activity_splits_rocket_for_active_claude_and_codex(self):
        self.write_events(
            [
                {
                    "event_id": "claude-recent",
                    "source_agent": "claude",
                    "source_path": "fixture",
                    "source_session_id": "claude-recent",
                    "occurred_at": "2026-05-06T04:00:05+09:00",
                    "project_path": "/work/project",
                    "input_tokens": 20_000,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 20_000,
                    "record_type": "session",
                    "imported_at": "2026-05-06T04:00:05+09:00",
                },
                {
                    "event_id": "codex-recent",
                    "source_agent": "codex",
                    "source_path": "fixture",
                    "source_session_id": "codex-recent",
                    "occurred_at": "2026-05-06T04:00:10+09:00",
                    "project_path": "/work/project",
                    "input_tokens": 30_000,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 30_000,
                    "record_type": "turn",
                    "imported_at": "2026-05-06T04:00:10+09:00",
                },
            ]
        )

        state = build_sidebar_state(self.repo, now="2026-05-06T04:00:30+09:00", notification_file=self.notifications_file)

        recent = state["recent_token_activity"]
        self.assertEqual(recent["active_agents"], ["claude", "codex"])
        self.assertEqual(recent["rocket_count"], 2)
        self.assertEqual(recent["agent_usage"]["claude"]["last_1m_tokens"], 20_000)
        self.assertEqual(recent["agent_usage"]["codex"]["last_1m_tokens"], 30_000)
        self.assertEqual(state["status_animation_activity"]["rocket_count"], 2)

    def test_memory_monitor_alerts_at_80_percent_threshold(self):
        normal = memory_monitor_state(total_bytes=1_000, used_bytes=799, threshold_percent=80)
        alert = memory_monitor_state(total_bytes=1_000, used_bytes=800, threshold_percent=80)

        self.assertEqual(normal["threshold_percent"], 80)
        self.assertEqual(normal["used_percent"], 79)
        self.assertFalse(normal["alert"])
        self.assertEqual(normal["status"], "ok")
        self.assertEqual(alert["used_percent"], 80)
        self.assertTrue(alert["alert"])
        self.assertEqual(alert["status"], "alert")
        self.assertIn("80%", alert["message"])

    def test_sidebar_state_exposes_memory_monitor(self):
        state = build_sidebar_state(self.repo, now="2026-05-06T04:00:00Z", notification_file=self.notifications_file)

        monitor = state["memory_monitor"]
        self.assertEqual(monitor["threshold_percent"], 80)
        self.assertIn("used_percent", monitor)
        self.assertIn("used_bytes", monitor)
        self.assertIn("total_bytes", monitor)
        self.assertIsInstance(monitor["alert"], bool)
        self.assertIn(monitor["status"], {"ok", "alert", "unavailable"})

    def test_memory_pressure_notification_dedupes_once_per_day(self):
        sent = []
        monitor = memory_monitor_state(total_bytes=1_000, used_bytes=850, threshold_percent=80)

        first = notify_memory_pressure(
            monitor,
            self.notifications_file,
            now="2026-05-06T04:05:00Z",
            sender=lambda title, message: sent.append((title, message)),
        )
        second = notify_memory_pressure(
            monitor,
            self.notifications_file,
            now="2026-05-06T05:05:00Z",
            sender=lambda title, message: sent.append((title, message)),
        )

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(sent, [("AgentBoost memory alert", "System memory is 85% used. Consider closing idle AI agent sessions before spawning more subagents.")])
        ledger = json.loads(self.notifications_file.read_text(encoding="utf-8"))
        self.assertEqual(ledger["memory_alerts"][0]["used_percent"], 85)
        self.assertEqual(ledger["memory_alerts"][0]["threshold_percent"], 80)

    def test_status_animation_activity_keeps_token_context_when_network_controls_speed(self):
        self.write_events(
            [
                {
                    "event_id": "codex-active-today",
                    "source_agent": "codex",
                    "source_path": "fixture",
                    "source_session_id": "codex-active-today",
                    "occurred_at": "2026-05-06T02:00:00Z",
                    "project_path": "/work/project",
                    "input_tokens": 50_000_000,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 50_000_000,
                    "record_type": "turn",
                    "imported_at": "2026-05-06T02:00:00Z",
                }
            ]
        )

        state = build_sidebar_state(self.repo, now="2026-05-06T04:00:00Z", notification_file=self.notifications_file)

        self.assertEqual(state["recent_token_activity"]["rocket_speed"], 0)
        animation = state["status_animation_activity"]
        self.assertEqual(animation["source"], "today")
        self.assertEqual(animation["speed_source"], "token_usage")
        self.assertEqual(animation["display_tokens"], "50M")
        self.assertEqual(animation["activity_level"], "high")
        self.assertEqual(animation["rocket_speed"], 0)
        self.assertEqual(animation["rocket_altitude"], 0)
        self.assertFalse(animation["has_flame"])

    def test_status_animation_activity_splits_rocket_for_two_agent_day_when_recent_window_is_idle(self):
        self.write_events(
            [
                {
                    "event_id": "claude-active-today",
                    "source_agent": "claude",
                    "source_path": "fixture",
                    "source_session_id": "claude-active-today",
                    "occurred_at": "2026-05-06T02:00:00Z",
                    "project_path": "/work/project",
                    "input_tokens": 20_000_000,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 20_000_000,
                    "record_type": "turn",
                    "imported_at": "2026-05-06T02:00:00Z",
                },
                {
                    "event_id": "codex-active-today",
                    "source_agent": "codex",
                    "source_path": "fixture",
                    "source_session_id": "codex-active-today",
                    "occurred_at": "2026-05-06T02:05:00Z",
                    "project_path": "/work/project",
                    "input_tokens": 30_000_000,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 30_000_000,
                    "record_type": "turn",
                    "imported_at": "2026-05-06T02:05:00Z",
                },
            ]
        )

        state = build_sidebar_state(self.repo, now="2026-05-06T04:00:00Z", notification_file=self.notifications_file)

        self.assertEqual(state["recent_token_activity"]["rocket_speed"], 0)
        animation = state["status_animation_activity"]
        self.assertEqual(animation["source"], "today")
        self.assertEqual(animation["speed_source"], "token_usage")
        self.assertEqual(animation["active_agents"], ["claude", "codex"])
        self.assertEqual(animation["rocket_count"], 2)
        self.assertEqual(animation["rocket_speed"], 0)
        self.assertEqual(animation["rocket_altitude"], 0)

    def test_status_animation_adds_running_idle_agent_to_recent_rocket_contract(self):
        recent = {
            "last_1m_tokens": 150_000,
            "display_tokens": "150K",
            "activity_level": "high",
            "rocket_state": "surging",
            "rocket_speed": 1.0,
            "rocket_altitude": 70,
            "has_flame": True,
            "active_agents": ["claude"],
            "rocket_count": 1,
        }
        today = {
            "today_tokens": 2_000_000,
            "intensity": "active",
            "rocket_speed": 0.45,
            "active_agents": ["claude", "codex"],
            "rocket_count": 2,
        }
        network = network_activity_state(outbound_bytes_per_second=0)
        running = {
            "active_agents": ["claude", "codex"],
            "rocket_count": 2,
        }

        animation = status_animation_activity(recent, today, network, running)

        self.assertEqual(animation["source"], "recent")
        self.assertEqual(animation["active_agents"], ["claude", "codex"])
        self.assertEqual(animation["rocket_count"], 2)
        self.assertEqual(animation["rocket_speed"], recent["rocket_speed"])
        self.assertEqual(animation["last_1m_tokens"], 150_000)

    def test_status_animation_shows_running_agent_without_token_usage(self):
        recent = {
            "last_1m_tokens": 0,
            "display_tokens": "0",
            "activity_level": "idle",
            "rocket_state": "waiting",
            "rocket_speed": 0.0,
            "rocket_altitude": 0,
            "has_flame": False,
            "active_agents": [],
            "rocket_count": 1,
            "agent_usage": {
                "claude": {"last_1m_tokens": 0, "display_tokens": "0"},
                "codex": {"last_1m_tokens": 0, "display_tokens": "0"},
            },
        }
        today = {
            "today_tokens": 0,
            "intensity": "idle",
            "rocket_speed": 0.0,
            "active_agents": [],
            "rocket_count": 1,
        }
        network = network_activity_state(outbound_bytes_per_second=0)
        running = {
            "active_agents": ["codex"],
            "rocket_count": 1,
        }

        animation = status_animation_activity(recent, today, network, running)

        self.assertEqual(animation["source"], "running")
        self.assertEqual(animation["active_agents"], ["codex"])
        self.assertEqual(animation["rocket_count"], 1)
        self.assertEqual(animation["rocket_speed"], 0)
        self.assertEqual(animation["rocket_altitude"], 0)
        self.assertEqual(animation["agent_usage"]["codex"]["last_1m_tokens"], 0)

    def test_network_activity_state_maps_outbound_bytes_to_animation_speed(self):
        idle = network_activity_state(outbound_bytes_per_second=0)
        active = network_activity_state(outbound_bytes_per_second=64_000)
        surge = network_activity_state(outbound_bytes_per_second=500_000)

        self.assertEqual(idle["activity_level"], "idle")
        self.assertEqual(idle["rocket_speed"], 0)
        self.assertFalse(idle["has_flame"])
        self.assertEqual(active["activity_level"], "active")
        self.assertGreater(active["rocket_speed"], idle["rocket_speed"])
        self.assertTrue(active["has_flame"])
        self.assertEqual(surge["activity_level"], "surge")
        self.assertGreater(surge["rocket_speed"], active["rocket_speed"])

    def test_status_animation_composes_usage_speed_with_network_without_token_mutation(self):
        recent = {
            "last_1m_tokens": 250_000,
            "display_tokens": "250K",
            "activity_level": "high",
            "rocket_state": "surging",
            "rocket_speed": 1.1,
            "rocket_altitude": 90,
            "has_flame": True,
            "active_agents": ["codex"],
            "rocket_count": 1,
        }
        today = {
            "today_tokens": 50_000_000,
            "intensity": "high",
            "rocket_speed": 0.9,
            "active_agents": ["codex"],
            "rocket_count": 1,
        }
        network = network_activity_state(outbound_bytes_per_second=500_000)

        animation = status_animation_activity(recent, today, network)

        self.assertEqual(animation["source"], "network")
        self.assertEqual(animation["speed_source"], "network")
        self.assertEqual(animation["display_tokens"], "250K")
        self.assertEqual(animation["activity_level"], "high")
        self.assertEqual(animation["active_agents"], ["codex"])
        self.assertEqual(animation["rocket_speed"], network["rocket_speed"])
        self.assertEqual(animation["animation_interval_seconds"], network["animation_interval_seconds"])
        self.assertEqual(animation["outbound_bytes_per_second"], 500_000)

    def test_status_animation_stops_when_recent_usage_is_zero_even_if_network_is_active(self):
        recent = {
            "last_1m_tokens": 0,
            "display_tokens": "0",
            "activity_level": "idle",
            "rocket_state": "waiting",
            "rocket_speed": 0.0,
            "rocket_altitude": 0,
            "has_flame": False,
            "active_agents": [],
            "rocket_count": 1,
        }
        today = {
            "today_tokens": 0,
            "intensity": "idle",
            "rocket_speed": 0.0,
            "active_agents": [],
            "rocket_count": 1,
        }
        network = network_activity_state(outbound_bytes_per_second=500_000)

        animation = status_animation_activity(recent, today, network)

        self.assertEqual(animation["speed_source"], "token_usage")
        self.assertEqual(animation["rocket_speed"], 0)
        self.assertEqual(animation["rocket_altitude"], 0)
        self.assertFalse(animation["has_flame"])
        self.assertEqual(animation["outbound_bytes_per_second"], 500_000)

    def test_recent_token_activity_matches_prd_thresholds_and_display_format(self):
        self.write_events(
            [
                {
                    "event_id": "moderate",
                    "source_agent": "codex",
                    "source_path": "fixture",
                    "source_session_id": "moderate",
                    "occurred_at": "2026-05-06T04:00:05+09:00",
                    "project_path": "/work/project",
                    "input_tokens": 1_500,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 1_500,
                    "record_type": "turn",
                    "imported_at": "2026-05-06T04:00:05+09:00",
                },
                {
                    "event_id": "high",
                    "source_agent": "codex",
                    "source_path": "fixture",
                    "source_session_id": "high",
                    "occurred_at": "2026-05-06T04:02:05+09:00",
                    "project_path": "/work/project",
                    "input_tokens": 50_000,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 50_000,
                    "record_type": "turn",
                    "imported_at": "2026-05-06T04:02:05+09:00",
                },
            ]
        )

        idle = build_sidebar_state(self.repo, now="2026-05-06T04:05:00+09:00", notification_file=self.notifications_file)[
            "recent_token_activity"
        ]
        moderate = build_sidebar_state(
            self.repo,
            now="2026-05-06T04:00:30+09:00",
            notification_file=self.notifications_file,
        )["recent_token_activity"]
        high = build_sidebar_state(
            self.repo,
            now="2026-05-06T04:02:30+09:00",
            notification_file=self.notifications_file,
        )["recent_token_activity"]

        self.assertEqual(idle["display_tokens"], "0")
        self.assertEqual(idle["activity_level"], "idle")
        self.assertEqual(idle["rocket_state"], "waiting")
        self.assertFalse(idle["has_flame"])
        self.assertEqual(moderate["display_tokens"], "1.5K")
        self.assertEqual(moderate["activity_level"], "moderate")
        self.assertEqual(moderate["rocket_state"], "flying")
        self.assertTrue(moderate["has_flame"])
        self.assertEqual(high["display_tokens"], "50K")
        self.assertEqual(high["activity_level"], "high")
        self.assertEqual(high["rocket_state"], "surging")
        self.assertGreater(high["rocket_speed"], moderate["rocket_speed"])
        self.assertGreater(high["rocket_altitude"], moderate["rocket_altitude"])

    def test_recent_token_activity_velocity_keeps_increasing_with_usage(self):
        def recent_for(tokens: int) -> dict[str, object]:
            self.write_events(
                [
                    {
                        "event_id": f"usage-{tokens}",
                        "source_agent": "codex",
                        "source_path": "fixture",
                        "source_session_id": f"usage-{tokens}",
                        "occurred_at": "2026-05-06T04:00:05+09:00",
                        "project_path": "/work/project",
                        "input_tokens": tokens,
                        "cached_input_tokens": 0,
                        "output_tokens": 0,
                        "reasoning_output_tokens": 0,
                        "total_tokens": tokens,
                        "record_type": "turn",
                        "imported_at": "2026-05-06T04:00:05+09:00",
                    }
                ]
            )
            return build_sidebar_state(
                self.repo,
                now="2026-05-06T04:00:30+09:00",
                notification_file=self.notifications_file,
            )["recent_token_activity"]

        low = recent_for(10_000)
        high = recent_for(250_000)
        surge = recent_for(1_500_000)

        self.assertGreater(high["rocket_speed"], low["rocket_speed"])
        self.assertGreater(surge["rocket_speed"], high["rocket_speed"])
        self.assertGreater(high["rocket_altitude"], low["rocket_altitude"])
        self.assertGreater(surge["rocket_altitude"], high["rocket_altitude"])

    def test_status_views_rotate_seven_day_total_and_token_per_minute(self):
        self.write_events(
            [
                {
                    "event_id": "today-recent",
                    "source_agent": "codex",
                    "source_path": "fixture",
                    "source_session_id": "today-recent",
                    "occurred_at": "2026-05-06T04:04:30+09:00",
                    "project_path": "/work/project",
                    "input_tokens": 1_000_000,
                    "cached_input_tokens": 0,
                    "output_tokens": 500_000,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 1_500_000,
                    "record_type": "turn",
                    "imported_at": "2026-05-06T04:04:30+09:00",
                },
                {
                    "event_id": "today-previous-window",
                    "source_agent": "codex",
                    "source_path": "fixture",
                    "source_session_id": "today-previous-window",
                    "occurred_at": "2026-05-06T03:56:30+09:00",
                    "project_path": "/work/project",
                    "input_tokens": 400_000,
                    "cached_input_tokens": 0,
                    "output_tokens": 100_000,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 500_000,
                    "record_type": "turn",
                    "imported_at": "2026-05-06T03:56:30+09:00",
                },
                {
                    "event_id": "yesterday",
                    "source_agent": "claude",
                    "source_path": "fixture",
                    "source_session_id": "yesterday",
                    "occurred_at": "2026-05-05T12:00:00+09:00",
                    "project_path": "/work/project",
                    "input_tokens": 2_000_000,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 2_000_000,
                    "record_type": "session",
                    "imported_at": "2026-05-05T12:00:00+09:00",
                },
                {
                    "event_id": "older-than-seven-days",
                    "source_agent": "claude",
                    "source_path": "fixture",
                    "source_session_id": "older-than-seven-days",
                    "occurred_at": "2026-04-20T12:00:00+09:00",
                    "project_path": "/work/project",
                    "input_tokens": 3_000_000,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 3_000_000,
                    "record_type": "session",
                    "imported_at": "2026-04-20T12:00:00+09:00",
                },
            ]
        )

        state = build_sidebar_state(self.repo, now="2026-05-06T04:05:00+09:00", notification_file=self.notifications_file)

        views = state["status_views"]
        self.assertEqual([view["view_id"] for view in views], ["last_7d_cumulative", "total_cumulative", "token_per_minute"])
        self.assertEqual(views[0]["label"], "7d")
        self.assertEqual(views[0]["tokens"], 4_000_000)
        self.assertEqual(views[0]["display_tokens"], "4M")
        self.assertEqual(views[0]["display_text"], "7d 4M")
        self.assertEqual(views[1]["label"], "Total")
        self.assertEqual(views[1]["tokens"], 7_000_000)
        self.assertEqual(views[1]["display_tokens"], "7M")
        self.assertEqual(views[1]["display_text"], "Total 7M")
        self.assertEqual(views[2]["label"], "Token/min")
        self.assertEqual(views[2]["tokens"], 1_500_000)
        self.assertEqual(views[2]["display_tokens"], "1.5M/min")
        self.assertEqual(views[2]["display_text"], "1.5M/min")

    def test_status_views_cycle_each_active_agent_then_combined_totals(self):
        self.write_events(
            [
                {
                    "event_id": "claude-recent",
                    "source_agent": "claude",
                    "source_path": "fixture",
                    "source_session_id": "claude-recent",
                    "occurred_at": "2026-05-06T04:04:45+09:00",
                    "project_path": "/work/project",
                    "input_tokens": 100_000,
                    "cached_input_tokens": 0,
                    "output_tokens": 20_000,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 120_000,
                    "record_type": "turn",
                    "imported_at": "2026-05-06T04:04:45+09:00",
                },
                {
                    "event_id": "codex-recent",
                    "source_agent": "codex",
                    "source_path": "fixture",
                    "source_session_id": "codex-recent",
                    "occurred_at": "2026-05-06T04:04:50+09:00",
                    "project_path": "/work/project",
                    "input_tokens": 1_000_000,
                    "cached_input_tokens": 0,
                    "output_tokens": 250_000,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 1_250_000,
                    "record_type": "turn",
                    "imported_at": "2026-05-06T04:04:50+09:00",
                },
                {
                    "event_id": "claude-seven-day",
                    "source_agent": "claude",
                    "source_path": "fixture",
                    "source_session_id": "claude-seven-day",
                    "occurred_at": "2026-05-05T04:00:00+09:00",
                    "project_path": "/work/project",
                    "input_tokens": 2_000_000,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 2_000_000,
                    "record_type": "turn",
                    "imported_at": "2026-05-05T04:00:00+09:00",
                },
                {
                    "event_id": "codex-older",
                    "source_agent": "codex",
                    "source_path": "fixture",
                    "source_session_id": "codex-older",
                    "occurred_at": "2026-04-20T04:00:00+09:00",
                    "project_path": "/work/project",
                    "input_tokens": 4_000_000,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 4_000_000,
                    "record_type": "turn",
                    "imported_at": "2026-04-20T04:00:00+09:00",
                },
            ]
        )

        state = build_sidebar_state(self.repo, now="2026-05-06T04:05:00+09:00", notification_file=self.notifications_file)

        views = state["status_views"]
        self.assertEqual(
            [view["view_id"] for view in views],
            [
                "claude_token_per_minute",
                "claude_last_7d_cumulative",
                "claude_total_cumulative",
                "codex_token_per_minute",
                "codex_last_7d_cumulative",
                "codex_total_cumulative",
                "combined_token_per_minute",
                "combined_last_7d_cumulative",
                "combined_total_cumulative",
            ],
        )
        self.assertEqual([view["scope"] for view in views[:6]], ["agent"] * 6)
        self.assertEqual([view["agent"] for view in views[:3]], ["claude"] * 3)
        self.assertEqual([view["agent"] for view in views[3:6]], ["codex"] * 3)
        self.assertEqual([view["scope"] for view in views[6:]], ["combined"] * 3)
        self.assertEqual(views[0]["tokens"], 120_000)
        self.assertEqual(views[3]["tokens"], 1_250_000)
        self.assertEqual(views[6]["tokens"], 1_370_000)
        self.assertEqual(views[7]["tokens"], 3_370_000)
        self.assertEqual(views[8]["tokens"], 7_370_000)
        self.assertEqual(views[0]["display_text"], "Claude 1m 120K/min")
        self.assertEqual(views[3]["display_text"], "Codex 1m 1.3M/min")
        self.assertEqual(views[6]["display_text"], "All 1m 1.4M/min")

    def test_weekly_missions_use_sunday_start_local_week(self):
        self.write_events(
            [
                {
                    "event_id": "sunday-local",
                    "source_agent": "codex",
                    "source_path": "fixture",
                    "source_session_id": "sunday-local",
                    "occurred_at": "2026-05-03T10:00:00+09:00",
                    "project_path": "/work/project",
                    "input_tokens": 1,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 1,
                    "record_type": "turn",
                    "imported_at": "2026-05-03T10:00:00+09:00",
                }
            ]
        )
        goal = add_goal(
            goals_file=self.goals_file,
            title="Sunday completion",
            goal_type="verification",
            period="2026-W-sunday",
            target=1,
            human_skill="verification",
        )
        complete_goal(
            self.goals_file,
            goal["goal_id"],
            evidence=["task-log:sunday"],
            reflection="Completed on Sunday local time.",
            completed_at="2026-05-03T11:00:00+09:00",
        )

        state = build_sidebar_state(self.repo, now="2026-05-04T12:00:00+09:00", notification_file=self.notifications_file)

        self.assertFalse(any(mission["mission_id"] == "weekly_collect_usage" for mission in state["weekly_missions"]))
        weekly_review = next(
            mission for mission in state["weekly_missions"] if mission["mission_id"] == "weekly_skill_prompt_review"
        )
        self.assertEqual(weekly_review["status"], "active")
        self.assertEqual(weekly_review["progress"], 0)
        self.assertEqual(weekly_review["goal"], 1)
        self.assertEqual(state["rollups"]["This Week"]["total_tokens"], 1)

    def test_meta_review_state_and_app_meta_review_surface(self):
        state_file = self.write_review_state(tasks=5, score=96)

        state = build_sidebar_state(self.repo, now="2026-05-06T04:00:00Z", notification_file=self.notifications_file)

        self.assertIn("meta_review", state)
        self.assertEqual(state["meta_review"]["status"], "due")
        self.assertTrue(state["meta_review"]["due"])
        self.assertEqual(state["meta_review"]["tasks_since_last_review"], 5)
        self.assertEqual(state["meta_review"]["latest_score"], 96)

        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        meta_json = subprocess.run(
            [
                sys.executable,
                "bin/agentboost",
                "--repo-root",
                str(self.repo),
                "--meta-review-json",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(meta_json.returncode, 0, meta_json.stderr)
        self.assertEqual(json.loads(meta_json.stdout)["status"], "due")

        done = subprocess.run(
            [
                sys.executable,
                "bin/agentboost",
                "--repo-root",
                str(self.repo),
                "--do-meta-review",
                "--meta-review-score",
                "97",
                "--now",
                "2026-05-06T04:00:00Z",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("meta_review=reviewed score=97 status=green", done.stdout)
        self.assertIn("review_artifact=", done.stdout)
        updated = state_file.read_text(encoding="utf-8")
        self.assertIn("- Last meta-review: 2026-05-06", updated)
        self.assertIn("- Non-trivial tasks since last meta-review: 0", updated)
        self.assertIn("- Latest meta-review score: 97", updated)
        review_log = (self.repo / "skill" / "review-log.md").read_text(encoding="utf-8")
        self.assertIn("ai-system App Meta-Review", review_log)
        self.assertIn("Completed a meta-review from the local ai-system app surface.", review_log)
        self.assertNotIn("Marked meta-review done", review_log)
        review_artifact = self.repo / "skill" / "meta-review-2026-05-06-ai-system-app.md"
        self.assertTrue(review_artifact.exists())
        artifact_text = review_artifact.read_text(encoding="utf-8")
        self.assertIn("# Workflow Meta-Review", artifact_text)
        self.assertIn("## Signals Reviewed", artifact_text)
        self.assertIn("Previous counters: tasks=5 cbs=0 repeats=0", artifact_text)
        self.assertIn("## Scorecard", artifact_text)

    def test_meta_review_ok_copy_uses_up_to_date_wording(self):
        self.write_review_state(tasks=0, score=95, last_review="2026-05-06")

        state = build_sidebar_state(self.repo, now="2026-05-06T04:00:00Z", notification_file=self.notifications_file)

        self.assertEqual(state["meta_review"]["status"], "ok")
        self.assertEqual(state["meta_review"]["reason"], "Meta-review is up to date.")
        self.assertNotIn("current", state["meta_review"]["reason"])

    def test_meta_review_state_prefers_canonical_two_phase_state_path(self):
        canonical = self.repo / "skill" / "public" / "two-phase-execution" / "common" / "state" / "review-state.md"
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_text(
            "\n".join(
                [
                    "# Workflow Review State",
                    "",
                    "- Last meta-review: 2026-05-01",
                    "- Non-trivial tasks since last meta-review: 5",
                    "- Circuit-breakers since last meta-review: 0",
                    "- Repeated-assumption failures since last meta-review: 0",
                    "- Latest meta-review score: 96",
                    "- Rolling 3-review average: 100",
                    "- Score status: green",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        state = build_sidebar_state(self.repo, now="2026-05-06T04:00:00Z", notification_file=self.notifications_file)

        self.assertEqual(state["meta_review"]["status"], "due")
        self.assertEqual(state["meta_review"]["state_file"], str(canonical))

    def test_only_one_representative_badge_can_be_selected_and_persisted(self):
        self.write_events(self.two_key_agent_events())
        for index in range(10):
            self.complete_verification_goal(index)
        initial = build_sidebar_state(self.repo, now="2026-05-06T04:00:00Z", notification_file=self.notifications_file)
        earned_badges = initial["earned_badges"]
        self.assertGreaterEqual(len(earned_badges), 2)
        two_agent_badge = next(badge for badge in earned_badges if badge["name"] == "Two key agents")
        ordered_badges = ([two_agent_badge] + [badge for badge in earned_badges if badge["badge_id"] != two_agent_badge["badge_id"]])[:3]

        selected = select_representative_badges(
            self.notifications_file,
            initial["badges"],
            [badge["badge_id"] for badge in ordered_badges],
        )
        state = build_sidebar_state(self.repo, now="2026-05-06T04:00:00Z", notification_file=self.notifications_file)

        self.assertTrue(selected)
        self.assertEqual(state["representative_badge"]["name"], "Two key agents")
        self.assertEqual(
            [badge["name"] for badge in state["representative_badges"]],
            ["Two key agents"],
        )
        selected_inventory = [badge for badge in state["badge_inventory"] if badge["is_representative"]]
        self.assertEqual(
            [(badge["name"], badge["representative_rank"]) for badge in selected_inventory],
            [("Two key agents", 1)],
        )
        ledger = json.loads(self.notifications_file.read_text(encoding="utf-8"))
        self.assertEqual(ledger["representative_badge_id"], two_agent_badge["badge_id"])
        self.assertEqual(ledger["representative_badge_ids"], [two_agent_badge["badge_id"]])

    def test_select_representative_badges_caps_and_dedupes_selection(self):
        self.write_events(self.big_two_agent_events())
        for index in range(10):
            self.complete_verification_goal(index)
        state = build_sidebar_state(self.repo, now="2026-05-06T04:00:00Z", notification_file=self.notifications_file)
        earned_ids = [badge["badge_id"] for badge in state["earned_badges"]]

        selected = select_representative_badges(
            self.notifications_file,
            state["badges"],
            earned_ids + earned_ids + ["missing-badge"],
        )

        self.assertTrue(selected)
        ledger = json.loads(self.notifications_file.read_text(encoding="utf-8"))
        self.assertEqual(ledger["representative_badge_ids"], earned_ids[:1])
        self.assertEqual(len(ledger["representative_badge_ids"]), len(set(ledger["representative_badge_ids"])))

    def test_select_representative_badge_rejects_unknown_badge(self):
        self.write_events(self.big_two_agent_events())
        state = build_sidebar_state(self.repo, now="2026-05-06T04:00:00Z", notification_file=self.notifications_file)

        selected = select_representative_badge(self.notifications_file, state["badges"], "missing-badge")

        self.assertFalse(selected)
        self.assertFalse(self.notifications_file.exists())

    def test_representative_badge_selection_rejects_unearned_achievable_badge(self):
        self.write_events(self.big_two_agent_events())
        state = build_sidebar_state(self.repo, now="2026-05-06T04:00:00Z", notification_file=self.notifications_file)
        heavy = next(badge for badge in state["badges"] if badge["name"] == "Heavy user")

        selected = select_representative_badge(self.notifications_file, state["badges"], heavy["badge_id"])

        self.assertFalse(selected)
        self.assertFalse(self.notifications_file.exists())

    def test_notify_new_badges_dedupes_and_writes_ledger(self):
        self.write_events(self.big_two_agent_events())
        for index in range(10):
            self.complete_verification_goal(index)
        state = build_sidebar_state(self.repo, now="2026-05-06T04:00:00Z", notification_file=self.notifications_file)
        sent = []

        first = notify_new_badges(
            state["badges"],
            self.notifications_file,
            now="2026-05-06T04:05:00Z",
            sender=lambda title, message: sent.append((title, message)),
        )
        second = notify_new_badges(
            state["badges"],
            self.notifications_file,
            now="2026-05-06T04:06:00Z",
            sender=lambda title, message: sent.append((title, message)),
        )

        self.assertGreaterEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(len(sent), len(first))
        ledger = json.loads(self.notifications_file.read_text(encoding="utf-8"))
        self.assertIn("badges", ledger)
        self.assertTrue(any(row["name"] == "Billion Club" for row in ledger["badges"]))

    def test_notification_settings_suppress_configured_categories(self):
        self.write_events(self.big_two_agent_events())
        for index in range(10):
            self.complete_verification_goal(index)
        state = build_sidebar_state(self.repo, now="2026-05-06T04:00:00Z", notification_file=self.notifications_file)
        settings_file = self.repo / "data" / "ai-usage" / "settings.json"
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(
            json.dumps(
                {
                    "notifications": {
                        "enabled": True,
                        "categories": {"achievements": False, "missions": False},
                    }
                }
            ),
            encoding="utf-8",
        )
        sent = []

        badge_result = notify_new_badges(
            state["badges"],
            self.notifications_file,
            now="2026-05-06T04:05:00Z",
            sender=lambda title, message: sent.append((title, message)),
            settings_file=settings_file,
        )
        growth_result = notify_growth_updates(
            state,
            self.notifications_file,
            now="2026-05-06T04:06:00Z",
            sender=lambda title, message: sent.append((title, message)),
            settings_file=settings_file,
        )

        self.assertEqual(badge_result, [])
        self.assertEqual(growth_result["total"], 0)
        self.assertEqual(sent, [])

    def test_growth_update_notifications_include_mission_encouragement_once_per_period(self):
        state = build_sidebar_state(self.repo, now="2026-05-06T04:00:00Z", notification_file=self.notifications_file)
        sent = []

        first = notify_growth_updates(
            state,
            self.notifications_file,
            now="2026-05-06T04:05:00Z",
            sender=lambda title, message: sent.append((title, message)),
        )
        second = notify_growth_updates(
            state,
            self.notifications_file,
            now="2026-05-06T05:05:00Z",
            sender=lambda title, message: sent.append((title, message)),
        )

        self.assertGreaterEqual(first["missions"], 1)
        self.assertEqual(second["missions"], 0)
        ledger = json.loads(self.notifications_file.read_text(encoding="utf-8"))
        self.assertIn("mission_prompts", ledger)
        self.assertTrue(any(row["period"] == "2026-05-06" for row in ledger["mission_prompts"]))
        self.assertTrue(any(row["period"] == "2026-05-03" for row in ledger["mission_prompts"]))

    def test_growth_update_notifications_include_meta_review_due_once_per_review_cycle(self):
        self.write_review_state(tasks=5, score=96, last_review="2026-05-06")
        settings_file = self.repo / "data" / "ai-usage" / "settings.json"
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(
            json.dumps(
                {
                    "notifications": {
                        "enabled": True,
                        "categories": {"achievements": False, "missions": False, "workflow": True},
                    }
                }
            ),
            encoding="utf-8",
        )
        state = build_sidebar_state(self.repo, now="2026-05-06T04:00:00Z", notification_file=self.notifications_file)
        sent = []

        first = notify_growth_updates(
            state,
            self.notifications_file,
            now="2026-05-06T04:05:00Z",
            sender=lambda title, message: sent.append((title, message)),
            settings_file=settings_file,
        )
        second = notify_growth_updates(
            state,
            self.notifications_file,
            now="2026-05-06T04:06:00Z",
            sender=lambda title, message: sent.append((title, message)),
            settings_file=settings_file,
        )

        self.assertEqual(first["meta_review"], 1)
        self.assertEqual(first["total"], 1)
        self.assertEqual(second["meta_review"], 0)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "AgentBoost meta-review due")
        self.assertIn("5 non-trivial tasks since last meta-review.", sent[0][1])
        ledger = json.loads(self.notifications_file.read_text(encoding="utf-8"))
        self.assertIn("meta_review_prompts", ledger)
        self.assertEqual(ledger["meta_review_prompts"][0]["key"], "meta-review-due:2026-05-06:due")

        perform_meta_review_from_app(self.repo, score=97, now="2026-05-06T04:07:00Z")
        updated_state_file = self.repo / "skill" / "review-state.md"
        updated_state_file.write_text(
            updated_state_file.read_text(encoding="utf-8").replace(
                "- Non-trivial tasks since last meta-review: 0",
                "- Non-trivial tasks since last meta-review: 5",
            ),
            encoding="utf-8",
        )
        renewed_state = build_sidebar_state(self.repo, now="2026-05-06T04:08:00Z", notification_file=self.notifications_file)
        third = notify_growth_updates(
            renewed_state,
            self.notifications_file,
            now="2026-05-06T04:08:30Z",
            sender=lambda title, message: sent.append((title, message)),
            settings_file=settings_file,
        )

        self.assertEqual(third["meta_review"], 1)
        self.assertEqual(len(sent), 2)

    def test_cli_state_json_notify_only_and_check(self):
        self.write_events(self.two_key_agent_events())
        for index in range(10):
            self.complete_verification_goal(index)

        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        state = subprocess.run(
            [
                sys.executable,
                "bin/agentboost",
                "--repo-root",
                str(self.repo),
                "--state-json",
                "--no-notify",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(state.returncode, 0, state.stderr)
        payload = json.loads(state.stdout)
        self.assertIn("daily_missions", payload)
        self.assertIn("weekly_missions", payload)
        self.assertIn("badges", payload)
        self.assertIn("memory_monitor", payload)
        self.assertEqual(payload["memory_monitor"]["threshold_percent"], 80)
        self.assertIn("network_activity", payload)
        self.assertIn("token_activity", payload)
        self.assertIn("import_window", payload)
        self.assertIn("representative_badge", payload)
        self.assertIn("badge_inventory", payload)

        target_badge = next(badge for badge in payload["badges"] if badge["name"] == "Two key agents")
        select_badge = subprocess.run(
            [
                sys.executable,
                "bin/agentboost",
                "--repo-root",
                str(self.repo),
                "--select-representative-badge",
                target_badge["badge_id"],
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(select_badge.returncode, 0, select_badge.stderr)
        self.assertIn("representative_badge=Two key agents", select_badge.stdout)

        notify = subprocess.run(
            [
                sys.executable,
                "bin/agentboost",
                "--repo-root",
                str(self.repo),
                "--notify-only",
                "--no-system-notify",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(notify.returncode, 0, notify.stderr)
        self.assertIn("notified=", notify.stdout)
        self.assertTrue(default_notifications_file(self.repo).exists())

        check = subprocess.run(
            [
                sys.executable,
                "bin/agentboost",
                "--repo-root",
                str(self.repo),
                "--check",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn("OK", check.stdout)

    def test_fallback_sidebar_keeps_import_window_debug_only(self):
        source = (Path.cwd() / "agentboost" / "growth_sidebar.py").read_text(encoding="utf-8")

        self.assertIn('"import_window": import_window(events)', source)
        self.assertNotIn("Import window:", source)

    def test_cli_can_run_identity_update_and_report_measurable_artifact(self):
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        source = self.repo / "skill" / "public" / "two-phase-execution" / "common" / "state" / "task-log.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "- The agent verified the implementation, measured state JSON, and encoded the lesson in AGENTS.md.\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                "bin/agentboost",
                "--repo-root",
                str(self.repo),
                "--do-identity-update",
                "--now",
                "2026-05-09T12:00:00+09:00",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("identity_update=reviewed", result.stdout)
        self.assertIn("evidence_items=", result.stdout)
        self.assertIn("review_artifact=", result.stdout)

    def test_cli_state_json_can_refresh_usage_before_rendering_activity(self):
        codex_dir = self.repo / "codex-home"
        claude_dir = self.repo / "claude-home"
        codex_log = codex_dir / "sessions" / "2026" / "05" / "06" / "active.jsonl"
        codex_log.parent.mkdir(parents=True)
        codex_log.write_text(
            json.dumps(
                {
                    "type": "event_msg",
                    "timestamp": "2026-05-06T04:00:05+09:00",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 75_000,
                                "cached_input_tokens": 0,
                                "output_tokens": 25_000,
                                "reasoning_output_tokens": 0,
                                "total_tokens": 100_000,
                            }
                        },
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)

        state = subprocess.run(
            [
                sys.executable,
                "bin/agentboost",
                "--repo-root",
                str(self.repo),
                "--state-json",
                "--no-notify",
                "--refresh-usage",
                "--codex-dir",
                str(codex_dir),
                "--claude-dir",
                str(claude_dir),
                "--now",
                "2026-05-06T04:00:30+09:00",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(state.returncode, 0, state.stderr)
        payload = json.loads(state.stdout)
        self.assertEqual(payload["usage_refresh"]["imported"], 1)
        self.assertEqual(payload["recent_token_activity"]["last_1m_tokens"], 100_000)
        self.assertEqual(payload["status_animation_activity"]["source"], "recent")
        self.assertEqual(payload["status_animation_activity"]["speed_source"], "token_usage")
        self.assertEqual(
            payload["status_animation_activity"]["rocket_speed"],
            payload["recent_token_activity"]["rocket_speed"],
        )

    def test_cli_settings_tips_inactivity_and_caffeinate_surfaces(self):
        tips_source = self.repo / "tips-source.json"
        tips_source.write_text(
            json.dumps(
                {
                    "tips": [
                        {"tip_id": "tip-1", "title": "Review before merge", "message": "Run verification."},
                        {"tip_id": "community-low", "title": "Too early", "source": "community", "upvotes": 9},
                        {"tip_id": "community-high", "title": "Shared win", "source": "community", "upvotes": 10},
                    ]
                }
            ),
            encoding="utf-8",
        )
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)

        set_enabled = subprocess.run(
            [
                sys.executable,
                "bin/agentboost",
                "--repo-root",
                str(self.repo),
                "--set-notification-enabled",
                "false",
                "--set-notification-category",
                "tips:true",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(set_enabled.returncode, 0, set_enabled.stderr)
        settings = subprocess.run(
            [sys.executable, "bin/agentboost", "--repo-root", str(self.repo), "--settings-json"],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(settings.returncode, 0, settings.stderr)
        settings_payload = json.loads(settings.stdout)
        self.assertFalse(settings_payload["notifications"]["enabled"])
        self.assertTrue(settings_payload["notifications"]["categories"]["tips"])
        self.assertTrue(settings_payload["caffeinate"]["enabled"])
        self.assertFalse(settings_payload["display"]["floating_overlay_enabled"])
        self.assertEqual(settings_payload["work_hours"]["start"], "09:00")
        self.assertEqual(settings_payload["work_hours"]["end"], "18:00")

        set_floating = subprocess.run(
            [
                sys.executable,
                "bin/agentboost",
                "--repo-root",
                str(self.repo),
                "--set-floating-overlay",
                "on",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(set_floating.returncode, 0, set_floating.stderr)
        self.assertIn("floating_overlay_enabled=True", set_floating.stdout)
        settings_after_floating = subprocess.run(
            [sys.executable, "bin/agentboost", "--repo-root", str(self.repo), "--settings-json"],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(settings_after_floating.returncode, 0, settings_after_floating.stderr)
        self.assertTrue(json.loads(settings_after_floating.stdout)["display"]["floating_overlay_enabled"])

        fetch = subprocess.run(
            [
                sys.executable,
                "bin/agentboost",
                "--repo-root",
                str(self.repo),
                "--fetch-tips",
                "--tips-source",
                str(tips_source),
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(fetch.returncode, 0, fetch.stderr)
        self.assertIn("tips_cached=2", fetch.stdout)
        self.assertTrue((self.repo / "data" / "ai-usage" / "tips-cache.json").exists())

        for category in ("tips", "inactivity", "caffeinate"):
            subprocess.run(
                [
                    sys.executable,
                    "bin/agentboost",
                    "--repo-root",
                    str(self.repo),
                    "--set-notification-enabled",
                    "true",
                    "--set-notification-category",
                    f"{category}:true",
                ],
                cwd=Path.cwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        tip = subprocess.run(
            [
                sys.executable,
                "bin/agentboost",
                "--repo-root",
                str(self.repo),
                "--tip-notify-only",
                "--no-system-notify",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(tip.returncode, 0, tip.stderr)
        self.assertIn("tips_notified=1", tip.stdout)
        tip_again = subprocess.run(
            [
                sys.executable,
                "bin/agentboost",
                "--repo-root",
                str(self.repo),
                "--tip-notify-only",
                "--no-system-notify",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(tip_again.returncode, 0, tip_again.stderr)
        self.assertIn("tips_notified=0", tip_again.stdout)

        inactivity = subprocess.run(
            [
                sys.executable,
                "bin/agentboost",
                "--repo-root",
                str(self.repo),
                "--inactivity-check",
                "--no-system-notify",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(inactivity.returncode, 0, inactivity.stderr)
        self.assertIn("inactivity_notified=", inactivity.stdout)

        caffeinate = subprocess.run(
            [
                sys.executable,
                "bin/agentboost",
                "--repo-root",
                str(self.repo),
                "--caffeinate-check",
                "--no-system-notify",
                "--now",
                "2026-05-06T11:00:00+09:00",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(caffeinate.returncode, 0, caffeinate.stderr)
        self.assertIn("caffeinate_notified=0", caffeinate.stdout)

    def test_inactivity_check_waits_thirty_minutes_and_respects_work_hours(self):
        self.write_events(
            [
                {
                    "event_id": "recent-usage",
                    "source_agent": "codex",
                    "source_path": "fixture",
                    "source_session_id": "recent-usage",
                    "occurred_at": "2026-05-06T10:45:00+09:00",
                    "project_path": "/work/project",
                    "input_tokens": 100,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 100,
                    "record_type": "turn",
                    "imported_at": "2026-05-06T10:45:00+09:00",
                }
            ]
        )
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)

        too_soon = subprocess.run(
            [
                sys.executable,
                "bin/agentboost",
                "--repo-root",
                str(self.repo),
                "--inactivity-check",
                "--no-system-notify",
                "--now",
                "2026-05-06T11:00:00+09:00",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        late_enough = subprocess.run(
            [
                sys.executable,
                "bin/agentboost",
                "--repo-root",
                str(self.repo),
                "--inactivity-check",
                "--no-system-notify",
                "--now",
                "2026-05-06T11:20:00+09:00",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        outside_work = subprocess.run(
            [
                sys.executable,
                "bin/agentboost",
                "--repo-root",
                str(self.repo),
                "--inactivity-check",
                "--no-system-notify",
                "--now",
                "2026-05-06T21:00:00+09:00",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(too_soon.returncode, 0, too_soon.stderr)
        self.assertIn("inactivity_notified=0", too_soon.stdout)
        self.assertEqual(late_enough.returncode, 0, late_enough.stderr)
        self.assertIn("inactivity_notified=1", late_enough.stdout)
        self.assertEqual(outside_work.returncode, 0, outside_work.stderr)
        self.assertIn("inactivity_notified=0", outside_work.stdout)

        caffeinate = subprocess.run(
            [
                sys.executable,
                "bin/agentboost",
                "--repo-root",
                str(self.repo),
                "--caffeinate-check",
                "--no-system-notify",
                "--now",
                "2026-05-06T10:45:30+09:00",
            ],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(caffeinate.returncode, 0, caffeinate.stderr)
        self.assertIn("caffeinate_notified=1", caffeinate.stdout)

    def test_caffeinate_setting_disables_caffeinate_behavior(self):
        self.write_events(
            [
                {
                    "event_id": "recent-usage",
                    "source_agent": "codex",
                    "source_path": "fixture",
                    "source_session_id": "recent-usage",
                    "occurred_at": "2026-05-06T10:45:30+09:00",
                    "project_path": "/work/project",
                    "input_tokens": 100,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 100,
                    "record_type": "turn",
                    "imported_at": "2026-05-06T10:45:30+09:00",
                }
            ]
        )
        settings_file = self.repo / "data" / "ai-usage" / "settings.json"
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(
            json.dumps(
                {
                    "notifications": {"enabled": True, "categories": {"caffeinate": True}},
                    "caffeinate": {"enabled": False},
                }
            ),
            encoding="utf-8",
        )

        settings = load_settings(settings_file)
        notified = notify_caffeinate(
            self.repo,
            self.notifications_file,
            settings_file=settings_file,
            sender=lambda title, message: None,
            now="2026-05-06T10:45:45+09:00",
        )

        self.assertFalse(settings["caffeinate"]["enabled"])
        self.assertTrue(settings["notifications"]["categories"]["caffeinate"])
        self.assertEqual(notified, 0)

    def test_default_cli_opens_menu_bar_app_not_debug_window(self):
        with mock.patch("agentboost.growth_sidebar.open_menu_bar_app", return_value=0) as app, mock.patch(
            "agentboost.growth_sidebar.launch_sidebar"
        ) as debug_window:
            rc = sidebar_main(["--repo-root", str(self.repo)], default_repo_root=self.repo)

        self.assertEqual(rc, 0)
        app.assert_called_once()
        self.assertEqual(app.call_args.args[0], self.repo.resolve())
        self.assertEqual(app.call_args.kwargs["app_path"], None)
        self.assertFalse(app.call_args.kwargs["rebuild"])
        debug_window.assert_not_called()

    def test_debug_window_requires_explicit_flag(self):
        with mock.patch("agentboost.growth_sidebar.open_menu_bar_app") as app, mock.patch(
            "agentboost.growth_sidebar.launch_sidebar", return_value=0
        ) as debug_window:
            rc = sidebar_main(
                ["--repo-root", str(self.repo), "--debug-window", "--no-notify"],
                default_repo_root=self.repo,
            )

        self.assertEqual(rc, 0)
        app.assert_not_called()
        debug_window.assert_called_once_with(self.repo.resolve(), now=None, no_notify=True)

    def test_prefers_appkit_on_macos_when_available(self):
        self.assertEqual(preferred_gui_backend("darwin", appkit_available=True, tk_available=True), "appkit")
        self.assertEqual(preferred_gui_backend("darwin", appkit_available=False, tk_available=True), "tk")
        self.assertEqual(preferred_gui_backend("linux", appkit_available=True, tk_available=True), "tk")
