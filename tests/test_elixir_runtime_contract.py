import shutil
import subprocess
import unittest
from pathlib import Path


class ElixirRuntimeContractTest(unittest.TestCase):
    def setUp(self):
        self.project = Path("elixir") / "agentboost"
        self.mix = self.project / "mix.exs"
        self.runtime = self.project / "lib" / "agentboost" / "runtime.ex"
        self.cli = self.project / "lib" / "agentboost" / "cli.ex"

    def test_mix_project_defines_agentboost_release_contract(self):
        text = self.mix.read_text(encoding="utf-8")

        self.assertIn('app: :agentboost', text)
        self.assertIn("releases:", text)
        self.assertIn("agentboost:", text)
        self.assertIn("include_executables_for: [:unix]", text)
        self.assertIn("Agentboost.CLI", text)

    def test_elixir_runtime_exposes_app_store_state_contract(self):
        source = self.runtime.read_text(encoding="utf-8")

        for required in [
            "agentboost_state_v1",
            "elixir_beam",
            '"app"',
            '"repo_root"',
            "events_count",
            "goals_count",
            "source_counts",
            "import_window",
            '"xp"',
            '"level"',
            "workforce_fitness_score",
            '"rollups"',
            "token_activity",
            "recent_token_activity",
            "status_views",
            "agentboost_daily_7d",
            "status_animation_activity",
            "network_activity",
            "memory_monitor",
            '"badges"',
            "representative_badge",
            "new_achievements",
            "daily_missions",
            "weekly_missions",
            '"streak"',
            "notification_file",
            "usage_refresh",
            "folder_access",
            "threshold_percent",
            "privacy_controls",
            "Refresh Usage",
            "Remove Folder Access",
            "Delete Local Usage Data",
            "Export Local Report",
        ]:
            self.assertIn(required, source)
        for forbidden in ["prompt_text", "completion_text", "transcript", "source_files"]:
            self.assertNotIn(forbidden, source)

    def test_elixir_cli_supports_state_json_and_check_modes(self):
        source = self.cli.read_text(encoding="utf-8")

        self.assertIn('--state-json', source)
        self.assertIn('--check', source)
        self.assertIn("Agentboost.Runtime.state", source)
        self.assertIn("Agentboost.JSON.encode!", source)

    @unittest.skipUnless(shutil.which("mix"), "mix is not installed")
    def test_mix_tests_pass_when_elixir_is_installed(self):
        result = subprocess.run(
            ["mix", "test"],
            cwd=self.project,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
