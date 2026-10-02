#!/usr/bin/env bash
# Build the release bundle for one target and write it to dist/.
#
# Usage: scripts/bundle.sh [target-triple]   (default: the host's triple)
#
# The bundle mirrors the repo, so `./respec` run from its root works with
# every cwd-relative default: web/dist for the UI, python/.venv/bin/respec-worker
# for the worker once `uv sync --frozen` has run in python/.
#
#   respec-<version>-<target>/
#     respec  web/dist/  python/{pyproject.toml,uv.lock,src/}  LICENSE  README.md
#
# Every build step is locked (ISC-48). The last line printed is the tarball path.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
target="${1:-$(rustc -vV | sed -n 's/^host: //p')}"
version="$(awk -F'"' '/^version = /{print $2; exit}' "$root/Cargo.toml")"
name="respec-$version-$target"

cargo build --release --locked --target "$target" --manifest-path "$root/Cargo.toml"
(cd "$root/web" && npm ci && npm run build)

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
bundle="$stage/$name"

mkdir -p "$bundle/web" "$bundle/python"
cp "${CARGO_TARGET_DIR:-$root/target}/$target/release/respec" "$bundle/respec"
cp -R "$root/web/dist" "$bundle/web/dist"
cp "$root/python/pyproject.toml" "$root/python/uv.lock" "$bundle/python/"
cp -R "$root/python/src" "$bundle/python/src"
find "$bundle/python/src" -name __pycache__ -prune -exec rm -rf {} +
cp "$root/LICENSE" "$root/README.md" "$bundle/"

mkdir -p "$root/dist"
# COPYFILE_DISABLE keeps macOS tar from adding ._ metadata entries.
COPYFILE_DISABLE=1 tar -czf "$root/dist/$name.tar.gz" -C "$stage" "$name"

echo "$root/dist/$name.tar.gz"
