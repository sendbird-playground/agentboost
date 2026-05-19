defmodule Agentboost.CLI do
  @moduledoc false

  def main(args) do
    case args do
      ["--check"] ->
        IO.puts("OK agentboost elixir runtime")
        0

      ["--state-json"] ->
        Agentboost.Runtime.state()
        |> Agentboost.JSON.encode!()
        |> IO.puts()

        0

      ["--state-json", "--data-root", data_root] ->
        [data_root: data_root]
        |> Agentboost.Runtime.state()
        |> Agentboost.JSON.encode!()
        |> IO.puts()

        0

      _ ->
        IO.puts(:stderr, "usage: agentboost --check | --state-json [--data-root PATH]")
        2
    end
  end
end
