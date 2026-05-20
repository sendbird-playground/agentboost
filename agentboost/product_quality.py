from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
from pathlib import Path
from typing import Any, Iterable


Check = dict[str, Any]
APP_NAME = "AgentBoost"
BEAM_STATE_TIMEOUT_SECONDS = 20
PLACEHOLDER_ICON_BYTES = b"icns\x00\x00\x00\x10ic10\x00\x00\x00\x08"
BEAM_STATE_PARITY_FIELDS = {
    "app": str,
    "repo_root": str,
    "events_count": int,
    "goals_count": int,
    "source_counts": dict,
    "import_window": str,
    "xp": int,
    "level": int,
    "level_label": str,
    "level_progress": dict,
    "xp_breakdown": dict,
    "workforce_fitness_score": int,
    "rollups": dict,
    "token_activity": dict,
    "recent_token_activity": dict,
    "status_views": list,
    "agentboost_daily_7d": list,
    "network_activity": dict,
    "status_animation_activity": dict,
    "memory_monitor": dict,
    "badges": list,
    "badge_inventory": list,
    "earned_badges": list,
    "meta_review": dict,
    "new_achievements": list,
    "daily_missions": list,
    "weekly_missions": list,
    "streak": dict,
    "notification_file": str,
    "usage_refresh": dict,
    "folder_access": dict,
}


def default_agentboost_app_path(home: Path | None = None) -> Path:
    home = Path(home) if home is not None else Path.home()
    return home / "Applications" / f"{APP_NAME}.app"


def placeholder_app_icon_bytes() -> bytes:
    return PLACEHOLDER_ICON_BYTES


def product_quality_report(repo_root: Path, app_path: Path | None = None) -> dict[str, Any]:
    repo_root = Path(repo_root).expanduser().resolve()
    app_path = Path(app_path or default_agentboost_app_path()).expanduser()
    contents = app_path / "Contents"
    resources = contents / "Resources"
    info_path = contents / "Info.plist"
    executable = contents / "MacOS" / APP_NAME
    swift_source = resources / "AgentBoostApp.swift"
    checks: list[Check] = []

    checks.append(_check(app_path.exists(), "bundle.exists", "App bundle exists.", str(app_path)))
    info = _read_plist(info_path)
    checks.append(_metadata_check(info_path, info))
    checks.append(_repo_root_metadata_check(info_path, info))
    checks.append(_check(executable.exists() and os.access(executable, os.X_OK), "bundle.executable", "Executable exists and is executable.", str(executable)))
    checks.append(_beam_runtime_check(resources))
    checks.append(_beam_host_bridge_check(swift_source))
    checks.append(_privacy_manifest_check(resources / "PrivacyInfo.xcprivacy"))
    checks.append(_privacy_user_controls_check(swift_source))
    checks.append(_usage_native_refresh_check(swift_source))
    checks.append(_usage_live_status_refresh_check(swift_source))
    checks.append(_elapsed_background_motion_check(swift_source))
    checks.append(_network_animation_check(swift_source))
    checks.append(_memory_monitor_check(swift_source))
    checks.append(_entitlements_file_check(resources / "AgentBoost.entitlements"))
    icon_path = resources / "AppIcon.icns"
    checks.append(
        _check(
            icon_path.exists(),
            "icon.bundle_icon",
            "Bundle contains AppIcon.icns.",
            str(icon_path),
            "Add a production-quality macOS app icon asset.",
        )
    )
    checks.append(_final_icon_check(icon_path))
    checks.append(_external_helper_check(swift_source))
    checks.append(_prd_check(repo_root / "docs" / "prd-agentboost-vnext.md"))

    failures = [check for check in checks if check["status"] == "fail"]
    warnings = [check for check in checks if check["status"] == "warn"]
    return {
        "app": "AgentBoost",
        "app_path": str(app_path),
        "ready": not failures,
        "summary": {
            "pass": sum(1 for check in checks if check["status"] == "pass"),
            "warn": len(warnings),
            "fail": len(failures),
        },
        "checks": checks,
    }


def quality_exit_code(report: dict[str, Any]) -> int:
    return 0 if report.get("ready") else 1


