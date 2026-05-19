defmodule Agentboost.MixProject do
  use Mix.Project

  def project do
    [
      app: :agentboost,
      version: "0.1.0",
      elixir: "~> 1.17",
      start_permanent: Mix.env() == :prod,
      deps: [],
      escript: [main_module: Agentboost.CLI],
      releases: [
        agentboost: [
          include_executables_for: [:unix],
          applications: [runtime_tools: :permanent]
        ]
      ]
    ]
  end

  def application do
    [
      extra_applications: [:logger]
    ]
  end
end
