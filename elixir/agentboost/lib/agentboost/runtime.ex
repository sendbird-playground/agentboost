defmodule Agentboost.Runtime do
  @moduledoc false

  @contract "agentboost_state_v1"
  @runtime "elixir_beam"
  @privacy_controls [
    "Refresh Usage",
    "Remove Folder Access",
    "Delete Local Usage Data",
    "Export Local Report"
  ]
  @level_xp_requirements [
    {1, 15},
    {2, 34},
    {3, 57},
    {4, 92},
    {5, 135},
    {6, 372},
    {7, 560},
    {8, 840},
    {9, 1_242},
    {10, 1_716},
    {11, 2_360},
    {12, 3_216},
    {13, 4_200},
    {14, 5_460},
    {15, 7_050},
    {16, 8_840},
    {17, 11_040},
    {18, 13_716},
    {19, 16_680},
    {20, 20_216},
    {21, 24_402},
    {22, 28_980},
    {23, 34_320},
    {24, 40_512},
    {25, 54_900},
    {26, 57_210},
    {27, 63_666},
    {28, 73_080},
    {29, 83_270},
    {30, 95_700},
    {31, 108_480},
    {32, 122_760},
    {33, 138_666},
    {34, 155_540},
    {35, 174_216},
    {36, 194_832},
    {37, 216_600},
    {38, 240_550},
    {39, 266_682},
    {40, 294_216},
    {41, 324_240},
    {42, 356_916},
    {43, 391_160},
    {44, 428_280},
    {45, 468_450},
    {46, 510_420},
    {47, 555_680},
    {48, 604_416},
    {49, 655_200},
    {50, 709_716}
  ]
  @badge_id_aliases %{
    "billion-token-operator" => "a758dd50b1415e27",
    "billion-club" => "a758dd50b1415e27",
    "two-agent-day" => "b0aec0de4cd56059",
    "two-key-agents" => "b0aec0de4cd56059",
    "verified-workflow" => "9ad1f6c937fd3839"
  }
  @representative_badge_limit 1
  @two_key_agent_threshold 1_000_000_000
  @heavy_user_weekly_threshold 10_000_000_000

  def state(opts \\ []) do
    data_root = Keyword.get(opts, :data_root, ".") |> to_string()
    events = usage_events(data_root)
    goals = goals(data_root)
    totals = token_totals(events)
    rollups = build_rollups(events)
    today_activity = token_activity(rollups, totals)
    split_io = load_split_io_setting(data_root)
    recent_activity = recent_token_activity(events, split_io: split_io)
    network_activity = network_activity()
    badges = badge_inventory(events, goals)
    earned_badges = Enum.filter(badges, &(&1["status"] == "earned"))
    representatives = representative_badges(badges, selected_representative_badge_ids(data_root))
    representative = List.first(representatives)
    daily_missions = missions("daily", events, goals, data_root)
    weekly_missions = missions("weekly", events, goals, data_root)
    base_xp = xp(events, goals)
    mission_xp = earned_mission_xp(daily_missions ++ weekly_missions)
    total_xp = base_xp + mission_xp
    level_progress = level_progress(total_xp)

    %{
      "app" => "AgentBoost",
      "contract" => @contract,
      "repo_root" => data_root,
      "runtime" => @runtime,
      "events_count" => length(events),
      "goals_count" => length(goals),
      "source_counts" => source_counts(events),
      "import_window" => import_window(events),
      "xp" => total_xp,
      "level" => level_progress["current_level"],
      "level_label" => level_progress["level_label"],
      "level_progress" => level_progress,
      "xp_breakdown" => %{"base_xp" => base_xp, "mission_xp" => mission_xp},
      "workforce_fitness_score" => workforce_fitness(events, goals),
      "rollups" => rollups,
      "token_activity" => today_activity,
      "recent_token_activity" => recent_activity,
      "status_views" => status_views(events, rollups),
      "agentboost_daily_7d" => daily_usage_buckets(events),
      "network_activity" => network_activity,
      "status_animation_activity" =>
        status_animation_activity(recent_activity, today_activity, network_activity, split_io),
      "memory_monitor" => %{
        "threshold_percent" => 80,
        "status" => "unknown"
      },
      "badges" => badges,
      "earned_badges" => earned_badges,
      "badge_inventory" =>
        Enum.map(badges, fn badge ->
          rank = representative_badge_rank(representatives, badge["badge_id"])

          badge
          |> Map.put("is_representative", rank > 0)
          |> Map.put("representative_rank", rank)
          |> Map.put("can_select", badge["status"] == "earned")
        end),
      "representative_badge" => representative,
      "representative_badges" => representatives,
      "meta_review" => meta_review_state(data_root),
      "identity_update" => identity_update_state(data_root),
      "new_achievements" => Enum.filter(badges, &(&1["status"] == "earned")),
      "daily_missions" => daily_missions,
      "weekly_missions" => weekly_missions,
      "streak" => %{"status" => "local"},
      "notification_file" =>
        Path.join([data_root, "data", "ai-usage", "sidebar-notifications.json"]),
      "usage_refresh" =>
        read_simple_json(Path.join([data_root, "data", "ai-usage", "sidebar-usage-refresh.json"])),
      "folder_access" => %{"agentboost" => false, "claude" => false, "codex" => false},
      "privacy_controls" => @privacy_controls,
      "missions" => daily_missions
    }
  end

  defp usage_events(data_root) do
    path = Path.join([to_string(data_root), "data", "ai-usage", "events.jsonl"])

    if File.exists?(path) do
      path
      |> File.stream!([], :line)
      |> Stream.map(&String.trim/1)
      |> Stream.reject(&(&1 == ""))
      |> Stream.map(&decode_event_line/1)
      |> Stream.reject(&is_nil/1)
      |> Enum.to_list()
    else
      []
    end
  rescue
    _ -> []
  end

  defp decode_event_line(line) do
    case :json.decode(line) do
      map when is_map(map) -> annotate_event(map)
      _ -> nil
    end
  rescue
    _ -> nil
  catch
    _, _ -> nil
  end

  # Pre-compute the parsed datetime and local date once at ingest, so the
  # dozen downstream rollup passes don't each re-parse `occurred_at`.
  defp annotate_event(map) do
    case Map.get(map, "occurred_at") do
      value when is_binary(value) and value != "" ->
        case DateTime.from_iso8601(value) do
          {:ok, datetime, _offset} ->
            map
            |> Map.put(:__datetime, datetime)
            |> Map.put(:__local_date, local_date(datetime))

          _ ->
            map
        end

      _ ->
        map
    end
  end

  defp goals(data_root) do
    path = Path.join([to_string(data_root), "data", "ai-usage", "goals.json"])

    case File.read(path) do
      {:ok, raw} ->
        Regex.scan(~r/"status"\s*:\s*"([^"]+)"/, raw)
        |> Enum.map(fn [_match, status] -> %{"status" => status} end)

      _ ->
        []
    end
  end

  defp token_totals(events) do
    Enum.reduce(
      events,
      %{input_tokens: 0, output_tokens: 0, reasoning_output_tokens: 0, total_tokens: 0},
      fn event, totals ->
        input_tokens = integer_field(event, "input_tokens")
        output_tokens = integer_field(event, "output_tokens")
        reasoning_tokens = integer_field(event, "reasoning_output_tokens")
        explicit_total = integer_field(event, "total_tokens")

        total_tokens =
          if explicit_total > 0,
            do: explicit_total,
            else: input_tokens + output_tokens + reasoning_tokens

        %{
          input_tokens: totals.input_tokens + input_tokens,
          output_tokens: totals.output_tokens + output_tokens,
          reasoning_output_tokens: totals.reasoning_output_tokens + reasoning_tokens,
          total_tokens: totals.total_tokens + total_tokens
        }
      end
    )
  end

  defp active_agents(events) do
    events
    |> Enum.map(&source_agent/1)
    |> Enum.reject(&(&1 == ""))
    |> Enum.uniq()
    |> Enum.take(2)
  end

  defp source_counts(events) do
    Enum.reduce(events, %{}, fn event, counts ->
      source = source_agent(event)

      if source == "" do
        counts
      else
        Map.update(counts, source, 1, &(&1 + 1))
      end
    end)
  end

  defp token_usage_by_agent(events) do
    Enum.reduce(events, %{}, fn event, totals ->
      source = source_agent(event)

      if source == "" do
        totals
      else
        Map.update(totals, source, event_total_tokens(event), &(&1 + event_total_tokens(event)))
      end
    end)
  end

  defp max_weekly_claude_codex_tokens(events) do
    weekly =
      Enum.reduce(events, %{}, fn event, totals ->
        source = source_agent(event)

        if source in ["claude", "codex"] do
          case event_local_date(event) do
            nil ->
              totals

            date ->
              week_start = sunday_week_start(date)

              Map.update(
                totals,
                week_start,
                event_total_tokens(event),
                &(&1 + event_total_tokens(event))
              )
          end
        else
          totals
        end
      end)

    case Map.values(weekly) do
      [] -> 0
      values -> Enum.max(values)
    end
  end

  defp import_window(events) do
    dates =
      events
      |> Enum.map(&event_datetime/1)
      |> Enum.reject(&is_nil/1)
      |> Enum.sort(DateTime)

    case dates do
      [] -> "No local usage events"
      [first | _] -> DateTime.to_iso8601(first) <> " to " <> DateTime.to_iso8601(List.last(dates))
    end
  end

  defp build_rollups(events) do
    today = local_today()
    current_week_start = sunday_week_start(today)

    seeds = %{
      "Today" => rollup_seed(),
      "This Week" => rollup_seed(),
      "This Month" => rollup_seed(),
      "Lifetime" => rollup_seed()
    }

    Enum.reduce(events, seeds, fn event, rollups ->
      tokens = event_total_tokens(event)
      source = source_agent(event)
      rollups = add_rollup_tokens(rollups, "Lifetime", tokens, source)

      case event_local_date(event) do
        nil ->
          rollups

        date ->
          rollups
          |> maybe_add_rollup(date == today, "Today", tokens, source)
          |> maybe_add_rollup(
            sunday_week_start(date) == current_week_start,
            "This Week",
            tokens,
            source
          )
          |> maybe_add_rollup(
            date.year == today.year and date.month == today.month,
            "This Month",
            tokens,
            source
          )
      end
    end)
  end

  defp rollup_seed, do: %{"total_tokens" => 0, "by_agent" => %{}}

  defp maybe_add_rollup(rollups, true, name, tokens, source),
    do: add_rollup_tokens(rollups, name, tokens, source)

  defp maybe_add_rollup(rollups, false, _name, _tokens, _source), do: rollups

  defp add_rollup_tokens(rollups, name, tokens, source) do
    update_in(rollups, [name], fn rollup ->
      by_agent =
        if source == "" do
          rollup["by_agent"]
        else
          Map.update(rollup["by_agent"], source, tokens, &(&1 + tokens))
        end

      %{"total_tokens" => rollup["total_tokens"] + tokens, "by_agent" => by_agent}
    end)
  end

  defp token_activity(rollups, totals) do
    today_tokens = rollups["Today"]["total_tokens"]
    active_agents = active_agents_from_rollup(rollups["Today"])
    rocket_count = if length(active_agents) >= 2, do: 2, else: 1

    base = %{
      "input_tokens" => totals.input_tokens,
      "output_tokens" => totals.output_tokens,
      "reasoning_output_tokens" => totals.reasoning_output_tokens,
      "total_tokens" => totals.total_tokens,
      "today_tokens" => today_tokens,
      "active_agents" => active_agents,
      "rocket_count" => rocket_count
    }

    cond do
      today_tokens <= 0 ->
        Map.merge(base, %{
          "intensity" => "idle",
          "animation_interval_seconds" => 1.5,
          "emoji" => "",
          "rocket_speed" => 0.0
        })

      today_tokens < 10_000_000 ->
        Map.merge(base, %{
          "intensity" => "active",
          "animation_interval_seconds" => 0.9,
          "emoji" => "*",
          "rocket_speed" => 0.45
        })

      today_tokens < 100_000_000 ->
        Map.merge(base, %{
          "intensity" => "high",
          "animation_interval_seconds" => 0.45,
          "emoji" => "^",
          "rocket_speed" => 0.9
        })

      true ->
        Map.merge(base, %{
          "intensity" => "surge",
          "animation_interval_seconds" => 0.2,
          "emoji" => "!",
          "rocket_speed" => 1.8
        })
    end
  end

  defp recent_token_activity(events, opts) do
    split_io = Keyword.get(opts, :split_io, false)
    cutoff = DateTime.add(DateTime.utc_now(), -60, :second)

    recent =
      Enum.filter(events, fn event ->
        case event_datetime(event) do
          nil -> false
          datetime -> DateTime.compare(datetime, cutoff) != :lt
        end
      end)

    tokens = Enum.reduce(recent, 0, &(&2 + event_total_tokens(&1)))
    {by_agent_total, by_agent_input, by_agent_output} = agent_io_totals(recent)
    active_agents = if tokens > 0, do: active_agents(recent), else: active_agents(events)
    rocket_count = if length(active_agents) >= 2, do: 2, else: 1

    base = %{
      "active" => tokens > 0 or event_total_tokens_sum(events) > 0,
      "last_1m_tokens" => tokens,
      "display_tokens" => compact_token_count(tokens),
      "active_agents" => active_agents,
      "rocket_count" => rocket_count,
      "agent_usage" =>
        Map.new(["claude", "codex"], fn agent ->
          {agent,
           %{
             "last_1m_tokens" => Map.get(by_agent_total, agent, 0),
             "display_tokens" => compact_token_count(Map.get(by_agent_total, agent, 0)),
             "input_tokens" => Map.get(by_agent_input, agent, 0),
             "output_tokens" => Map.get(by_agent_output, agent, 0)
           }}
        end),
      "split_io_enabled" => split_io
    }

    base =
      cond do
        tokens <= 0 ->
          Map.merge(base, %{
            "activity_level" => "idle",
            "rocket_state" => "waiting",
            "rocket_speed" => 0.0,
            "rocket_altitude" => 0,
            "has_flame" => false
          })

        tokens < 50_000 ->
          Map.merge(base, %{
            "activity_level" => "moderate",
            "rocket_state" => "flying",
            "rocket_speed" => 0.6,
            "rocket_altitude" => min(100, max(1, div(tokens, 1_000))),
            "has_flame" => true
          })

        true ->
          Map.merge(base, %{
            "activity_level" => "high",
            "rocket_state" => "surging",
            "rocket_speed" => 1.2,
            "rocket_altitude" => min(250, 100 + div(tokens, 10_000)),
            "has_flame" => true
          })
      end

    if split_io do
      rockets =
        for agent <- ["claude", "codex"],
            {channel, ch_tokens} <- [
              {"input", Map.get(by_agent_input, agent, 0)},
              {"output", Map.get(by_agent_output, agent, 0)}
            ] do
          speed = usage_animation_speed(ch_tokens)

          %{
            "agent" => agent,
            "channel" => channel,
            "tokens" => ch_tokens,
            "display_tokens" => compact_token_count(ch_tokens),
            "speed" => speed,
            "altitude" => usage_animation_altitude(ch_tokens),
            "animation_interval_seconds" => animation_interval_for_speed(speed),
            "has_flame" => ch_tokens > 0
          }
        end

      rockets = apply_relative_speed(rockets)

      base
      |> Map.put("rockets", rockets)
      |> Map.put("rocket_count", length(rockets))
    else
      base
    end
  end

  defp agent_io_totals(events) do
    Enum.reduce(events, {%{}, %{}, %{}}, fn event, {totals, inputs, outputs} ->
      agent = source_agent(event)

      if agent in ["claude", "codex"] do
        total = event_total_tokens(event)
        input = integer_field(event, "input_tokens") + integer_field(event, "cached_input_tokens")

        output =
          integer_field(event, "output_tokens") +
            integer_field(event, "reasoning_output_tokens")

        {
          Map.update(totals, agent, total, &(&1 + total)),
          Map.update(inputs, agent, input, &(&1 + input)),
          Map.update(outputs, agent, output, &(&1 + output))
        }
      else
        {totals, inputs, outputs}
      end
    end)
  end

  defp usage_animation_speed(tokens) do
    tokens = max(0, integer_value(tokens))

    cond do
      tokens <= 0 -> 0.0
      tokens < 50_000 -> Float.round(0.35 + tokens / 50_000 * 0.45, 3)
      true -> Float.round(min(2.4, 0.8 + (tokens - 50_000) / 950_000 * 1.6), 3)
    end
  end

  defp usage_animation_altitude(tokens) do
    tokens = max(0, integer_value(tokens))

    cond do
      tokens <= 0 -> 0
      tokens < 50_000 -> min(50, max(1, div(tokens, 1_000)))
      true -> min(250, 50 + div(tokens - 50_000, 5_000))
    end
  end

  defp animation_interval_for_speed(speed) do
    speed = max(0.0, float_value(speed))

    if speed <= 0 do
      1.5
    else
      Float.round(max(0.12, 1.2 - speed * 0.35), 3)
    end
  end

  defp apply_relative_speed(rockets) do
    peak =
      rockets
      |> Enum.map(&integer_value(&1["tokens"]))
      |> Enum.max(fn -> 0 end)

    if peak <= 0 do
      rockets
    else
      Enum.map(rockets, fn rocket ->
        tokens = integer_value(rocket["tokens"])

        if tokens <= 0 do
          rocket
        else
          ratio = max(0.4, min(1.0, tokens / peak))
          base_speed = float_value(rocket["speed"])
          scaled = Float.round(base_speed * ratio, 3)

          rocket
          |> Map.put("speed", scaled)
          |> Map.put("animation_interval_seconds", animation_interval_for_speed(scaled))
        end
      end)
    end
  end

  defp load_split_io_setting(data_root) do
    path = Path.join([to_string(data_root), "data", "ai-usage", "settings.json"])

    with {:ok, raw} <- File.read(path),
         {:ok, decoded} <- decode_json(raw),
         %{} = display <- Map.get(decoded, "display", %{}) do
      display["split_io_rockets"] == true
    else
      _ -> false
    end
  end

  defp decode_json(raw) do
    try do
      {:ok, :json.decode(raw)}
    rescue
      _ -> :error
    catch
      _, _ -> :error
    end
  end

  defp status_views(events, rollups) do
    lifetime = rollups["Lifetime"]["total_tokens"]
    now = DateTime.utc_now()
    seven_day_tokens = token_sum_since(events, DateTime.add(now, -7 * 24 * 60 * 60, :second), now)
    minute_tokens = token_sum_since(events, DateTime.add(now, -60, :second), now)

    [
      %{
        "view_id" => "last_7d_cumulative",
        "label" => "7d",
        "tokens" => seven_day_tokens,
        "display_tokens" => compact_token_count(seven_day_tokens),
        "display_text" => "7d #{compact_token_count(seven_day_tokens)}",
        "trend" => "flat",
        "trend_symbol" => "flat"
      },
      %{
        "view_id" => "total_cumulative",
        "label" => "Total",
        "tokens" => lifetime,
        "display_tokens" => compact_token_count(lifetime),
        "display_text" => "Total #{compact_token_count(lifetime)}",
        "trend" => "flat",
        "trend_symbol" => "flat"
      },
      %{
        "view_id" => "token_per_minute",
        "label" => "Token/min",
        "tokens" => minute_tokens,
        "display_tokens" => "#{compact_token_count(minute_tokens)}/min",
        "display_text" => "#{compact_token_count(minute_tokens)}/min",
        "trend" => "flat",
        "trend_symbol" => "flat"
      }
    ]
  end

  defp token_sum_since(events, start_datetime, end_datetime) do
    Enum.reduce(events, 0, fn event, total ->
      case event_datetime(event) do
        nil ->
          total

        datetime ->
          if DateTime.compare(datetime, start_datetime) != :lt and
               DateTime.compare(datetime, end_datetime) != :gt do
            total + event_total_tokens(event)
          else
            total
          end
      end
    end)
  end

  defp daily_usage_buckets(events) do
    today = local_today()
    days = Enum.map(6..0//-1, &Date.add(today, -&1))

    buckets =
      Map.new(days, fn day ->
        {day,
         %{
           "day" => Calendar.strftime(day, "%a"),
           "date" => Date.to_iso8601(day),
           "claude" => 0,
           "codex" => 0
         }}
      end)

    buckets =
      Enum.reduce(events, buckets, fn event, acc ->
        with %Date{} = day <- event_local_date(event),
             true <- Map.has_key?(acc, day),
             agent when agent in ["claude", "codex"] <- source_agent(event) do
          update_in(acc, [day, agent], &(&1 + event_total_tokens(event)))
        else
          _ -> acc
        end
      end)

    Enum.map(days, &Map.fetch!(buckets, &1))
  end

  defp network_activity do
    %{
      "speed_source" => "network",
      "bytes_per_second" => 0,
      "outbound_bytes_per_second" => 0,
      "rocket_speed" => 0.0,
      "animation_interval_seconds" => 1.5,
      "has_flame" => false
    }
  end

  defp status_animation_activity(recent, today, network, split_io) do
    cond do
      float_value(recent["rocket_speed"]) > 0 ->
        recent |> Map.put("source", "recent") |> apply_network_animation_speed(network)

      float_value(today["rocket_speed"]) > 0 and integer_value(today["today_tokens"]) > 0 ->
        today_tokens = integer_value(today["today_tokens"])
        intensity = today["intensity"] || "active"

        base = %{
          "last_1m_tokens" => integer_value(recent["last_1m_tokens"]),
          "display_tokens" => compact_token_count(today_tokens),
          "activity_level" => intensity,
          "rocket_state" => "waiting",
          "rocket_speed" => 0.0,
          "rocket_altitude" => 0,
          "animation_interval_seconds" => 1.5,
          "has_flame" => false,
          "active_agents" => today["active_agents"] || [],
          "rocket_count" => max(1, min(2, integer_value(today["rocket_count"]))),
          "source" => "today",
          "split_io_enabled" => split_io
        }

        base =
          if split_io and is_list(recent["rockets"]) do
            base
            |> Map.put("rockets", recent["rockets"])
            |> Map.put(
              "rocket_count",
              max(length(recent["rockets"]), base["rocket_count"])
            )
            |> Map.put("agent_usage", recent["agent_usage"] || %{})
          else
            base
          end

        apply_network_animation_speed(base, network)

      true ->
        recent |> Map.put("source", "recent") |> apply_network_animation_speed(network)
    end
  end

  defp apply_network_animation_speed(activity, network) do
    usage_speed = float_value(activity["rocket_speed"])

    result =
      activity
      |> Map.put("outbound_bytes_per_second", integer_value(network["outbound_bytes_per_second"]))

    if usage_speed <= 0 do
      result
      |> Map.put("speed_source", "token_usage")
      |> Map.put("rocket_speed", 0.0)
      |> Map.put("rocket_altitude", 0)
      |> Map.put(
        "animation_interval_seconds",
        float_value(activity["animation_interval_seconds"]) |> zero_default(1.5)
      )
      |> Map.put("has_flame", false)
    else
      network_speed = float_value(network["rocket_speed"])

      if network_speed > usage_speed do
        result
        |> Map.put("speed_source", "network")
        |> Map.put("rocket_speed", network_speed)
        |> Map.put(
          "animation_interval_seconds",
          float_value(network["animation_interval_seconds"])
        )
        |> Map.put("has_flame", true)
        |> Map.put("source", "network")
      else
        result
        |> Map.put("speed_source", "token_usage")
        |> Map.put("rocket_speed", usage_speed)
        |> Map.put(
          "animation_interval_seconds",
          float_value(activity["animation_interval_seconds"])
          |> zero_default(animation_interval_for_speed(usage_speed))
        )
        |> Map.put("has_flame", true)
      end
    end
  end

  defp zero_default(value, default) when value <= 0, do: default
  defp zero_default(value, _default), do: value

  defp xp(events, goals),
    do: div(event_total_tokens_sum(events), 1_000_000) + completed_goals(goals) * 100

  defp earned_mission_xp(missions) do
    missions
    |> Enum.filter(&(&1["status"] == "done"))
    |> Enum.map(&integer_value(&1["xp"]))
    |> Enum.sum()
  end

  defp level_progress(total_xp) do
    total = max(0, integer_value(total_xp))
    level_progress(total, total, @level_xp_requirements)
  end

  defp level_progress(total, remaining, [{level, required_xp}]) do
    current = min(remaining, required_xp)

    %{
      "current_level" => level,
      "level_label" => "Lv #{level}",
      "current_level_xp" => current,
      "current_level_required_xp" => required_xp,
      "xp_to_next_level" => if(remaining >= required_xp, do: 0, else: required_xp - current),
      "progress_percent" => min(100, div(current * 100, required_xp)),
      "next_level" => nil,
      "max_level" => level,
      "total_xp" => total
    }
  end

  defp level_progress(total, remaining, [{level, required_xp} | rest]) do
    if remaining < required_xp do
      %{
        "current_level" => level,
        "level_label" => "Lv #{level}",
        "current_level_xp" => remaining,
        "current_level_required_xp" => required_xp,
        "xp_to_next_level" => required_xp - remaining,
        "progress_percent" => min(100, div(remaining * 100, required_xp)),
        "next_level" => level + 1,
        "max_level" => 50,
        "total_xp" => total
      }
    else
      level_progress(total, remaining - required_xp, rest)
    end
  end

  defp workforce_fitness(events, goals) do
    min(100, map_size(source_counts(events)) * 25 + completed_goals(goals) * 5)
  end

  defp badge_inventory(events, goals) do
    total = event_total_tokens_sum(events)
    tokens_by_agent = token_usage_by_agent(events)
    claude_tokens = tokens_by_agent["claude"] || 0
    codex_tokens = tokens_by_agent["codex"] || 0

    two_key_agents_earned =
      claude_tokens >= @two_key_agent_threshold and codex_tokens >= @two_key_agent_threshold

    heavy_user_weekly_tokens = max_weekly_claude_codex_tokens(events)
    heavy_user_earned = heavy_user_weekly_tokens >= @heavy_user_weekly_threshold

    completed = completed_goals(goals)

    [
      badge(
        "a758dd50b1415e27",
        "Billion Club",
        if(total >= 1_000_000_000, do: "earned", else: "in_progress"),
        min(100, div(total, 10_000_000)),
        %{
          "endorsement_text" =>
            "Uses AI agents as daily working partners, not occasional search boxes.",
          "evidence_requirement" => "10 completed evidence-backed goals"
        }
      ),
      badge(
        "b0aec0de4cd56059",
        "Two key agents",
        if(two_key_agents_earned, do: "earned", else: "in_progress"),
        min(100, div(min(claude_tokens, codex_tokens), 10_000_000)),
        %{
          "endorsement_text" => "Token usage for Claude and Codex each reaches over 1B.",
          "evidence_requirement" => "Claude and Codex lifetime token usage each over 1B"
        }
      ),
      badge(
        "8f5a9291c21f44bf",
        "Heavy user",
        if(heavy_user_earned, do: "earned", else: "in_progress"),
        min(100, div(heavy_user_weekly_tokens, 100_000_000)),
        %{
          "endorsement_text" => "Weekly Claude and Codex token usage reaches 10B total.",
          "evidence_requirement" => "Claude and Codex weekly usage reaches 10B combined",
          "threshold" => @heavy_user_weekly_threshold
        }
      ),
      badge(
        "9ad1f6c937fd3839",
        "Verified Workflow",
        if(completed > 0, do: "earned", else: "in_progress"),
        min(100, completed * 10)
      )
    ]
  end

  defp badge(id, name, status, progress, attrs \\ %{}) do
    %{"badge_id" => id, "name" => name, "status" => status, "progress_percent" => progress}
    |> Map.merge(attrs)
  end

  defp representative_badges(badges, selected_ids) do
    selected =
      selected_ids
      |> Enum.map(&normalize_badge_id/1)
      |> Enum.uniq()
      |> Enum.take(@representative_badge_limit)
      |> Enum.map(fn selected_id ->
        Enum.find(
          badges,
          &(selected_id != "" and &1["badge_id"] == selected_id and &1["status"] == "earned")
        )
      end)
      |> Enum.reject(&is_nil/1)

    if selected == [] do
      badges
      |> Enum.find(&(&1["status"] == "earned"))
      |> Kernel.||(List.first(badges))
      |> List.wrap()
    else
      selected
    end
  end

  defp representative_badge_rank(representatives, badge_id) do
    representatives
    |> Enum.find_index(&(&1["badge_id"] == badge_id))
    |> case do
      nil -> 0
      index -> index + 1
    end
  end

  defp normalize_badge_id(nil), do: ""

  defp normalize_badge_id(id) do
    id = to_string(id)
    Map.get(@badge_id_aliases, id, id)
  end

  defp selected_representative_badge_ids(data_root) do
    ledger =
      data_root
      |> Path.join("data/ai-usage/sidebar-notifications.json")
      |> read_simple_json()

    values =
      case Map.get(ledger, "representative_badge_ids") do
        ids when is_list(ids) -> ids
        _ -> [Map.get(ledger, "representative_badge_id", "")]
      end

    values
    |> Enum.map(&(to_string(&1) |> String.trim()))
    |> Enum.reject(&(&1 == ""))
    |> Enum.uniq()
    |> Enum.take(@representative_badge_limit)
  end

  defp missions("weekly" = prefix, events, _goals, data_root) do
    active_workdays = active_workdays_this_week(events)
    weekly_target = self_adjusted_weekly_target(events)
    skill_prompt_reviews = skill_prompt_reviews_this_week(data_root)
    skill_prompt_progress = min(skill_prompt_reviews, 1)
    identity_updates = identity_updates_this_week(data_root)
    identity_progress = min(identity_updates, 1)

    [
      mission(
        "weekly_ai_streak",
        "Build a #{weekly_target}-workday AI rhythm",
        mission_progress_state(active_workdays, weekly_target),
        "Use Claude or Codex on #{weekly_target} workdays this week",
        prefix,
        "#{weekly_target}/week",
        min(active_workdays, weekly_target),
        weekly_target,
        "active_workdays",
        25,
        "loaded_events_only",
        adaptive: true,
        target_source: "recent_weekly_workdays",
        target_window_days: 28
      ),
      mission(
        "weekly_skill_prompt_review",
        "Review current skills and prompts",
        mission_progress_state(skill_prompt_progress, 1),
        "bin/agentboost --do-skill-prompt-review",
        prefix,
        "1/week",
        skill_prompt_progress,
        1,
        "skill_prompt_review_this_week",
        25,
        "local_artifact_scan"
      ),
      mission(
        "weekly_identity_update",
        "Update personality and thinking path",
        mission_progress_state(identity_progress, 1),
        "bin/agentboost --do-identity-update",
        prefix,
        "1/week",
        identity_progress,
        1,
        "identity_update_this_week",
        25,
        "local_artifact_scan",
        evidence_hint: "identity draft update artifact for the current week"
      )
    ]
  end

  defp missions("daily" = prefix, events, _goals, _data_root) do
    daily_target = self_adjusted_daily_target(events)
    today_count = events_today(events) |> length() |> min(daily_target)

    [
      mission(
        "daily_ai_turn",
        "Use #{count_text(daily_target)} AI agent #{pluralize("turn", daily_target)} today",
        mission_progress_state(today_count, daily_target),
        "Run #{count_text(daily_target)} Claude or Codex #{pluralize("turn", daily_target)} today",
        prefix,
        "#{daily_target}/day",
        today_count,
        daily_target,
        "local_usage_event",
        5,
        "loaded_events_only",
        adaptive: true,
        target_source: "recent_active_day_average",
        target_window_days: 14
      )
    ]
  end

  defp mission(
         mission_id,
         title,
         status,
         command_hint,
         cadence,
         frequency,
         progress,
         goal,
         metric,
         xp,
         check_cost,
         opts \\ []
       ) do
    base = %{
      "mission_id" => mission_id,
      "title" => title,
      "status" => status,
      "command_hint" => command_hint,
      "cadence" => cadence,
      "frequency" => frequency,
      "progress" => max(0, min(integer_value(progress), integer_value(goal))),
      "goal" => max(0, integer_value(goal)),
      "metric" => metric,
      "xp" => max(0, integer_value(xp)),
      "auto_check" => true,
      "check_cost" => check_cost
    }

    Enum.reduce(opts, base, fn {key, value}, mission ->
      Map.put(mission, Atom.to_string(key), value)
    end)
  end

  defp mission_progress_state(progress, goal) do
    if integer_value(progress) >= integer_value(goal), do: "done", else: "active"
  end

  defp count_text(1), do: "one"
  defp count_text(count), do: "#{count}"

  defp pluralize(word, 1), do: word
  defp pluralize(word, _count), do: word <> "s"

  defp events_today(events) do
    today = local_today()

    Enum.filter(events, fn event -> event_local_date(event) == today end)
  end

  defp active_workdays_this_week(events) do
    current_week_start = sunday_week_start(local_today())

    events
    |> Enum.map(&event_local_date/1)
    |> Enum.reject(&is_nil/1)
    |> Enum.filter(&(sunday_week_start(&1) == current_week_start and workday?(&1)))
    |> Enum.uniq()
    |> length()
  end

  defp workday?(day), do: Date.day_of_week(day) in 1..5

  defp sunday_week_start(day), do: Date.add(day, -rem(Date.day_of_week(day), 7))

  defp self_adjusted_daily_target(events) do
    {average, active_days} = recent_active_day_average(events, 14)

    cond do
      active_days >= 5 and average >= 6.0 -> 3
      active_days >= 4 and average >= 2.5 -> 2
      true -> 1
    end
  end

  defp recent_active_day_average(events, days) do
    today = local_today()
    start_day = Date.add(today, -days)

    counts =
      Enum.reduce(events, %{}, fn event, acc ->
        case event_local_date(event) do
          nil ->
            acc

          day ->
            if Date.compare(day, start_day) != :lt and Date.compare(day, today) == :lt do
              Map.update(acc, day, 1, &(&1 + 1))
            else
              acc
            end
        end
      end)

    if map_size(counts) == 0 do
      {0.0, 0}
    else
      {Enum.sum(Map.values(counts)) / map_size(counts), map_size(counts)}
    end
  end

  defp self_adjusted_weekly_target(events) do
    {average, active_weeks} = recent_weekly_workday_average(events, 28)

    cond do
      active_weeks > 0 and average >= 5.0 -> 5
      true -> 4
    end
  end

  defp recent_weekly_workday_average(events, days) do
    current_week_start = sunday_week_start(local_today())
    start_day = Date.add(current_week_start, -max(days, 7))

    active_workdays_by_week =
      Enum.reduce(events, %{}, fn event, acc ->
        case event_local_date(event) do
          nil ->
            acc

          day ->
            if Date.compare(day, start_day) != :lt and
                 Date.compare(day, current_week_start) == :lt and workday?(day) do
              week_start = sunday_week_start(day)
              Map.update(acc, week_start, MapSet.new([day]), &MapSet.put(&1, day))
            else
              acc
            end
        end
      end)

    if map_size(active_workdays_by_week) == 0 do
      {0.0, 0}
    else
      counts = Enum.map(Map.values(active_workdays_by_week), &MapSet.size/1)
      {Enum.sum(counts) / length(counts), length(counts)}
    end
  end

  defp skill_prompt_reviews_this_week(data_root) do
    review_dir =
      Path.join([
        to_string(data_root),
        "skill",
        "public",
        "two-phase-execution",
        "common",
        "skill-prompt-reviews"
      ])

    if File.dir?(review_dir) do
      current_week_start = sunday_week_start(local_today())
      current_week_end = Date.add(current_week_start, 7)

      review_dir
      |> Path.join("skill-prompt-review-*.md")
      |> Path.wildcard()
      |> Enum.count(fn path ->
        case skill_prompt_review_date(Path.basename(path)) do
          {:ok, day} ->
            Date.compare(day, current_week_start) != :lt and
              Date.compare(day, current_week_end) == :lt

          :error ->
            false
        end
      end)
    else
      0
    end
  end

  defp skill_prompt_review_date(name) do
    case Regex.run(~r/skill-prompt-review-(\d{4}-\d{2}-\d{2})/, name) do
      [_match, date] -> Date.from_iso8601(date)
      _ -> :error
    end
  end

  defp identity_update_state(data_root) do
    latest = latest_identity_update_summary(data_root)
    progress = if latest && identity_update_summary_this_week?(latest), do: 1, else: 0

    base = %{
      "status" => if(progress == 1, do: "done", else: "active"),
      "progress" => progress,
      "goal" => 1,
      "metric" => "identity_update_this_week",
      "command_hint" => "bin/agentboost --do-identity-update",
      "evidence_hint" => "identity draft update artifact for the current week",
      "reason" =>
        if(progress == 1,
          do: "Identity drafts were updated this week.",
          else: "No personality/thinking-path draft update this week."
        ),
      "review_artifact" => "",
      "source_file_count" => 0,
      "evidence_items" => 0,
      "personality_theme_count" => 0,
      "thinking_theme_count" => 0
    }

    if latest do
      Map.merge(base, read_identity_update_summary_metrics(latest))
      |> Map.put("review_artifact", latest)
      |> Map.put("updated_at", identity_update_updated_at(latest))
    else
      base
    end
  end

  defp identity_update_updated_at(path) do
    case identity_update_date(path) do
      %Date{} = day -> Date.to_iso8601(day)
      _ -> ""
    end
  end

  defp identity_updates_this_week(data_root) do
    data_root
    |> identity_update_summaries()
    |> Enum.count(&identity_update_summary_this_week?/1)
  end

  defp latest_identity_update_summary(data_root) do
    data_root
    |> identity_update_summaries()
    |> Enum.sort_by(
      fn path ->
        day = identity_update_date(path)
        {if(day, do: Date.to_iso8601(day), else: "0000-00-00"), path_mtime(path), path}
      end,
      :desc
    )
    |> List.first()
  end

  defp path_mtime(path) do
    case File.stat(path, time: :posix) do
      {:ok, %File.Stat{mtime: mtime}} -> mtime
      _ -> 0
    end
  end

  defp identity_update_summaries(data_root) do
    data_root
    |> to_string()
    |> Path.join("identity/drafts/identity-update-*-agentboost*/summary.md")
    |> Path.wildcard()
  end

  defp identity_update_summary_this_week?(path) do
    current_week_start = sunday_week_start(local_today())
    current_week_end = Date.add(current_week_start, 7)

    case identity_update_date(path) do
      nil ->
        false

      day ->
        Date.compare(day, current_week_start) != :lt and
          Date.compare(day, current_week_end) == :lt
    end
  end

  defp identity_update_date(path) do
    case Regex.run(~r/identity-update-(\d{4}-\d{2}-\d{2})-agentboost/, path) do
      [_match, date] ->
        case Date.from_iso8601(date) do
          {:ok, day} -> day
          _ -> nil
        end

      _ ->
        nil
    end
  end

  defp read_identity_update_summary_metrics(path) do
    raw =
      case File.read(path) do
        {:ok, text} -> text
        _ -> ""
      end

    %{
      "source_file_count" => markdown_metric(raw, "Source files"),
      "evidence_items" => markdown_metric(raw, "Evidence items"),
      "personality_theme_count" => markdown_metric(raw, "Personality themes"),
      "thinking_theme_count" => markdown_metric(raw, "Thinking themes")
    }
  end

  defp markdown_metric(raw, label) do
    case Regex.run(~r/- #{Regex.escape(label)}:\s*(\d+)/, raw) do
      [_match, value] -> String.to_integer(value)
      _ -> 0
    end
  end

  defp meta_review_state(data_root) do
    state = review_state(data_root)
    state_file = workflow_state_file(data_root, "review-state.md")
    last_review = state["Last meta-review"] || ""
    score = integer_value(state["Latest meta-review score"])
    tasks = integer_value(state["Non-trivial tasks since last meta-review"])
    cbs = integer_value(state["Circuit-breakers since last meta-review"])

    {status, reason} =
      cond do
        score < 60 -> {"blocked", "Score #{score} is below 60."}
        tasks >= 5 -> {"due", "#{tasks} non-trivial tasks since last meta-review."}
        cbs >= 2 -> {"due", "#{cbs} circuit-breakers since last meta-review."}
        true -> {"ok", "Meta-review is up to date."}
      end

    %{
      "status" => status,
      "due" => status != "ok",
      "reason" => reason,
      "last_review" => last_review,
      "latest_score" => score,
      "tasks_since_last_review" => tasks,
      "circuit_breakers_since_last_review" => cbs,
      "repeated_assumption_failures" =>
        integer_value(state["Repeated-assumption failures since last meta-review"]),
      "state_file" => state_file
    }
  end

  defp review_state(data_root) do
    path = workflow_state_file(data_root, "review-state.md")

    case File.read(path) do
      {:ok, raw} ->
        Regex.scan(~r/^- ([^:]+):\s*(.+)$/m, raw)
        |> Map.new(fn [_match, key, value] -> {key, String.trim(value)} end)

      _ ->
        %{}
    end
  end

  defp workflow_state_file(data_root, filename) do
    canonical =
      Path.join([
        to_string(data_root),
        "skill",
        "public",
        "two-phase-execution",
        "common",
        "state",
        filename
      ])

    legacy = Path.join([to_string(data_root), "skill", filename])

    if File.exists?(canonical) or not File.exists?(legacy) do
      canonical
    else
      legacy
    end
  end

  defp read_simple_json(path) do
    case File.read(path) do
      {:ok, raw} ->
        case decode_json(raw) do
          {:ok, decoded} when is_map(decoded) -> decoded
          _ -> simple_json_object(raw)
        end

      _ ->
        %{}
    end
  end

  defp simple_json_object(raw) do
    Regex.scan(~r/"([^"]+)"\s*:\s*("[^"]*"|-?\d+|true|false|null)/, raw)
    |> Map.new(fn [_match, key, value] -> {key, simple_json_value(value)} end)
  end

  defp simple_json_value("true"), do: true
  defp simple_json_value("false"), do: false
  defp simple_json_value("null"), do: nil
  defp simple_json_value("\"" <> rest), do: String.trim_trailing(rest, "\"")
  defp simple_json_value(value), do: String.to_integer(value)

  defp active_agents_from_rollup(rollup) do
    by_agent = rollup["by_agent"] || %{}
    Enum.filter(["claude", "codex"], fn agent -> integer_value(by_agent[agent]) > 0 end)
  end

  defp completed_goals(goals), do: Enum.count(goals, &(&1["status"] == "completed"))

  defp compact_token_count(tokens) do
    tokens = max(0, integer_value(tokens))

    cond do
      tokens >= 1_000_000_000 -> compact_unit(tokens, 1_000_000_000, "B")
      tokens >= 1_000_000 -> compact_unit(tokens, 1_000_000, "M")
      tokens >= 1_000 -> compact_unit(tokens, 1_000, "K")
      true -> "#{tokens}"
    end
  end

  defp compact_unit(tokens, threshold, suffix) do
    tenths = div(tokens * 10 + div(threshold, 2), threshold)
    whole = div(tenths, 10)
    fraction = rem(tenths, 10)

    if fraction == 0 do
      "#{whole}#{suffix}"
    else
      "#{whole}.#{fraction}#{suffix}"
    end
  end

  defp event_total_tokens_sum(events), do: Enum.reduce(events, 0, &(&2 + event_total_tokens(&1)))

  defp event_total_tokens(event) do
    explicit_total = integer_field(event, "total_tokens")

    if explicit_total > 0 do
      explicit_total
    else
      integer_field(event, "input_tokens") + integer_field(event, "output_tokens") +
        integer_field(event, "reasoning_output_tokens")
    end
  end

  defp event_datetime(event) when is_map(event) do
    case Map.get(event, :__datetime) do
      %DateTime{} = datetime ->
        datetime

      _ ->
        case string_field(event, "occurred_at") do
          "" ->
            nil

          value ->
            case DateTime.from_iso8601(value) do
              {:ok, datetime, _offset} -> datetime
              _ -> nil
            end
        end
    end
  end

  defp event_datetime(line) when is_binary(line) do
    case string_field(line, "occurred_at") do
      "" ->
        nil

      value ->
        case DateTime.from_iso8601(value) do
          {:ok, datetime, _offset} -> datetime
          _ -> nil
        end
    end
  end

  defp event_local_date(event) when is_map(event) do
    case Map.get(event, :__local_date) do
      %Date{} = date ->
        date

      _ ->
        case event_datetime(event) do
          nil -> nil
          datetime -> local_date(datetime)
        end
    end
  end

  defp event_local_date(_), do: nil

  defp local_today do
    {{year, month, day}, _time} = :calendar.local_time()
    Date.new!(year, month, day)
  end

  defp local_date(%DateTime{} = datetime) do
    datetime
    |> DateTime.shift_zone!("Etc/UTC")
    |> DateTime.to_naive()
    |> NaiveDateTime.to_erl()
    |> :calendar.universal_time_to_local_time()
    |> elem(0)
    |> Date.from_erl!()
  end

  defp source_agent(event) do
    case string_field(event, "source_agent") do
      "" -> string_field(event, "source") |> String.downcase()
      value -> String.downcase(value)
    end
  end

  defp integer_field(event, field) when is_map(event) do
    case Map.get(event, field) do
      nil -> 0
      value when is_integer(value) -> value
      value when is_binary(value) ->
        case Integer.parse(value) do
          {n, _} -> n
          :error -> 0
        end
      _ -> 0
    end
  end

  defp integer_field(line, field) when is_binary(line) do
    pattern = ~r/"#{Regex.escape(field)}"\s*:\s*(\d+)/

    case Regex.run(pattern, line) do
      [_match, value] -> String.to_integer(value)
      _ -> 0
    end
  end

  defp integer_value(value) when is_integer(value), do: value
  defp integer_value(value) when is_binary(value), do: String.to_integer(value)
  defp integer_value(_value), do: 0

  defp float_value(value) when is_float(value), do: value
  defp float_value(value) when is_integer(value), do: value / 1
  defp float_value(value) when is_binary(value), do: String.to_float(value)
  defp float_value(_value), do: 0.0

  defp string_field(event, field) when is_map(event) do
    case Map.get(event, field) do
      nil -> ""
      value when is_binary(value) -> value
      value -> to_string(value)
    end
  end

  defp string_field(line, field) when is_binary(line) do
    pattern = ~r/"#{Regex.escape(field)}"\s*:\s*"([^"]+)"/

    case Regex.run(pattern, line) do
      [_match, value] -> value
      _ -> ""
    end
  end
end