def quality_check_main(argv: Iterable[str] | None = None) -> int:
    raw_args = list(argv) if argv is not None else None

    parser = argparse.ArgumentParser(description="Check AgentBoost product-quality readiness.")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--app-path", type=Path, default=default_agentboost_app_path())
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(raw_args)

    report = product_quality_report(args.repo_root, args.app_path)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_report(report)
    return quality_exit_code(report)


def _print_report(report: dict[str, Any]) -> None:
    status = "READY" if report["ready"] else "NOT READY"
    print(f"AgentBoost product quality: {status}")
    print(f"app_path={report['app_path']}")
    for check in report["checks"]:
        print(f"- {check['status'].upper()} {check['id']}: {check['message']}")
        if check.get("remediation"):
            print(f"  remediation: {check['remediation']}")


def _check(ok: bool, check_id: str, pass_message: str, evidence: str = "", remediation: str = "") -> Check:
    return {
        "id": check_id,
        "status": "pass" if ok else "fail",
        "message": pass_message if ok else remediation or pass_message,
        "evidence": evidence,
        "remediation": "" if ok else remediation,
    }


def _read_plist(path: Path) -> dict[str, Any]:
    try:
        data = plistlib.loads(path.read_bytes())
    except (OSError, plistlib.InvalidFileException):
        return {}
    return data if isinstance(data, dict) else {}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_plist_from_text(text: str) -> dict[str, Any]:
    starts = [position for marker in ("<?xml", "<plist") if (position := text.find(marker)) != -1]
    if not starts:
        return {}
    try:
        data = plistlib.loads(text[min(starts):].encode("utf-8"))
    except plistlib.InvalidFileException:
        return {}
    return data if isinstance(data, dict) else {}


