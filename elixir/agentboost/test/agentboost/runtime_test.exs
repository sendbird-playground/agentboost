defmodule Agentboost.RuntimeTest do
  use ExUnit.Case, async: true

  test "state exposes the product quality contract and local controls" do
    state = Agentboost.Runtime.state()

    assert state["contract"] == "agentboost_state_v1"
    assert state["runtime"] == "elixir_beam"
    assert state["memory_monitor"]["threshold_percent"] == 80
    assert "Refresh Usage" in state["privacy_controls"]
    assert "Remove Folder Access" in state["privacy_controls"]
    assert "Delete Local Usage Data" in state["privacy_controls"]
    assert "Export Local Report" in state["privacy_controls"]
  end

  test "state summarizes local token metadata without external dependencies" do
    tmp = Path.join(System.tmp_dir!(), "agentboost-runtime-#{System.unique_integer([:positive])}")
    on_exit(fn -> File.rm_rf(tmp) end)
    events = Path.join([tmp, "data", "ai-usage", "events.jsonl"])
    File.mkdir_p!(Path.dirname(events))

    File.write!(events, """
    {"source":"claude","input_tokens":10,"output_tokens":3,"reasoning_output_tokens":1}
    {"source":"codex","input_tokens":7,"output_tokens":5}
    """)

    state = Agentboost.Runtime.state(data_root: tmp)

    assert state["token_activity"]["total_tokens"] == 26
    assert state["recent_token_activity"]["active_agents"] == ["claude", "codex"]
    assert state["status_animation_activity"]["rocket_count"] == 2
    assert state["recent_token_activity"]["split_io_enabled"] == false
    refute Map.has_key?(state["recent_token_activity"], "rockets")
  end

  test "split_io_rockets setting produces a 4-rocket activity payload" do
    tmp =
      Path.join(
        System.tmp_dir!(),
        "agentboost-runtime-splitio-#{System.unique_integer([:positive])}"
      )

    on_exit(fn -> File.rm_rf(tmp) end)
    usage_dir = Path.join([tmp, "data", "ai-usage"])
    File.mkdir_p!(usage_dir)
    events_path = Path.join(usage_dir, "events.jsonl")
    settings_path = Path.join(usage_dir, "settings.json")

    now = DateTime.utc_now() |> DateTime.truncate(:second) |> DateTime.to_iso8601()

    File.write!(events_path, """
    {"source_agent":"claude","occurred_at":"#{now}","input_tokens":40000,"output_tokens":10000,"total_tokens":50000}
    {"source_agent":"codex","occurred_at":"#{now}","input_tokens":5000,"output_tokens":3000,"reasoning_output_tokens":2000,"total_tokens":10000}
    """)

    File.write!(settings_path, ~s({"display":{"split_io_rockets":true}}))

    state = Agentboost.Runtime.state(data_root: tmp)
    recent = state["recent_token_activity"]

    assert recent["split_io_enabled"] == true
    assert recent["rocket_count"] == 4
    assert is_list(recent["rockets"])
    assert length(recent["rockets"]) == 4

    keyed = Map.new(recent["rockets"], &{{&1["agent"], &1["channel"]}, &1})
    assert keyed[{"claude", "input"}]["tokens"] == 40_000
    assert keyed[{"claude", "output"}]["tokens"] == 10_000
    assert keyed[{"codex", "input"}]["tokens"] == 5_000
    assert keyed[{"codex", "output"}]["tokens"] == 5_000
    assert keyed[{"claude", "input"}]["has_flame"] == true
    assert keyed[{"claude", "input"}]["speed"] > 0
    assert recent["agent_usage"]["claude"]["input_tokens"] == 40_000
    assert recent["agent_usage"]["codex"]["output_tokens"] == 5_000
  end

  test "status animation preserves visible agents but stays stopped when last minute usage is zero" do
    tmp =
      Path.join(
        System.tmp_dir!(),
        "agentboost-runtime-idle-animation-#{System.unique_integer([:positive])}"
      )

    on_exit(fn -> File.rm_rf(tmp) end)
    events_path = Path.join([tmp, "data", "ai-usage", "events.jsonl"])
    File.mkdir_p!(Path.dirname(events_path))

    old_today =
      DateTime.utc_now()
      |> DateTime.add(-120, :second)
      |> DateTime.truncate(:second)
      |> DateTime.to_iso8601()

    File.write!(events_path, """
    {"source_agent":"claude","occurred_at":"#{old_today}","total_tokens":1200000}
    {"source_agent":"codex","occurred_at":"#{old_today}","total_tokens":800000}
    """)

    state = Agentboost.Runtime.state(data_root: tmp)
    animation = state["status_animation_activity"]

    assert state["recent_token_activity"]["last_1m_tokens"] == 0
    assert animation["source"] == "today"
    assert animation["active_agents"] == ["claude", "codex"]
    assert animation["rocket_count"] == 2
    assert animation["rocket_state"] == "waiting"
    assert animation["rocket_speed"] == 0.0
    assert animation["rocket_altitude"] == 0
    assert animation["has_flame"] == false
    assert animation["speed_source"] == "token_usage"
  end

  test "state keeps raw token totals while display fields use compact decimal units" do
    tmp =
      Path.join(System.tmp_dir!(), "agentboost-runtime-format-#{System.unique_integer([:positive])}")

    on_exit(fn -> File.rm_rf(tmp) end)
    events = Path.join([tmp, "data", "ai-usage", "events.jsonl"])
    File.mkdir_p!(Path.dirname(events))
    now = DateTime.utc_now() |> DateTime.truncate(:second) |> DateTime.to_iso8601()

    File.write!(events, """
    {"source_agent":"claude","occurred_at":"#{now}","total_tokens":1500000000}
    """)

    state = Agentboost.Runtime.state(data_root: tmp)
    total_view = Enum.find(state["status_views"], &(&1["view_id"] == "total_cumulative"))
    minute_view = Enum.find(state["status_views"], &(&1["view_id"] == "token_per_minute"))

    assert state["rollups"]["Lifetime"]["total_tokens"] == 1_500_000_000
    assert state["recent_token_activity"]["display_tokens"] == "1.5B"
    assert total_view["display_tokens"] == "1.5B"
    assert minute_view["display_tokens"] == "1.5B/min"
  end

  test "rollups and daily chart group usage by local day" do
    tmp =
      Path.join(
        System.tmp_dir!(),
        "agentboost-runtime-local-day-#{System.unique_integer([:positive])}"
      )

    on_exit(fn -> File.rm_rf(tmp) end)
    events = Path.join([tmp, "data", "ai-usage", "events.jsonl"])
    File.mkdir_p!(Path.dirname(events))
    occurred_at = local_today_at!(~T[00:30:00]) |> DateTime.to_iso8601()

    File.write!(events, """
    {"source_agent":"codex","occurred_at":"#{occurred_at}","total_tokens":123}
    """)

    state = Agentboost.Runtime.state(data_root: tmp)
    today_bucket = List.last(state["agentboost_daily_7d"])

    assert state["rollups"]["Today"]["by_agent"]["codex"] == 123
    assert state["rollups"]["Today"]["total_tokens"] == 123
    assert today_bucket["codex"] == 123
  end

  test "missions expose frequency based auto checked progress fields" do
    tmp =
      Path.join(
        System.tmp_dir!(),
        "agentboost-runtime-missions-#{System.unique_integer([:positive])}"
      )

    on_exit(fn -> File.rm_rf(tmp) end)
    events = Path.join([tmp, "data", "ai-usage", "events.jsonl"])
    File.mkdir_p!(Path.dirname(events))
    now = DateTime.utc_now() |> DateTime.truncate(:second) |> DateTime.to_iso8601()

    File.write!(events, """
    {"source_agent":"codex","occurred_at":"#{now}","total_tokens":12}
    """)

    state = Agentboost.Runtime.state(data_root: tmp)
    daily = hd(state["daily_missions"])
    weekly = hd(state["weekly_missions"])

    assert daily["mission_id"] == "daily_ai_turn"
    assert daily["cadence"] == "daily"
    assert daily["frequency"] == "1/day"
    assert daily["goal"] == 1
    assert daily["progress"] == 1
    assert daily["status"] == "done"
    assert daily["auto_check"] == true
    assert daily["check_cost"] == "loaded_events_only"

    assert weekly["mission_id"] == "weekly_ai_streak"
    assert weekly["cadence"] == "weekly"
    assert weekly["frequency"] == "4/week"
    assert weekly["metric"] == "active_workdays"
    assert weekly["goal"] == 4
    assert weekly["progress"] >= 1
    assert weekly["auto_check"] == true
    refute Map.has_key?(weekly, "difficulty")

    review =
      Enum.find(state["weekly_missions"], &(&1["mission_id"] == "weekly_skill_prompt_review"))

    assert review["title"] == "Review current skills and prompts"
    assert review["check_cost"] == "local_artifact_scan"

    identity =
      Enum.find(state["weekly_missions"], &(&1["mission_id"] == "weekly_identity_update"))

    assert identity["title"] == "Update personality and thinking path"
    assert identity["command_hint"] == "bin/agentboost --do-identity-update"
    assert identity["metric"] == "identity_update_this_week"
    assert identity["check_cost"] == "local_artifact_scan"
    assert state["identity_update"]["status"] == "active"
  end

  test "identity update state and mission are measured from local draft artifacts" do
    tmp =
      Path.join(
        System.tmp_dir!(),
        "agentboost-runtime-identity-#{System.unique_integer([:positive])}"
      )

    on_exit(fn -> File.rm_rf(tmp) end)
    today = Date.utc_today() |> Date.to_iso8601()

    summary =
      Path.join([tmp, "identity", "drafts", "identity-update-#{today}-agentboost", "summary.md"])

    File.mkdir_p!(Path.dirname(summary))

    File.write!(summary, """
    # AgentBoost Identity Update
    - Evidence items: 7
    - Source files: 3
    """)

    state = Agentboost.Runtime.state(data_root: tmp)
    identity = state["identity_update"]
    mission = Enum.find(state["weekly_missions"], &(&1["mission_id"] == "weekly_identity_update"))

    assert identity["status"] == "done"
    assert identity["progress"] == 1
    assert identity["evidence_items"] == 7
    assert identity["source_file_count"] == 3
    assert identity["review_artifact"] == summary
    assert identity["updated_at"] == today
    assert mission["status"] == "done"
    assert mission["progress"] == 1
    assert Agentboost.JSON.encode!(state) =~ "\"identity_update\""
  end

  test "level progress follows xp table and includes done mission xp" do
    tmp =
      Path.join(System.tmp_dir!(), "agentboost-runtime-levels-#{System.unique_integer([:positive])}")

    on_exit(fn -> File.rm_rf(tmp) end)
    events = Path.join([tmp, "data", "ai-usage", "events.jsonl"])
    goals = Path.join([tmp, "data", "ai-usage", "goals.json"])
    File.mkdir_p!(Path.dirname(events))
    now = DateTime.utc_now() |> DateTime.truncate(:second) |> DateTime.to_iso8601()

    File.write!(events, """
    {"source_agent":"codex","occurred_at":"#{now}","total_tokens":1000000}
    """)

    File.write!(goals, """
    [{"status":"completed"}]
    """)

    state = Agentboost.Runtime.state(data_root: tmp)

    assert state["xp"] == 106
    assert state["level"] == 4
    assert state["level_label"] == "Lv 4"
    assert state["xp_breakdown"]["base_xp"] == 101
    assert state["xp_breakdown"]["mission_xp"] == 5
    assert state["level_progress"]["current_level"] == 4
    assert state["level_progress"]["current_level_xp"] == 0
    assert state["level_progress"]["current_level_required_xp"] == 92
    assert state["level_progress"]["xp_to_next_level"] == 92
  end

  test "representative badges are selected from ordered notification ledger ids and earned badges are explicit" do
    tmp =
      Path.join(System.tmp_dir!(), "agentboost-runtime-badges-#{System.unique_integer([:positive])}")

    on_exit(fn -> File.rm_rf(tmp) end)
    usage = Path.join([tmp, "data", "ai-usage"])
    events = Path.join(usage, "events.jsonl")
    notifications = Path.join(usage, "sidebar-notifications.json")
    File.mkdir_p!(usage)
    now = DateTime.utc_now() |> DateTime.truncate(:second) |> DateTime.to_iso8601()

    File.write!(events, """
    {"source_agent":"claude","occurred_at":"#{now}","total_tokens":1100000000}
    {"source_agent":"codex","occurred_at":"#{now}","total_tokens":1200000000}
    """)

    File.write!(notifications, """
    {"version":1,"representative_badge_id":"b0aec0de4cd56059","representative_badge_ids":["b0aec0de4cd56059","a758dd50b1415e27"],"badges":[]}
    """)

    state = Agentboost.Runtime.state(data_root: tmp)

    assert state["representative_badge"]["badge_id"] == "b0aec0de4cd56059"

    assert Enum.map(state["representative_badges"], & &1["badge_id"]) == [
             "b0aec0de4cd56059"
           ]

    assert Enum.map(state["earned_badges"], & &1["badge_id"]) == [
             "a758dd50b1415e27",
             "b0aec0de4cd56059"
           ]

    billion = Enum.find(state["earned_badges"], &(&1["badge_id"] == "a758dd50b1415e27"))
    assert billion["name"] == "Billion Club"

    assert billion["endorsement_text"] ==
             "Uses AI agents as daily working partners, not occasional search boxes."

    two_key_agents = Enum.find(state["earned_badges"], &(&1["badge_id"] == "b0aec0de4cd56059"))
    assert two_key_agents["name"] == "Two key agents"

    assert two_key_agents["endorsement_text"] ==
             "Token usage for Claude and Codex each reaches over 1B."

    heavy = Enum.find(state["badge_inventory"], &(&1["badge_id"] == "8f5a9291c21f44bf"))
    assert heavy["name"] == "Heavy user"
    assert heavy["status"] == "in_progress"
    assert heavy["progress_percent"] == 23
    assert heavy["can_select"] == false
    assert heavy["endorsement_text"] == "Weekly Claude and Codex token usage reaches 10B total."

    selected = Enum.filter(state["badge_inventory"], & &1["is_representative"])

    assert Enum.map(selected, &{&1["badge_id"], &1["representative_rank"]}) == [
             {"b0aec0de4cd56059", 1}
           ]
  end

  test "heavy user achievement is earned by weekly ten billion Claude and Codex usage" do
    tmp =
      Path.join(System.tmp_dir!(), "agentboost-runtime-heavy-#{System.unique_integer([:positive])}")

    on_exit(fn -> File.rm_rf(tmp) end)
    events = Path.join([tmp, "data", "ai-usage", "events.jsonl"])
    File.mkdir_p!(Path.dirname(events))
    now = DateTime.utc_now() |> DateTime.truncate(:second) |> DateTime.to_iso8601()

    File.write!(events, """
    {"source_agent":"claude","occurred_at":"#{now}","total_tokens":6000000000}
    {"source_agent":"codex","occurred_at":"#{now}","total_tokens":4000000000}
    """)

    state = Agentboost.Runtime.state(data_root: tmp)
    heavy = Enum.find(state["earned_badges"], &(&1["badge_id"] == "8f5a9291c21f44bf"))

    assert heavy["name"] == "Heavy user"
    assert heavy["status"] == "earned"
    assert heavy["progress_percent"] == 100
    assert heavy["threshold"] == 10_000_000_000
    assert heavy["endorsement_text"] == "Weekly Claude and Codex token usage reaches 10B total."
  end

  test "missions self adjust daily frequency from recent loaded usage" do
    tmp =
      Path.join(
        System.tmp_dir!(),
        "agentboost-runtime-adaptive-missions-#{System.unique_integer([:positive])}"
      )

    on_exit(fn -> File.rm_rf(tmp) end)
    events = Path.join([tmp, "data", "ai-usage", "events.jsonl"])
    File.mkdir_p!(Path.dirname(events))

    rows =
      for offset <- 1..7, turn <- 0..2 do
        day = Date.utc_today() |> Date.add(-offset)

        occurred_at =
          DateTime.new!(day, Time.new!(10 + turn, 0, 0), "Etc/UTC") |> DateTime.to_iso8601()

        ~s({"source_agent":"codex","occurred_at":"#{occurred_at}","total_tokens":12})
      end

    today_rows =
      for turn <- 0..2 do
        occurred_at =
          DateTime.utc_now()
          |> DateTime.add(turn * 60, :second)
          |> DateTime.truncate(:second)
          |> DateTime.to_iso8601()

        ~s({"source_agent":"claude","occurred_at":"#{occurred_at}","total_tokens":12})
      end

    File.write!(events, Enum.join(rows ++ today_rows, "\n") <> "\n")

    state = Agentboost.Runtime.state(data_root: tmp)
    daily = hd(state["daily_missions"])

    assert daily["mission_id"] == "daily_ai_turn"
    assert daily["frequency"] == "2/day"
    assert daily["goal"] == 2
    assert daily["progress"] == 2
    assert daily["adaptive"] == true
    assert daily["target_source"] == "recent_active_day_average"
    assert daily["target_window_days"] == 14
  end

  test "weekly mission caps to five workdays and marks skill prompt review artifact done" do
    tmp =
      Path.join(
        System.tmp_dir!(),
        "agentboost-runtime-workday-missions-#{System.unique_integer([:positive])}"
      )

    on_exit(fn -> File.rm_rf(tmp) end)
    usage = Path.join([tmp, "data", "ai-usage"])
    events = Path.join(usage, "events.jsonl")

    review_dir =
      Path.join([tmp, "skill", "public", "two-phase-execution", "common", "skill-prompt-reviews"])

    File.mkdir_p!(usage)
    File.mkdir_p!(review_dir)

    today = Date.utc_today()
    current_week_start = Date.add(today, -rem(Date.day_of_week(today), 7))

    rows =
      for offset <- 0..6 do
        day = Date.add(current_week_start, offset)

        occurred_at =
          DateTime.new!(day, ~T[10:00:00], "Etc/UTC") |> DateTime.to_iso8601()

        ~s({"source_agent":"codex","occurred_at":"#{occurred_at}","total_tokens":12})
      end

    historical =
      for offset <- 1..5 do
        day = Date.add(current_week_start, -7 + offset)

        occurred_at =
          DateTime.new!(day, ~T[10:00:00], "Etc/UTC") |> DateTime.to_iso8601()

        ~s({"source_agent":"claude","occurred_at":"#{occurred_at}","total_tokens":12})
      end

    File.write!(events, Enum.join(rows ++ historical, "\n") <> "\n")

    File.write!(
      Path.join(review_dir, "skill-prompt-review-#{Date.to_iso8601(today)}-agentboost.md"),
      "# Review\n"
    )

    state = Agentboost.Runtime.state(data_root: tmp)
    streak = hd(state["weekly_missions"])

    review =
      Enum.find(state["weekly_missions"], &(&1["mission_id"] == "weekly_skill_prompt_review"))

    assert streak["frequency"] == "5/week"
    assert streak["goal"] == 5
    assert streak["progress"] <= 5
    assert streak["target_source"] == "recent_weekly_workdays"
    assert review["status"] == "done"
    assert review["progress"] == 1
  end

  test "state owns the menu-visible product fields that Swift consumes" do
    tmp =
      Path.join(System.tmp_dir!(), "agentboost-runtime-parity-#{System.unique_integer([:positive])}")

    on_exit(fn -> File.rm_rf(tmp) end)
    events = Path.join([tmp, "data", "ai-usage", "events.jsonl"])
    goals = Path.join([tmp, "data", "ai-usage", "goals.json"])
    refresh = Path.join([tmp, "data", "ai-usage", "sidebar-usage-refresh.json"])

    review_state =
      Path.join([
        tmp,
        "skill",
        "public",
        "two-phase-execution",
        "common",
        "state",
        "review-state.md"
      ])

    File.mkdir_p!(Path.dirname(events))
    File.mkdir_p!(Path.dirname(review_state))
    now = DateTime.utc_now() |> DateTime.truncate(:second) |> DateTime.to_iso8601()

    File.write!(events, """
    {"source_agent":"claude","occurred_at":"#{now}","input_tokens":10,"output_tokens":3,"reasoning_output_tokens":1,"total_tokens":14}
    {"source_agent":"codex","occurred_at":"#{now}","input_tokens":7,"output_tokens":5,"total_tokens":12}
    """)

    File.write!(goals, """
    [{"status":"completed"},{"status":"active"}]
    """)

    File.write!(refresh, """
    {"last_refreshed_at":"#{now}","events_imported":2}
    """)

    File.write!(review_state, """
    - Last meta-review: 2026-05-01
    - Latest meta-review score: 98
    - Non-trivial tasks since last meta-review: 5
    - Circuit-breakers since last meta-review: 0
    - Repeated-assumption failures since last meta-review: 0
    """)

    state = Agentboost.Runtime.state(data_root: tmp)

    assert state["app"] == "AgentBoost"
    assert state["repo_root"] == tmp
    assert state["events_count"] == 2
    assert state["goals_count"] == 2
    assert state["source_counts"] == %{"claude" => 1, "codex" => 1}
    assert state["rollups"]["Lifetime"]["total_tokens"] == 26
    assert state["token_activity"]["today_tokens"] == 26
    assert state["token_activity"]["active_agents"] == ["claude", "codex"]
    assert length(state["status_views"]) == 3
    assert length(state["agentboost_daily_7d"]) == 7
    today_bucket = List.last(state["agentboost_daily_7d"])
    assert today_bucket["claude"] == 14
    assert today_bucket["codex"] == 12
    assert length(state["badge_inventory"]) == 4
    refute Enum.any?(state["new_achievements"], &(&1["badge_id"] == "b0aec0de4cd56059"))
    assert Enum.any?(state["new_achievements"], &(&1["badge_id"] == "9ad1f6c937fd3839"))
    assert state["representative_badge"]["badge_id"] == "9ad1f6c937fd3839"
    assert hd(state["daily_missions"])["mission_id"] == "daily_ai_turn"
    assert hd(state["weekly_missions"])["mission_id"] == "weekly_ai_streak"
    assert state["meta_review"]["status"] == "due"
    assert state["meta_review"]["last_review"] == "2026-05-01"
    assert state["meta_review"]["state_file"] == review_state
    assert state["usage_refresh"]["events_imported"] == 2
    assert state["folder_access"] == %{"agentboost" => false, "claude" => false, "codex" => false}
  end

  test "meta review ok reason uses up to date wording" do
    tmp =
      Path.join(
        System.tmp_dir!(),
        "agentboost-runtime-meta-copy-#{System.unique_integer([:positive])}"
      )

    on_exit(fn -> File.rm_rf(tmp) end)

    review_state =
      Path.join([
        tmp,
        "skill",
        "public",
        "two-phase-execution",
        "common",
        "state",
        "review-state.md"
      ])

    File.mkdir_p!(Path.dirname(review_state))

    File.write!(review_state, """
    - Last meta-review: 2026-05-11
    - Latest meta-review score: 95
    - Non-trivial tasks since last meta-review: 0
    - Circuit-breakers since last meta-review: 0
    - Repeated-assumption failures since last meta-review: 0
    """)

    state = Agentboost.Runtime.state(data_root: tmp)

    assert state["meta_review"]["status"] == "ok"
    assert state["meta_review"]["reason"] == "Meta-review is up to date."
    refute state["meta_review"]["reason"] =~ "current"
  end

  defp local_today_at!(%Time{} = time) do
    {{year, month, day}, _time} = :calendar.local_time()
    local_tuple = {{year, month, day}, {time.hour, time.minute, time.second}}

    local_tuple
    |> local_time_to_utc_tuple()
    |> NaiveDateTime.from_erl!()
    |> DateTime.from_naive!("Etc/UTC")
  end

  defp local_time_to_utc_tuple(local_tuple) do
    case :calendar.local_time_to_universal_time_dst(local_tuple) do
      [utc_tuple | _] -> utc_tuple
      utc_tuple -> utc_tuple
    end
  end
end
