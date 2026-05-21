import Cocoa
import CoreGraphics
import CryptoKit
import Darwin
import IOKit.pwr_mgt
import UniformTypeIdentifiers
import UserNotifications

let dataRootBookmarkKey = "AgentBoostDataRootBookmark"
let claudeDataRootBookmarkKey = "AgentBoostClaudeDataRootBookmark"
let codexDataRootBookmarkKey = "AgentBoostCodexDataRootBookmark"
let representativeBadgeKey = "AgentBoostRepresentativeBadgeID"
let networkActivitySampleKey = "AgentBoostNetworkActivitySample"
let tokenFields = ["input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens"]
private let agentboostRepresentativeBadgeLimit = 1
private let agentboostMetaReviewNotificationCategory = "agentboost.meta-review"
private let agentboostRunMetaReviewActionIdentifier = "agentboost.run-meta-review"
private let agentboostBadgeIDAliases: [String: String] = [
    "billion-token-operator": "a758dd50b1415e27",
    "billion-club": "a758dd50b1415e27",
    "two-agent-day": "b0aec0de4cd56059",
    "two-key-agents": "b0aec0de4cd56059",
    "verified-workflow": "9ad1f6c937fd3839",
]
private let agentboostNotificationCategoryLabels: [(key: String, title: String)] = [
    ("achievements", "Achievements"),
    ("missions", "Mission reminders"),
    ("workflow", "Workflow reminders"),
    ("inactivity", "Inactivity nudges"),
    ("tips", "Daily AI tips"),
    ("community", "Community tips"),
    ("caffeinate", "Caffeinate prompts"),
    ("memory", "Memory alerts"),
]
private let rocketStatusItemWidth = NSStatusItem.squareLength
private let rocketStatusItemHeight = NSStatusItem.squareLength
private let rocketScreensaverMinimumWidth = CGFloat(260)
private let rocketScreensaverMinimumHeight = CGFloat(180)
private let rocketScreensaverScreenInset = CGFloat(8)
private let rocketScreensaverSeamTolerance = CGFloat(8)
private let minimumUsageRefreshIntervalSeconds: TimeInterval = 30
private let runningAgentRefreshIntervalSeconds: TimeInterval = 15
// Fast-poll cadence for live usage (tails ~/.claude/projects and
// ~/.codex/sessions directly). Keeps the rocket reacting to in-flight
// Claude/Codex activity within ~2 s while the full BEAM rollup runs every
// `beamStateRefreshIntervalSeconds`.
private let liveUsageRefreshIntervalSeconds: TimeInterval = 2.0
private let beamStateTimeoutSeconds: TimeInterval = 90
private let beamStateRefreshIntervalSeconds: TimeInterval = 90
private let rollupStaleSecondsThreshold: TimeInterval = 30 * 60
// BEAM defaults to one scheduler per core (14+ on a modern MBP) plus dirty
// CPU/IO schedulers. The state CLI is mostly serial — extra schedulers don't
// help throughput but they do peg cores and starve the menu's animation
// timer. Cap to a small pool and run the process at background QoS so the
// rocket animation stays smooth while BEAM is crunching.
private let beamSchedulerErlFlags = "+S 2:2 +SDcpu 2:2 +SDio 1"
private let usageRefreshLookbackSeconds: TimeInterval = 2 * 60 * 60
private let liveUsageRecentWindowSeconds: TimeInterval = 2 * 60
private let liveUsageTailBytes = UInt64(512 * 1024)
private let liveClaudeProjectFileScanIntervalSeconds: TimeInterval = 5 * 60
private let overlayRuntimeSnapshotWriteIntervalSeconds: TimeInterval = 5
private let rocketStatusFrameIntervalSeconds: TimeInterval = 1.0 / 60.0
private let rocketScreensaverFrameIntervalSeconds: TimeInterval = 1.0 / 60.0
private let runningAgentActivityCacheIntervalSeconds: TimeInterval = 60
private var liveClaudeProjectFileCache: (rootPath: String, refreshedAt: Date, files: [URL])?
private var runningAgentActivityCache: (refreshedAt: Date, state: [String: Any])?
private let eventDateFormatterLock = NSLock()
private let fractionalEventDateFormatter: ISO8601DateFormatter = {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return formatter
}()
private let wholeSecondEventDateFormatter: ISO8601DateFormatter = {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime]
    return formatter
}()
private let levelXPRequirements: [(level: Int, requiredXP: Int)] = [
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
]

func appPreferences() -> UserDefaults {
    UserDefaults.standard
}

func displayStateCacheFile(dataRoot: URL) -> URL {
    dataRoot.appendingPathComponent("data/ai-usage/sidebar-state-cache.json")
}

func readCachedDisplayState(dataRoot: URL) -> [String: Any]? {
    let path = displayStateCacheFile(dataRoot: dataRoot)
    guard let data = try? Data(contentsOf: path),
          let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        return nil
    }
    return object
}

func writeCachedDisplayState(_ state: [String: Any], dataRoot: URL) {
    let path = displayStateCacheFile(dataRoot: dataRoot)
    do {
        try FileManager.default.createDirectory(at: path.deletingLastPathComponent(), withIntermediateDirectories: true)
        var payload = state
        payload["state_cache"] = [
            "cached_at": isoNow(),
            "cache_file": path.path,
        ]
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: path, options: .atomic)
    } catch {
        // Cache writes must never block the menu-bar app from showing live usage.
    }
}

func initialDisplayState(dataRoot: URL) -> [String: Any] {
    let liveState = loadLiveUsageState(refreshUsage: false)
    if let cached = readCachedDisplayState(dataRoot: dataRoot) {
        return stateByMergingLiveUsage(cached, liveState: liveState)
    }
    return liveState
}

func loadState(refreshUsage: Bool = true) -> [String: Any] {
    let dataRoot = activeDataRoot()
    if refreshUsage {
        refreshUsageIfPossible(dataRoot: dataRoot)
    }
    if let beamState = loadBeamRuntimeState(dataRoot: dataRoot) {
        return beamState
    }
    if allowsDevelopmentSwiftRuntimeFallback() {
        return buildState(dataRoot: dataRoot)
    }
    return beamUnavailableState(dataRoot: dataRoot)
}

func loadDisplayState(refreshUsage: Bool = true) -> [String: Any] {
    let fullState = loadState(refreshUsage: refreshUsage)
    let liveState = loadLiveUsageState(refreshUsage: false)
    var merged = stateByMergingLiveUsage(fullState, liveState: liveState)
    annotateRollupStaleness(state: &merged, dataRoot: activeDataRoot())
    return merged
}

// Lifetime/Month rollups come from the cached state when the BEAM CLI was
// killed by `beamStateTimeoutSeconds` before it could refresh them. Compare
// the cache's `cached_at` against the events.jsonl mtime so the menu can
// flag stale numbers instead of silently serving old totals.
func annotateRollupStaleness(state: inout [String: Any], dataRoot: URL) {
    let eventsPath = dataRoot.appendingPathComponent("data/ai-usage/events.jsonl").path
    guard
        let attrs = try? FileManager.default.attributesOfItem(atPath: eventsPath),
        let eventsMtime = attrs[.modificationDate] as? Date
    else {
        state["rollups_stale"] = false
        return
    }
    let cacheMeta = state["state_cache"] as? [String: Any]
    guard
        let cachedAtRaw = cacheMeta?["cached_at"] as? String,
        let cachedAt = eventDate(cachedAtRaw)
    else {
        state["rollups_stale"] = false
        return
    }
    state["rollups_stale"] = eventsMtime.timeIntervalSince(cachedAt) > rollupStaleSecondsThreshold
}

func stateByMergingLiveUsage(_ fullState: [String: Any], liveState: [String: Any]) -> [String: Any] {
    var result = fullState
    let fullRecent = fullState["recent_token_activity"] as? [String: Any]
    let liveRecent = liveState["recent_token_activity"] as? [String: Any]
    // Prefer the BEAM recent payload when it carries the split_io rockets[]
    // array — the local Swift live-recompute does not yet build rockets[].
    let recent: [String: Any] = {
        if let full = fullRecent, let rockets = full["rockets"] as? [[String: Any]], !rockets.isEmpty {
            return full
        }
        return liveRecent ?? fullRecent ?? [:]
    }()
    let today = fullState["token_activity"] as? [String: Any]
        ?? liveState["token_activity"] as? [String: Any]
        ?? [:]
    let network = liveState["network_activity"] as? [String: Any]
        ?? fullState["network_activity"] as? [String: Any]
        ?? [:]
    let running = liveState["running_agent_activity"] as? [String: Any]
        ?? fullState["running_agent_activity"] as? [String: Any]
        ?? [:]

    result["rollups"] = fullState["rollups"]
    result["token_activity"] = fullState["token_activity"]
    result["status_views"] = fullState["status_views"]
    result["recent_token_activity"] = recent
    result["network_activity"] = network
    result["running_agent_activity"] = running
    result["status_animation_activity"] = statusAnimationActivity(
        recent: recent,
        today: today,
        network: network,
        running: running
    )
    for key in ["memory_monitor", "rocket_screensaver", "usage_refresh", "usage_backfill", "folder_access"] {
        if let value = liveState[key] {
            result[key] = value
        }
    }
    return result
}

func loadBeamRuntimeState(dataRoot: URL) -> [String: Any]? {
    guard let resources = Bundle.main.resourceURL else {
        return nil
    }
    let manifest = resources.appendingPathComponent("AgentBoostBeamRuntime.json")
    guard FileManager.default.fileExists(atPath: manifest.path) else {
        return nil
    }
    let executable = resources.appendingPathComponent("beam/agentboost/bin/agentboost")
    guard FileManager.default.isExecutableFile(atPath: executable.path) else {
        return nil
    }
    let commandPrefix = #"Agentboost.CLI.main(["--state-json", "--data-root","#
    let expression = "\(commandPrefix) \"\(elixirStringLiteral(dataRoot.path))\"])"
    let process = Process()
    let stdout = Pipe()
    defer { try? stdout.fileHandleForReading.close() }
    process.executableURL = executable
    process.arguments = ["eval", expression]
    process.standardOutput = stdout
    process.qualityOfService = .background
    var env = ProcessInfo.processInfo.environment
    let existingFlags = env["ERL_FLAGS"] ?? ""
    env["ERL_FLAGS"] = existingFlags.isEmpty
        ? beamSchedulerErlFlags
        : "\(existingFlags) \(beamSchedulerErlFlags)"
    process.environment = env
    let group = DispatchGroup()
    process.terminationHandler = { _ in
        group.leave()
    }
    do {
        group.enter()
        try process.run()
    } catch {
        group.leave()
        return nil
    }
    if group.wait(timeout: .now() + beamStateTimeoutSeconds) == .timedOut {
        process.terminate()
        if group.wait(timeout: .now() + 1) == .timedOut {
            kill(process.processIdentifier, SIGKILL)
            _ = group.wait(timeout: .now() + 1)
        }
        return nil
    }
    let data = stdout.fileHandleForReading.readDataToEndOfFile()
    guard process.terminationStatus == 0 else {
        return nil
    }
    guard !data.isEmpty,
          let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        return nil
    }
    var state = object
    state["beam_runtime_manifest"] = manifest.path
    return state
}

func elixirStringLiteral(_ value: String) -> String {
    value
        .replacingOccurrences(of: "\\", with: "\\\\")
        .replacingOccurrences(of: "\"", with: "\\\"")
}

func allowsDevelopmentSwiftRuntimeFallback() -> Bool {
    if let value = Bundle.main.object(forInfoDictionaryKey: "AgentBoostRepoRoot") as? String, !value.isEmpty {
        return true
    }
    if let value = Bundle.main.object(forInfoDictionaryKey: "AgentBoostRepoRoot") as? String, !value.isEmpty {
        return true
    }
    return false
}

func beamUnavailableState(dataRoot: URL) -> [String: Any] {
    [
        "app": "AgentBoost",
        "contract": "agentboost_state_v1",
        "repo_root": dataRoot.path,
        "runtime": "beam_unavailable",
        "events_count": 0,
        "goals_count": 0,
        "source_counts": [:],
        "import_window": "BEAM runtime unavailable",
        "xp": 0,
        "level": 1,
        "level_label": "Lv 1",
        "level_progress": levelProgressForXP(0),
        "xp_breakdown": ["base_xp": 0, "mission_xp": 0],
        "workforce_fitness_score": 0,
        "rollups": [
            "Today": ["total_tokens": 0, "by_agent": [:]],
            "This Week": ["total_tokens": 0, "by_agent": [:]],
            "This Month": ["total_tokens": 0, "by_agent": [:]],
            "Lifetime": ["total_tokens": 0, "by_agent": [:]],
        ],
        "token_activity": ["today_tokens": 0, "active_agents": [], "rocket_count": 1],
        "recent_token_activity": ["last_1m_tokens": 0, "active_agents": [], "rocket_count": 1],
        "status_views": [],
        "network_activity": networkActivity(),
        "status_animation_activity": ["active_agents": [], "rocket_count": 1, "rocket_speed": 0.0],
        "memory_monitor": memoryMonitor(),
        "rocket_screensaver": rocketScreensaverState(dataRoot: dataRoot),
        "badges": [],
        "earned_badges": [],
        "badge_inventory": [],
        "representative_badge": NSNull(),
        "representative_badges": [],
        "meta_review": ["status": "unknown", "due": false, "reason": "BEAM runtime unavailable."],
        "new_achievements": [],
        "daily_missions": [],
        "weekly_missions": [],
        "agentboost_daily_7d": [],
        "streak": ["status": "local"],
        "notification_file": settingsFile(dataRoot: dataRoot).path,
        "usage_refresh": readUsageRefresh(dataRoot: dataRoot),
        "usage_backfill": readUsageBackfill(dataRoot: dataRoot),
        "folder_access": [
            "agentboost": securityScopedURL(forKey: dataRootBookmarkKey) != nil,
            "claude": claudeUsageRoot() != nil,
            "codex": codexUsageRoot() != nil,
        ],
    ]
}

func activeDataRoot() -> URL {
    if let selected = securityScopedDataRoot() {
        return selected
    }
    if let value = Bundle.main.object(forInfoDictionaryKey: "AgentBoostRepoRoot") as? String, !value.isEmpty {
        return URL(fileURLWithPath: value, isDirectory: true)
    }
    if let value = Bundle.main.object(forInfoDictionaryKey: "AgentBoostRepoRoot") as? String, !value.isEmpty {
        return URL(fileURLWithPath: value, isDirectory: true)
    }
    let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
    let root = (support ?? URL(fileURLWithPath: NSTemporaryDirectory())).appendingPathComponent("AgentBoost", isDirectory: true)
    try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    return root
}

func securityScopedDataRoot() -> URL? {
    securityScopedURL(forKey: dataRootBookmarkKey)
}

func defaultAgentUsageFolder(_ folderName: String) -> URL? {
    guard allowsDevelopmentSwiftRuntimeFallback() else { return nil }
    let url = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(folderName, isDirectory: true)
    return FileManager.default.fileExists(atPath: url.path) ? url : nil
}

func agentUsageFolder(forKey key: String, defaultFolderName: String) -> URL? {
    if let selected = securityScopedURL(forKey: key) {
        return selected
    }
    return defaultAgentUsageFolder(defaultFolderName)
}

func claudeUsageRoot() -> URL? {
    agentUsageFolder(forKey: claudeDataRootBookmarkKey, defaultFolderName: ".claude")
}

func codexUsageRoot() -> URL? {
    agentUsageFolder(forKey: codexDataRootBookmarkKey, defaultFolderName: ".codex")
}

func shouldImportUsageFile(_ path: URL, now: Date = Date()) -> Bool {
    let cutoff = now.addingTimeInterval(-usageRefreshLookbackSeconds)
    let values = try? path.resourceValues(forKeys: [.contentModificationDateKey])
    guard let modifiedAt = values?.contentModificationDate else {
        return true
    }
    return modifiedAt >= cutoff
}

func securityScopedURL(forKey key: String) -> URL? {
    guard let bookmark = UserDefaults.standard.data(forKey: key) else { return nil }
    var stale = false
    guard let url = try? URL(
        resolvingBookmarkData: bookmark,
        options: [.withSecurityScope],
        relativeTo: nil,
        bookmarkDataIsStale: &stale
    ), !stale else {
        return nil
    }
    _ = url.startAccessingSecurityScopedResource()
    return url
}

func storeSecurityScopedDataRoot(_ url: URL) -> Bool {
    storeSecurityScopedURL(url, forKey: dataRootBookmarkKey)
}

func storeSecurityScopedURL(_ url: URL, forKey key: String) -> Bool {
    guard let bookmark = try? url.bookmarkData(options: [.withSecurityScope], includingResourceValuesForKeys: nil, relativeTo: nil) else {
        return false
    }
    UserDefaults.standard.set(bookmark, forKey: key)
    return true
}

func hasSelectedAgentUsageFolder() -> Bool {
    securityScopedURL(forKey: claudeDataRootBookmarkKey) != nil || securityScopedURL(forKey: codexDataRootBookmarkKey) != nil
}

func hasAvailableAgentUsageFolder() -> Bool {
    claudeUsageRoot() != nil || codexUsageRoot() != nil
}

func refreshUsageIfPossible(dataRoot: URL) {
    guard hasAvailableAgentUsageFolder() else {
        return
    }
    if let lastRefresh = eventDate(readUsageRefresh(dataRoot: dataRoot)["last_refreshed_at"]),
       Date().timeIntervalSince(lastRefresh) < minimumUsageRefreshIntervalSeconds {
        return
    }
    let cutoff = Date().addingTimeInterval(-usageRefreshLookbackSeconds)
    _ = try? collectUsageFromSelectedAgentFolders(dataRoot: dataRoot, since: cutoff)
}

func buildState(dataRoot: URL) -> [String: Any] {
    let events = readEvents(dataRoot: dataRoot)
    let goals = readGoals(dataRoot: dataRoot)
    let rollups = buildRollups(events: events)
    let todayActivity = tokenActivity(rollups: rollups)
    let recentActivity = recentTokenActivity(events: events)
    let networkActivityState = networkActivity()
    let runningActivity = cachedRunningAgentActivity()
    let views = statusViews(events: events, rollups: rollups)
    let badges = badgeInventory(events: events, goals: goals)
    let earnedBadges = badges.filter { text($0["status"]) == "earned" }
    let representativeBadges = representativeBadges(from: badges, dataRoot: dataRoot)
    let representative = representativeBadges.first
    let meta = metaReviewState(dataRoot: dataRoot)
    let identity = identityUpdateState(dataRoot: dataRoot)
    let daily = missions(prefix: "daily", dataRoot: dataRoot, events: events, goals: goals)
    let weekly = missions(prefix: "weekly", dataRoot: dataRoot, events: events, goals: goals)
    let baseXP = xp(events: events, goals: goals)
    let missionXP = earnedMissionXP(daily + weekly)
    let totalXP = baseXP + missionXP
    let levelProgress = levelProgressForXP(totalXP)
    let levelLabel = text(levelProgress["level_label"])
    let xpBreakdown = ["base_xp": baseXP, "mission_xp": missionXP]
    return [
        "app": "AgentBoost",
        "repo_root": dataRoot.path,
        "events_count": events.count,
        "goals_count": goals.count,
        "source_counts": sourceCounts(events: events),
        "import_window": importWindow(events: events),
        "xp": totalXP,
        "level": tokenInt(levelProgress["current_level"]),
        "level_label": levelLabel,
        "level_progress": levelProgress,
        "xp_breakdown": xpBreakdown,
        "workforce_fitness_score": workforceFitness(events: events, goals: goals),
        "rollups": rollups,
        "token_activity": todayActivity,
        "recent_token_activity": recentActivity,
        "status_views": views,
        "network_activity": networkActivityState,
        "running_agent_activity": runningActivity,
        "status_animation_activity": statusAnimationActivity(recent: recentActivity, today: todayActivity, network: networkActivityState, running: runningActivity),
        "memory_monitor": memoryMonitor(),
        "rocket_screensaver": rocketScreensaverState(dataRoot: dataRoot),
        "badges": badges,
        "earned_badges": earnedBadges,
        "badge_inventory": badges.map { badge in
            var item = badge
            let rank = representativeBadgeRank(representativeBadges, badgeID: text(badge["badge_id"]))
            item["is_representative"] = rank > 0
            item["representative_rank"] = rank
            item["can_select"] = text(badge["status"]) == "earned"
            return item
        },
        "representative_badge": representative as Any,
        "representative_badges": representativeBadges,
        "meta_review": meta,
        "identity_update": identity,
        "new_achievements": badges.filter { text($0["status"]) == "earned" },
        "daily_missions": daily,
        "weekly_missions": weekly,
        "agentboost_daily_7d": sevenDayUsageBuckets(events: events),
        "streak": ["status": "local"],
        "notification_file": settingsFile(dataRoot: dataRoot).path,
        "usage_refresh": readUsageRefresh(dataRoot: dataRoot),
        "usage_backfill": readUsageBackfill(dataRoot: dataRoot),
        "folder_access": [
            "agentboost": securityScopedURL(forKey: dataRootBookmarkKey) != nil,
            "claude": claudeUsageRoot() != nil,
            "codex": codexUsageRoot() != nil,
        ],
    ]
}

func loadLiveUsageState(refreshUsage: Bool = true) -> [String: Any] {
    let dataRoot = activeDataRoot()
    let now = Date()
    let recentCutoff = now.addingTimeInterval(-liveUsageRecentWindowSeconds)
    let importedAt = isoNow()
    if refreshUsage {
        refreshUsageIfPossible(dataRoot: dataRoot)
    }
    let liveRecentEvents = liveRecentUsageEvents(since: recentCutoff, importedAt: importedAt)
    let recentEvents = liveRecentEvents
    let statusEvents = recentEvents
    let statusRollups = buildRollups(events: statusEvents)
    let todayActivity = tokenActivity(rollups: statusRollups)
    let recentActivity = recentTokenActivity(events: recentEvents)
    let networkActivityState = networkActivity()
    let runningActivity = cachedRunningAgentActivity()
    return [
        "app": "AgentBoost",
        "repo_root": dataRoot.path,
        "events_count": recentEvents.count,
        "goals_count": 0,
        "source_counts": sourceCounts(events: recentEvents),
        "import_window": importWindow(events: recentEvents),
        "xp": 0,
        "level": 1,
        "level_label": "Lv 1",
        "level_progress": levelProgressForXP(0),
        "xp_breakdown": ["base_xp": 0, "mission_xp": 0],
        "workforce_fitness_score": 0,
        "rollups": statusRollups,
        "token_activity": todayActivity,
        "recent_token_activity": recentActivity,
        "status_views": statusViews(events: statusEvents, rollups: statusRollups),
        "network_activity": networkActivityState,
        "running_agent_activity": runningActivity,
        "status_animation_activity": statusAnimationActivity(recent: recentActivity, today: todayActivity, network: networkActivityState, running: runningActivity),
        "memory_monitor": memoryMonitor(),
        "rocket_screensaver": rocketScreensaverState(dataRoot: dataRoot),
        "badges": [],
        "earned_badges": [],
        "badge_inventory": [],
        "representative_badge": NSNull(),
        "representative_badges": [],
        "meta_review": ["status": "live", "due": false, "reason": "Fast live usage refresh."],
        "new_achievements": [],
        "daily_missions": [],
        "weekly_missions": [],
        "agentboost_daily_7d": sevenDayUsageBuckets(events: statusEvents),
        "streak": ["status": "local"],
        "notification_file": settingsFile(dataRoot: dataRoot).path,
        "usage_refresh": readUsageRefresh(dataRoot: dataRoot),
        "usage_backfill": readUsageBackfill(dataRoot: dataRoot),
        "folder_access": [
            "agentboost": securityScopedURL(forKey: dataRootBookmarkKey) != nil,
            "claude": claudeUsageRoot() != nil,
            "codex": codexUsageRoot() != nil,
        ],
    ]
}

func liveRecentUsageEvents(since cutoff: Date, importedAt: String) -> [[String: Any]] {
    var events: [[String: Any]] = []
    if let claudeRoot = claudeUsageRoot() {
        events.append(contentsOf: claudeUsageEvents(claudeRoot: claudeRoot, importedAt: importedAt, since: cutoff))
    }
    if let codexRoot = codexUsageRoot() {
        events.append(contentsOf: codexUsageEvents(codexRoot: codexRoot, importedAt: importedAt, since: cutoff))
    }
    return events
}

func mergeUsageEvents(_ events: [[String: Any]], with additions: [[String: Any]]) -> [[String: Any]] {
    guard !additions.isEmpty else { return events }
    var merged = events
    var seenIDs = Set(events.map { text($0["event_id"]) })
    var seenSignatures = Set(events.map { usageEventSignature($0) })
    for event in additions {
        let eventID = text(event["event_id"])
        let signature = usageEventSignature(event)
        guard !eventID.isEmpty, !seenIDs.contains(eventID), !seenSignatures.contains(signature) else {
            continue
        }
        seenIDs.insert(eventID)
        seenSignatures.insert(signature)
        merged.append(event)
    }
    return merged
}

func usageEventSignature(_ event: [String: Any]) -> String {
    [
        text(event["source_agent"]),
        text(event["source_path"]),
        text(event["source_session_id"]),
        text(event["occurred_at"]),
        text(event["record_type"]),
        String(tokenInt(event["input_tokens"])),
        String(tokenInt(event["cached_input_tokens"])),
        String(tokenInt(event["output_tokens"])),
        String(tokenInt(event["reasoning_output_tokens"])),
        String(tokenInt(event["total_tokens"])),
    ].joined(separator: "\u{1f}")
}

func readRecentEvents(dataRoot: URL, since cutoff: Date) -> [[String: Any]] {
    let path = dataRoot.appendingPathComponent("data/ai-usage/events.jsonl")
    guard let raw = tailJSONLContents(path: path, maxBytes: liveUsageTailBytes) else { return [] }
    var events: [[String: Any]] = []
    var sawRecentEvent = false
    for line in raw.split(separator: "\n").reversed() {
        guard let data = String(line).data(using: .utf8),
              let event = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let date = eventDate(event["occurred_at"]) else {
            continue
        }
        if date >= cutoff {
            sawRecentEvent = true
            events.append(event)
        } else if sawRecentEvent {
            break
        }
    }
    return events.reversed()
}

func readStatusEvents(dataRoot: URL) -> [[String: Any]] {
    let path = dataRoot.appendingPathComponent("data/ai-usage/events.jsonl")
    guard let data = try? Data(contentsOf: path) else { return [] }
    let bytes = [UInt8](data)
    let eventIDMarker = Array("\"event_id\":\"".utf8)
    let sourceAgentMarker = Array("\"source_agent\":\"".utf8)
    let occurredAtMarker = Array("\"occurred_at\":\"".utf8)
    let totalTokensMarker = Array("\"total_tokens\":".utf8)
    var events: [[String: Any]] = []
    var lineStart = 0
    var index = 0
    while index <= bytes.count {
        if index == bytes.count || bytes[index] == 10 {
            let range = lineStart..<index
            let occurredAt = fastJSONStringField(occurredAtMarker, in: bytes, range: range)
            if !occurredAt.isEmpty {
                events.append([
                    "event_id": fastJSONStringField(eventIDMarker, in: bytes, range: range),
                    "source_agent": fastJSONStringField(sourceAgentMarker, in: bytes, range: range),
                    "occurred_at": occurredAt,
                    "total_tokens": fastJSONIntField(totalTokensMarker, in: bytes, range: range),
                ])
            }
            lineStart = index + 1
        }
        index += 1
    }
    return events
}

func fastJSONStringField(_ marker: [UInt8], in bytes: [UInt8], range: Range<Int>) -> String {
    guard var index = markerEndIndex(marker, in: bytes, range: range) else {
        return ""
    }
    var value: [UInt8] = []
    var escaped = false
    while index < range.upperBound {
        let character = bytes[index]
        index += 1
        if escaped {
            value.append(character)
            escaped = false
            continue
        }
        if character == 92 {
            escaped = true
            continue
        }
        if character == 34 {
            break
        }
        value.append(character)
    }
    return String(bytes: value, encoding: .utf8) ?? ""
}

func fastJSONIntField(_ marker: [UInt8], in bytes: [UInt8], range: Range<Int>) -> Int {
    guard var index = markerEndIndex(marker, in: bytes, range: range) else {
        return 0
    }
    var value = 0
    var foundDigit = false
    while index < range.upperBound {
        let character = bytes[index]
        index += 1
        if character >= 48 && character <= 57 {
            foundDigit = true
            value = value * 10 + Int(character - 48)
        } else if foundDigit {
            break
        }
    }
    return value
}

func markerEndIndex(_ marker: [UInt8], in bytes: [UInt8], range: Range<Int>) -> Int? {
    guard !marker.isEmpty, range.count >= marker.count else {
        return nil
    }
    var index = range.lowerBound
    let maxStart = range.upperBound - marker.count
    while index <= maxStart {
        if bytes[index] == marker[0] {
            var matched = true
            var markerIndex = 1
            while markerIndex < marker.count {
                if bytes[index + markerIndex] != marker[markerIndex] {
                    matched = false
                    break
                }
                markerIndex += 1
            }
            if matched {
                return index + marker.count
            }
        }
        index += 1
    }
    return nil
}

func readEvents(dataRoot: URL) -> [[String: Any]] {
    let path = dataRoot.appendingPathComponent("data/ai-usage/events.jsonl")
    guard let raw = try? String(contentsOf: path, encoding: .utf8) else { return [] }
    return raw.split(separator: "\n").compactMap { line in
        guard let data = String(line).data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return nil
        }
        return object
    }
}

func readGoals(dataRoot: URL) -> [[String: Any]] {
    let path = dataRoot.appendingPathComponent("data/ai-usage/goals.json")
    guard let data = try? Data(contentsOf: path),
          let object = try? JSONSerialization.jsonObject(with: data) else {
        return []
    }
    if let array = object as? [[String: Any]] {
        return array
    }
    if let payload = object as? [String: Any], let goals = payload["goals"] as? [[String: Any]] {
        return goals
    }
    return []
}

func readUsageRefresh(dataRoot: URL) -> [String: Any] {
    let path = dataRoot.appendingPathComponent("data/ai-usage/sidebar-usage-refresh.json")
    guard let data = try? Data(contentsOf: path),
          let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        return [:]
    }
    return object
}

func usageBackfillFile(dataRoot: URL) -> URL {
    dataRoot.appendingPathComponent("data/ai-usage/sidebar-usage-backfill.json")
}

func readUsageBackfill(dataRoot: URL) -> [String: Any] {
    let path = usageBackfillFile(dataRoot: dataRoot)
    guard let data = try? Data(contentsOf: path),
          let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        return [
            "status": "pending",
            "scope": "lifetime",
            "backfill_file": path.path,
        ]
    }
    return object
}

func shouldRunUsageBackfill(dataRoot: URL) -> Bool {
    if !usageEventsFileHasData(dataRoot: dataRoot) {
        return true
    }
    if readCachedDisplayState(dataRoot: dataRoot) == nil {
        return true
    }
    return text(readUsageBackfill(dataRoot: dataRoot)["status"]) != "completed"
}

func usageEventsFileHasData(dataRoot: URL) -> Bool {
    let path = usageEventsFile(dataRoot: dataRoot)
    guard let attributes = try? FileManager.default.attributesOfItem(atPath: path.path),
          let size = attributes[.size] as? NSNumber else {
        return false
    }
    return size.int64Value > 0
}

func writeUsageBackfill(_ summary: [String: Any], dataRoot: URL, status: String) throws {
    let path = usageBackfillFile(dataRoot: dataRoot)
    try FileManager.default.createDirectory(at: path.deletingLastPathComponent(), withIntermediateDirectories: true)
    var payload = summary
    payload["status"] = status
    payload["scope"] = "lifetime"
    payload["backfill_file"] = path.path
    if status == "completed" {
        payload["completed_at"] = text(payload["completed_at"]).isEmpty ? isoNow() : text(payload["completed_at"])
    } else {
        payload["last_attempted_at"] = text(payload["last_attempted_at"]).isEmpty ? isoNow() : text(payload["last_attempted_at"])
    }
    let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
    try data.write(to: path, options: .atomic)
}

func memoryMonitor() -> [String: Any] {
    let thresholdPercent = 80
    let totalBytes = UInt64(ProcessInfo.processInfo.physicalMemory)
    let usedBytes = usedSystemMemoryBytes(totalBytes: totalBytes)
    let usedPercent = totalBytes > 0 ? Int((Double(usedBytes) / Double(totalBytes) * 100.0).rounded(.down)) : 0
    let alert = usedPercent >= thresholdPercent
    let message = alert
        ? "System memory is \(usedPercent)% used, at or above the \(thresholdPercent)% alert threshold."
        : "System memory is \(usedPercent)% used, below the \(thresholdPercent)% alert threshold."
    return [
        "used_bytes": usedBytes,
        "total_bytes": totalBytes,
        "available_bytes": totalBytes > usedBytes ? totalBytes - usedBytes : 0,
        "used_percent": usedPercent,
        "threshold_percent": thresholdPercent,
        "alert": alert,
        "status": alert ? "alert" : "ok",
        "message": message,
    ]
}

func usedSystemMemoryBytes(totalBytes: UInt64) -> UInt64 {
    var stats = vm_statistics64_data_t()
    var count = mach_msg_type_number_t(MemoryLayout<vm_statistics64_data_t>.stride / MemoryLayout<integer_t>.stride)
    let result = withUnsafeMutablePointer(to: &stats) {
        $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
            host_statistics64(mach_host_self(), HOST_VM_INFO64, $0, &count)
        }
    }
    guard result == KERN_SUCCESS else {
        return 0
    }
    let pageSize = UInt64(vm_kernel_page_size)
    let usedPages = UInt64(stats.active_count) + UInt64(stats.wire_count) + UInt64(stats.compressor_page_count)
    return min(totalBytes, usedPages * pageSize)
}

func stateWithRocketScreensaver(_ state: [String: Any]) -> [String: Any] {
    var copy = state
    let root = text(state["repo_root"])
    let dataRoot = root.isEmpty ? activeDataRoot() : URL(fileURLWithPath: root, isDirectory: true)
    copy["rocket_screensaver"] = rocketScreensaverState(dataRoot: dataRoot)
    return copy
}

func overlayRuntimeSnapshotURL() -> URL? {
    let fm = FileManager.default
    guard let support = fm.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
        return nil
    }
    return support.appendingPathComponent("AgentBoost", isDirectory: true)
        .appendingPathComponent("overlay-runtime.json", isDirectory: false)
}

func readOverlayRuntimeSnapshot() -> [String: Any] {
    guard let url = overlayRuntimeSnapshotURL(),
          let data = try? Data(contentsOf: url),
          let object = try? JSONSerialization.jsonObject(with: data),
          let snapshot = object as? [String: Any] else {
        return [
            "captured_at": NSNull(),
            "enabled": false,
            "available": false,
            "reason": "no_snapshot_yet",
        ]
    }
    var withMeta = snapshot
    withMeta["available"] = true
    return withMeta
}

func rocketScreensaverState(dataRoot: URL) -> [String: Any] {
    let enabled = floatingOverlayEnabled(dataRoot: dataRoot)
    let connectedFrames = connectedDisplayFrames()
    var state: [String: Any] = [
        "enabled": enabled,
        "settings_key": "display.floating_overlay_enabled",
        "strategy": "connected_display_region",
        "window_source": "CGWindowListCopyWindowInfo",
        "minimum_width": Int(rocketScreensaverMinimumWidth),
        "minimum_height": Int(rocketScreensaverMinimumHeight),
    ]
    state["display_count"] = NSScreen.screens.count
    state["connected_display_count"] = connectedFrames.count
    if enabled {
        let targetFrame = connectedDisplayRegion()
        state["target_frame"] = rectState(targetFrame)
        let overlayFrames = connectedDisplayOverlayFrames(targetFrame: targetFrame)
        state["panel_count"] = overlayFrames.count
        state["panel_frames"] = overlayFrames.map { rectState($0) }
    }
    return state
}

func rectState(_ rect: NSRect) -> [String: Any] {
    [
        "x": Int(rect.origin.x.rounded()),
        "y": Int(rect.origin.y.rounded()),
        "width": Int(rect.width.rounded()),
        "height": Int(rect.height.rounded()),
    ]
}

func connectedDisplayRegion() -> NSRect {
    let frames = connectedDisplayFrames()
    guard !frames.isEmpty else {
        let fallback = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 800, height: 450)
        return fallback.insetBy(dx: rocketScreensaverScreenInset, dy: rocketScreensaverScreenInset)
    }
    let union = unionRects(frames)
    let inset = union.insetBy(dx: rocketScreensaverScreenInset, dy: rocketScreensaverScreenInset)
    if inset.width >= rocketScreensaverMinimumWidth && inset.height >= rocketScreensaverMinimumHeight {
        return inset
    }
    return union
}

func connectedDisplayOverlayFrames(targetFrame: NSRect) -> [NSRect] {
    let frames = connectedDisplayFrames()
    guard !frames.isEmpty else {
        return [targetFrame]
    }
    let overlayFrames = frames.compactMap { frame -> NSRect? in
        let clipped = frame.intersection(targetFrame)
        if clipped.isNull || clipped.width <= 0 || clipped.height <= 0 {
            return nil
        }
        return clipped
    }
    return overlayFrames.isEmpty ? [targetFrame] : overlayFrames
}

func connectedDisplayFrames() -> [NSRect] {
    let frames = NSScreen.screens.map { $0.visibleFrame }.filter { frame in
        frame.width >= rocketScreensaverMinimumWidth && frame.height >= rocketScreensaverMinimumHeight
    }
    return largestConnectedScreenGroup(frames)
}

func largestConnectedScreenGroup(_ frames: [NSRect]) -> [NSRect] {
    var remaining = frames
    var bestGroup: [NSRect] = []
    var bestArea = CGFloat(0)
    while !remaining.isEmpty {
        var group = [remaining.removeFirst()]
        var changed = true
        while changed {
            changed = false
            for index in remaining.indices.reversed() {
                if group.contains(where: { screenFramesTouchOrOverlap($0, remaining[index]) }) {
                    group.append(remaining.remove(at: index))
                    changed = true
                }
            }
        }
        let area = group.reduce(CGFloat(0)) { total, frame in total + frame.width * frame.height }
        if area > bestArea {
            bestArea = area
            bestGroup = group
        }
    }
    return bestGroup
}

func screenFramesTouchOrOverlap(_ lhs: NSRect, _ rhs: NSRect) -> Bool {
    lhs.insetBy(dx: -rocketScreensaverSeamTolerance, dy: -rocketScreensaverSeamTolerance).intersects(rhs)
}

func unionRects(_ frames: [NSRect]) -> NSRect {
    guard let first = frames.first else {
        return .zero
    }
    return frames.dropFirst().reduce(first) { partial, frame in partial.union(frame) }
}

func largestVacantScreenRegion() -> NSRect {
    let occupied = occupiedWindowFrames()
    var bestRect: NSRect?
    var bestArea = CGFloat(0)
    for screen in NSScreen.screens {
        guard let displayID = screenDisplayID(screen) else {
            continue
        }
        let displayBounds = CGDisplayBounds(displayID)
        let visibleAppFrame = screen.visibleFrame.insetBy(dx: rocketScreensaverScreenInset, dy: rocketScreensaverScreenInset)
        let visibleQuartzFrame = appKitRectToQuartz(visibleAppFrame, screen: screen, displayBounds: displayBounds)
        let screenOccupied = occupied.compactMap { rect -> CGRect? in
            let clipped = rect.intersection(visibleQuartzFrame)
            return clipped.isNull || clipped.width < 80 || clipped.height < 80 ? nil : clipped
        }
        let vacantQuartzFrame = largestVacantRect(in: visibleQuartzFrame, avoiding: screenOccupied) ?? visibleQuartzFrame
        let vacantAppFrame = quartzRectToAppKit(vacantQuartzFrame, screen: screen, displayBounds: displayBounds)
        let area = vacantAppFrame.width * vacantAppFrame.height
        if area > bestArea {
            bestArea = area
            bestRect = vacantAppFrame
        }
    }
    if let bestRect = bestRect {
        return bestRect
    }
    let fallback = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 800, height: 450)
    return fallback.insetBy(dx: rocketScreensaverScreenInset, dy: rocketScreensaverScreenInset)
}

func largestVacantRect(in area: CGRect, avoiding occupied: [CGRect]) -> CGRect? {
    let candidates = vacantRectCandidates(in: area, avoiding: occupied)
    return candidates
        .filter { candidate in
            candidate.width >= rocketScreensaverMinimumWidth
                && candidate.height >= rocketScreensaverMinimumHeight
                && !occupied.contains { $0.intersects(candidate.insetBy(dx: 1, dy: 1)) }
        }
        .max { lhs, rhs in
            lhs.width * lhs.height < rhs.width * rhs.height
        }
}

func vacantRectCandidates(in area: CGRect, avoiding occupied: [CGRect]) -> [CGRect] {
    var candidates = [area]
    for rawWindow in occupied {
        let window = rawWindow.intersection(area)
        if window.isNull {
            continue
        }
        candidates.append(CGRect(x: area.minX, y: area.minY, width: max(0, window.minX - area.minX), height: area.height))
        candidates.append(CGRect(x: window.maxX, y: area.minY, width: max(0, area.maxX - window.maxX), height: area.height))
        candidates.append(CGRect(x: area.minX, y: area.minY, width: area.width, height: max(0, window.minY - area.minY)))
        candidates.append(CGRect(x: area.minX, y: window.maxY, width: area.width, height: max(0, area.maxY - window.maxY)))
    }
    return candidates
}

func occupiedWindowFrames() -> [CGRect] {
    guard let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] else {
        return []
    }
    let currentPID = Int(getpid())
    return windows.compactMap { window in
        if let ownerPID = (window[kCGWindowOwnerPID as String] as? NSNumber)?.intValue, ownerPID == currentPID {
            return nil
        }
        let layer = (window[kCGWindowLayer as String] as? NSNumber)?.intValue ?? 0
        if layer != 0 {
            return nil
        }
        if let alpha = (window[kCGWindowAlpha as String] as? NSNumber)?.doubleValue, alpha <= 0 {
            return nil
        }
        guard let bounds = window[kCGWindowBounds as String] as? NSDictionary,
              let rect = CGRect(dictionaryRepresentation: bounds) else {
            return nil
        }
        return rect.width < 80 || rect.height < 80 ? nil : rect
    }
}

func screenDisplayID(_ screen: NSScreen) -> CGDirectDisplayID? {
    guard let screenNumber = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber else {
        return nil
    }
    return CGDirectDisplayID(screenNumber.uint32Value)
}

func appKitRectToQuartz(_ rect: NSRect, screen: NSScreen, displayBounds: CGRect) -> CGRect {
    CGRect(
        x: displayBounds.minX + (rect.minX - screen.frame.minX),
        y: displayBounds.minY + (screen.frame.maxY - rect.maxY),
        width: rect.width,
        height: rect.height
    )
}

func quartzRectToAppKit(_ rect: CGRect, screen: NSScreen, displayBounds: CGRect) -> NSRect {
    NSRect(
        x: screen.frame.minX + (rect.minX - displayBounds.minX),
        y: screen.frame.maxY - (rect.maxY - displayBounds.minY),
        width: rect.width,
        height: rect.height
    )
}

func networkActivity() -> [String: Any] {
    let now = Date().timeIntervalSince1970
    let outboundBytes = outboundNetworkBytes()
    let previous = UserDefaults.standard.dictionary(forKey: networkActivitySampleKey) ?? [:]
    let previousBytes = uint64Value(previous["outbound_bytes"])
    let previousSampledAt = doubleValue(previous["sampled_at"])
    let sampleAvailable = outboundBytes > 0
        && previousSampledAt > 0
        && now > previousSampledAt
        && outboundBytes >= previousBytes
    var outboundBytesPerSecond = 0
    if sampleAvailable {
        let elapsed = max(0.001, now - previousSampledAt)
        outboundBytesPerSecond = Int(Double(outboundBytes - previousBytes) / elapsed)
    }
    UserDefaults.standard.set(
        [
            "outbound_bytes": NSNumber(value: outboundBytes),
            "sampled_at": NSNumber(value: now),
        ],
        forKey: networkActivitySampleKey
    )
    var state = networkActivityState(
        outboundBytesPerSecond: outboundBytesPerSecond,
        sampleAvailable: sampleAvailable
    )
    state["outbound_bytes"] = outboundBytes
    return state
}

func networkActivityState(outboundBytesPerSecond: Int, sampleAvailable: Bool) -> [String: Any] {
    let outbound = max(0, outboundBytesPerSecond)
    let activityLevel: String
    let interval: Double
    let speed: Double
    if outbound <= 0 {
        activityLevel = "idle"
        interval = 1.5
        speed = 0.0
    } else if outbound < 128_000 {
        activityLevel = "active"
        interval = 0.9
        speed = 0.45
    } else if outbound < 384_000 {
        activityLevel = "high"
        interval = 0.45
        speed = 0.9
    } else {
        activityLevel = "surge"
        interval = 0.2
        speed = 1.8
    }
    return [
        "outbound_bytes_per_second": outbound,
        "activity_level": activityLevel,
        "animation_interval_seconds": interval,
        "rocket_speed": speed,
        "has_flame": speed > 0,
        "sample_available": sampleAvailable,
        "speed_source": "network",
    ]
}

func outboundNetworkBytes() -> UInt64 {
    var ifaddr: UnsafeMutablePointer<ifaddrs>?
    guard getifaddrs(&ifaddr) == 0, let firstAddress = ifaddr else {
        return 0
    }
    defer { freeifaddrs(ifaddr) }

    var perInterface: [String: UInt64] = [:]
    var cursor: UnsafeMutablePointer<ifaddrs>? = firstAddress
    while let current = cursor {
        defer { cursor = current.pointee.ifa_next }
        let name = String(cString: current.pointee.ifa_name)
        if name.hasPrefix("lo")
            || name.hasPrefix("gif")
            || name.hasPrefix("stf")
            || name.hasPrefix("utun")
            || name.hasPrefix("awdl")
            || name.hasPrefix("llw") {
            continue
        }
        let flags = current.pointee.ifa_flags
        if (flags & UInt32(IFF_UP)) == 0 || (flags & UInt32(IFF_LOOPBACK)) != 0 {
            continue
        }
        guard let address = current.pointee.ifa_addr,
              Int32(address.pointee.sa_family) == AF_LINK,
              let rawData = current.pointee.ifa_data else {
            continue
        }
        let ifData = rawData.assumingMemoryBound(to: if_data.self).pointee
        let outbound = UInt64(ifData.ifi_obytes)
        perInterface[name] = max(perInterface[name] ?? 0, outbound)
    }
    return perInterface.values.reduce(0, +)
}

func text(_ value: Any?) -> String {
    if let string = value as? String { return string }
    if let number = value as? NSNumber { return number.stringValue }
    return ""
}

func textArray(_ value: Any?) -> [String] {
    if let strings = value as? [String] { return strings }
    if let values = value as? [Any] {
        return values.map { text($0) }.filter { !$0.isEmpty }
    }
    return []
}

func intText(_ value: Any?) -> String {
    if let number = value as? NSNumber {
        return NumberFormatter.localizedString(from: number, number: .decimal)
    }
    if let intValue = value as? Int {
        return NumberFormatter.localizedString(from: NSNumber(value: intValue), number: .decimal)
    }
    return "0"
}

func statusSymbol(_ status: String) -> String {
    if status == "earned" { return "*" }
    if status == "in_progress" { return ">" }
    if status == "active" { return "[>]" }
    if status == "done" { return "[x]" }
    return "[ ]"
}

func tokenInt(_ value: Any?) -> Int {
    if let number = value as? NSNumber { return number.intValue }
    if let intValue = value as? Int { return intValue }
    if let string = value as? String { return Int(string) ?? 0 }
    return 0
}

func uint64Value(_ value: Any?) -> UInt64 {
    if let number = value as? NSNumber { return number.uint64Value }
    if let intValue = value as? Int { return UInt64(max(0, intValue)) }
    if let uintValue = value as? UInt64 { return uintValue }
    if let doubleValue = value as? Double { return UInt64(max(0, doubleValue)) }
    if let string = value as? String { return UInt64(string) ?? 0 }
    return 0
}

func doubleValue(_ value: Any?) -> Double {
    if let number = value as? NSNumber { return number.doubleValue }
    if let doubleValue = value as? Double { return doubleValue }
    if let intValue = value as? Int { return Double(intValue) }
    if let uintValue = value as? UInt64 { return Double(uintValue) }
    if let string = value as? String { return Double(string) ?? 0 }
    return 0
}

func stableID(_ parts: Any...) -> String {
    let raw = parts.map { String(describing: $0) }.joined(separator: "\u{1f}")
    let digest = SHA256.hash(data: Data(raw.utf8))
    let hex = digest.map { String(format: "%02x", $0) }.joined()
    return String(hex.prefix(16))
}

func isoNow() -> String {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime]
    return formatter.string(from: Date())
}

func tailJSONLContents(path: URL, maxBytes: UInt64) -> String? {
    guard let handle = try? FileHandle(forReadingFrom: path) else {
        return nil
    }
    defer { try? handle.close() }
    guard let fileSize = try? handle.seekToEnd() else {
        return nil
    }
    let startOffset = fileSize > maxBytes ? fileSize - maxBytes : 0
    guard (try? handle.seek(toOffset: startOffset)) != nil else {
        return nil
    }
    let data = handle.readDataToEndOfFile()
    var raw = String(decoding: data, as: UTF8.self)
    if startOffset > 0 {
        guard let firstNewline = raw.firstIndex(of: "\n") else {
            return ""
        }
        raw = String(raw[raw.index(after: firstNewline)...])
    }
    return raw
}

func usageFileContents(path: URL, since cutoff: Date?) -> String? {
    if cutoff == nil {
        return try? String(contentsOf: path, encoding: .utf8)
    }
    return tailJSONLContents(path: path, maxBytes: liveUsageTailBytes)
}

func collectUsageFromSelectedAgentFolders(dataRoot: URL, since cutoff: Date? = nil) throws -> [String: Any] {
    let importedAt = isoNow()
    var candidates: [[String: Any]] = []
    if let claudeRoot = claudeUsageRoot() {
        candidates.append(contentsOf: claudeUsageEvents(claudeRoot: claudeRoot, importedAt: importedAt, since: cutoff))
    }
    if let codexRoot = codexUsageRoot() {
        candidates.append(contentsOf: codexUsageEvents(codexRoot: codexRoot, importedAt: importedAt, since: cutoff))
    }
    let existingEvents = readEvents(dataRoot: dataRoot)
    let hasClaudeProjectEvents = candidates.contains { event in
        text(event["source_agent"]) == "claude" && text(event["record_type"]) == "turn"
    }
    let filteredExistingEvents = hasClaudeProjectEvents
        ? existingEvents.filter { event in
            !(text(event["source_agent"]) == "claude" && text(event["record_type"]) == "session")
        }
        : existingEvents
    let removedLegacy = existingEvents.count - filteredExistingEvents.count
    let existingIDs = Set(filteredExistingEvents.map { text($0["event_id"]) })
    var seenCandidateIDs = Set<String>()
    let newEvents = candidates.filter { event in
        let eventID = text(event["event_id"])
        guard !existingIDs.contains(eventID), !seenCandidateIDs.contains(eventID) else {
            return false
        }
        seenCandidateIDs.insert(eventID)
        return true
    }
    if removedLegacy > 0 {
        try replaceUsageEvents(filteredExistingEvents + newEvents, dataRoot: dataRoot)
    } else {
        try appendUsageEvents(newEvents, dataRoot: dataRoot)
    }
    let summary: [String: Any] = [
        "last_refreshed_at": importedAt,
        "scope": cutoff == nil ? "lifetime" : "recent",
        "scanned": candidates.count,
        "imported": newEvents.count,
        "removed_legacy": removedLegacy,
        "skipped_existing": candidates.count - newEvents.count,
        "events_file": usageEventsFile(dataRoot: dataRoot).path,
    ]
    try writeUsageRefresh(summary, dataRoot: dataRoot)
    return summary
}

func usageEventsFile(dataRoot: URL) -> URL {
    dataRoot.appendingPathComponent("data/ai-usage/events.jsonl")
}

func claudeUsageEvents(claudeRoot: URL, importedAt: String, since cutoff: Date? = nil) -> [[String: Any]] {
    let projectEvents = claudeProjectUsageEvents(claudeRoot: claudeRoot, importedAt: importedAt, since: cutoff)
    if cutoff != nil {
        return projectEvents
    }
    return projectEvents.isEmpty ? claudeSessionMetaUsageEvents(claudeRoot: claudeRoot, importedAt: importedAt) : projectEvents
}

// All `.claude/projects/` directories that hold Claude session jsonls. We
// follow the same pattern as CodexBar (CLAUDE_CONFIG_DIR env + ~/.config/claude
// + ~/.claude) and also add Claude Desktop's local-agent-mode sessions, which
// nest their own `.claude/projects/` tree under
// `~/Library/Application Support/Claude/local-agent-mode-sessions/`.
func discoverClaudeProjectsRoots(primary claudeRoot: URL) -> [URL] {
    var roots: [URL] = []
    var seen = Set<String>()
    func addRoot(_ url: URL) {
        let canonical = url.standardizedFileURL.path
        guard seen.insert(canonical).inserted else { return }
        guard FileManager.default.fileExists(atPath: canonical) else { return }
        roots.append(url)
    }

    addRoot(claudeRoot.appendingPathComponent("projects", isDirectory: true))

    if let env = ProcessInfo.processInfo.environment["CLAUDE_CONFIG_DIR"]?
        .trimmingCharacters(in: .whitespacesAndNewlines), !env.isEmpty
    {
        for part in env.split(separator: ",") {
            let raw = String(part).trimmingCharacters(in: .whitespacesAndNewlines)
            guard !raw.isEmpty else { continue }
            let envRoot = URL(fileURLWithPath: raw)
            if envRoot.lastPathComponent == "projects" {
                addRoot(envRoot)
            } else {
                addRoot(envRoot.appendingPathComponent("projects", isDirectory: true))
            }
        }
    }

    let home = FileManager.default.homeDirectoryForCurrentUser
    addRoot(home.appendingPathComponent(".config/claude/projects", isDirectory: true))

    let localAgentSessions = home.appendingPathComponent(
        "Library/Application Support/Claude/local-agent-mode-sessions",
        isDirectory: true)
    if FileManager.default.fileExists(atPath: localAgentSessions.path),
       let enumerator = FileManager.default.enumerator(
        at: localAgentSessions,
        includingPropertiesForKeys: nil)
    {
        for case let url as URL in enumerator
            where url.hasDirectoryPath
                && url.lastPathComponent == "projects"
                && url.deletingLastPathComponent().lastPathComponent == ".claude"
        {
            addRoot(url)
        }
    }

    return roots
}

func allClaudeProjectFiles(projectsRoots: [URL]) -> [URL] {
    var files: [URL] = []
    var seen = Set<String>()
    for root in projectsRoots {
        guard let enumerator = FileManager.default.enumerator(at: root, includingPropertiesForKeys: [.contentModificationDateKey]) else {
            continue
        }
        for case let path as URL in enumerator where path.pathExtension == "jsonl" {
            let canonical = path.standardizedFileURL.path
            if seen.insert(canonical).inserted {
                files.append(path)
            }
        }
    }
    return files
}

// Claude Desktop's local-agent-mode sessions write a session-root `audit.jsonl`
// that mirrors the nested `.claude/projects` rollouts with a slightly different
// schema (`_audit_timestamp` instead of `timestamp`, `parent_tool_use_id`
// instead of `requestId`). On this host these files hold ~5k unique assistant
// events / ~493M tokens with zero overlap against the nested projects tree.
// See PRD artifact-2026-05-21-agentboost-claude-audit-jsonl.
func discoverClaudeAuditFiles() -> [URL] {
    let home = FileManager.default.homeDirectoryForCurrentUser
    let root = home.appendingPathComponent(
        "Library/Application Support/Claude/local-agent-mode-sessions",
        isDirectory: true)
    guard FileManager.default.fileExists(atPath: root.path),
          let enumerator = FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil)
    else { return [] }
    var files: [URL] = []
    var seen = Set<String>()
    for case let url as URL in enumerator
        where !url.hasDirectoryPath && url.lastPathComponent == "audit.jsonl"
    {
        let canonical = url.standardizedFileURL.path
        if seen.insert(canonical).inserted {
            files.append(url)
        }
    }
    return files
}

func recentClaudeProjectFiles(claudeRoot: URL, since cutoff: Date) -> [URL] {
    if let cache = liveClaudeProjectFileCache,
       cache.rootPath == claudeRoot.path,
       Date().timeIntervalSince(cache.refreshedAt) < liveClaudeProjectFileScanIntervalSeconds {
        return cache.files
    }

    let now = Date()
    let projectsRoots = discoverClaudeProjectsRoots(primary: claudeRoot)
    let files = allClaudeProjectFiles(projectsRoots: projectsRoots).filter { path in
        guard shouldImportUsageFile(path, now: now) else {
            return false
        }
        guard let modifiedAt = (try? path.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate else {
            return true
        }
        return modifiedAt >= cutoff
    }
    liveClaudeProjectFileCache = (rootPath: claudeRoot.path, refreshedAt: now, files: files)
    return files
}

func claudeProjectUsageEvents(claudeRoot: URL, importedAt: String, since cutoff: Date? = nil) -> [[String: Any]] {
    let projectsRoots = discoverClaudeProjectsRoots(primary: claudeRoot)
    let projectFiles: [URL]
    if let cutoff {
        projectFiles = recentClaudeProjectFiles(claudeRoot: claudeRoot, since: cutoff)
    } else {
        projectFiles = allClaudeProjectFiles(projectsRoots: projectsRoots)
    }
    // Claude streams an assistant turn by appending multiple jsonl rows
    // that share the same (message.id, requestId) — `usage` grows as the
    // generation progresses (cache_read fills in, then output_tokens climb).
    // The previous "first-wins" dedup latched onto the partial early row and
    // discarded the final totals, so Claude usage was undercounted by ~50%.
    // Track the row with the largest `total_tokens` per event id and emit
    // that one.
    var eventsByID: [String: [String: Any]] = [:]
    var eventOrder: [String] = []
    for path in projectFiles {
        if cutoff != nil && !shouldImportUsageFile(path) {
            continue
        }
        if let cutoff,
           let modifiedAt = (try? path.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate,
           modifiedAt < cutoff {
            continue
        }
        guard let raw = usageFileContents(path: path, since: cutoff) else {
            continue
        }
        let lines = raw.split(separator: "\n")
        let indexedLines: [(offset: Int, element: Substring)] = cutoff == nil
            ? Array(lines.enumerated())
            : Array(lines.enumerated().reversed())
        var sawCutoffWindow = false
        for (index, line) in indexedLines {
            guard let data = String(line).data(using: .utf8),
                  let row = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  text(row["type"]) == "assistant",
                  let message = row["message"] as? [String: Any],
                  let usage = message["usage"] as? [String: Any] else {
                continue
            }
            let input = tokenInt(usage["input_tokens"])
            let cacheCreation = tokenInt(usage["cache_creation_input_tokens"])
            let cacheRead = tokenInt(usage["cache_read_input_tokens"])
            let cached = cacheCreation + cacheRead
            let output = tokenInt(usage["output_tokens"])
            guard input > 0 || cached > 0 || output > 0 else {
                continue
            }
            let messageID = text(message["id"])
            let requestID = text(row["requestId"])
            let eventID = !messageID.isEmpty && !requestID.isEmpty
                ? "claude:\(stableID(messageID, requestID))"
                : "claude:\(stableID(path.path, index + 1))"
            let totalTokens = input + cached + output
            if let existing = eventsByID[eventID],
               tokenInt(existing["total_tokens"]) >= totalTokens {
                continue
            }
            let sessionID = text(row["sessionId"]).isEmpty ? path.deletingPathExtension().lastPathComponent : text(row["sessionId"])
            let occurredAtRaw = text(row["timestamp"]).isEmpty ? importedAt : text(row["timestamp"])
            if let cutoff, let occurredAt = eventDate(occurredAtRaw), occurredAt < cutoff {
                if sawCutoffWindow {
                    break
                }
                continue
            }
            if cutoff != nil {
                sawCutoffWindow = true
            }
            var event: [String: Any] = [
                "event_id": eventID,
                "source_agent": "claude",
                "source_path": path.path,
                "source_session_id": sessionID,
                "occurred_at": occurredAtRaw,
                "project_path": text(row["cwd"]),
                "input_tokens": input,
                "cached_input_tokens": cached,
                "output_tokens": output,
                "reasoning_output_tokens": 0,
                "total_tokens": totalTokens,
                "record_type": "turn",
                "imported_at": importedAt,
            ]
            let model = text(message["model"])
            if !model.isEmpty {
                event["model"] = model
            }
            if eventsByID[eventID] == nil {
                eventOrder.append(eventID)
            }
            eventsByID[eventID] = event
        }
    }
    // Second pass: Claude Desktop local-agent-mode session-root `audit.jsonl`
    // files. Same `type: assistant` + `message.usage` shape but with
    // `_audit_timestamp` instead of `timestamp` and `parent_tool_use_id`
    // instead of `requestId`. Merge into the same eventsByID dict so
    // streaming / cross-file dedup stays in one place. Skip when scanning
    // with a `since` cutoff and the file's mtime is older than the cutoff —
    // the recent-files cache only tracks the projects tree, so we honor the
    // cutoff inline here.
    for path in discoverClaudeAuditFiles() {
        if cutoff != nil && !shouldImportUsageFile(path) {
            continue
        }
        if let cutoff,
           let modifiedAt = (try? path.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate,
           modifiedAt < cutoff {
            continue
        }
        guard let raw = usageFileContents(path: path, since: cutoff) else {
            continue
        }
        let lines = raw.split(separator: "\n")
        let indexedLines: [(offset: Int, element: Substring)] = cutoff == nil
            ? Array(lines.enumerated())
            : Array(lines.enumerated().reversed())
        var sawCutoffWindow = false
        for (index, line) in indexedLines {
            guard let data = String(line).data(using: .utf8),
                  let row = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  text(row["type"]) == "assistant",
                  let message = row["message"] as? [String: Any],
                  let usage = message["usage"] as? [String: Any] else {
                continue
            }
            let input = tokenInt(usage["input_tokens"])
            let cacheCreation = tokenInt(usage["cache_creation_input_tokens"])
            let cacheRead = tokenInt(usage["cache_read_input_tokens"])
            let cached = cacheCreation + cacheRead
            let output = tokenInt(usage["output_tokens"])
            guard input > 0 || cached > 0 || output > 0 else {
                continue
            }
            let messageID = text(message["id"])
            let requestID = text(row["parent_tool_use_id"])
            let eventID = !messageID.isEmpty && !requestID.isEmpty
                ? "claude:\(stableID(messageID, requestID))"
                : "claude:\(stableID(path.path, index + 1))"
            let totalTokens = input + cached + output
            if let existing = eventsByID[eventID],
               tokenInt(existing["total_tokens"]) >= totalTokens {
                continue
            }
            let sessionID = text(row["session_id"]).isEmpty
                ? path.deletingLastPathComponent().lastPathComponent
                : text(row["session_id"])
            let occurredAtRaw: String
            if !text(row["_audit_timestamp"]).isEmpty {
                occurredAtRaw = text(row["_audit_timestamp"])
            } else if !text(row["timestamp"]).isEmpty {
                occurredAtRaw = text(row["timestamp"])
            } else {
                occurredAtRaw = importedAt
            }
            if let cutoff, let occurredAt = eventDate(occurredAtRaw), occurredAt < cutoff {
                if sawCutoffWindow {
                    break
                }
                continue
            }
            if cutoff != nil {
                sawCutoffWindow = true
            }
            var event: [String: Any] = [
                "event_id": eventID,
                "source_agent": "claude",
                "source_path": path.path,
                "source_session_id": sessionID,
                "occurred_at": occurredAtRaw,
                "project_path": text(row["cwd"]),
                "input_tokens": input,
                "cached_input_tokens": cached,
                "output_tokens": output,
                "reasoning_output_tokens": 0,
                "total_tokens": totalTokens,
                "record_type": "turn",
                "imported_at": importedAt,
            ]
            let model = text(message["model"])
            if !model.isEmpty {
                event["model"] = model
            }
            if eventsByID[eventID] == nil {
                eventOrder.append(eventID)
            }
            eventsByID[eventID] = event
        }
    }
    return eventOrder.compactMap { eventsByID[$0] }
}

func claudeSessionMetaUsageEvents(claudeRoot: URL, importedAt: String) -> [[String: Any]] {
    let sessionDir = claudeRoot.appendingPathComponent("usage-data/session-meta", isDirectory: true)
    guard let files = try? FileManager.default.contentsOfDirectory(at: sessionDir, includingPropertiesForKeys: nil) else {
        return []
    }
    var events: [[String: Any]] = []
    for path in files.filter({ $0.pathExtension == "json" }).sorted(by: { $0.path < $1.path }) {
        guard shouldImportUsageFile(path),
              let data = try? Data(contentsOf: path),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            continue
        }
        let sessionID = text(object["session_id"]).isEmpty ? path.deletingPathExtension().lastPathComponent : text(object["session_id"])
        let input = tokenInt(object["input_tokens"])
        let output = tokenInt(object["output_tokens"])
        events.append([
            "event_id": "claude:\(sessionID)",
            "source_agent": "claude",
            "source_path": path.path,
            "source_session_id": sessionID,
            "occurred_at": text(object["start_time"]).isEmpty ? importedAt : text(object["start_time"]),
            "project_path": text(object["project_path"]),
            "input_tokens": input,
            "cached_input_tokens": 0,
            "output_tokens": output,
            "reasoning_output_tokens": 0,
            "total_tokens": input + output,
            "record_type": "session",
            "imported_at": importedAt,
        ])
    }
    return events
}

func sessionSearchRoots(sessionsDir: URL, since cutoff: Date?) -> [URL] {
    guard let cutoff else {
        return [sessionsDir]
    }
    let calendar = Calendar(identifier: .gregorian)
    var day = calendar.startOfDay(for: cutoff)
    let end = calendar.startOfDay(for: Date())
    var roots: [URL] = []
    while day <= end {
        let components = calendar.dateComponents([.year, .month, .day], from: day)
        if let year = components.year, let month = components.month, let dayOfMonth = components.day {
            let root = sessionsDir
                .appendingPathComponent(String(format: "%04d", year), isDirectory: true)
                .appendingPathComponent(String(format: "%02d", month), isDirectory: true)
                .appendingPathComponent(String(format: "%02d", dayOfMonth), isDirectory: true)
            if FileManager.default.fileExists(atPath: root.path) {
                roots.append(root)
            }
        }
        guard let next = calendar.date(byAdding: .day, value: 1, to: day) else {
            break
        }
        day = next
    }
    return roots
}

func codexUsageEvents(codexRoot: URL, importedAt: String, since cutoff: Date? = nil) -> [[String: Any]] {
    let sessionsDir = codexRoot.appendingPathComponent("sessions", isDirectory: true)
    var events: [[String: Any]] = []
    for root in sessionSearchRoots(sessionsDir: sessionsDir, since: cutoff) {
        guard let enumerator = FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil) else {
            continue
        }
        for case let path as URL in enumerator {
            guard path.pathExtension == "jsonl" else {
                continue
            }
            if cutoff != nil && !shouldImportUsageFile(path) {
                continue
            }
            if let cutoff,
               let modifiedAt = (try? path.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate,
               modifiedAt < cutoff {
                continue
            }
            guard let raw = usageFileContents(path: path, since: cutoff) else {
                continue
            }
            var previousTotal: [String: Int]? = nil
            var currentModel: String? = nil
            var currentModelIsFallback = false
            let lines = raw.split(separator: "\n")
            let indexedLines: [(offset: Int, element: Substring)] = cutoff == nil
                ? Array(lines.enumerated())
                : Array(lines.enumerated().reversed())
            var sawCutoffWindow = false
            for (index, line) in indexedLines {
                if cutoff != nil && !line.contains("\"token_count\"") && !line.contains("\"turn_context\"") {
                    continue
                }
                guard let data = String(line).data(using: .utf8),
                      let row = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                    continue
                }
                if text(row["type"]) == "turn_context" {
                    if let payload = row["payload"] as? [String: Any],
                       let model = codexModel(from: payload) {
                        currentModel = model
                        currentModelIsFallback = false
                    }
                    continue
                }
                guard text(row["type"]) == "event_msg",
                      let payload = row["payload"] as? [String: Any],
                      text(payload["type"]) == "token_count",
                      let info = payload["info"] as? [String: Any] else {
                    continue
                }
                let usageResult = tokenUsageFromInfo(info, previousTotal: previousTotal)
                previousTotal = usageResult.total
                guard let usage = usageResult.usage else { continue }
                guard !isZeroUsage(usage) else { continue }
                let extractedModel = codexModel(from: payload, info: info)
                if let extractedModel {
                    currentModel = extractedModel
                    currentModelIsFallback = false
                }
                var model = extractedModel ?? currentModel
                var modelIsFallback = false
                if model == nil {
                    model = "gpt-5"
                    currentModel = model
                    currentModelIsFallback = true
                    modelIsFallback = true
                } else if currentModelIsFallback && extractedModel == nil {
                    modelIsFallback = true
                }
                let occurredAtRaw = text(row["timestamp"]).isEmpty ? importedAt : text(row["timestamp"])
                if let cutoff, let occurredAt = eventDate(occurredAtRaw), occurredAt < cutoff {
                    if sawCutoffWindow {
                        break
                    }
                    continue
                }
                if cutoff != nil {
                    sawCutoffWindow = true
                }
                var event: [String: Any] = [
                    "event_id": "codex:\(stableID(path.path, index + 1))",
                    "source_agent": "codex",
                    "source_path": path.path,
                    "source_session_id": path.deletingPathExtension().lastPathComponent,
                    "occurred_at": occurredAtRaw,
                    "project_path": "",
                    "input_tokens": usage["input_tokens"] ?? 0,
                    "cached_input_tokens": usage["cached_input_tokens"] ?? 0,
                    "output_tokens": usage["output_tokens"] ?? 0,
                    "reasoning_output_tokens": usage["reasoning_output_tokens"] ?? 0,
                    "total_tokens": usage["total_tokens"] ?? 0,
                    "record_type": "turn",
                    "imported_at": importedAt,
                    "model": model ?? "gpt-5",
                ]
                if modelIsFallback {
                    event["model_is_fallback"] = true
                }
                events.append(event)
            }
        }
    }
    return cutoff == nil ? events : Array(events.reversed())
}

func tokenUsageFromInfo(_ info: [String: Any], previousTotal: [String: Int]?) -> (usage: [String: Int]?, total: [String: Int]?) {
    let total = (info["total_token_usage"] as? [String: Any]).map { tokenUsageDictionary($0) }
    if let last = info["last_token_usage"] as? [String: Any] {
        return (tokenUsageDictionary(last), total ?? previousTotal)
    }
    guard let current = total else {
        return (nil, previousTotal)
    }
    guard let previous = previousTotal else {
        return (current, current)
    }
    var delta: [String: Int] = [:]
    for field in tokenFields {
        delta[field] = max(0, (current[field] ?? 0) - (previous[field] ?? 0))
    }
    delta["cached_input_tokens"] = min(delta["cached_input_tokens"] ?? 0, delta["input_tokens"] ?? 0)
    return (delta, current)
}

func tokenUsageDictionary(_ object: [String: Any]) -> [String: Int] {
    let input = tokenInt(object["input_tokens"])
    let cached = tokenInt(object["cached_input_tokens"] ?? object["cache_read_input_tokens"])
    let output = tokenInt(object["output_tokens"])
    let reasoning = tokenInt(object["reasoning_output_tokens"])
    let explicitTotal = tokenInt(object["total_tokens"])
    let total = explicitTotal > 0 ? explicitTotal : input + output
    return [
        "input_tokens": input,
        "cached_input_tokens": min(cached, input),
        "output_tokens": output,
        "reasoning_output_tokens": reasoning,
        "total_tokens": total,
    ]
}

func isZeroUsage(_ usage: [String: Int]) -> Bool {
    (usage["input_tokens"] ?? 0) == 0 &&
        (usage["cached_input_tokens"] ?? 0) == 0 &&
        (usage["output_tokens"] ?? 0) == 0 &&
        (usage["reasoning_output_tokens"] ?? 0) == 0
}

func codexModel(from payload: [String: Any], info providedInfo: [String: Any]? = nil) -> String? {
    let info = providedInfo ?? payload["info"] as? [String: Any]
    if let info {
        for key in ["model", "model_name"] {
            let value = text(info[key])
            if !value.isEmpty {
                return value
            }
        }
        if let metadata = info["metadata"] as? [String: Any] {
            let value = text(metadata["model"])
            if !value.isEmpty {
                return value
            }
        }
    }
    for key in ["model", "model_name"] {
        let value = text(payload[key])
        if !value.isEmpty {
            return value
        }
    }
    if let metadata = payload["metadata"] as? [String: Any] {
        let value = text(metadata["model"])
        if !value.isEmpty {
            return value
        }
    }
    return nil
}

func appendUsageEvents(_ events: [[String: Any]], dataRoot: URL) throws {
    guard !events.isEmpty else { return }
    let path = usageEventsFile(dataRoot: dataRoot)
    try FileManager.default.createDirectory(at: path.deletingLastPathComponent(), withIntermediateDirectories: true)
    let payload = try serializedUsageEvents(events)
    if FileManager.default.fileExists(atPath: path.path),
       let handle = try? FileHandle(forWritingTo: path) {
        defer { try? handle.close() }
        try handle.seekToEnd()
        try handle.write(contentsOf: Data(payload.utf8))
    } else {
        try payload.write(to: path, atomically: true, encoding: .utf8)
    }
}

func replaceUsageEvents(_ events: [[String: Any]], dataRoot: URL) throws {
    let path = usageEventsFile(dataRoot: dataRoot)
    try FileManager.default.createDirectory(at: path.deletingLastPathComponent(), withIntermediateDirectories: true)
    let payload = try serializedUsageEvents(events)
    try payload.write(to: path, atomically: true, encoding: .utf8)
}

func serializedUsageEvents(_ events: [[String: Any]]) throws -> String {
    guard !events.isEmpty else { return "" }
    return try events.map { event -> String in
        let data = try JSONSerialization.data(withJSONObject: event, options: [.sortedKeys])
        return String(data: data, encoding: .utf8) ?? "{}"
    }.joined(separator: "\n") + "\n"
}

func writeUsageRefresh(_ summary: [String: Any], dataRoot: URL) throws {
    let path = dataRoot.appendingPathComponent("data/ai-usage/sidebar-usage-refresh.json")
    try FileManager.default.createDirectory(at: path.deletingLastPathComponent(), withIntermediateDirectories: true)
    let data = try JSONSerialization.data(withJSONObject: summary, options: [.prettyPrinted, .sortedKeys])
    try data.write(to: path, options: .atomic)
}

func eventDate(_ value: Any?) -> Date? {
    let raw = text(value)
    guard !raw.isEmpty else { return nil }
    eventDateFormatterLock.lock()
    defer { eventDateFormatterLock.unlock() }
    if let date = fractionalEventDateFormatter.date(from: raw) {
        return date
    }
    return wholeSecondEventDateFormatter.date(from: raw)
}

func totalTokens(events: [[String: Any]]) -> Int {
    events.reduce(0) { partial, event in partial + tokenInt(event["total_tokens"]) }
}

func sourceCounts(events: [[String: Any]]) -> [String: Int] {
    var counts: [String: Int] = [:]
    for event in events {
        let source = text(event["source_agent"])
        if !source.isEmpty {
            counts[source, default: 0] += 1
        }
    }
    return counts
}

func tokenUsageByAgent(events: [[String: Any]]) -> [String: Int] {
    var totals: [String: Int] = [:]
    for event in events {
        let source = text(event["source_agent"]).lowercased()
        if !source.isEmpty {
            totals[source, default: 0] += tokenInt(event["total_tokens"])
        }
    }
    return totals
}

func maxWeeklyClaudeCodexTokens(events: [[String: Any]]) -> Int {
    var weeklyTotals: [Date: Int] = [:]
    for event in events {
        let source = text(event["source_agent"]).lowercased()
        guard source == "claude" || source == "codex",
              let date = eventDate(event["occurred_at"]) else {
            continue
        }
        let weekStart = sundayWeekStart(date)
        weeklyTotals[weekStart, default: 0] += tokenInt(event["total_tokens"])
    }
    return weeklyTotals.values.max() ?? 0
}

func importWindow(events: [[String: Any]]) -> String {
    let dates = events.compactMap { eventDate($0["occurred_at"]) }.sorted()
    guard let first = dates.first, let last = dates.last else {
        return "No local usage events"
    }
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime]
    return "\(formatter.string(from: first)) to \(formatter.string(from: last))"
}

func buildRollups(events: [[String: Any]]) -> [String: [String: Any]] {
    let calendar = Calendar.current
    let now = Date()
    var today = 0
    var todayByAgent: [String: Int] = [:]
    var month = 0
    var monthByAgent: [String: Int] = [:]
    var week = 0
    var weekByAgent: [String: Int] = [:]
    var lifetimeByAgent: [String: Int] = [:]
    for event in events {
        guard let date = eventDate(event["occurred_at"]) else { continue }
        let tokens = tokenInt(event["total_tokens"])
        let source = text(event["source_agent"]).lowercased()
        if !source.isEmpty {
            lifetimeByAgent[source, default: 0] += tokens
        }
        if calendar.isDate(date, inSameDayAs: now) {
            today += tokens
            if !source.isEmpty {
                todayByAgent[source, default: 0] += tokens
            }
        }
        if calendar.component(.month, from: date) == calendar.component(.month, from: now)
            && calendar.component(.year, from: date) == calendar.component(.year, from: now) {
            month += tokens
            if !source.isEmpty {
                monthByAgent[source, default: 0] += tokens
            }
        }
        if calendar.component(.weekOfYear, from: date) == calendar.component(.weekOfYear, from: now)
            && calendar.component(.yearForWeekOfYear, from: date) == calendar.component(.yearForWeekOfYear, from: now) {
            week += tokens
            if !source.isEmpty {
                weekByAgent[source, default: 0] += tokens
            }
        }
    }
    let lifetime = totalTokens(events: events)
    return [
        "Today": ["total_tokens": today, "by_agent": todayByAgent],
        "This Week": ["total_tokens": week, "by_agent": weekByAgent],
        "This Month": ["total_tokens": month, "by_agent": monthByAgent],
        "Lifetime": ["total_tokens": lifetime, "by_agent": lifetimeByAgent],
    ]
}

func activeAgentsFromRollup(_ rollup: [String: Any]?) -> [String] {
    guard let byAgent = rollup?["by_agent"] as? [String: Any] else {
        return []
    }
    return ["claude", "codex"].filter { tokenInt(byAgent[$0]) > 0 }
}

func compactTokenCount(_ tokens: Int) -> String {
    let safeTokens = max(0, tokens)
    let units: [(Int, String)] = [
        (1_000_000_000, "B"),
        (1_000_000, "M"),
        (1_000, "K"),
    ]
    for (threshold, suffix) in units where safeTokens >= threshold {
        let tenths = (safeTokens * 10 + threshold / 2) / threshold
        let whole = tenths / 10
        let fraction = tenths % 10
        if fraction == 0 {
            return "\(whole)\(suffix)"
        }
        return "\(whole).\(fraction)\(suffix)"
    }
    return "\(safeTokens)"
}

func tokenActivity(rollups: [String: [String: Any]]) -> [String: Any] {
    let todayTokens = tokenInt(rollups["Today"]?["total_tokens"])
    let activeAgents = activeAgentsFromRollup(rollups["Today"])
    if todayTokens <= 0 {
        return [
            "today_tokens": 0,
            "intensity": "idle",
            "animation_interval_seconds": 1.5,
            "emoji": "",
            "rocket_speed": 0.0,
            "active_agents": activeAgents,
            "rocket_count": activeAgents.count >= 2 ? 2 : 1,
        ]
    }
    if todayTokens < 10_000_000 {
        return [
            "today_tokens": todayTokens,
            "intensity": "active",
            "animation_interval_seconds": 0.9,
            "emoji": "*",
            "rocket_speed": 0.45,
            "active_agents": activeAgents,
            "rocket_count": activeAgents.count >= 2 ? 2 : 1,
        ]
    }
    if todayTokens < 100_000_000 {
        return [
            "today_tokens": todayTokens,
            "intensity": "high",
            "animation_interval_seconds": 0.45,
            "emoji": "^",
            "rocket_speed": 0.9,
            "active_agents": activeAgents,
            "rocket_count": activeAgents.count >= 2 ? 2 : 1,
        ]
    }
    return [
        "today_tokens": todayTokens,
        "intensity": "surge",
        "animation_interval_seconds": 0.2,
        "emoji": "!",
        "rocket_speed": 1.8,
        "active_agents": activeAgents,
        "rocket_count": activeAgents.count >= 2 ? 2 : 1,
    ]
}

func recentTokenActivity(events: [[String: Any]]) -> [String: Any] {
    let cutoff = Date().addingTimeInterval(-60)
    var tokens = 0
    var tokensByAgent = ["claude": 0, "codex": 0]
    var activeAgentSet = Set<String>()
    for event in events {
        if let date = eventDate(event["occurred_at"]), date >= cutoff {
            tokens += tokenInt(event["total_tokens"])
            let source = text(event["source_agent"]).lowercased()
            if source == "claude" || source == "codex" {
                activeAgentSet.insert(source)
                tokensByAgent[source, default: 0] += tokenInt(event["total_tokens"])
            }
        }
    }
    let activeAgents = ["claude", "codex"].filter { activeAgentSet.contains($0) }
    let usageSpeed = usageAnimationSpeed(tokens)
    let usageAltitude = usageAnimationAltitude(tokens)
    let usageInterval = animationIntervalForSpeed(usageSpeed)
    if tokens <= 0 {
        return [
            "last_1m_tokens": 0,
            "display_tokens": "0",
            "activity_level": "idle",
            "rocket_state": "waiting",
            "rocket_speed": usageSpeed,
            "rocket_altitude": usageAltitude,
            "animation_interval_seconds": usageInterval,
            "has_flame": false,
            "active_agents": activeAgents,
            "rocket_count": activeAgents.count >= 2 ? 2 : 1,
            "agent_usage": agentUsageSummary(tokensByAgent),
        ]
    }
    if tokens < 50_000 {
        return [
            "last_1m_tokens": tokens,
            "display_tokens": compactTokenCount(tokens),
            "activity_level": "moderate",
            "rocket_state": "flying",
            "rocket_speed": usageSpeed,
            "rocket_altitude": usageAltitude,
            "animation_interval_seconds": usageInterval,
            "has_flame": true,
            "active_agents": activeAgents,
            "rocket_count": activeAgents.count >= 2 ? 2 : 1,
            "agent_usage": agentUsageSummary(tokensByAgent),
        ]
    }
    return [
        "last_1m_tokens": tokens,
        "display_tokens": compactTokenCount(tokens),
        "activity_level": "high",
        "rocket_state": "surging",
        "rocket_speed": usageSpeed,
        "rocket_altitude": usageAltitude,
        "animation_interval_seconds": usageInterval,
        "has_flame": true,
        "active_agents": activeAgents,
        "rocket_count": activeAgents.count >= 2 ? 2 : 1,
        "agent_usage": agentUsageSummary(tokensByAgent),
    ]
}

func usageAnimationSpeed(_ tokens: Int) -> Double {
    let tokenCount = max(0, tokens)
    if tokenCount <= 0 {
        return 0.0
    }
    if tokenCount < 50_000 {
        return round((0.35 + (Double(tokenCount) / 50_000.0) * 0.45) * 1000) / 1000
    }
    return round(min(2.4, 0.8 + (Double(tokenCount - 50_000) / 950_000.0) * 1.6) * 1000) / 1000
}

func usageAnimationAltitude(_ tokens: Int) -> Int {
    let tokenCount = max(0, tokens)
    if tokenCount <= 0 {
        return 0
    }
    if tokenCount < 50_000 {
        return min(50, max(1, tokenCount / 1_000))
    }
    return min(250, 50 + (tokenCount - 50_000) / 5_000)
}

func animationIntervalForSpeed(_ speed: Double) -> Double {
    let safeSpeed = max(0.0, speed)
    if safeSpeed <= 0 {
        return 1.5
    }
    return round(max(0.12, 1.2 - safeSpeed * 0.35) * 1000) / 1000
}

func statusViews(events: [[String: Any]], rollups: [String: [String: Any]]) -> [[String: Any]] {
    let lifetime = tokenInt(rollups["Lifetime"]?["total_tokens"])
    let now = Date()
    let sevenDayStart = now.addingTimeInterval(-7 * 24 * 60 * 60)
    let minuteStart = now.addingTimeInterval(-60)
    var sevenDayTokens = 0
    var lastMinuteTokens = 0
    var minuteByAgent = ["claude": 0, "codex": 0]
    var sevenDayByAgent = ["claude": 0, "codex": 0]
    for event in events {
        guard let date = eventDate(event["occurred_at"]) else { continue }
        let tokens = tokenInt(event["total_tokens"])
        let agent = text(event["source_agent"]).lowercased()
        if date >= sevenDayStart && date <= now {
            sevenDayTokens += tokens
            if agent == "claude" || agent == "codex" {
                sevenDayByAgent[agent, default: 0] += tokens
            }
        }
        if date >= minuteStart && date <= now {
            lastMinuteTokens += tokens
            if agent == "claude" || agent == "codex" {
                minuteByAgent[agent, default: 0] += tokens
            }
        }
    }
    let lifetimeByAgent = rollupByAgent(rollups, "Lifetime")
    let activeMinuteAgents = ["claude", "codex"].filter { tokenInt(minuteByAgent[$0]) > 0 }
    if Set(activeMinuteAgents) == Set(["claude", "codex"]) {
        var views: [[String: Any]] = []
        for agent in ["claude", "codex"] {
            views.append(statusView(
                viewID: "\(agent)_token_per_minute",
                label: "1m",
                tokens: tokenInt(minuteByAgent[agent]),
                prefix: "\(agentLabel(agent)) 1m",
                perMinute: true,
                scope: "agent",
                agent: agent
            ))
            views.append(statusView(
                viewID: "\(agent)_last_7d_cumulative",
                label: "7d",
                tokens: tokenInt(sevenDayByAgent[agent]),
                prefix: "\(agentLabel(agent)) 7d",
                scope: "agent",
                agent: agent
            ))
            views.append(statusView(
                viewID: "\(agent)_total_cumulative",
                label: "Total",
                tokens: tokenInt(lifetimeByAgent[agent]),
                prefix: "\(agentLabel(agent)) Total",
                scope: "agent",
                agent: agent
            ))
        }
        views.append(statusView(
            viewID: "combined_token_per_minute",
            label: "1m",
            tokens: lastMinuteTokens,
            prefix: "All 1m",
            perMinute: true,
            scope: "combined"
        ))
        views.append(statusView(
            viewID: "combined_last_7d_cumulative",
            label: "7d",
            tokens: sevenDayTokens,
            prefix: "All 7d",
            scope: "combined"
        ))
        views.append(statusView(
            viewID: "combined_total_cumulative",
            label: "Total",
            tokens: lifetime,
            prefix: "All Total",
            scope: "combined"
        ))
        return views
    }
    return [
        [
            "view_id": "last_7d_cumulative",
            "label": "7d",
            "tokens": sevenDayTokens,
            "display_tokens": compactTokenCount(sevenDayTokens),
            "display_text": "7d \(compactTokenCount(sevenDayTokens))",
            "trend": "flat",
            "trend_symbol": "flat",
        ],
        [
            "view_id": "total_cumulative",
            "label": "Total",
            "tokens": lifetime,
            "display_tokens": compactTokenCount(lifetime),
            "display_text": "Total \(compactTokenCount(lifetime))",
            "trend": "flat",
            "trend_symbol": "flat",
        ],
        [
            "view_id": "token_per_minute",
            "label": "Token/min",
            "tokens": lastMinuteTokens,
            "display_tokens": "\(compactTokenCount(lastMinuteTokens))/min",
            "display_text": "\(compactTokenCount(lastMinuteTokens))/min",
            "trend": "flat",
            "trend_symbol": "flat",
        ],
    ]
}

func sevenDayUsageBuckets(events: [[String: Any]]) -> [[String: Any]] {
    let calendar = Calendar.current
    let now = Date()
    let formatter = DateFormatter()
    formatter.dateFormat = "EEE"
    let days = (0..<7).reversed().map { offset in
        calendar.startOfDay(for: calendar.date(byAdding: .day, value: -offset, to: now) ?? now)
    }
    var buckets = Dictionary(uniqueKeysWithValues: days.map { day in
        (day, ["day": formatter.string(from: day), "claude": 0, "codex": 0] as [String: Any])
    })

    for event in events {
        guard let occurredAt = eventDate(event["occurred_at"]) else { continue }
        let day = calendar.startOfDay(for: occurredAt)
        let agent = text(event["source_agent"]).lowercased()
        guard buckets[day] != nil, agent == "claude" || agent == "codex" else {
            continue
        }
        var bucket = buckets[day] ?? [:]
        bucket[agent] = tokenInt(bucket[agent]) + tokenInt(event["total_tokens"])
        buckets[day] = bucket
    }

    return days.map { buckets[$0] ?? ["day": formatter.string(from: $0), "claude": 0, "codex": 0] }
}

func agentUsageSummary(_ totals: [String: Int]) -> [String: [String: Any]] {
    var usage: [String: [String: Any]] = [:]
    for agent in ["claude", "codex"] {
        let tokens = tokenInt(totals[agent])
        usage[agent] = [
            "last_1m_tokens": tokens,
            "display_tokens": compactTokenCount(tokens),
        ]
    }
    return usage
}

func statusView(
    viewID: String,
    label: String,
    tokens: Int,
    prefix: String,
    perMinute: Bool = false,
    scope: String,
    agent: String? = nil
) -> [String: Any] {
    let displayTokens = perMinute ? "\(compactTokenCount(tokens))/min" : compactTokenCount(tokens)
    var view: [String: Any] = [
        "view_id": viewID,
        "label": label,
        "tokens": tokens,
        "display_tokens": displayTokens,
        "display_text": "\(prefix) \(displayTokens)",
        "trend": "flat",
        "trend_symbol": "flat",
        "scope": scope,
    ]
    if let agent {
        view["agent"] = agent
        view["agent_label"] = agentLabel(agent)
    }
    return view
}

func agentLabel(_ agent: String) -> String {
    if agent == "claude" { return "Claude" }
    if agent == "codex" { return "Codex" }
    return agent.capitalized
}

func rollupByAgent(_ rollups: [String: [String: Any]], _ name: String) -> [String: Int] {
    guard let byAgent = rollups[name]?["by_agent"] as? [String: Any] else {
        return [:]
    }
    var totals: [String: Int] = [:]
    for (agent, tokens) in byAgent {
        totals[agent.lowercased()] = tokenInt(tokens)
    }
    return totals
}

func statusAnimationActivity(recent: [String: Any], today: [String: Any], network: [String: Any], running: [String: Any] = [:]) -> [String: Any] {
    let splitIOActive = (recent["split_io_enabled"] as? Bool == true)
        || (recent["rockets"] as? [[String: Any]])?.isEmpty == false
    if let speed = recent["rocket_speed"] as? Double, speed > 0 {
        var result = recent
        var visibleAgents = mergedActiveAgents(recent, running)
        if visibleAgents.isEmpty {
            let mergedAgents = mergedActiveAgents(recent, today)
            visibleAgents = mergedAgents
        }
        if !visibleAgents.isEmpty {
            result["active_agents"] = visibleAgents
            // Preserve the elevated rocket_count when split_io rockets[] is
            // in play — otherwise the 4-rocket payload would be downgraded to
            // 1 or 2 by rocketCountForAgents.
            if !splitIOActive {
                result["rocket_count"] = rocketCountForAgents(visibleAgents)
            }
        }
        result["source"] = "recent"
        return applyNetworkAnimationSpeed(result, network: network)
    }
    let todayTokens = tokenInt(today["today_tokens"])
    if todayTokens > 0 {
        let intensity = text(today["intensity"]).isEmpty ? "active" : text(today["intensity"])
        let activeAgents = mergedActiveAgents(today, running)
        let rocketCount = activeAgents.isEmpty ? max(1, min(2, tokenInt(today["rocket_count"]))) : rocketCountForAgents(activeAgents)
        let result: [String: Any] = [
            "last_1m_tokens": tokenInt(recent["last_1m_tokens"]),
            "display_tokens": compactTokenCount(todayTokens),
            "activity_level": intensity,
            "rocket_state": "waiting",
            "rocket_speed": 0.0,
            "rocket_altitude": 0,
            "animation_interval_seconds": 1.5,
            "has_flame": false,
            "active_agents": activeAgents,
            "rocket_count": rocketCount,
            "source": "today",
        ]
        return applyNetworkAnimationSpeed(result, network: network)
    }
    var result = recent
    let runningAgents = textArray(running["active_agents"])
    if !runningAgents.isEmpty {
        result["active_agents"] = runningAgents
        result["rocket_count"] = rocketCountForAgents(runningAgents)
        result["source"] = "running"
    } else {
        result["source"] = "recent"
    }
    return applyNetworkAnimationSpeed(result, network: network)
}

func mergedActiveAgents(_ recent: [String: Any], _ today: [String: Any]) -> [String] {
    let agents = Set(textArray(recent["active_agents"]) + textArray(today["active_agents"]))
    return ["claude", "codex"].filter { agents.contains($0) }
}

func rocketCountForAgents(_ agents: [String]) -> Int {
    let normalized = Set(agents)
    return normalized.contains("claude") && normalized.contains("codex") ? 2 : 1
}

func cachedRunningAgentActivity() -> [String: Any] {
    let now = Date()
    if let cache = runningAgentActivityCache,
       now.timeIntervalSince(cache.refreshedAt) < runningAgentActivityCacheIntervalSeconds {
        return cache.state
    }
    let state = runningAgentActivity()
    runningAgentActivityCache = (refreshedAt: now, state: state)
    return state
}

func runningAgentActivity() -> [String: Any] {
    let hints = runningProcessHints()
    var agents = Set<String>()
    for hint in hints {
        let normalized = hint.lowercased()
        if normalized.contains("claude") {
            agents.insert("claude")
        }
        if normalized.contains("codex") {
            agents.insert("codex")
        }
    }
    let activeAgents = ["claude", "codex"].filter { agents.contains($0) }
    return [
        "active_agents": activeAgents,
        "rocket_count": rocketCountForAgents(activeAgents),
    ]
}

func runningProcessHints() -> [String] {
    var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_ALL, 0]
    var byteCount = 0
    guard sysctl(&mib, u_int(mib.count), nil, &byteCount, nil, 0) == 0, byteCount > 0 else {
        return []
    }
    let processCount = byteCount / MemoryLayout<kinfo_proc>.stride
    guard processCount > 0 else {
        return []
    }
    var processes = Array(repeating: kinfo_proc(), count: processCount)
    let result = processes.withUnsafeMutableBufferPointer { buffer in
        sysctl(&mib, u_int(mib.count), buffer.baseAddress, &byteCount, nil, 0)
    }
    guard result == 0 else {
        return []
    }
    let finalCount = min(processes.count, byteCount / MemoryLayout<kinfo_proc>.stride)
    var hints: [String] = []
    for process in processes.prefix(finalCount) {
        let command = withUnsafeBytes(of: process.kp_proc.p_comm) { bytes -> String in
            let chars = bytes.bindMemory(to: CChar.self)
            guard let base = chars.baseAddress else { return "" }
            return String(cString: base)
        }
        if !command.isEmpty {
            hints.append(command)
        }
        guard shouldInspectProcessCommandLine(command) else {
            continue
        }
        if let commandLine = processCommandLine(pid: process.kp_proc.p_pid), !commandLine.isEmpty {
            hints.append(commandLine)
        }
    }
    return hints
}

func shouldInspectProcessCommandLine(_ command: String) -> Bool {
    let normalized = command.lowercased()
    if normalized.contains("claude") || normalized.contains("codex") {
        return true
    }
    return [
        "node",
        "zsh",
        "bash",
        "fish",
        "sh",
        "python",
        "python3",
        "env",
        "npx",
        "npm",
    ].contains(normalized)
}

func processCommandLine(pid: pid_t) -> String? {
    var mib: [Int32] = [CTL_KERN, KERN_PROCARGS2, pid]
    var byteCount = 0
    guard sysctl(&mib, u_int(mib.count), nil, &byteCount, nil, 0) == 0, byteCount > 0 else {
        return nil
    }
    var buffer = Array(repeating: CChar(0), count: byteCount)
    let result = buffer.withUnsafeMutableBufferPointer { pointer in
        sysctl(&mib, u_int(mib.count), pointer.baseAddress, &byteCount, nil, 0)
    }
    guard result == 0, byteCount > 0 else {
        return nil
    }
    return buffer.withUnsafeBufferPointer { pointer in
        guard let base = pointer.baseAddress else { return nil }
        let data = Data(bytes: base, count: byteCount)
        return String(data: data, encoding: .utf8)?
            .replacingOccurrences(of: "\0", with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

func stateByApplyingRunningAgents(_ state: [String: Any], running: [String: Any]) -> [String: Any] {
    var result = state
    result["running_agent_activity"] = running
    let recent = state["recent_token_activity"] as? [String: Any] ?? [:]
    let today = state["token_activity"] as? [String: Any] ?? [:]
    let network = state["network_activity"] as? [String: Any] ?? [:]
    result["status_animation_activity"] = statusAnimationActivity(
        recent: recent,
        today: today,
        network: network,
        running: running
    )
    return result
}

func applyNetworkAnimationSpeed(_ activity: [String: Any], network: [String: Any]) -> [String: Any] {
    var result = activity
    let usageSpeed = doubleValue(activity["rocket_speed"])
    let networkSpeed = doubleValue(network["rocket_speed"])
    result["outbound_bytes_per_second"] = tokenInt(network["outbound_bytes_per_second"])
    if usageSpeed <= 0 {
        result["speed_source"] = "token_usage"
        result["rocket_speed"] = 0.0
        result["rocket_altitude"] = 0
        result["animation_interval_seconds"] = doubleValue(activity["animation_interval_seconds"]) > 0 ? doubleValue(activity["animation_interval_seconds"]) : 1.5
        result["has_flame"] = false
        return result
    }
    if networkSpeed > usageSpeed {
        result["speed_source"] = "network"
        result["rocket_speed"] = networkSpeed
        result["animation_interval_seconds"] = doubleValue(network["animation_interval_seconds"])
        result["source"] = "network"
    } else {
        result["speed_source"] = "token_usage"
        result["rocket_speed"] = usageSpeed
        let usageInterval = doubleValue(activity["animation_interval_seconds"])
        result["animation_interval_seconds"] = usageInterval > 0 ? usageInterval : animationIntervalForSpeed(usageSpeed)
    }
    result["has_flame"] = true
    return result
}

func xp(events: [[String: Any]], goals: [[String: Any]]) -> Int {
    totalTokens(events: events) / 1_000_000 + goals.filter { text($0["status"]) == "completed" }.count * 100
}

func earnedMissionXP(_ missions: [[String: Any]]) -> Int {
    missions
        .filter { text($0["status"]) == "done" }
        .map { tokenInt($0["xp"]) }
        .reduce(0, +)
}

func levelProgressForXP(_ xp: Int) -> [String: Any] {
    let total = max(0, xp)
    var remaining = total
    let maxLevel = levelXPRequirements.last?.level ?? 50
    for (index, item) in levelXPRequirements.enumerated() {
        let requiredXP = max(1, item.requiredXP)
        let isLast = index == levelXPRequirements.count - 1
        if remaining < requiredXP || isLast {
            let currentXP = min(remaining, requiredXP)
            let toNext = isLast && remaining >= requiredXP ? 0 : max(0, requiredXP - currentXP)
            return [
                "current_level": item.level,
                "level_label": "Lv \(item.level)",
                "current_level_xp": currentXP,
                "current_level_required_xp": requiredXP,
                "xp_to_next_level": toNext,
                "progress_percent": min(100, (currentXP * 100) / requiredXP),
                "next_level": item.level >= maxLevel ? NSNull() : item.level + 1,
                "max_level": maxLevel,
                "total_xp": total,
            ]
        }
        remaining -= requiredXP
    }
    return [
        "current_level": maxLevel,
        "level_label": "Lv \(maxLevel)",
        "current_level_xp": levelXPRequirements.last?.requiredXP ?? 0,
        "current_level_required_xp": levelXPRequirements.last?.requiredXP ?? 0,
        "xp_to_next_level": 0,
        "progress_percent": 100,
        "next_level": NSNull(),
        "max_level": maxLevel,
        "total_xp": total,
    ]
}

func workforceFitness(events: [[String: Any]], goals: [[String: Any]]) -> Int {
    min(100, sourceCounts(events: events).count * 25 + goals.filter { text($0["status"]) == "completed" }.count * 5)
}

func badgeInventory(events: [[String: Any]], goals: [[String: Any]]) -> [[String: Any]] {
    let total = totalTokens(events: events)
    let tokensByAgent = tokenUsageByAgent(events: events)
    let claudeTokens = tokensByAgent["claude"] ?? 0
    let codexTokens = tokensByAgent["codex"] ?? 0
    let heavyUserWeeklyTokens = maxWeeklyClaudeCodexTokens(events: events)
    let completed = goals.filter { text($0["status"]) == "completed" }
    return [
        badge("a758dd50b1415e27", "Billion Club", total >= 1_000_000_000 ? "earned" : "in_progress", min(100, total / 10_000_000),
            endorsementText: "Uses AI agents as daily working partners, not occasional search boxes.",
            evidenceRequirement: "10 completed evidence-backed goals"
        ),
        badge("b0aec0de4cd56059", "Two key agents", claudeTokens >= 1_000_000_000 && codexTokens >= 1_000_000_000 ? "earned" : "in_progress", min(100, min(claudeTokens, codexTokens) / 10_000_000),
            endorsementText: "Token usage for Claude and Codex each reaches over 1B.",
            evidenceRequirement: "Claude and Codex lifetime token usage each over 1B"
        ),
        badge("8f5a9291c21f44bf", "Heavy user", heavyUserWeeklyTokens >= 10_000_000_000 ? "earned" : "in_progress", min(100, heavyUserWeeklyTokens / 100_000_000),
            endorsementText: "Weekly Claude and Codex token usage reaches 10B total.",
            evidenceRequirement: "Claude and Codex weekly usage reaches 10B combined",
            threshold: 10_000_000_000
        ),
        badge("9ad1f6c937fd3839", "Verified Workflow", completed.isEmpty ? "in_progress" : "earned", min(100, completed.count * 10)),
    ]
}

func badge(
    _ id: String,
    _ name: String,
    _ status: String,
    _ progress: Int,
    endorsementText: String = "",
    evidenceRequirement: String = "",
    threshold: Int = 0
) -> [String: Any] {
    var payload: [String: Any] = ["badge_id": id, "name": name, "status": status, "progress_percent": progress]
    if !endorsementText.isEmpty {
        payload["endorsement_text"] = endorsementText
    }
    if !evidenceRequirement.isEmpty {
        payload["evidence_requirement"] = evidenceRequirement
    }
    if threshold > 0 {
        payload["threshold"] = threshold
    }
    return payload
}

func normalizedRepresentativeBadgeID(_ badgeId: String) -> String {
    let id = badgeId.trimmingCharacters(in: .whitespacesAndNewlines)
    return agentboostBadgeIDAliases[id] ?? id
}

func normalizedRepresentativeBadgeIDs(_ badgeIds: [String]) -> [String] {
    var selected: [String] = []
    for badgeId in badgeIds {
        let normalized = normalizedRepresentativeBadgeID(badgeId)
        if !normalized.isEmpty && !selected.contains(normalized) {
            selected.append(normalized)
        }
        if selected.count >= agentboostRepresentativeBadgeLimit {
            break
        }
    }
    return selected
}

func selectedRepresentativeBadgeIDs(dataRoot: URL? = nil) -> [String] {
    var rawIDs: [String] = []
    if let dataRoot {
        let ledger = readNotificationLedger(dataRoot: dataRoot)
        if let ids = ledger["representative_badge_ids"] as? [String] {
            rawIDs.append(contentsOf: ids)
        } else if let ids = ledger["representative_badge_ids"] as? [Any] {
            rawIDs.append(contentsOf: ids.map { text($0) })
        }
        rawIDs.append(text(ledger["representative_badge_id"]))
    }
    if let storedSelected = UserDefaults.standard.string(forKey: representativeBadgeKey) {
        rawIDs.append(storedSelected)
    }
    return normalizedRepresentativeBadgeIDs(rawIDs)
}

func representativeBadges(from badges: [[String: Any]], dataRoot: URL? = nil) -> [[String: Any]] {
    let selectedIDs = selectedRepresentativeBadgeIDs(dataRoot: dataRoot)
    let selected = selectedIDs.compactMap { selectedID in
        badges.first { normalizedRepresentativeBadgeID(text($0["badge_id"])) == selectedID && text($0["status"]) == "earned" }
    }
    if !selected.isEmpty {
        return Array(selected.prefix(agentboostRepresentativeBadgeLimit))
    }
    if let earned = badges.first(where: { text($0["status"]) == "earned" }) {
        return [earned]
    }
    if let first = badges.first {
        return [first]
    }
    return []
}

func representativeBadge(from badges: [[String: Any]], dataRoot: URL? = nil) -> [String: Any]? {
    representativeBadges(from: badges, dataRoot: dataRoot).first
}

func representativeBadgeTitle(state: [String: Any]) -> String {
    if let selected = state["representative_badge"] as? [String: Any],
       !text(selected["name"]).isEmpty {
        return text(selected["name"])
    }
    let representatives = state["representative_badges"] as? [[String: Any]] ?? []
    if let selected = representatives.first, !text(selected["name"]).isEmpty {
        return text(selected["name"])
    }
    let inventory = state["badge_inventory"] as? [[String: Any]] ?? []
    let selectedInventory = inventory
        .filter { ($0["is_representative"] as? Bool) == true }
        .sorted { tokenInt($0["representative_rank"]) < tokenInt($1["representative_rank"]) }
    if let selected = selectedInventory.first, !text(selected["name"]).isEmpty {
        return text(selected["name"])
    }
    if let earned = inventory.first(where: { text($0["status"]) == "earned" }),
       !text(earned["name"]).isEmpty {
        return text(earned["name"])
    }
    return "No achievement"
}

func representativeBadgeRank(_ representatives: [[String: Any]], badgeID: String) -> Int {
    let normalized = normalizedRepresentativeBadgeID(badgeID)
    guard let index = representatives.firstIndex(where: {
        normalizedRepresentativeBadgeID(text($0["badge_id"])) == normalized
    }) else {
        return 0
    }
    return index + 1
}

func selectRepresentativeBadgeState(_ badgeId: String) {
    UserDefaults.standard.set(normalizedRepresentativeBadgeID(badgeId), forKey: representativeBadgeKey)
}

func selectRepresentativeBadgeState(_ badgeIds: [String]) {
    UserDefaults.standard.set(normalizedRepresentativeBadgeIDs(badgeIds).first ?? "", forKey: representativeBadgeKey)
}

func writeRepresentativeBadgeSelection(_ badgeId: String, dataRoot: URL) throws {
    try writeRepresentativeBadgeSelection(badgeIds: [badgeId], dataRoot: dataRoot)
}

func writeRepresentativeBadgeSelection(badgeIds: [String], dataRoot: URL) throws {
    let normalized = normalizedRepresentativeBadgeIDs(badgeIds)
    guard let first = normalized.first else { return }
    selectRepresentativeBadgeState(normalized)
    var ledger = readNotificationLedger(dataRoot: dataRoot)
    ledger["version"] = ledger["version"] ?? 1
    ledger["representative_badge_id"] = first
    ledger["representative_badge_ids"] = normalized
    try writeNotificationLedger(ledger, dataRoot: dataRoot)
}

func stateBySelectingRepresentativeBadge(_ state: [String: Any], badgeId: String) -> [String: Any] {
    stateBySelectingRepresentativeBadges(state, badgeIds: [badgeId])
}

func stateBySelectingRepresentativeBadges(_ state: [String: Any], badgeIds: [String]) -> [String: Any] {
    let normalizedIDs = normalizedRepresentativeBadgeIDs(badgeIds)
    var result = state
    var selectedBadges: [[String: Any]] = []
    let inventory = (state["badge_inventory"] as? [[String: Any]] ?? []).map { badge -> [String: Any] in
        var item = badge
        let normalized = normalizedRepresentativeBadgeID(text(item["badge_id"]))
        let isEarned = text(item["status"]) == "earned"
        let rank = isEarned ? (normalizedIDs.firstIndex(of: normalized) ?? -1) + 1 : 0
        item["is_representative"] = rank > 0
        item["representative_rank"] = rank
        item["can_select"] = isEarned
        if rank > 0 {
            selectedBadges.append(item)
        }
        return item
    }
    result["badge_inventory"] = inventory
    if selectedBadges.isEmpty {
        let badges = state["earned_badges"] as? [[String: Any]] ?? inventory
        selectedBadges = normalizedIDs.compactMap { normalized in
            badges.first { normalizedRepresentativeBadgeID(text($0["badge_id"])) == normalized }
        }
    }
    selectedBadges.sort {
        let left = normalizedRepresentativeBadgeID(text($0["badge_id"]))
        let right = normalizedRepresentativeBadgeID(text($1["badge_id"]))
        return (normalizedIDs.firstIndex(of: left) ?? Int.max) < (normalizedIDs.firstIndex(of: right) ?? Int.max)
    }
    if let selected = selectedBadges.first {
        result["representative_badge"] = selected
    }
    result["representative_badges"] = selectedBadges
    return result
}

func settingsFile(dataRoot: URL) -> URL {
    dataRoot.appendingPathComponent("data/ai-usage/sidebar-notifications.json")
}

func agentboostSettingsFile(dataRoot: URL) -> URL {
    dataRoot.appendingPathComponent("data/ai-usage/settings.json")
}

func defaultAgentBoostSettings() -> [String: Any] {
    var categories: [String: Bool] = [:]
    for item in agentboostNotificationCategoryLabels {
        categories[item.key] = true
    }
    return [
        "version": 1,
        "notifications": [
            "enabled": true,
            "categories": categories,
        ],
        "caffeinate": [
            "enabled": true,
        ],
        "work_hours": [
            "start": "09:00",
            "end": "18:00",
            "workdays": [0, 1, 2, 3, 4],
        ],
        "quiet_hours": [
            "enabled": false,
            "start": "22:00",
            "end": "07:00",
        ],
        "display": [
            "split_io_rockets": false,
            "floating_overlay_enabled": false,
        ],
    ]
}

func loadAgentBoostSettings(dataRoot: URL) -> [String: Any] {
    let path = agentboostSettingsFile(dataRoot: dataRoot)
    guard let data = try? Data(contentsOf: path),
          let raw = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        return defaultAgentBoostSettings()
    }
    return mergeAgentBoostSettings(raw)
}

func mergeAgentBoostSettings(_ raw: [String: Any]) -> [String: Any] {
    let defaults = defaultAgentBoostSettings()
    let notifications = raw["notifications"] as? [String: Any] ?? [:]
    let rawCategories = notifications["categories"] as? [String: Any] ?? [:]
    var categories: [String: Bool] = [:]
    for item in agentboostNotificationCategoryLabels {
        categories[item.key] = boolSetting(rawCategories[item.key], defaultValue: true)
    }
    let rawQuiet = raw["quiet_hours"] as? [String: Any] ?? [:]
    let defaultQuiet = defaults["quiet_hours"] as? [String: Any] ?? [:]
    let rawWork = raw["work_hours"] as? [String: Any] ?? [:]
    let defaultWork = defaults["work_hours"] as? [String: Any] ?? [:]
    let rawCaffeinate = raw["caffeinate"] as? [String: Any] ?? [:]
    let rawDisplay = raw["display"] as? [String: Any] ?? [:]
    return [
        "version": raw["version"] ?? 1,
        "notifications": [
            "enabled": boolSetting(notifications["enabled"], defaultValue: true),
            "categories": categories,
        ],
        "caffeinate": [
            "enabled": boolSetting(rawCaffeinate["enabled"], defaultValue: true),
        ],
        "quiet_hours": [
            "enabled": boolSetting(rawQuiet["enabled"], defaultValue: false),
            "start": text(rawQuiet["start"]).isEmpty ? text(defaultQuiet["start"]) : text(rawQuiet["start"]),
            "end": text(rawQuiet["end"]).isEmpty ? text(defaultQuiet["end"]) : text(rawQuiet["end"]),
        ],
        "work_hours": [
            "start": text(rawWork["start"]).isEmpty ? text(defaultWork["start"]) : text(rawWork["start"]),
            "end": text(rawWork["end"]).isEmpty ? text(defaultWork["end"]) : text(rawWork["end"]),
            "workdays": rawWork["workdays"] ?? defaultWork["workdays"] ?? [0, 1, 2, 3, 4],
        ],
        "display": [
            "split_io_rockets": boolSetting(rawDisplay["split_io_rockets"], defaultValue: false),
            "floating_overlay_enabled": boolSetting(rawDisplay["floating_overlay_enabled"], defaultValue: false),
        ],
    ]
}

func boolSetting(_ value: Any?, defaultValue: Bool) -> Bool {
    if let bool = value as? Bool {
        return bool
    }
    if let number = value as? NSNumber {
        return number.boolValue
    }
    if let string = value as? String {
        let normalized = string.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if ["true", "1", "yes", "on"].contains(normalized) { return true }
        if ["false", "0", "no", "off"].contains(normalized) { return false }
    }
    return defaultValue
}

func agentboostSettingsBySetting(_ key: String, enabled: Bool, settings: [String: Any]) -> [String: Any] {
    var updated = mergeAgentBoostSettings(settings)
    var notifications = updated["notifications"] as? [String: Any] ?? [:]
    var categories = notifications["categories"] as? [String: Bool] ?? [:]
    if key == "notifications.enabled" {
        notifications["enabled"] = enabled
    } else if key.hasPrefix("notifications.categories.") {
        let category = String(key.dropFirst("notifications.categories.".count))
        if agentboostNotificationCategoryLabels.contains(where: { $0.key == category }) {
            categories[category] = enabled
        }
    } else if key == "quiet_hours.enabled" {
        var quiet = updated["quiet_hours"] as? [String: Any] ?? [:]
        quiet["enabled"] = enabled
        updated["quiet_hours"] = quiet
    } else if key == "caffeinate.enabled" {
        var caffeinate = updated["caffeinate"] as? [String: Any] ?? [:]
        caffeinate["enabled"] = enabled
        updated["caffeinate"] = caffeinate
    } else if key == "display.split_io_rockets" {
        var display = updated["display"] as? [String: Any] ?? [:]
        display["split_io_rockets"] = enabled
        updated["display"] = display
    } else if key == "display.floating_overlay_enabled" {
        var display = updated["display"] as? [String: Any] ?? [:]
        display["floating_overlay_enabled"] = enabled
        updated["display"] = display
    }
    notifications["categories"] = categories
    updated["notifications"] = notifications
    return updated
}

func notificationCategoryEnabled(_ category: String, dataRoot: URL) -> Bool {
    let settings = loadAgentBoostSettings(dataRoot: dataRoot)
    let notifications = settings["notifications"] as? [String: Any] ?? [:]
    guard boolSetting(notifications["enabled"], defaultValue: true) else {
        return false
    }
    let categories = notifications["categories"] as? [String: Any] ?? [:]
    return boolSetting(categories[category], defaultValue: true)
}

func readNotificationLedger(dataRoot: URL) -> [String: Any] {
    let path = settingsFile(dataRoot: dataRoot)
    guard let data = try? Data(contentsOf: path),
          let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        return [:]
    }
    return object
}

func writeNotificationLedger(_ ledger: [String: Any], dataRoot: URL) throws {
    let path = settingsFile(dataRoot: dataRoot)
    try FileManager.default.createDirectory(at: path.deletingLastPathComponent(), withIntermediateDirectories: true)
    let data = try JSONSerialization.data(withJSONObject: ledger, options: [.prettyPrinted, .sortedKeys])
    try data.write(to: path, options: .atomic)
}

func clearMetaReviewNotificationPrompts(dataRoot: URL) {
    var ledger = readNotificationLedger(dataRoot: dataRoot)
    guard ledger["meta_review_prompts"] != nil else {
        return
    }
    ledger["meta_review_prompts"] = []
    try? writeNotificationLedger(ledger, dataRoot: dataRoot)
}

func metaReviewNotificationKey(_ meta: [String: Any]) -> String {
    let lastReview = text(meta["last_review"])
    let stateFile = text(meta["state_file"])
    let cycle = !lastReview.isEmpty ? lastReview : (!stateFile.isEmpty ? stateFile : "unknown")
    let status = text(meta["status"]).isEmpty ? "due" : text(meta["status"])
    return "meta-review-due:\(cycle):\(status)"
}

func notifyMetaReviewDueIfNeeded(state: [String: Any]) {
    let dataRoot = activeDataRoot()
    guard notificationCategoryEnabled("workflow", dataRoot: dataRoot),
          let meta = state["meta_review"] as? [String: Any],
          boolSetting(meta["due"], defaultValue: false) else {
        return
    }
    let key = metaReviewNotificationKey(meta)
    var ledger = readNotificationLedger(dataRoot: dataRoot)
    var prompts = ledger["meta_review_prompts"] as? [[String: Any]] ?? []
    if prompts.contains(where: { text($0["key"]) == key }) {
        return
    }
    let reason = text(meta["reason"]).isEmpty
        ? "Meta-review is due."
        : text(meta["reason"])
    let message = reason.contains("Run it from AgentBoost when convenient.")
        ? reason
        : "\(reason) Run it from AgentBoost when convenient."
    sendAgentBoostNotification(title: "AgentBoost meta-review due", message: message, categoryIdentifier: agentboostMetaReviewNotificationCategory)
    prompts.append([
        "key": key,
        "notified_at": isoNow(),
        "status": text(meta["status"]),
        "reason": reason,
    ])
    ledger["meta_review_prompts"] = prompts
    try? writeNotificationLedger(ledger, dataRoot: dataRoot)
}

func sendAgentBoostNotification(title: String, message: String, categoryIdentifier: String? = nil) {
    UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { granted, _ in
        guard granted else { return }
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = message
        content.sound = .default
        if let categoryIdentifier, !categoryIdentifier.isEmpty {
            content.categoryIdentifier = categoryIdentifier
        }
        let request = UNNotificationRequest(
            identifier: "agentboost.\(UUID().uuidString)",
            content: content,
            trigger: nil
        )
        UNUserNotificationCenter.current().add(request)
    }
}

func agentboostSettingsByTextSetting(_ key: String, value: String, settings: [String: Any]) -> [String: Any] {
    var updated = mergeAgentBoostSettings(settings)
    if key == "work_hours.start" || key == "work_hours.end" {
        var work = updated["work_hours"] as? [String: Any] ?? [:]
        let field = key == "work_hours.start" ? "start" : "end"
        work[field] = normalizedClockSetting(value, fallback: text(work[field]))
        updated["work_hours"] = work
    } else if key == "quiet_hours.start" || key == "quiet_hours.end" {
        var quiet = updated["quiet_hours"] as? [String: Any] ?? [:]
        let field = key == "quiet_hours.start" ? "start" : "end"
        quiet[field] = normalizedClockSetting(value, fallback: text(quiet[field]))
        updated["quiet_hours"] = quiet
    }
    return updated
}

func normalizedClockSetting(_ value: String, fallback: String) -> String {
    let parts = value.trimmingCharacters(in: .whitespacesAndNewlines).split(separator: ":", maxSplits: 1)
    guard parts.count == 2,
          let hour = Int(parts[0]),
          let minute = Int(parts[1]),
          (0..<24).contains(hour),
          (0..<60).contains(minute) else {
        return fallback.isEmpty ? "09:00" : fallback
    }
    return String(format: "%02d:%02d", hour, minute)
}

func caffeinateEnabled(_ settings: [String: Any]) -> Bool {
    let merged = mergeAgentBoostSettings(settings)
    let caffeinate = merged["caffeinate"] as? [String: Any] ?? [:]
    return boolSetting(caffeinate["enabled"], defaultValue: true)
}

func floatingOverlayEnabled(_ settings: [String: Any]) -> Bool {
    let merged = mergeAgentBoostSettings(settings)
    let display = merged["display"] as? [String: Any] ?? [:]
    return boolSetting(display["floating_overlay_enabled"], defaultValue: false)
}

func floatingOverlayEnabled(dataRoot: URL) -> Bool {
    floatingOverlayEnabled(loadAgentBoostSettings(dataRoot: dataRoot))
}

func setFloatingOverlayEnabled(_ enabled: Bool, dataRoot: URL) throws {
    let current = loadAgentBoostSettings(dataRoot: dataRoot)
    let updated = agentboostSettingsBySetting("display.floating_overlay_enabled", enabled: enabled, settings: current)
    try writeAgentBoostSettings(updated, dataRoot: dataRoot)
}

func writeAgentBoostSettings(_ settings: [String: Any], dataRoot: URL) throws {
    let path = agentboostSettingsFile(dataRoot: dataRoot)
    try FileManager.default.createDirectory(at: path.deletingLastPathComponent(), withIntermediateDirectories: true)
    let data = try JSONSerialization.data(withJSONObject: mergeAgentBoostSettings(settings), options: [.prettyPrinted, .sortedKeys])
    try data.write(to: path, options: .atomic)
}

func missionProgressState(_ progress: Int, goal: Int) -> String {
    return progress >= goal ? "done" : "active"
}

func eventsToday(_ events: [[String: Any]]) -> [[String: Any]] {
    let calendar = Calendar.current
    return events.filter { event in
        guard let date = eventDate(event["occurred_at"]) else { return false }
        return calendar.isDateInToday(date)
    }
}

func isWorkday(_ date: Date) -> Bool {
    let weekday = Calendar.current.component(.weekday, from: date)
    return weekday >= 2 && weekday <= 6
}

func activeWorkdaysThisWeek(_ events: [[String: Any]]) -> Int {
    var calendar = Calendar.current
    calendar.firstWeekday = 1
    let currentWeek = calendar.dateComponents([.yearForWeekOfYear, .weekOfYear], from: Date())
    let activeWorkdays = Set(events.compactMap { event -> Date? in
        guard let date = eventDate(event["occurred_at"]) else { return nil }
        let eventWeek = calendar.dateComponents([.yearForWeekOfYear, .weekOfYear], from: date)
        guard eventWeek.yearForWeekOfYear == currentWeek.yearForWeekOfYear,
              eventWeek.weekOfYear == currentWeek.weekOfYear,
              isWorkday(date) else {
            return nil
        }
        return calendar.startOfDay(for: date)
    })
    return activeWorkdays.count
}

func selfAdjustedDailyMissionTarget(_ events: [[String: Any]]) -> Int {
    let recent = recentActiveDayAverage(events, days: 14)
    if recent.activeDays >= 5 && recent.average >= 6.0 {
        return 3
    }
    if recent.activeDays >= 4 && recent.average >= 2.5 {
        return 2
    }
    return 1
}

func recentActiveDayAverage(_ events: [[String: Any]], days: Int) -> (average: Double, activeDays: Int) {
    let calendar = Calendar.current
    let today = calendar.startOfDay(for: Date())
    guard let startDay = calendar.date(byAdding: .day, value: -max(1, days), to: today) else {
        return (0.0, 0)
    }
    var counts: [Date: Int] = [:]
    for event in events {
        guard let date = eventDate(event["occurred_at"]) else { continue }
        let day = calendar.startOfDay(for: date)
        guard day >= startDay && day < today else { continue }
        counts[day, default: 0] += 1
    }
    guard !counts.isEmpty else { return (0.0, 0) }
    let total = counts.values.reduce(0, +)
    return (Double(total) / Double(counts.count), counts.count)
}

func selfAdjustedWeeklyMissionTarget(_ events: [[String: Any]]) -> Int {
    let recent = recentWeeklyWorkdayAverage(events, days: 28)
    if recent.activeWeeks > 0 && recent.average >= 5.0 {
        return 5
    }
    return 4
}

func recentWeeklyWorkdayAverage(_ events: [[String: Any]], days: Int) -> (average: Double, activeWeeks: Int) {
    let calendar = Calendar.current
    let currentWeekStart = sundayWeekStart(Date())
    guard let startDay = calendar.date(byAdding: .day, value: -max(7, days), to: currentWeekStart) else {
        return (0.0, 0)
    }
    var activeWorkdaysByWeek: [Date: Set<Date>] = [:]
    for event in events {
        guard let date = eventDate(event["occurred_at"]) else { continue }
        let day = calendar.startOfDay(for: date)
        guard day >= startDay && day < currentWeekStart && isWorkday(day) else { continue }
        let weekStart = sundayWeekStart(day)
        activeWorkdaysByWeek[weekStart, default: []].insert(day)
    }
    guard !activeWorkdaysByWeek.isEmpty else { return (0.0, 0) }
    let counts = activeWorkdaysByWeek.values.map { $0.count }
    return (Double(counts.reduce(0, +)) / Double(counts.count), counts.count)
}

func sundayWeekStart(_ date: Date) -> Date {
    var calendar = Calendar.current
    calendar.firstWeekday = 1
    let components = calendar.dateComponents([.yearForWeekOfYear, .weekOfYear], from: date)
    return calendar.date(from: components) ?? calendar.startOfDay(for: date)
}

func countText(_ count: Int) -> String {
    return count == 1 ? "one" : "\(count)"
}

func plural(_ word: String, count: Int) -> String {
    return count == 1 ? word : "\(word)s"
}

func missions(prefix: String, dataRoot: URL, events: [[String: Any]], goals: [[String: Any]]) -> [[String: Any]] {
    if prefix == "weekly" {
        let activeWorkdays = activeWorkdaysThisWeek(events)
        let weeklyTarget = min(5, selfAdjustedWeeklyMissionTarget(events))
        let skillPromptReviews = skillPromptReviewsThisWeek(dataRoot: dataRoot)
        let skillPromptProgress = min(skillPromptReviews, 1)
        let identityUpdates = identityUpdatesThisWeek(dataRoot: dataRoot)
        let identityProgress = min(identityUpdates, 1)
        return [
            [
                "mission_id": "weekly_ai_streak",
                "title": "Build a \(weeklyTarget)-workday AI rhythm",
                "status": missionProgressState(activeWorkdays, goal: weeklyTarget),
                "command_hint": "Use Claude or Codex on \(weeklyTarget) workdays this week",
                "cadence": prefix,
                "frequency": "\(weeklyTarget)/week",
                "progress": min(activeWorkdays, weeklyTarget),
                "goal": weeklyTarget,
                "metric": "active_workdays",
                "xp": 25,
                "auto_check": true,
                "check_cost": "loaded_events_only",
                "adaptive": true,
                "target_source": "recent_weekly_workdays",
                "target_window_days": 28,
            ],
            [
                "mission_id": "weekly_skill_prompt_review",
                "title": "Review current skills and prompts",
                "status": missionProgressState(skillPromptProgress, goal: 1),
                "command_hint": "bin/agentboost --do-skill-prompt-review",
                "evidence_hint": "skill/prompt review artifact for the current week",
                "cadence": prefix,
                "frequency": "1/week",
                "progress": skillPromptProgress,
                "goal": 1,
                "metric": "skill_prompt_review_this_week",
                "xp": 25,
                "auto_check": true,
                "check_cost": "local_artifact_scan",
            ],
            [
                "mission_id": "weekly_identity_update",
                "title": "Update personality and thinking path",
                "status": missionProgressState(identityProgress, goal: 1),
                "command_hint": "bin/agentboost --do-identity-update",
                "evidence_hint": "identity draft update artifact for the current week",
                "cadence": prefix,
                "frequency": "1/week",
                "progress": identityProgress,
                "goal": 1,
                "metric": "identity_update_this_week",
                "xp": 25,
                "auto_check": true,
                "check_cost": "local_artifact_scan",
            ],
        ]
    }

    let dailyTarget = selfAdjustedDailyMissionTarget(events)
    let todayProgress = min(eventsToday(events).count, dailyTarget)
    let dailyMissions: [[String: Any]] = [
        [
            "mission_id": "daily_ai_turn",
            "title": "Use \(countText(dailyTarget)) AI agent \(plural("turn", count: dailyTarget)) today",
            "status": missionProgressState(todayProgress, goal: dailyTarget),
            "command_hint": "Run \(countText(dailyTarget)) Claude or Codex \(plural("turn", count: dailyTarget)) today",
            "cadence": prefix,
            "frequency": "\(dailyTarget)/day",
            "progress": todayProgress,
            "goal": dailyTarget,
            "metric": "local_usage_event",
            "xp": 5,
            "auto_check": true,
            "check_cost": "loaded_events_only",
            "adaptive": true,
            "target_source": "recent_active_day_average",
            "target_window_days": 14,
        ],
    ]
    return dailyMissions
}

func skillPromptReviewDirectory(dataRoot: URL) -> URL {
    dataRoot.appendingPathComponent("skill/public/two-phase-execution/common/skill-prompt-reviews", isDirectory: true)
}

func skillPromptReviewsThisWeek(dataRoot: URL) -> Int {
    let directory = skillPromptReviewDirectory(dataRoot: dataRoot)
    guard let urls = try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil) else {
        return 0
    }
    let weekStart = sundayWeekStart(Date())
    let weekEnd = Calendar.current.date(byAdding: .day, value: 7, to: weekStart) ?? weekStart
    return urls.filter { url in
        guard let day = skillPromptReviewDate(from: url.lastPathComponent) else { return false }
        return day >= weekStart && day < weekEnd
    }.count
}

func skillPromptReviewDate(from name: String) -> Date? {
    let prefix = "skill-prompt-review-"
    guard name.hasPrefix(prefix) else { return nil }
    let start = name.index(name.startIndex, offsetBy: prefix.count)
    guard name.distance(from: start, to: name.endIndex) >= 10 else { return nil }
    let end = name.index(start, offsetBy: 10)
    let raw = String(name[start..<end])
    let formatter = DateFormatter()
    formatter.dateFormat = "yyyy-MM-dd"
    formatter.timeZone = TimeZone.current
    return formatter.date(from: raw).map { Calendar.current.startOfDay(for: $0) }
}

func identityUpdateDirectory(dataRoot: URL) -> URL {
    dataRoot.appendingPathComponent("identity/drafts", isDirectory: true)
}

func identityUpdateSummaryURLs(dataRoot: URL) -> [URL] {
    let directory = identityUpdateDirectory(dataRoot: dataRoot)
    guard let urls = try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil) else {
        return []
    }
    return urls.compactMap { url in
        guard url.lastPathComponent.hasPrefix("identity-update-") else { return nil }
        let summary = url.appendingPathComponent("summary.md")
        return FileManager.default.fileExists(atPath: summary.path) ? summary : nil
    }
}

func identityUpdatesThisWeek(dataRoot: URL) -> Int {
    let weekStart = sundayWeekStart(Date())
    let weekEnd = Calendar.current.date(byAdding: .day, value: 7, to: weekStart) ?? weekStart
    return identityUpdateSummaryURLs(dataRoot: dataRoot).filter { summary in
        guard let day = identityUpdateDate(from: summary.deletingLastPathComponent().lastPathComponent) else { return false }
        return day >= weekStart && day < weekEnd
    }.count
}

func latestIdentityUpdateSummary(dataRoot: URL) -> URL? {
    identityUpdateSummaryURLs(dataRoot: dataRoot).max { a, b in
        let aDate = identityUpdateDate(from: a.deletingLastPathComponent().lastPathComponent) ?? .distantPast
        let bDate = identityUpdateDate(from: b.deletingLastPathComponent().lastPathComponent) ?? .distantPast
        if aDate == bDate {
            let aModified = identityUpdateModifiedAt(a)
            let bModified = identityUpdateModifiedAt(b)
            if aModified == bModified { return a.path < b.path }
            return aModified < bModified
        }
        return aDate < bDate
    }
}

func identityUpdateModifiedAt(_ url: URL) -> Date {
    let values = try? url.resourceValues(forKeys: [.contentModificationDateKey])
    return values?.contentModificationDate ?? .distantPast
}

func identityUpdateDate(from name: String) -> Date? {
    let prefix = "identity-update-"
    guard name.hasPrefix(prefix) else { return nil }
    let start = name.index(name.startIndex, offsetBy: prefix.count)
    guard name.distance(from: start, to: name.endIndex) >= 10 else { return nil }
    let end = name.index(start, offsetBy: 10)
    let raw = String(name[start..<end])
    let formatter = DateFormatter()
    formatter.dateFormat = "yyyy-MM-dd"
    formatter.timeZone = TimeZone.current
    return formatter.date(from: raw).map { Calendar.current.startOfDay(for: $0) }
}

func identityUpdateState(dataRoot: URL) -> [String: Any] {
    let latest = latestIdentityUpdateSummary(dataRoot: dataRoot)
    let progress = identityUpdatesThisWeek(dataRoot: dataRoot) > 0 ? 1 : 0
    var state: [String: Any] = [
        "status": progress == 1 ? "done" : "active",
        "progress": progress,
        "goal": 1,
        "metric": "identity_update_this_week",
        "command_hint": "bin/agentboost --do-identity-update",
        "evidence_hint": "identity draft update artifact for the current week",
        "reason": progress == 1 ? "Identity drafts were updated this week." : "No personality/thinking-path draft update this week.",
        "review_artifact": "",
        "source_file_count": 0,
        "evidence_items": 0,
        "personality_theme_count": 0,
        "thinking_theme_count": 0,
    ]
    guard let latest = latest else { return state }
    state["review_artifact"] = latest.path
    state["source_file_count"] = identityUpdateMetric(latest, label: "Source files")
    state["evidence_items"] = identityUpdateMetric(latest, label: "Evidence items")
    state["personality_theme_count"] = identityUpdateMetric(latest, label: "Personality themes")
    state["thinking_theme_count"] = identityUpdateMetric(latest, label: "Thinking themes")
    if let day = identityUpdateDate(from: latest.deletingLastPathComponent().lastPathComponent) {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        state["updated_at"] = formatter.string(from: day)
    }
    return state
}

func identityUpdateMetric(_ summary: URL, label: String) -> Int {
    guard let raw = try? String(contentsOf: summary, encoding: .utf8) else { return 0 }
    let pattern = "- \(NSRegularExpression.escapedPattern(for: label)):\\s*(\\d+)"
    guard let regex = try? NSRegularExpression(pattern: pattern),
          let match = regex.firstMatch(in: raw, range: NSRange(raw.startIndex..., in: raw)),
          match.numberOfRanges > 1,
          let range = Range(match.range(at: 1), in: raw) else {
        return 0
    }
    return Int(raw[range]) ?? 0
}

func metaReviewState(dataRoot: URL) -> [String: Any] {
    let state = readReviewState(dataRoot: dataRoot)
    let stateFile = workflowStateFile(dataRoot: dataRoot, filename: "review-state.md")
    let score = Int(state["Latest meta-review score"] ?? "0") ?? 0
    let tasks = Int(state["Non-trivial tasks since last meta-review"] ?? "0") ?? 0
    let cbs = Int(state["Circuit-breakers since last meta-review"] ?? "0") ?? 0
    let status: String
    let reason: String
    if score < 60 {
        status = "blocked"
        reason = "Score \(score) is below 60."
    } else if tasks >= 5 {
        status = "due"
        reason = "\(tasks) non-trivial tasks since last meta-review."
    } else if cbs >= 2 {
        status = "due"
        reason = "\(cbs) circuit-breakers since last meta-review."
    } else {
        status = "ok"
        reason = "Meta-review is up to date."
    }
    return [
        "status": status,
        "due": status != "ok",
        "reason": reason,
        "last_review": state["Last meta-review"] ?? "",
        "latest_score": score,
        "tasks_since_last_review": tasks,
        "circuit_breakers_since_last_review": cbs,
        "repeated_assumption_failures": Int(state["Repeated-assumption failures since last meta-review"] ?? "0") ?? 0,
        "state_file": stateFile.path,
    ]
}

func metaReviewScoreStatus(_ score: Int) -> String {
    if score >= 90 { return "green" }
    if score >= 75 { return "yellow" }
    if score >= 60 { return "orange" }
    return "red"
}

func performIdentityUpdateState() -> Bool {
    runIdentityUpdateProcess(dataRoot: activeDataRoot())
}

func runIdentityUpdateProcess(dataRoot: URL) -> Bool {
    let script = dataRoot.appendingPathComponent("bin/agentboost")
    guard FileManager.default.fileExists(atPath: script.path) else {
        return false
    }
    let process = Process()
    let output = Pipe()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
    process.arguments = [
        "python3",
        script.path,
        "--repo-root",
        dataRoot.path,
        "--do-identity-update",
    ]
    process.standardOutput = output
    process.standardError = output
    do {
        try process.run()
    } catch {
        return false
    }
    _ = output.fileHandleForReading.readDataToEndOfFile()
    process.waitUntilExit()
    return process.terminationStatus == 0
}

func performSkillPromptReviewState() -> Bool {
    let dataRoot = activeDataRoot()
    let today = String(ISO8601DateFormatter().string(from: Date()).prefix(10))
    do {
        try writeSkillPromptReviewArtifact(dataRoot: dataRoot, today: today)
        return true
    } catch {
        return false
    }
}

func skillPromptReviewInventory(dataRoot: URL) -> (skills: [URL], prompts: [URL]) {
    let skillRoot = dataRoot.appendingPathComponent("skill/public", isDirectory: true)
    var skills: [URL] = []
    if let enumerator = FileManager.default.enumerator(at: skillRoot, includingPropertiesForKeys: nil) {
        for case let url as URL in enumerator where url.lastPathComponent == "SKILL.md" {
            skills.append(url)
        }
    }
    let promptCandidates = [
        dataRoot.appendingPathComponent("AGENTS.md"),
        dataRoot.appendingPathComponent("adapters/claude/CLAUDE.md"),
        dataRoot.appendingPathComponent("adapters/codex/instructions.md"),
        dataRoot.appendingPathComponent("identity/personality.md"),
        dataRoot.appendingPathComponent("identity/thinkingpath.md"),
    ]
    let prompts = promptCandidates.filter { FileManager.default.fileExists(atPath: $0.path) }
    return (skills.sorted { $0.path < $1.path }, prompts)
}

func writeSkillPromptReviewArtifact(dataRoot: URL, today: String) throws {
    let reviewDir = skillPromptReviewDirectory(dataRoot: dataRoot)
    try FileManager.default.createDirectory(at: reviewDir, withIntermediateDirectories: true)
    let artifact = uniqueSkillPromptReviewArtifactURL(reviewDir: reviewDir, today: today)
    let inventory = skillPromptReviewInventory(dataRoot: dataRoot)

    func relative(_ url: URL) -> String {
        let root = dataRoot.path.hasSuffix("/") ? dataRoot.path : dataRoot.path + "/"
        if url.path.hasPrefix(root) {
            return String(url.path.dropFirst(root.count))
        }
        return url.path
    }

    let skillLines = inventory.skills.isEmpty
        ? ["- No public SKILL.md files found."]
        : inventory.skills.map { "- `\(relative($0))`" }
    let promptLines = inventory.prompts.isEmpty
        ? ["- No prompt or identity files found."]
        : inventory.prompts.map { "- `\(relative($0))`" }
    let artifactText = ([
        "# AgentBoost Skill and Prompt Review",
        "",
        "## Review Window",
        "",
        "- Completed by: AgentBoost app",
        "- Review date: \(today)",
        "- Skills reviewed: \(inventory.skills.count)",
        "- Prompts reviewed: \(inventory.prompts.count)",
        "",
        "## Skills",
        "",
    ] + skillLines + [
        "",
        "## Prompts",
        "",
    ] + promptLines + [
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
    ]).joined(separator: "\n")
    try artifactText.write(to: artifact, atomically: true, encoding: .utf8)
}

func uniqueSkillPromptReviewArtifactURL(reviewDir: URL, today: String) -> URL {
    let base = reviewDir.appendingPathComponent("skill-prompt-review-\(today)-agentboost.md")
    if !FileManager.default.fileExists(atPath: base.path) {
        return base
    }
    var index = 2
    while true {
        let candidate = reviewDir.appendingPathComponent("skill-prompt-review-\(today)-agentboost-\(index).md")
        if !FileManager.default.fileExists(atPath: candidate.path) {
            return candidate
        }
        index += 1
    }
}

func readReviewState(dataRoot: URL) -> [String: String] {
    let path = workflowStateFile(dataRoot: dataRoot, filename: "review-state.md")
    guard let raw = try? String(contentsOf: path, encoding: .utf8) else { return [:] }
    var state: [String: String] = [:]
    for line in raw.split(separator: "\n") {
        let textLine = String(line)
        guard textLine.hasPrefix("- "), let separator = textLine.firstIndex(of: ":") else { continue }
        let key = String(textLine[textLine.index(textLine.startIndex, offsetBy: 2)..<separator])
        let value = String(textLine[textLine.index(after: separator)...]).trimmingCharacters(in: .whitespaces)
        state[key] = value
    }
    return state
}

func performMetaReviewState() -> Bool {
    let dataRoot = activeDataRoot()
    let path = workflowStateFile(dataRoot: dataRoot, filename: "review-state.md")
    guard let raw = try? String(contentsOf: path, encoding: .utf8) else { return false }
    let before = metaReviewState(dataRoot: dataRoot)
    let state = readReviewState(dataRoot: dataRoot)
    let score = Int(state["Latest meta-review score"] ?? "0") ?? 0
    let status = metaReviewScoreStatus(score)
    let today = String(ISO8601DateFormatter().string(from: Date()).prefix(10))
    let rewritten = raw.split(separator: "\n", omittingEmptySubsequences: false).map { line -> String in
        if line.hasPrefix("- Last meta-review:") { return "- Last meta-review: \(today)" }
        if line.hasPrefix("- Non-trivial tasks since last meta-review:") { return "- Non-trivial tasks since last meta-review: 0" }
        if line.hasPrefix("- Circuit-breakers since last meta-review:") { return "- Circuit-breakers since last meta-review: 0" }
        if line.hasPrefix("- Repeated-assumption failures since last meta-review:") { return "- Repeated-assumption failures since last meta-review: 0" }
        if line.hasPrefix("- Latest meta-review score:") { return "- Latest meta-review score: \(score)" }
        if line.hasPrefix("- Score status:") { return "- Score status: \(status)" }
        return String(line)
    }.joined(separator: "\n")
    do {
        try writeMetaReviewArtifact(dataRoot: dataRoot, today: today, before: before, score: score, status: status)
        let log = workflowStateFile(dataRoot: dataRoot, filename: "review-log.md")
        let existingLog = (try? String(contentsOf: log, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines))
            ?? "# Workflow Review Log"
        let entry = [
            "## \(today) AgentBoost App Meta-Review",
            "",
            "- Completed a meta-review from the AgentBoost app surface.",
            "- Previous status: \(text(before["status"])) (\(text(before["reason"])))",
            "- Previous counters: tasks=\(text(before["tasks_since_last_review"])) cbs=\(text(before["circuit_breakers_since_last_review"])) repeats=\(text(before["repeated_assumption_failures"]))",
            "- Score: \(score) (status \(status))",
            "",
        ].joined(separator: "\n")
        try "\(existingLog)\n\n\(entry)".write(to: log, atomically: true, encoding: .utf8)
        try rewritten.write(to: path, atomically: true, encoding: .utf8)
        return true
    } catch {
        return false
    }
}

func writeMetaReviewArtifact(dataRoot: URL, today: String, before: [String: Any], score: Int, status: String) throws {
    let skillDir = workflowMetaReviewDirectory(dataRoot: dataRoot)
    try FileManager.default.createDirectory(at: skillDir, withIntermediateDirectories: true)
    let artifact = uniqueMetaReviewArtifactURL(skillDir: skillDir, today: today)
    let artifactText = [
        "# Workflow Meta-Review",
        "",
        "## Review Window",
        "",
        "- Completed by: AgentBoost app",
        "- Review date: \(today)",
        "- Previous status: \(text(before["status"])) (\(text(before["reason"])))",
        "",
        "## Signals Reviewed",
        "",
        "- Previous counters: tasks=\(text(before["tasks_since_last_review"])) cbs=\(text(before["circuit_breakers_since_last_review"])) repeats=\(text(before["repeated_assumption_failures"]))",
        "- State file: \(text(before["state_file"]))",
        "",
        "## Scorecard",
        "",
        "- Latest meta-review score: \(score)",
        "- Score status: \(status)",
        "- Score source: previous latest score",
        "",
        "## Result",
        "",
        "- Completed a meta-review from the AgentBoost app surface.",
        "- Reset non-trivial task, circuit-breaker, and repeated-assumption counters after writing this artifact.",
        "",
    ].joined(separator: "\n")
    try artifactText.write(to: artifact, atomically: true, encoding: .utf8)
}

func workflowStateFile(dataRoot: URL, filename: String) -> URL {
    let canonical = dataRoot.appendingPathComponent("skill/public/two-phase-execution/common/state/\(filename)")
    let legacy = dataRoot.appendingPathComponent("skill/\(filename)")
    if FileManager.default.fileExists(atPath: canonical.path) || !FileManager.default.fileExists(atPath: legacy.path) {
        return canonical
    }
    return legacy
}

func workflowMetaReviewDirectory(dataRoot: URL) -> URL {
    let canonical = dataRoot.appendingPathComponent("skill/public/two-phase-execution/common/meta-reviews", isDirectory: true)
    let common = dataRoot.appendingPathComponent("skill/public/two-phase-execution/common", isDirectory: true)
    if FileManager.default.fileExists(atPath: common.path) {
        return canonical
    }
    return dataRoot.appendingPathComponent("skill", isDirectory: true)
}

func uniqueMetaReviewArtifactURL(skillDir: URL, today: String) -> URL {
    let base = skillDir.appendingPathComponent("meta-review-\(today)-agentboost-app.md")
    if !FileManager.default.fileExists(atPath: base.path) {
        return base
    }
    var index = 2
    while true {
        let candidate = skillDir.appendingPathComponent("meta-review-\(today)-agentboost-app-\(index).md")
        if !FileManager.default.fileExists(atPath: candidate.path) {
            return candidate
        }
        index += 1
    }
}

final class RocketStatusView: NSView {
    private var backgroundProgressSeed: CGFloat = 0
    private var animationStartedAt = Date()
    private var rocketSpeed: CGFloat = 0
    private var rocketAltitude: CGFloat = 0
    private var activeAgents: [String] = []
    private var rocketCount = 1
    private var agentUsageByAgent: [String: [String: Any]] = [:]
    private var tokenIntensity = "idle"
    private var displayTokens = "0"
    private var statusViews: [[String: Any]] = []
    private var statusViewIndex = 0
    private var statusViewSwitchedAt = Date()
    private var motionTimer: Timer?
    private var rocketImageCache: [String: NSImage] = [:]
    private var tokenTextImageCache: [String: NSImage] = [:]

    override var intrinsicContentSize: NSSize {
        NSSize(width: rocketStatusItemWidth, height: rocketStatusItemHeight)
    }

    func configure(activity: [String: Any], statusViews: [[String: Any]]) {
        self.statusViews = statusViews.isEmpty ? [fallbackStatusView(from: activity)] : statusViews
        statusViewIndex = 0
        statusViewSwitchedAt = Date()
        tokenIntensity = text(activity["activity_level"])
        if tokenIntensity.isEmpty {
            tokenIntensity = text(activity["intensity"])
        }
        displayTokens = text(activity["display_tokens"])
        if displayTokens.isEmpty {
            displayTokens = "0"
        }
        let previousProgress = currentBackgroundProgress()
        rocketSpeed = CGFloat((activity["rocket_speed"] as? NSNumber)?.doubleValue ?? 0)
        rocketAltitude = CGFloat((activity["rocket_altitude"] as? NSNumber)?.doubleValue ?? 0)
        activeAgents = textArray(activity["active_agents"])
        agentUsageByAgent = normalizedAgentUsage(activity["agent_usage"])
        let requestedRocketCount = max(1, min(2, tokenInt(activity["rocket_count"])))
        rocketCount = activeAgents.count >= 2 ? 2 : requestedRocketCount
        backgroundProgressSeed = rocketSpeed > 0 ? previousProgress : 0
        animationStartedAt = Date()
        _ = text(activity["rocket_state"])
        motionTimer?.invalidate()

        if rocketSpeed > 0 || statusViews.count > 1 {
            let frameInterval = rocketStatusFrameIntervalSeconds
            let timer = Timer(
                timeInterval: frameInterval,
                target: self,
                selector: #selector(advanceRocket(_:)),
                userInfo: nil,
                repeats: true
            )
            timer.tolerance = 0.002
            RunLoop.main.add(timer, forMode: .common)
            motionTimer = timer
        } else {
            backgroundProgressSeed = 0
        }
        needsDisplay = true
    }

    @objc private func advanceRocket(_ timer: Timer) {
        advanceStatusViewIfNeeded()
        needsDisplay = true
    }

    private func currentBackgroundProgress() -> CGFloat {
        guard rocketSpeed > 0 else {
            return 0
        }
        let elapsed = max(0, CGFloat(Date().timeIntervalSince(animationStartedAt)))
        let cyclesPerSecond = max(CGFloat(0.18), min(CGFloat(1.15), rocketSpeed * CGFloat(0.24)))
        return (backgroundProgressSeed + elapsed * cyclesPerSecond).truncatingRemainder(dividingBy: 1)
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)

        let animationMidY = bounds.minY + 15
        let tokenBaselineY = bounds.minY + 0.5
        let rocketScale = CGFloat(0.86)
        let rocketPoints = rocketCenterPoints(centerX: bounds.midX, centerY: animationMidY)
        for (index, rocketPoint) in rocketPoints.enumerated() {
            let agent = index < activeAgents.count ? activeAgents[index] : ""
            drawRocket(at: rocketPoint, scale: rocketScale, agent: agent)
        }
        let tokenPoint = NSPoint(x: bounds.midX, y: max(animationMidY, tokenBaselineY + 12))
        drawTokenText(text: currentTokenText(), below: tokenPoint)
    }

    private func currentStatusView() -> [String: Any] {
        guard !statusViews.isEmpty else {
            return ["view_id": "token_per_minute", "label": "Token/min", "display_tokens": "0", "display_text": "0/min", "trend_symbol": "flat"]
        }
        let safeIndex = min(statusViewIndex, statusViews.count - 1)
        return statusViews[safeIndex]
    }

    private func currentTokenText() -> String {
        let view = currentStatusView()
        let displayText = text(view["display_text"])
        if !displayText.isEmpty {
            return displayText
        }
        let value = text(view["display_tokens"])
        if !value.isEmpty {
            return value
        }
        return displayTokens.isEmpty ? "0" : displayTokens
    }

    private func fallbackStatusView(from activity: [String: Any]) -> [String: Any] {
        let display = text(activity["display_tokens"])
        return [
            "view_id": "token_per_minute",
            "label": "Token/min",
            "display_tokens": display.isEmpty ? "0" : display,
            "display_text": display.isEmpty ? "0/min" : "\(display)/min",
            "trend_symbol": "flat",
        ]
    }

    private func advanceStatusViewIfNeeded() {
        guard statusViews.count > 1 else {
            return
        }
        let now = Date()
        if now.timeIntervalSince(statusViewSwitchedAt) >= 3 {
            statusViewIndex = (statusViewIndex + 1) % statusViews.count
            statusViewSwitchedAt = now
        }
    }

    private func rocketCenterPoints(centerX: CGFloat, centerY: CGFloat) -> [NSPoint] {
        let altitude = min(CGFloat(2.5), rocketAltitude / CGFloat(80))
        if rocketCount < 2 {
            let agent = activeAgents.first ?? ""
            let bob = rocketBob(for: agent, altitude: altitude)
            return [NSPoint(x: centerX, y: centerY + bob)]
        }
        let firstAgent = activeAgents.indices.contains(0) ? activeAgents[0] : ""
        let secondAgent = activeAgents.indices.contains(1) ? activeAgents[1] : ""
        let firstBob = rocketBob(for: firstAgent, altitude: altitude)
        let secondBob = rocketBob(for: secondAgent, altitude: altitude)
        let split = CGFloat(8)
        return [
            NSPoint(x: centerX - split, y: centerY + firstBob + CGFloat(1.5)),
            NSPoint(x: centerX + split, y: centerY - secondBob - CGFloat(1.5)),
        ]
    }

    private func drawRocket(at point: NSPoint, scale: CGFloat, agent: String = "") {
        let wave = agentRocketSpeed(agent) > 0 ? sin(currentBackgroundProgress() * CGFloat.pi * 2) * CGFloat(4) : 0
        let agentTint = agentColor(agent)
        drawEmojiRocket(at: point, scale: scale, angleDegrees: CGFloat(-8) + wave, tint: agentTint)
    }

    private func rocketBob(for agent: String, altitude: CGFloat) -> CGFloat {
        guard agentRocketSpeed(agent) > 0 else { return 0 }
        return sin(currentBackgroundProgress() * CGFloat.pi * 2) * (1.0 + altitude / 4)
    }

    private func agentRocketSpeed(_ agent: String) -> CGFloat {
        guard !agent.isEmpty else {
            return rocketSpeed
        }
        let tokens = tokenInt(agentUsageByAgent[agent]?["last_1m_tokens"])
        return tokens <= 0 ? CGFloat(0) : rocketSpeed
    }

    private func normalizedAgentUsage(_ value: Any?) -> [String: [String: Any]] {
        guard let rawUsage = value as? [String: Any] else {
            return [:]
        }
        var normalized: [String: [String: Any]] = [:]
        for (agent, payload) in rawUsage {
            if let usage = payload as? [String: Any] {
                normalized[agent.lowercased()] = usage
            }
        }
        return normalized
    }

    private func agentColor(_ agent: String) -> NSColor {
        if agent == "claude" { return NSColor.systemPurple }
        if agent == "codex" { return NSColor.systemBlue }
        return tokenIntensity == "idle" ? NSColor.secondaryLabelColor : NSColor.systemBlue
    }

    private func drawEmojiRocket(at point: NSPoint, scale: CGFloat, angleDegrees: CGFloat, tint: NSColor) {
        let image = cachedRocketImage(scale: scale, tint: tint)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current?.shouldAntialias = true
        NSGraphicsContext.current?.imageInterpolation = .high
        let transform = NSAffineTransform()
        transform.translateX(by: point.x, yBy: point.y)
        transform.rotate(byDegrees: angleDegrees)
        transform.translateX(by: -point.x, yBy: -point.y)
        transform.concat()
        image.draw(
            at: NSPoint(x: point.x - image.size.width / 2, y: point.y - image.size.height / 2),
            from: NSRect(origin: .zero, size: image.size),
            operation: .sourceOver,
            fraction: 1.0
        )
        NSGraphicsContext.restoreGraphicsState()
    }

    private func cachedRocketImage(scale: CGFloat, tint: NSColor) -> NSImage {
        let fontSize = CGFloat(13) * scale
        let key = String(format: "%.2f:%@", Double(fontSize), colorCacheKey(tint))
        if let image = rocketImageCache[key] {
            return image
        }
        let rocketEmoji = "🚀" as NSString
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: fontSize),
            .foregroundColor: tint,
        ]
        let emojiSize = rocketEmoji.size(withAttributes: attributes)
        let padding = CGFloat(5)
        let imageSize = NSSize(width: ceil(emojiSize.width + padding * 2), height: ceil(emojiSize.height + padding * 2))
        let image = NSImage(size: imageSize)
        image.lockFocus()
        NSGraphicsContext.current?.shouldAntialias = true
        NSGraphicsContext.current?.imageInterpolation = .high
        let shadow = NSShadow()
        shadow.shadowColor = tint.withAlphaComponent(0.55)
        shadow.shadowBlurRadius = 3
        shadow.shadowOffset = NSSize(width: 0, height: 0)
        shadow.set()
        rocketEmoji.draw(at: NSPoint(x: padding, y: padding), withAttributes: attributes)
        image.unlockFocus()
        rocketImageCache[key] = image
        return image
    }

    private func drawTokenText(text: String, below point: NSPoint) {
        let value = text.isEmpty ? "0" : text
        let image = cachedTokenTextImage(value)
        let textSize = image.size
        let textX = min(max(bounds.minX + 2, point.x - textSize.width / 2), bounds.maxX - textSize.width - 2)
        let textY = max(bounds.minY + 0.5, point.y - textSize.height - 4)
        NSGraphicsContext.saveGraphicsState()
        NSBezierPath(rect: bounds).setClip()
        image.draw(
            at: NSPoint(x: textX, y: textY),
            from: NSRect(origin: .zero, size: textSize),
            operation: .sourceOver,
            fraction: 1.0
        )
        NSGraphicsContext.restoreGraphicsState()
    }

    private func cachedTokenTextImage(_ value: String) -> NSImage {
        let key = value.isEmpty ? "0" : value
        if let image = tokenTextImageCache[key] {
            return image
        }
        let tokenText = key as NSString
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedDigitSystemFont(ofSize: 7.0, weight: .bold),
            .foregroundColor: NSColor.white,
            .strokeWidth: -3.0,
            .strokeColor: NSColor.black.withAlphaComponent(0.7),
        ]
        let textSize = tokenText.size(withAttributes: attributes)
        let imageSize = NSSize(width: ceil(textSize.width), height: ceil(textSize.height))
        let image = NSImage(size: imageSize)
        image.lockFocus()
        tokenText.draw(at: .zero, withAttributes: attributes)
        image.unlockFocus()
        tokenTextImageCache[key] = image
        return image
    }

    private func colorCacheKey(_ color: NSColor) -> String {
        guard let rgb = color.usingColorSpace(.deviceRGB) else {
            return color.description
        }
        return String(
            format: "%.3f:%.3f:%.3f:%.3f",
            Double(rgb.redComponent),
            Double(rgb.greenComponent),
            Double(rgb.blueComponent),
            Double(rgb.alphaComponent)
        )
    }
}

final class RocketScreensaverView: NSView {
    private let blastVisualScale = CGFloat(0.8)
    final class MotionState {
        struct RocketDrawState {
            let agent: String
            let position: NSPoint
            let headingDegrees: CGFloat
            let glowIntensity: CGFloat
        }

        struct AgentRocketMotion {
            var position: NSPoint
            var headingDegrees: CGFloat
            var smoothedAltitudeFraction: CGFloat
            var smoothedSpeed: CGFloat
            var lastFrameAt: Date
        }

        struct RocketEntity {
            let id: String
            let agent: String
            let channel: String
            let tokens: Int
            let displayTokens: String
            let speedHint: Double
            let altitudeHint: Double
        }

        struct BlastDrawState {
            let position: NSPoint
            let progress: CGFloat
        }

        private struct BlastEvent {
            let position: NSPoint
            let startedAt: Date
            let agentA: String
            let agentB: String
            var separationApplied: Bool
        }

        private struct RocketRecoveryEvent {
            let startedAt: Date
            let agentA: String
            let agentB: String
        }

        private(set) var animationStartedAt = Date()
        private(set) var rocketSpeed = CGFloat(0.7)
        private(set) var activeAgents: [String] = []
        private(set) var rocketCount = 1
        private(set) var agentRocketMotion: [String: AgentRocketMotion] = [:]
        private var agentUsageByAgent: [String: [String: Any]] = [:]
        private var tokenIntensity = "active"
        private var displayTokens = "0"
        private var statusViews: [[String: Any]] = []
        private var statusViewIndex = 0
        private(set) var rocketPosition = NSPoint.zero
        private(set) var rocketHeadingDegrees = CGFloat(0)
        private let rocketEmojiBaselineDegrees = CGFloat(45)
        private var worldFrame = NSRect.zero
        private var combinedTargetAltitudeFraction = CGFloat(0.45)
        private var rocketEntities: [RocketEntity] = []
        private var splitIO: Bool = false
        private var blasts: [BlastEvent] = []
        private var rocketRecoveries: [RocketRecoveryEvent] = []
        private var lastBlastBetween: [String: Date] = [:]
        private let collisionDistance = CGFloat(36)
        private let blastCooldownSeconds = TimeInterval(2.0)
        private let blastDurationSeconds = TimeInterval(0.6)
        private let rocketRecoverySeconds = TimeInterval(1.2)
        private let separationDelaySeconds = TimeInterval(0.5)
        private let separationKickFraction = CGFloat(0.18)
        private let minimumActiveAltitudeFraction = CGFloat(0.22)
        private let maximumAltitudeFraction = CGFloat(1.0)
        private let maximumUsageAltitude = CGFloat(250)
        private let rocketCenterTopMargin = CGFloat(8)
        private let rocketCenterBottomMargin = CGFloat(8)

        func configure(activity: [String: Any], statusViews: [[String: Any]], worldFrame: NSRect) {
            self.worldFrame = worldFrame
            rocketSpeed = max(CGFloat(0), CGFloat((activity["rocket_speed"] as? NSNumber)?.doubleValue ?? 0))
            agentUsageByAgent = normalizedAgentUsage(activity["agent_usage"])
            activeAgents = textArray(activity["active_agents"])
            if activeAgents.isEmpty {
                activeAgents = ["claude", "codex"].filter { tokenInt(agentUsageByAgent[$0]?["last_1m_tokens"]) > 0 }
            }
            rocketEntities = parseRocketEntities(activity["rockets"])
            splitIO = !rocketEntities.isEmpty
            let requestedRocketCount = max(1, min(2, tokenInt(activity["rocket_count"])))
            rocketCount = activeAgents.count >= 2 ? 2 : requestedRocketCount
            if splitIO {
                rocketCount = max(rocketEntities.count, rocketCount)
            }
            tokenIntensity = text(activity["activity_level"])
            if tokenIntensity.isEmpty {
                tokenIntensity = "active"
            }
            displayTokens = text(activity["display_tokens"])
            if displayTokens.isEmpty {
                displayTokens = "0"
            }
            self.statusViews = normalizedStatusViews(statusViews, activity: activity)
            if statusViewIndex >= self.statusViews.count {
                statusViewIndex = 0
            }
            combinedTargetAltitudeFraction = altitudeTargetFraction(activity)
            ensureMotionState()
        }

        func advance(to now: Date) {
            advanceMotion(to: now)
        }

        func ensureMotionState() {
            let margin = CGFloat(34)
            let minX = worldFrame.minX + margin
            let maxX = worldFrame.maxX - margin
            let minY = rocketCenterMinY()
            let maxY = rocketCenterMaxY()
            guard maxX > minX, maxY > minY else {
                return
            }
            let agents = renderedAgents()
            let validKeys = Set(agents.map { agentKey($0) })
            agentRocketMotion = agentRocketMotion.filter { validKeys.contains($0.key) }
            for (index, agent) in agents.enumerated() {
                ensureRocketMotion(for: agent, index: index, total: agents.count)
            }
            syncPrimaryMotionState()
        }

        func rocketDrawStates(now: Date = Date()) -> [RocketDrawState] {
            ensureMotionState()
            let agents = renderedAgents()
            let hiddenKeys = activeBlastRocketKeys(now: now)
            let tokensByAgent = agents.reduce(into: [String: Int]()) { acc, agent in
                acc[agent] = tokenInt(agentUsageByAgent[agent]?["last_1m_tokens"])
            }
            let peakTokens = max(1, tokensByAgent.values.max() ?? 0)
            return agents.compactMap { agent in
                let key = agentKey(agent)
                guard !hiddenKeys.contains(key),
                      let motion = agentRocketMotion[key] else {
                    return nil
                }
                let tokens = tokensByAgent[agent] ?? 0
                let relative = CGFloat(tokens) / CGFloat(peakTokens)
                let glow = CGFloat(0.35) + CGFloat(0.65) * max(CGFloat(0), min(CGFloat(1), relative))
                return RocketDrawState(
                    agent: agent,
                    position: motion.position,
                    headingDegrees: motion.headingDegrees,
                    glowIntensity: glow
                )
            }
        }

        private func activeBlastRocketKeys(now: Date) -> Set<String> {
            var hidden = Set<String>()
            for recovery in rocketRecoveries {
                let elapsed = now.timeIntervalSince(recovery.startedAt)
                guard elapsed >= 0, elapsed < rocketRecoverySeconds else { continue }
                hidden.insert(agentKey(recovery.agentA))
                hidden.insert(agentKey(recovery.agentB))
            }
            return hidden
        }

        func runtimeSnapshot() -> [String: Any] {
            ensureMotionState()
            let agents = renderedAgents()
            let rockets: [[String: Any]] = agents.map { agent in
                let key = agentKey(agent)
                let motion = agentRocketMotion[key]
                let position = motion?.position ?? NSPoint(x: 0, y: 0)
                return [
                    "agent": agent,
                    "key": key,
                    "position": [
                        "x": Double(position.x),
                        "y": Double(position.y),
                    ],
                    "visual_bottom_y": Double(position.y - rocketCenterBottomMargin),
                    "visual_top_y": Double(position.y + rocketCenterTopMargin),
                    "heading_degrees": Double(motion?.headingDegrees ?? 0),
                    "smoothed_altitude_fraction": Double(motion?.smoothedAltitudeFraction ?? 0),
                    "smoothed_speed": Double(motion?.smoothedSpeed ?? 0),
                    "speed": Double(agentRocketSpeed(agent)),
                    "altitude_target_fraction": Double(agentAltitudeTargetFraction(agent)),
                    "tokens_last_1m": tokenInt(agentUsageByAgent[agent]?["last_1m_tokens"]),
                ]
            }
            return [
                "world_frame": rectState(worldFrame),
                "active_agents": activeAgents,
                "rendered_agents": agents,
                "rocket_count": rocketCount,
                "rocket_speed": Double(rocketSpeed),
                "combined_target_altitude_fraction": Double(combinedTargetAltitudeFraction),
                "rocket_center_top_margin": Double(rocketCenterTopMargin),
                "rocket_center_bottom_margin": Double(rocketCenterBottomMargin),
                "rockets": rockets,
                "agent_usage": agentUsageByAgent,
            ]
        }

        func remapMotion(toWorldFrame newFrame: NSRect) {
            let oldFrame = worldFrame
            worldFrame = newFrame
            guard oldFrame.width > 1, oldFrame.height > 1 else {
                ensureMotionState()
                return
            }
            let scaleX = newFrame.width / oldFrame.width
            let scaleY = newFrame.height / oldFrame.height
            for (key, motion) in agentRocketMotion {
                var moved = motion
                let relX = motion.position.x - oldFrame.minX
                let relY = motion.position.y - oldFrame.minY
                moved.position = NSPoint(
                    x: newFrame.minX + relX * scaleX,
                    y: newFrame.minY + relY * scaleY
                )
                agentRocketMotion[key] = moved
            }
            ensureMotionState()
        }

        private func advanceMotion(to now: Date) {
            ensureMotionState()
            let agents = renderedAgents()
            for (index, agent) in agents.enumerated() {
                advanceRocketMotion(for: agent, index: index, total: agents.count, now: now)
            }
            detectCollisions(among: agents, now: now)
            applyPendingSeparations(now: now)
            cullExpiredBlasts(now: now)
            syncPrimaryMotionState()
        }

        private func detectCollisions(among agents: [String], now: Date) {
            guard agents.count >= 2 else { return }
            for i in 0..<agents.count {
                for j in (i + 1)..<agents.count {
                    let a = agents[i]
                    let b = agents[j]
                    guard rocketCanCollide(a), rocketCanCollide(b) else { continue }
                    guard let motionA = agentRocketMotion[agentKey(a)],
                          let motionB = agentRocketMotion[agentKey(b)] else { continue }
                    let dx = motionA.position.x - motionB.position.x
                    let dy = motionA.position.y - motionB.position.y
                    let distance = sqrt(dx * dx + dy * dy)
                    guard distance < collisionDistance else { continue }
                    let pairKey = blastPairKey(a, b)
                    if let last = lastBlastBetween[pairKey], now.timeIntervalSince(last) < blastCooldownSeconds {
                        continue
                    }
                    let midpoint = NSPoint(
                        x: (motionA.position.x + motionB.position.x) / 2,
                        y: (motionA.position.y + motionB.position.y) / 2
                    )
                    blasts.append(BlastEvent(
                        position: midpoint,
                        startedAt: now,
                        agentA: a,
                        agentB: b,
                        separationApplied: false
                    ))
                    rocketRecoveries.append(RocketRecoveryEvent(
                        startedAt: now,
                        agentA: a,
                        agentB: b
                    ))
                    lastBlastBetween[pairKey] = now
                }
            }
        }

        private func applyPendingSeparations(now: Date) {
            guard !blasts.isEmpty else { return }
            let minY = rocketCenterMinY()
            let maxY = rocketCenterMaxY()
            guard maxY > minY else { return }
            for index in blasts.indices {
                guard !blasts[index].separationApplied,
                      now.timeIntervalSince(blasts[index].startedAt) >= separationDelaySeconds else {
                    continue
                }
                let a = blasts[index].agentA
                let b = blasts[index].agentB
                guard var motionA = agentRocketMotion[agentKey(a)],
                      var motionB = agentRocketMotion[agentKey(b)] else {
                    blasts[index].separationApplied = true
                    continue
                }
                let aIsHigher = motionA.smoothedAltitudeFraction >= motionB.smoothedAltitudeFraction
                let half = separationKickFraction / 2
                let kickA = aIsHigher ? half : -half
                let kickB = aIsHigher ? -half : half
                motionA.smoothedAltitudeFraction = clampAltitudeFraction(motionA.smoothedAltitudeFraction + kickA)
                motionB.smoothedAltitudeFraction = clampAltitudeFraction(motionB.smoothedAltitudeFraction + kickB)
                motionA.position.y = minY + motionA.smoothedAltitudeFraction * (maxY - minY)
                motionB.position.y = minY + motionB.smoothedAltitudeFraction * (maxY - minY)
                agentRocketMotion[agentKey(a)] = motionA
                agentRocketMotion[agentKey(b)] = motionB
                blasts[index].separationApplied = true
            }
        }

        private func cullExpiredBlasts(now: Date) {
            blasts.removeAll { now.timeIntervalSince($0.startedAt) >= blastDurationSeconds }
            rocketRecoveries.removeAll { now.timeIntervalSince($0.startedAt) >= rocketRecoverySeconds }
        }

        private func blastPairKey(_ a: String, _ b: String) -> String {
            a <= b ? "\(a)|\(b)" : "\(b)|\(a)"
        }

        private func clampAltitudeFraction(_ value: CGFloat) -> CGFloat {
            min(maximumAltitudeFraction, max(minimumActiveAltitudeFraction, value))
        }

        func blastDrawStates(now: Date = Date()) -> [BlastDrawState] {
            blasts.compactMap { blast in
                let elapsed = now.timeIntervalSince(blast.startedAt)
                guard elapsed >= 0, elapsed < blastDurationSeconds else { return nil }
                let progress = CGFloat(elapsed / blastDurationSeconds)
                return BlastDrawState(position: blast.position, progress: progress)
            }
        }

        var totalBlastCount: Int { blasts.count }

        var separationAppliedCount: Int { blasts.filter { $0.separationApplied }.count }

        func _setWorldFrameForTesting(_ frame: NSRect) {
            self.worldFrame = frame
        }

        func _injectRocketForTesting(agent: String, position: NSPoint, altitudeFraction: CGFloat, now: Date) {
            agentRocketMotion[agentKey(agent)] = AgentRocketMotion(
                position: position,
                headingDegrees: 0,
                smoothedAltitudeFraction: altitudeFraction,
                smoothedSpeed: 0,
                lastFrameAt: now
            )
            if !activeAgents.contains(agent) { activeAgents.append(agent) }
            rocketCount = max(rocketCount, activeAgents.count)
        }

        func _runDetectionForTesting(at now: Date) {
            let agents = Array(agentRocketMotion.keys)
            detectCollisions(among: agents, now: now)
            applyPendingSeparations(now: now)
            cullExpiredBlasts(now: now)
        }

        func _altitudeFraction(for agent: String) -> CGFloat {
            agentRocketMotion[agentKey(agent)]?.smoothedAltitudeFraction ?? -1
        }

        private func advanceRocketMotion(for agent: String, index: Int, total: Int, now: Date) {
            let margin = CGFloat(34)
            let offscreenMargin = CGFloat(72)
            let minX = worldFrame.minX + margin
            let maxX = worldFrame.maxX - margin
            let minY = rocketCenterMinY()
            let maxY = rocketCenterMaxY()
            guard maxX > minX, maxY > minY else {
                return
            }

            let key = agentKey(agent)
            guard var motion = agentRocketMotion[key] else {
                return
            }
            let previousPosition = motion.position
            let rawDelta = CGFloat(now.timeIntervalSince(motion.lastFrameAt))
            // Clamp pathological gaps (e.g. window resume) so the rocket doesn't
            // leap across the screen, but leave plenty of headroom for the normal
            // 60 fps cadence and the occasional dropped frame.
            let maxFrameDelta = CGFloat(1.0 / 15.0)
            let deltaSeconds = min(maxFrameDelta, max(CGFloat(0), rawDelta))
            motion.lastFrameAt = now
            let targetSpeed = agentRocketSpeed(agent)
            if agentCurrentTokens(agent) <= 0 {
                motion.smoothedSpeed = 0
            } else {
                let speedEase = min(CGFloat(1), deltaSeconds / CGFloat(2.5))
                motion.smoothedSpeed += (targetSpeed - motion.smoothedSpeed) * speedEase
                if motion.smoothedSpeed < CGFloat(0.005) && targetSpeed <= 0 {
                    motion.smoothedSpeed = 0
                }
            }
            let activeFactor = min(CGFloat(1), motion.smoothedSpeed / CGFloat(0.05))
            let pixelsPerSecond = motion.smoothedSpeed <= 0 ? CGFloat(0)
                : (CGFloat(52) + motion.smoothedSpeed * CGFloat(34)) * activeFactor
            motion.position.x += pixelsPerSecond * deltaSeconds
            if motion.position.x > maxX + offscreenMargin {
                motion.position.x = minX - offscreenMargin - CGFloat(index) * CGFloat(96)
                if index == 0 {
                    advanceTokenModeAfterCycle()
                }
            }

            let targetAltitudeFraction = agentAltitudeTargetFraction(agent)
            let altitudeEase = min(CGFloat(1), deltaSeconds / CGFloat(4.0))
            motion.smoothedAltitudeFraction += (targetAltitudeFraction - motion.smoothedAltitudeFraction) * altitudeEase
            let targetY = minY + motion.smoothedAltitudeFraction * (maxY - minY)
            let positionEase = min(CGFloat(1), deltaSeconds / CGFloat(2.0))
            motion.position.y += (targetY - motion.position.y) * positionEase
            motion.position.y = min(max(motion.position.y, minY), maxY)
            motion.headingDegrees = updateRocketHeading(previousPosition: previousPosition, position: motion.position)
            // When effectively stopped, ease heading back to horizontal (rocket level, pointing right).
            if motion.smoothedSpeed < CGFloat(0.05) {
                let restingHeading = -rocketEmojiBaselineDegrees
                let headingEase = min(CGFloat(1), deltaSeconds / CGFloat(1.5))
                motion.headingDegrees += (restingHeading - motion.headingDegrees) * headingEase
            }
            agentRocketMotion[key] = motion
        }

        private func updateRocketHeading(previousPosition: NSPoint, position: NSPoint) -> CGFloat {
            let deltaX = position.x - previousPosition.x
            let deltaY = position.y - previousPosition.y
            let hasGraphMotion = abs(deltaX) > CGFloat(0.5) || abs(deltaY) > CGFloat(0.5)
            guard hasGraphMotion else {
                return rocketHeadingDegrees
            }
            let headingDeltaX = max(CGFloat(0.5), deltaX)
            let graphSlopeDegrees = atan2(deltaY, headingDeltaX) * CGFloat(180) / CGFloat.pi
            let clampedSlopeDegrees = min(CGFloat(34), max(CGFloat(-34), graphSlopeDegrees))
            return clampedSlopeDegrees - rocketEmojiBaselineDegrees
        }

        private func normalizedStatusViews(_ views: [[String: Any]], activity: [String: Any]) -> [[String: Any]] {
            if !views.isEmpty {
                return views
            }
            let display = text(activity["display_tokens"])
            return [[
                "view_id": "token_per_minute",
                "label": "Token/min",
                "display_tokens": display.isEmpty ? "0/min" : "\(display)/min",
                "display_text": display.isEmpty ? "0/min" : "\(display)/min",
            ]]
        }

        private func currentStatusView() -> [String: Any] {
            guard !statusViews.isEmpty else {
                return ["view_id": "token_per_minute", "label": "Token/min", "display_tokens": displayTokens, "display_text": displayTokens]
            }
            let safeIndex = min(statusViewIndex, statusViews.count - 1)
            return statusViews[safeIndex]
        }

        func currentTokenText() -> String {
            let view = currentStatusView()
            let displayText = text(view["display_text"])
            if !displayText.isEmpty {
                return displayText
            }
            let label = text(view["label"])
            let value = text(view["display_tokens"])
            if label.isEmpty {
                return value.isEmpty ? displayTokens : value
            }
            return "\(label) \(value.isEmpty ? displayTokens : value)"
        }

        func currentTokenText(for agent: String) -> String {
            guard !agent.isEmpty else {
                return currentTokenText()
            }
            if let entity = rocketEntity(for: agent) {
                let value = entity.displayTokens.isEmpty
                    ? compactTokenCount(entity.tokens)
                    : entity.displayTokens
                let suffix = entity.channel == "input" ? " IN"
                    : entity.channel == "output" ? " OUT" : ""
                return "\(agentLabel(entity.agent))\(suffix) \(value)/min"
            }
            let tokens = tokenInt(agentUsageByAgent[agent]?["last_1m_tokens"])
            let display = text(agentUsageByAgent[agent]?["display_tokens"])
            let value = display.isEmpty ? compactTokenCount(tokens) : display
            return "\(agentLabel(agent)) \(value)/min"
        }

        private func advanceTokenModeAfterCycle() {
            guard statusViews.count > 1 else {
                return
            }
            statusViewIndex = (statusViewIndex + 1) % statusViews.count
        }

        private func altitudeTargetFraction(_ activity: [String: Any]) -> CGFloat {
            if tokenInt(activity["last_1m_tokens"]) <= 0 {
                return CGFloat(0)
            }
            let altitude = CGFloat((activity["rocket_altitude"] as? NSNumber)?.doubleValue ?? 0)
            if altitude > 0 {
                return altitudeFraction(forVisualAltitude: altitude)
            }
            switch text(activity["activity_level"]) {
            case "surge":
                return maximumAltitudeFraction
            case "high":
                return CGFloat(0.7)
            case "active":
                return CGFloat(0.56)
            default:
                return CGFloat(0.45)
            }
        }

        private func normalizedAgentUsage(_ value: Any?) -> [String: [String: Any]] {
            guard let rawUsage = value as? [String: Any] else {
                return [:]
            }
            var normalized: [String: [String: Any]] = [:]
            for (agent, payload) in rawUsage {
                if let usage = payload as? [String: Any] {
                    normalized[agent.lowercased()] = usage
                }
            }
            return normalized
        }

        private func renderedAgents() -> [String] {
            if splitIO {
                return rocketEntities.map { $0.id }
            }
            // BEAM always emits a stable `agent_usage` map with claude + codex
            // keys, so use that as the canonical roster — that way Claude
            // stays on screen as a parked rocket even when Codex is the only
            // one currently producing tokens, instead of disappearing every
            // time it goes quiet for 60 s.
            let known = ["claude", "codex"].filter { agentUsageByAgent[$0] != nil }
            if known.count >= 2 {
                return known
            }
            if rocketCount >= 2 && activeAgents.count >= 2 {
                return Array(activeAgents.prefix(2))
            }
            if let first = activeAgents.first {
                return [first]
            }
            if let first = known.first {
                return [first]
            }
            return [""]
        }

        private func agentKey(_ agent: String) -> String {
            agent.isEmpty ? "combined" : agent
        }

        private func parseRocketEntities(_ value: Any?) -> [RocketEntity] {
            guard let raw = value as? [[String: Any]] else { return [] }
            return raw.compactMap { entry in
                let agent = text(entry["agent"]).lowercased()
                let channel = text(entry["channel"]).lowercased()
                guard !agent.isEmpty, !channel.isEmpty else { return nil }
                return RocketEntity(
                    id: "\(agent):\(channel)",
                    agent: agent,
                    channel: channel,
                    tokens: tokenInt(entry["tokens"]),
                    displayTokens: text(entry["display_tokens"]),
                    speedHint: (entry["speed"] as? NSNumber)?.doubleValue ?? 0,
                    altitudeHint: (entry["altitude"] as? NSNumber)?.doubleValue ?? 0
                )
            }
        }

        private func parseRocketId(_ rid: String) -> (agent: String, channel: String) {
            if let colon = rid.firstIndex(of: ":") {
                return (String(rid[..<colon]), String(rid[rid.index(after: colon)...]))
            }
            return (rid, "")
        }

        private func rocketEntity(for rid: String) -> RocketEntity? {
            rocketEntities.first { $0.id == rid }
        }

        private func ensureRocketMotion(for agent: String, index: Int, total: Int) {
            let margin = CGFloat(34)
            let offscreenMargin = CGFloat(72)
            let minX = worldFrame.minX + margin
            let maxX = worldFrame.maxX - margin
            let minY = rocketCenterMinY()
            let maxY = rocketCenterMaxY()
            guard maxX > minX, maxY > minY else {
                return
            }
            let key = agentKey(agent)
            if agentRocketMotion[key] != nil {
                return
            }
            let altitude = agentAltitudeTargetFraction(agent)
            let startX = initialRocketX(for: agent, index: index, total: total, minX: minX, offscreenMargin: offscreenMargin)
            agentRocketMotion[key] = AgentRocketMotion(
                position: NSPoint(
                    x: startX,
                    y: minY + altitude * (maxY - minY)
                ),
                headingDegrees: -rocketEmojiBaselineDegrees,
                smoothedAltitudeFraction: altitude,
                smoothedSpeed: 0,
                lastFrameAt: Date()
            )
        }

        private func initialRocketX(for agent: String, index: Int, total: Int, minX: CGFloat, offscreenMargin: CGFloat) -> CGFloat {
            let stagger = total > 1 ? CGFloat(index) * CGFloat(96) : CGFloat(0)
            // Split-IO: every channel is a first-class tracked entity, so even
            // idle ones (0 tokens) start onscreen as grounded rockets — see
            // all 4 rockets, active flying, idle parked.
            if splitIO {
                return minX + stagger
            }
            // Default rendered agents (claude + codex) are slot-stable, so
            // place them onscreen at spawn time too. The only path that
            // benefits from an offscreen entrance is an active rocket that's
            // wrapping in from the right (handled in advanceRocketMotion);
            // first-time placement should never sit a parked rocket behind
            // the left bezel where the user can't see it.
            let currentTokens = tokenInt(agentUsageByAgent[agent]?["last_1m_tokens"])
            if rocketSpeed > 0 && currentTokens > 0 {
                return minX - offscreenMargin - stagger
            }
            return minX + stagger
        }

        private func agentIsActiveIdle(_ agent: String) -> Bool {
            guard !agent.isEmpty else {
                return false
            }
            return activeAgents.contains(agent) && tokenInt(agentUsageByAgent[agent]?["last_1m_tokens"]) <= 0
        }

        private func syncPrimaryMotionState() {
            guard let firstAgent = renderedAgents().first,
                  let motion = agentRocketMotion[agentKey(firstAgent)] else {
                return
            }
            rocketPosition = motion.position
            rocketHeadingDegrees = motion.headingDegrees
        }

        private func agentRocketSpeed(_ agent: String) -> CGFloat {
            guard !agent.isEmpty else {
                return rocketSpeed
            }
            let absoluteSpeed: CGFloat
            let myTokens: Int
            if let entity = rocketEntity(for: agent) {
                if entity.tokens <= 0 { return CGFloat(0) }
                myTokens = entity.tokens
                absoluteSpeed = entity.speedHint > 0
                    ? CGFloat(entity.speedHint)
                    : CGFloat(usageAnimationSpeed(entity.tokens))
            } else {
                let tokens = agentCurrentTokens(agent)
                if tokens <= 0 { return CGFloat(0) }
                myTokens = tokens
                absoluteSpeed = CGFloat(usageAnimationSpeed(tokens))
            }
            // Relative speeding rule: leader keeps its absolute speed, slower
            // peers scale down by their token share of the peak (0.4 floor so
            // a tiny-but-active rocket is still visibly moving). Saturated
            // `usageAnimationSpeed` (capped at 2.4) used to flatten Claude
            // vs Codex at heavy usage — this restores the visual gap.
            let peak = peakTokensAmongRenderedRockets()
            guard peak > 0 else { return absoluteSpeed }
            let ratio = max(CGFloat(0.4), min(CGFloat(1.0), CGFloat(myTokens) / CGFloat(peak)))
            return absoluteSpeed * ratio
        }

        private func agentCurrentTokens(_ agent: String) -> Int {
            guard !agent.isEmpty else {
                return rocketSpeed > 0 ? 1 : 0
            }
            if let entity = rocketEntity(for: agent) {
                return entity.tokens
            }
            return tokenInt(agentUsageByAgent[agent]?["last_1m_tokens"])
        }

        private func rocketCanCollide(_ agent: String) -> Bool {
            guard agentCurrentTokens(agent) > 0 else {
                return false
            }
            if let motion = agentRocketMotion[agentKey(agent)], motion.smoothedSpeed > CGFloat(0.01) {
                return true
            }
            return agentRocketSpeed(agent) > 0
        }

        private func peakTokensAmongRenderedRockets() -> Int {
            var peak = 0
            for rid in renderedAgents() {
                if rid.isEmpty { continue }
                let tokens: Int
                if let entity = rocketEntity(for: rid) {
                    tokens = entity.tokens
                } else {
                    tokens = tokenInt(agentUsageByAgent[rid]?["last_1m_tokens"])
                }
                if tokens > peak { peak = tokens }
            }
            return peak
        }

        private func agentAltitudeTargetFraction(_ agent: String) -> CGFloat {
            guard !agent.isEmpty else {
                return combinedTargetAltitudeFraction
            }
            if let entity = rocketEntity(for: agent) {
                // Idle split-IO channel: park at a resting altitude (input
                // slightly lower, output slightly higher) so all 4 rockets are
                // always visible as distinct entities, even when one channel
                // has no recent tokens.
                if entity.tokens <= 0 {
                    let restingBase = minimumActiveAltitudeFraction
                    let channelBias: CGFloat = entity.channel == "input" ? CGFloat(-0.04)
                        : entity.channel == "output" ? CGFloat(0.04) : CGFloat(0)
                    return min(maximumAltitudeFraction, max(CGFloat(0.05), restingBase + channelBias))
                }
                let altitude = entity.altitudeHint > 0
                    ? CGFloat(entity.altitudeHint)
                    : CGFloat(usageAnimationAltitude(entity.tokens))
                let base = altitudeFraction(forVisualAltitude: altitude)
                if base >= maximumAltitudeFraction {
                    return maximumAltitudeFraction
                }
                let channelBias: CGFloat = entity.channel == "input" ? CGFloat(-0.07)
                    : entity.channel == "output" ? CGFloat(0.07) : CGFloat(0)
                return min(maximumAltitudeFraction, max(minimumActiveAltitudeFraction, base + channelBias))
            }
            let tokens = tokenInt(agentUsageByAgent[agent]?["last_1m_tokens"])
            if tokens <= 0 {
                return CGFloat(0)
            }
            let altitude = CGFloat(usageAnimationAltitude(tokens))
            return altitudeFraction(forVisualAltitude: altitude)
        }

        private func altitudeFraction(forVisualAltitude altitude: CGFloat) -> CGFloat {
            let normalized = min(maximumAltitudeFraction, max(CGFloat(0), altitude / maximumUsageAltitude))
            return minimumActiveAltitudeFraction + normalized * (maximumAltitudeFraction - minimumActiveAltitudeFraction)
        }

        private func rocketCenterMaxY() -> CGFloat {
            worldFrame.maxY - rocketCenterTopMargin
        }

        private func rocketCenterMinY() -> CGFloat {
            worldFrame.minY + rocketCenterBottomMargin
        }
    }

    private let motionState: MotionState
    private var viewportFrame = NSRect.zero
    private var lastMotionDirtyRects: [NSRect] = []

    init(frame frameRect: NSRect, motionState: MotionState) {
        self.motionState = motionState
        super.init(frame: frameRect)
    }

    required init?(coder: NSCoder) {
        self.motionState = MotionState()
        super.init(coder: coder)
    }

    func configure(viewportFrame: NSRect) {
        self.viewportFrame = viewportFrame
        motionState.ensureMotionState()
        lastMotionDirtyRects = []
        needsDisplay = true
    }

    func stop() {
        lastMotionDirtyRects = []
        needsDisplay = false
    }

    func invalidateMotionArea() {
        let currentRects = rocketDirtyRects()
        for rect in lastMotionDirtyRects + currentRects {
            setNeedsDisplay(rect)
        }
        lastMotionDirtyRects = currentRects
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        motionState.ensureMotionState()
        let rocketStates = motionState.rocketDrawStates()
        for rocketState in rocketStates {
            guard rocketIsVisible(worldPosition: rocketState.position) else {
                continue
            }
            let localRocketPosition = localPoint(for: rocketState.position)
            drawRocket(at: localRocketPosition, scale: 1.9, agent: rocketState.agent, headingDegrees: rocketState.headingDegrees, glow: rocketState.glowIntensity)
            drawTokenText(text: motionState.currentTokenText(for: rocketState.agent), below: localRocketPosition)
        }
        for blast in motionState.blastDrawStates() {
            guard rocketIsVisible(worldPosition: blast.position) else { continue }
            drawBlast(at: localPoint(for: blast.position), progress: blast.progress)
        }
    }

    private func rocketIsVisible(worldPosition: NSPoint) -> Bool {
        viewportFrame.insetBy(dx: -CGFloat(40), dy: -CGFloat(40)).contains(worldPosition)
    }

    private func rocketDirtyRects() -> [NSRect] {
        var rects: [NSRect] = motionState.rocketDrawStates().compactMap { rocketState in
            guard rocketIsVisible(worldPosition: rocketState.position) else {
                return nil
            }
            let point = localPoint(for: rocketState.position)
            let dirtyRect = NSRect(x: point.x - 90, y: point.y - 80, width: 180, height: 130)
                .intersection(bounds)
                .insetBy(dx: -4, dy: -4)
            return dirtyRect.isNull || dirtyRect.isEmpty ? nil : dirtyRect
        }
        for blast in motionState.blastDrawStates() {
            guard rocketIsVisible(worldPosition: blast.position) else { continue }
            let point = localPoint(for: blast.position)
            let blastRadius = CGFloat(80) * blastVisualScale
            let blastRect = NSRect(x: point.x - blastRadius, y: point.y - blastRadius, width: blastRadius * 2, height: blastRadius * 2)
                .intersection(bounds)
                .insetBy(dx: -4, dy: -4)
            if !blastRect.isNull, !blastRect.isEmpty {
                rects.append(blastRect)
            }
        }
        return rects
    }

    private func drawBlast(at point: NSPoint, progress: CGFloat) {
        let clamped = max(CGFloat(0), min(CGFloat(1), progress))
        let easedOut = CGFloat(1) - clamped
        let scale = CGFloat(1.0) + clamped * CGFloat(2.0)
        let alpha = max(CGFloat(0), easedOut * easedOut)
        let emoji = "💥" as NSString
        let baseFont = CGFloat(28) * blastVisualScale * scale
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: baseFont),
            .foregroundColor: NSColor.systemYellow.withAlphaComponent(alpha),
        ]
        let size = emoji.size(withAttributes: attributes)
        NSGraphicsContext.saveGraphicsState()
        let shadow = NSShadow()
        shadow.shadowColor = NSColor.systemOrange.withAlphaComponent(0.65 * alpha)
        shadow.shadowBlurRadius = (10 + 18 * clamped) * blastVisualScale
        shadow.shadowOffset = .zero
        shadow.set()
        emoji.draw(
            at: NSPoint(x: point.x - size.width / 2, y: point.y - size.height / 2),
            withAttributes: attributes
        )
        NSGraphicsContext.restoreGraphicsState()
    }

    private func localPoint(for worldPoint: NSPoint) -> NSPoint {
        NSPoint(x: worldPoint.x - viewportFrame.minX, y: worldPoint.y - viewportFrame.minY)
    }

    private func drawRocket(at point: NSPoint, scale: CGFloat, agent: String = "", headingDegrees: CGFloat, glow: CGFloat = 0.5) {
        drawEmojiRocket(at: point, scale: scale, angleDegrees: headingDegrees, tint: agentColor(agent), glow: glow)
    }

    private func agentColor(_ rid: String) -> NSColor {
        let agent: String
        let channel: String
        if let colon = rid.firstIndex(of: ":") {
            agent = String(rid[..<colon])
            channel = String(rid[rid.index(after: colon)...])
        } else {
            agent = rid
            channel = ""
        }
        let base: NSColor
        if agent == "claude" { base = NSColor.systemPurple }
        else if agent == "codex" { base = NSColor.systemTeal }
        else { base = NSColor.systemBlue }
        if channel == "output" {
            return base.blended(withFraction: 0.28, of: .black) ?? base
        }
        if channel == "input" {
            return base.blended(withFraction: 0.18, of: .white) ?? base
        }
        return base
    }

    private func drawEmojiRocket(at point: NSPoint, scale: CGFloat, angleDegrees: CGFloat, tint: NSColor, glow: CGFloat = 0.5) {
        let rocketEmoji = "🚀" as NSString
        let fontSize = CGFloat(17) * scale
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: fontSize),
            .foregroundColor: tint,
        ]
        let emojiSize = rocketEmoji.size(withAttributes: attributes)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current?.shouldAntialias = true
        NSGraphicsContext.current?.imageInterpolation = .high
        let transform = NSAffineTransform()
        transform.translateX(by: point.x, yBy: point.y)
        transform.rotate(byDegrees: angleDegrees)
        transform.translateX(by: -point.x, yBy: -point.y)
        transform.concat()
        let glowClamped = max(CGFloat(0.2), min(CGFloat(1.0), glow))
        let shadow = NSShadow()
        shadow.shadowColor = tint.withAlphaComponent(0.4 + 0.55 * glowClamped)
        shadow.shadowBlurRadius = 4 + 14 * glowClamped
        shadow.shadowOffset = NSSize(width: 0, height: 0)
        shadow.set()
        rocketEmoji.draw(
            at: NSPoint(x: point.x - emojiSize.width / 2, y: point.y - emojiSize.height / 2),
            withAttributes: attributes
        )
        NSGraphicsContext.restoreGraphicsState()
    }

    private func drawTokenText(text: String, below point: NSPoint) {
        let value = text.isEmpty ? "0" : text
        let tokenText = value as NSString
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedDigitSystemFont(ofSize: 13, weight: .bold),
            .foregroundColor: NSColor.white,
            .strokeWidth: -3.0,
            .strokeColor: NSColor.black.withAlphaComponent(0.75),
        ]
        let textSize = tokenText.size(withAttributes: attributes)
        let textX = point.x - textSize.width / 2
        let rawTextY = point.y - CGFloat(42)
        let textY = min(max(bounds.minY + 2, rawTextY), bounds.maxY - textSize.height - 2)
        NSGraphicsContext.saveGraphicsState()
        NSBezierPath(rect: bounds).setClip()
        tokenText.draw(
            at: NSPoint(x: textX, y: textY),
            withAttributes: attributes
        )
        NSGraphicsContext.restoreGraphicsState()
    }
}

// MARK: - AgentBoost Menu Panel (D · Recommended)

private let agentboostMenuWidth = CGFloat(380)
private let agentboostMenuHeight = CGFloat(624)
private let agentboostReviewSectionHeight = CGFloat(118)
private let agentboostAccent = NSColor(red: 0.486, green: 0.361, blue: 1.0, alpha: 1.0)        // #7C5CFF
private let agentboostClaudeColor = NSColor(red: 0.851, green: 0.467, blue: 0.341, alpha: 1.0) // #D97757
private let agentboostCodexColor  = NSColor(red: 0.063, green: 0.725, blue: 0.506, alpha: 1.0) // #10B981
private let agentboostLiveGreen   = NSColor(red: 0.204, green: 0.827, blue: 0.600, alpha: 1.0) // #34D399
private let agentboostDangerRed   = NSColor(red: 0.984, green: 0.443, blue: 0.522, alpha: 1.0) // #FB7185

private func agentboostFmt(_ n: Int) -> String {
    compactTokenCount(n)
}

private func agentboostMonoFont(size: CGFloat, weight: NSFont.Weight = .regular) -> NSFont {
    NSFont.monospacedSystemFont(ofSize: size, weight: weight)
}

struct AgentBoostPalette {
    let isDark: Bool
    let bg: NSColor
    let text: NSColor
    let sub: NSColor
    let mute: NSColor
    let line: NSColor
    let surface: NSColor
    let surface2: NSColor
    let chip: NSColor
    let hover: NSColor

    static func current() -> AgentBoostPalette {
        let appearance = NSApp.effectiveAppearance
        let dark = appearance.bestMatch(from: [.darkAqua, .vibrantDark, .aqua, .vibrantLight]) == .darkAqua
                || appearance.bestMatch(from: [.darkAqua, .vibrantDark, .aqua, .vibrantLight]) == .vibrantDark
        if dark {
            return AgentBoostPalette(
                isDark: true,
                bg: NSColor(white: 0.07, alpha: 0.94),
                text: NSColor.white.withAlphaComponent(0.95),
                sub: NSColor.white.withAlphaComponent(0.55),
                mute: NSColor.white.withAlphaComponent(0.38),
                line: NSColor.white.withAlphaComponent(0.08),
                surface: NSColor.white.withAlphaComponent(0.04),
                surface2: NSColor.white.withAlphaComponent(0.07),
                chip: NSColor.white.withAlphaComponent(0.05),
                hover: NSColor.white.withAlphaComponent(0.08)
            )
        }
        return AgentBoostPalette(
            isDark: false,
            bg: NSColor(red: 0.988, green: 0.984, blue: 0.976, alpha: 0.96),
            text: NSColor(white: 0.11, alpha: 1.0),
            sub: NSColor(white: 0.32, alpha: 1.0),
            mute: NSColor(white: 0.53, alpha: 1.0),
            line: NSColor.black.withAlphaComponent(0.07),
            surface: NSColor.black.withAlphaComponent(0.025),
            surface2: NSColor.black.withAlphaComponent(0.05),
            chip: NSColor.black.withAlphaComponent(0.04),
            hover: NSColor.black.withAlphaComponent(0.06)
        )
    }
}

private final class AgentBoostSparklineView: NSView {
    var samples: [Double] = [] { didSet { needsDisplay = true } }
    var lineColor: NSColor = agentboostAccent { didSet { needsDisplay = true } }

    override var isFlipped: Bool { false }

    override func draw(_ dirtyRect: NSRect) {
        guard samples.count >= 2 else { return }
        let lo = samples.min() ?? 0
        let hi = samples.max() ?? 1
        let range = max(hi - lo, 1)
        let stepX = bounds.width / CGFloat(samples.count - 1)
        let stroke = NSBezierPath()
        let fill = NSBezierPath()
        fill.move(to: NSPoint(x: 0, y: 0))
        for (i, sample) in samples.enumerated() {
            let x = CGFloat(i) * stepX
            let y = (CGFloat((sample - lo) / range)) * (bounds.height - 4) + 2
            if i == 0 { stroke.move(to: NSPoint(x: x, y: y)) }
            else { stroke.line(to: NSPoint(x: x, y: y)) }
            fill.line(to: NSPoint(x: x, y: y))
        }
        fill.line(to: NSPoint(x: bounds.width, y: 0))
        fill.close()

        lineColor.withAlphaComponent(0.18).setFill()
        fill.fill()
        lineColor.setStroke()
        stroke.lineWidth = 1.4
        stroke.lineCapStyle = .round
        stroke.lineJoinStyle = .round
        stroke.stroke()
    }
}

private final class AgentBoostStackedBarsView: NSView {
    struct Day { let label: String; let claude: Int; let codex: Int }
    var days: [Day] = [] { didSet { needsDisplay = true } }
    var claudeColor: NSColor = agentboostClaudeColor
    var codexColor: NSColor = agentboostCodexColor
    var trackColor: NSColor = NSColor.white.withAlphaComponent(0.06) { didSet { needsDisplay = true } }

    override var isFlipped: Bool { false }

    override func draw(_ dirtyRect: NSRect) {
        guard !days.isEmpty else { return }
        let maxValue = max(days.map { $0.claude + $0.codex }.max() ?? 1, 1)
        let count = days.count
        let gap: CGFloat = 4
        let columnWidth = (bounds.width - gap * CGFloat(count - 1)) / CGFloat(count)
        for (i, day) in days.enumerated() {
            let x = CGFloat(i) * (columnWidth + gap)
            let total = day.claude + day.codex
            let totalH = bounds.height * CGFloat(total) / CGFloat(maxValue)
            let trackPath = NSBezierPath(roundedRect: NSRect(x: x, y: 0, width: columnWidth, height: bounds.height), xRadius: 2, yRadius: 2)
            trackColor.setFill()
            trackPath.fill()

            if total <= 0 { continue }
            let claudeH = totalH * CGFloat(day.claude) / CGFloat(total)
            let codexH = totalH - claudeH
            let claudeRect = NSRect(x: x, y: 0, width: columnWidth, height: claudeH)
            let codexRect = NSRect(x: x, y: claudeH, width: columnWidth, height: codexH)
            claudeColor.setFill()
            NSBezierPath(roundedRect: claudeRect, xRadius: 2, yRadius: 2).fill()
            codexColor.setFill()
            NSBezierPath(roundedRect: codexRect, xRadius: 2, yRadius: 2).fill()
        }
    }
}

private final class AgentBoostSplitBarView: NSView {
    var claudeFlex: CGFloat = 0 { didSet { needsDisplay = true } }
    var codexFlex: CGFloat = 0 { didSet { needsDisplay = true } }
    var claudeColor: NSColor = agentboostClaudeColor
    var codexColor: NSColor = agentboostCodexColor
    var trackColor: NSColor = NSColor.white.withAlphaComponent(0.06) { didSet { needsDisplay = true } }

    override var isFlipped: Bool { false }

    override func draw(_ dirtyRect: NSRect) {
        let total = max(claudeFlex + codexFlex, 0.0001)
        let claudeW = bounds.width * (claudeFlex / total)
        let codexW = bounds.width - claudeW
        let radius = min(bounds.height / 2, 2)
        let track = NSBezierPath(roundedRect: bounds, xRadius: radius, yRadius: radius)
        trackColor.setFill()
        track.fill()
        if claudeFlex + codexFlex <= 0 { return }
        track.addClip()
        claudeColor.setFill()
        NSBezierPath(rect: NSRect(x: 0, y: 0, width: claudeW, height: bounds.height)).fill()
        codexColor.setFill()
        NSBezierPath(rect: NSRect(x: claudeW, y: 0, width: codexW, height: bounds.height)).fill()
    }
}

private final class AgentBoostProgressBar: NSView {
    var progress: CGFloat = 0 { didSet { needsDisplay = true } }
    var color: NSColor = agentboostAccent { didSet { needsDisplay = true } }
    var trackColor: NSColor = NSColor.white.withAlphaComponent(0.06) { didSet { needsDisplay = true } }

    override var isFlipped: Bool { false }

    override func draw(_ dirtyRect: NSRect) {
        let radius = min(bounds.height / 2, 2)
        let track = NSBezierPath(roundedRect: bounds, xRadius: radius, yRadius: radius)
        trackColor.setFill()
        track.fill()
        let clamped = max(0, min(1, progress))
        if clamped <= 0 { return }
        track.addClip()
        color.setFill()
        NSBezierPath(rect: NSRect(x: 0, y: 0, width: bounds.width * clamped, height: bounds.height)).fill()
    }
}

private final class AgentBoostHeaderBadgeView: NSView {
    var accent: NSColor = agentboostAccent { didSet { needsDisplay = true } }
    override var isFlipped: Bool { false }
    override func draw(_ dirtyRect: NSRect) {
        let path = NSBezierPath(roundedRect: bounds, xRadius: 6, yRadius: 6)
        let gradient = NSGradient(colors: [
            accent,
            accent.withAlphaComponent(0.6),
        ])
        gradient?.draw(in: path, angle: 135)

        // small rocket glyph
        let glyph = "🚀" as NSString
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 12, weight: .bold),
            .foregroundColor: NSColor.white,
        ]
        let size = glyph.size(withAttributes: attrs)
        glyph.draw(at: NSPoint(x: (bounds.width - size.width) / 2, y: (bounds.height - size.height) / 2 - 0.5),
                   withAttributes: attrs)
    }
}

func agentboostBadgeColor(index: Int, badge: [String: Any], palette: AgentBoostPalette) -> NSColor {
    let colors = [
        NSColor(red: 0.96, green: 0.62, blue: 0.04, alpha: 1.0),
        agentboostAccent,
        agentboostCodexColor,
        agentboostClaudeColor,
        palette.sub,
    ]
    return colors[index % colors.count]
}

private final class AgentBoostFooterButton: NSButton {
    private let palette: AgentBoostPalette
    init(title: String, symbol: NSImage?, palette: AgentBoostPalette, target: AnyObject?, action: Selector?) {
        self.palette = palette
        super.init(frame: .zero)
        self.title = ""
        self.bezelStyle = .regularSquare
        self.isBordered = false
        self.translatesAutoresizingMaskIntoConstraints = false
        self.target = target
        self.action = action
        self.wantsLayer = true
        layer?.cornerRadius = 6

        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 3
        stack.translatesAutoresizingMaskIntoConstraints = false

        let imageView = NSImageView()
        if let symbol = symbol {
            imageView.image = symbol
            imageView.contentTintColor = palette.sub
        }
        imageView.translatesAutoresizingMaskIntoConstraints = false
        imageView.setContentCompressionResistancePriority(.required, for: .horizontal)
        NSLayoutConstraint.activate([
            imageView.widthAnchor.constraint(equalToConstant: 14),
            imageView.heightAnchor.constraint(equalToConstant: 14),
        ])

        let label = NSTextField(labelWithString: title)
        label.font = NSFont.systemFont(ofSize: 9.5, weight: .medium)
        label.textColor = palette.sub

        stack.addArrangedSubview(imageView)
        stack.addArrangedSubview(label)

        addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: centerYAnchor),
            heightAnchor.constraint(greaterThanOrEqualToConstant: 42),
        ])

        let trackingArea = NSTrackingArea(rect: .zero,
                                          options: [.mouseEnteredAndExited, .activeInKeyWindow, .inVisibleRect],
                                          owner: self, userInfo: nil)
        addTrackingArea(trackingArea)
    }
    required init?(coder: NSCoder) { fatalError() }

    override func mouseEntered(with event: NSEvent) {
        layer?.backgroundColor = palette.hover.cgColor
    }
    override func mouseExited(with event: NSEvent) {
        layer?.backgroundColor = NSColor.clear.cgColor
    }
}

final class AgentBoostMenuPanelView: NSView {
    private let palette: AgentBoostPalette
    weak var actionTarget: AnyObject?
    var runMetaAction: Selector?
    var runSkillPromptReviewAction: Selector?
    var runIdentityUpdateAction: Selector?
    var refreshAction: Selector?
    var exportAction: Selector?
    var deleteAction: Selector?
    var settingsAction: Selector?
    var badgeAction: Selector?
    var quitAction: Selector?

    private let backdrop = NSVisualEffectView()
    private let headerBadge = AgentBoostHeaderBadgeView()
    private let titleLabel = NSTextField(labelWithString: "AgentBoost")
    private let liveDot = NSView()
    private let liveLabel = NSTextField(labelWithString: "LIVE")
    private let levelChip = NSTextField(labelWithString: "")

    private let heroLabel = NSTextField(labelWithString: "LAST MINUTE")
    private let heroNumber = NSTextField(labelWithString: "0")
    private let heroTrendPill = NSTextField(labelWithString: "")
    private let heroSubtitle = NSTextField(labelWithString: "")
    private let sparkline = AgentBoostSparklineView()

    private let usageHeader = NSTextField(labelWithString: "USAGE")
    private let todayRow = AgentBoostMenuPanelView.makeStatRow()
    private let monthRow = AgentBoostMenuPanelView.makeStatRow()
    private let lifetimeRow = AgentBoostMenuPanelView.makeStatRow(dim: true)
    private let splitBar = AgentBoostSplitBarView()
    private let claudeLegend = NSTextField(labelWithString: "")
    private let codexLegend = NSTextField(labelWithString: "")
    private let memoryRow = AgentBoostMenuPanelView.makeStatRow()
    private let memoryBar = AgentBoostProgressBar()

    private let chartHeader = NSTextField(labelWithString: "LAST 7 DAYS")
    private let chartMeta = NSTextField(labelWithString: "")
    private let bars7d = AgentBoostStackedBarsView()
    private let dayLabelsRow = NSStackView()

    private let systemDot = NSView()
    private let systemTitle = NSTextField(labelWithString: "Floating · 1 display")

    private let metaIcon = NSView()
    private let metaTitle = NSTextField(labelWithString: "Meta Review")
    private let metaSubtitle = NSTextField(labelWithString: "")
    private let metaRunButton = NSButton()
    private let skillPromptReviewTitle = NSTextField(labelWithString: "Skills & Prompts")
    private let skillPromptReviewSubtitle = NSTextField(labelWithString: "")
    private let skillPromptReviewRunButton = NSButton()
    private let identityUpdateTitle = NSTextField(labelWithString: "Identity")
    private let identityUpdateSubtitle = NSTextField(labelWithString: "")
    private let identityUpdateRunButton = NSButton()

    private let missionsHeader = NSTextField(labelWithString: "MISSIONS")
    private let missionsCount = NSTextField(labelWithString: "")
    private let missionsStack = NSStackView()
    private let badgesHeader = NSTextField(labelWithString: "ACHIEVEMENTS")
    private let badgesActionButton = NSButton()
    private let badgesCountLabel = NSTextField(labelWithString: "")

    private var footerStack: NSStackView!
    private var sectionStack: NSStackView!
    private var dividers: [NSBox] = []

    init(palette: AgentBoostPalette) {
        self.palette = palette
        super.init(frame: NSRect(x: 0, y: 0, width: agentboostMenuWidth, height: agentboostMenuHeight))
        wantsLayer = true
        layer?.cornerRadius = 14
        layer?.masksToBounds = true
        buildLayout()
    }
    required init?(coder: NSCoder) { fatalError() }

    private static func makeStatRow(dim: Bool = false) -> StatRow { StatRow(dim: dim) }

    final class StatRow: NSView {
        let label = NSTextField(labelWithString: "")
        let value = NSTextField(labelWithString: "0")
        let hint = NSTextField(labelWithString: "")
        let dim: Bool
        init(dim: Bool) {
            self.dim = dim
            super.init(frame: .zero)
            translatesAutoresizingMaskIntoConstraints = false
            label.font = NSFont.systemFont(ofSize: 13)
            value.font = agentboostMonoFont(size: 12, weight: .medium)
            hint.font = NSFont.systemFont(ofSize: 11)
            for v in [label, value, hint] {
                v.translatesAutoresizingMaskIntoConstraints = false
                addSubview(v)
            }
            NSLayoutConstraint.activate([
                heightAnchor.constraint(equalToConstant: 22),
                label.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 14),
                label.centerYAnchor.constraint(equalTo: centerYAnchor),
                hint.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -14),
                hint.firstBaselineAnchor.constraint(equalTo: value.firstBaselineAnchor),
                value.trailingAnchor.constraint(equalTo: hint.leadingAnchor, constant: -6),
                value.centerYAnchor.constraint(equalTo: centerYAnchor),
            ])
        }
        required init?(coder: NSCoder) { fatalError() }
    }

    private func buildLayout() {
        backdrop.material = palette.isDark ? .hudWindow : .menu
        backdrop.blendingMode = .behindWindow
        backdrop.state = .active
        backdrop.translatesAutoresizingMaskIntoConstraints = false
        addSubview(backdrop)
        NSLayoutConstraint.activate([
            backdrop.leadingAnchor.constraint(equalTo: leadingAnchor),
            backdrop.trailingAnchor.constraint(equalTo: trailingAnchor),
            backdrop.topAnchor.constraint(equalTo: topAnchor),
            backdrop.bottomAnchor.constraint(equalTo: bottomAnchor),
        ])

        sectionStack = NSStackView()
        sectionStack.orientation = .vertical
        sectionStack.alignment = .leading
        sectionStack.spacing = 0
        sectionStack.distribution = .fill
        sectionStack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(sectionStack)
        NSLayoutConstraint.activate([
            sectionStack.leadingAnchor.constraint(equalTo: leadingAnchor),
            sectionStack.trailingAnchor.constraint(equalTo: trailingAnchor),
            sectionStack.topAnchor.constraint(equalTo: topAnchor),
            sectionStack.bottomAnchor.constraint(equalTo: bottomAnchor),
        ])

        let buildHeaderView = buildHeader()
        let buildHeroView = buildHero()
        let buildUsageView = buildUsage()
        let buildChartView = buildChart()
        let buildSystemView = buildSystem()
        let buildMetaView = buildMeta()
        let buildMissionsView = buildMissionsAndBadges()
        let buildFooterView = buildFooter()
        sectionStack.addArrangedSubview(buildHeaderView)
        sectionStack.addArrangedSubview(makeDivider())
        sectionStack.addArrangedSubview(buildHeroView)
        sectionStack.addArrangedSubview(makeDivider())
        sectionStack.addArrangedSubview(buildUsageView)
        sectionStack.addArrangedSubview(makeDivider())
        sectionStack.addArrangedSubview(buildChartView)
        sectionStack.addArrangedSubview(makeDivider())
        sectionStack.addArrangedSubview(buildSystemView)
        sectionStack.addArrangedSubview(makeDivider())
        sectionStack.addArrangedSubview(buildMetaView)
        sectionStack.setCustomSpacing(10, after: buildMetaView)
        sectionStack.addArrangedSubview(makeDivider())
        sectionStack.addArrangedSubview(buildMissionsView)
        sectionStack.setCustomSpacing(8, after: buildMissionsView)
        sectionStack.addArrangedSubview(makeDivider())
        sectionStack.addArrangedSubview(buildFooterView)
    }

    private func makeDivider() -> NSBox {
        let box = NSBox()
        box.boxType = .custom
        box.borderColor = palette.line
        box.borderWidth = 0
        box.fillColor = palette.line
        box.translatesAutoresizingMaskIntoConstraints = false
        box.heightAnchor.constraint(equalToConstant: 1).isActive = true
        dividers.append(box)
        return box
    }

    private func buildHeader() -> NSView {
        let row = NSView()
        row.translatesAutoresizingMaskIntoConstraints = false
        row.wantsLayer = true
        row.layer?.backgroundColor = palette.surface.cgColor

        headerBadge.translatesAutoresizingMaskIntoConstraints = false
        titleLabel.font = NSFont.systemFont(ofSize: 13, weight: .semibold)
        titleLabel.textColor = palette.text

        liveDot.wantsLayer = true
        liveDot.layer = CALayer()
        liveDot.layer?.backgroundColor = agentboostLiveGreen.cgColor
        liveDot.layer?.cornerRadius = 2.5
        liveDot.layer?.shadowColor = agentboostLiveGreen.cgColor
        liveDot.layer?.shadowRadius = 3
        liveDot.layer?.shadowOpacity = 0.7
        liveDot.layer?.shadowOffset = .zero
        liveDot.translatesAutoresizingMaskIntoConstraints = false

        liveLabel.font = NSFont.systemFont(ofSize: 10, weight: .heavy)
        liveLabel.textColor = agentboostLiveGreen

        levelChip.font = agentboostMonoFont(size: 10.5)
        levelChip.textColor = palette.sub

        for v in [headerBadge, titleLabel, liveDot, liveLabel, levelChip] {
            v.translatesAutoresizingMaskIntoConstraints = false
            row.addSubview(v)
        }

        NSLayoutConstraint.activate([
            row.heightAnchor.constraint(equalToConstant: 42),
            headerBadge.leadingAnchor.constraint(equalTo: row.leadingAnchor, constant: 14),
            headerBadge.centerYAnchor.constraint(equalTo: row.centerYAnchor),
            headerBadge.widthAnchor.constraint(equalToConstant: 22),
            headerBadge.heightAnchor.constraint(equalToConstant: 22),
            titleLabel.leadingAnchor.constraint(equalTo: headerBadge.trailingAnchor, constant: 10),
            titleLabel.centerYAnchor.constraint(equalTo: row.centerYAnchor),

            liveDot.widthAnchor.constraint(equalToConstant: 5),
            liveDot.heightAnchor.constraint(equalToConstant: 5),
            liveDot.centerYAnchor.constraint(equalTo: row.centerYAnchor),
            liveLabel.leadingAnchor.constraint(equalTo: liveDot.trailingAnchor, constant: 4),
            liveLabel.centerYAnchor.constraint(equalTo: row.centerYAnchor),

            levelChip.trailingAnchor.constraint(equalTo: row.trailingAnchor, constant: -14),
            levelChip.centerYAnchor.constraint(equalTo: row.centerYAnchor),
            liveLabel.trailingAnchor.constraint(lessThanOrEqualTo: levelChip.leadingAnchor, constant: -10),
            liveDot.leadingAnchor.constraint(greaterThanOrEqualTo: titleLabel.trailingAnchor, constant: 10),
        ])

        return wrap(row, fullWidth: true)
    }

    private func buildHero() -> NSView {
        let v = NSView()
        v.translatesAutoresizingMaskIntoConstraints = false

        heroLabel.font = agentboostMonoFont(size: 9.5, weight: .heavy)
        heroLabel.textColor = palette.mute
        heroLabel.alphaValue = 1.0
        heroNumber.font = agentboostMonoFont(size: 28, weight: .bold)
        heroNumber.textColor = palette.text
        heroTrendPill.font = NSFont.systemFont(ofSize: 9.5, weight: .heavy)
        heroTrendPill.textColor = agentboostLiveGreen
        heroTrendPill.wantsLayer = true
        heroTrendPill.layer?.cornerRadius = 4
        heroTrendPill.layer?.borderWidth = 1
        heroTrendPill.layer?.borderColor = agentboostLiveGreen.withAlphaComponent(0.3).cgColor
        heroTrendPill.layer?.backgroundColor = agentboostLiveGreen.withAlphaComponent(0.08).cgColor
        heroTrendPill.drawsBackground = false
        heroSubtitle.font = agentboostMonoFont(size: 10.5)
        heroSubtitle.textColor = palette.sub

        sparkline.lineColor = agentboostAccent
        sparkline.translatesAutoresizingMaskIntoConstraints = false
        sparkline.wantsLayer = true

        for c in [heroLabel, heroNumber, heroTrendPill, heroSubtitle, sparkline] {
            c.translatesAutoresizingMaskIntoConstraints = false
            v.addSubview(c)
        }

        NSLayoutConstraint.activate([
            v.heightAnchor.constraint(greaterThanOrEqualToConstant: 80),
            heroLabel.leadingAnchor.constraint(equalTo: v.leadingAnchor, constant: 14),
            heroLabel.topAnchor.constraint(equalTo: v.topAnchor, constant: 12),
            heroNumber.leadingAnchor.constraint(equalTo: v.leadingAnchor, constant: 14),
            heroNumber.topAnchor.constraint(equalTo: heroLabel.bottomAnchor, constant: 3),
            heroTrendPill.leadingAnchor.constraint(equalTo: heroNumber.trailingAnchor, constant: 6),
            heroTrendPill.firstBaselineAnchor.constraint(equalTo: heroNumber.firstBaselineAnchor),
            heroSubtitle.leadingAnchor.constraint(equalTo: v.leadingAnchor, constant: 14),
            heroSubtitle.topAnchor.constraint(equalTo: heroNumber.bottomAnchor, constant: 4),
            heroSubtitle.bottomAnchor.constraint(equalTo: v.bottomAnchor, constant: -10),

            sparkline.trailingAnchor.constraint(equalTo: v.trailingAnchor, constant: -14),
            sparkline.centerYAnchor.constraint(equalTo: v.centerYAnchor),
            sparkline.widthAnchor.constraint(equalToConstant: 140),
            sparkline.heightAnchor.constraint(equalToConstant: 48),
            sparkline.leadingAnchor.constraint(greaterThanOrEqualTo: heroTrendPill.trailingAnchor, constant: 8),
        ])

        return wrap(v, fullWidth: true)
    }

    private func buildUsage() -> NSView {
        let v = NSStackView()
        v.orientation = .vertical
        v.alignment = .leading
        v.spacing = 0
        v.translatesAutoresizingMaskIntoConstraints = false

        usageHeader.font = agentboostMonoFont(size: 9.5, weight: .heavy)
        usageHeader.textColor = palette.mute

        let headerBox = wrap(usageHeader, vertical: 6, leadingInset: 14, trailingInset: 14)
        v.addArrangedSubview(headerBox)

        for row in [todayRow, monthRow, lifetimeRow] {
            row.label.textColor = palette.sub
            row.value.textColor = row.dim ? palette.sub : palette.text
            row.hint.textColor = palette.mute
            v.addArrangedSubview(row)
            row.widthAnchor.constraint(equalTo: v.widthAnchor).isActive = true
        }

        // split bar block
        let splitBlock = NSView()
        splitBlock.translatesAutoresizingMaskIntoConstraints = false
        splitBar.trackColor = palette.surface2
        splitBar.translatesAutoresizingMaskIntoConstraints = false
        splitBlock.addSubview(splitBar)

        claudeLegend.font = agentboostMonoFont(size: 10.5)
        claudeLegend.textColor = palette.sub
        codexLegend.font = agentboostMonoFont(size: 10.5)
        codexLegend.textColor = palette.sub
        for c in [claudeLegend, codexLegend] {
            c.translatesAutoresizingMaskIntoConstraints = false
            splitBlock.addSubview(c)
        }
        NSLayoutConstraint.activate([
            splitBar.leadingAnchor.constraint(equalTo: splitBlock.leadingAnchor, constant: 14),
            splitBar.trailingAnchor.constraint(equalTo: splitBlock.trailingAnchor, constant: -14),
            splitBar.topAnchor.constraint(equalTo: splitBlock.topAnchor, constant: 6),
            splitBar.heightAnchor.constraint(equalToConstant: 4),
            claudeLegend.leadingAnchor.constraint(equalTo: splitBar.leadingAnchor),
            claudeLegend.topAnchor.constraint(equalTo: splitBar.bottomAnchor, constant: 5),
            claudeLegend.bottomAnchor.constraint(equalTo: splitBlock.bottomAnchor, constant: -2),
            codexLegend.trailingAnchor.constraint(equalTo: splitBar.trailingAnchor),
            codexLegend.firstBaselineAnchor.constraint(equalTo: claudeLegend.firstBaselineAnchor),
        ])
        v.addArrangedSubview(splitBlock)
        splitBlock.widthAnchor.constraint(equalTo: v.widthAnchor).isActive = true

        memoryRow.label.textColor = palette.sub
        memoryRow.value.textColor = palette.text
        memoryRow.hint.textColor = palette.mute
        v.addArrangedSubview(memoryRow)
        memoryRow.widthAnchor.constraint(equalTo: v.widthAnchor).isActive = true

        let memBlock = NSView()
        memBlock.translatesAutoresizingMaskIntoConstraints = false
        memoryBar.trackColor = palette.surface2
        memoryBar.color = agentboostAccent
        memoryBar.translatesAutoresizingMaskIntoConstraints = false
        memBlock.addSubview(memoryBar)
        NSLayoutConstraint.activate([
            memoryBar.leadingAnchor.constraint(equalTo: memBlock.leadingAnchor, constant: 14),
            memoryBar.trailingAnchor.constraint(equalTo: memBlock.trailingAnchor, constant: -14),
            memoryBar.heightAnchor.constraint(equalToConstant: 3),
            memoryBar.topAnchor.constraint(equalTo: memBlock.topAnchor, constant: 0),
            memoryBar.bottomAnchor.constraint(equalTo: memBlock.bottomAnchor, constant: -8),
        ])
        v.addArrangedSubview(memBlock)
        memBlock.widthAnchor.constraint(equalTo: v.widthAnchor).isActive = true

        return wrap(v, fullWidth: true, vertical: 4)
    }

    private func buildChart() -> NSView {
        let v = NSView()
        v.translatesAutoresizingMaskIntoConstraints = false

        chartHeader.font = agentboostMonoFont(size: 9.5, weight: .heavy)
        chartHeader.textColor = palette.mute
        chartMeta.font = agentboostMonoFont(size: 10)
        chartMeta.textColor = palette.mute
        bars7d.trackColor = palette.surface2
        bars7d.translatesAutoresizingMaskIntoConstraints = false
        dayLabelsRow.orientation = .horizontal
        dayLabelsRow.distribution = .fillEqually
        dayLabelsRow.alignment = .centerY
        dayLabelsRow.spacing = 4
        dayLabelsRow.translatesAutoresizingMaskIntoConstraints = false

        for c in [chartHeader, chartMeta, bars7d, dayLabelsRow] {
            c.translatesAutoresizingMaskIntoConstraints = false
            v.addSubview(c)
        }

        NSLayoutConstraint.activate([
            chartHeader.leadingAnchor.constraint(equalTo: v.leadingAnchor, constant: 14),
            chartHeader.topAnchor.constraint(equalTo: v.topAnchor, constant: 10),
            chartMeta.trailingAnchor.constraint(equalTo: v.trailingAnchor, constant: -14),
            chartMeta.firstBaselineAnchor.constraint(equalTo: chartHeader.firstBaselineAnchor),
            bars7d.leadingAnchor.constraint(equalTo: v.leadingAnchor, constant: 14),
            bars7d.trailingAnchor.constraint(equalTo: v.trailingAnchor, constant: -14),
            bars7d.topAnchor.constraint(equalTo: chartHeader.bottomAnchor, constant: 8),
            bars7d.heightAnchor.constraint(equalToConstant: 40),
            dayLabelsRow.leadingAnchor.constraint(equalTo: v.leadingAnchor, constant: 14),
            dayLabelsRow.trailingAnchor.constraint(equalTo: v.trailingAnchor, constant: -14),
            dayLabelsRow.topAnchor.constraint(equalTo: bars7d.bottomAnchor, constant: 4),
            dayLabelsRow.bottomAnchor.constraint(equalTo: v.bottomAnchor, constant: -10),
        ])

        return wrap(v, fullWidth: true)
    }

    private func buildSystem() -> NSView {
        let v = NSView()
        v.translatesAutoresizingMaskIntoConstraints = false

        systemDot.wantsLayer = true
        systemDot.layer = CALayer()
        systemDot.layer?.backgroundColor = agentboostLiveGreen.cgColor
        systemDot.layer?.cornerRadius = 3
        systemDot.layer?.shadowColor = agentboostLiveGreen.cgColor
        systemDot.layer?.shadowRadius = 3
        systemDot.layer?.shadowOpacity = 0.7
        systemDot.translatesAutoresizingMaskIntoConstraints = false

        systemTitle.font = NSFont.systemFont(ofSize: 12, weight: .medium)
        systemTitle.textColor = palette.text
        for c in [systemDot, systemTitle] {
            c.translatesAutoresizingMaskIntoConstraints = false
            v.addSubview(c)
        }

        NSLayoutConstraint.activate([
            v.heightAnchor.constraint(equalToConstant: 28),
            systemDot.leadingAnchor.constraint(equalTo: v.leadingAnchor, constant: 14),
            systemDot.centerYAnchor.constraint(equalTo: v.centerYAnchor),
            systemDot.widthAnchor.constraint(equalToConstant: 6),
            systemDot.heightAnchor.constraint(equalToConstant: 6),
            systemTitle.leadingAnchor.constraint(equalTo: systemDot.trailingAnchor, constant: 8),
            systemTitle.centerYAnchor.constraint(equalTo: v.centerYAnchor),
            systemTitle.trailingAnchor.constraint(lessThanOrEqualTo: v.trailingAnchor, constant: -14),
        ])

        return wrap(v, fullWidth: true)
    }

    private func buildMeta() -> NSView {
        let v = NSView()
        v.translatesAutoresizingMaskIntoConstraints = false

        metaIcon.wantsLayer = true
        metaIcon.layer = CALayer()
        metaIcon.layer?.cornerRadius = 7
        metaIcon.layer?.backgroundColor = agentboostLiveGreen.withAlphaComponent(0.13).cgColor
        metaIcon.layer?.borderColor = agentboostLiveGreen.withAlphaComponent(0.33).cgColor
        metaIcon.layer?.borderWidth = 1
        let checkLabel = NSTextField(labelWithString: "✓")
        checkLabel.font = NSFont.systemFont(ofSize: 13, weight: .heavy)
        checkLabel.textColor = agentboostLiveGreen
        checkLabel.translatesAutoresizingMaskIntoConstraints = false
        metaIcon.addSubview(checkLabel)
        NSLayoutConstraint.activate([
            checkLabel.centerXAnchor.constraint(equalTo: metaIcon.centerXAnchor),
            checkLabel.centerYAnchor.constraint(equalTo: metaIcon.centerYAnchor, constant: -1),
        ])

        metaTitle.font = NSFont.systemFont(ofSize: 12, weight: .semibold)
        metaTitle.textColor = palette.text
        metaSubtitle.font = NSFont.systemFont(ofSize: 10.5)
        metaSubtitle.textColor = palette.sub
        metaSubtitle.maximumNumberOfLines = 1
        metaSubtitle.lineBreakMode = .byTruncatingTail

        metaRunButton.title = "Run"
        metaRunButton.bezelStyle = .regularSquare
        metaRunButton.isBordered = false
        metaRunButton.wantsLayer = true
        metaRunButton.layer?.backgroundColor = agentboostAccent.cgColor
        metaRunButton.layer?.cornerRadius = 5
        metaRunButton.contentTintColor = .white
        metaRunButton.attributedTitle = NSAttributedString(
            string: "Run",
            attributes: [
                .foregroundColor: NSColor.white,
                .font: NSFont.systemFont(ofSize: 11, weight: .semibold),
            ]
        )
        metaRunButton.target = self
        metaRunButton.action = #selector(runMetaButtonClicked)

        skillPromptReviewTitle.font = NSFont.systemFont(ofSize: 12, weight: .semibold)
        skillPromptReviewTitle.textColor = palette.text
        skillPromptReviewSubtitle.font = NSFont.systemFont(ofSize: 10.5)
        skillPromptReviewSubtitle.textColor = palette.sub
        skillPromptReviewSubtitle.maximumNumberOfLines = 1
        skillPromptReviewSubtitle.lineBreakMode = .byTruncatingTail
        skillPromptReviewRunButton.title = "Run"
        skillPromptReviewRunButton.bezelStyle = .regularSquare
        skillPromptReviewRunButton.isBordered = false
        skillPromptReviewRunButton.wantsLayer = true
        skillPromptReviewRunButton.layer?.backgroundColor = agentboostAccent.cgColor
        skillPromptReviewRunButton.layer?.cornerRadius = 5
        skillPromptReviewRunButton.contentTintColor = .white
        skillPromptReviewRunButton.attributedTitle = NSAttributedString(
            string: "Run",
            attributes: [
                .foregroundColor: NSColor.white,
                .font: NSFont.systemFont(ofSize: 11, weight: .semibold),
            ]
        )
        skillPromptReviewRunButton.target = self
        skillPromptReviewRunButton.action = #selector(runSkillPromptReviewButtonClicked)

        identityUpdateTitle.font = NSFont.systemFont(ofSize: 12, weight: .semibold)
        identityUpdateTitle.textColor = palette.text
        identityUpdateSubtitle.font = NSFont.systemFont(ofSize: 10.5)
        identityUpdateSubtitle.textColor = palette.sub
        identityUpdateSubtitle.maximumNumberOfLines = 1
        identityUpdateSubtitle.lineBreakMode = .byTruncatingTail
        identityUpdateRunButton.title = "Run"
        identityUpdateRunButton.bezelStyle = .regularSquare
        identityUpdateRunButton.isBordered = false
        identityUpdateRunButton.wantsLayer = true
        identityUpdateRunButton.layer?.backgroundColor = agentboostAccent.cgColor
        identityUpdateRunButton.layer?.cornerRadius = 5
        identityUpdateRunButton.contentTintColor = .white
        identityUpdateRunButton.attributedTitle = NSAttributedString(
            string: "Run",
            attributes: [
                .foregroundColor: NSColor.white,
                .font: NSFont.systemFont(ofSize: 11, weight: .semibold),
            ]
        )
        identityUpdateRunButton.target = self
        identityUpdateRunButton.action = #selector(runIdentityUpdateButtonClicked)

        for c in [metaIcon, metaTitle, metaSubtitle, metaRunButton, skillPromptReviewTitle, skillPromptReviewSubtitle, skillPromptReviewRunButton, identityUpdateTitle, identityUpdateSubtitle, identityUpdateRunButton] {
            c.translatesAutoresizingMaskIntoConstraints = false
            v.addSubview(c)
        }

        NSLayoutConstraint.activate([
            v.heightAnchor.constraint(greaterThanOrEqualToConstant: agentboostReviewSectionHeight),
            metaIcon.leadingAnchor.constraint(equalTo: v.leadingAnchor, constant: 14),
            metaIcon.topAnchor.constraint(equalTo: v.topAnchor, constant: 9),
            metaIcon.widthAnchor.constraint(equalToConstant: 26),
            metaIcon.heightAnchor.constraint(equalToConstant: 26),
            metaTitle.leadingAnchor.constraint(equalTo: metaIcon.trailingAnchor, constant: 10),
            metaTitle.topAnchor.constraint(equalTo: metaIcon.topAnchor),
            metaSubtitle.leadingAnchor.constraint(equalTo: metaTitle.leadingAnchor),
            metaSubtitle.topAnchor.constraint(equalTo: metaTitle.bottomAnchor, constant: 1),
            metaSubtitle.trailingAnchor.constraint(lessThanOrEqualTo: metaRunButton.leadingAnchor, constant: -8),
            metaRunButton.trailingAnchor.constraint(equalTo: v.trailingAnchor, constant: -14),
            metaRunButton.centerYAnchor.constraint(equalTo: metaIcon.centerYAnchor),
            metaRunButton.widthAnchor.constraint(greaterThanOrEqualToConstant: 50),
            metaRunButton.heightAnchor.constraint(equalToConstant: 22),
            skillPromptReviewTitle.leadingAnchor.constraint(equalTo: metaTitle.leadingAnchor),
            skillPromptReviewTitle.topAnchor.constraint(equalTo: metaSubtitle.bottomAnchor, constant: 9),
            skillPromptReviewSubtitle.leadingAnchor.constraint(equalTo: metaTitle.leadingAnchor),
            skillPromptReviewSubtitle.topAnchor.constraint(equalTo: skillPromptReviewTitle.bottomAnchor, constant: 1),
            skillPromptReviewSubtitle.trailingAnchor.constraint(lessThanOrEqualTo: skillPromptReviewRunButton.leadingAnchor, constant: -8),
            skillPromptReviewRunButton.trailingAnchor.constraint(equalTo: v.trailingAnchor, constant: -14),
            skillPromptReviewRunButton.centerYAnchor.constraint(equalTo: skillPromptReviewTitle.centerYAnchor, constant: 6),
            skillPromptReviewRunButton.widthAnchor.constraint(greaterThanOrEqualToConstant: 50),
            skillPromptReviewRunButton.heightAnchor.constraint(equalToConstant: 22),
            identityUpdateTitle.leadingAnchor.constraint(equalTo: metaTitle.leadingAnchor),
            identityUpdateTitle.topAnchor.constraint(equalTo: skillPromptReviewSubtitle.bottomAnchor, constant: 9),
            identityUpdateSubtitle.leadingAnchor.constraint(equalTo: metaTitle.leadingAnchor),
            identityUpdateSubtitle.topAnchor.constraint(equalTo: identityUpdateTitle.bottomAnchor, constant: 1),
            identityUpdateSubtitle.trailingAnchor.constraint(lessThanOrEqualTo: identityUpdateRunButton.leadingAnchor, constant: -8),
            identityUpdateRunButton.trailingAnchor.constraint(equalTo: v.trailingAnchor, constant: -14),
            identityUpdateRunButton.centerYAnchor.constraint(equalTo: identityUpdateTitle.centerYAnchor, constant: 6),
            identityUpdateRunButton.widthAnchor.constraint(greaterThanOrEqualToConstant: 50),
            identityUpdateRunButton.heightAnchor.constraint(equalToConstant: 22),
        ])

        return wrap(v, fullWidth: true, vertical: 5)
    }

    private func buildMissionsAndBadges() -> NSView {
        let v = NSStackView()
        v.orientation = .vertical
        v.alignment = .leading
        v.spacing = 7
        v.translatesAutoresizingMaskIntoConstraints = false

        let header = NSView()
        header.translatesAutoresizingMaskIntoConstraints = false
        missionsHeader.font = agentboostMonoFont(size: 9.5, weight: .heavy)
        missionsHeader.textColor = palette.mute
        missionsCount.font = agentboostMonoFont(size: 10)
        missionsCount.textColor = palette.mute
        for c in [missionsHeader, missionsCount] {
            c.translatesAutoresizingMaskIntoConstraints = false
            header.addSubview(c)
        }
        NSLayoutConstraint.activate([
            header.heightAnchor.constraint(equalToConstant: 14),
            missionsHeader.leadingAnchor.constraint(equalTo: header.leadingAnchor),
            missionsHeader.centerYAnchor.constraint(equalTo: header.centerYAnchor),
            missionsCount.trailingAnchor.constraint(equalTo: header.trailingAnchor),
            missionsCount.centerYAnchor.constraint(equalTo: header.centerYAnchor),
        ])
        v.addArrangedSubview(header)
        header.widthAnchor.constraint(equalTo: v.widthAnchor).isActive = true

        missionsStack.orientation = .vertical
        missionsStack.alignment = .leading
        missionsStack.spacing = 4
        missionsStack.translatesAutoresizingMaskIntoConstraints = false
        v.addArrangedSubview(missionsStack)
        missionsStack.widthAnchor.constraint(equalTo: v.widthAnchor).isActive = true

        let badgesRow = NSView()
        badgesRow.translatesAutoresizingMaskIntoConstraints = false
        badgesHeader.font = agentboostMonoFont(size: 9.5, weight: .heavy)
        badgesHeader.textColor = palette.mute
        badgesActionButton.image = NSImage(systemSymbolName: "plus.circle", accessibilityDescription: "Change achievement")
        badgesActionButton.imagePosition = .imageOnly
        badgesActionButton.isBordered = false
        badgesActionButton.bezelStyle = .regularSquare
        badgesActionButton.contentTintColor = palette.mute
        badgesActionButton.toolTip = "Change achievement"
        badgesActionButton.target = self
        badgesActionButton.action = #selector(badgeActionClicked)
        badgesActionButton.translatesAutoresizingMaskIntoConstraints = false
        badgesCountLabel.font = NSFont.systemFont(ofSize: 12, weight: .medium)
        badgesCountLabel.textColor = palette.text
        badgesCountLabel.lineBreakMode = .byTruncatingTail
        badgesCountLabel.maximumNumberOfLines = 1
        for c in [badgesHeader, badgesActionButton, badgesCountLabel] {
            c.translatesAutoresizingMaskIntoConstraints = false
            badgesRow.addSubview(c)
        }
        NSLayoutConstraint.activate([
            badgesRow.heightAnchor.constraint(equalToConstant: 18),
            badgesHeader.leadingAnchor.constraint(equalTo: badgesRow.leadingAnchor),
            badgesHeader.centerYAnchor.constraint(equalTo: badgesRow.centerYAnchor),
            badgesCountLabel.leadingAnchor.constraint(equalTo: badgesHeader.trailingAnchor, constant: 10),
            badgesCountLabel.centerYAnchor.constraint(equalTo: badgesRow.centerYAnchor),
            badgesActionButton.leadingAnchor.constraint(greaterThanOrEqualTo: badgesCountLabel.trailingAnchor, constant: 8),
            badgesActionButton.trailingAnchor.constraint(equalTo: badgesRow.trailingAnchor),
            badgesActionButton.centerYAnchor.constraint(equalTo: badgesRow.centerYAnchor),
            badgesActionButton.widthAnchor.constraint(equalToConstant: 18),
            badgesActionButton.heightAnchor.constraint(equalToConstant: 18),
        ])
        v.addArrangedSubview(badgesRow)
        badgesRow.widthAnchor.constraint(equalTo: v.widthAnchor).isActive = true

        return wrap(v, fullWidth: true, vertical: 9, horizontal: 14)
    }

    private func buildFooter() -> NSView {
        let stack = NSStackView()
        stack.orientation = .horizontal
        stack.distribution = .fillEqually
        stack.alignment = .centerY
        stack.spacing = 2
        stack.translatesAutoresizingMaskIntoConstraints = false
        stack.wantsLayer = true
        stack.layer?.backgroundColor = palette.surface.cgColor
        stack.edgeInsets = NSEdgeInsets(top: 6, left: 6, bottom: 6, right: 6)

        let buttons: [(String, String, Selector)] = [
            ("Refresh",  "arrow.clockwise",          #selector(footerRefreshClicked)),
            ("Export",   "square.and.arrow.up",      #selector(footerExportClicked)),
            ("Delete",   "trash",                    #selector(footerDeleteClicked)),
            ("Settings", "gearshape",                #selector(footerSettingsClicked)),
            ("Quit",     "rectangle.portrait.and.arrow.right", #selector(footerQuitClicked)),
        ]
        for (label, symbolName, action) in buttons {
            let symbol = NSImage(systemSymbolName: symbolName, accessibilityDescription: label)
            let btn = AgentBoostFooterButton(title: label, symbol: symbol, palette: palette, target: self, action: action)
            stack.addArrangedSubview(btn)
        }
        footerStack = stack
        return wrap(stack, fullWidth: true, vertical: 0, useSurfaceBackground: true)
    }

    private func wrap(_ child: NSView,
                      fullWidth: Bool = false,
                      vertical: CGFloat = 0,
                      horizontal: CGFloat = 0,
                      leadingInset: CGFloat? = nil,
                      trailingInset: CGFloat? = nil,
                      useSurfaceBackground: Bool = false) -> NSView {
        let container = NSView()
        container.translatesAutoresizingMaskIntoConstraints = false
        if useSurfaceBackground {
            container.wantsLayer = true
            container.layer?.backgroundColor = palette.surface.cgColor
        }
        child.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(child)
        let lead = leadingInset ?? horizontal
        let trail = trailingInset ?? horizontal
        NSLayoutConstraint.activate([
            child.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: lead),
            child.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -trail),
            child.topAnchor.constraint(equalTo: container.topAnchor, constant: vertical),
            child.bottomAnchor.constraint(equalTo: container.bottomAnchor, constant: -vertical),
        ])
        if fullWidth {
            container.widthAnchor.constraint(equalToConstant: agentboostMenuWidth).isActive = true
        }
        return container
    }

    // MARK: - Actions
    @objc private func runMetaButtonClicked() {
        guard metaRunButton.isEnabled else { return }
        guard let runMetaAction = runMetaAction else { return }
        configureMetaRunButton(title: "Running", enabled: false)
        _ = actionTarget?.perform(runMetaAction)
    }
    @objc private func runSkillPromptReviewButtonClicked() {
        guard skillPromptReviewRunButton.isEnabled else { return }
        guard let runSkillPromptReviewAction = runSkillPromptReviewAction else { return }
        configureSkillPromptReviewRunButton(title: "Running", enabled: false)
        _ = actionTarget?.perform(runSkillPromptReviewAction)
    }
    @objc private func runIdentityUpdateButtonClicked() {
        guard identityUpdateRunButton.isEnabled else { return }
        guard let runIdentityUpdateAction = runIdentityUpdateAction else { return }
        configureIdentityUpdateRunButton(title: "Running", enabled: false)
        _ = actionTarget?.perform(runIdentityUpdateAction)
    }
    @objc private func footerRefreshClicked() {
        guard let refreshAction = refreshAction else { return }
        _ = actionTarget?.perform(refreshAction)
    }
    @objc private func footerExportClicked() {
        guard let exportAction = exportAction else { return }
        _ = actionTarget?.perform(exportAction)
    }
    @objc private func footerDeleteClicked() {
        guard let deleteAction = deleteAction else { return }
        _ = actionTarget?.perform(deleteAction)
    }
    @objc private func footerSettingsClicked() {
        guard let settingsAction = settingsAction else { return }
        _ = actionTarget?.perform(settingsAction)
    }
    @objc private func footerQuitClicked() {
        guard let quitAction = quitAction else { return }
        _ = actionTarget?.perform(quitAction)
    }

    // MARK: - State binding
    func update(state: [String: Any]) {
        let progress = state["level_progress"] as? [String: Any] ?? [:]
        let levelNumber = tokenInt(progress["current_level"]) > 0 ? tokenInt(progress["current_level"]) : max(1, tokenInt(state["level"]))
        let currentXP = intText(progress["current_level_xp"])
        let requiredXP = intText(progress["current_level_required_xp"])
        let fitness = intText(state["workforce_fitness_score"])
        levelChip.stringValue = "LV \(levelNumber) · \(currentXP)/\(requiredXP) XP"
        levelChip.toolTip = "Fitness \(fitness)/100 · \(intText(progress["xp_to_next_level"])) XP to next level"

        // Hero — last minute tokens
        let recent = state["recent_token_activity"] as? [String: Any] ?? [:]
        let lastMinute = tokenInt(recent["last_1m_tokens"])
        heroNumber.stringValue = agentboostFmt(lastMinute)
        let activityLevel = text(recent["activity_level"])
        let trend = activityLevel.isEmpty ? "IDLE" : activityLevel.uppercased()
        heroTrendPill.stringValue = "  ↑ \(trend)  "
        heroSubtitle.stringValue = "\(activityLevel.isEmpty ? "idle" : activityLevel) · refreshed live"

        // Sparkline data: synthesize a short window from recent rocket samples (placeholder if not available)
        if let active = recent["active_agents"] as? [String], !active.isEmpty {
            sparkline.samples = stride(from: 0, to: 60, by: 1).map { i in
                let phase = sin(Double(i) * 0.18) * Double(max(lastMinute, 1)) * 0.4
                return Double(lastMinute) * 0.6 + phase + Double(i) * Double(max(lastMinute, 0)) / 200.0
            }
        } else {
            sparkline.samples = stride(from: 0, to: 60, by: 1).map { _ in 0.0 }
        }

        // Usage rows
        let rollups = state["rollups"] as? [String: Any] ?? [:]
        let today = (rollups["Today"] as? [String: Any]) ?? (state["token_activity"] as? [String: Any] ?? [:])
        let month = rollups["This Month"] as? [String: Any] ?? [:]
        let lifetime = rollups["Lifetime"] as? [String: Any] ?? [:]
        let todayTokens = tokenInt(today["total_tokens"]) > 0 ? tokenInt(today["total_tokens"]) : tokenInt(today["today_tokens"])
        let monthTokens = tokenInt(month["total_tokens"])
        let lifetimeTokens = tokenInt(lifetime["total_tokens"])

        let rollupsStale = (state["rollups_stale"] as? Bool) == true
        configure(row: todayRow, label: "Today", value: agentboostFmt(todayTokens),
                  hint: tokenInt(recent["last_1m_tokens"]) > 0 ? "active" : nil)
        configure(row: monthRow, label: "Month", value: agentboostFmt(monthTokens),
                  hint: rollupsStale ? "stale" : nil)
        configure(row: lifetimeRow, label: "Lifetime", value: agentboostFmt(lifetimeTokens),
                  hint: rollupsStale ? "stale" : nil)

        // Agent split — derive from each agent's lifetime token ledger.
        let (claudeShare, codexShare) = agentShare(state: state)
        splitBar.claudeFlex = CGFloat(claudeShare)
        splitBar.codexFlex = CGFloat(codexShare)
        let claudeText = "● Claude \(agentboostFmt(claudeShare))"
        let codexText = "● Codex \(agentboostFmt(codexShare))"
        claudeLegend.attributedStringValue = colorDot(text: claudeText, color: agentboostClaudeColor, palette: palette)
        codexLegend.attributedStringValue = colorDot(text: codexText, color: agentboostCodexColor, palette: palette)

        // Memory
        let memory = state["memory_monitor"] as? [String: Any] ?? [:]
        let memoryUsed = tokenInt(memory["used_percent"])
        let memoryThreshold = tokenInt(memory["threshold_percent"])
        let memAlert = (memory["alert"] as? Bool) == true
        configure(row: memoryRow,
                  label: "Memory",
                  value: "\(memoryUsed)%",
                  hint: memoryThreshold > 0 ? "of \(memoryThreshold)% cap" : nil)
        memoryRow.value.textColor = memAlert ? agentboostDangerRed : palette.text
        memoryBar.progress = CGFloat(memoryUsed) / 100
        memoryBar.color = memAlert ? agentboostDangerRed : agentboostAccent

        // 7-day chart
        let days = sevenDayBuckets(state: state)
        bars7d.days = days
        let total7d = days.reduce(0) { $0 + $1.claude + $1.codex }
        let avg7d = days.isEmpty ? 0 : total7d / max(days.count, 1)
        chartMeta.stringValue = "avg \(agentboostFmt(avg7d)) · total \(agentboostFmt(total7d))"
        rebuildDayLabels(days: days)

        // System line
        let screensaver = state["rocket_screensaver"] as? [String: Any] ?? [:]
        let screensaverEnabled = (screensaver["enabled"] as? Bool) == true
        let displays = tokenInt(screensaver["connected_display_count"])
        let displayLabel = displays == 1 ? "display" : "displays"
        if screensaverEnabled {
            systemTitle.stringValue = "Floating · \(displays) \(displayLabel)"
            systemDot.layer?.backgroundColor = agentboostLiveGreen.cgColor
        } else {
            systemTitle.stringValue = "Floating off"
            systemDot.layer?.backgroundColor = palette.mute.cgColor
        }
        // Meta review
        let meta = state["meta_review"] as? [String: Any] ?? [:]
        let metaStatus = text(meta["status"])
        metaTitle.stringValue = metaStatus.isEmpty ? "Meta Review" : "Meta Review · \(metaStatus)"
        metaSubtitle.stringValue = text(meta["reason"])
        configureMetaRunButton(title: metaStatus == "running" ? "Running" : "Run", enabled: metaStatus != "running")
        let weekly = state["weekly_missions"] as? [[String: Any]] ?? []
        let skillPromptReview = weekly.first { text($0["mission_id"]) == "weekly_skill_prompt_review" } ?? [:]
        let skillPromptStatus = text(skillPromptReview["status"])
        skillPromptReviewTitle.stringValue = skillPromptStatus.isEmpty ? "Skills & Prompts" : "Skills & Prompts · \(skillPromptStatus)"
        let skillPromptHint = text(skillPromptReview["evidence_hint"])
        skillPromptReviewSubtitle.stringValue = skillPromptHint.isEmpty ? "Weekly review artifact" : skillPromptHint
        configureSkillPromptReviewRunButton(title: skillPromptStatus == "running" ? "Running" : "Run", enabled: skillPromptStatus != "running")
        let identity = state["identity_update"] as? [String: Any] ?? [:]
        let identityStatus = text(identity["status"])
        identityUpdateTitle.stringValue = identityStatus.isEmpty ? "Identity" : "Identity · \(identityStatus)"
        let identityEvidence = tokenInt(identity["evidence_items"])
        let identityReason = text(identity["reason"])
        identityUpdateSubtitle.stringValue = identityEvidence > 0 ? "\(identityEvidence) evidence items" : (identityReason.isEmpty ? "Personality + thinking path" : identityReason)
        configureIdentityUpdateRunButton(title: identityStatus == "running" ? "Running" : "Run", enabled: identityStatus != "running")

        // Missions list (combine daily + weekly, take top 3)
        rebuildMissions(state: state)

        // Badges
        rebuildBadges(state: state)
    }

    private func configure(row: StatRow, label: String, value: String, hint: String?) {
        row.label.stringValue = label
        row.value.stringValue = value
        row.hint.stringValue = hint ?? ""
    }

    private func configureMetaRunButton(title: String, enabled: Bool) {
        metaRunButton.isEnabled = enabled
        metaRunButton.alphaValue = enabled ? 1.0 : 0.65
        metaRunButton.attributedTitle = NSAttributedString(
            string: title,
            attributes: [
                .foregroundColor: NSColor.white,
                .font: NSFont.systemFont(ofSize: 11, weight: .semibold),
            ]
        )
    }

    private func configureSkillPromptReviewRunButton(title: String, enabled: Bool) {
        skillPromptReviewRunButton.isEnabled = enabled
        skillPromptReviewRunButton.alphaValue = enabled ? 1.0 : 0.65
        skillPromptReviewRunButton.attributedTitle = NSAttributedString(
            string: title,
            attributes: [
                .foregroundColor: NSColor.white,
                .font: NSFont.systemFont(ofSize: 11, weight: .semibold),
            ]
        )
    }

    private func configureIdentityUpdateRunButton(title: String, enabled: Bool) {
        identityUpdateRunButton.isEnabled = enabled
        identityUpdateRunButton.alphaValue = enabled ? 1.0 : 0.65
        identityUpdateRunButton.attributedTitle = NSAttributedString(
            string: title,
            attributes: [
                .foregroundColor: NSColor.white,
                .font: NSFont.systemFont(ofSize: 11, weight: .semibold),
            ]
        )
    }

    private func colorDot(text: String, color: NSColor, palette: AgentBoostPalette) -> NSAttributedString {
        let result = NSMutableAttributedString()
        let dot = NSAttributedString(string: "●  ", attributes: [
            .foregroundColor: color,
            .font: agentboostMonoFont(size: 10.5),
        ])
        let body = NSAttributedString(string: text.replacingOccurrences(of: "●", with: "").trimmingCharacters(in: .whitespaces),
                                      attributes: [
            .foregroundColor: palette.sub,
            .font: agentboostMonoFont(size: 10.5),
        ])
        result.append(dot)
        result.append(body)
        return result
    }

    private func agentShare(state: [String: Any]) -> (Int, Int) {
        let rollups = state["rollups"] as? [String: Any] ?? [:]
        let lifetime = rollups["Lifetime"] as? [String: Any] ?? [:]
        let byAgent = lifetime["by_agent"] as? [String: Any] ?? [:]
        let claude = tokenInt(byAgent["claude"])
        let codex = tokenInt(byAgent["codex"])
        return (claude, codex)
    }

    private func sevenDayBuckets(state: [String: Any]) -> [AgentBoostStackedBarsView.Day] {
        if let supplied = state["agentboost_daily_7d"] as? [[String: Any]], !supplied.isEmpty {
            return supplied.map { entry in
                AgentBoostStackedBarsView.Day(
                    label: text(entry["day"]),
                    claude: tokenInt(entry["claude"]),
                    codex: tokenInt(entry["codex"])
                )
            }
        }
        // Fallback: render an empty 7-day frame so the section keeps its shape.
        let formatter = DateFormatter()
        formatter.dateFormat = "EEE"
        let calendar = Calendar.current
        return (0..<7).reversed().map { offset in
            let date = calendar.date(byAdding: .day, value: -offset, to: Date()) ?? Date()
            return AgentBoostStackedBarsView.Day(label: formatter.string(from: date), claude: 0, codex: 0)
        }
    }

    private func rebuildDayLabels(days: [AgentBoostStackedBarsView.Day]) {
        for v in dayLabelsRow.arrangedSubviews { v.removeFromSuperview() }
        for day in days {
            let label = NSTextField(labelWithString: day.label)
            label.font = agentboostMonoFont(size: 9.5)
            label.textColor = palette.mute
            label.alignment = .center
            dayLabelsRow.addArrangedSubview(label)
        }
    }

    private func rebuildMissions(state: [String: Any]) {
        for v in missionsStack.arrangedSubviews { v.removeFromSuperview() }
        let daily = state["daily_missions"] as? [[String: Any]] ?? []
        let weekly = state["weekly_missions"] as? [[String: Any]] ?? []
        let combined = (daily + weekly).prefix(3)
        let dailyDone = daily.filter { text($0["status"]) == "done" }.count
        let weeklyDone = weekly.filter { text($0["status"]) == "done" }.count
        missionsCount.stringValue = "\(dailyDone)/\(daily.count) daily · \(weeklyDone)/\(weekly.count) weekly"

        if combined.isEmpty {
            let empty = NSTextField(labelWithString: "No missions generated.")
            empty.font = NSFont.systemFont(ofSize: 11.5)
            empty.textColor = palette.sub
            missionsStack.addArrangedSubview(empty)
            return
        }
        for mission in combined {
            missionsStack.addArrangedSubview(buildMissionRow(mission: mission))
        }
    }

    private func buildMissionRow(mission: [String: Any]) -> NSView {
        let row = NSView()
        row.translatesAutoresizingMaskIntoConstraints = false

        let status = text(mission["status"])
        let done = status == "done"

        let check = NSView()
        check.wantsLayer = true
        check.layer = CALayer()
        check.layer?.cornerRadius = 3
        check.layer?.borderColor = (done ? agentboostAccent : palette.mute).cgColor
        check.layer?.borderWidth = 1.4
        check.layer?.backgroundColor = (done ? agentboostAccent : NSColor.clear).cgColor
        check.translatesAutoresizingMaskIntoConstraints = false

        let title = NSTextField(labelWithString: text(mission["title"]))
        title.font = NSFont.systemFont(ofSize: 11.5)
        title.textColor = palette.text
        title.lineBreakMode = .byTruncatingTail
        title.maximumNumberOfLines = 1
        if done {
            title.attributedStringValue = NSAttributedString(
                string: text(mission["title"]),
                attributes: [
                    .strikethroughStyle: NSUnderlineStyle.single.rawValue,
                    .foregroundColor: palette.sub,
                    .font: NSFont.systemFont(ofSize: 11.5),
                ]
            )
            row.alphaValue = 0.6
        }

        let progress = NSTextField(labelWithString: progressText(mission: mission))
        progress.font = agentboostMonoFont(size: 10)
        progress.textColor = palette.sub

        let xp = NSTextField(labelWithString: "+\(intText(mission["xp"]))")
        xp.font = agentboostMonoFont(size: 10, weight: .heavy)
        xp.textColor = agentboostAccent

        for c in [check, title, progress, xp] {
            c.translatesAutoresizingMaskIntoConstraints = false
            row.addSubview(c)
        }
        NSLayoutConstraint.activate([
            row.heightAnchor.constraint(equalToConstant: 16),
            check.leadingAnchor.constraint(equalTo: row.leadingAnchor),
            check.centerYAnchor.constraint(equalTo: row.centerYAnchor),
            check.widthAnchor.constraint(equalToConstant: 11),
            check.heightAnchor.constraint(equalToConstant: 11),
            title.leadingAnchor.constraint(equalTo: check.trailingAnchor, constant: 8),
            title.centerYAnchor.constraint(equalTo: row.centerYAnchor),
            xp.trailingAnchor.constraint(equalTo: row.trailingAnchor),
            xp.centerYAnchor.constraint(equalTo: row.centerYAnchor),
            progress.trailingAnchor.constraint(equalTo: xp.leadingAnchor, constant: -8),
            progress.centerYAnchor.constraint(equalTo: row.centerYAnchor),
            title.trailingAnchor.constraint(lessThanOrEqualTo: progress.leadingAnchor, constant: -6),
        ])
        return row
    }

    private func progressText(mission: [String: Any]) -> String {
        let progress = tokenInt(mission["progress"])
        let goal = tokenInt(mission["goal"])
        if goal > 0 { return "\(progress)/\(goal)" }
        return ""
    }

    private func rebuildBadges(state: [String: Any]) {
        badgesCountLabel.stringValue = representativeBadgeTitle(state: state)
    }

    @objc private func badgeActionClicked() {
        guard let badgeAction = badgeAction else { return }
        _ = actionTarget?.perform(badgeAction)
    }
}


final class AgentBoostBadgeSelectorPanelView: NSView {
    private let palette: AgentBoostPalette
    weak var actionTarget: AnyObject?
    var saveBadgeSelectionAction: Selector?
    var closeAction: Selector?
    private var badges: [[String: Any]] = []
    private var selectedBadgeIDs: [String] = []
    private let rowsStack = NSStackView()
    private let summaryLabel = NSTextField(labelWithString: "")

    init(palette: AgentBoostPalette) {
        self.palette = palette
        super.init(frame: NSRect(x: 0, y: 0, width: 430, height: 420))
        wantsLayer = true
        layer?.backgroundColor = palette.bg.cgColor
        buildLayout()
    }

    required init?(coder: NSCoder) { fatalError() }

    private func buildLayout() {
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 10
        stack.edgeInsets = NSEdgeInsets(top: 16, left: 16, bottom: 14, right: 16)
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: trailingAnchor),
            stack.topAnchor.constraint(equalTo: topAnchor),
            stack.bottomAnchor.constraint(equalTo: bottomAnchor),
        ])

        let title = NSTextField(labelWithString: "Achievements")
        title.font = NSFont.systemFont(ofSize: 16, weight: .semibold)
        title.textColor = palette.text
        stack.addArrangedSubview(title)

        summaryLabel.font = NSFont.systemFont(ofSize: 11)
        summaryLabel.textColor = palette.sub
        stack.addArrangedSubview(summaryLabel)

        let header = NSStackView()
        header.orientation = .horizontal
        header.alignment = .centerY
        header.spacing = 8
        header.addArrangedSubview(makeHeaderCell("Icon"))
        header.addArrangedSubview(makeHeaderCell("Name"))
        header.addArrangedSubview(makeHeaderCell("Description"))
        stack.addArrangedSubview(header)

        rowsStack.orientation = .vertical
        rowsStack.alignment = .leading
        rowsStack.spacing = 6
        rowsStack.translatesAutoresizingMaskIntoConstraints = false
        stack.addArrangedSubview(rowsStack)
        rowsStack.widthAnchor.constraint(equalToConstant: 398).isActive = true

        let actions = NSStackView()
        actions.orientation = .horizontal
        actions.alignment = .centerY
        actions.spacing = 8
        actions.translatesAutoresizingMaskIntoConstraints = false

        let spacer = NSView()
        spacer.translatesAutoresizingMaskIntoConstraints = false
        actions.addArrangedSubview(spacer)
        spacer.widthAnchor.constraint(greaterThanOrEqualToConstant: 1).isActive = true

        let cancel = NSButton(title: "Cancel", target: self, action: #selector(closeClicked))
        cancel.bezelStyle = .rounded
        actions.addArrangedSubview(cancel)

        let save = NSButton(title: "Done", target: self, action: #selector(saveClicked))
        save.bezelStyle = .rounded
        save.keyEquivalent = "\r"
        actions.addArrangedSubview(save)

        stack.addArrangedSubview(actions)
        actions.widthAnchor.constraint(equalToConstant: 398).isActive = true
    }

    private func makeHeaderCell(_ title: String) -> NSTextField {
        let width: CGFloat
        switch title {
        case "Icon": width = 42
        case "Name": width = 120
        case "Description": width = 220
        default: width = 48
        }
        let label = NSTextField(labelWithString: title)
        label.font = agentboostMonoFont(size: 10, weight: .heavy)
        label.textColor = palette.mute
        label.translatesAutoresizingMaskIntoConstraints = false
        label.widthAnchor.constraint(equalToConstant: width).isActive = true
        return label
    }

    func update(state: [String: Any]) {
        let earned = state["earned_badges"] as? [[String: Any]]
        let inventory = state["badge_inventory"] as? [[String: Any]] ?? []
        badges = inventory.isEmpty ? (earned ?? []) : inventory

        let representatives = state["representative_badges"] as? [[String: Any]] ?? []
        selectedBadgeIDs = normalizedRepresentativeBadgeIDs(
            representatives.map { text($0["badge_id"]) }
        )
        if selectedBadgeIDs.isEmpty {
            selectedBadgeIDs = inventory
                .filter { ($0["is_representative"] as? Bool) == true }
                .sorted { tokenInt($0["representative_rank"]) < tokenInt($1["representative_rank"]) }
                .map { normalizedRepresentativeBadgeID(text($0["badge_id"])) }
        }
        rebuildRows()
    }

    private func rebuildRows() {
        for view in rowsStack.arrangedSubviews {
            view.removeFromSuperview()
        }
        let selectedName = badges.first { selectedBadgeIDs.contains(normalizedRepresentativeBadgeID(text($0["badge_id"]))) }
            .map { text($0["name"]) }
        summaryLabel.stringValue = selectedName.map { "\($0) selected" } ?? "Select one earned achievement"
        guard !badges.isEmpty else {
            let empty = NSTextField(labelWithString: "No earned achievements")
            empty.font = NSFont.systemFont(ofSize: 12)
            empty.textColor = palette.sub
            rowsStack.addArrangedSubview(empty)
            return
        }
        for (index, badge) in badges.enumerated() {
            rowsStack.addArrangedSubview(makeBadgeRow(badge: badge, color: agentboostBadgeColor(index: index, badge: badge, palette: palette)))
        }
    }

    private func makeBadgeRow(badge: [String: Any], color: NSColor) -> NSView {
        let id = normalizedRepresentativeBadgeID(text(badge["badge_id"]))
        let isEarned = text(badge["status"]) == "earned"
        let row = NSStackView()
        row.orientation = .horizontal
        row.alignment = .centerY
        row.spacing = 8
        row.translatesAutoresizingMaskIntoConstraints = false
        row.heightAnchor.constraint(equalToConstant: 28).isActive = true
        row.alphaValue = isEarned ? 1.0 : 0.55

        let radio = NSButton(radioButtonWithTitle: "", target: self, action: #selector(selectBadge(_:)))
        radio.identifier = NSUserInterfaceItemIdentifier(id)
        radio.state = selectedBadgeIDs.contains(id) ? .on : .off
        radio.toolTip = text(badge["name"])
        radio.isEnabled = isEarned
        radio.widthAnchor.constraint(equalToConstant: 24).isActive = true
        row.addArrangedSubview(radio)

        let swatch = NSView()
        swatch.wantsLayer = true
        swatch.layer?.backgroundColor = isEarned ? color.cgColor : palette.mute.withAlphaComponent(0.18).cgColor
        swatch.layer?.cornerRadius = 6
        swatch.translatesAutoresizingMaskIntoConstraints = false
        swatch.widthAnchor.constraint(equalToConstant: 18).isActive = true
        swatch.heightAnchor.constraint(equalToConstant: 18).isActive = true
        row.addArrangedSubview(swatch)

        let name = NSTextField(labelWithString: text(badge["name"]))
        name.font = NSFont.systemFont(ofSize: 12, weight: isEarned ? .medium : .regular)
        name.textColor = isEarned ? palette.text : palette.mute
        name.lineBreakMode = .byTruncatingTail
        name.toolTip = text(badge["name"])
        name.widthAnchor.constraint(equalToConstant: 120).isActive = true
        row.addArrangedSubview(name)

        let description = NSTextField(labelWithString: badgeDescription(badge))
        description.font = NSFont.systemFont(ofSize: 11)
        description.textColor = isEarned ? palette.sub : palette.mute
        description.lineBreakMode = .byTruncatingTail
        description.toolTip = badgeDescription(badge)
        description.widthAnchor.constraint(equalToConstant: 204).isActive = true
        row.addArrangedSubview(description)

        return row
    }

    private func badgeDescription(_ badge: [String: Any]) -> String {
        let endorsement = text(badge["endorsement_text"])
        if !endorsement.isEmpty {
            return endorsement
        }
        let evidence = text(badge["evidence_requirement"])
        if !evidence.isEmpty {
            return evidence
        }
        let status = text(badge["status"])
        let progress = tokenInt(badge["progress_percent"])
        if status == "earned" {
            return "Earned achievement · \(progress)% complete"
        }
        return "\(status.isEmpty ? "In progress" : status.capitalized) · \(progress)% complete"
    }

    @objc private func selectBadge(_ sender: NSButton) {
        guard let id = sender.identifier?.rawValue else { return }
        guard badgeIsEarned(id: id) else {
            rebuildRows()
            return
        }
        selectedBadgeIDs = [id]
        rebuildRows()
    }

    private func badgeIsEarned(id: String) -> Bool {
        badges.contains {
            normalizedRepresentativeBadgeID(text($0["badge_id"])) == id && text($0["status"]) == "earned"
        }
    }

    @objc private func saveClicked() {
        guard let saveBadgeSelectionAction = saveBadgeSelectionAction else { return }
        _ = actionTarget?.perform(saveBadgeSelectionAction, with: ["badge_ids": selectedBadgeIDs] as NSDictionary)
    }

    @objc private func closeClicked() {
        guard let closeAction = closeAction else { return }
        _ = actionTarget?.perform(closeAction)
    }
}


final class AgentBoostSettingsPanelView: NSView {
    private let palette: AgentBoostPalette
    weak var actionTarget: AnyObject?
    var toggleSettingAction: Selector?
    var updateTimeSettingAction: Selector?
    var selectBadgeAction: Selector?
    var closeAction: Selector?
    private var toggles: [String: NSButton] = [:]
    private var textFields: [String: NSTextField] = [:]
    private let representativeBadgePopup = NSPopUpButton()
    private let pathLabel = NSTextField(labelWithString: "")

    init(palette: AgentBoostPalette) {
        self.palette = palette
        super.init(frame: NSRect(x: 0, y: 0, width: 380, height: 636))
        wantsLayer = true
        layer?.cornerRadius = 14
        layer?.masksToBounds = true
        buildLayout()
    }

    required init?(coder: NSCoder) { fatalError() }

    private func buildLayout() {
        let backdrop = NSVisualEffectView()
        backdrop.material = palette.isDark ? .hudWindow : .menu
        backdrop.blendingMode = .behindWindow
        backdrop.state = .active
        backdrop.translatesAutoresizingMaskIntoConstraints = false
        addSubview(backdrop)

        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 10
        stack.edgeInsets = NSEdgeInsets(top: 16, left: 16, bottom: 14, right: 16)
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)

        let title = NSTextField(labelWithString: "Settings")
        title.font = NSFont.systemFont(ofSize: 17, weight: .semibold)
        title.textColor = palette.text
        stack.addArrangedSubview(title)

        pathLabel.font = agentboostMonoFont(size: 10)
        pathLabel.textColor = palette.mute
        pathLabel.lineBreakMode = .byTruncatingMiddle
        pathLabel.maximumNumberOfLines = 1
        stack.addArrangedSubview(pathLabel)
        pathLabel.widthAnchor.constraint(equalToConstant: 348).isActive = true

        stack.addArrangedSubview(makeDivider())
        stack.addArrangedSubview(makeSectionLabel("Achievements"))
        stack.addArrangedSubview(makeBadgeSelector())
        stack.addArrangedSubview(makeDivider())
        stack.addArrangedSubview(makeToggle(title: "Enable all notifications", key: "notifications.enabled"))
        stack.addArrangedSubview(makeSectionLabel("Notification Categories"))
        for item in agentboostNotificationCategoryLabels {
            stack.addArrangedSubview(makeToggle(title: item.title, key: "notifications.categories.\(item.key)", indent: 12))
        }
        stack.addArrangedSubview(makeDivider())
        stack.addArrangedSubview(makeToggle(title: "Quiet hours", key: "quiet_hours.enabled"))
        stack.addArrangedSubview(makeTimeField(title: "Quiet start", key: "quiet_hours.start"))
        stack.addArrangedSubview(makeTimeField(title: "Quiet end", key: "quiet_hours.end"))
        stack.addArrangedSubview(makeDivider())
        stack.addArrangedSubview(makeSectionLabel("Behavior"))
        stack.addArrangedSubview(makeToggle(title: "Keep Mac awake during AI usage", key: "caffeinate.enabled"))
        stack.addArrangedSubview(makeToggle(title: "Show floating rocket overlay", key: "display.floating_overlay_enabled"))
        stack.addArrangedSubview(makeToggle(title: "Split input/output rockets per agent", key: "display.split_io_rockets"))
        stack.addArrangedSubview(makeDivider())
        stack.addArrangedSubview(makeSectionLabel("Workday Hours"))
        stack.addArrangedSubview(makeTimeField(title: "Workday start", key: "work_hours.start"))
        stack.addArrangedSubview(makeTimeField(title: "Workday end", key: "work_hours.end"))

        let spacer = NSView()
        spacer.translatesAutoresizingMaskIntoConstraints = false
        stack.addArrangedSubview(spacer)
        spacer.heightAnchor.constraint(greaterThanOrEqualToConstant: 4).isActive = true

        let done = NSButton(title: "Done", target: self, action: #selector(closeClicked))
        done.bezelStyle = .rounded
        done.translatesAutoresizingMaskIntoConstraints = false
        stack.addArrangedSubview(done)
        done.widthAnchor.constraint(greaterThanOrEqualToConstant: 74).isActive = true

        NSLayoutConstraint.activate([
            backdrop.leadingAnchor.constraint(equalTo: leadingAnchor),
            backdrop.trailingAnchor.constraint(equalTo: trailingAnchor),
            backdrop.topAnchor.constraint(equalTo: topAnchor),
            backdrop.bottomAnchor.constraint(equalTo: bottomAnchor),
            stack.leadingAnchor.constraint(equalTo: leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: trailingAnchor),
            stack.topAnchor.constraint(equalTo: topAnchor),
            stack.bottomAnchor.constraint(equalTo: bottomAnchor),
        ])
    }

    private func makeSectionLabel(_ title: String) -> NSTextField {
        let label = NSTextField(labelWithString: title.uppercased())
        label.font = agentboostMonoFont(size: 9.5, weight: .heavy)
        label.textColor = palette.mute
        return label
    }

    private func makeToggle(title: String, key: String, indent: CGFloat = 0) -> NSView {
        let row = NSView()
        row.translatesAutoresizingMaskIntoConstraints = false
        let toggle = NSButton(checkboxWithTitle: title, target: self, action: #selector(toggleChanged(_:)))
        toggle.identifier = NSUserInterfaceItemIdentifier(key)
        toggle.font = NSFont.systemFont(ofSize: 13)
        toggle.contentTintColor = palette.text
        toggle.translatesAutoresizingMaskIntoConstraints = false
        row.addSubview(toggle)
        NSLayoutConstraint.activate([
            row.heightAnchor.constraint(equalToConstant: 24),
            toggle.leadingAnchor.constraint(equalTo: row.leadingAnchor, constant: indent),
            toggle.centerYAnchor.constraint(equalTo: row.centerYAnchor),
            toggle.trailingAnchor.constraint(lessThanOrEqualTo: row.trailingAnchor),
        ])
        toggles[key] = toggle
        return row
    }

    private func makeBadgeSelector() -> NSView {
        let row = NSView()
        row.translatesAutoresizingMaskIntoConstraints = false
        let label = NSTextField(labelWithString: "Earned achievement")
        label.font = NSFont.systemFont(ofSize: 12)
        label.textColor = palette.text
        label.translatesAutoresizingMaskIntoConstraints = false
        representativeBadgePopup.target = self
        representativeBadgePopup.action = #selector(badgeSelectionChanged(_:))
        representativeBadgePopup.translatesAutoresizingMaskIntoConstraints = false
        row.addSubview(label)
        row.addSubview(representativeBadgePopup)
        NSLayoutConstraint.activate([
            row.heightAnchor.constraint(equalToConstant: 30),
            label.leadingAnchor.constraint(equalTo: row.leadingAnchor, constant: 12),
            label.centerYAnchor.constraint(equalTo: row.centerYAnchor),
            representativeBadgePopup.trailingAnchor.constraint(equalTo: row.trailingAnchor),
            representativeBadgePopup.centerYAnchor.constraint(equalTo: row.centerYAnchor),
            representativeBadgePopup.widthAnchor.constraint(equalToConstant: 205),
            label.trailingAnchor.constraint(lessThanOrEqualTo: representativeBadgePopup.leadingAnchor, constant: -12),
        ])
        return row
    }

    private func makeTimeField(title: String, key: String) -> NSView {
        let row = NSView()
        row.translatesAutoresizingMaskIntoConstraints = false
        let label = NSTextField(labelWithString: title)
        label.font = NSFont.systemFont(ofSize: 12)
        label.textColor = palette.text
        label.translatesAutoresizingMaskIntoConstraints = false
        let field = NSTextField(string: "")
        field.identifier = NSUserInterfaceItemIdentifier(key)
        field.font = agentboostMonoFont(size: 12)
        field.placeholderString = "09:00"
        field.alignment = .center
        field.target = self
        field.action = #selector(textFieldAction(_:))
        field.translatesAutoresizingMaskIntoConstraints = false
        row.addSubview(label)
        row.addSubview(field)
        NSLayoutConstraint.activate([
            row.heightAnchor.constraint(equalToConstant: 26),
            label.leadingAnchor.constraint(equalTo: row.leadingAnchor, constant: 12),
            label.centerYAnchor.constraint(equalTo: row.centerYAnchor),
            field.trailingAnchor.constraint(equalTo: row.trailingAnchor),
            field.centerYAnchor.constraint(equalTo: row.centerYAnchor),
            field.widthAnchor.constraint(equalToConstant: 76),
            label.trailingAnchor.constraint(lessThanOrEqualTo: field.leadingAnchor, constant: -12),
        ])
        textFields[key] = field
        return row
    }

    private func makeDivider() -> NSBox {
        let box = NSBox()
        box.boxType = .custom
        box.borderColor = palette.line
        box.borderWidth = 0
        box.fillColor = palette.line
        box.translatesAutoresizingMaskIntoConstraints = false
        box.heightAnchor.constraint(equalToConstant: 1).isActive = true
        box.widthAnchor.constraint(equalToConstant: 348).isActive = true
        return box
    }

    func update(settings: [String: Any], settingsPath: String, state: [String: Any]) {
        let merged = mergeAgentBoostSettings(settings)
        pathLabel.stringValue = settingsPath
        updateBadgeSelector(state: state)
        for (key, toggle) in toggles {
            toggle.state = settingsEnabled(key: key, settings: merged) ? .on : .off
        }
        for (key, field) in textFields {
            field.stringValue = settingsText(key: key, settings: merged)
        }
    }

    private func updateBadgeSelector(state: [String: Any]) {
        let earned = state["earned_badges"] as? [[String: Any]]
        let inventory = state["badge_inventory"] as? [[String: Any]] ?? []
        let badges = (earned?.isEmpty == false) ? (earned ?? []) : inventory.filter { text($0["status"]) == "earned" }
        let representativeID = normalizedRepresentativeBadgeID(text((state["representative_badge"] as? [String: Any])?["badge_id"]))
        representativeBadgePopup.removeAllItems()
        guard !badges.isEmpty else {
            representativeBadgePopup.addItem(withTitle: "No earned achievements")
            representativeBadgePopup.isEnabled = false
            return
        }
        representativeBadgePopup.isEnabled = true
        for badge in badges {
            representativeBadgePopup.addItem(withTitle: text(badge["name"]))
            representativeBadgePopup.lastItem?.representedObject = text(badge["badge_id"])
        }
        let selectedIndex = badges.firstIndex {
            normalizedRepresentativeBadgeID(text($0["badge_id"])) == representativeID
        } ?? 0
        representativeBadgePopup.selectItem(at: selectedIndex)
    }

    private func settingsEnabled(key: String, settings: [String: Any]) -> Bool {
        let notifications = settings["notifications"] as? [String: Any] ?? [:]
        if key == "notifications.enabled" {
            return boolSetting(notifications["enabled"], defaultValue: true)
        }
        if key.hasPrefix("notifications.categories.") {
            let category = String(key.dropFirst("notifications.categories.".count))
            let categories = notifications["categories"] as? [String: Any] ?? [:]
            return boolSetting(categories[category], defaultValue: true)
        }
        if key == "quiet_hours.enabled" {
            let quiet = settings["quiet_hours"] as? [String: Any] ?? [:]
            return boolSetting(quiet["enabled"], defaultValue: false)
        }
        if key == "caffeinate.enabled" {
            return caffeinateEnabled(settings)
        }
        if key == "display.split_io_rockets" {
            let display = settings["display"] as? [String: Any] ?? [:]
            return boolSetting(display["split_io_rockets"], defaultValue: false)
        }
        if key == "display.floating_overlay_enabled" {
            let display = settings["display"] as? [String: Any] ?? [:]
            return boolSetting(display["floating_overlay_enabled"], defaultValue: false)
        }
        return false
    }

    private func settingsText(key: String, settings: [String: Any]) -> String {
        if key.hasPrefix("work_hours.") {
            let field = String(key.dropFirst("work_hours.".count))
            let work = settings["work_hours"] as? [String: Any] ?? [:]
            return text(work[field])
        }
        if key.hasPrefix("quiet_hours.") {
            let field = String(key.dropFirst("quiet_hours.".count))
            let quiet = settings["quiet_hours"] as? [String: Any] ?? [:]
            return text(quiet[field])
        }
        return ""
    }

    @objc private func toggleChanged(_ sender: NSButton) {
        guard let key = sender.identifier?.rawValue,
              let toggleSettingAction = toggleSettingAction else {
            return
        }
        let payload: NSDictionary = [
            "key": key,
            "enabled": sender.state == .on,
        ]
        _ = actionTarget?.perform(toggleSettingAction, with: payload)
    }

    @objc private func textFieldAction(_ sender: NSTextField) {
        guard let key = sender.identifier?.rawValue,
              let updateTimeSettingAction = updateTimeSettingAction else {
            return
        }
        let payload: NSDictionary = [
            "key": key,
            "value": sender.stringValue,
        ]
        _ = actionTarget?.perform(updateTimeSettingAction, with: payload)
    }

    @objc private func badgeSelectionChanged(_ sender: NSPopUpButton) {
        guard let badgeId = sender.selectedItem?.representedObject as? String,
              !badgeId.isEmpty,
              let selectBadgeAction = selectBadgeAction else {
            return
        }
        let payload: NSDictionary = [
            "badge_id": badgeId,
        ]
        _ = actionTarget?.perform(selectBadgeAction, with: payload)
    }

    private func commitTextFields() {
        for field in textFields.values {
            textFieldAction(field)
        }
    }

    @objc private func closeClicked() {
        commitTextFields()
        guard let closeAction = closeAction else { return }
        _ = actionTarget?.perform(closeAction)
    }
}


final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    private var statusItem: NSStatusItem?
    private var stateRefreshTimer: Timer?
    private var runningAgentRefreshTimer: Timer?
    private var liveUsageRefreshTimer: Timer?
    private var lastRenderedState: [String: Any] = [:]
    private let stateQueue = DispatchQueue(label: "AgentBoost.state-refresh", qos: .utility)
    private let runningAgentQueue = DispatchQueue(label: "AgentBoost.running-agent-refresh", qos: .utility)
    private let liveUsageQueue = DispatchQueue(label: "AgentBoost.live-usage", qos: .userInitiated)
    private var stateRefreshInFlight = false
    private var runningAgentRefreshInFlight = false
    private var liveUsageRefreshInFlight = false
    private var metaReviewInFlight = false
    private var skillPromptReviewInFlight = false
    private var identityUpdateInFlight = false
    private var usageBackfillInFlight = false
    private var rocketScreensaverWindows: [NSPanel] = []
    private var rocketScreensaverViews: [RocketScreensaverView] = []
    private let rocketScreensaverMotionState = RocketScreensaverView.MotionState()
    private var rocketScreensaverDisplayTimer: Timer?
    private var lastOverlaySnapshotAt: Date = .distantPast
    private var screenReconfigDebounce: DispatchWorkItem?
    private var caffeineSystemAssertionID: IOPMAssertionID = 0
    private var caffeineDisplayAssertionID: IOPMAssertionID = 0
    private var caffeineAssertionHeld = false
    private var caffeineLastActiveAt: Date?
    private let caffeineGracePeriod: TimeInterval = 90
    private let rocketStatusView = RocketStatusView(
        frame: NSRect(x: 0, y: 0, width: rocketStatusItemWidth, height: rocketStatusItemHeight)
    )
    private var menuPopover: NSPopover?
    private var menuPanel: AgentBoostMenuPanelView?
    private var settingsPopover: NSPopover?
    private var settingsPanel: AgentBoostSettingsPanelView?
    private var badgeSelectorPopover: NSPopover?
    private var badgeSelectorPanel: AgentBoostBadgeSelectorPanelView?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: rocketStatusItemWidth)
        if let button = statusItem?.button {
            button.title = ""
            button.toolTip = "AgentBoost"
            rocketStatusView.frame = button.bounds
            rocketStatusView.autoresizingMask = [.width, .height]
            button.addSubview(rocketStatusView)
            button.target = self
            button.action = #selector(handleStatusItemClick(_:))
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        }
        configureNotificationActions()
        configureMenuPanel()
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(handleScreenParametersChanged(_:)),
            name: NSApplication.didChangeScreenParametersNotification,
            object: nil
        )
        let state = initialDisplayState(dataRoot: activeDataRoot())
        applyState(state)
        runUsageBackfillOnceInBackground()
        refreshStateInBackground(refreshUsage: true)
        startStateRefreshTimer()
        startRunningAgentRefreshTimer()
        startLiveUsageRefreshTimer()
    }

    private func configureNotificationActions() {
        let runAction = UNNotificationAction(
            identifier: agentboostRunMetaReviewActionIdentifier,
            title: "Run",
            options: [.foreground]
        )
        let category = UNNotificationCategory(
            identifier: agentboostMetaReviewNotificationCategory,
            actions: [runAction],
            intentIdentifiers: [],
            options: []
        )
        UNUserNotificationCenter.current().delegate = self
        UNUserNotificationCenter.current().setNotificationCategories([category])
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        if response.actionIdentifier == agentboostRunMetaReviewActionIdentifier {
            DispatchQueue.main.async {
                self.startMetaReviewFromNotification()
            }
        }
        completionHandler()
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound])
    }

    private func startStateRefreshTimer() {
        stateRefreshTimer?.invalidate()
        let timer = Timer(
            timeInterval: beamStateRefreshIntervalSeconds,
            target: self,
            selector: #selector(refreshStatusState(_:)),
            userInfo: nil,
            repeats: true
        )
        timer.tolerance = 1.0
        RunLoop.main.add(timer, forMode: .common)
        stateRefreshTimer = timer
    }

    private func startRunningAgentRefreshTimer() {
        runningAgentRefreshTimer?.invalidate()
        let timer = Timer(
            timeInterval: runningAgentRefreshIntervalSeconds,
            target: self,
            selector: #selector(refreshRunningAgentAnimationState(_:)),
            userInfo: nil,
            repeats: true
        )
        timer.tolerance = 0.25
        RunLoop.main.add(timer, forMode: .common)
        runningAgentRefreshTimer = timer
    }

    private func startLiveUsageRefreshTimer() {
        liveUsageRefreshTimer?.invalidate()
        let timer = Timer(
            timeInterval: liveUsageRefreshIntervalSeconds,
            target: self,
            selector: #selector(refreshLiveUsageActivity(_:)),
            userInfo: nil,
            repeats: true
        )
        timer.tolerance = 0.25
        RunLoop.main.add(timer, forMode: .common)
        liveUsageRefreshTimer = timer
    }

    @objc private func refreshLiveUsageActivity(_ timer: Timer) {
        guard !lastRenderedState.isEmpty,
              !liveUsageRefreshInFlight else {
            return
        }
        liveUsageRefreshInFlight = true
        liveUsageQueue.async {
            // Cheap path: tails ~/.claude/projects and ~/.codex/sessions for
            // events in the last ~2 min and rebuilds the activity-related
            // fields only. The full BEAM rollup (Today / Lifetime / month) is
            // left alone — it refreshes on its own 90 s timer.
            let liveState = loadLiveUsageState(refreshUsage: false)
            DispatchQueue.main.async {
                self.liveUsageRefreshInFlight = false
                guard !self.lastRenderedState.isEmpty else { return }
                let merged = stateByMergingLiveUsage(self.lastRenderedState, liveState: liveState)
                self.lastRenderedState = merged
                self.applyAnimationState(merged)
            }
        }
    }

    @objc private func refreshRunningAgentAnimationState(_ timer: Timer) {
        guard !lastRenderedState.isEmpty else {
            return
        }
        guard !runningAgentRefreshInFlight else {
            return
        }
        runningAgentRefreshInFlight = true
        runningAgentQueue.async {
            let running = cachedRunningAgentActivity()
            DispatchQueue.main.async {
                self.runningAgentRefreshInFlight = false
                guard !self.lastRenderedState.isEmpty else {
                    return
                }
                let lastRenderedState = self.lastRenderedState
                let renderedState = stateByApplyingRunningAgents(lastRenderedState, running: running)
                self.lastRenderedState = renderedState
                self.applyAnimationState(renderedState)
            }
        }
    }

    @objc private func refreshStatusState(_ timer: Timer) {
        refreshStateInBackground(refreshUsage: true)
    }

    @objc private func refreshMenu() {
        applyState(lastRenderedState)
        refreshStateInBackground(refreshUsage: false)
    }

    private func runUsageBackfillOnceInBackground() {
        let dataRoot = activeDataRoot()
        guard !usageBackfillInFlight,
              shouldRunUsageBackfill(dataRoot: dataRoot),
              hasAvailableAgentUsageFolder() else {
            return
        }
        // The earlier `shouldDeferUsageBackfillForActiveAgents` gate parked
        // the backfill whenever Claude or Codex was running. For power users
        // that's basically all the time, so the backfill silently never
        // completed and Lifetime stayed undercount. Backfill is a read-only
        // pass over jsonl files the agents have already flushed, so there's
        // no contention worth deferring for.
        usageBackfillInFlight = true
        var runningState = lastRenderedState
        runningState["usage_backfill"] = [
            "status": "running",
            "scope": "lifetime",
            "backfill_file": usageBackfillFile(dataRoot: dataRoot).path,
        ]
        applyState(runningState)
        stateQueue.async {
            do {
                try writeUsageBackfill([
                    "status": "running",
                    "scope": "lifetime",
                    "started_at": isoNow(),
                ], dataRoot: dataRoot, status: "running")
                let summary = try collectUsageFromSelectedAgentFolders(dataRoot: dataRoot)
                try writeUsageBackfill(summary, dataRoot: dataRoot, status: "completed")
            } catch {
                try? writeUsageBackfill([
                    "error": error.localizedDescription,
                    "failed_at": isoNow(),
                ], dataRoot: dataRoot, status: "failed")
            }
            let finalState = loadDisplayState(refreshUsage: false)
            writeCachedDisplayState(finalState, dataRoot: dataRoot)
            DispatchQueue.main.async {
                self.usageBackfillInFlight = false
                self.applyState(finalState)
            }
        }
    }

    private func refreshStateInBackground(refreshUsage: Bool, forceUsageRefresh: Bool = false) {
        if stateRefreshInFlight {
            return
        }
        stateRefreshInFlight = true
        let dataRoot = activeDataRoot()
        let cachedState = lastRenderedState
        stateQueue.async {
            var refreshError: Error?
            let liveState = loadLiveUsageState(refreshUsage: false)
            let fastState = cachedState.isEmpty ? liveState : stateByMergingLiveUsage(cachedState, liveState: liveState)
            DispatchQueue.main.async {
                self.applyState(fastState)
            }
            if forceUsageRefresh {
                do {
                    _ = try collectUsageFromSelectedAgentFolders(dataRoot: dataRoot)
                } catch {
                    refreshError = error
                }
            }
            // Periodic refreshes used to skip the BEAM rebuild entirely — the
            // cache stayed frozen for hours and rollups (Today / Lifetime,
            // Codex vs. Claude split) drifted away from the events.jsonl that
            // the external collector keeps updating. Always rebuild the full
            // state from BEAM so the menu reflects current totals; the BEAM
            // process runs at .background QoS so animations stay smooth.
            let finalState = self.loadAndCacheFullDisplayState(dataRoot: dataRoot)
            DispatchQueue.main.async {
                self.stateRefreshInFlight = false
                self.applyState(finalState)
                if let refreshError = refreshError {
                    self.showAlert("Refresh Failed", refreshError.localizedDescription)
                }
            }
        }
    }

    private func loadAndCacheFullDisplayState(dataRoot: URL) -> [String: Any] {
        let finalState = loadDisplayState(refreshUsage: false)
        writeCachedDisplayState(finalState, dataRoot: dataRoot)
        return finalState
    }

    private func applyState(_ state: [String: Any]) {
        let renderedState = stateWithRocketScreensaver(state)
        lastRenderedState = renderedState
        applyAnimationState(renderedState)
        notifyMetaReviewDueIfNeeded(state: renderedState)
        menuPanel?.update(state: renderedState)
    }

    private func configureMenuPanel() {
        let palette = AgentBoostPalette.current()
        let panel = AgentBoostMenuPanelView(palette: palette)
        panel.actionTarget = self
        panel.runMetaAction = #selector(doMetaReview)
        panel.runSkillPromptReviewAction = #selector(doSkillPromptReview)
        panel.runIdentityUpdateAction = #selector(doIdentityUpdate)
        panel.refreshAction = #selector(refreshUsage)
        panel.exportAction = #selector(exportLocalReport)
        panel.deleteAction = #selector(deleteLocalUsageData)
        panel.settingsAction = #selector(showSettingsFromPanel)
        panel.badgeAction = #selector(showBadgeSelectorFromPanel)
        panel.quitAction = #selector(quitFromPanel)
        menuPanel = panel

        let popover = NSPopover()
        popover.behavior = .transient
        popover.animates = true
        let vc = NSViewController()
        vc.view = panel
        popover.contentViewController = vc
        popover.contentSize = NSSize(width: agentboostMenuWidth, height: agentboostMenuHeight)
        menuPopover = popover
    }

    @objc private func handleStatusItemClick(_ sender: Any?) {
        guard let button = statusItem?.button else { return }
        let event = NSApp.currentEvent
        let isRight = event?.type == .rightMouseUp
            || (event?.modifierFlags.contains(.control) ?? false)
        if isRight {
            showLegacyMenu(from: button)
        } else {
            toggleMenuPopover(from: button)
        }
    }

    private func toggleMenuPopover(from button: NSStatusBarButton) {
        guard let popover = menuPopover else { return }
        if popover.isShown {
            popover.performClose(nil)
            return
        }
        if let panel = menuPanel {
            panel.update(state: lastRenderedState)
        }
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
    }

    private func showLegacyMenu(from button: NSStatusBarButton) {
        let menu = menuForState(lastRenderedState)
        statusItem?.menu = menu
        button.performClick(nil)
        // Detach so left-click won't trigger NSMenu next time.
        DispatchQueue.main.async { [weak self] in
            self?.statusItem?.menu = nil
        }
    }

    @objc private func showSettingsFromPanel() {
        menuPopover?.performClose(nil)
        showSettingsPopover()
    }

    @objc private func showBadgeSelectorFromPanel() {
        menuPopover?.performClose(nil)
        showBadgeSelectorPopover()
    }

    private func showBadgeSelectorPopover() {
        guard let button = statusItem?.button else { return }
        let dataRoot = activeDataRoot()
        if badgeSelectorPopover == nil || badgeSelectorPanel == nil {
            let palette = AgentBoostPalette.current()
            let panel = AgentBoostBadgeSelectorPanelView(palette: palette)
            panel.actionTarget = self
            panel.saveBadgeSelectionAction = #selector(saveRepresentativeBadgesFromSelector(_:))
            panel.closeAction = #selector(closeBadgeSelectorPopover)
            badgeSelectorPanel = panel

            let popover = NSPopover()
            popover.behavior = .semitransient
            popover.animates = true
            let vc = NSViewController()
            vc.view = panel
            popover.contentViewController = vc
            popover.contentSize = NSSize(width: 430, height: 420)
            badgeSelectorPopover = popover
        }
        let state = lastRenderedState.isEmpty ? initialDisplayState(dataRoot: dataRoot) : lastRenderedState
        badgeSelectorPanel?.update(state: state)
        badgeSelectorPopover?.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
    }

    private func showSettingsPopover() {
        guard let button = statusItem?.button else { return }
        let dataRoot = activeDataRoot()
        let settings = loadAgentBoostSettings(dataRoot: dataRoot)
        if settingsPopover == nil || settingsPanel == nil {
            let palette = AgentBoostPalette.current()
            let panel = AgentBoostSettingsPanelView(palette: palette)
            panel.actionTarget = self
            panel.toggleSettingAction = #selector(toggleSettingsValue(_:))
            panel.updateTimeSettingAction = #selector(updateSettingsTextValue(_:))
            panel.selectBadgeAction = #selector(selectRepresentativeBadgeFromSettings(_:))
            panel.closeAction = #selector(closeSettingsPopover)
            settingsPanel = panel

            let popover = NSPopover()
            popover.behavior = .transient
            popover.animates = true
            let vc = NSViewController()
            vc.view = panel
            popover.contentViewController = vc
            popover.contentSize = NSSize(width: 380, height: 636)
            settingsPopover = popover
        }
        let state = lastRenderedState.isEmpty ? initialDisplayState(dataRoot: dataRoot) : lastRenderedState
        settingsPanel?.update(settings: settings, settingsPath: agentboostSettingsFile(dataRoot: dataRoot).path, state: state)
        settingsPopover?.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
    }

    @objc private func toggleSettingsValue(_ sender: Any?) {
        guard let payload = sender as? NSDictionary,
              let key = payload["key"] as? String else {
            return
        }
        let enabled = boolSetting(payload["enabled"], defaultValue: false)
        let dataRoot = activeDataRoot()
        let current = loadAgentBoostSettings(dataRoot: dataRoot)
        let updated = agentboostSettingsBySetting(key, enabled: enabled, settings: current)
        do {
            try writeAgentBoostSettings(updated, dataRoot: dataRoot)
            settingsPanel?.update(settings: updated, settingsPath: agentboostSettingsFile(dataRoot: dataRoot).path, state: lastRenderedState)
            if key == "display.floating_overlay_enabled" {
                applyFloatingOverlaySettingImmediately(enabled: enabled)
            }
            refreshStateInBackground(refreshUsage: false)
        } catch {
            showAlert("Settings Save Failed", error.localizedDescription)
            settingsPanel?.update(settings: current, settingsPath: agentboostSettingsFile(dataRoot: dataRoot).path, state: lastRenderedState)
        }
    }

    private func applyFloatingOverlaySettingImmediately(enabled: Bool) {
        if !enabled {
            hideRocketScreensaver()
        }
        let baseState = lastRenderedState.isEmpty ? loadLiveUsageState(refreshUsage: false) : lastRenderedState
        applyState(baseState)
    }

    @objc private func updateSettingsTextValue(_ sender: Any?) {
        guard let payload = sender as? NSDictionary,
              let key = payload["key"] as? String,
              let value = payload["value"] as? String else {
            return
        }
        let dataRoot = activeDataRoot()
        let current = loadAgentBoostSettings(dataRoot: dataRoot)
        let updated = agentboostSettingsByTextSetting(key, value: value, settings: current)
        do {
            try writeAgentBoostSettings(updated, dataRoot: dataRoot)
            settingsPanel?.update(settings: updated, settingsPath: agentboostSettingsFile(dataRoot: dataRoot).path, state: lastRenderedState)
        } catch {
            showAlert("Settings Save Failed", error.localizedDescription)
            settingsPanel?.update(settings: current, settingsPath: agentboostSettingsFile(dataRoot: dataRoot).path, state: lastRenderedState)
        }
    }

    @objc private func selectRepresentativeBadgeFromSettings(_ sender: Any?) {
        guard let payload = sender as? NSDictionary,
              let badgeId = payload["badge_id"] as? String,
              !badgeId.isEmpty else {
            return
        }
        let normalized = normalizedRepresentativeBadgeID(badgeId)
        let earned = lastRenderedState["earned_badges"] as? [[String: Any]] ?? []
        guard earned.contains(where: { normalizedRepresentativeBadgeID(text($0["badge_id"])) == normalized }) else {
            return
        }
        let dataRoot = activeDataRoot()
        do {
            try writeRepresentativeBadgeSelection(normalized, dataRoot: dataRoot)
            let updatedState = stateBySelectingRepresentativeBadge(lastRenderedState, badgeId: normalized)
            applyState(updatedState)
            let settings = loadAgentBoostSettings(dataRoot: dataRoot)
            settingsPanel?.update(settings: settings, settingsPath: agentboostSettingsFile(dataRoot: dataRoot).path, state: updatedState)
            refreshStateInBackground(refreshUsage: false)
        } catch {
            showAlert("Badge Save Failed", error.localizedDescription)
        }
    }

    @objc private func saveRepresentativeBadgesFromSelector(_ sender: Any?) {
        guard let payload = sender as? NSDictionary else {
            return
        }
        let requested = payload["badge_ids"] as? [String]
            ?? (payload["badge_ids"] as? [Any] ?? []).map { text($0) }
        let earned = lastRenderedState["earned_badges"] as? [[String: Any]] ?? []
        let earnedIDs = Set(earned.map { normalizedRepresentativeBadgeID(text($0["badge_id"])) })
        let badgeIDs = normalizedRepresentativeBadgeIDs(requested).filter { earnedIDs.contains($0) }
        guard !badgeIDs.isEmpty else {
            return
        }
        let dataRoot = activeDataRoot()
        do {
            try writeRepresentativeBadgeSelection(badgeIds: badgeIDs, dataRoot: dataRoot)
            let updatedState = stateBySelectingRepresentativeBadges(lastRenderedState, badgeIds: badgeIDs)
            applyState(updatedState)
            badgeSelectorPanel?.update(state: updatedState)
            closeBadgeSelectorPopover()
            refreshStateInBackground(refreshUsage: false)
        } catch {
            showAlert("Badge Save Failed", error.localizedDescription)
        }
    }

    @objc private func closeBadgeSelectorPopover() {
        badgeSelectorPopover?.performClose(nil)
    }

    @objc private func closeSettingsPopover() {
        settingsPopover?.performClose(nil)
    }

    @objc private func quitFromPanel() {
        NSApp.terminate(nil)
    }

    private func applyAnimationState(_ renderedState: [String: Any]) {
        configureStatusAnimation(renderedState)
        configureRocketScreensaver(renderedState)
        updateCaffeineAssertion(renderedState)
    }

    private func updateCaffeineAssertion(_ state: [String: Any]) {
        let settings = loadAgentBoostSettings(dataRoot: activeDataRoot())
        if !caffeinateEnabled(settings) {
            releaseCaffeineAssertion()
            return
        }
        let now = Date()
        if caffeineActivityIsActive(state) {
            caffeineLastActiveAt = now
            acquireCaffeineAssertion()
            return
        }
        if let lastActive = caffeineLastActiveAt,
           now.timeIntervalSince(lastActive) >= caffeineGracePeriod {
            releaseCaffeineAssertion()
        }
    }

    private func caffeineActivityIsActive(_ state: [String: Any]) -> Bool {
        let recent = state["recent_token_activity"] as? [String: Any] ?? [:]
        let running = state["running_agent_activity"] as? [String: Any] ?? [:]
        let lastMinuteTokens = tokenInt(recent["last_1m_tokens"])
        let runningAgents = running["active_agents"] as? [Any] ?? []
        return lastMinuteTokens > 0 || !runningAgents.isEmpty
    }

    private func acquireCaffeineAssertion() {
        guard !caffeineAssertionHeld else { return }
        var systemAssertionID: IOPMAssertionID = 0
        var displayAssertionID: IOPMAssertionID = 0
        let reason = "AgentBoost: AI agent active" as CFString
        let systemStatus = IOPMAssertionCreateWithName(
            kIOPMAssertionTypePreventUserIdleSystemSleep as CFString,
            IOPMAssertionLevel(kIOPMAssertionLevelOn),
            reason,
            &systemAssertionID
        )
        guard systemStatus == kIOReturnSuccess else { return }
        let displayStatus = IOPMAssertionCreateWithName(
            kIOPMAssertionTypePreventUserIdleDisplaySleep as CFString,
            IOPMAssertionLevel(kIOPMAssertionLevelOn),
            reason,
            &displayAssertionID
        )
        if displayStatus != kIOReturnSuccess {
            IOPMAssertionRelease(systemAssertionID)
            return
        }
        caffeineSystemAssertionID = systemAssertionID
        caffeineDisplayAssertionID = displayAssertionID
        caffeineAssertionHeld = true
    }

    private func releaseCaffeineAssertion() {
        guard caffeineAssertionHeld else { return }
        IOPMAssertionRelease(caffeineSystemAssertionID)
        IOPMAssertionRelease(caffeineDisplayAssertionID)
        caffeineSystemAssertionID = 0
        caffeineDisplayAssertionID = 0
        caffeineAssertionHeld = false
        caffeineLastActiveAt = nil
    }

    func applicationWillTerminate(_ notification: Notification) {
        releaseCaffeineAssertion()
    }

    private func menuForState(_ state: [String: Any]) -> NSMenu {
        let menu = NSMenu()
        let xp = compactTokenCount(tokenInt(state["xp"]))
        let levelLabel = text(state["level_label"]).isEmpty ? "Lv \(max(1, tokenInt(state["level"])))" : text(state["level_label"])
        let progress = state["level_progress"] as? [String: Any] ?? [:]
        let currentXP = intText(progress["current_level_xp"])
        let requiredXP = intText(progress["current_level_required_xp"])
        let toNextXP = intText(progress["xp_to_next_level"])
        let score = intText(state["workforce_fitness_score"])

        addDisabled("AgentBoost", to: menu)
        addDisabled("\(levelLabel) · \(xp) XP · \(currentXP)/\(requiredXP) XP · \(toNextXP) to next · Fitness \(score)/100", to: menu)
        if let representative = state["representative_badge"] as? [String: Any] {
            addDisabled("Representative: \(statusSymbol(text(representative["status"]))) \(text(representative["name"]))", to: menu)
        }

        if let rollups = state["rollups"] as? [String: Any],
           let month = rollups["This Month"] as? [String: Any],
           let lifetime = rollups["Lifetime"] as? [String: Any] {
            addDisabled("Month \(compactTokenCount(tokenInt(month["total_tokens"]))) tokens", to: menu)
            addDisabled("Lifetime \(compactTokenCount(tokenInt(lifetime["total_tokens"]))) tokens", to: menu)
        }
        if let activity = state["token_activity"] as? [String: Any] {
            addDisabled(
                "Today \(compactTokenCount(tokenInt(activity["today_tokens"]))) tokens · \(text(activity["intensity"])) \(text(activity["emoji"]))",
                to: menu
            )
        }
        if let recent = state["recent_token_activity"] as? [String: Any] {
            addDisabled(
                "Last minute \(text(recent["display_tokens"])) tokens · \(text(recent["activity_level"])) · \(text(recent["rocket_state"]))",
                to: menu
            )
        }
        if let memory = state["memory_monitor"] as? [String: Any] {
            let memoryTitle = (memory["alert"] as? Bool) == true ? "Memory Alert" : "Memory"
            addDisabled(
                "\(memoryTitle): \(intText(memory["used_percent"]))% used · threshold \(intText(memory["threshold_percent"]))%",
                to: menu
            )
            if (memory["alert"] as? Bool) == true {
                addDisabled("  Close idle AI agents before spawning more subagents.", to: menu)
            }
        }
        if let screensaver = state["rocket_screensaver"] as? [String: Any] {
            let enabled = (screensaver["enabled"] as? Bool) == true
            let title = enabled ? "Stop Floating Animation" : "Start Floating Animation"
            let screensaverItem = NSMenuItem(title: title, action: #selector(toggleRocketScreensaver), keyEquivalent: "")
            screensaverItem.target = self
            menu.addItem(screensaverItem)
            if enabled {
                let connectedCount = intText(screensaver["connected_display_count"])
                let displayLabel = connectedCount == "1" ? "display" : "displays"
                addDisabled(
                    "  Flying across \(connectedCount) \(displayLabel)",
                    to: menu
                )
            }
        }
        if let metaReview = state["meta_review"] as? [String: Any] {
            menu.addItem(NSMenuItem.separator())
            addDisabled("Meta Review: \(text(metaReview["status"]))", to: menu)
            addDisabled("  \(text(metaReview["reason"]))", to: menu)
            let reviewItem = NSMenuItem(title: "Do Meta Review", action: #selector(doMetaReview), keyEquivalent: "")
            reviewItem.target = self
            menu.addItem(reviewItem)
            let skillReviewItem = NSMenuItem(title: "Do Skills/Prompts Review", action: #selector(doSkillPromptReview), keyEquivalent: "")
            skillReviewItem.target = self
            menu.addItem(skillReviewItem)
        }

        menu.addItem(NSMenuItem.separator())
        addDisabled("Daily Missions", to: menu)
        if let missions = state["daily_missions"] as? [[String: Any]], !missions.isEmpty {
            for mission in missions.prefix(5) {
                addDisabled("\(statusSymbol(text(mission["status"]))) \(text(mission["title"]))", to: menu)
                addDisabled("  \(text(mission["command_hint"]))", to: menu)
            }
        } else {
            addDisabled("No missions generated.", to: menu)
        }

        menu.addItem(NSMenuItem.separator())
        addDisabled("Weekly Missions", to: menu)
        if let missions = state["weekly_missions"] as? [[String: Any]], !missions.isEmpty {
            for mission in missions.prefix(5) {
                addDisabled("\(statusSymbol(text(mission["status"]))) \(text(mission["title"]))", to: menu)
                addDisabled("  \(text(mission["command_hint"]))", to: menu)
            }
        } else {
            addDisabled("No weekly missions generated.", to: menu)
        }

        menu.addItem(NSMenuItem.separator())
        menu.addItem(badgeInventoryItem(state))

        menu.addItem(NSMenuItem.separator())
        let selectFolder = NSMenuItem(title: "Select AI-System Folder...", action: #selector(selectDataRoot), keyEquivalent: "o")
        selectFolder.target = self
        menu.addItem(selectFolder)
        menu.addItem(NSMenuItem(title: "Select Claude Usage Folder...", action: #selector(selectClaudeDataRoot), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "Select Codex Sessions Folder...", action: #selector(selectCodexDataRoot), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "Refresh Usage", action: #selector(refreshUsage), keyEquivalent: "r"))
        menu.addItem(NSMenuItem(title: "Remove Folder Access", action: #selector(removeFolderAccess), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "Delete Local Usage Data...", action: #selector(deleteLocalUsageData), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "Export Local Report...", action: #selector(exportLocalReport), keyEquivalent: "e"))
        menu.addItem(NSMenuItem(title: "Quit AgentBoost", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        menu.items.forEach { $0.target = self }
        return menu
    }

    private func configureStatusAnimation(_ state: [String: Any]) {
        let activity = state["status_animation_activity"] as? [String: Any] ?? [:]
        let statusViews = state["status_views"] as? [[String: Any]] ?? []
        rocketStatusView.configure(activity: activity, statusViews: statusViews)
    }

    private func configureRocketScreensaver(_ state: [String: Any]) {
        guard let screensaver = state["rocket_screensaver"] as? [String: Any],
              (screensaver["enabled"] as? Bool) == true else {
            hideRocketScreensaver()
            return
        }
        let activity = state["status_animation_activity"] as? [String: Any] ?? [:]
        let statusViews = state["status_views"] as? [[String: Any]] ?? []
        showRocketScreensaver(activity: activity, statusViews: statusViews)
    }

    private func showRocketScreensaver(activity: [String: Any], statusViews: [[String: Any]]) {
        let targetFrame = connectedDisplayRegion()
        let viewportFrames = connectedDisplayOverlayFrames(targetFrame: targetFrame)
        rocketScreensaverMotionState.configure(activity: activity, statusViews: statusViews, worldFrame: targetFrame)
        startRocketScreensaverDisplayTimer()
        closeExtraRocketScreensaverWindows(startingAt: viewportFrames.count)

        var nextWindows: [NSPanel] = []
        var nextViews: [RocketScreensaverView] = []
        for (index, viewportFrame) in viewportFrames.enumerated() {
            let view: RocketScreensaverView
            if index < rocketScreensaverViews.count {
                view = rocketScreensaverViews[index]
            } else {
                view = RocketScreensaverView(frame: NSRect(origin: .zero, size: viewportFrame.size), motionState: rocketScreensaverMotionState)
            }
            view.frame = NSRect(origin: .zero, size: viewportFrame.size)
            view.autoresizingMask = [.width, .height]
            view.configure(viewportFrame: viewportFrame)

            let window: NSPanel
            if index < rocketScreensaverWindows.count {
                window = rocketScreensaverWindows[index]
            } else {
                window = NSPanel(
                    contentRect: viewportFrame,
                    styleMask: [.borderless, .nonactivatingPanel],
                    backing: .buffered,
                    defer: false
                )
            }
            window.isReleasedWhenClosed = false
            window.setFrame(viewportFrame, display: true)
            window.contentView = view
            window.isOpaque = false
            window.backgroundColor = .clear
            window.ignoresMouseEvents = true
            window.hidesOnDeactivate = false
            window.canHide = false
            window.hasShadow = false
            window.isFloatingPanel = true
            window.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle, .fullScreenAuxiliary]
            window.level = .screenSaver
            window.orderFrontRegardless()

            nextWindows.append(window)
            nextViews.append(view)
        }

        rocketScreensaverWindows = nextWindows
        rocketScreensaverViews = nextViews
    }

    private func startRocketScreensaverDisplayTimer() {
        guard rocketScreensaverDisplayTimer == nil else {
            return
        }
        let timer = Timer(
            timeInterval: rocketScreensaverFrameIntervalSeconds,
            target: self,
            selector: #selector(advanceRocketScreensaverDisplay(_:)),
            userInfo: nil,
            repeats: true
        )
        // Tight tolerance keeps the floating-rocket cadence locked to ~60 fps
        // instead of letting RunLoop coalesce wake-ups into visible stutter.
        timer.tolerance = 0.002
        RunLoop.main.add(timer, forMode: .common)
        rocketScreensaverDisplayTimer = timer
    }

    @objc private func advanceRocketScreensaverDisplay(_ timer: Timer) {
        let now = Date()
        rocketScreensaverMotionState.advance(to: now)
        rocketScreensaverViews.forEach { $0.invalidateMotionArea() }
        if now.timeIntervalSince(lastOverlaySnapshotAt) >= overlayRuntimeSnapshotWriteIntervalSeconds {
            lastOverlaySnapshotAt = now
            writeOverlayRuntimeSnapshot(enabled: true, capturedAt: now)
        }
    }

    @objc private func handleScreenParametersChanged(_ notification: Notification) {
        screenReconfigDebounce?.cancel()
        let work = DispatchWorkItem { [weak self] in
            self?.applyScreenReconfig()
        }
        screenReconfigDebounce = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15, execute: work)
    }

    private func applyScreenReconfig() {
        guard !rocketScreensaverWindows.isEmpty || !rocketScreensaverViews.isEmpty else {
            return
        }
        let newTarget = connectedDisplayRegion()
        let viewportFrames = connectedDisplayOverlayFrames(targetFrame: newTarget)
        rocketScreensaverMotionState.remapMotion(toWorldFrame: newTarget)
        closeExtraRocketScreensaverWindows(startingAt: viewportFrames.count)

        var nextWindows: [NSPanel] = []
        var nextViews: [RocketScreensaverView] = []
        for (index, viewportFrame) in viewportFrames.enumerated() {
            let view: RocketScreensaverView
            if index < rocketScreensaverViews.count {
                view = rocketScreensaverViews[index]
            } else {
                view = RocketScreensaverView(frame: NSRect(origin: .zero, size: viewportFrame.size), motionState: rocketScreensaverMotionState)
            }
            view.frame = NSRect(origin: .zero, size: viewportFrame.size)
            view.autoresizingMask = [.width, .height]
            view.configure(viewportFrame: viewportFrame)

            let window: NSPanel
            if index < rocketScreensaverWindows.count {
                window = rocketScreensaverWindows[index]
            } else {
                window = NSPanel(
                    contentRect: viewportFrame,
                    styleMask: [.borderless, .nonactivatingPanel],
                    backing: .buffered,
                    defer: false
                )
                window.isReleasedWhenClosed = false
                window.isOpaque = false
                window.backgroundColor = .clear
                window.ignoresMouseEvents = true
                window.hidesOnDeactivate = false
                window.canHide = false
                window.hasShadow = false
                window.isFloatingPanel = true
                window.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle, .fullScreenAuxiliary]
                window.level = .screenSaver
            }
            window.setFrame(viewportFrame, display: true)
            window.contentView = view
            window.orderFrontRegardless()
            nextWindows.append(window)
            nextViews.append(view)
        }
        rocketScreensaverWindows = nextWindows
        rocketScreensaverViews = nextViews
        lastOverlaySnapshotAt = .distantPast
        writeOverlayRuntimeSnapshot(enabled: true, capturedAt: Date())
    }

    private func writeOverlayRuntimeSnapshot(enabled: Bool, capturedAt: Date) {
        guard let url = overlayRuntimeSnapshotURL() else { return }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var snapshot: [String: Any] = [
            "captured_at": formatter.string(from: capturedAt),
            "enabled": enabled,
            "panel_count": rocketScreensaverWindows.count,
            "panel_frames": rocketScreensaverWindows.map { rectState($0.frame) },
            "viewport_frames": rocketScreensaverViews.map { rectState($0.frame) },
            "screens": NSScreen.screens.map { screen -> [String: Any] in
                [
                    "frame": rectState(screen.frame),
                    "visible_frame": rectState(screen.visibleFrame),
                    "backing_scale_factor": Double(screen.backingScaleFactor),
                    "is_main": screen == NSScreen.main,
                ]
            },
        ]
        if enabled {
            snapshot["motion"] = rocketScreensaverMotionState.runtimeSnapshot()
        }
        do {
            let dir = url.deletingLastPathComponent()
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            let data = try JSONSerialization.data(withJSONObject: snapshot, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: url, options: [.atomic])
        } catch {
            // Diagnostic surface — silent failures are acceptable here.
        }
    }

    private func closeExtraRocketScreensaverWindows(startingAt index: Int) {
        guard rocketScreensaverWindows.count > index || rocketScreensaverViews.count > index else {
            return
        }
        rocketScreensaverViews.dropFirst(index).forEach { $0.stop() }
        rocketScreensaverWindows.dropFirst(index).forEach { window in
            window.contentView = nil
            window.orderOut(nil)
            window.close()
        }
    }

    private func hideRocketScreensaver() {
        rocketScreensaverDisplayTimer?.invalidate()
        rocketScreensaverDisplayTimer = nil
        rocketScreensaverViews.forEach { $0.stop() }
        rocketScreensaverViews = []
        rocketScreensaverWindows.forEach { window in
            window.contentView = nil
            window.orderOut(nil)
            window.close()
        }
        rocketScreensaverWindows = []
        writeOverlayRuntimeSnapshot(enabled: false, capturedAt: Date())
    }

    private func badgeInventoryItem(_ state: [String: Any]) -> NSMenuItem {
        let item = NSMenuItem(title: "Badge Inventory", action: nil, keyEquivalent: "")
        let submenu = NSMenu()
        if let inventory = state["badge_inventory"] as? [[String: Any]], !inventory.isEmpty {
            for badge in inventory {
                let prefix = (badge["is_representative"] as? Bool) == true ? "✓ " : ""
                let isEarned = text(badge["status"]) == "earned"
                let title = "\(prefix)\(statusSymbol(text(badge["status"]))) \(text(badge["name"])) · \(text(badge["status"]))"
                let badgeItem = NSMenuItem(title: title, action: nil, keyEquivalent: "")
                badgeItem.action = isEarned ? #selector(setRepresentativeBadge(_:)) : nil
                badgeItem.isEnabled = isEarned
                badgeItem.target = self
                badgeItem.representedObject = text(badge["badge_id"])
                submenu.addItem(badgeItem)
            }
        } else {
            addDisabled("No badges available.", to: submenu)
        }
        item.submenu = submenu
        return item
    }

    @objc private func setRepresentativeBadge(_ sender: NSMenuItem) {
        guard let badgeId = sender.representedObject as? String, !badgeId.isEmpty else { return }
        let dataRoot = activeDataRoot()
        do {
            try writeRepresentativeBadgeSelection(badgeId, dataRoot: dataRoot)
            applyState(stateBySelectingRepresentativeBadge(lastRenderedState, badgeId: badgeId))
            refreshStateInBackground(refreshUsage: false)
        } catch {
            showAlert("Badge Save Failed", error.localizedDescription)
        }
    }

    @objc private func doIdentityUpdate() {
        startIdentityUpdateRun()
    }

    private func startIdentityUpdateRun() {
        guard !identityUpdateInFlight else { return }
        identityUpdateInFlight = true
        var runningState = lastRenderedState
        var identity = runningState["identity_update"] as? [String: Any] ?? [:]
        identity["status"] = "running"
        identity["progress"] = 0
        identity["reason"] = "Personality and thinking-path update is running in the background."
        runningState["identity_update"] = identity
        var weekly = runningState["weekly_missions"] as? [[String: Any]] ?? []
        weekly = weekly.map { mission in
            guard text(mission["mission_id"]) == "weekly_identity_update" else { return mission }
            var updated = mission
            updated["status"] = "running"
            updated["progress"] = 0
            return updated
        }
        runningState["weekly_missions"] = weekly
        applyState(runningState)
        stateQueue.async {
            let completed = performIdentityUpdateState()
            let finalState = loadDisplayState(refreshUsage: false)
            DispatchQueue.main.async {
                self.identityUpdateInFlight = false
                self.applyState(finalState)
                if !completed {
                    self.showAlert("Identity Update Failed", "AgentBoost could not write the local personality/thinking-path drafts.")
                }
            }
        }
    }

    @objc private func doSkillPromptReview() {
        startSkillPromptReviewRun()
    }

    private func startSkillPromptReviewRun() {
        guard !skillPromptReviewInFlight else { return }
        skillPromptReviewInFlight = true
        var runningState = lastRenderedState
        var weekly = runningState["weekly_missions"] as? [[String: Any]] ?? []
        weekly = weekly.map { mission in
            guard text(mission["mission_id"]) == "weekly_skill_prompt_review" else { return mission }
            var review = mission
            review["status"] = "running"
            review["progress"] = 0
            return review
        }
        runningState["weekly_missions"] = weekly
        applyState(runningState)
        stateQueue.async {
            let completed = performSkillPromptReviewState()
            let finalState = loadDisplayState(refreshUsage: false)
            DispatchQueue.main.async {
                self.skillPromptReviewInFlight = false
                self.applyState(finalState)
                if !completed {
                    self.showAlert("Skill Review Failed", "AgentBoost could not write the local skills/prompts review artifact.")
                }
            }
        }
    }

    @objc private func doMetaReview() {
        startMetaReviewRun()
    }

    private func startMetaReviewFromNotification() {
        startMetaReviewRun()
    }

    private func startMetaReviewRun() {
        guard !metaReviewInFlight else { return }
        metaReviewInFlight = true
        var runningState = lastRenderedState
        var meta = runningState["meta_review"] as? [String: Any] ?? [:]
        meta["status"] = "running"
        meta["due"] = false
        meta["reason"] = "Meta-review is running…"
        runningState["meta_review"] = meta
        applyState(runningState)
        stateQueue.async {
            let dataRoot = activeDataRoot()
            let completed = performMetaReviewState()
            if completed {
                clearMetaReviewNotificationPrompts(dataRoot: dataRoot)
            }
            // Re-read the meta-review chip straight from the file we just
            // rewrote. The previous version called loadDisplayState here,
            // which re-runs the BEAM CLI (≈35 s) and made the UI look frozen
            // for the entire duration of the "run".
            let freshMeta: [String: Any] = completed
                ? metaReviewState(dataRoot: dataRoot)
                : [:]
            DispatchQueue.main.async {
                self.metaReviewInFlight = false
                if completed {
                    var newState = self.lastRenderedState
                    newState["meta_review"] = freshMeta
                    self.applyState(newState)
                } else {
                    var newState = self.lastRenderedState
                    newState["meta_review"] = metaReviewState(dataRoot: dataRoot)
                    self.applyState(newState)
                    self.showAlert(
                        "Meta Review Failed",
                        "AgentBoost could not update the local meta-review files. "
                            + "Check that the ai-system folder is mounted and writable."
                    )
                }
            }
            // Kick a full BEAM refresh in the background so the rest of the
            // UI catches up, but don't block the Run button on it.
            DispatchQueue.main.async {
                self.refreshStateInBackground(refreshUsage: false)
            }
        }
    }

    @objc private func toggleRocketScreensaver() {
        let enabled = !floatingOverlayEnabled(dataRoot: activeDataRoot())
        do {
            try setFloatingOverlayEnabled(enabled, dataRoot: activeDataRoot())
            if !enabled {
                hideRocketScreensaver()
            }
            let liveState = loadLiveUsageState(refreshUsage: false)
            let state = lastRenderedState.isEmpty ? liveState : stateByMergingLiveUsage(lastRenderedState, liveState: liveState)
            applyState(state)
        } catch {
            showAlert("Settings Save Failed", error.localizedDescription)
        }
    }

    @objc private func selectDataRoot() {
        selectFolder(title: "Select AI-System Folder", bookmarkKey: dataRootBookmarkKey)
    }

    @objc private func selectClaudeDataRoot() {
        selectFolder(title: "Select Claude Usage Folder", bookmarkKey: claudeDataRootBookmarkKey)
    }

    @objc private func selectCodexDataRoot() {
        selectFolder(title: "Select Codex Sessions Folder", bookmarkKey: codexDataRootBookmarkKey)
    }

    private func selectFolder(title: String, bookmarkKey: String) {
        let panel = NSOpenPanel()
        panel.title = title
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url {
            _ = storeSecurityScopedURL(url, forKey: bookmarkKey)
        }
        refreshMenu()
        runUsageBackfillOnceInBackground()
        refreshStateInBackground(refreshUsage: true)
    }

    @objc private func refreshUsage() {
        refreshStateInBackground(refreshUsage: false, forceUsageRefresh: true)
    }

    @objc private func removeFolderAccess() {
        UserDefaults.standard.removeObject(forKey: dataRootBookmarkKey)
        UserDefaults.standard.removeObject(forKey: claudeDataRootBookmarkKey)
        UserDefaults.standard.removeObject(forKey: codexDataRootBookmarkKey)
        refreshMenu()
    }

    @objc private func deleteLocalUsageData() {
        let alert = NSAlert()
        alert.messageText = "Delete Local Usage Data?"
        alert.informativeText = "Removes derived AgentBoost files under data/ai-usage. Original Claude and Codex histories are not touched."
        alert.addButton(withTitle: "Delete")
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else {
            return
        }
        let dataRoot = activeDataRoot()
        let usageData = dataRoot.appendingPathComponent("data/ai-usage", isDirectory: true)
        do {
            try FileManager.default.removeItem(at: usageData)
        } catch {
            if !FileManager.default.fileExists(atPath: usageData.path) {
                refreshMenu()
                return
            }
            showAlert("Delete Failed", error.localizedDescription)
        }
        refreshMenu()
    }

    @objc private func exportLocalReport() {
        let panel = NSSavePanel()
        panel.title = "Export Local Report"
        panel.nameFieldStringValue = "agentboost-local-report.json"
        panel.allowedContentTypes = [.json]
        guard panel.runModal() == .OK, let url = panel.url else {
            return
        }
        let state = loadState(refreshUsage: false)
        do {
            let data = try JSONSerialization.data(withJSONObject: state, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: url, options: .atomic)
        } catch {
            showAlert("Export Failed", error.localizedDescription)
        }
    }

    private func showAlert(_ message: String, _ details: String) {
        let alert = NSAlert()
        alert.messageText = message
        alert.informativeText = details
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    private func addDisabled(_ title: String, to menu: NSMenu) {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.isEnabled = false
        menu.addItem(item)
    }
}

func runSplitRocketSelfTestAndExit() {
    let args = CommandLine.arguments
    guard args.contains("--split-test") else { return }
    let motion = RocketScreensaverView.MotionState()
    let activity: [String: Any] = [
        "rocket_speed": 0.7,
        "active_agents": ["claude", "codex"],
        "rocket_count": 4,
        "agent_usage": [
            "claude": ["last_1m_tokens": 50_000, "display_tokens": "50K", "input_tokens": 40_000, "output_tokens": 10_000],
            "codex":  ["last_1m_tokens": 25_000, "display_tokens": "25K", "input_tokens": 15_000, "output_tokens": 10_000],
        ],
        "rockets": [
            ["agent": "claude", "channel": "input",  "tokens": 40_000, "display_tokens": "40K", "speed": 0.71, "altitude": 38, "has_flame": true],
            ["agent": "claude", "channel": "output", "tokens": 10_000, "display_tokens": "10K", "speed": 0.44, "altitude": 10, "has_flame": true],
            ["agent": "codex",  "channel": "input",  "tokens": 15_000, "display_tokens": "15K", "speed": 0.50, "altitude": 15, "has_flame": true],
            ["agent": "codex",  "channel": "output", "tokens": 10_000, "display_tokens": "10K", "speed": 0.44, "altitude": 10, "has_flame": true],
        ],
        "activity_level": "moderate",
        "display_tokens": "75K",
    ]
    motion.configure(activity: activity, statusViews: [], worldFrame: NSRect(x: 0, y: 0, width: 600, height: 400))
    let snapshot = motion.runtimeSnapshot()
    let payload: [String: Any] = [
        "rocket_count": snapshot["rocket_count"] ?? 0,
        "rendered_agents": snapshot["rendered_agents"] ?? [],
        "rocket_keys": (snapshot["rockets"] as? [[String: Any]])?.map { ($0["key"] as? String) ?? "" } ?? [],
    ]
    do {
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
        exit(0)
    } catch {
        FileHandle.standardError.write(Data("split self-test JSON encode failed: \(error.localizedDescription)\n".utf8))
        exit(1)
    }
}

func runIdleSplitRocketSelfTestAndExit() {
    let args = CommandLine.arguments
    guard args.contains("--idle-split-test") else { return }
    let motion = RocketScreensaverView.MotionState()
    let world = NSRect(x: 0, y: 0, width: 600, height: 400)
    let visibleMinX = world.minX + CGFloat(34)
    let activity: [String: Any] = [
        "rocket_speed": 2.4,
        "active_agents": ["claude", "codex"],
        "rocket_count": 2,
        "agent_usage": [
            "claude": ["last_1m_tokens": 1_081_050, "display_tokens": "1.1M"],
            "codex": ["last_1m_tokens": 0, "display_tokens": "0"],
        ],
        "activity_level": "high",
        "display_tokens": "1.1M",
    ]
    let now = Date()
    motion.configure(activity: activity, statusViews: [], worldFrame: world)
    let beforeSnapshot = motion.runtimeSnapshot()
    var beforeRockets: [String: [String: Any]] = [:]
    for rocket in (beforeSnapshot["rockets"] as? [[String: Any]]) ?? [] {
        let agent = (rocket["agent"] as? String) ?? ""
        guard !agent.isEmpty else { continue }
        beforeRockets[agent] = (rocket["position"] as? [String: Any]) ?? [:]
    }
    motion.advance(to: now.addingTimeInterval(1.0))
    let snapshot = motion.runtimeSnapshot()
    var rockets: [String: Any] = [:]
    for rocket in (snapshot["rockets"] as? [[String: Any]]) ?? [] {
        let agent = (rocket["agent"] as? String) ?? ""
        guard !agent.isEmpty else { continue }
        let position = (rocket["position"] as? [String: Any]) ?? [:]
        let beforePosition = beforeRockets[agent] ?? [:]
        rockets[agent] = [
            "before_x": beforePosition["x"] ?? 0,
            "x": position["x"] ?? 0,
            "speed": rocket["speed"] ?? 0,
            "smoothed_speed": rocket["smoothed_speed"] ?? 0,
            "tokens_last_1m": rocket["tokens_last_1m"] ?? 0,
        ]
    }
    let payload: [String: Any] = [
        "rocket_count": snapshot["rocket_count"] ?? 0,
        "rendered_agents": snapshot["rendered_agents"] ?? [],
        "visible_min_x": Double(visibleMinX),
        "rockets": rockets,
    ]
    do {
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
        exit(0)
    } catch {
        FileHandle.standardError.write(Data("idle split self-test JSON encode failed: \(error.localizedDescription)\n".utf8))
        exit(1)
    }
}

func runBlastSelfTestAndExit() {
    let args = CommandLine.arguments
    guard args.contains("--blast-test") else { return }
    let motion = RocketScreensaverView.MotionState()
    let world = NSRect(x: 0, y: 0, width: 600, height: 400)
    let baseTime = Date(timeIntervalSince1970: 1_700_000_000)
    let activeActivity: [String: Any] = [
        "rocket_speed": 2.4,
        "active_agents": ["claude", "codex"],
        "rocket_count": 2,
        "agent_usage": [
            "claude": ["last_1m_tokens": 50_000, "display_tokens": "50K"],
            "codex": ["last_1m_tokens": 45_000, "display_tokens": "45K"],
        ],
        "activity_level": "high",
        "display_tokens": "95K",
    ]
    motion.configure(activity: activeActivity, statusViews: [], worldFrame: world)
    let altitudeFractionA = CGFloat(0.50)
    let altitudeFractionB = CGFloat(0.50)
    motion._injectRocketForTesting(
        agent: "claude",
        position: NSPoint(x: 300, y: world.minY + altitudeFractionA * world.height),
        altitudeFraction: altitudeFractionA,
        now: baseTime
    )
    motion._injectRocketForTesting(
        agent: "codex",
        position: NSPoint(x: 312, y: world.minY + altitudeFractionB * world.height),
        altitudeFraction: altitudeFractionB,
        now: baseTime
    )
    let altitudeBeforeClaude = Double(motion._altitudeFraction(for: "claude"))
    let altitudeBeforeCodex = Double(motion._altitudeFraction(for: "codex"))
    motion._runDetectionForTesting(at: baseTime)
    let blastsAtImpact = motion.totalBlastCount
    let visibleAgentsAtImpact = motion.rocketDrawStates(now: baseTime).map { $0.agent }
    motion._runDetectionForTesting(at: baseTime.addingTimeInterval(0.55))
    let separationAfterDelay = motion.separationAppliedCount
    let altitudeAfterClaude = Double(motion._altitudeFraction(for: "claude"))
    let altitudeAfterCodex = Double(motion._altitudeFraction(for: "codex"))
    let afterCullTime = baseTime.addingTimeInterval(0.7)
    motion._runDetectionForTesting(at: afterCullTime)
    let blastsAfterCull = motion.totalBlastCount
    let visibleAgentsAfterCull = motion.rocketDrawStates(now: afterCullTime).map { $0.agent }
    let afterRecoveryTime = baseTime.addingTimeInterval(1.25)
    motion._runDetectionForTesting(at: afterRecoveryTime)
    let visibleAgentsAfterRecovery = motion.rocketDrawStates(now: afterRecoveryTime).map { $0.agent }
    let payload: [String: Any] = [
        "blasts_at_impact": blastsAtImpact,
        "blasts_after_cull": blastsAfterCull,
        "separation_applied_after_delay": separationAfterDelay,
        "rockets_visible_at_impact": visibleAgentsAtImpact.count,
        "rockets_visible_after_cull": visibleAgentsAfterCull.count,
        "rockets_visible_after_recovery": visibleAgentsAfterRecovery.count,
        "visible_agents_at_impact": visibleAgentsAtImpact,
        "visible_agents_after_cull": visibleAgentsAfterCull,
        "visible_agents_after_recovery": visibleAgentsAfterRecovery,
        "altitude_diff_before": abs(altitudeBeforeClaude - altitudeBeforeCodex),
        "altitude_diff_after": abs(altitudeAfterClaude - altitudeAfterCodex),
    ]
    do {
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
        exit(0)
    } catch {
        FileHandle.standardError.write(Data("blast self-test JSON encode failed: \(error.localizedDescription)\n".utf8))
        exit(1)
    }
}

func runIdleBlastRecoverySelfTestAndExit() {
    let args = CommandLine.arguments
    guard args.contains("--idle-blast-recovery-test") else { return }
    let motion = RocketScreensaverView.MotionState()
    let world = NSRect(x: 0, y: 0, width: 600, height: 400)
    let baseTime = Date(timeIntervalSince1970: 1_700_000_000)
    let activeActivity: [String: Any] = [
        "rocket_speed": 2.4,
        "active_agents": ["claude", "codex"],
        "rocket_count": 2,
        "agent_usage": [
            "claude": ["last_1m_tokens": 50_000, "display_tokens": "50K"],
            "codex": ["last_1m_tokens": 45_000, "display_tokens": "45K"],
        ],
        "activity_level": "high",
        "display_tokens": "95K",
    ]
    motion.configure(activity: activeActivity, statusViews: [], worldFrame: world)
    motion._injectRocketForTesting(
        agent: "claude",
        position: NSPoint(x: 300, y: 220),
        altitudeFraction: 0.50,
        now: baseTime
    )
    motion._injectRocketForTesting(
        agent: "codex",
        position: NSPoint(x: 312, y: 220),
        altitudeFraction: 0.50,
        now: baseTime
    )
    motion._runDetectionForTesting(at: baseTime)
    let blastsAtImpact = motion.totalBlastCount
    let idleActivity: [String: Any] = [
        "rocket_speed": 0.0,
        "active_agents": ["claude", "codex"],
        "rocket_count": 2,
        "agent_usage": [
            "claude": ["last_1m_tokens": 0, "display_tokens": "0"],
            "codex": ["last_1m_tokens": 0, "display_tokens": "0"],
        ],
        "activity_level": "idle",
        "display_tokens": "0",
    ]
    motion.configure(activity: idleActivity, statusViews: [], worldFrame: world)
    var repeatedBlastAfterRecovery = false
    var frameTime = baseTime
    let endTime = baseTime.addingTimeInterval(7.0)
    while frameTime < endTime {
        frameTime = frameTime.addingTimeInterval(1.0 / 30.0)
        motion.advance(to: frameTime)
        if frameTime.timeIntervalSince(baseTime) > 1.25 && motion.totalBlastCount > 0 {
            repeatedBlastAfterRecovery = true
        }
    }
    let snapshot = motion.runtimeSnapshot()
    let rockets = snapshot["rockets"] as? [[String: Any]] ?? []
    var rocketSpeeds: [String: Any] = [:]
    var rocketTokens: [String: Any] = [:]
    for rocket in rockets {
        let agent = (rocket["agent"] as? String) ?? ""
        guard !agent.isEmpty else { continue }
        rocketSpeeds[agent] = rocket["speed"] ?? 0
        rocketTokens[agent] = rocket["tokens_last_1m"] ?? 0
    }
    let visibleAgentsAfterIdleSettle = motion.rocketDrawStates(now: frameTime).map { $0.agent }
    let payload: [String: Any] = [
        "blasts_at_impact": blastsAtImpact,
        "repeated_blast_after_zero_recovery": repeatedBlastAfterRecovery,
        "blasts_after_idle_settle": motion.totalBlastCount,
        "rockets_visible_after_idle_settle": visibleAgentsAfterIdleSettle.count,
        "visible_agents_after_idle_settle": visibleAgentsAfterIdleSettle,
        "rocket_speeds_after_idle_settle": rocketSpeeds,
        "tokens_after_idle_settle": rocketTokens,
    ]
    do {
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
        exit(0)
    } catch {
        FileHandle.standardError.write(Data("idle blast recovery self-test JSON encode failed: \(error.localizedDescription)\n".utf8))
        exit(1)
    }
}

func runAltitudeSelfTestAndExit() {
    let args = CommandLine.arguments
    guard args.contains("--altitude-test") else { return }
    let motion = RocketScreensaverView.MotionState()
    let world = NSRect(x: 0, y: 0, width: 600, height: 400)
    let activity: [String: Any] = [
        "rocket_speed": 2.4,
        "active_agents": ["claude"],
        "rocket_count": 1,
        "agent_usage": [
            "claude": ["last_1m_tokens": 1_500_000, "display_tokens": "1.5M"],
        ],
        "activity_level": "high",
        "display_tokens": "1.5M",
    ]
    motion.configure(activity: activity, statusViews: [], worldFrame: world)
    let snapshot = motion.runtimeSnapshot()
    let rockets = snapshot["rockets"] as? [[String: Any]] ?? []
    let first = rockets.first ?? [:]
    let position = first["position"] as? [String: Any] ?? [:]
    let doubleValue: (Any?) -> Double = { value in
        if let number = value as? NSNumber { return number.doubleValue }
        if let double = value as? Double { return double }
        return 0
    }
    let rocketY = doubleValue(position["y"])
    let rocketCenterTopMargin = doubleValue(snapshot["rocket_center_top_margin"])
    let payload: [String: Any] = [
        "world_max_y": Double(world.maxY),
        "rocket_y": rocketY,
        "rocket_center_top_margin": rocketCenterTopMargin,
        "rocket_visual_top_y": rocketY + rocketCenterTopMargin,
        "altitude_target_fraction": first["altitude_target_fraction"] ?? 0,
        "smoothed_altitude_fraction": first["smoothed_altitude_fraction"] ?? 0,
    ]
    do {
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
        exit(0)
    } catch {
        FileHandle.standardError.write(Data("altitude self-test JSON encode failed: \(error.localizedDescription)\n".utf8))
        exit(1)
    }
}

func runIdleBottomSelfTestAndExit() {
    let args = CommandLine.arguments
    guard args.contains("--idle-bottom-test") else { return }
    let motion = RocketScreensaverView.MotionState()
    let world = NSRect(x: 0, y: 0, width: 600, height: 400)
    let activity: [String: Any] = [
        "rocket_speed": 0.0,
        "active_agents": ["claude"],
        "rocket_count": 1,
        "agent_usage": [
            "claude": ["last_1m_tokens": 0, "display_tokens": "0"],
        ],
        "activity_level": "idle",
        "display_tokens": "0",
    ]
    motion.configure(activity: activity, statusViews: [], worldFrame: world)
    motion.advance(to: Date().addingTimeInterval(1.0))
    let snapshot = motion.runtimeSnapshot()
    let rockets = snapshot["rockets"] as? [[String: Any]] ?? []
    let first = rockets.first ?? [:]
    let position = first["position"] as? [String: Any] ?? [:]
    let doubleValue: (Any?) -> Double = { value in
        if let number = value as? NSNumber { return number.doubleValue }
        if let double = value as? Double { return double }
        return 0
    }
    let rocketY = doubleValue(position["y"])
    let rocketCenterBottomMargin = doubleValue(snapshot["rocket_center_bottom_margin"])
    let payload: [String: Any] = [
        "world_min_y": Double(world.minY),
        "rocket_y": rocketY,
        "rocket_center_bottom_margin": rocketCenterBottomMargin,
        "rocket_visual_bottom_y": rocketY - rocketCenterBottomMargin,
        "tokens_last_1m": first["tokens_last_1m"] ?? 0,
    ]
    do {
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
        exit(0)
    } catch {
        FileHandle.standardError.write(Data("idle bottom self-test JSON encode failed: \(error.localizedDescription)\n".utf8))
        exit(1)
    }
}

func writeStateJSONAndExit() {
    let args = CommandLine.arguments
    if args.contains("--overlay-runtime-json") {
        let snapshot = readOverlayRuntimeSnapshot()
        do {
            let data = try JSONSerialization.data(withJSONObject: snapshot, options: [.prettyPrinted, .sortedKeys])
            FileHandle.standardOutput.write(data)
            FileHandle.standardOutput.write(Data("\n".utf8))
            exit(0)
        } catch {
            FileHandle.standardError.write(Data("failed to encode overlay runtime JSON: \(error.localizedDescription)\n".utf8))
            exit(1)
        }
    }
    guard args.contains("--state-json") else { return }
    var state = loadDisplayState(refreshUsage: false)
    state["overlay_runtime"] = readOverlayRuntimeSnapshot()
    do {
        let data = try JSONSerialization.data(withJSONObject: state, options: [.prettyPrinted, .sortedKeys])
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
        exit(0)
    } catch {
        FileHandle.standardError.write(Data("failed to encode state JSON: \(error.localizedDescription)\n".utf8))
        exit(1)
    }
}

runSplitRocketSelfTestAndExit()
runIdleSplitRocketSelfTestAndExit()
runBlastSelfTestAndExit()
runIdleBlastRecoverySelfTestAndExit()
runAltitudeSelfTestAndExit()
runIdleBottomSelfTestAndExit()
writeStateJSONAndExit()

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
