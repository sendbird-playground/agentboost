import json
import subprocess
import unittest
from pathlib import Path


BINARY = Path.home() / "Applications" / "AgentBoost.app" / "Contents" / "MacOS" / "AgentBoost"


@unittest.skipUnless(BINARY.exists(), f"AgentBoost binary not built at {BINARY}")
class BlastCollisionTest(unittest.TestCase):
    def test_collision_triggers_blast_separation_and_cull(self):
        result = subprocess.run(
            [str(BINARY), "--blast-test"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["blasts_at_impact"], 1, "collision should create exactly one blast")
        self.assertEqual(payload["rockets_visible_at_impact"], 0, "collided rockets should disappear immediately")
        self.assertEqual(payload["separation_applied_after_delay"], 1, "separation should fire >=0.5s after blast")
        self.assertEqual(payload["blasts_after_cull"], 0, "blast should cull after blastDuration (0.6s)")
        self.assertEqual(payload["rockets_visible_after_cull"], 0, "rockets should stay hidden during recovery")
        self.assertEqual(payload["rockets_visible_after_recovery"], 2, "rockets should return after recovery")
        self.assertAlmostEqual(payload["altitude_diff_before"], 0.0, places=4)
        self.assertAlmostEqual(payload["altitude_diff_after"], 0.18, places=2)

    def test_zero_minute_after_blast_does_not_retrigger_or_hide_rockets(self):
        result = subprocess.run(
            [str(BINARY), "--idle-blast-recovery-test"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)

        self.assertEqual(payload["blasts_at_impact"], 1)
        self.assertFalse(payload["repeated_blast_after_zero_recovery"])
        self.assertEqual(payload["blasts_after_idle_settle"], 0)
        self.assertEqual(payload["rockets_visible_after_idle_settle"], 2)
        self.assertEqual(payload["rocket_speeds_after_idle_settle"]["claude"], 0)
        self.assertEqual(payload["rocket_speeds_after_idle_settle"]["codex"], 0)
        self.assertEqual(payload["tokens_after_idle_settle"]["claude"], 0)
        self.assertEqual(payload["tokens_after_idle_settle"]["codex"], 0)

    def test_split_io_renders_four_rockets_one_per_agent_channel(self):
        result = subprocess.run(
            [str(BINARY), "--split-test"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["rocket_count"], 4)
        self.assertEqual(
            payload["rendered_agents"],
            ["claude:input", "claude:output", "codex:input", "codex:output"],
        )
        self.assertEqual(
            payload["rocket_keys"],
            ["claude:input", "claude:output", "codex:input", "codex:output"],
        )

    def test_max_altitude_reaches_world_top(self):
        result = subprocess.run(
            [str(BINARY), "--altitude-test"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertAlmostEqual(payload["altitude_target_fraction"], 1.0, places=4)
        self.assertAlmostEqual(payload["smoothed_altitude_fraction"], 1.0, places=4)
        self.assertAlmostEqual(
            payload["rocket_y"],
            payload["world_max_y"] - payload["rocket_center_top_margin"],
            places=4,
        )
        self.assertAlmostEqual(payload["rocket_visual_top_y"], payload["world_max_y"], places=4)

    def test_idle_altitude_reaches_world_bottom_corner(self):
        result = subprocess.run(
            [str(BINARY), "--idle-bottom-test"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertAlmostEqual(
            payload["rocket_y"],
            payload["world_min_y"] + payload["rocket_center_bottom_margin"],
            places=4,
        )
        self.assertAlmostEqual(payload["rocket_visual_bottom_y"], payload["world_min_y"], places=4)
        self.assertEqual(payload["tokens_last_1m"], 0)

    def test_idle_running_agent_rocket_starts_visible_and_stopped_when_peer_is_active(self):
        result = subprocess.run(
            [str(BINARY), "--idle-split-test"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        codex = payload["rockets"]["codex"]

        self.assertEqual(payload["rendered_agents"], ["claude", "codex"])
        self.assertEqual(payload["rocket_count"], 2)
        self.assertGreaterEqual(codex["x"], payload["visible_min_x"])
        self.assertEqual(codex["speed"], 0)
        self.assertEqual(codex["smoothed_speed"], 0)
        self.assertEqual(codex["x"], codex["before_x"])
        self.assertEqual(codex["tokens_last_1m"], 0)


if __name__ == "__main__":
    unittest.main()
