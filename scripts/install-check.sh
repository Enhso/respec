#!/usr/bin/env bash
# Prove install.sh on a macOS runner, against a release bundle, in a fresh
# home directory.
#
# Usage: scripts/install-check.sh <tarball>
#
# 1. HOME becomes a new temp directory.
# 2. First run of install.sh with a minimal PATH, so uv is missing and gets
#    installed, as on the friend's Mac. Require 200 from /api/health and /.
# 3. Save a fake key, then run install.sh again (idempotency).
# 4. Require exactly one plist, a loaded agent and the fake key's last four.
# 5. Kill the respec process and require that launchd restarts it (KeepAlive).
#
# The agent is always booted out on exit, and the respec log is printed if
# anything failed. This registers a real LaunchAgent: run it on a throwaway
# macOS runner, not on a machine you use.
set -euo pipefail

port=7377
base="http://127.0.0.1:$port"
label="io.github.enhso.respec"
minimal_path="/usr/bin:/bin:/usr/sbin:/sbin"
fake_key="sk-or-install-check-wxyz"

root="$(cd "$(dirname "$0")/.." && pwd)"
fake_home=""

cleanup() {
  local status=$?
  if [[ "$status" -ne 0 && -n "$fake_home" && -f "$fake_home/Library/Logs/Respec/respec.log" ]]; then
    echo "--- respec log ---" >&2
    cat "$fake_home/Library/Logs/Respec/respec.log" >&2
  fi
  launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1 || true
  if [[ -n "$fake_home" ]]; then
    rm -rf "$fake_home"
  fi
}
trap cleanup EXIT

fail() {
  echo "install check failed: $*" >&2
  exit 1
}

status() { curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$1" || true; }

run_install() {
  env PATH="$minimal_path" RESPEC_BUNDLE="$tarball" "$root/install.sh"
}

[[ $# -eq 1 && -f "$1" ]] || { echo "usage: $0 <tarball>" >&2; exit 2; }
tarball="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
[[ "$(uname -s)" == Darwin ]] || fail "this registers a LaunchAgent and needs macOS"
[[ "$(status "$base/api/health")" == "000" ]] || fail "something already answers on $base"

# 1. A fresh home, resolved to its physical path so the process's command
#    line matches what pgrep is given below (macOS temp dirs sit behind the
#    /var symlink).
fake_home="$(cd "$(mktemp -d)" && pwd -P)"
export HOME="$fake_home"
app_bin="$HOME/Library/Application Support/Respec/app/respec"
echo "fresh HOME: $HOME"

# 2. First run, with uv out of reach.
env PATH="$minimal_path" sh -c 'command -v uv' >/dev/null 2>&1 &&
  fail "uv is on the minimal PATH, so the uv install would not run"
echo "--- first install ---"
run_install
[[ -x "$HOME/.local/bin/uv" ]] || fail "install.sh did not install uv under HOME"
echo "uv installed under the fresh HOME"

health="$(status "$base/api/health")"
page="$(status "$base/")"
echo "GET /api/health -> $health"
echo "GET / -> $page"
[[ "$health" == "200" ]] || fail "/api/health answered $health"
[[ "$page" == "200" ]] || fail "/ answered $page"

# 3. Save a key, then install again.
saved="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -X POST \
  -H "Origin: $base" -H 'Content-Type: application/json' \
  -d "{\"openrouter\":\"$fake_key\"}" "$base/api/settings/keys" || true)"
[[ "$saved" == "200" ]] || fail "saving a key answered $saved"
echo "--- second install ---"
run_install

# 4. One agent, still loaded, keys kept.
plist="$HOME/Library/LaunchAgents/$label.plist"
count="$(find "$HOME/Library/LaunchAgents" -maxdepth 1 -name '*.plist' | wc -l | tr -d ' ')"
[[ "$count" == "1" && -f "$plist" ]] || fail "expected exactly one plist ($plist), found $count"
plutil -lint "$plist"
launchctl print "gui/$(id -u)/$label" >/dev/null || fail "launchctl print gui/$(id -u)/$label failed"
echo "one plist, agent loaded"

settings="$(curl -s --max-time 5 "$base/api/settings" || true)"
echo "GET /api/settings -> $settings"
[[ "$settings" == *'"openrouter":"wxyz"'* ]] || fail "the saved key's last four are gone after the second run"
[[ "$settings" != *"$fake_key"* ]] || fail "/api/settings shows the whole key"
[[ "$(status "$base/api/health")" == "200" ]] || fail "Respec does not answer after the second run"

# 5. Kill it; KeepAlive must bring it back.
old_pid="$(pgrep -f "^$app_bin\$" || true)"
[[ -n "$old_pid" && "$old_pid" != *$'\n'* ]] || fail "expected one respec process at $app_bin, found: ${old_pid:-none}"
kill "$old_pid"
restarted=""
for _ in $(seq 100); do
  new_pid="$(pgrep -f "^$app_bin\$" || true)"
  if [[ -n "$new_pid" && "$new_pid" != "$old_pid" && "$(status "$base/api/health")" == "200" ]]; then
    restarted=yes
    break
  fi
  sleep 0.2
done
[[ -n "$restarted" ]] || fail "respec did not come back within 20 s after pid $old_pid was killed"
echo "killed pid $old_pid, launchd restarted it as pid $new_pid"
echo "install check ok"
