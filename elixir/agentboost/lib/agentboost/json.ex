defmodule Agentboost.JSON do
  @moduledoc false

  def encode!(value), do: encode(value)

  defp encode(value) when is_binary(value), do: inspect(value)
  defp encode(value) when is_integer(value), do: Integer.to_string(value)
  defp encode(value) when is_float(value), do: Float.to_string(value)
  defp encode(true), do: "true"
  defp encode(false), do: "false"
  defp encode(nil), do: "null"

  defp encode(value) when is_list(value) do
    "[" <> Enum.map_join(value, ",", &encode/1) <> "]"
  end

  defp encode(value) when is_map(value) do
    entries =
      value
      |> Enum.sort_by(fn {key, _item} -> to_string(key) end)
      |> Enum.map_join(",", fn {key, item} -> encode(to_string(key)) <> ":" <> encode(item) end)

    "{" <> entries <> "}"
  end
end
