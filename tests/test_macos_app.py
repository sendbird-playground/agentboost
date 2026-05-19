import plistlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentboost.product_quality import product_quality_report, quality_exit_code
from agentboost.macos_app import (
    build_agentboost_app,
    default_agentboost_app_path,
    final_app_icon_bytes,
    placeholder_app_icon_bytes,
)


class MacOSAppBundleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        source = self.repo / "macos" / "agentboost" / "AgentBoostApp.swift"
        source.parent.mkdir(parents=True)
        source.write_text(
            'import Cocoa\nlet helper = repoRoot() + "/bin/agentboost"\nlet key = "AgentBoostRepoRoot"\n',
            encoding="utf-8",
        )
        self.app_path = self.root / "AgentBoost.app"

    def tearDown(self):
        self.tmp.cleanup()

    def _beam_release_fixture(self) -> Path:
        release = self.root / "rel" / "agentboost"
        entrypoint = release / "bin" / "agentboost"
        entrypoint.parent.mkdir(parents=True, exist_ok=True)
        entrypoint.write_text(self._beam_entrypoint_script(self._beam_state_json()), encoding="utf-8")
        entrypoint.chmod(0o755)
        return release

    def _beam_state_json(self) -> str:
        return (
            '{"app":"AgentBoost","repo_root":".","events_count":0,"goals_count":0,'
            '"source_counts":{},"import_window":"No local usage events","xp":0,'
            '"level":1,"level_label":"Lv 1",'
            '"level_progress":{"current_level":1,"level_label":"Lv 1","current_level_xp":0,'
            '"current_level_required_xp":15,"xp_to_next_level":15,"progress_percent":0,'
            '"next_level":2,"max_level":50,"total_xp":0},'
            '"xp_breakdown":{"base_xp":0,"mission_xp":0},"workforce_fitness_score":0,'
            '"rollups":{"Today":{"total_tokens":0,"by_agent":{}},'
            '"This Week":{"total_tokens":0,"by_agent":{}},'
            '"This Month":{"total_tokens":0,"by_agent":{}},'
            '"Lifetime":{"total_tokens":0,"by_agent":{}}},'
            '"token_activity":{"today_tokens":0,"active_agents":[],"rocket_count":1},'
            '"recent_token_activity":{"last_1m_tokens":0,"active_agents":[],"rocket_count":1},'
            '"status_views":[],"agentboost_daily_7d":[],"status_animation_activity":{"active_agents":[],"rocket_count":1},'
            '"network_activity":{"speed_source":"network"},'
            '"memory_monitor":{"threshold_percent":80},"badges":[],"badge_inventory":[],'
            '"earned_badges":[],'
            '"representative_badge":null,"representative_badges":[],"meta_review":{"status":"ok"},'
            '"new_achievements":[],"daily_missions":[],"weekly_missions":[],'
            '"streak":{"status":"local"},"notification_file":"",'
            '"usage_refresh":{},"folder_access":{"agentboost":false,"claude":false,"codex":false},'
            '"privacy_controls":[],"contract":"agentboost_state_v1","runtime":"elixir_beam"}'
        )

    def _beam_entrypoint_script(self, state_json: str) -> str:
        return "#!/bin/sh\ncat <<'JSON'\n" + state_json + "\nJSON\n"

    def _build_portable_profile_with_beam(self) -> None:
        build_agentboost_app(
            self.repo,
            self.app_path,
            compile_app=False,
            portable_profile=True,
            beam_release_path=self._beam_release_fixture(),
            beam_elixir_version="1.19.5",
            beam_otp_release="28",
        )

    def test_builds_finder_launchable_bundle_layout_without_compiling(self):
        result = build_agentboost_app(self.repo, self.app_path, compile_app=False)

        executable = self.app_path / "Contents" / "MacOS" / "AgentBoost"
        plist = self.app_path / "Contents" / "Info.plist"
        source = self.app_path / "Contents" / "Resources" / "AgentBoostApp.swift"
        privacy = self.app_path / "Contents" / "Resources" / "PrivacyInfo.xcprivacy"
        entitlements = self.app_path / "Contents" / "Resources" / "AgentBoost.entitlements"
        icon = self.app_path / "Contents" / "Resources" / "AppIcon.icns"

        self.assertEqual(result.app_path, self.app_path)
        self.assertTrue(executable.exists())
        self.assertTrue(source.exists())
        self.assertTrue(plist.exists())
        self.assertTrue(privacy.exists())
        self.assertTrue(entitlements.exists())
        self.assertTrue(icon.exists())
        info = plistlib.loads(plist.read_bytes())
        self.assertEqual(info["CFBundleName"], "AgentBoost")
        self.assertEqual(info["CFBundlePackageType"], "APPL")
        self.assertEqual(info["CFBundleExecutable"], "AgentBoost")
        self.assertEqual(info["CFBundleIconFile"], "AppIcon")
        self.assertEqual(info["AgentBoostRepoRoot"], str(self.repo.resolve()))
        self.assertTrue(info["LSUIElement"])
        manifest = plistlib.loads(privacy.read_bytes())
        self.assertFalse(manifest["NSPrivacyTracking"])
        self.assertIsInstance(manifest["NSPrivacyCollectedDataTypes"], list)
        entitlements_payload = plistlib.loads(entitlements.read_bytes())
        # Default ad-hoc/local builds are unsandboxed so the dev fallback can read
        # ~/.claude and ~/.codex without per-folder grants. Sandbox is only enabled
        # for --portable-profile builds.
        self.assertNotIn("com.apple.security.app-sandbox", entitlements_payload)
        self.assertTrue(entitlements_payload["com.apple.security.files.user-selected.read-only"])

    def test_portable_profile_enables_sandbox_in_entitlements(self):
        build_agentboost_app(self.repo, self.app_path, compile_app=False, portable_profile=True)
        entitlements = self.app_path / "Contents" / "Resources" / "AgentBoost.entitlements"
        payload = plistlib.loads(entitlements.read_bytes())
        self.assertTrue(payload["com.apple.security.app-sandbox"])
        self.assertTrue(payload["com.apple.security.files.user-selected.read-only"])

    def test_compiled_native_app_uses_optimized_swift_build(self):
        with mock.patch("agentboost.macos_app.subprocess.run") as run:
            build_agentboost_app(self.repo, self.app_path, compile_app=True)

        command = run.call_args.args[0]
        self.assertIn("-O", command)
        self.assertLess(command.index("-O"), command.index("-o"))

    def test_portable_profile_omits_repo_root_metadata(self):
        build_agentboost_app(self.repo, self.app_path, compile_app=False, portable_profile=True)

        plist = self.app_path / "Contents" / "Info.plist"
        info = plistlib.loads(plist.read_bytes())
        self.assertNotIn("AgentBoostRepoRoot", info)
        self.assertEqual(info["CFBundleIdentifier"], "com.sendbirdplayground.agentboost")

    def test_portable_profile_passes_local_architecture_gates(self):
        source = self.repo / "macos" / "agentboost" / "AgentBoostApp.swift"
        source.write_text("import Cocoa\nfunc loadState() -> [String: Any] { [:] }\n", encoding="utf-8")
        build_agentboost_app(self.repo, self.app_path, compile_app=False, portable_profile=True)

        report = product_quality_report(self.repo, self.app_path)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["architecture.repo_root_metadata"]["status"], "pass")
        self.assertEqual(checks["architecture.external_helper"]["status"], "pass")
        self.assertNotIn("signing.distribution_signature", checks)

    def test_product_quality_report_requires_bundled_beam_runtime_for_elixir_candidate(self):
        build_agentboost_app(self.repo, self.app_path, compile_app=False, portable_profile=True)

        report = product_quality_report(self.repo, self.app_path)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertIn("runtime.beam_release", checks)
        self.assertEqual(checks["runtime.beam_release"]["status"], "fail")
        self.assertIn("Mix release", checks["runtime.beam_release"]["remediation"])
        self.assertIn("runtime.beam_host_bridge", checks)
        self.assertEqual(checks["runtime.beam_host_bridge"]["status"], "fail")
        self.assertIn("loadBeamRuntimeState", checks["runtime.beam_host_bridge"]["remediation"])

    def test_product_quality_report_accepts_bundled_beam_runtime_manifest_contract(self):
        build_agentboost_app(self.repo, self.app_path, compile_app=False, portable_profile=True)
        resources = self.app_path / "Contents" / "Resources"
        release_root = resources / "beam" / "agentboost"
        executable = release_root / "bin" / "agentboost"
        executable.parent.mkdir(parents=True)
        executable.write_text(self._beam_entrypoint_script(self._beam_state_json()), encoding="utf-8")
        executable.chmod(0o755)
        manifest = resources / "AgentBoostBeamRuntime.json"
        manifest.write_text(
            json.dumps(
                {
                    "runtime": "elixir_beam",
                    "mix_release": "agentboost",
                    "entrypoint": "beam/agentboost/bin/agentboost",
                    "elixir_version": "1.18.0",
                    "otp_release": "28",
                    "status_item_bridge": "native_appkit_host",
                    "state_contract": "agentboost_state_v1",
                    "sandbox": "app_sandbox_user_selected_read_only",
                    "repo_local_helper": False,
                    "prompt_content_ingestion": False,
                }
            ),
            encoding="utf-8",
        )

        report = product_quality_report(self.repo, self.app_path)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["runtime.beam_release"]["status"], "pass")
        self.assertIn(str(manifest), checks["runtime.beam_release"]["evidence"])
        self.assertIn(str(executable), checks["runtime.beam_release"]["evidence"])

    def test_product_quality_report_rejects_beam_runtime_without_state_parity_contract(self):
        build_agentboost_app(self.repo, self.app_path, compile_app=False, portable_profile=True)
        resources = self.app_path / "Contents" / "Resources"
        release_root = resources / "beam" / "agentboost"
        executable = release_root / "bin" / "agentboost"
        executable.parent.mkdir(parents=True)
        executable.write_text(
            "#!/bin/sh\n"
            "echo '{\"contract\":\"agentboost_state_v1\",\"runtime\":\"elixir_beam\"}'\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        manifest = resources / "AgentBoostBeamRuntime.json"
        manifest.write_text(
            json.dumps(
                {
                    "runtime": "elixir_beam",
                    "mix_release": "agentboost",
                    "entrypoint": "beam/agentboost/bin/agentboost",
                    "elixir_version": "1.18.0",
                    "otp_release": "28",
                    "status_item_bridge": "native_appkit_host",
                    "state_contract": "agentboost_state_v1",
                    "sandbox": "app_sandbox_user_selected_read_only",
                    "repo_local_helper": False,
                    "prompt_content_ingestion": False,
                }
            ),
            encoding="utf-8",
        )

        report = product_quality_report(self.repo, self.app_path)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["runtime.beam_release"]["status"], "fail")
        self.assertIn("state parity", checks["runtime.beam_release"]["message"])

    def test_current_swift_source_bridges_status_state_to_bundled_beam_runtime(self):
        source = self.repo / "macos" / "agentboost" / "AgentBoostApp.swift"
        source.write_text(
            (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        build_agentboost_app(
            self.repo,
            self.app_path,
            compile_app=False,
            portable_profile=True,
            beam_release_path=self._beam_release_fixture(),
            beam_elixir_version="1.19.5",
            beam_otp_release="28",
        )

        report = product_quality_report(self.repo, self.app_path)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["runtime.beam_host_bridge"]["status"], "pass")

    def test_ad_hoc_sign_uses_bundle_entitlements_for_local_sandbox_verification(self):
        with mock.patch("agentboost.macos_app.subprocess.run") as run:
            build_agentboost_app(self.repo, self.app_path, compile_app=False, ad_hoc_sign=True)

        entitlements = self.app_path / "Contents" / "Resources" / "AgentBoost.entitlements"
        run.assert_called_once_with(
            ["codesign", "--force", "--sign", "-", "--entitlements", str(entitlements), str(self.app_path)],
            text=True,
            check=True,
        )

    def test_build_app_can_bundle_existing_beam_release_contract(self):
        release = self.root / "rel" / "agentboost"
        entrypoint = release / "bin" / "agentboost"
        entrypoint.parent.mkdir(parents=True)
        entrypoint.write_text(self._beam_entrypoint_script(self._beam_state_json()), encoding="utf-8")
        entrypoint.chmod(0o755)

        build_agentboost_app(
            self.repo,
            self.app_path,
            compile_app=False,
            portable_profile=True,
            beam_release_path=release,
            beam_elixir_version="1.18.0",
            beam_otp_release="28",
        )

        resources = self.app_path / "Contents" / "Resources"
        bundled_entrypoint = resources / "beam" / "agentboost" / "bin" / "agentboost"
        manifest = resources / "AgentBoostBeamRuntime.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertTrue(bundled_entrypoint.exists())
        self.assertEqual(payload["runtime"], "elixir_beam")
        self.assertEqual(payload["mix_release"], "agentboost")
        self.assertEqual(payload["entrypoint"], "beam/agentboost/bin/agentboost")
        self.assertEqual(payload["state_contract"], "agentboost_state_v1")

        report = product_quality_report(self.repo, self.app_path)
        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["runtime.beam_release"]["status"], "pass")

    def test_build_cli_supports_no_compile_mode(self):
        run = subprocess.run(
            [
                sys.executable,
                "bin/agentboost-build-app",
                "--repo-root",
                str(self.repo),
                "--app-path",
                str(self.app_path),
                "--no-compile",
            ],
            cwd=Path.cwd(),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn(str(self.app_path), run.stdout)
        self.assertTrue((self.app_path / "Contents" / "Info.plist").exists())

    def test_default_app_path_is_user_applications(self):
        self.assertEqual(default_agentboost_app_path(Path("/Users/example")), Path("/Users/example/Applications/AgentBoost.app"))

    def test_product_quality_report_reports_hard_blockers_and_passed_bundle_assets(self):
        build_agentboost_app(self.repo, self.app_path, compile_app=False, portable_profile=True)

        report = product_quality_report(self.repo, self.app_path)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["bundle.exists"]["status"], "pass")
        self.assertEqual(checks["metadata.required_keys"]["status"], "pass")
        self.assertEqual(checks["privacy.manifest"]["status"], "pass")
        self.assertEqual(checks["sandbox.entitlements_file"]["status"], "pass")
        self.assertEqual(checks["icon.bundle_icon"]["status"], "pass")
        self.assertEqual(checks["icon.final_art"]["status"], "pass")
        self.assertEqual(checks["architecture.external_helper"]["status"], "fail")
        self.assertNotIn("signing.distribution_signature", checks)
        self.assertNotIn("connect.credentials", checks)
        self.assertFalse(report["ready"])
        self.assertEqual(quality_exit_code(report), 1)

    def test_product_quality_report_requires_local_only_privacy_manifest(self):
        build_agentboost_app(self.repo, self.app_path, compile_app=False)
        manifest = self.app_path / "Contents" / "Resources" / "PrivacyInfo.xcprivacy"
        manifest.write_bytes(
            plistlib.dumps(
                {
                    "NSPrivacyAccessedAPITypes": [],
                    "NSPrivacyCollectedDataTypes": [{"NSPrivacyCollectedDataType": "NSPrivacyCollectedDataTypeOtherDataTypes"}],
                    "NSPrivacyTracking": True,
                    "NSPrivacyTrackingDomains": ["example.com"],
                }
            )
        )

        report = product_quality_report(self.repo, self.app_path)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["privacy.manifest"]["status"], "fail")
        self.assertIn("no collected data", checks["privacy.manifest"]["remediation"])

    def test_product_quality_report_requires_user_selected_file_entitlement(self):
        build_agentboost_app(self.repo, self.app_path, compile_app=False)
        entitlements = self.app_path / "Contents" / "Resources" / "AgentBoost.entitlements"
        entitlements.write_bytes(plistlib.dumps({"com.apple.security.app-sandbox": True}))

        report = product_quality_report(self.repo, self.app_path)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["sandbox.entitlements_file"]["status"], "fail")
        self.assertIn("user-selected read-only", checks["sandbox.entitlements_file"]["remediation"])

    def test_product_quality_report_rejects_temporary_sandbox_exception_entitlements(self):
        build_agentboost_app(self.repo, self.app_path, compile_app=False)
        entitlements = self.app_path / "Contents" / "Resources" / "AgentBoost.entitlements"
        entitlements.write_bytes(
            plistlib.dumps(
                {
                    "com.apple.security.app-sandbox": True,
                    "com.apple.security.files.user-selected.read-only": True,
                    "com.apple.security.temporary-exception.files.home-relative-path.read-only": ["Downloads"],
                }
            )
        )

        report = product_quality_report(self.repo, self.app_path)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["sandbox.entitlements_file"]["status"], "fail")
        self.assertIn("temporary sandbox exception", checks["sandbox.entitlements_file"]["remediation"])

    def test_product_quality_report_rejects_placeholder_icon(self):
        build_agentboost_app(self.repo, self.app_path, compile_app=False)
        icon = self.app_path / "Contents" / "Resources" / "AppIcon.icns"
        icon.write_bytes(placeholder_app_icon_bytes())

        report = product_quality_report(self.repo, self.app_path)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["icon.bundle_icon"]["status"], "pass")
        self.assertEqual(checks["icon.final_art"]["status"], "fail")

    def test_product_quality_icon_uses_final_multi_size_rocket_artwork(self):
        build_agentboost_app(self.repo, self.app_path, compile_app=False)
        icon = self.app_path / "Contents" / "Resources" / "AppIcon.icns"
        icon_bytes = icon.read_bytes()
        source = (Path.cwd() / "agentboost" / "macos_app.py").read_text(encoding="utf-8")

        self.assertEqual(icon_bytes, final_app_icon_bytes())
        self.assertNotEqual(icon_bytes, placeholder_app_icon_bytes())
        self.assertTrue(icon_bytes.startswith(b"icns"))
        for chunk in (b"icp4", b"icp5", b"ic07", b"ic08", b"ic09", b"ic10"):
            self.assertIn(chunk, icon_bytes)
        self.assertIn('AGENTBOOST_ICON_ROCKET_EMOJI = "🚀"', source)
        self.assertIn("rocket_emoji_reference", source)

    def test_product_quality_report_cli_json(self):
        build_agentboost_app(self.repo, self.app_path, compile_app=False)
        run = subprocess.run(
            [
                sys.executable,
                "bin/agentboost-quality-check",
                "--repo-root",
                str(self.repo),
                "--app-path",
                str(self.app_path),
                "--json",
            ],
            cwd=Path.cwd(),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(run.returncode, 1, run.stderr)
        self.assertIn('"ready": false', run.stdout)
        self.assertIn('"architecture.external_helper"', run.stdout)

    def test_product_quality_report_requires_privacy_menu_controls(self):
        source = self.repo / "macos" / "agentboost" / "AgentBoostApp.swift"
        source.write_text("import Cocoa\nfunc loadState() -> [String: Any] { [:] }\n", encoding="utf-8")
        build_agentboost_app(self.repo, self.app_path, compile_app=False, portable_profile=True)

        report = product_quality_report(self.repo, self.app_path)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["privacy.user_controls"]["status"], "fail")
        self.assertIn("Remove Folder Access", checks["privacy.user_controls"]["remediation"])

    def test_product_quality_report_requires_native_usage_refresh(self):
        source = self.repo / "macos" / "agentboost" / "AgentBoostApp.swift"
        source.write_text("import Cocoa\nfunc loadState() -> [String: Any] { [:] }\n", encoding="utf-8")
        build_agentboost_app(self.repo, self.app_path, compile_app=False, portable_profile=True)

        report = product_quality_report(self.repo, self.app_path)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["usage.native_refresh"]["status"], "fail")
        self.assertIn("Select Claude Usage Folder", checks["usage.native_refresh"]["remediation"])
        self.assertIn("Select Codex Sessions Folder", checks["usage.native_refresh"]["remediation"])

    def test_product_quality_report_requires_live_usage_refresh_before_status_animation(self):
        source = self.repo / "macos" / "agentboost" / "AgentBoostApp.swift"
        source.write_text(
            "\n".join(
                [
                    "import Cocoa",
                    "func activeDataRoot() -> URL { URL(fileURLWithPath: \"/tmp\") }",
                    "func buildState(dataRoot: URL) -> [String: Any] { [:] }",
                    "func loadState() -> [String: Any] { buildState(dataRoot: activeDataRoot()) }",
                    "func collectUsageFromSelectedAgentFolders(dataRoot: URL) throws -> [String: Any] { [:] }",
                    "func claudeUsageEvents() {}",
                    "func codexUsageEvents() {}",
                    "func appendUsageEvents() {}",
                    "let _refresh = NSMenuItem(title: \"Refresh Usage\", action: nil, keyEquivalent: \"\")",
                    "let _claude = NSMenuItem(title: \"Select Claude Usage Folder...\", action: nil, keyEquivalent: \"\")",
                    "let _codex = NSMenuItem(title: \"Select Codex Sessions Folder...\", action: nil, keyEquivalent: \"\")",
                ]
            ),
            encoding="utf-8",
        )
        build_agentboost_app(self.repo, self.app_path, compile_app=False, portable_profile=True)

        report = product_quality_report(self.repo, self.app_path)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertIn("usage.live_status_refresh", checks)
        self.assertEqual(checks["usage.live_status_refresh"]["status"], "fail")
        self.assertIn("refreshUsageIfPossible", checks["usage.live_status_refresh"]["remediation"])

    def test_product_quality_report_rejects_live_usage_refresh_after_building_state(self):
        source = self.repo / "macos" / "agentboost" / "AgentBoostApp.swift"
        source.write_text(
            "\n".join(
                [
                    "import Cocoa",
                    "func activeDataRoot() -> URL { URL(fileURLWithPath: \"/tmp\") }",
                    "func buildState(dataRoot: URL) -> [String: Any] { [:] }",
                    "func hasSelectedAgentUsageFolder() -> Bool { true }",
                    "func refreshUsageIfPossible(dataRoot: URL) {}",
                    "func loadState(refreshUsage: Bool = true) -> [String: Any] {",
                    "    let dataRoot = activeDataRoot()",
                    "    let state = buildState(dataRoot: dataRoot)",
                    "    refreshUsageIfPossible(dataRoot: dataRoot)",
                    "    return state",
                    "}",
                ]
            ),
            encoding="utf-8",
        )
        build_agentboost_app(self.repo, self.app_path, compile_app=False, portable_profile=True)

        report = product_quality_report(self.repo, self.app_path)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["usage.live_status_refresh"]["status"], "fail")
        self.assertIn("before buildState", checks["usage.live_status_refresh"]["remediation"])

    def test_product_quality_report_requires_elapsed_time_status_animation(self):
        source = self.repo / "macos" / "agentboost" / "AgentBoostApp.swift"
        source.write_text(
            "\n".join(
                [
                    "import Cocoa",
                    "final class RocketStatusView: NSView {",
                    "    private var backgroundProgress: CGFloat = 0",
                    "    private var motionTimer: Timer?",
                    "    @objc private func advanceRocket(_ timer: Timer) {",
                    "        backgroundProgress += 0.02",
                    "        needsDisplay = true",
                    "    }",
                    "}",
                ]
            ),
            encoding="utf-8",
        )
        build_agentboost_app(self.repo, self.app_path, compile_app=False, portable_profile=True)

        report = product_quality_report(self.repo, self.app_path)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertIn("animation.elapsed_background_motion", checks)
        self.assertEqual(checks["animation.elapsed_background_motion"]["status"], "fail")
        self.assertIn("elapsed wall-clock", checks["animation.elapsed_background_motion"]["remediation"])

    def test_product_quality_report_requires_two_agent_split_rocket_contract(self):
        source = self.repo / "macos" / "agentboost" / "AgentBoostApp.swift"
        source.write_text(
            "\n".join(
                [
                    "import Cocoa",
                    "final class RocketStatusView: NSView {",
                    "    private var animationStartedAt = Date()",
                    "    private var motionTimer: Timer?",
                    "    private func currentBackgroundProgress() -> CGFloat {",
                    "        Date().timeIntervalSince(animationStartedAt)",
                    "        return 0.truncatingRemainder(dividingBy: 1)",
                    "    }",
                    "    @objc private func advanceRocket(_ timer: Timer) { needsDisplay = true }",
                    "    override func draw(_ dirtyRect: NSRect) {",
                    "        let animationMidY = CGFloat(15)",
                    "        let tokenPoint = NSPoint(x: bounds.midX, y: animationMidY)",
                    "        let rocketPoint = NSPoint(x: bounds.midX, y: animationMidY)",
                    "        drawRocket(at: rocketPoint, scale: CGFloat(1.25))",
                    "        drawTokenText(text: currentTokenText(), below: tokenPoint)",
                    "    }",
                    "    private func currentTokenText() -> String { \"0\" }",
                    "    private func drawTokenText(text: String, below point: NSPoint) {",
                    "        let textX = min(max(bounds.minX + 2, point.x), bounds.maxX - 2)",
                    "        _ = textX",
                    "    }",
                    "    private func drawRocket(at point: NSPoint, scale: CGFloat) {}",
                    "    func configure(activity: [String: Any], statusViews: [[String: Any]]) {",
                    "        let timer = Timer(timeInterval: 0.1, target: self, selector: #selector(advanceRocket(_:)), userInfo: nil, repeats: true)",
                    "        RunLoop.main.add(timer, forMode: .common)",
                    "    }",
                    "}",
                ]
            ),
            encoding="utf-8",
        )
        build_agentboost_app(self.repo, self.app_path, compile_app=False, portable_profile=True)

        report = product_quality_report(self.repo, self.app_path)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["animation.elapsed_background_motion"]["status"], "fail")
        self.assertIn("two-agent split rocket", checks["animation.elapsed_background_motion"]["message"])

    def test_product_quality_report_requires_memory_monitor_contract(self):
        source = self.repo / "macos" / "agentboost" / "AgentBoostApp.swift"
        source.write_text("import Cocoa\nfunc buildState(dataRoot: URL) -> [String: Any] { [:] }\n", encoding="utf-8")
        build_agentboost_app(self.repo, self.app_path, compile_app=False, portable_profile=True)

        report = product_quality_report(self.repo, self.app_path)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertIn("memory.monitor", checks)
        self.assertEqual(checks["memory.monitor"]["status"], "fail")
        self.assertIn("80%", checks["memory.monitor"]["remediation"])

    def test_product_quality_report_requires_network_speed_animation_contract(self):
        source = self.repo / "macos" / "agentboost" / "AgentBoostApp.swift"
        source.write_text("import Cocoa\nfunc buildState(dataRoot: URL) -> [String: Any] { [:] }\n", encoding="utf-8")
        build_agentboost_app(self.repo, self.app_path, compile_app=False, portable_profile=True)

        report = product_quality_report(self.repo, self.app_path)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertIn("animation.network_speed", checks)
        self.assertEqual(checks["animation.network_speed"]["status"], "fail")
        self.assertIn("network traffic", checks["animation.network_speed"]["remediation"])

    def test_current_swift_source_passes_live_animation_readiness_contracts(self):
        source = self.repo / "macos" / "agentboost" / "AgentBoostApp.swift"
        source.write_text(
            (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        build_agentboost_app(self.repo, self.app_path, compile_app=False, portable_profile=True)

        report = product_quality_report(self.repo, self.app_path)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["usage.live_status_refresh"]["status"], "pass")
        self.assertEqual(checks["animation.elapsed_background_motion"]["status"], "pass")
        self.assertEqual(checks["animation.network_speed"]["status"], "pass")
        self.assertEqual(checks["memory.monitor"]["status"], "pass")

    def test_swift_app_keeps_menu_bar_surface_with_optional_overlay_only(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        self.assertIn("NSStatusBar.system.statusItem", source)
        self.assertIn("NSMenu", source)
        self.assertIn("setActivationPolicy(.accessory)", source)
        self.assertIn("token_activity", source)
        self.assertIn("recent_token_activity", source)
        self.assertIn("status_animation_activity", source)
        self.assertIn("AgentBoostBeamRuntime.json", source)
        self.assertIn("loadBeamRuntimeState(dataRoot:", source)
        self.assertIn("status_views", source)
        self.assertIn("display_tokens", source)
        self.assertIn("activity_level", source)
        self.assertIn("rocket_state", source)
        self.assertIn("rocket_altitude", source)
        self.assertIn("has_flame", source)
        self.assertIn("animation_interval_seconds", source)
        self.assertIn("RocketStatusView", source)
        self.assertIn("draw(_ dirtyRect", source)
        self.assertIn("backgroundProgress", source)
        self.assertIn("Badge Inventory", source)
        self.assertIn("setRepresentativeBadge", source)
        self.assertIn("representative_badge", source)
        self.assertIn("meta_review", source)
        self.assertIn("doMetaReview", source)
        self.assertIn("memory_monitor", source)
        self.assertIn("memoryMonitor()", source)
        self.assertIn("Memory Alert", source)
        self.assertIn("network_activity", source)
        self.assertIn("networkActivity()", source)
        self.assertIn("RunLoop.main.add(timer, forMode: .common)", source)
        self.assertIn("RocketScreensaverView", source)
        self.assertIn("NSPanel(", source)
        self.assertIn(".nonactivatingPanel", source)
        self.assertNotIn("animationFrames", source)
        self.assertNotIn("makeKeyAndOrderFront", source)

    def test_native_menu_does_not_render_import_window_in_general_ui(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        panel_body = source.split("final class AgentBoostMenuPanelView", 1)[1].split("final class AppDelegate", 1)[0]
        menu_body = source.split("private func menuForState", 1)[1].split("private func configureStatusAnimation", 1)[0]

        self.assertIn('"import_window": importWindow(events: events)', source)
        self.assertNotIn("systemImport", panel_body)
        self.assertNotIn("Import window:", menu_body)
        self.assertNotIn('state["import_window"]', panel_body)

    def test_native_footer_removes_nonfunctional_shortcut_indicators(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        footer_button_body = source.split("private final class AgentBoostFooterButton", 1)[1].split("final class AgentBoostMenuPanelView", 1)[0]
        footer_body = source.split("private func buildFooter", 1)[1].split("private func wrap", 1)[0]

        self.assertIn("AgentBoostFooterButton(title: label, symbol: symbol", footer_body)
        self.assertNotIn("kbd:", footer_body)
        self.assertNotIn("kbdLabel", footer_button_body)
        self.assertNotIn("⌘R", footer_body)
        self.assertNotIn("⌘E", footer_body)
        self.assertNotIn("⌘,", footer_body)
        self.assertNotIn("⌘Q", footer_body)

    def test_settings_footer_opens_native_settings_panel_not_legacy_menu(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        configure_body = source.split("private func configureMenuPanel", 1)[1].split("@objc private func handleStatusItemClick", 1)[0]

        self.assertIn("final class AgentBoostSettingsPanelView", source)
        self.assertIn("@objc private func showSettingsFromPanel", source)
        settings_body = source.split("@objc private func showSettingsFromPanel", 1)[1].split("@objc private func quitFromPanel", 1)[0]
        self.assertIn("panel.settingsAction = #selector(showSettingsFromPanel)", configure_body)
        self.assertNotIn("panel.settingsAction = #selector(showLegacyMenuFromPanel)", configure_body)
        self.assertNotIn("showLegacyMenuFromPanel", source)
        self.assertIn("showSettingsPopover()", settings_body)
        self.assertNotIn("showLegacyMenu", settings_body)
        self.assertIn("settingsPopover", source)
        self.assertIn("agentboostSettingsFile(dataRoot:", source)

    def test_settings_panel_exposes_and_persists_full_settings_contract(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        panel_body = source.split("final class AgentBoostSettingsPanelView", 1)[1].split("final class AppDelegate", 1)[0]
        helper_body = source.split("func defaultAgentBoostSettings", 1)[1].split("func missions", 1)[0]
        app_body = source.split("final class AppDelegate", 1)[1]

        self.assertIn('makeToggle(title: "Keep Mac awake during AI usage", key: "caffeinate.enabled")', panel_body)
        self.assertIn('makeTimeField(title: "Workday start", key: "work_hours.start")', panel_body)
        self.assertIn('makeTimeField(title: "Workday end", key: "work_hours.end")', panel_body)
        self.assertIn('makeTimeField(title: "Quiet start", key: "quiet_hours.start")', panel_body)
        self.assertIn('makeTimeField(title: "Quiet end", key: "quiet_hours.end")', panel_body)
        self.assertIn('makeToggle(title: "Show floating rocket overlay", key: "display.floating_overlay_enabled")', panel_body)
        self.assertIn("private var textFields: [String: NSTextField] = [:]", panel_body)
        self.assertIn("#selector(textFieldAction(_:))", panel_body)
        self.assertIn("commitTextFields()", panel_body)
        close_body = panel_body.split("@objc private func closeClicked", 1)[1]
        self.assertLess(close_body.index("commitTextFields()"), close_body.index("closeAction"))
        self.assertIn("updateTimeSettingAction = #selector(updateSettingsTextValue(_:))", app_body)
        self.assertIn('"caffeinate": [', helper_body)
        self.assertIn('if key == "caffeinate.enabled"', helper_body)
        self.assertIn('"floating_overlay_enabled": false', helper_body)
        self.assertIn('key == "display.floating_overlay_enabled"', helper_body)
        self.assertIn("func agentboostSettingsByTextSetting", helper_body)
        self.assertIn('if key == "work_hours.start"', helper_body)
        self.assertIn('key == "quiet_hours.start" || key == "quiet_hours.end"', helper_body)

    def test_settings_panel_lists_earned_badges_and_persists_representative_badge(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        panel_body = source.split("final class AgentBoostSettingsPanelView", 1)[1].split("final class AppDelegate", 1)[0]
        app_body = source.split("final class AppDelegate", 1)[1]

        self.assertIn("private let representativeBadgePopup = NSPopUpButton()", panel_body)
        self.assertIn("var selectBadgeAction: Selector?", panel_body)
        self.assertIn('makeSectionLabel("Achievements")', panel_body)
        self.assertIn("makeBadgeSelector()", panel_body)
        self.assertIn("func update(settings: [String: Any], settingsPath: String, state: [String: Any])", panel_body)
        self.assertIn('let earned = state["earned_badges"] as? [[String: Any]]', panel_body)
        self.assertIn("badgeSelectionChanged", panel_body)
        self.assertIn('"badge_id": badgeId', panel_body)
        self.assertIn("panel.selectBadgeAction = #selector(selectRepresentativeBadgeFromSettings(_:))", app_body)
        self.assertIn("selectRepresentativeBadgeFromSettings", app_body)
        self.assertIn("writeRepresentativeBadgeSelection", source)
        self.assertIn('"representative_badge_id"', source)
        self.assertIn("stateBySelectingRepresentativeBadge", source)

    def test_native_caffeinate_respects_settings_toggle(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        caffeine_body = source.split("private func updateCaffeineAssertion", 1)[1].split("private func acquireCaffeineAssertion", 1)[0]

        self.assertIn("func caffeinateEnabled(_ settings:", source)
        self.assertIn("let settings = loadAgentBoostSettings(dataRoot: activeDataRoot())", caffeine_body)
        self.assertIn("!caffeinateEnabled(settings)", caffeine_body)
        self.assertLess(caffeine_body.index("releaseCaffeineAssertion()"), caffeine_body.index("let recent = state"))

    def test_native_caffeinate_stays_awake_for_running_agents_without_recent_tokens(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        caffeine_body = source.split("private func updateCaffeineAssertion", 1)[1].split("private func acquireCaffeineAssertion", 1)[0]

        self.assertIn("private func caffeineActivityIsActive(_ state: [String: Any]) -> Bool", source)
        self.assertIn('state["running_agent_activity"] as? [String: Any] ?? [:]', source)
        self.assertIn('running["active_agents"] as? [Any] ?? []', source)
        self.assertIn("lastMinuteTokens > 0 || !runningAgents.isEmpty", source)
        self.assertIn("caffeineActivityIsActive(state)", caffeine_body)
        self.assertLess(caffeine_body.index("caffeineActivityIsActive(state)"), caffeine_body.index("if let lastActive"))

    def test_native_caffeinate_prevents_system_and_display_idle_sleep(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        acquire_body = source.split("private func acquireCaffeineAssertion", 1)[1].split("private func releaseCaffeineAssertion", 1)[0]
        release_body = source.split("private func releaseCaffeineAssertion", 1)[1].split("func applicationWillTerminate", 1)[0]

        self.assertIn("private var caffeineSystemAssertionID: IOPMAssertionID = 0", source)
        self.assertIn("private var caffeineDisplayAssertionID: IOPMAssertionID = 0", source)
        self.assertIn("kIOPMAssertionTypePreventUserIdleSystemSleep", acquire_body)
        self.assertIn("kIOPMAssertionTypePreventUserIdleDisplaySleep", acquire_body)
        self.assertIn("IOPMAssertionRelease(caffeineSystemAssertionID)", release_body)
        self.assertIn("IOPMAssertionRelease(caffeineDisplayAssertionID)", release_body)

    def test_native_menu_numbers_use_compact_human_readable_units(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        compact_body = source.split("func compactTokenCount", 1)[1].split("func tokenActivity", 1)[0]
        panel_update_body = source.split("func update(state: [String: Any])", 1)[1].split("private func configure(row:", 1)[0]
        menu_body = source.split("private func menuForState", 1)[1].split("private func configureStatusAnimation", 1)[0]

        self.assertIn("1_000_000_000", compact_body)
        self.assertIn("safeTokens * 10 + threshold / 2", compact_body)
        self.assertIn("tenths / 10", compact_body)
        self.assertIn("tenths % 10", compact_body)
        self.assertIn("value: agentboostFmt(todayTokens)", panel_update_body)
        self.assertIn("value: agentboostFmt(monthTokens)", panel_update_body)
        self.assertIn("value: agentboostFmt(lifetimeTokens)", panel_update_body)
        self.assertNotIn("agentboostFmtFull", panel_update_body)
        self.assertIn('Month \\(compactTokenCount(tokenInt(month["total_tokens"]))) tokens', menu_body)
        self.assertIn('Lifetime \\(compactTokenCount(tokenInt(lifetime["total_tokens"]))) tokens', menu_body)
        self.assertIn('Today \\(compactTokenCount(tokenInt(activity["today_tokens"]))) tokens', menu_body)
        self.assertNotIn('Month \\(intText(month["total_tokens"])) tokens', menu_body)

    def test_native_menu_can_toggle_vacant_region_floating_animation(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        menu_body = source.split("private func menuForState", 1)[1].split("private func configureStatusAnimation", 1)[0]

        self.assertIn('"rocket_screensaver": rocketScreensaverState(dataRoot: dataRoot)', source)
        self.assertIn('state["rocket_screensaver"]', menu_body)
        self.assertIn('"Start Floating Animation"', menu_body)
        self.assertIn('"Stop Floating Animation"', menu_body)
        self.assertNotIn('"Start Rocket Screen Saver"', menu_body)
        self.assertNotIn('"Stop Rocket Screen Saver"', menu_body)
        self.assertIn("toggleRocketScreensaver", source)
        self.assertIn("configureRocketScreensaver", source)
        self.assertIn("showRocketScreensaver", source)
        self.assertIn("hideRocketScreensaver", source)
        self.assertIn("rocketScreensaverWindows", source)
        self.assertIn("floatingOverlayEnabled(dataRoot:", source)
        self.assertIn("setFloatingOverlayEnabled(", source)

    def test_floating_overlay_setting_disables_native_overlay_windows(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        configure_body = source.split("private func configureRocketScreensaver", 1)[1].split("private func showRocketScreensaver", 1)[0]
        toggle_body = source.split("@objc private func toggleRocketScreensaver", 1)[1].split("@objc private func selectDataRoot", 1)[0]
        settings_toggle_body = source.split("@objc private func toggleSettingsValue", 1)[1].split("@objc private func updateSettingsTextValue", 1)[0]

        self.assertIn("floatingOverlayEnabled(dataRoot: dataRoot)", source)
        self.assertIn('"settings_key": "display.floating_overlay_enabled"', source)
        self.assertIn('key == "display.floating_overlay_enabled"', settings_toggle_body)
        self.assertIn("hideRocketScreensaver()", settings_toggle_body)
        self.assertIn('setFloatingOverlayEnabled(enabled, dataRoot: activeDataRoot())', toggle_body)
        self.assertIn("(screensaver[\"enabled\"] as? Bool) == true", configure_body)
        self.assertLess(configure_body.index("hideRocketScreensaver()"), configure_body.index("showRocketScreensaver"))

    def test_floating_overlay_setting_shows_overlay_before_background_refresh(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        settings_toggle_body = source.split("@objc private func toggleSettingsValue", 1)[1].split("@objc private func updateSettingsTextValue", 1)[0]
        self.assertIn("private func applyFloatingOverlaySettingImmediately", source)
        helper_body = source.split("private func applyFloatingOverlaySettingImmediately", 1)[1].split("@objc private func updateSettingsTextValue", 1)[0]

        self.assertIn('key == "display.floating_overlay_enabled"', settings_toggle_body)
        self.assertIn("applyFloatingOverlaySettingImmediately(enabled: enabled)", settings_toggle_body)
        self.assertLess(
            settings_toggle_body.index("applyFloatingOverlaySettingImmediately(enabled: enabled)"),
            settings_toggle_body.index("refreshStateInBackground(refreshUsage: false)"),
        )
        self.assertIn("let baseState = lastRenderedState.isEmpty ? loadLiveUsageState(refreshUsage: false) : lastRenderedState", helper_body)
        self.assertIn("hideRocketScreensaver()", helper_body)
        self.assertIn("applyState(baseState)", helper_body)

    def test_native_sidebar_allocates_space_between_review_actions_and_missions(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        panel_body = source.split("final class AgentBoostMenuPanelView", 1)[1].split("final class AgentBoostBadgeSelectorPanelView", 1)[0]
        app_body = source.split("final class AppDelegate", 1)[1]

        self.assertIn("private let agentboostMenuHeight = CGFloat(624)", source)
        self.assertIn("private let agentboostReviewSectionHeight = CGFloat(118)", source)
        self.assertIn("super.init(frame: NSRect(x: 0, y: 0, width: agentboostMenuWidth, height: agentboostMenuHeight))", panel_body)
        self.assertIn("popover.contentSize = NSSize(width: agentboostMenuWidth, height: agentboostMenuHeight)", app_body)
        self.assertIn("v.heightAnchor.constraint(greaterThanOrEqualToConstant: agentboostReviewSectionHeight)", panel_body)
        self.assertIn("sectionStack.setCustomSpacing(10, after: buildMetaView)", panel_body)
        self.assertIn("sectionStack.setCustomSpacing(8, after: buildMissionsView)", panel_body)

    def test_rocket_screensaver_overlay_is_non_activating_and_click_through(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        overlay_body = source.split("private func showRocketScreensaver", 1)[1].split("private func hideRocketScreensaver", 1)[0]

        self.assertIn("NSPanel(", overlay_body)
        self.assertIn("styleMask: [.borderless, .nonactivatingPanel]", overlay_body)
        self.assertIn("window.isOpaque = false", overlay_body)
        self.assertIn("window.backgroundColor = .clear", overlay_body)
        self.assertIn("window.ignoresMouseEvents = true", overlay_body)
        self.assertIn("window.hidesOnDeactivate = false", overlay_body)
        self.assertIn("window.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle, .fullScreenAuxiliary]", overlay_body)
        self.assertIn("window.level = .screenSaver", overlay_body)
        self.assertIn("window.isFloatingPanel = true", overlay_body)
        self.assertIn("window.canHide = false", overlay_body)
        self.assertIn("window.orderFrontRegardless()", overlay_body)
        self.assertNotIn("makeKeyAndOrderFront", overlay_body)

    def test_stopping_rocket_screensaver_closes_overlay_window_immediately(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        toggle_body = source.split("@objc private func toggleRocketScreensaver", 1)[1].split("@objc private func selectDataRoot", 1)[0]
        hide_body = source.split("private func hideRocketScreensaver", 1)[1].split("private func badgeInventoryItem", 1)[0]

        self.assertIn("try setFloatingOverlayEnabled(enabled, dataRoot: activeDataRoot())", toggle_body)
        self.assertIn("if !enabled {", toggle_body)
        self.assertIn("hideRocketScreensaver()", toggle_body)
        self.assertIn("let liveState = loadLiveUsageState(refreshUsage: false)", toggle_body)
        self.assertIn("let state = lastRenderedState.isEmpty ? liveState : stateByMergingLiveUsage(lastRenderedState, liveState: liveState)", toggle_body)
        self.assertIn("applyState(state)", toggle_body)
        self.assertLess(toggle_body.index("hideRocketScreensaver()"), toggle_body.index("loadLiveUsageState"))
        self.assertIn("rocketScreensaverViews.forEach { $0.stop() }", hide_body)
        self.assertIn("rocketScreensaverViews = []", hide_body)
        self.assertIn("rocketScreensaverWindows.forEach { window in", hide_body)
        self.assertIn("window.contentView = nil", hide_body)
        self.assertIn("window.orderOut(nil)", hide_body)
        self.assertIn("window.close()", hide_body)
        self.assertIn("rocketScreensaverWindows = []", hide_body)

    def test_rocket_screensaver_motion_is_continuous_without_wrap_teleports(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        view_body = source.split("final class RocketScreensaverView", 1)[1].split("final class AppDelegate", 1)[0]

        self.assertIn("var lastFrameAt: Date", view_body)
        self.assertIn("private(set) var agentRocketMotion: [String: AgentRocketMotion] = [:]", view_body)
        self.assertIn("private func advanceMotion(to now: Date)", view_body)
        self.assertIn("let maxFrameDelta = CGFloat(1.0 / 30.0)", view_body)
        self.assertIn("let deltaSeconds = min(maxFrameDelta, max(CGFloat(0), rawDelta))", view_body)
        self.assertIn("motion.position.x += pixelsPerSecond * deltaSeconds", view_body)
        self.assertIn("let offscreenMargin = CGFloat(72)", view_body)
        self.assertIn("motion.position.x = minX - offscreenMargin", view_body)
        self.assertNotIn("horizontalDirection = -1", view_body)
        self.assertNotIn("horizontalDirection = 1", view_body)
        self.assertIn("motion.position.y = min(max(motion.position.y, minY), maxY)", view_body)
        self.assertNotIn("truncatingRemainder(dividingBy: width)", view_body)
        self.assertNotIn("truncatingRemainder(dividingBy: height)", view_body)
        self.assertNotIn("let yProgress", view_body)

    def test_rocket_screensaver_moves_left_to_right_without_reflection(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        view_body = source.split("final class RocketScreensaverView", 1)[1].split("final class AppDelegate", 1)[0]

        self.assertIn("private func rocketIsVisible(worldPosition: NSPoint) -> Bool", view_body)
        self.assertIn("guard rocketIsVisible(worldPosition: rocketState.position) else", view_body)
        self.assertIn("rocketState.headingDegrees", view_body)
        self.assertNotIn("private var horizontalDirection", view_body)
        self.assertNotIn("directionAngle = horizontalDirection", view_body)
        self.assertNotIn("point.x - badgeSize.width - 28", view_body)

    def test_rocket_screensaver_heading_matches_smoothed_token_graph_slope(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        view_body = source.split("final class RocketScreensaverView", 1)[1].split("final class AppDelegate", 1)[0]
        motion_body = view_body.split("final class MotionState", 1)[1].split("private let motionState", 1)[0]
        draw_body = view_body.split("private func drawRocket", 1)[1].split("private func drawEmojiRocket", 1)[0]

        self.assertIn("private(set) var rocketHeadingDegrees = CGFloat(0)", motion_body)
        self.assertIn("let previousPosition = motion.position", motion_body)
        self.assertIn("updateRocketHeading(previousPosition: previousPosition, position: motion.position)", motion_body)
        self.assertIn("private func updateRocketHeading(previousPosition: NSPoint, position: NSPoint) -> CGFloat", motion_body)
        self.assertIn("let hasGraphMotion = abs(deltaX) > CGFloat(0.5) || abs(deltaY) > CGFloat(0.5)", motion_body)
        self.assertIn("guard hasGraphMotion else", motion_body)
        self.assertNotIn("guard deltaX > CGFloat(0.5) else", motion_body)
        self.assertIn("let headingDeltaX = max(CGFloat(0.5), deltaX)", motion_body)
        self.assertIn("let graphSlopeDegrees = atan2(deltaY, headingDeltaX) * CGFloat(180) / CGFloat.pi", motion_body)
        self.assertIn("let clampedSlopeDegrees = min(CGFloat(34), max(CGFloat(-34), graphSlopeDegrees))", motion_body)
        self.assertIn("return clampedSlopeDegrees - rocketEmojiBaselineDegrees", motion_body)
        self.assertIn("headingDegrees: CGFloat", draw_body)
        self.assertNotIn("let directionAngle = CGFloat(-10) + lift", draw_body)

    def test_rocket_screensaver_altitude_is_smoothed_as_continuous_graph(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        view_body = source.split("final class RocketScreensaverView", 1)[1].split("final class AppDelegate", 1)[0]

        self.assertIn("private var combinedTargetAltitudeFraction = CGFloat(0.45)", view_body)
        self.assertIn("var smoothedAltitudeFraction: CGFloat", view_body)
        self.assertIn("combinedTargetAltitudeFraction = altitudeTargetFraction(activity)", view_body)
        self.assertIn("private func altitudeTargetFraction(_ activity: [String: Any]) -> CGFloat", view_body)
        self.assertIn('if tokenInt(activity["last_1m_tokens"]) <= 0 {', view_body)
        self.assertIn("private func agentAltitudeTargetFraction(_ agent: String) -> CGFloat", view_body)
        self.assertIn("let tokens = tokenInt(agentUsageByAgent[agent]?[\"last_1m_tokens\"])", view_body)
        self.assertIn("return CGFloat(0)", view_body)
        self.assertLess(
            view_body.index('if tokenInt(activity["last_1m_tokens"]) <= 0 {'),
            view_body.index('let altitude = CGFloat((activity["rocket_altitude"]'),
        )
        self.assertIn("motion.smoothedAltitudeFraction += (targetAltitudeFraction - motion.smoothedAltitudeFraction) * altitudeEase", view_body)
        self.assertIn("let targetY = minY + motion.smoothedAltitudeFraction * (maxY - minY)", view_body)
        self.assertIn("motion.position.y += (targetY - motion.position.y) * positionEase", view_body)
        self.assertNotIn("pathSamples", view_body)
        self.assertNotIn("drawContinuationTrail", view_body)
        self.assertNotIn("drawMotionTrail", view_body)
        self.assertNotIn("path.line(to:", view_body)

    def test_rocket_screensaver_max_altitude_keeps_rocket_fully_visible_at_top(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        view_body = source.split("final class RocketScreensaverView", 1)[1].split("final class AppDelegate", 1)[0]
        motion_body = view_body.split("final class MotionState", 1)[1].split("private let motionState", 1)[0]

        self.assertIn("private let rocketCenterTopMargin = CGFloat(8)", motion_body)
        self.assertIn("private let maximumAltitudeFraction = CGFloat(1.0)", motion_body)
        self.assertIn("private let maximumUsageAltitude = CGFloat(250)", motion_body)
        self.assertIn("private func altitudeFraction(forVisualAltitude altitude: CGFloat) -> CGFloat", motion_body)
        self.assertIn("private func rocketCenterMaxY() -> CGFloat", motion_body)
        self.assertIn("worldFrame.maxY - rocketCenterTopMargin", motion_body)
        self.assertIn("let maxY = rocketCenterMaxY()", motion_body)
        self.assertIn("private let rocketCenterBottomMargin = CGFloat(8)", motion_body)
        self.assertIn("private func rocketCenterMinY() -> CGFloat", motion_body)
        self.assertIn("worldFrame.minY + rocketCenterBottomMargin", motion_body)
        self.assertIn("let minY = rocketCenterMinY()", motion_body)
        self.assertIn("motion.position.y = min(max(motion.position.y, minY), maxY)", motion_body)
        self.assertNotIn("let maxY = worldFrame.maxY - margin", motion_body)
        self.assertNotIn("let minY = worldFrame.minY + margin", motion_body)
        self.assertNotIn("let maxY = worldFrame.maxY\n", motion_body)
        self.assertNotIn("min(CGFloat(0.86)", motion_body)

    def test_rocket_animation_draws_plain_token_text_below_real_rocket_emoji(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        status_body = source.split("final class RocketStatusView", 1)[1].split("final class RocketScreensaverView", 1)[0]
        overlay_body = source.split("final class RocketScreensaverView", 1)[1].split("private final class AgentBoostSparklineView", 1)[0]

        for body in (status_body, overlay_body):
            self.assertIn('private var displayTokens = "0"', body)
            self.assertIn('displayTokens = text(activity["display_tokens"])', body)
            self.assertIn("drawTokenText(text:", body)
            self.assertIn("private func drawTokenText(text: String, below point: NSPoint", body)
            self.assertNotIn("drawTokenBadge", body)
            self.assertNotIn("roundedRect", body)
            self.assertNotIn("windowBackgroundColor.withAlphaComponent", body)
            self.assertIn('let rocketEmoji = "🚀"', body)
            self.assertIn("NSAffineTransform()", body)
            self.assertIn("transform.rotate(byDegrees:", body)
            self.assertIn("rocketEmoji.draw", body)

        status_rocket_body = status_body.split("private func drawRocket", 1)[1]
        overlay_rocket_body = overlay_body.split("private func drawRocket", 1)[1]
        self.assertNotIn("let body = NSBezierPath()", status_rocket_body)
        self.assertNotIn("let body = NSBezierPath()", overlay_rocket_body)
        self.assertNotIn("let flame = NSBezierPath()", status_rocket_body)
        self.assertNotIn("let flame = NSBezierPath()", overlay_rocket_body)
        self.assertIn("let textX = min(max(bounds.minX + 2", status_body)
        self.assertIn("let textX = point.x - textSize.width / 2", overlay_body)
        self.assertNotIn("let textX = min(max(bounds.minX + 2", overlay_body)

    def test_status_item_caches_rocket_and_token_glyph_images_for_cpu(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        status_body = source.split("final class RocketStatusView", 1)[1].split("final class RocketScreensaverView", 1)[0]
        emoji_body = status_body.split("private func drawEmojiRocket", 1)[1].split("private func cachedRocketImage", 1)[0]
        token_body = status_body.split("private func drawTokenText", 1)[1].split("private func cachedTokenTextImage", 1)[0]

        self.assertIn("private var rocketImageCache: [String: NSImage] = [:]", status_body)
        self.assertIn("private var tokenTextImageCache: [String: NSImage] = [:]", status_body)
        self.assertIn("private func cachedRocketImage(scale: CGFloat, tint: NSColor) -> NSImage", status_body)
        self.assertIn("private func cachedTokenTextImage(_ value: String) -> NSImage", status_body)
        self.assertIn("private func colorCacheKey(_ color: NSColor) -> String", status_body)
        self.assertIn("let image = cachedRocketImage(scale: scale, tint: tint)", emoji_body)
        self.assertIn("image.draw(", emoji_body)
        self.assertNotIn("rocketEmoji.draw(", emoji_body)
        self.assertIn("let image = cachedTokenTextImage(value)", token_body)
        self.assertIn("image.draw(", token_body)
        self.assertNotIn("tokenText.draw(", token_body)

    def test_rocket_screensaver_token_text_clips_with_rocket_at_window_edge(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        overlay_body = source.split("final class RocketScreensaverView", 1)[1].split("final class AppDelegate", 1)[0]
        token_body = overlay_body.split("private func drawTokenText", 1)[1].split("}", 1)[0]

        self.assertIn("let textX = point.x - textSize.width / 2", token_body)
        self.assertIn("NSBezierPath(rect: bounds).setClip()", token_body)
        self.assertIn("NSGraphicsContext.saveGraphicsState()", token_body)
        self.assertIn("NSGraphicsContext.restoreGraphicsState()", token_body)
        self.assertNotIn("bounds.maxX - textSize.width - 2", token_body)

    def test_rocket_animation_removes_background_tracks_and_connector_lines(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        status_body = source.split("final class RocketStatusView", 1)[1].split("final class RocketScreensaverView", 1)[0]
        overlay_body = source.split("final class RocketScreensaverView", 1)[1].split("final class AppDelegate", 1)[0]
        status_draw_body = status_body.split("override func draw", 1)[1].split("private func currentStatusView", 1)[0]
        overlay_draw_body = overlay_body.split("override func draw", 1)[1].split("private func ensureMotionState", 1)[0]

        self.assertNotIn("drawMovingBackground", status_body)
        self.assertNotIn("trail.line", status_body)
        self.assertNotIn("marker = NSBezierPath", status_body)
        self.assertNotIn("drawContinuationTrail", overlay_body)
        self.assertNotIn("drawMotionTrail", overlay_body)
        self.assertNotIn("trail.line", overlay_body)
        self.assertNotIn("curve(", overlay_body)
        self.assertIn("drawRocket(at:", status_draw_body)
        self.assertIn("drawTokenText(text: currentTokenText(), below:", status_draw_body)
        self.assertIn("let rocketStates = motionState.rocketDrawStates()", overlay_draw_body)
        self.assertIn("let localRocketPosition = localPoint(for: rocketState.position)", overlay_draw_body)
        self.assertIn("drawRocket(at: localRocketPosition, scale: 1.9, agent: rocketState.agent, headingDegrees: rocketState.headingDegrees, glow: rocketState.glowIntensity)", overlay_draw_body)
        self.assertIn("drawTokenText(text: motionState.currentTokenText(for: rocketState.agent), below: localRocketPosition)", overlay_draw_body)

    def test_rocket_screensaver_blast_visual_uses_smaller_crash_scale(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        overlay_body = source.split("final class RocketScreensaverView", 1)[1].split("final class AppDelegate", 1)[0]
        blast_body = overlay_body.split("private func drawBlast", 1)[1].split("private func localPoint", 1)[0]
        dirty_body = overlay_body.split("private func rocketDirtyRects", 1)[1].split("private func drawBlast", 1)[0]

        self.assertIn("private let blastVisualScale = CGFloat(0.8)", overlay_body)
        self.assertIn("let blastRadius = CGFloat(80) * blastVisualScale", dirty_body)
        self.assertIn("let baseFont = CGFloat(28) * blastVisualScale * scale", blast_body)

    def test_status_views_expose_requested_rocket_cycle_modes(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        status_views_body = source.split("func statusViews", 1)[1].split("func statusAnimationActivity", 1)[0]

        self.assertIn("minuteByAgent", status_views_body)
        self.assertIn('Set(activeMinuteAgents) == Set(["claude", "codex"])', status_views_body)
        self.assertIn('viewID: "\\(agent)_token_per_minute"', status_views_body)
        self.assertIn('viewID: "combined_token_per_minute"', status_views_body)
        self.assertIn('scope: "agent"', status_views_body)
        self.assertIn('scope: "combined"', status_views_body)
        self.assertLess(status_views_body.index('"claude"'), status_views_body.index('"codex"'))
        self.assertLess(status_views_body.index('viewID: "combined_token_per_minute"'), status_views_body.index('viewID: "combined_total_cumulative"'))
        self.assertIn('"view_id": "last_7d_cumulative"', status_views_body)
        self.assertIn('"label": "7d"', status_views_body)
        self.assertIn('"display_text": "7d \\(compactTokenCount(sevenDayTokens))"', status_views_body)
        self.assertIn('"view_id": "total_cumulative"', status_views_body)
        self.assertIn('"display_text": "Total \\(compactTokenCount(lifetime))"', status_views_body)
        self.assertIn('"view_id": "token_per_minute"', status_views_body)
        self.assertIn('"label": "Token/min"', status_views_body)
        self.assertIn('"display_text": "\\(compactTokenCount(lastMinuteTokens))/min"', status_views_body)
        self.assertLess(status_views_body.index('"last_7d_cumulative"'), status_views_body.index('"total_cumulative"'))
        self.assertLess(status_views_body.index('"total_cumulative"'), status_views_body.index('"token_per_minute"'))
        self.assertNotIn('"view_id": "daily_cumulative"', status_views_body)
        self.assertNotIn('"view_id": "last_5m_trend"', status_views_body)

    def test_status_item_identifies_agent_rockets_and_uses_full_cycle_text(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        view_body = source.split("final class RocketStatusView", 1)[1].split("final class RocketScreensaverView", 1)[0]
        draw_body = view_body.split("override func draw", 1)[1].split("private func currentStatusView", 1)[0]
        token_body = view_body.split("private func currentTokenText", 1)[1].split("private func fallbackStatusView", 1)[0]

        self.assertIn("agentColor(agent)", view_body)
        self.assertIn("NSColor.systemPurple", view_body)
        self.assertIn("NSColor.systemBlue", view_body)
        self.assertIn("let agent = index < activeAgents.count ? activeAgents[index] : \"\"", draw_body)
        self.assertIn("drawRocket(at: rocketPoint, scale: rocketScale, agent: agent)", draw_body)
        self.assertIn('let displayText = text(view["display_text"])', token_body)
        self.assertIn("if !displayText.isEmpty", token_body)

    def test_native_live_status_uses_recent_and_live_tail_without_full_ledger_scan(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        live_body = source.split("func loadLiveUsageState", 1)[1].split("func readRecentEvents", 1)[0]
        recent_body = source.split("func recentTokenActivity", 1)[1].split("func statusViews", 1)[0]

        self.assertIn("private let liveUsageRecentWindowSeconds: TimeInterval = 2 * 60", source)
        self.assertIn("let recentCutoff = now.addingTimeInterval(-liveUsageRecentWindowSeconds)", live_body)
        self.assertIn("let liveRecentEvents = liveRecentUsageEvents(since: recentCutoff", live_body)
        self.assertIn("let recentEvents = liveRecentEvents", live_body)
        self.assertNotIn("readRecentEvents(dataRoot: dataRoot, since: recentCutoff)", live_body)
        self.assertIn("let statusEvents = recentEvents", live_body)
        self.assertNotIn("readStatusEvents(dataRoot: dataRoot)", live_body)
        self.assertIn("let statusRollups = buildRollups(events: statusEvents)", live_body)
        self.assertIn("let todayActivity = tokenActivity(rollups: statusRollups)", live_body)
        self.assertIn('"rollups": statusRollups', live_body)
        self.assertIn('"token_activity": todayActivity', live_body)
        self.assertIn('"status_views": statusViews(events: statusEvents, rollups: statusRollups)', live_body)
        self.assertIn("statusAnimationActivity(recent: recentActivity, today: todayActivity", live_body)
        self.assertIn("var tokensByAgent = [\"claude\": 0, \"codex\": 0]", recent_body)
        self.assertIn('"agent_usage": agentUsageSummary(tokensByAgent)', recent_body)

    def test_status_animation_merges_running_agents_into_visible_rocket_contract(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        status_activity_body = source.split("func statusAnimationActivity", 1)[1].split("func applyNetworkAnimationSpeed", 1)[0]

        self.assertIn("running: [String: Any]", status_activity_body)
        self.assertIn("var visibleAgents = mergedActiveAgents(recent, running)", status_activity_body)
        self.assertIn('result["active_agents"] = visibleAgents', status_activity_body)
        self.assertIn('result["rocket_count"] = rocketCountForAgents(visibleAgents)', status_activity_body)
        self.assertIn('let runningAgents = textArray(running["active_agents"])', status_activity_body)
        self.assertIn('result["source"] = "running"', status_activity_body)
        self.assertIn("let mergedAgents = mergedActiveAgents(recent, today)", status_activity_body)
        self.assertIn("func mergedActiveAgents(_ recent: [String: Any], _ today: [String: Any]) -> [String]", source)

    def test_native_running_agent_refresh_adds_joining_rocket_without_ledger_cycle(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        live_body = source.split("func loadLiveUsageState", 1)[1].split("func liveRecentUsageEvents", 1)[0]
        app_body = source.split("final class AppDelegate", 1)[1]
        running_refresh_body = app_body.split("@objc private func refreshRunningAgentAnimationState", 1)[1].split("@objc private func refreshStatusState", 1)[0]

        self.assertIn("func runningAgentActivity() -> [String: Any]", source)
        self.assertIn("func runningProcessHints() -> [String]", source)
        self.assertIn("KERN_PROCARGS2", source)
        self.assertIn("func shouldInspectProcessCommandLine(_ command: String) -> Bool", source)
        self.assertIn("guard shouldInspectProcessCommandLine(command) else", source)
        self.assertIn("let runningActivity = cachedRunningAgentActivity()", live_body)
        self.assertIn('"running_agent_activity": runningActivity', live_body)
        self.assertIn(
            "statusAnimationActivity(recent: recentActivity, today: todayActivity, network: networkActivityState, running: runningActivity)",
            live_body,
        )
        self.assertIn("private var runningAgentRefreshTimer: Timer?", app_body)
        self.assertIn("private let runningAgentQueue = DispatchQueue", app_body)
        self.assertIn("private var runningAgentRefreshInFlight = false", app_body)
        self.assertIn("private var lastRenderedState: [String: Any] = [:]", app_body)
        self.assertIn("startRunningAgentRefreshTimer()", app_body)
        self.assertIn("private let runningAgentRefreshIntervalSeconds: TimeInterval = 15", source)
        self.assertIn("timeInterval: runningAgentRefreshIntervalSeconds", app_body)
        self.assertIn("runningAgentQueue.async", running_refresh_body)
        self.assertIn("guard !runningAgentRefreshInFlight", running_refresh_body)
        self.assertIn("private let runningAgentActivityCacheIntervalSeconds: TimeInterval = 60", source)
        self.assertIn("func cachedRunningAgentActivity() -> [String: Any]", source)
        self.assertIn("private var runningAgentActivityCache:", source)
        self.assertIn("let running = cachedRunningAgentActivity()", running_refresh_body)
        self.assertIn("stateByApplyingRunningAgents(lastRenderedState, running: running)", running_refresh_body)
        self.assertIn("applyAnimationState(renderedState)", running_refresh_body)
        self.assertNotIn("loadLiveUsageState", running_refresh_body)

    def test_running_agent_activity_uses_sysctl_hints_without_appkit_metadata_scan(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        running_body = source.split("func runningAgentActivity() -> [String: Any]", 1)[1].split("func runningProcessHints()", 1)[0]

        self.assertIn("let hints = runningProcessHints()", running_body)
        self.assertIn("for hint in hints", running_body)
        self.assertNotIn("NSWorkspace.shared.runningApplications", running_body)
        self.assertNotIn("application.localizedName", running_body)
        self.assertNotIn("application.bundleIdentifier", running_body)
        self.assertNotIn("application.bundleURL", running_body)
        self.assertNotIn("application.executableURL", running_body)

    def test_rocket_screensaver_rotates_token_mode_only_after_connected_display_cycle(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        app_body = source.split("private func configureRocketScreensaver", 1)[1].split("private func badgeInventoryItem", 1)[0]
        overlay_body = source.split("final class RocketScreensaverView", 1)[1].split("final class AppDelegate", 1)[0]
        configure_body = overlay_body.split("func configure", 1)[1].split("func stop", 1)[0]
        advance_body = overlay_body.split("private func advanceMotion", 1)[1].split("private func rocketIsVisible", 1)[0]
        wrap_body = advance_body.split("if motion.position.x > maxX + offscreenMargin", 1)[1].split("}", 1)[0]

        self.assertIn('let statusViews = state["status_views"] as? [[String: Any]] ?? []', app_body)
        self.assertIn("showRocketScreensaver(activity: activity, statusViews: statusViews)", app_body)
        self.assertIn("private func showRocketScreensaver(activity: [String: Any], statusViews: [[String: Any]])", app_body)
        self.assertIn("rocketScreensaverMotionState.configure(activity: activity, statusViews: statusViews, worldFrame: targetFrame)", app_body)
        self.assertIn("view.configure(viewportFrame: viewportFrame)", app_body)
        self.assertIn("private var statusViews: [[String: Any]] = []", overlay_body)
        self.assertIn("private var statusViewIndex = 0", overlay_body)
        self.assertIn("func configure(activity: [String: Any], statusViews: [[String: Any]], worldFrame: NSRect)", overlay_body)
        self.assertIn("self.statusViews = normalizedStatusViews(statusViews, activity: activity)", configure_body)
        self.assertIn("if statusViewIndex >= self.statusViews.count", configure_body)
        self.assertIn("func currentTokenText() -> String", overlay_body)
        self.assertIn("drawTokenText(text: motionState.currentTokenText(for: rocketState.agent), below: localRocketPosition)", overlay_body)
        self.assertIn("private func advanceTokenModeAfterCycle()", overlay_body)
        self.assertIn("motion.position.x = minX - offscreenMargin", wrap_body)
        self.assertIn("advanceTokenModeAfterCycle()", wrap_body)
        self.assertLess(wrap_body.index("motion.position.x = minX - offscreenMargin"), wrap_body.index("advanceTokenModeAfterCycle()"))

    def test_rocket_screensaver_targets_connected_display_region(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")

        self.assertIn("func rocketScreensaverState(dataRoot: URL)", source)
        self.assertIn('"settings_key": "display.floating_overlay_enabled"', source)
        self.assertIn('"strategy": "connected_display_region"', source)
        self.assertIn("func connectedDisplayRegion()", source)
        self.assertIn("func connectedDisplayFrames()", source)
        self.assertIn("func largestConnectedScreenGroup", source)
        self.assertIn("func screenFramesTouchOrOverlap", source)
        self.assertIn("rocketScreensaverSeamTolerance", source)
        self.assertIn("state[\"display_count\"]", source)
        self.assertIn("state[\"connected_display_count\"]", source)
        self.assertIn("let targetFrame = connectedDisplayRegion()", source)
        self.assertNotIn("let targetFrame = largestVacantScreenRegion()", source)

    def test_rocket_screensaver_uses_per_display_panels_with_world_coordinates(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        app_body = source.split("private func showRocketScreensaver", 1)[1].split("private func hideRocketScreensaver", 1)[0]
        hide_body = source.split("private func hideRocketScreensaver", 1)[1].split("private func badgeInventoryItem", 1)[0]
        overlay_body = source.split("final class RocketScreensaverView", 1)[1].split("final class AppDelegate", 1)[0]

        self.assertIn("private var rocketScreensaverWindows: [NSPanel] = []", source)
        self.assertIn("private var rocketScreensaverViews: [RocketScreensaverView] = []", source)
        self.assertNotIn("private var rocketScreensaverWindow: NSPanel?", source)
        self.assertIn("func connectedDisplayOverlayFrames(targetFrame: NSRect) -> [NSRect]", source)
        self.assertIn("state[\"panel_count\"]", source)
        self.assertIn("let viewportFrames = connectedDisplayOverlayFrames(targetFrame: targetFrame)", app_body)
        self.assertIn("for (index, viewportFrame) in viewportFrames.enumerated()", app_body)
        self.assertIn("window.setFrame(viewportFrame, display: true)", app_body)
        self.assertIn("rocketScreensaverMotionState.configure(activity: activity, statusViews: statusViews, worldFrame: targetFrame)", app_body)
        self.assertIn("view.configure(viewportFrame: viewportFrame)", app_body)
        self.assertIn("closeExtraRocketScreensaverWindows(startingAt: viewportFrames.count)", app_body)
        self.assertIn("rocketScreensaverWindows.forEach", hide_body)
        self.assertIn("rocketScreensaverViews.forEach", hide_body)
        self.assertIn("private var worldFrame = NSRect.zero", overlay_body)
        self.assertIn("private var viewportFrame = NSRect.zero", overlay_body)
        self.assertIn("func configure(activity: [String: Any], statusViews: [[String: Any]], worldFrame: NSRect)", overlay_body)
        self.assertIn("func configure(viewportFrame: NSRect)", overlay_body)
        self.assertIn("viewportFrame.insetBy(dx: -CGFloat(40), dy: -CGFloat(40)).contains(worldPosition)", overlay_body)
        self.assertIn("let minX = worldFrame.minX + margin", overlay_body)
        self.assertIn("let maxX = worldFrame.maxX - margin", overlay_body)
        self.assertIn("private func localPoint(for worldPoint: NSPoint) -> NSPoint", overlay_body)
        self.assertIn("NSPoint(x: worldPoint.x - viewportFrame.minX, y: worldPoint.y - viewportFrame.minY)", overlay_body)

    def test_rocket_screensaver_panels_share_one_motion_state(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        app_body = source.split("final class AppDelegate", 1)[1]
        overlay_body = source.split("final class RocketScreensaverView", 1)[1].split("final class AppDelegate", 1)[0]

        self.assertIn("final class MotionState", overlay_body)
        self.assertIn("private let motionState: MotionState", overlay_body)
        self.assertIn("init(frame frameRect: NSRect, motionState: MotionState)", overlay_body)
        self.assertIn("motionState.rocketDrawStates()", overlay_body)
        self.assertIn("motionState.currentTokenText(for: rocketState.agent)", overlay_body)
        self.assertIn("private let rocketScreensaverMotionState = RocketScreensaverView.MotionState()", app_body)
        self.assertIn("private var rocketScreensaverDisplayTimer: Timer?", app_body)
        self.assertIn("rocketScreensaverMotionState.configure(activity: activity, statusViews: statusViews, worldFrame: targetFrame)", app_body)
        self.assertIn("RocketScreensaverView(frame: NSRect(origin: .zero, size: viewportFrame.size), motionState: rocketScreensaverMotionState)", app_body)
        self.assertIn("startRocketScreensaverDisplayTimer()", app_body)
        self.assertIn("let now = Date()", app_body)
        self.assertIn("rocketScreensaverMotionState.advance(to: now)", app_body)
        self.assertIn("rocketScreensaverViews.forEach { $0.invalidateMotionArea() }", app_body)
        self.assertIn("func invalidateMotionArea()", overlay_body)
        self.assertIn("setNeedsDisplay(rect)", overlay_body)
        self.assertIn("rocketScreensaverDisplayTimer?.invalidate()", app_body)
        self.assertNotIn("private var motionTimer: Timer?", overlay_body)

    def test_rocket_screensaver_preserves_window_privacy_while_crossing_displays(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")

        self.assertIn("func largestVacantRect(in area: CGRect, avoiding occupied: [CGRect]) -> CGRect?", source)
        self.assertIn("CGWindowListCopyWindowInfo", source)
        self.assertIn("kCGWindowBounds", source)
        self.assertIn("kCGWindowLayer", source)
        self.assertIn("kCGWindowOwnerPID", source)
        self.assertIn("getpid()", source)
        self.assertIn("occupiedWindowFrames()", source)
        self.assertIn("NSScreen.screens", source)
        self.assertIn("screen.deviceDescription[NSDeviceDescriptionKey(\"NSScreenNumber\")]", source)
        self.assertIn("quartzRectToAppKit", source)
        self.assertIn("appKitRectToQuartz", source)
        self.assertIn("rectState(", source)
        self.assertNotIn("kCGWindowName", source)
        self.assertNotIn("kCGWindowOwnerName", source)

    def test_status_item_rotates_usage_views_with_rocket_and_no_ai_title(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")

        self.assertIn("statusViews", source)
        self.assertIn("statusViewIndex", source)
        self.assertIn("currentStatusView", source)
        self.assertIn("currentTokenText", source)
        self.assertIn("advanceStatusViewIfNeeded", source)
        self.assertIn("drawTokenText(text: currentTokenText(), below:", source)
        self.assertIn('state["status_views"]', source)
        self.assertIn("private let rocketStatusItemWidth = NSStatusItem.squareLength", source)
        self.assertIn("private let rocketStatusItemHeight = NSStatusItem.squareLength", source)
        self.assertIn("NSSize(width: rocketStatusItemWidth, height: rocketStatusItemHeight)", source)
        self.assertIn("statusItem = NSStatusBar.system.statusItem(withLength: rocketStatusItemWidth)", source)
        self.assertIn("RocketStatusView(", source)
        self.assertIn("width: rocketStatusItemWidth, height: rocketStatusItemHeight", source)
        self.assertNotIn("private let rocketStatusItemWidth = CGFloat(92)", source)
        self.assertNotIn("private let rocketStatusItemWidth = CGFloat(112)", source)
        self.assertNotIn("private let rocketStatusItemWidth = CGFloat(136)", source)
        self.assertNotIn("width: 176, height: 22", source)
        self.assertNotIn('("AI" as NSString).draw', source)
        self.assertNotIn("recentDisplayTokens.draw", source)
        configure_body = source.split("func configure(activity:", 1)[1].split("@objc private func advanceRocket", 1)[0]
        self.assertIn("statusViews: [[String: Any]]", configure_body)
        self.assertIn("self.statusViews = statusViews", configure_body)
        self.assertIn("statusViewIndex = 0", configure_body)
        self.assertIn('text(activity["activity_level"])', configure_body)

    def test_status_item_places_statistics_below_compact_animation(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        status_body = source.split("final class RocketStatusView", 1)[1].split("final class RocketScreensaverView", 1)[0]
        draw_body = source.split("override func draw", 1)[1].split("private func currentStatusView", 1)[0]

        self.assertIn("let animationMidY", draw_body)
        self.assertIn("let tokenBaselineY", draw_body)
        self.assertIn("let rocketScale = CGFloat(0.86)", draw_body)
        self.assertIn("let rocketPoints = rocketCenterPoints(centerX: bounds.midX, centerY: animationMidY)", draw_body)
        self.assertIn("drawRocket(at: rocketPoint, scale: rocketScale, agent: agent)", draw_body)
        self.assertIn("drawTokenText(text: currentTokenText(), below: tokenPoint)", draw_body)
        self.assertLess(draw_body.index("let tokenBaselineY"), draw_body.index("drawTokenText(text: currentTokenText(), below: tokenPoint)"))
        self.assertIn("NSFont.monospacedDigitSystemFont(ofSize: 7.0", status_body)
        self.assertIn("let fontSize = CGFloat(13) * scale", status_body)
        self.assertIn("NSBezierPath(rect: bounds).setClip()", status_body)
        self.assertNotIn("drawMovingBackground", draw_body)
        self.assertNotIn("statusText.draw", draw_body)
        self.assertNotIn("drawTokenBadge", draw_body)
        self.assertIn('let rocketEmoji = "🚀"', source)
        self.assertNotIn("let body = NSBezierPath()", source.split("private func drawRocket", 1)[1])

    def test_status_item_splits_rocket_when_claude_and_codex_are_active(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        configure_body = source.split("func configure(activity:", 1)[1].split("@objc private func advanceRocket", 1)[0]
        draw_body = source.split("override func draw", 1)[1].split("private func currentStatusView", 1)[0]

        self.assertIn('activeAgents = textArray(activity["active_agents"])', configure_body)
        self.assertIn('let requestedRocketCount = max(1, min(2, tokenInt(activity["rocket_count"])))', configure_body)
        self.assertIn("rocketCount = activeAgents.count >= 2 ? 2 : requestedRocketCount", configure_body)
        self.assertIn("let rocketPoints = rocketCenterPoints(centerX: bounds.midX, centerY: animationMidY)", draw_body)
        self.assertIn("for (index, rocketPoint) in rocketPoints.enumerated()", draw_body)
        self.assertIn("drawRocket(at: rocketPoint, scale: rocketScale, agent: agent)", draw_body)
        self.assertIn("private func rocketCenterPoints", source)
        self.assertIn('let activeAgents = ["claude", "codex"].filter', source)
        self.assertIn('"rocket_count": activeAgents.count >= 2 ? 2 : 1', source)

    def test_floating_animation_draws_two_agent_rockets_when_both_agents_are_active(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        overlay_body = source.split("final class RocketScreensaverView", 1)[1].split("final class AppDelegate", 1)[0]
        motion_body = overlay_body.split("final class MotionState", 1)[1].split("private let motionState", 1)[0]
        configure_body = motion_body.split("func configure", 1)[1].split("func advance", 1)[0]
        draw_body = overlay_body.split("override func draw", 1)[1].split("private func rocketIsVisible", 1)[0]

        self.assertIn("private(set) var activeAgents: [String] = []", motion_body)
        self.assertIn("private(set) var rocketCount = 1", motion_body)
        self.assertIn("struct AgentRocketMotion", motion_body)
        self.assertIn("private(set) var agentRocketMotion: [String: AgentRocketMotion] = [:]", motion_body)
        self.assertIn('private var agentUsageByAgent: [String: [String: Any]] = [:]', motion_body)
        self.assertIn('activeAgents = textArray(activity["active_agents"])', configure_body)
        self.assertIn('agentUsageByAgent = normalizedAgentUsage(activity["agent_usage"])', configure_body)
        self.assertIn('let requestedRocketCount = max(1, min(2, tokenInt(activity["rocket_count"])))', configure_body)
        self.assertIn("rocketCount = activeAgents.count >= 2 ? 2 : requestedRocketCount", configure_body)
        self.assertIn("let rocketStates = motionState.rocketDrawStates()", draw_body)
        self.assertIn("for rocketState in rocketStates", draw_body)
        self.assertIn("let localRocketPosition = localPoint(for: rocketState.position)", draw_body)
        self.assertIn("drawRocket(at: localRocketPosition, scale: 1.9, agent: rocketState.agent, headingDegrees: rocketState.headingDegrees, glow: rocketState.glowIntensity)", draw_body)
        self.assertIn("drawTokenText(text: motionState.currentTokenText(for: rocketState.agent), below: localRocketPosition)", draw_body)
        self.assertIn("private func advanceRocketMotion(for agent: String", motion_body)
        self.assertIn("let targetSpeed = agentRocketSpeed(agent)", motion_body)
        self.assertIn("let pixelsPerSecond = motion.smoothedSpeed <= 0 ? CGFloat(0)", motion_body)
        self.assertIn("(CGFloat(52) + motion.smoothedSpeed * CGFloat(34)) * activeFactor", motion_body)
        self.assertIn("motion.position.x += pixelsPerSecond * deltaSeconds", motion_body)
        self.assertIn("private func agentRocketSpeed(_ agent: String) -> CGFloat", motion_body)
        self.assertIn("CGFloat(usageAnimationSpeed(tokens))", motion_body)
        self.assertIn("peakTokensAmongRenderedRockets()", motion_body)

    def test_zero_minute_agent_stays_stationary_when_peer_is_active(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        motion_body = source.split("final class MotionState", 1)[1].split("private let motionState", 1)[0]
        speed_body = motion_body.split("private func agentRocketSpeed", 1)[1].split("private func peakTokensAmongRenderedRockets", 1)[0]
        advance_body = motion_body.split("private func advanceRocketMotion", 1)[1].split("private func updateRocketHeading", 1)[0]
        idle_self_test = source.split("func runIdleSplitRocketSelfTestAndExit", 1)[1].split("func runBlastSelfTestAndExit", 1)[0]

        self.assertIn('"codex": ["last_1m_tokens": 0', idle_self_test)
        self.assertIn("let beforeSnapshot = motion.runtimeSnapshot()", idle_self_test)
        self.assertIn('"before_x": beforePosition["x"] ?? 0', idle_self_test)
        self.assertIn("if agentCurrentTokens(agent) <= 0", advance_body)
        self.assertIn("motion.smoothedSpeed = 0", advance_body)
        self.assertIn("if tokens <= 0 { return CGFloat(0) }", speed_body)
        self.assertNotIn("agentIsActiveIdle(agent) && rocketSpeed > 0", speed_body)
        self.assertNotIn("rocketSpeed * CGFloat(0.35)", speed_body)

    def test_status_item_zero_minute_agent_uses_no_bob_or_rotation_when_peer_moves(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        status_body = source.split("final class RocketStatusView", 1)[1].split("final class RocketScreensaverView", 1)[0]
        configure_body = status_body.split("func configure(activity:", 1)[1].split("@objc private func advanceRocket", 1)[0]
        center_body = status_body.split("private func rocketCenterPoints", 1)[1].split("private func drawRocket", 1)[0]
        draw_rocket_body = status_body.split("private func drawRocket", 1)[1].split("private func agentColor", 1)[0]

        self.assertIn("private var agentUsageByAgent: [String: [String: Any]] = [:]", status_body)
        self.assertIn('agentUsageByAgent = normalizedAgentUsage(activity["agent_usage"])', configure_body)
        self.assertIn("let firstBob = rocketBob(for: firstAgent, altitude: altitude)", center_body)
        self.assertIn("let secondBob = rocketBob(for: secondAgent, altitude: altitude)", center_body)
        self.assertIn("private func rocketBob(for agent: String, altitude: CGFloat) -> CGFloat", status_body)
        self.assertIn("guard agentRocketSpeed(agent) > 0 else { return 0 }", status_body)
        self.assertIn("private func agentRocketSpeed(_ agent: String) -> CGFloat", status_body)
        self.assertIn('let tokens = tokenInt(agentUsageByAgent[agent]?["last_1m_tokens"])', status_body)
        self.assertIn("return tokens <= 0 ? CGFloat(0) : rocketSpeed", status_body)
        self.assertIn("let wave = agentRocketSpeed(agent) > 0", draw_rocket_body)
        self.assertNotIn("let bob = rocketSpeed > 0 ?", center_body)
        self.assertNotIn("let wave = rocketSpeed > 0 ?", draw_rocket_body)

    def test_status_item_preserves_two_agent_split_when_today_activity_drives_animation(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        token_activity_body = source.split("func tokenActivity", 1)[1].split("func recentTokenActivity", 1)[0]
        status_activity_body = source.split("func statusAnimationActivity", 1)[1].split("func xp", 1)[0]

        self.assertIn('let activeAgents = activeAgentsFromRollup(rollups["Today"])', token_activity_body)
        self.assertIn('"active_agents": activeAgents', token_activity_body)
        self.assertIn('"rocket_count": activeAgents.count >= 2 ? 2 : 1', token_activity_body)
        self.assertIn("let activeAgents = mergedActiveAgents(today, running)", status_activity_body)
        self.assertIn('let rocketCount = activeAgents.isEmpty ? max(1, min(2, tokenInt(today["rocket_count"]))) : rocketCountForAgents(activeAgents)', status_activity_body)
        self.assertIn('"active_agents": activeAgents', status_activity_body)
        self.assertIn('"rocket_count": rocketCount', status_activity_body)

    def test_status_item_uses_animation_activity_not_idle_recent_activity(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        configure_body = source.split("private func configureStatusAnimation", 1)[1].split("private func badgeInventoryItem", 1)[0]

        self.assertIn('state["status_animation_activity"]', configure_body)
        self.assertIn('state["status_views"]', configure_body)
        self.assertNotIn('state["recent_token_activity"] as? [String: Any] ?? state["token_activity"]', configure_body)

    def test_status_animation_speed_uses_network_activity_without_replacing_token_state(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        build_state_body = source.split("func buildState", 1)[1].split("func readEvents", 1)[0]
        status_activity_body = source.split("func statusAnimationActivity", 1)[1].split("func xp", 1)[0]

        self.assertIn("let networkActivityState = networkActivity()", build_state_body)
        self.assertIn("let runningActivity = cachedRunningAgentActivity()", build_state_body)
        self.assertIn('"network_activity": networkActivityState', build_state_body)
        self.assertIn('"running_agent_activity": runningActivity', build_state_body)
        self.assertIn(
            '"status_animation_activity": statusAnimationActivity(recent: recentActivity, today: todayActivity, network: networkActivityState, running: runningActivity)',
            build_state_body,
        )
        self.assertIn("func networkActivity()", source)
        self.assertIn("func outboundNetworkBytes()", source)
        self.assertIn("getifaddrs", source)
        self.assertIn("if_data", source)
        self.assertIn('let usageSpeed = doubleValue(activity["rocket_speed"])', status_activity_body)
        self.assertIn('if usageSpeed <= 0 {', status_activity_body)
        self.assertIn('result["rocket_speed"] = 0.0', status_activity_body)
        self.assertIn('let networkSpeed = doubleValue(network["rocket_speed"])', status_activity_body)
        self.assertIn('if networkSpeed > usageSpeed {', status_activity_body)
        self.assertIn('result["rocket_speed"] = networkSpeed', status_activity_body)
        self.assertIn('result["speed_source"] = "network"', status_activity_body)
        self.assertIn('result["speed_source"] = "token_usage"', status_activity_body)
        self.assertIn('result["outbound_bytes_per_second"] = tokenInt(network["outbound_bytes_per_second"])', status_activity_body)
        self.assertIn("display_tokens", status_activity_body)
        self.assertNotIn("nettop", source)
        self.assertNotIn("tcpdump", source)
        self.assertNotIn("bin/agentboost-usage-collect", source)

    def test_floating_animation_idle_usage_stops_horizontal_motion_and_targets_bottom(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        overlay_body = source.split("final class RocketScreensaverView", 1)[1].split("final class AppDelegate", 1)[0]
        configure_body = overlay_body.split("func configure", 1)[1].split("func advance", 1)[0]
        advance_body = overlay_body.split("private func advanceMotion", 1)[1].split("private func updateRocketHeading", 1)[0]

        self.assertIn("rocketSpeed = max(CGFloat(0), CGFloat((activity[\"rocket_speed\"]", configure_body)
        self.assertIn("let targetSpeed = agentRocketSpeed(agent)", advance_body)
        self.assertIn("motion.smoothedSpeed += (targetSpeed - motion.smoothedSpeed) * speedEase", advance_body)
        self.assertIn("let pixelsPerSecond = motion.smoothedSpeed <= 0 ? CGFloat(0)", advance_body)
        self.assertIn("(CGFloat(52) + motion.smoothedSpeed * CGFloat(34)) * activeFactor", advance_body)
        self.assertIn("motion.position.x += pixelsPerSecond * deltaSeconds", advance_body)
        self.assertIn('if tokenInt(activity["last_1m_tokens"]) <= 0 {', overlay_body)
        self.assertIn("return CGFloat(0)", overlay_body)

    def test_floating_animation_idle_running_agent_starts_visible_when_peer_is_active(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        overlay_body = source.split("final class RocketScreensaverView", 1)[1].split("final class AppDelegate", 1)[0]
        ensure_body = overlay_body.split("private func ensureRocketMotion", 1)[1].split("private func syncPrimaryMotionState", 1)[0]
        speed_body = overlay_body.split("private func agentRocketSpeed", 1)[1].split("private func agentAltitudeTargetFraction", 1)[0]

        self.assertIn("let startX = initialRocketX", ensure_body)
        self.assertIn("private func initialRocketX", overlay_body)
        self.assertIn("agentIsActiveIdle(agent)", ensure_body)
        self.assertIn("return minX + stagger", overlay_body)
        self.assertIn("private func agentIsActiveIdle", overlay_body)
        self.assertIn("if tokens <= 0 { return CGFloat(0) }", speed_body)
        self.assertNotIn("if agentIsActiveIdle(agent) && rocketSpeed > 0", speed_body)
        self.assertNotIn("return max(CGFloat(0.18), rocketSpeed * CGFloat(0.35))", speed_body)

    def test_status_item_refreshes_activity_state_periodically(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")

        self.assertIn("private let minimumUsageRefreshIntervalSeconds: TimeInterval = 30", source)
        self.assertIn("private var stateRefreshTimer: Timer?", source)
        self.assertIn("private let stateQueue = DispatchQueue", source)
        self.assertIn("private var stateRefreshInFlight = false", source)
        self.assertIn("startStateRefreshTimer()", source)
        self.assertIn("@objc private func refreshStatusState", source)
        refresh_body = source.split("private func startStateRefreshTimer", 1)[1].split("@objc private func refreshStatusState", 1)[0]
        self.assertIn("stateRefreshTimer?.invalidate()", refresh_body)
        self.assertIn("timeInterval: minimumUsageRefreshIntervalSeconds", refresh_body)
        self.assertIn("selector: #selector(refreshStatusState(_:))", refresh_body)
        self.assertIn("timer.tolerance = 0.5", refresh_body)
        self.assertIn("RunLoop.main.add(timer, forMode: .common)", refresh_body)
        self.assertIn("stateRefreshTimer = timer", refresh_body)
        self.assertNotIn("timeInterval: 10", refresh_body)
        status_body = source.split("@objc private func refreshStatusState", 1)[1].split("@objc private func refreshMenu", 1)[0]
        self.assertIn("refreshStateInBackground(refreshUsage: true)", status_body)
        self.assertNotIn("let state = loadState()", status_body)
        self.assertIn("stateQueue.async", source)
        self.assertIn("DispatchQueue.main.async", source)

    def test_menu_open_path_uses_fast_cached_state_not_usage_import(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        launch_body = source.split("func applicationDidFinishLaunching", 1)[1].split("private func startStateRefreshTimer", 1)[0]
        menu_body = source.split("@objc private func refreshMenu", 1)[1].split("private func configureStatusAnimation", 1)[0]
        refresh_body = source.split("private func refreshStateInBackground", 1)[1].split("private func loadAndCacheFullDisplayState", 1)[0]
        full_refresh_body = source.split("private func loadAndCacheFullDisplayState", 1)[1].split("private func applyState", 1)[0]
        load_state_body = source.split("func loadState(refreshUsage: Bool = true)", 1)[1].split("func activeDataRoot", 1)[0]
        display_state_body = source.split("func loadDisplayState(refreshUsage: Bool = true)", 1)[1].split("func stateByMergingLiveUsage", 1)[0]
        merge_body = source.split("func stateByMergingLiveUsage", 1)[1].split("func activeDataRoot", 1)[0]

        self.assertIn("let state = initialDisplayState(dataRoot: activeDataRoot())", launch_body)
        self.assertNotIn("let state = loadLiveUsageState(refreshUsage: false)", launch_body)
        self.assertNotIn("loadDisplayState", launch_body)
        self.assertIn("applyState(state)", launch_body)
        self.assertIn("refreshStateInBackground(refreshUsage: true)", launch_body)
        self.assertIn("applyState(lastRenderedState)", menu_body)
        self.assertIn("refreshStateInBackground(refreshUsage: false)", menu_body)
        self.assertNotIn("let state = loadState()", menu_body)
        self.assertIn("let liveState = loadLiveUsageState(refreshUsage: false)", refresh_body)
        self.assertIn("let fastState = cachedState.isEmpty ? liveState : stateByMergingLiveUsage(cachedState, liveState: liveState)", refresh_body)
        self.assertIn("self.applyState(fastState)", refresh_body)
        self.assertNotIn("refreshUsageIfPossible(dataRoot: dataRoot)", refresh_body)
        self.assertNotIn("collectUsageFromSelectedAgentFolders(dataRoot: dataRoot, since:", refresh_body)
        self.assertIn("let refreshedLiveState = loadLiveUsageState(refreshUsage: false)", refresh_body)
        self.assertIn("let finalState = stateByMergingLiveUsage(fastState, liveState: refreshedLiveState)", refresh_body)
        self.assertNotIn("loadDisplayState(refreshUsage: false)", refresh_body)
        self.assertNotIn("writeCachedDisplayState", refresh_body)
        self.assertIn("let finalState = loadDisplayState(refreshUsage: false)", full_refresh_body)
        self.assertIn("writeCachedDisplayState(finalState, dataRoot: dataRoot)", full_refresh_body)
        self.assertIn("refreshUsageIfPossible(dataRoot: dataRoot)", load_state_body)
        self.assertIn("let fullState = loadState(refreshUsage: refreshUsage)", display_state_body)
        self.assertIn("let liveState = loadLiveUsageState(refreshUsage: false)", display_state_body)
        self.assertIn('"rollups"', merge_body)
        self.assertIn('"recent_token_activity"', merge_body)
        self.assertIn("statusAnimationActivity(", merge_body)
        self.assertIn("loadBeamRuntimeState(dataRoot: dataRoot)", load_state_body)
        self.assertIn("allowsDevelopmentSwiftRuntimeFallback()", load_state_body)
        self.assertIn("beamUnavailableState(dataRoot: dataRoot)", load_state_body)
        self.assertIn("minimumUsageRefreshIntervalSeconds", source)

    def test_native_panel_uses_agent_lifetime_usage_for_agent_split(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        update_body = source.split("func update(state: [String: Any])", 1)[1].split("private func configure(row:", 1)[0]
        agent_share_body = source.split("private func agentShare(state: [String: Any])", 1)[1].split("private func sevenDayBuckets", 1)[0]

        self.assertIn("let (claudeShare, codexShare) = agentShare(state: state)", update_body)
        self.assertIn('rollups["Lifetime"] as? [String: Any]', agent_share_body)
        self.assertIn('lifetime["by_agent"] as? [String: Any]', agent_share_body)
        self.assertIn('tokenInt(byAgent["claude"])', agent_share_body)
        self.assertIn('tokenInt(byAgent["codex"])', agent_share_body)
        self.assertNotIn('rollups["Today"]', agent_share_body)
        self.assertNotIn('state["source_counts"]', agent_share_body)

    def test_native_app_loads_state_without_repo_helper_process(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        load_state_body = source.split("func loadState(refreshUsage: Bool = true)", 1)[1].split("func activeDataRoot", 1)[0]

        self.assertIn("refreshUsageIfPossible(dataRoot: dataRoot)", load_state_body)
        self.assertIn("loadBeamRuntimeState(dataRoot: dataRoot)", load_state_body)
        self.assertIn("if allowsDevelopmentSwiftRuntimeFallback()", load_state_body)
        self.assertIn("buildState(dataRoot: dataRoot)", load_state_body)
        self.assertIn("beamUnavailableState(dataRoot: dataRoot)", load_state_body)
        self.assertNotIn("?? buildState(dataRoot: dataRoot)", load_state_body)
        self.assertIn("securityScopedDataRoot()", source)
        self.assertIn("NSOpenPanel", source)
        self.assertNotIn('"/bin/agentboost"', source)
        self.assertIn("Process()", source)
        self.assertIn("beam/agentboost/bin/agentboost", source)
        self.assertIn("AgentBoostBeamRuntime.json", source)
        self.assertNotIn("runHelper", source)

    def test_native_beam_bridge_times_out_and_terminates_slow_state_process(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        beam_body = source.split("func loadBeamRuntimeState(dataRoot:", 1)[1].split("func elixirStringLiteral", 1)[0]

        self.assertIn("private let beamStateTimeoutSeconds", source)
        self.assertIn("let group = DispatchGroup()", beam_body)
        self.assertIn("process.terminationHandler", beam_body)
        self.assertIn("group.wait(timeout: .now() + beamStateTimeoutSeconds)", beam_body)
        self.assertIn("process.terminate()", beam_body)
        self.assertIn("kill(process.processIdentifier, SIGKILL)", beam_body)
        self.assertLess(
            beam_body.index("group.wait(timeout: .now() + beamStateTimeoutSeconds)"),
            beam_body.index("stdout.fileHandleForReading.readDataToEndOfFile()"),
        )

    def test_native_app_refreshes_usage_without_repo_helper_process(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")

        self.assertIn("claudeDataRootBookmarkKey", source)
        self.assertIn("codexDataRootBookmarkKey", source)
        self.assertIn('NSMenuItem(title: "Select Claude Usage Folder..."', source)
        self.assertIn('NSMenuItem(title: "Select Codex Sessions Folder..."', source)
        self.assertIn('NSMenuItem(title: "Refresh Usage"', source)
        self.assertIn("@objc private func refreshUsage", source)
        self.assertIn("collectUsageFromSelectedAgentFolders(dataRoot:", source)
        self.assertIn("func claudeUsageEvents", source)
        self.assertIn("func codexUsageEvents", source)
        self.assertIn("appendUsageEvents", source)
        self.assertIn("sidebar-usage-refresh.json", source)
        self.assertNotIn("bin/agentboost-usage-collect", source)
        self.assertIn("beam/agentboost/bin/agentboost", source)

    def test_native_dev_app_auto_discovers_default_agent_usage_folders(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        refresh_body = source.split("func refreshUsageIfPossible", 1)[1].split("func buildState", 1)[0]
        collect_body = source.split("func collectUsageFromSelectedAgentFolders", 1)[1].split("func usageEventsFile", 1)[0]

        self.assertIn("func defaultAgentUsageFolder(_ folderName: String) -> URL?", source)
        self.assertIn("allowsDevelopmentSwiftRuntimeFallback()", source)
        self.assertIn('agentUsageFolder(forKey: claudeDataRootBookmarkKey, defaultFolderName: ".claude")', source)
        self.assertIn('agentUsageFolder(forKey: codexDataRootBookmarkKey, defaultFolderName: ".codex")', source)
        self.assertIn("hasAvailableAgentUsageFolder()", refresh_body)
        self.assertIn("if let claudeRoot = claudeUsageRoot()", collect_body)
        self.assertIn("if let codexRoot = codexUsageRoot()", collect_body)

    def test_native_codex_event_ids_match_python_stable_contract(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        codex_body = source.split("func codexUsageEvents", 1)[1].split("func tokenUsageFromInfo", 1)[0]

        self.assertIn("import CryptoKit", source)
        self.assertIn("func stableID(_ parts: Any...) -> String", source)
        self.assertIn('joined(separator: "\\u{1f}")', source)
        self.assertIn("SHA256.hash(data:", source)
        self.assertIn('"event_id": "codex:\\(stableID(path.path, index + 1))"', codex_body)
        self.assertNotIn('"event_id": "codex:\\(path.path):\\(index + 1)"', codex_body)

    def test_native_claude_usage_reads_project_jsonl_like_ccusage_without_content(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        claude_body = source.split("func claudeUsageEvents", 1)[1].split("func codexUsageEvents", 1)[0]
        project_body = (
            source.split("func claudeProjectUsageEvents", 1)[1].split("func claudeSessionMetaUsageEvents", 1)[0]
            if "func claudeProjectUsageEvents" in source
            else ""
        )

        self.assertIn("func claudeProjectUsageEvents", source)
        self.assertIn('appendingPathComponent("projects", isDirectory: true)', project_body)
        self.assertIn('message["usage"] as? [String: Any]', project_body)
        self.assertIn('"cache_creation_input_tokens"', project_body)
        self.assertIn('"cache_read_input_tokens"', project_body)
        self.assertIn('stableID(messageID, requestID)', project_body)
        self.assertIn('"record_type": "turn"', project_body)
        self.assertNotIn('"content"', project_body)
        self.assertIn("let projectEvents = claudeProjectUsageEvents", claude_body)
        self.assertIn("return projectEvents.isEmpty ? claudeSessionMetaUsageEvents", claude_body)

    def test_native_codex_usage_matches_ccusage_delta_aliases_and_zero_skip(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        codex_body = source.split("func codexUsageEvents", 1)[1].split("func tokenUsageFromInfo", 1)[0]
        token_body = source.split("func tokenUsageDictionary", 1)[1].split("func codexModel", 1)[0]

        self.assertIn('text(row["type"]) == "turn_context"', codex_body)
        self.assertIn("codexModel(from:", codex_body)
        self.assertIn("isZeroUsage(usage)", codex_body)
        self.assertIn('"model": model', codex_body)
        self.assertIn('"cache_read_input_tokens"', token_body)
        self.assertIn("input + output", token_body)
        self.assertIn("min(cached, input)", token_body)

    def test_native_usage_refresh_replaces_legacy_claude_session_rows(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        collect_body = source.split("func collectUsageFromSelectedAgentFolders", 1)[1].split("func usageEventsFile", 1)[0]

        self.assertIn("let hasClaudeProjectEvents", collect_body)
        self.assertIn('text(event["source_agent"]) == "claude" && text(event["record_type"]) == "session"', collect_body)
        self.assertIn("let removedLegacy", collect_body)
        self.assertIn('"removed_legacy": removedLegacy', collect_body)
        self.assertIn("replaceUsageEvents", collect_body)
        self.assertIn("func replaceUsageEvents", source)

    def test_native_app_exposes_headless_state_json_for_usage_refresh(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        main_body = source.split("let app = NSApplication.shared", 1)[0]
        state_json_body = source.split("func writeStateJSONAndExit()", 1)[1].split("writeStateJSONAndExit()", 1)[0]

        self.assertIn("func writeStateJSONAndExit()", source)
        self.assertIn("let args = CommandLine.arguments", main_body)
        self.assertIn('args.contains("--state-json")', main_body)
        self.assertIn("loadDisplayState(refreshUsage: false)", state_json_body)
        self.assertNotIn("loadLiveUsageState(refreshUsage: false)", state_json_body)
        self.assertIn("JSONSerialization.data(withJSONObject: state", main_body)
        self.assertIn("FileHandle.standardOutput.write(data)", main_body)
        self.assertIn("writeStateJSONAndExit()", main_body)

    def test_native_usage_refresh_limits_recursive_session_scan_to_recent_files(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        refresh_possible_body = source.split("func refreshUsageIfPossible", 1)[1].split("func buildState", 1)[0]
        claude_body = source.split("func claudeUsageEvents", 1)[1].split("func codexUsageEvents", 1)[0]
        codex_body = source.split("func codexUsageEvents", 1)[1].split("func tokenUsageFromInfo", 1)[0]
        collect_body = source.split("func collectUsageFromSelectedAgentFolders", 1)[1].split("func usageEventsFile", 1)[0]

        self.assertIn("private let usageRefreshLookbackSeconds", source)
        self.assertIn("func shouldImportUsageFile(_ path: URL, now: Date = Date()) -> Bool", source)
        self.assertIn(".contentModificationDateKey", source)
        self.assertIn("now.addingTimeInterval(-usageRefreshLookbackSeconds)", source)
        self.assertIn("let cutoff = Date().addingTimeInterval(-usageRefreshLookbackSeconds)", refresh_possible_body)
        self.assertIn("collectUsageFromSelectedAgentFolders(dataRoot: dataRoot, since: cutoff)", refresh_possible_body)
        self.assertIn("func collectUsageFromSelectedAgentFolders(dataRoot: URL, since cutoff: Date? = nil)", source)
        self.assertIn("claudeUsageEvents(claudeRoot: claudeRoot, importedAt: importedAt, since: cutoff)", collect_body)
        self.assertIn("codexUsageEvents(codexRoot: codexRoot, importedAt: importedAt, since: cutoff)", collect_body)
        self.assertIn('"scope": cutoff == nil ? "lifetime" : "recent"', collect_body)
        self.assertIn("if cutoff != nil && !shouldImportUsageFile(path)", claude_body)
        self.assertIn("if cutoff != nil && !shouldImportUsageFile(path)", codex_body)

    def test_manual_usage_refresh_collects_agent_lifetime_without_recent_file_filter(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        refresh_body = source.split("private func refreshStateInBackground", 1)[1].split("private func loadAndCacheFullDisplayState", 1)[0]
        collect_body = source.split("func collectUsageFromSelectedAgentFolders", 1)[1].split("func usageEventsFile", 1)[0]

        self.assertIn("if forceUsageRefresh", refresh_body)
        self.assertIn("collectUsageFromSelectedAgentFolders(dataRoot: dataRoot)", refresh_body)
        self.assertNotIn("collectUsageFromSelectedAgentFolders(dataRoot: dataRoot, since:", refresh_body)
        self.assertIn("since cutoff: Date? = nil", collect_body)
        self.assertIn("cutoff == nil ? \"lifetime\" : \"recent\"", collect_body)

    def test_startup_runs_lifetime_usage_backfill_once_in_background(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        launch_body = source.split("func applicationDidFinishLaunching", 1)[1].split("private func startStateRefreshTimer", 1)[0]
        backfill_body = source.split("private func runUsageBackfillOnceInBackground", 1)[1].split("private func refreshStateInBackground", 1)[0]
        should_backfill_body = source.split("func shouldRunUsageBackfill", 1)[1].split("func writeUsageBackfill", 1)[0]

        self.assertIn("private var usageBackfillInFlight = false", source)
        self.assertIn("runUsageBackfillOnceInBackground()", launch_body)
        self.assertLess(launch_body.index("runUsageBackfillOnceInBackground()"), launch_body.index("refreshStateInBackground(refreshUsage: true)"))
        self.assertIn("guard !usageBackfillInFlight", backfill_body)
        self.assertIn("shouldRunUsageBackfill(dataRoot: dataRoot)", backfill_body)
        self.assertIn("shouldDeferUsageBackfillForActiveAgents()", backfill_body)
        self.assertLess(backfill_body.index("shouldDeferUsageBackfillForActiveAgents()"), backfill_body.index("collectUsageFromSelectedAgentFolders(dataRoot: dataRoot)"))
        self.assertIn('"reason": "active_agents_running"', backfill_body)
        self.assertIn("!usageEventsFileHasData(dataRoot: dataRoot)", should_backfill_body)
        self.assertIn("readCachedDisplayState(dataRoot: dataRoot) == nil", should_backfill_body)
        self.assertIn("func shouldDeferUsageBackfillForActiveAgents() -> Bool", source)
        self.assertIn('!textArray(cachedRunningAgentActivity()["active_agents"]).isEmpty', source)
        self.assertIn("hasAvailableAgentUsageFolder()", backfill_body)
        self.assertIn("stateQueue.async", backfill_body)
        self.assertIn("collectUsageFromSelectedAgentFolders(dataRoot: dataRoot)", backfill_body)
        self.assertNotIn("collectUsageFromSelectedAgentFolders(dataRoot: dataRoot, since:", backfill_body)
        self.assertIn("writeUsageBackfill", backfill_body)
        self.assertIn("let finalState = loadDisplayState(refreshUsage: false)", backfill_body)
        self.assertIn("writeCachedDisplayState(finalState, dataRoot: dataRoot)", backfill_body)
        self.assertIn("DispatchQueue.main.async", backfill_body)

    def test_startup_usage_backfill_state_is_exposed_to_headless_state(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        merge_body = source.split("func stateByMergingLiveUsage", 1)[1].split("func loadBeamRuntimeState", 1)[0]
        build_state_body = source.split("func buildState(dataRoot:", 1)[1].split("func loadLiveUsageState", 1)[0]
        live_state_body = source.split("func loadLiveUsageState", 1)[1].split("func liveRecentUsageEvents", 1)[0]

        self.assertIn("func usageBackfillFile(dataRoot: URL) -> URL", source)
        self.assertIn("func readUsageBackfill(dataRoot: URL) -> [String: Any]", source)
        self.assertIn("func shouldRunUsageBackfill(dataRoot: URL) -> Bool", source)
        self.assertIn("func writeUsageBackfill(_ summary: [String: Any], dataRoot: URL, status: String)", source)
        self.assertIn('"usage_backfill": readUsageBackfill(dataRoot: dataRoot)', build_state_body)
        self.assertIn('"usage_backfill": readUsageBackfill(dataRoot: dataRoot)', live_state_body)
        self.assertIn('"usage_backfill"', merge_body)

    def test_relaunch_uses_cached_full_usage_state_before_live_refresh(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        launch_body = source.split("func applicationDidFinishLaunching", 1)[1].split("private func startStateRefreshTimer", 1)[0]
        initial_body = source.split("func initialDisplayState", 1)[1].split("func loadState", 1)[0]
        refresh_body = source.split("private func refreshStateInBackground", 1)[1].split("private func loadAndCacheFullDisplayState", 1)[0]
        full_refresh_body = source.split("private func loadAndCacheFullDisplayState", 1)[1].split("private func applyState", 1)[0]

        self.assertIn("func displayStateCacheFile(dataRoot: URL) -> URL", source)
        self.assertIn("func readCachedDisplayState(dataRoot: URL) -> [String: Any]?", source)
        self.assertIn("func writeCachedDisplayState(_ state: [String: Any], dataRoot: URL)", source)
        self.assertIn("let state = initialDisplayState(dataRoot: activeDataRoot())", launch_body)
        self.assertNotIn("let state = loadLiveUsageState(refreshUsage: false)", launch_body)
        self.assertIn("let liveState = loadLiveUsageState(refreshUsage: false)", initial_body)
        self.assertIn("if let cached = readCachedDisplayState(dataRoot: dataRoot)", initial_body)
        self.assertIn("stateByMergingLiveUsage(cached, liveState: liveState)", initial_body)
        self.assertNotIn("loadDisplayState", initial_body)
        self.assertNotIn("writeCachedDisplayState(finalState, dataRoot: dataRoot)", refresh_body)
        self.assertIn("writeCachedDisplayState(finalState, dataRoot: dataRoot)", full_refresh_body)
        self.assertLess(full_refresh_body.index("let finalState = loadDisplayState(refreshUsage: false)"), full_refresh_body.index("writeCachedDisplayState(finalState, dataRoot: dataRoot)"))
        self.assertNotIn("writeCachedDisplayState(fastState", refresh_body)

    def test_selecting_agent_usage_folder_triggers_backfill_retry_when_needed(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        select_body = source.split("private func selectFolder", 1)[1].split("@objc private func refreshUsage", 1)[0]

        self.assertIn("runUsageBackfillOnceInBackground()", select_body)
        self.assertIn("refreshStateInBackground(refreshUsage: true)", select_body)

    def test_native_fallback_missions_use_frequency_auto_checked_contract(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        mission_body = source.split("func missions(prefix:", 1)[1].split("func metaReviewState", 1)[0]

        self.assertIn("func missionProgressState", source)
        self.assertIn("func activeWorkdaysThisWeek", source)
        self.assertIn('"mission_id": "daily_ai_turn"', mission_body)
        self.assertIn('"mission_id": "weekly_ai_streak"', mission_body)
        self.assertIn('"mission_id": "weekly_skill_prompt_review"', mission_body)
        self.assertIn('"cadence": prefix', mission_body)
        self.assertIn('"frequency": "\\(dailyTarget)/day"', mission_body)
        self.assertIn('"frequency": "\\(weeklyTarget)/week"', mission_body)
        self.assertIn('"auto_check": true', mission_body)
        self.assertIn('"check_cost": "loaded_events_only"', mission_body)
        self.assertIn('"check_cost": "local_artifact_scan"', mission_body)
        self.assertIn('"metric": "active_workdays"', mission_body)
        self.assertIn('"metric": "skill_prompt_review_this_week"', mission_body)
        self.assertNotIn('"difficulty"', mission_body)

    def test_native_fallback_missions_self_adjust_frequency_contract(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        mission_body = source.split("func missions(prefix:", 1)[1].split("func metaReviewState", 1)[0]

        self.assertIn("func selfAdjustedDailyMissionTarget", source)
        self.assertIn("func selfAdjustedWeeklyMissionTarget", source)
        self.assertIn("recentActiveDayAverage", source)
        self.assertIn("recentWeeklyWorkdayAverage", source)
        self.assertIn('let dailyTarget = selfAdjustedDailyMissionTarget(events)', mission_body)
        self.assertIn('let weeklyTarget = min(5, selfAdjustedWeeklyMissionTarget(events))', mission_body)
        self.assertIn('"frequency": "\\(dailyTarget)/day"', mission_body)
        self.assertIn('"frequency": "\\(weeklyTarget)/week"', mission_body)
        self.assertIn('"adaptive": true', mission_body)
        self.assertIn('"target_source": "recent_active_day_average"', mission_body)
        self.assertIn('"target_source": "recent_weekly_workdays"', mission_body)
        self.assertIn('"target_window_days": 14', mission_body)
        self.assertIn('"target_window_days": 28', mission_body)

    def test_native_skill_prompt_review_button_runs_background_artifact_update(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        panel_body = source.split("final class AgentBoostMenuPanelView", 1)[1].split("final class AgentBoostBadgeSelectorPanelView", 1)[0]
        app_body = source.split("final class AppDelegate", 1)[1]

        self.assertIn("var runSkillPromptReviewAction: Selector?", panel_body)
        self.assertIn("private let skillPromptReviewTitle", panel_body)
        self.assertIn("private let skillPromptReviewRunButton", panel_body)
        self.assertIn("@objc private func runSkillPromptReviewButtonClicked", panel_body)
        self.assertIn("configureSkillPromptReviewRunButton", panel_body)
        self.assertIn("panel.runSkillPromptReviewAction = #selector(doSkillPromptReview)", app_body)
        self.assertIn("@objc private func doSkillPromptReview", app_body)
        self.assertIn("private func startSkillPromptReviewRun", app_body)
        run_body = app_body.split("private func startSkillPromptReviewRun", 1)[1].split("@objc private func doMetaReview", 1)[0]
        self.assertIn("stateQueue.async", run_body)
        self.assertIn("performSkillPromptReviewState()", run_body)
        self.assertIn('review["status"] = "running"', run_body)
        self.assertIn("let finalState = loadDisplayState(refreshUsage: false)", run_body)
        self.assertLess(run_body.index("stateQueue.async"), run_body.index("performSkillPromptReviewState()"))
        self.assertIn("func writeSkillPromptReviewArtifact", source)
        self.assertIn("AgentBoost Skill and Prompt Review", source)

    def test_native_identity_update_button_runs_background_artifact_update(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        panel_body = source.split("final class AgentBoostMenuPanelView", 1)[1].split("final class AgentBoostBadgeSelectorPanelView", 1)[0]
        app_body = source.split("final class AppDelegate", 1)[1]
        mission_body = source.split("func missions(prefix:", 1)[1].split("func metaReviewState", 1)[0]

        self.assertIn("var runIdentityUpdateAction: Selector?", panel_body)
        self.assertIn("private let identityUpdateTitle", panel_body)
        self.assertIn("private let identityUpdateRunButton", panel_body)
        self.assertIn("@objc private func runIdentityUpdateButtonClicked", panel_body)
        self.assertIn("configureIdentityUpdateRunButton", panel_body)
        self.assertIn("panel.runIdentityUpdateAction = #selector(doIdentityUpdate)", app_body)
        self.assertIn("@objc private func doIdentityUpdate", app_body)
        self.assertIn("private func startIdentityUpdateRun", app_body)
        run_body = app_body.split("private func startIdentityUpdateRun", 1)[1].split("@objc private func doSkillPromptReview", 1)[0]
        self.assertIn("stateQueue.async", run_body)
        self.assertIn("performIdentityUpdateState()", run_body)
        self.assertIn('identity["status"] = "running"', run_body)
        self.assertIn('text(mission["mission_id"]) == "weekly_identity_update"', run_body)
        self.assertIn("let finalState = loadDisplayState(refreshUsage: false)", run_body)
        self.assertLess(run_body.index("stateQueue.async"), run_body.index("performIdentityUpdateState()"))
        self.assertIn("func runIdentityUpdateProcess(dataRoot: URL) -> Bool", source)
        self.assertIn('"--do-identity-update"', source)
        self.assertIn('"mission_id": "weekly_identity_update"', mission_body)
        self.assertIn('"metric": "identity_update_this_week"', mission_body)
        self.assertIn("func identityUpdateState(dataRoot: URL", source)

    def test_review_run_buttons_disable_immediately_until_background_review_finishes(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        panel_body = source.split("final class AgentBoostMenuPanelView", 1)[1].split("final class AgentBoostBadgeSelectorPanelView", 1)[0]

        meta_click = panel_body.split("@objc private func runMetaButtonClicked", 1)[1].split("@objc private func runSkillPromptReviewButtonClicked", 1)[0]
        skill_click = panel_body.split("@objc private func runSkillPromptReviewButtonClicked", 1)[1].split("@objc private func runIdentityUpdateButtonClicked", 1)[0]
        identity_click = panel_body.split("@objc private func runIdentityUpdateButtonClicked", 1)[1].split("@objc private func footerRefreshClicked", 1)[0]

        self.assertIn("guard metaRunButton.isEnabled else { return }", meta_click)
        self.assertIn('configureMetaRunButton(title: "Running", enabled: false)', meta_click)
        self.assertLess(meta_click.index('configureMetaRunButton(title: "Running", enabled: false)'), meta_click.index("actionTarget?.perform(runMetaAction)"))

        self.assertIn("guard skillPromptReviewRunButton.isEnabled else { return }", skill_click)
        self.assertIn('configureSkillPromptReviewRunButton(title: "Running", enabled: false)', skill_click)
        self.assertLess(skill_click.index('configureSkillPromptReviewRunButton(title: "Running", enabled: false)'), skill_click.index("actionTarget?.perform(runSkillPromptReviewAction)"))

        self.assertIn("guard identityUpdateRunButton.isEnabled else { return }", identity_click)
        self.assertIn('configureIdentityUpdateRunButton(title: "Running", enabled: false)', identity_click)
        self.assertLess(identity_click.index('configureIdentityUpdateRunButton(title: "Running", enabled: false)'), identity_click.index("actionTarget?.perform(runIdentityUpdateAction)"))

    def test_floating_status_omits_window_size_from_normal_menu_surfaces(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        panel_body = source.split("final class AgentBoostMenuPanelView", 1)[1].split("final class AgentBoostBadgeSelectorPanelView", 1)[0]
        legacy_menu_body = source.split("private func menuForState", 1)[1].split("private func configureStatusAnimation", 1)[0]

        self.assertNotIn("private let systemDims", panel_body)
        self.assertNotIn("systemDims.stringValue", panel_body)
        self.assertNotIn('screensaver["target_frame"]', panel_body)
        self.assertIn('systemTitle.stringValue = "Floating · \\(displays) \\(displayLabel)"', panel_body)

        self.assertIn('"  Flying across \\(connectedCount) \\(displayLabel)"', legacy_menu_body)
        self.assertNotIn('intText(frame["width"])x\\(intText(frame["height"]))', legacy_menu_body)
        self.assertNotIn('screensaver["target_frame"]', legacy_menu_body)

    def test_native_level_header_uses_xp_table_progress_contract(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")

        self.assertIn("let levelXPRequirements: [(level: Int, requiredXP: Int)]", source)
        self.assertIn("(1, 15)", source)
        self.assertIn("(50, 709_716)", source)
        self.assertIn('"xp_breakdown": xpBreakdown', source)
        self.assertIn('"level_progress": levelProgress', source)
        self.assertIn('"level_label": levelLabel', source)
        self.assertIn('let progress = state["level_progress"] as? [String: Any] ?? [:]', source)
        self.assertIn('levelChip.stringValue = "LV \\(levelNumber) · \\(currentXP)/\\(requiredXP) XP"', source)

    def test_fast_live_refresh_preserves_cached_missions_and_seven_day_chart(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        refresh_body = source.split("private func refreshStateInBackground", 1)[1].split("@objc private func handleStatusItemClick", 1)[0]

        self.assertIn("let cachedState = lastRenderedState", refresh_body)
        self.assertIn("let liveState = loadLiveUsageState(refreshUsage: false)", refresh_body)
        self.assertIn("let fastState = cachedState.isEmpty ? liveState : stateByMergingLiveUsage(cachedState, liveState: liveState)", refresh_body)
        self.assertIn("self.applyState(fastState)", refresh_body)
        self.assertNotIn("let fastState = loadLiveUsageState(refreshUsage: false)", refresh_body)

    def test_native_state_exposes_seven_day_usage_buckets_for_chart(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        build_state_body = source.split("func buildState(dataRoot:", 1)[1].split("func loadLiveUsageState", 1)[0]

        self.assertIn("func sevenDayUsageBuckets(events: [[String: Any]]) -> [[String: Any]]", source)
        self.assertIn('"agentboost_daily_7d": sevenDayUsageBuckets(events: events)', build_state_body)
        self.assertIn('state["agentboost_daily_7d"]', source)

    def test_native_live_usage_state_tails_codex_sessions_like_ccusage_before_ledger_refresh(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        live_tail_body = source.split("func liveRecentUsageEvents", 1)[1].split("func mergeUsageEvents", 1)[0]
        codex_body = source.split("func codexUsageEvents", 1)[1].split("func tokenUsageFromInfo", 1)[0]

        self.assertIn('if let codexRoot = codexUsageRoot()', live_tail_body)
        self.assertIn("codexUsageEvents(codexRoot: codexRoot, importedAt: importedAt, since: cutoff)", live_tail_body)
        self.assertIn("func codexUsageEvents(codexRoot: URL, importedAt: String, since cutoff: Date? = nil)", source)
        self.assertIn('!line.contains("\\\"token_count\\\"") && !line.contains("\\\"turn_context\\\"")', codex_body)
        self.assertIn("Array(lines.enumerated().reversed())", codex_body)
        self.assertIn("if let cutoff, let occurredAt = eventDate(occurredAtRaw), occurredAt < cutoff", codex_body)
        self.assertLess(codex_body.index("modifiedAt < cutoff"), codex_body.index("usageFileContents(path: path, since: cutoff)"))
        self.assertIn("tokenUsageFromInfo(info, previousTotal: previousTotal)", codex_body)
        self.assertIn('"last_token_usage"', source)
        self.assertIn('"total_token_usage"', source)

    def test_native_live_usage_state_tails_claude_projects_before_ledger_refresh(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        live_tail_body = source.split("func liveRecentUsageEvents", 1)[1].split("func mergeUsageEvents", 1)[0]
        claude_body = source.split("func claudeUsageEvents", 1)[1].split("func codexUsageEvents", 1)[0]
        project_body = source.split("func claudeProjectUsageEvents", 1)[1].split("func claudeSessionMetaUsageEvents", 1)[0]

        self.assertIn('if let claudeRoot = claudeUsageRoot()', live_tail_body)
        self.assertIn("claudeUsageEvents(claudeRoot: claudeRoot, importedAt: importedAt, since: cutoff)", live_tail_body)
        self.assertIn("func claudeUsageEvents(claudeRoot: URL, importedAt: String, since cutoff: Date? = nil)", source)
        self.assertIn(
            "claudeProjectUsageEvents(claudeRoot: claudeRoot, importedAt: importedAt, since: cutoff)",
            claude_body,
        )
        self.assertIn("func claudeProjectUsageEvents(claudeRoot: URL, importedAt: String, since cutoff: Date? = nil)", source)
        self.assertIn("modifiedAt < cutoff", project_body)
        self.assertIn("usageFileContents(path: path, since: cutoff)", project_body)
        self.assertIn("Array(lines.enumerated().reversed())", project_body)
        self.assertIn("if let cutoff, let occurredAt = eventDate(occurredAtRaw), occurredAt < cutoff", project_body)
        self.assertLess(project_body.index("modifiedAt < cutoff"), project_body.index("usageFileContents(path: path, since: cutoff)"))

    def test_native_live_claude_tail_reuses_recent_file_scan_cache(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        project_body = source.split("func claudeProjectUsageEvents", 1)[1].split("func claudeSessionMetaUsageEvents", 1)[0]
        recent_files_body = source.split("func recentClaudeProjectFiles", 1)[1].split("func claudeProjectUsageEvents", 1)[0]

        self.assertIn("private let liveClaudeProjectFileScanIntervalSeconds: TimeInterval = 5 * 60", source)
        self.assertIn("private var liveClaudeProjectFileCache:", source)
        self.assertIn("func recentClaudeProjectFiles(claudeRoot: URL, since cutoff: Date) -> [URL]", source)
        self.assertIn("Date().timeIntervalSince(cache.refreshedAt) < liveClaudeProjectFileScanIntervalSeconds", recent_files_body)
        self.assertIn("return cache.files", recent_files_body)
        self.assertIn("recentClaudeProjectFiles(claudeRoot: claudeRoot, since: cutoff)", project_body)
        self.assertNotIn("FileManager.default.enumerator(at: projectsDir", project_body)

    def test_native_live_usage_state_reads_recent_ledger_tail_for_status_refresh(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        live_body = source.split("func loadLiveUsageState", 1)[1].split("func readRecentEvents", 1)[0]
        recent_body = source.split("func readRecentEvents", 1)[1].split("func readEvents", 1)[0]

        self.assertIn("func loadLiveUsageState(refreshUsage: Bool = true) -> [String: Any]", source)
        self.assertIn("private let liveUsageRecentWindowSeconds: TimeInterval = 2 * 60", source)
        self.assertIn("let liveRecentEvents = liveRecentUsageEvents(since: recentCutoff", live_body)
        self.assertIn("let recentEvents = liveRecentEvents", live_body)
        self.assertNotIn("readRecentEvents(dataRoot: dataRoot", live_body)
        self.assertIn("recentTokenActivity(events: recentEvents)", live_body)
        self.assertIn("statusAnimationActivity(recent: recentActivity", live_body)
        self.assertIn("raw.split(separator: \"\\n\").reversed()", recent_body)
        self.assertIn("else if sawRecentEvent", recent_body)
        self.assertIn("break", recent_body)

    def test_event_date_reuses_cached_iso8601_formatters(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        event_date_body = source.split("func eventDate", 1)[1].split("func totalTokens", 1)[0]

        self.assertIn("private let eventDateFormatterLock = NSLock()", source)
        self.assertIn("private let fractionalEventDateFormatter: ISO8601DateFormatter", source)
        self.assertIn("private let wholeSecondEventDateFormatter: ISO8601DateFormatter", source)
        self.assertIn("eventDateFormatterLock.lock()", event_date_body)
        self.assertIn("defer { eventDateFormatterLock.unlock() }", event_date_body)
        self.assertNotIn("ISO8601DateFormatter()", event_date_body)

    def test_native_live_usage_state_reads_bounded_jsonl_tails_for_animation(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        tail_body = source.split("func tailJSONLContents", 1)[1].split("func usageFileContents", 1)[0]
        usage_body = source.split("func usageFileContents", 1)[1].split("func collectUsageFromSelectedAgentFolders", 1)[0]
        recent_body = source.split("func readRecentEvents", 1)[1].split("func readEvents", 1)[0]
        codex_body = source.split("func codexUsageEvents", 1)[1].split("func tokenUsageFromInfo", 1)[0]
        merge_body = source.split("func mergeUsageEvents", 1)[1].split("func readRecentEvents", 1)[0]

        self.assertIn("private let liveUsageTailBytes = UInt64(512 * 1024)", source)
        self.assertIn("func sessionSearchRoots(sessionsDir: URL, since cutoff: Date?) -> [URL]", source)
        self.assertIn("FileHandle(forReadingFrom: path)", tail_body)
        self.assertIn("try? handle.seekToEnd()", tail_body)
        self.assertIn("try? handle.seek(toOffset: startOffset)", tail_body)
        self.assertIn("readDataToEndOfFile()", tail_body)
        self.assertIn("if startOffset > 0", tail_body)
        self.assertIn("tailJSONLContents(path: path, maxBytes: liveUsageTailBytes)", usage_body)
        self.assertIn("tailJSONLContents(path: path, maxBytes: liveUsageTailBytes)", recent_body)
        self.assertIn("for root in sessionSearchRoots(sessionsDir: sessionsDir, since: cutoff)", codex_body)
        self.assertIn("usageFileContents(path: path, since: cutoff)", codex_body)
        self.assertIn("usageEventSignature", merge_body)
        self.assertIn("seenSignatures", merge_body)

    def test_overlay_runtime_snapshot_is_not_written_every_animation_frame(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        app_body = source.split("final class AppDelegate", 1)[1]
        advance_body = app_body.split("@objc private func advanceRocketScreensaverDisplay", 1)[1].split("private func rebuildRocketScreensaverWindows", 1)[0]

        self.assertIn("private let overlayRuntimeSnapshotWriteIntervalSeconds: TimeInterval = 5", source)
        self.assertIn("private var lastOverlaySnapshotAt: Date = .distantPast", app_body)
        self.assertIn(
            "now.timeIntervalSince(lastOverlaySnapshotAt) >= overlayRuntimeSnapshotWriteIntervalSeconds",
            advance_body,
        )
        self.assertLess(
            advance_body.index("now.timeIntervalSince(lastOverlaySnapshotAt) >= overlayRuntimeSnapshotWriteIntervalSeconds"),
            advance_body.index("writeOverlayRuntimeSnapshot(enabled: true, capturedAt: now)"),
        )

    def test_rocket_animation_uses_elapsed_time_while_rocket_stays_centered(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        advance_body = source.split("@objc private func advanceRocket", 1)[1].split("override func draw", 1)[0]

        self.assertNotIn("if rocketProgress > 1", advance_body)
        self.assertNotIn("rocketProgress = 0", advance_body)
        self.assertNotIn("backgroundProgress += ", advance_body)
        self.assertIn("private var animationStartedAt = Date()", source)
        self.assertIn("private func currentBackgroundProgress()", source)
        self.assertIn("Date().timeIntervalSince(animationStartedAt)", source)
        self.assertIn("truncatingRemainder(dividingBy: 1)", source)
        self.assertNotIn("private func drawMovingBackground", source)
        self.assertNotIn("drawMovingBackground(", source)
        self.assertIn("private func rocketCenterPoints", source)
        center_body = source.split("private func rocketCenterPoints", 1)[1].split("private func drawRocket", 1)[0]
        status_body = source.split("final class RocketStatusView", 1)[1].split("final class RocketScreensaverView", 1)[0]
        self.assertIn("centerX", center_body)
        self.assertIn("sin(currentBackgroundProgress() * CGFloat.pi * 2)", status_body)
        self.assertIn("drawRocket(at: rocketPoint, scale: rocketScale, agent: agent)", source)
        self.assertIn("drawTokenText(text: currentTokenText(), below: tokenPoint)", source)
        self.assertNotIn("private func flightPoint", source)

    def test_rocket_animation_has_no_background_track_or_badge_bubble(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        status_body = source.split("final class RocketStatusView", 1)[1].split("final class RocketScreensaverView", 1)[0]

        self.assertNotIn("drawMovingBackground", status_body)
        self.assertNotIn("drawTokenBadge", status_body)
        self.assertNotIn("roundedRect", status_body)
        self.assertNotIn("windowBackgroundColor.withAlphaComponent", status_body)
        self.assertNotIn("let flame = NSBezierPath()", status_body)
        self.assertNotIn("sin(currentBackgroundProgress() * CGFloat.pi * 2) * (spacing / 2)", status_body)

    def test_product_quality_report_rejects_ping_pong_background_motion(self):
        source = self.repo / "macos" / "agentboost" / "AgentBoostApp.swift"
        source.write_text(
            "\n".join(
                [
                    "import Cocoa",
                    "func tokenActivity(rollups: [String: Any]) -> [String: Any] {",
                    '    let activeAgents = activeAgentsFromRollup(rollups["Today"])',
                    '    return ["active_agents": activeAgents, "rocket_count": activeAgents.count >= 2 ? 2 : 1]',
                    "}",
                    "func statusAnimationActivity(recent: [String: Any], today: [String: Any], network: [String: Any]) -> [String: Any] {",
                    '    let activeAgents = textArray(today["active_agents"])',
                    '    let rocketCount = max(1, min(2, tokenInt(today["rocket_count"])))',
                    '    return ["active_agents": activeAgents, "rocket_count": rocketCount]',
                    "}",
                    "func activeAgentsFromRollup(_ value: Any?) -> [String] { [] }",
                    "func textArray(_ value: Any?) -> [String] { [] }",
                    "func tokenInt(_ value: Any?) -> Int { 1 }",
                    "final class RocketStatusView: NSView {",
                    "    private var animationStartedAt = Date()",
                    "    private var activeAgents: [String] = []",
                    "    private var rocketCount = 1",
                    "    private var motionTimer: Timer?",
                    "    private func currentBackgroundProgress() -> CGFloat {",
                    "        Date().timeIntervalSince(animationStartedAt)",
                    "        return 0.truncatingRemainder(dividingBy: 1)",
                    "    }",
                    "    func configure(activity: [String: Any], statusViews: [[String: Any]]) {",
                    '        activeAgents = textArray(activity["active_agents"])',
                    '        rocketCount = max(1, min(2, tokenInt(activity["rocket_count"])))',
                    "        let timer = Timer(timeInterval: 0.1, target: self, selector: #selector(advanceRocket(_:)), userInfo: nil, repeats: true)",
                    "        RunLoop.main.add(timer, forMode: .common)",
                    "    }",
                    "    @objc private func advanceRocket(_ timer: Timer) { needsDisplay = true }",
                    "    override func draw(_ dirtyRect: NSRect) {",
                    "        let animationMidY = CGFloat(15)",
                    "        let trackStart = CGFloat(8)",
                    "        let trackEnd = bounds.width - 8",
                    "        drawMovingBackground(trackStart: trackStart, trackEnd: trackEnd, centerY: animationMidY)",
                    "        let rocketPoints = rocketCenterPoints(trackStart: trackStart, trackEnd: trackEnd, centerY: animationMidY)",
                    "        for rocketPoint in rocketPoints { drawRocket(at: rocketPoint, scale: CGFloat(1.0)) }",
                    "    }",
                    "    private func drawMovingBackground(trackStart: CGFloat, trackEnd: CGFloat, centerY: CGFloat) {",
                    "        let spacing = CGFloat(8)",
                    "        let phase = sin(currentBackgroundProgress() * CGFloat.pi * 2) * (spacing / 2)",
                    "        _ = phase",
                    "    }",
                    "    private func rocketCenterPoints(trackStart: CGFloat, trackEnd: CGFloat, centerY: CGFloat) -> [NSPoint] {",
                    "        let x = (trackStart + trackEnd) / 2",
                    "        return [NSPoint(x: x, y: centerY)]",
                    "    }",
                    "    private func drawRocket(at point: NSPoint, scale: CGFloat) {}",
                    "}",
                ]
            ),
            encoding="utf-8",
        )
        build_agentboost_app(self.repo, self.app_path, compile_app=False, portable_profile=True)

        report = product_quality_report(self.repo, self.app_path)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["animation.elapsed_background_motion"]["status"], "fail")
        self.assertIn("background track", checks["animation.elapsed_background_motion"]["message"])

    def test_rocket_animation_timer_runs_while_status_menu_is_open(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        configure_body = source.split("func configure(activity:", 1)[1].split("@objc private func advanceRocket", 1)[0]

        self.assertNotIn("Timer.scheduledTimer", configure_body)
        self.assertIn("statusViews.count > 1", configure_body)
        self.assertIn("let timer = Timer(", configure_body)
        self.assertIn("timeInterval: frameInterval", configure_body)
        self.assertIn("RunLoop.main.add(timer, forMode: .common)", configure_body)
        self.assertIn("motionTimer = timer", configure_body)

    def test_rocket_animation_uses_smooth_frame_cadence_without_large_catchup_jumps(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        status_body = source.split("final class RocketStatusView", 1)[1].split("final class RocketScreensaverView", 1)[0]
        configure_body = status_body.split("func configure(activity:", 1)[1].split("@objc private func advanceRocket", 1)[0]
        status_emoji_body = status_body.split("private func drawEmojiRocket", 1)[1].split("private func drawTokenText", 1)[0]
        overlay_body = source.split("final class RocketScreensaverView", 1)[1].split("final class AppDelegate", 1)[0]
        advance_body = overlay_body.split("private func advanceMotion", 1)[1].split("private func updateRocketHeading", 1)[0]
        app_body = source.split("private func startRocketScreensaverDisplayTimer", 1)[1].split("@objc private func advanceRocketScreensaverDisplay", 1)[0]

        self.assertIn("private let rocketStatusFrameIntervalSeconds: TimeInterval = 1.0 / 10.0", source)
        self.assertIn("let frameInterval = rocketStatusFrameIntervalSeconds", configure_body)
        self.assertNotIn("1.0 / 24.0", configure_body)
        self.assertIn("timer.tolerance = rocketStatusFrameIntervalSeconds / 4", configure_body)
        self.assertIn("private let rocketScreensaverFrameIntervalSeconds: TimeInterval = 1.0 / 10.0", source)
        self.assertIn("timeInterval: rocketScreensaverFrameIntervalSeconds", app_body)
        self.assertIn("timer.tolerance = rocketScreensaverFrameIntervalSeconds / 2", app_body)
        self.assertIn("let maxFrameDelta = CGFloat(1.0 / 30.0)", advance_body)
        self.assertIn("let deltaSeconds = min(maxFrameDelta, max(CGFloat(0), rawDelta))", advance_body)
        self.assertIn("NSGraphicsContext.current?.shouldAntialias = true", status_emoji_body)

    def test_badge_inventory_replaces_redundant_badges_section(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")

        self.assertIn("menu.addItem(badgeInventoryItem(state))", source)
        self.assertIn('NSMenuItem(title: "Badge Inventory"', source)
        self.assertNotIn('addDisabled("Badges"', source)
        self.assertNotIn('state["badges"] as? [[String: Any]]', source)
        self.assertNotIn("badges.prefix(9)", source)

    def test_badge_plus_affordance_is_a_clickable_native_button(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        panel_body = source.split("final class AgentBoostMenuPanelView", 1)[1].split("final class AgentBoostSettingsPanelView", 1)[0]
        app_body = source.split("final class AppDelegate", 1)[1]

        self.assertIn("private let badgesActionButton = NSButton()", panel_body)
        self.assertIn("var badgeAction: Selector?", panel_body)
        self.assertIn('NSImage(systemSymbolName: "plus.circle"', panel_body)
        self.assertIn("badgesActionButton.target = self", panel_body)
        self.assertIn("badgesActionButton.action = #selector(badgeActionClicked)", panel_body)
        self.assertIn("badgesActionButton.toolTip = \"Change achievement\"", panel_body)
        self.assertIn("@objc private func badgeActionClicked", panel_body)
        self.assertIn("badgeAction", panel_body)
        self.assertIn("panel.badgeAction = #selector(showBadgeSelectorFromPanel)", app_body)
        self.assertIn("@objc private func showBadgeSelectorFromPanel", app_body)
        self.assertIn("showBadgeSelectorPopover()", app_body)
        selector_body = app_body.split("@objc private func showBadgeSelectorFromPanel", 1)[1].split("private func showSettingsPopover", 1)[0]
        self.assertNotIn("showSettingsPopover()", selector_body)

    def test_main_badge_surface_shows_achievement_title_without_color_dot(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        panel_body = source.split("final class AgentBoostMenuPanelView", 1)[1].split("final class AgentBoostSettingsPanelView", 1)[0]

        self.assertNotIn("AgentBoostBadgesRowView", source)
        self.assertNotIn("AgentBoostBadgeDisplayItem", source)
        self.assertNotIn("badgesView", panel_body)
        self.assertIn('private let badgesHeader = NSTextField(labelWithString: "ACHIEVEMENTS")', panel_body)
        self.assertIn('badgesCountLabel.stringValue = representativeBadgeTitle(state: state)', panel_body)
        self.assertIn('func representativeBadgeTitle(state: [String: Any]) -> String', source)
        self.assertIn('return text(selected["name"])', source)

    def test_badge_plus_opens_single_selector_modal_with_name_icon_description(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        selector_body = source.split("final class AgentBoostBadgeSelectorPanelView", 1)[1].split("final class AgentBoostSettingsPanelView", 1)[0]
        app_body = source.split("final class AppDelegate", 1)[1]

        self.assertIn("private let agentboostRepresentativeBadgeLimit = 1", source)
        self.assertIn("final class AgentBoostBadgeSelectorPanelView", source)
        self.assertIn('NSTextField(labelWithString: "Achievements")', selector_body)
        self.assertIn("var saveBadgeSelectionAction: Selector?", selector_body)
        self.assertIn("private var selectedBadgeIDs: [String] = []", selector_body)
        self.assertIn('badges = inventory.isEmpty ? (earned ?? []) : inventory', selector_body)
        self.assertIn('makeHeaderCell("Icon")', selector_body)
        self.assertIn('makeHeaderCell("Name")', selector_body)
        self.assertIn('makeHeaderCell("Description")', selector_body)
        self.assertIn("badgeDescription(badge)", selector_body)
        self.assertIn("NSButton(radioButtonWithTitle:", selector_body)
        self.assertIn('let isEarned = text(badge["status"]) == "earned"', selector_body)
        self.assertIn("radio.isEnabled = isEarned", selector_body)
        self.assertIn("row.alphaValue = isEarned ? 1.0 : 0.55", selector_body)
        self.assertIn("name.font = NSFont.systemFont(ofSize: 12, weight: isEarned ? .medium : .regular)", selector_body)
        self.assertIn("name.textColor = isEarned ? palette.text : palette.mute", selector_body)
        self.assertIn("guard badgeIsEarned(id: id) else", selector_body)
        self.assertIn("selectedBadgeIDs = [id]", selector_body)
        self.assertNotIn("moveSelectedBadge", selector_body)
        self.assertNotIn('makeHeaderCell("Order")', selector_body)
        self.assertIn('"badge_ids": selectedBadgeIDs', selector_body)
        self.assertIn("private var badgeSelectorPopover: NSPopover?", app_body)
        self.assertIn("private var badgeSelectorPanel: AgentBoostBadgeSelectorPanelView?", app_body)
        self.assertIn("private func showBadgeSelectorPopover()", app_body)
        self.assertIn("panel.saveBadgeSelectionAction = #selector(saveRepresentativeBadgesFromSelector(_:))", app_body)
        self.assertIn("@objc private func saveRepresentativeBadgesFromSelector", app_body)
        self.assertIn("writeRepresentativeBadgeSelection(badgeIds:", app_body)

    def test_badge_state_exposes_ordered_representative_badges(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")

        self.assertIn('"representative_badges": representativeBadges', source)
        self.assertIn('"representative_badge_ids"', source)
        self.assertIn("func representativeBadges(from badges: [[String: Any]], dataRoot: URL? = nil) -> [[String: Any]]", source)
        self.assertIn("func writeRepresentativeBadgeSelection(badgeIds: [String], dataRoot: URL) throws", source)
        self.assertIn("func stateBySelectingRepresentativeBadges(_ state: [String: Any], badgeIds: [String]) -> [String: Any]", source)
        self.assertIn('"representative_rank"', source)

    def test_billion_club_badge_name_and_description_are_mapped_to_existing_badge_id(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")

        self.assertIn('"billion-club": "a758dd50b1415e27"', source)
        self.assertIn('badge("a758dd50b1415e27", "Billion Club"', source)
        self.assertIn('"Uses AI agents as daily working partners, not occasional search boxes."', source)
        self.assertNotIn('badge("a758dd50b1415e27", "Billion-Token Operator"', source)

    def test_two_key_agents_badge_requires_claude_and_codex_each_over_one_billion_tokens(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")

        self.assertIn('"two-agent-day": "b0aec0de4cd56059"', source)
        self.assertIn('"two-key-agents": "b0aec0de4cd56059"', source)
        self.assertIn('let tokensByAgent = tokenUsageByAgent(events: events)', source)
        self.assertIn('let claudeTokens = tokensByAgent["claude"] ?? 0', source)
        self.assertIn('let codexTokens = tokensByAgent["codex"] ?? 0', source)
        self.assertIn('badge("b0aec0de4cd56059", "Two key agents"', source)
        self.assertIn('claudeTokens >= 1_000_000_000 && codexTokens >= 1_000_000_000 ? "earned" : "in_progress"', source)
        self.assertIn('"Token usage for Claude and Codex each reaches over 1B."', source)
        self.assertNotIn('badge("b0aec0de4cd56059", "Two-Agent Day"', source)

    def test_heavy_user_badge_requires_weekly_ten_billion_combined_tokens(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")

        self.assertIn('func maxWeeklyClaudeCodexTokens(events: [[String: Any]]) -> Int', source)
        self.assertIn('let heavyUserWeeklyTokens = maxWeeklyClaudeCodexTokens(events: events)', source)
        self.assertIn('badge("8f5a9291c21f44bf", "Heavy user"', source)
        self.assertIn('heavyUserWeeklyTokens >= 10_000_000_000 ? "earned" : "in_progress"', source)
        self.assertIn('"Weekly Claude and Codex token usage reaches 10B total."', source)
        self.assertIn('"Claude and Codex weekly usage reaches 10B combined"', source)

    def test_badge_inventory_menu_disables_unearned_achievements(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        inventory_body = source.split("private func badgeInventoryItem", 1)[1].split("@objc private func setRepresentativeBadge", 1)[0]

        self.assertIn('let isEarned = text(badge["status"]) == "earned"', inventory_body)
        self.assertIn("badgeItem.isEnabled = isEarned", inventory_body)
        self.assertIn("badgeItem.action = isEarned ? #selector(setRepresentativeBadge(_:)) : nil", inventory_body)

    def test_meta_review_menu_can_do_review_from_app(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")

        self.assertIn('addDisabled("Meta Review: \\(text(metaReview["status"]))"', source)
        self.assertIn('NSMenuItem(title: "Do Meta Review"', source)
        self.assertNotIn("Mark Meta Review Done", source)
        self.assertIn("private var metaReviewInFlight = false", source)
        self.assertIn("@objc private func doMetaReview", source)
        do_meta_body = source.split("@objc private func doMetaReview", 1)[1].split("private func startMetaReviewRun", 1)[0]
        run_body = source.split("private func startMetaReviewRun", 1)[1].split("@objc private func toggleRocketScreensaver", 1)[0]
        self.assertIn("startMetaReviewRun()", do_meta_body)
        self.assertIn("stateQueue.async", run_body)
        self.assertIn("performMetaReviewState()", run_body)
        self.assertIn('meta["status"] = "running"', run_body)
        self.assertIn("let finalState = loadDisplayState(refreshUsage: false)", run_body)
        self.assertIn("clearMetaReviewNotificationPrompts", run_body)
        self.assertLess(run_body.index("stateQueue.async"), run_body.index("performMetaReviewState()"))
        self.assertNotIn("refreshMenu()", run_body)
        self.assertIn("writeMetaReviewArtifact", source)
        self.assertIn("Workflow Meta-Review", source)
        self.assertIn("Completed a meta-review from the local ai-system app surface.", source)

    def test_meta_review_ok_copy_uses_up_to_date_wording(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")

        self.assertIn('metaSubtitle.stringValue = text(meta["reason"])', source)
        self.assertIn('reason = "Meta-review is up to date."', source)
        self.assertNotIn('reason = "Meta-review is current."', source)

    def test_native_app_notifies_once_when_meta_review_is_due(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        apply_body = source.split("private func applyState", 1)[1].split("private func configureMenuPanel", 1)[0]

        self.assertIn("import UserNotifications", source)
        self.assertIn('("workflow", "Workflow reminders")', source)
        self.assertIn("notifyMetaReviewDueIfNeeded(state: renderedState)", apply_body)
        self.assertIn("func notifyMetaReviewDueIfNeeded(state: [String: Any])", source)
        self.assertIn("func metaReviewNotificationKey(_ meta: [String: Any]) -> String", source)
        self.assertIn("func clearMetaReviewNotificationPrompts(dataRoot: URL)", source)
        self.assertIn('"meta_review_prompts"', source)
        self.assertIn("UNUserNotificationCenter.current().requestAuthorization", source)
        self.assertIn("AgentBoost meta-review due", source)
        self.assertIn('notificationCategoryEnabled("workflow", dataRoot: dataRoot)', source)
        self.assertIn('private let agentboostMetaReviewNotificationCategory = "agentboost.meta-review"', source)
        self.assertIn('private let agentboostRunMetaReviewActionIdentifier = "agentboost.run-meta-review"', source)
        self.assertIn("UNUserNotificationCenterDelegate", source)
        self.assertIn("configureNotificationActions()", source)
        self.assertIn("UNNotificationAction(", source)
        self.assertIn("title: \"Run\"", source)
        self.assertIn("UNNotificationCategory(", source)
        self.assertIn("UNUserNotificationCenter.current().setNotificationCategories", source)
        self.assertIn("UNUserNotificationCenter.current().delegate = self", source)
        self.assertIn("categoryIdentifier: agentboostMetaReviewNotificationCategory", source)
        self.assertIn("content.categoryIdentifier = categoryIdentifier", source)
        self.assertIn("func userNotificationCenter(", source)
        self.assertIn("didReceive response: UNNotificationResponse", source)
        self.assertIn("response.actionIdentifier == agentboostRunMetaReviewActionIdentifier", source)
        self.assertIn("startMetaReviewFromNotification()", source)

    def test_native_menu_exposes_product_quality_privacy_controls(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")

        self.assertIn('NSMenuItem(title: "Remove Folder Access"', source)
        self.assertIn('NSMenuItem(title: "Delete Local Usage Data..."', source)
        self.assertIn('NSMenuItem(title: "Export Local Report..."', source)
        self.assertIn("@objc private func removeFolderAccess", source)
        self.assertIn("@objc private func deleteLocalUsageData", source)
        self.assertIn("@objc private func exportLocalReport", source)
        self.assertIn("UserDefaults.standard.removeObject(forKey: dataRootBookmarkKey)", source)
        self.assertIn('dataRoot.appendingPathComponent("data/ai-usage"', source)
        self.assertIn("NSAlert()", source)
        self.assertIn("NSSavePanel()", source)

    def test_native_menu_exposes_memory_monitor_alert(self):
        source = (Path.cwd() / "macos" / "agentboost" / "AgentBoostApp.swift").read_text(encoding="utf-8")
        menu_body = source.split("@objc private func refreshMenu", 1)[1].split("private func configureStatusAnimation", 1)[0]

        self.assertIn('"memory_monitor": memoryMonitor()', source)
        self.assertIn("func memoryMonitor()", source)
        self.assertIn("let thresholdPercent = 80", source)
        self.assertIn("ProcessInfo.processInfo.physicalMemory", source)
        self.assertIn("host_statistics64", source)
        self.assertIn('state["memory_monitor"]', menu_body)
        self.assertIn('"Memory Alert"', menu_body)
        self.assertIn('threshold \\(intText(memory["threshold_percent"]))%', menu_body)
        self.assertIn("Close idle AI agents before spawning more subagents.", menu_body)
        self.assertIn("JSONSerialization.data(withJSONObject: state", source)
