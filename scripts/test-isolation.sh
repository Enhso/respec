#!/usr/bin/env bash
# ISC-4: run every test suite with HOME pointed at an empty temporary
# directory, and fail if any test wrote under it. Toolchains and caches keep
# their real locations; only HOME moves. The web app has no tests yet.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"

export CARGO_HOME="${CARGO_HOME:-$HOME/.cargo}"
export RUSTUP_HOME="${RUSTUP_HOME:-$HOME/.rustup}"
UV_CACHE_DIR="$(uv cache dir)"
UV_PYTHON_INSTALL_DIR="$(uv python dir)"
export UV_CACHE_DIR UV_PYTHON_INSTALL_DIR

fake_home="$(mktemp -d)"
trap 'rm -rf "$fake_home"' EXIT

HOME="$fake_home" cargo test --locked --manifest-path "$root/Cargo.toml"
HOME="$fake_home" uv run --frozen --directory "$root/python" pytest

leftovers="$(find "$fake_home" -mindepth 1)"
if [[ -n "$leftovers" ]]; then
  echo "tests wrote under HOME:" >&2
  echo "$leftovers" >&2
  exit 1
fi
echo "test isolation ok: HOME untouched"