def _read_codesign_entitlements_text(text: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    current_key = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[Key] "):
            current_key = stripped.removeprefix("[Key] ")
            continue
        if not current_key:
            continue
        if stripped.startswith("[Bool] "):
            payload[current_key] = stripped.removeprefix("[Bool] ") == "true"
            current_key = ""
        elif stripped.startswith("[Array]"):
            payload[current_key] = []
            current_key = ""
        elif stripped.startswith("[String] "):
            payload[current_key] = stripped.removeprefix("[String] ")
            current_key = ""
    return payload


def _entitlement_policy_gaps(payload: dict[str, Any]) -> list[str]:
    gaps = []
    if payload.get("com.apple.security.app-sandbox") is not True:
        gaps.append("App Sandbox")
    if payload.get("com.apple.security.files.user-selected.read-only") is not True:
        gaps.append("user-selected read-only file access")
    temporary_exceptions = sorted(key for key in payload if key.startswith("com.apple.security.temporary-exception."))
    if temporary_exceptions:
        gaps.append(f"temporary sandbox exception entitlements: {', '.join(temporary_exceptions)}")
    return gaps


def _entitlement_policy_remediation(ok: bool) -> str:
    if ok:
        return ""
    return "Add com.apple.security.app-sandbox=true and user-selected read-only file access, remove temporary sandbox exception entitlements, and sign the app with that file."


def _metadata_check(info_path: Path, info: dict[str, Any]) -> Check:
    required = {
        "CFBundleIdentifier",
        "CFBundleShortVersionString",
        "CFBundleVersion",
        "CFBundleExecutable",
        "CFBundleName",
        "CFBundleIconFile",
        "LSMinimumSystemVersion",
    }
    missing = sorted(key for key in required if not info.get(key))
    return {
        "id": "metadata.required_keys",
        "status": "pass" if not missing else "fail",
        "message": "Info.plist has production bundle metadata." if not missing else f"Info.plist is missing: {', '.join(missing)}",
        "evidence": str(info_path),
        "remediation": "" if not missing else "Populate all required bundle metadata before distributing the app.",
    }


def _repo_root_metadata_check(info_path: Path, info: dict[str, Any]) -> Check:
    keys = [key for key in ("AgentBoostRepoRoot",) if info.get(key)]
    ok = not keys
    return {
        "id": "architecture.repo_root_metadata",
        "status": "pass" if ok else "fail",
        "message": "Bundle does not embed local repo-root metadata." if ok else f"Bundle embeds local repo-root metadata: {', '.join(keys)}",
        "evidence": str(info_path),
        "remediation": "" if ok else "Build with --portable-profile so users grant folder access through the sandboxed picker.",
    }


def _beam_runtime_check(resources: Path) -> Check:
    manifest = resources / "AgentBoostBeamRuntime.json"
    payload = _read_json(manifest)
    entrypoint = str(payload.get("entrypoint") or "")
    entrypoint_path = resources / entrypoint if entrypoint else resources / "__missing_beam_entrypoint__"
    required = {
        "runtime": "elixir_beam",
        "mix_release": "agentboost",
        "status_item_bridge": "native_appkit_host",
        "state_contract": "agentboost_state_v1",
        "sandbox": "app_sandbox_user_selected_read_only",
        "repo_local_helper": False,
        "prompt_content_ingestion": False,
    }
    missing = [key for key, expected in required.items() if payload.get(key) != expected]
    for key in ("elixir_version", "otp_release", "entrypoint"):
        if not payload.get(key):
            missing.append(key)
    if entrypoint and not entrypoint_path.exists():
        missing.append("entrypoint_file")
    if entrypoint and entrypoint_path.exists() and not os.access(entrypoint_path, os.X_OK):
        missing.append("entrypoint_executable")
    if entrypoint and entrypoint_path.exists() and os.access(entrypoint_path, os.X_OK):
        missing.extend(f"state parity: {field}" for field in _beam_state_parity_gaps(entrypoint_path))
    ok = bool(payload) and not missing
    return {
        "id": "runtime.beam_release",
        "status": "pass" if ok else "fail",
        "message": "Bundled Elixir/BEAM Mix release contract is present." if ok else f"Bundled Elixir/BEAM runtime contract is missing: {', '.join(missing) if missing else 'manifest'}",
        "evidence": "\n".join(part for part in [str(manifest), str(entrypoint_path) if entrypoint else ""] if part),
        "remediation": "" if ok else "Bundle a signed self-contained Elixir Mix release under Contents/Resources/beam and write AgentBoostBeamRuntime.json with runtime, version, sandbox, state-contract, entrypoint, and menu-visible state parity details.",
    }


def _beam_state_parity_gaps(entrypoint: Path) -> list[str]:
    state = _beam_state(entrypoint)
    if not state:
        return ["state-json"]
    gaps: list[str] = []
    if state.get("contract") != "agentboost_state_v1":
        gaps.append("contract")
    if state.get("runtime") != "elixir_beam":
        gaps.append("runtime")
    for field, expected_type in BEAM_STATE_PARITY_FIELDS.items():
        if not isinstance(state.get(field), expected_type):
            gaps.append(field)
    return gaps


def _beam_state(entrypoint: Path) -> dict[str, Any]:
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            [str(entrypoint), "eval", 'Agentboost.CLI.main(["--state-json"])'],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, _stderr = process.communicate(timeout=BEAM_STATE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        if process is not None:
            process.kill()
            process.communicate()
        return {}
    except OSError:
        return {}
    if process.returncode != 0:
        return {}
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _beam_host_bridge_markers(source: str) -> dict[str, str]:
    return {
        "beam state loader": "func loadBeamRuntimeState(dataRoot: URL) -> [String: Any]?",
        "loadState beam first": "if let beamState = loadBeamRuntimeState(dataRoot: dataRoot)",
        "development fallback guard": "allowsDevelopmentSwiftRuntimeFallback()",
        "portable unavailable state": "beamUnavailableState(dataRoot: dataRoot)",
        "runtime manifest": "AgentBoostBeamRuntime.json",
        "bundle entrypoint": "beam/agentboost/bin/agentboost",
        "state command": 'Agentboost.CLI.main(["--state-json", "--data-root",',
        "bundle-local process": "Process()",
        "json state parse": "JSONSerialization.jsonObject",
    }


def _beam_host_bridge_ok(source: str) -> bool:
    return all(marker in source for marker in _beam_host_bridge_markers(source).values())


def _uses_forbidden_process(source: str) -> bool:
    if "Process()" not in source:
        return False
    if '"/bin/agentboost"' in source or "bin/agentboost-usage-collect" in source:
        return True
    return not _beam_host_bridge_ok(source)


def _beam_host_bridge_check(swift_source: Path) -> Check:
    try:
        source = swift_source.read_text(encoding="utf-8")
    except OSError:
        source = ""
    markers = _beam_host_bridge_markers(source)
    missing = [label for label, marker in markers.items() if marker not in source]
    ok = not missing
    return {
        "id": "runtime.beam_host_bridge",
        "status": "pass" if ok else "fail",
        "message": "Native AppKit host loads status state from the bundled BEAM runtime." if ok else f"Native AppKit host is not wired to the bundled BEAM runtime: {', '.join(missing)}",
        "evidence": str(swift_source),
        "remediation": "" if ok else "Add loadBeamRuntimeState(dataRoot:) so loadState reads AgentBoostBeamRuntime.json, launches the bundle-local Mix release entrypoint, parses the agentboost_state_v1 JSON contract, and only permits Swift product-state fallback for development bundles with repo-root metadata.",
    }


def _privacy_manifest_check(path: Path) -> Check:
    payload = _read_plist(path)
    collected = payload.get("NSPrivacyCollectedDataTypes")
    tracking_domains = payload.get("NSPrivacyTrackingDomains")
    valid = (
        bool(payload)
        and collected == []
        and payload.get("NSPrivacyTracking") is False
        and isinstance(payload.get("NSPrivacyAccessedAPITypes"), list)
        and tracking_domains == []
    )
    return {
        "id": "privacy.manifest",
        "status": "pass" if valid else "fail",
        "message": "PrivacyInfo.xcprivacy declares local-only no-collection behavior." if valid else "PrivacyInfo.xcprivacy does not match local-only no-collection behavior.",
        "evidence": str(path),
        "remediation": "" if valid else "Set no collected data, tracking false, no tracking domains, and an explicit accessed-API list under Contents/Resources.",
    }


def _privacy_user_controls_check(swift_source: Path) -> Check:
    try:
        source = swift_source.read_text(encoding="utf-8")
    except OSError:
        source = ""
    required = {
        "Refresh Usage": 'NSMenuItem(title: "Refresh Usage"',
        "Remove Folder Access": 'NSMenuItem(title: "Remove Folder Access"',
        "Delete Local Usage Data": 'NSMenuItem(title: "Delete Local Usage Data..."',
        "Export Local Report": 'NSMenuItem(title: "Export Local Report..."',
    }
    missing = [label for label, marker in required.items() if marker not in source]
    ok = not missing
    return {
        "id": "privacy.user_controls",
        "status": "pass" if ok else "fail",
        "message": "Native app exposes user-facing privacy controls." if ok else f"Missing privacy controls: {', '.join(missing)}",
        "evidence": str(swift_source),
        "remediation": "" if ok else "Add menu controls for Refresh, Remove Folder Access, Delete Local Usage Data, and Export Local Report.",
    }


def _usage_native_refresh_check(swift_source: Path) -> Check:
    try:
        source = swift_source.read_text(encoding="utf-8")
    except OSError:
        source = ""
    required = {
        "Select Claude Usage Folder": 'NSMenuItem(title: "Select Claude Usage Folder..."',
        "Select Codex Sessions Folder": 'NSMenuItem(title: "Select Codex Sessions Folder..."',
        "Refresh Usage": 'NSMenuItem(title: "Refresh Usage"',
        "native collector": "collectUsageFromSelectedAgentFolders(dataRoot:",
        "Claude parser": "func claudeUsageEvents",
        "Codex parser": "func codexUsageEvents",
        "event append": "appendUsageEvents",
    }
    missing = [label for label, marker in required.items() if marker not in source]
    ok = not missing and not _uses_forbidden_process(source) and "bin/agentboost-usage-collect" not in source
    if not ok and not missing:
        missing = ["repo helper process"]
    return {
        "id": "usage.native_refresh",
        "status": "pass" if ok else "fail",
        "message": "Native app refreshes usage from selected Claude/Codex folders without a helper process." if ok else f"Missing native usage refresh: {', '.join(missing)}",
        "evidence": str(swift_source),
        "remediation": "" if ok else "Add native Refresh Usage, Select Claude Usage Folder, and Select Codex Sessions Folder controls without launching repo helper processes.",
    }


def _usage_live_status_refresh_check(swift_source: Path) -> Check:
    try:
        source = swift_source.read_text(encoding="utf-8")
    except OSError:
        source = ""
    load_marker = "func loadState(refreshUsage: Bool = true)"
    refresh_marker = "refreshUsageIfPossible(dataRoot: dataRoot)"
    build_marker = "buildState(dataRoot: dataRoot)"
    required = {
        "refresh-capable loadState": load_marker,
        "pre-state usage refresh": refresh_marker,
        "selected-folder guard": "hasSelectedAgentUsageFolder()",
        "live Claude project tail": "claudeUsageEvents(claudeRoot: claudeRoot, importedAt: importedAt, since: cutoff)",
        "live Codex session tail": "func liveRecentUsageEvents",
        "ccusage-style Codex tail": "codexUsageEvents(codexRoot: codexRoot, importedAt: importedAt, since: cutoff)",
        "recent live source": "let recentEvents = liveRecentEvents",
        "fast live status source": "let statusEvents = recentEvents",
        "running agent state": "let runningActivity = cachedRunningAgentActivity()",
        "cached running agent state": "func cachedRunningAgentActivity() -> [String: Any]",
        "running agent animation input": "running: runningActivity",
        "immediate running agent refresh": "refreshRunningAgentAnimationState",
        "fast live state load": "let liveState = loadLiveUsageState(refreshUsage: false)",
        "cached full-state preservation": "stateByMergingLiveUsage(cachedState, liveState: liveState)",
    }
    missing = [label for label, marker in required.items() if marker not in source]
    if load_marker in source and refresh_marker in source and build_marker in source:
        load_start = source.find(load_marker)
        next_function = source.find("\nfunc activeDataRoot", load_start)
        load_body = source[load_start : next_function if next_function != -1 else len(source)]
        refresh_index = load_body.find(refresh_marker)
        build_index = load_body.find(build_marker)
        if refresh_index == -1 or build_index == -1 or refresh_index > build_index:
            missing.append("refresh before buildState")
    ok = not missing and not _uses_forbidden_process(source) and "bin/agentboost-usage-collect" not in source
    if not ok and not missing:
        missing = ["repo helper process"]
    return {
        "id": "usage.live_status_refresh",
        "status": "pass" if ok else "fail",
        "message": "Status animation refreshes native usage before computing activity." if ok else f"Status animation can read stale usage: {', '.join(missing)}",
        "evidence": str(swift_source),
        "remediation": "" if ok else "Call refreshUsageIfPossible(dataRoot:) from loadState before buildState, merge live Claude project and ccusage-style Codex session tails into recent/status activity, and apply a fast live state before slower ledger refreshes so active agent sessions can drive rocket motion without waiting for a manual menu refresh.",
    }


def _elapsed_background_motion_check(swift_source: Path) -> Check:
    try:
        source = swift_source.read_text(encoding="utf-8")
    except OSError:
        source = ""
    required = {
        "animation time base": "private var animationStartedAt = Date()",
        "elapsed progress": "Date().timeIntervalSince(animationStartedAt)",
        "progress helper": "currentBackgroundProgress()",
        "centered rocket": "let rocketPoints = rocketCenterPoints(centerX: bounds.midX, centerY: animationMidY)",
        "rocket-only status draw": "drawRocket(at: rocketPoint, scale: rocketScale, agent: agent)",
        "plain token text below rocket": "drawTokenText(text: currentTokenText(), below: tokenPoint)",
        "plain token text helper": "private func drawTokenText(text: String, below point: NSPoint)",
        "two-agent split rocket": "private func rocketCenterPoints",
        "active agent contract": 'activeAgents = textArray(activity["active_agents"])',
        "requested rocket count contract": 'let requestedRocketCount = max(1, min(2, tokenInt(activity["rocket_count"])))',
        "active-agent rocket count override": "rocketCount = activeAgents.count >= 2 ? 2 : requestedRocketCount",
        "floating active agent contract": "private(set) var activeAgents: [String] = []",
        "floating rocket count contract": "private(set) var rocketCount = 1",
        "floating per-agent motion": "private(set) var agentRocketMotion: [String: AgentRocketMotion] = [:]",
        "floating agent usage": 'agentUsageByAgent = normalizedAgentUsage(activity["agent_usage"])',
        "floating per-agent speed": "private func agentRocketSpeed(_ agent: String) -> CGFloat",
        "floating agent draw states": "let rocketStates = motionState.rocketDrawStates()",
        "floating agent rocket draw": "drawRocket(at: localRocketPosition, scale: 1.9, agent: rocketState.agent, headingDegrees: rocketState.headingDegrees, glow: rocketState.glowIntensity)",
        "floating agent token text": "drawTokenText(text: motionState.currentTokenText(for: rocketState.agent), below: localRocketPosition)",
        "today active agent contract": 'let activeAgents = activeAgentsFromRollup(rollups["Today"])',
        "today split carryover": "let activeAgents = mergedActiveAgents(today, running)",
        "today rocket count carryover": 'let rocketCount = activeAgents.isEmpty ? max(1, min(2, tokenInt(today["rocket_count"]))) : rocketCountForAgents(activeAgents)',
    }
    missing = [label for label, marker in required.items() if marker not in source]
    forbidden = {
        "background track": "drawMovingBackground(",
        "token badge background": "drawTokenBadge",
        "rounded token badge": "roundedRect",
        "flame triangle": "let flame = NSBezierPath()",
        "ping-pong sine phase": "sin(currentBackgroundProgress() * CGFloat.pi * 2) * (spacing / 2)",
    }
    rocket_source = _rocket_animation_source(source)
    missing.extend(label for label, marker in forbidden.items() if marker in rocket_source)
    ok = not missing and "RunLoop.main.add(timer, forMode: .common)" in source
    if not ok and not missing:
        missing = ["common run loop timer"]
    return {
        "id": "animation.elapsed_background_motion",
        "status": "pass" if ok else "fail",
        "message": "Rocket-only animation uses elapsed-time motion with plain token text below the rocket." if ok else f"Rocket animation contract is not rocket-only: {', '.join(missing)}",
        "evidence": str(swift_source),
        "remediation": "" if ok else "Use elapsed wall-clock time for rocket bob/rotation, keep the rocket formation centered, render token text directly below the rocket, remove badge backgrounds, flame triangles, and background or connector lines, and run the redraw timer in common run loop modes.",
    }


def _rocket_animation_source(source: str) -> str:
    sections: list[str] = []
    if "final class RocketStatusView" in source and "final class RocketScreensaverView" in source:
        sections.append(source.split("final class RocketStatusView", 1)[1].split("final class RocketScreensaverView", 1)[0])
    if "final class RocketScreensaverView" in source:
        overlay = source.split("final class RocketScreensaverView", 1)[1]
        for boundary in ("private final class AgentBoostSparklineView", "final class AppDelegate"):
            if boundary in overlay:
                overlay = overlay.split(boundary, 1)[0]
                break
        sections.append(overlay)
    return "\n".join(sections) if sections else source


def _memory_monitor_check(swift_source: Path) -> Check:
    try:
        source = swift_source.read_text(encoding="utf-8")
    except OSError:
        source = ""
    required = {
        "memory state": '"memory_monitor": memoryMonitor()',
        "memory helper": "func memoryMonitor()",
        "80% threshold": "let thresholdPercent = 80",
        "system total memory": "ProcessInfo.processInfo.physicalMemory",
        "system memory stats": "host_statistics64",
        "menu alert": '"Memory Alert"',
        "subagent guidance": "Close idle AI agents before spawning more subagents.",
    }
    missing = [label for label, marker in required.items() if marker not in source]
    ok = not missing
    return {
        "id": "memory.monitor",
        "status": "pass" if ok else "fail",
        "message": "Native app exposes local system memory monitoring with an 80% alert." if ok else f"Missing memory monitoring contract: {', '.join(missing)}",
        "evidence": str(swift_source),
        "remediation": "" if ok else "Expose memory_monitor state, read local system memory natively, and show an 80% menu alert that tells the user to close idle AI agents before spawning more subagents.",
    }


def _network_animation_check(swift_source: Path) -> Check:
    try:
        source = swift_source.read_text(encoding="utf-8")
    except OSError:
        source = ""
    required = {
        "network state": '"network_activity": networkActivityState',
        "network helper": "func networkActivity()",
        "outbound helper": "func outboundNetworkBytes()",
        "native interface counters": "getifaddrs",
        "interface byte counters": "if_data",
        "network animation input": '"status_animation_activity": statusAnimationActivity(recent: recentActivity, today: todayActivity, network: networkActivityState, running: runningActivity)',
        "network speed source": 'result["speed_source"] = "network"',
        "network speed override": 'result["rocket_speed"] = networkSpeed',
        "token display preserved": "display_tokens",
        "renderer plain token text": "drawTokenText(text:",
        "renderer token display state": 'displayTokens = text(activity["display_tokens"])',
        "emoji rocket rendering": 'let rocketEmoji = "🚀"',
    }
    missing = [label for label, marker in required.items() if marker not in source]
    forbidden = [marker for marker in ("nettop", "tcpdump") if marker in source]
    if _uses_forbidden_process(source):
        forbidden.append("Process()")
    ok = not missing and not forbidden
    if forbidden:
        missing.extend(f"forbidden helper: {marker}" for marker in forbidden)
    return {
        "id": "animation.network_speed",
        "status": "pass" if ok else "fail",
        "message": "Animation speed is driven by local outbound network activity while token display remains on the usage pipeline and the native animation renders token usage." if ok else f"Missing network-speed animation contract: {', '.join(missing)}",
        "evidence": str(swift_source),
        "remediation": "" if ok else "Add local outbound network traffic sampling through native interface counters, use it only for animation speed, keep token totals/display on the existing usage pipeline, and draw the display token value inside the native animation.",
    }


def _entitlements_file_check(path: Path) -> Check:
    payload = _read_plist(path)
    gaps = _entitlement_policy_gaps(payload)
    ok = not gaps
    return {
        "id": "sandbox.entitlements_file",
        "status": "pass" if ok else "fail",
        "message": "Sandbox entitlement file enables App Sandbox with user-selected read-only file access and no temporary exceptions." if ok else f"Sandbox entitlement file is not production-safe: {', '.join(gaps)}.",
        "evidence": str(path),
        "remediation": _entitlement_policy_remediation(ok),
    }


def _final_icon_check(path: Path) -> Check:
    try:
        icon_bytes = path.read_bytes()
    except OSError:
        icon_bytes = b""
    final_icon = bool(icon_bytes) and icon_bytes.startswith(b"icns") and icon_bytes != placeholder_app_icon_bytes()
    return {
        "id": "icon.final_art",
        "status": "pass" if final_icon else "fail",
        "message": "App icon is not the generated placeholder." if final_icon else "App icon is still the generated placeholder.",
        "evidence": str(path),
        "remediation": "" if final_icon else "Replace the generated placeholder with final product icon artwork.",
    }


def _external_helper_check(swift_source: Path) -> Check:
    try:
        source = swift_source.read_text(encoding="utf-8")
    except OSError:
        source = ""
    uses_repo_helper = '"/bin/agentboost"' in source or _uses_forbidden_process(source)
    return {
        "id": "architecture.external_helper",
        "status": "fail" if uses_repo_helper else "pass",
        "message": "Native app still depends on a repo-local helper and local repo root." if uses_repo_helper else "Native app is self-contained for production use.",
        "evidence": str(swift_source),
        "remediation": "" if not uses_repo_helper else "Replace repo-local helper execution with bundled sandboxed code and user-selected Claude/Codex data folders.",
    }


def _prd_check(path: Path) -> Check:
    exists = path.exists()
    return {
        "id": "prd.product",
        "status": "pass" if exists else "fail",
        "message": "Product PRD exists." if exists else "Product PRD is missing.",
        "evidence": str(path),
        "remediation": "" if exists else "Create docs/prd-agentboost-vnext.md with product goals, acceptance criteria, privacy, and quality gates.",
    }


if __name__ == "__main__":
    raise SystemExit(quality_check_main())
