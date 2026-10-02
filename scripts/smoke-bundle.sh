#!/usr/bin/env bash
# Smoke-test a release bundle the way an installer would use it.
#
# Usage: scripts/smoke-bundle.sh <tarball>
#
# 1. Refuse an archive that lists a key, .env, corpus text or build residue.
# 2. Unpack it, `uv sync --frozen` its worker project, and run the worker.
# 3. Start ./respec from the bundle root with every path left at its default
#    (only RESPEC_CONFIG_DIR is set, to a temp dir) and require 200 from
#    /api/health and from the page.
set -euo pipefail

port=7377
base="http://127.0.0.1:$port"

work=""
pid=""
cleanup() {
  if [[ -n "$pid" ]]; then
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  fi
  if [[ -n "$work" ]]; then
    rm -rf "$work"
  fi
}
trap cleanup EXIT

fail() {
  echo "smoke failed: $*" >&2
  if [[ -n "$work" && -f "$work/respec.log" ]]; then
    echo "--- respec log ---" >&2
    cat "$work/respec.log" >&2
  fi
  exit 1
}

[[ $# -eq 1 && -f "$1" ]] || { echo "usage: $0 <tarball>" >&2; exit 2; }
tarball="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"

# 1. Nothing secret or heavy may ship.
forbidden="$(tar -tzf "$tarball" | grep -E '\.env|keys\.json|corpus|\.venv|__pycache__|node_modules' || true)"
[[ -z "$forbidden" ]] || fail "archive lists forbidden paths:"$'\n'"$forbidden"
echo "archive listing clean"

# 2. Unpack, then sync and run the worker.
work="$(mktemp -d)"
tar -xzf "$tarball" -C "$work"
tops=("$work"/*/)
[[ ${#tops[@]} -eq 1 ]] || fail "expected one top-level directory in the archive"
bundle="${tops[0]%/}"
name="$(basename "$bundle")"
[[ -x "$bundle/respec" ]] || fail "$name/respec is missing or not executable"

uv sync --frozen --directory "$bundle/python"
"$bundle/python/.venv/bin/respec-worker" --version

# 3. Start the binary, with RESPEC_WEB_DIR and RESPEC_WORKER unset so the
#    cwd-relative defaults are what gets exercised.
status() { curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$1" || true; }

[[ "$(status "$base/api/health")" == "000" ]] || fail "something already answers on $base"

mkdir "$work/config"
(
  cd "$bundle"
  exec env -u RESPEC_WEB_DIR -u RESPEC_WORKER RESPEC_CONFIG_DIR="$work/config" ./respec
) >"$work/respec.log" 2>&1 &
pid=$!

up=""
for _ in $(seq 50); do
  kill -0 "$pid" 2>/dev/null || fail "respec exited during startup"
  if [[ "$(status "$base/api/health")" == "200" ]]; then
    up=yes
    break
  fi
  sleep 0.2
done
[[ -n "$up" ]] || fail "respec did not answer on $base within 10 s"

health="$(status "$base/api/health")"
page="$(curl -s -o "$work/page.html" -w '%{http_code}' --max-time 5 "$base/" || true)"
echo "GET /api/health -> $health"
echo "GET / -> $page"
[[ "$health" == "200" ]] || fail "/api/health answered $health"
[[ "$page" == "200" ]] || fail "/ answered $page"
grep -qF '<div id="root">' "$work/page.html" || fail "page lacks <div id=\"root\">"
echo "page holds <div id=\"root\">"
echo "smoke ok: $name"
