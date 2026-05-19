#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail=0

check_no_files() {
  local description="$1"
  shift
  local matches
  matches="$(find "$@" -print 2>/dev/null || true)"
  if [[ -n "$matches" ]]; then
    echo "FAIL $description"
    echo "$matches"
    fail=1
  else
    echo "PASS $description"
  fi
}

check_rg_absent() {
  local description="$1"
  local pattern="$2"
  local matches
  matches="$(rg -n --hidden --glob '!*.pyc' --glob '!elixir/**/_build/**' --glob '!elixir/**/deps/**' --glob '!dist/**' --glob '!build/**' -- "$pattern" . || true)"
  if [[ -n "$matches" ]]; then
    echo "FAIL $description"
    echo "$matches"
    fail=1
  else
    echo "PASS $description"
  fi
}

check_no_files "no generated usage ledgers" ./data -type f
check_no_files "no installed app bundles" . -type d -name '*.app' -prune
check_no_files "no Elixir build/deps artifacts" ./elixir -type d \( -name '_build' -o -name 'deps' \) -prune
check_no_files "no local agent state directories" . -type d \( -name '.codex' -o -name '.claude' -o -name '.agents' \) -prune
check_no_files "no turn artifacts" ./data/turn-artifacts -type f

check_rg_absent "no personal absolute paths" '/Users/peter\.lyoo'
check_rg_absent "no private key blocks" '-----BEGIN (RSA |DSA |EC |OPENSSH |PGP )?PRIVATE KEY-----'
check_rg_absent "no AWS access keys" '\b(AKIA|ASIA)[0-9A-Z]{16}\b'
check_rg_absent "no GitHub tokens" '\bgh[pousr]_[A-Za-z0-9_]{30,}\b'
check_rg_absent "no OpenAI-style API keys" '\bsk-[A-Za-z0-9_-]{20,}\b'
check_rg_absent "no Slack tokens" '\bxox[baprs]-[A-Za-z0-9-]{20,}\b'
check_rg_absent "no Google API keys" '\bAIza[0-9A-Za-z_-]{35}\b'
check_rg_absent "no explicit secret assignments" '(password|passwd|client_secret|api[_-]?key|private[_-]?key)[[:space:]]*[:=][[:space:]]*["'\'']?[A-Za-z0-9_./+=-]{12,}'

if [[ "$fail" -ne 0 ]]; then
  echo "privacy scan failed"
  exit 1
fi

echo "privacy scan passed"
