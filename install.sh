#!/usr/bin/env bash
# Install Respec on an Apple Silicon Mac. Run it once, from Terminal:
#
#   curl -fsSL https://raw.githubusercontent.com/Enhso/respec/main/install.sh | bash
#
# Fetch it with curl, never through a browser: a browser marks downloads as
# quarantined, and macOS then blocks the binary until it is approved by hand.
#
# It installs uv if missing, downloads and unpacks the latest release bundle
# into ~/Library/Application Support/Respec/app, and runs `respec setup`,
# which registers a LaunchAgent so Respec starts at every login. Running it
# again is safe: it replaces the app directory and the agent, and leaves
# everything else in ~/Library/Application Support/Respec (the saved keys)
# alone.
#
# Environment:
#   RESPEC_BUNDLE                     a release tarball, as a local path or a
#                                     URL, instead of the latest release
#   RESPEC_INSTALL_SKIP_PLATFORM_CHECK  set to skip only the Mac check, to
#                                     test the download and unpack steps on
#                                     another OS (setup itself still refuses)
#
# Written for the bash 3.2 that macOS ships.
set -euo pipefail

repo="Enhso/respec"
address="http://127.0.0.1:7377"

die() {
  echo "install failed: $*" >&2
  exit 1
}

main() {
  if [[ -z "${RESPEC_INSTALL_SKIP_PLATFORM_CHECK:-}" ]]; then
    if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
      die "Respec installs only on a Mac with Apple Silicon (this is $(uname -s) $(uname -m))."
    fi
  fi

  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT

  # 1. uv, which setup uses to install the worker's Python environment.
  uv=""
  if command -v uv >/dev/null 2>&1; then
    uv="$(command -v uv)"
  elif [[ -x "$HOME/.local/bin/uv" ]]; then
    uv="$HOME/.local/bin/uv"
  else
    echo "Installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
    [[ -x "$HOME/.local/bin/uv" ]] || die "uv installed, but not at $HOME/.local/bin/uv."
    uv="$HOME/.local/bin/uv"
  fi
  export RESPEC_UV="$uv"

  # 2. The release bundle: a given tarball, or the latest release's asset.
  bundle_src="${RESPEC_BUNDLE:-}"
  if [[ -z "$bundle_src" ]]; then
    echo "Finding the latest Respec release"
    release="$(curl -fsSL "https://api.github.com/repos/$repo/releases/latest")" ||
      die "could not read the latest release from GitHub."
    bundle_src="$(printf '%s\n' "$release" |
      grep -o '"browser_download_url": *"[^"]*-aarch64-apple-darwin\.tar\.gz"' |
      sed -e 's/^[^:]*: *"//' -e 's/"$//' |
      sed -n 1p || true)"
    [[ -n "$bundle_src" ]] || die "the latest release has no Apple Silicon bundle."
  fi
  case "$bundle_src" in
    http://* | https://*)
      echo "Downloading $bundle_src"
      tarball="$work/bundle.tar.gz"
      curl -fsSL -o "$tarball" "$bundle_src" || die "could not download $bundle_src."
      ;;
    *)
      [[ -f "$bundle_src" ]] || die "RESPEC_BUNDLE is not a file: $bundle_src"
      tarball="$bundle_src"
      ;;
  esac

  # 3. Unpack, then replace the app directory wholesale. Nothing else under
  #    "Respec" is touched: keys.json lives beside app/.
  mkdir "$work/unpack"
  tar -xzf "$tarball" -C "$work/unpack" || die "could not unpack the bundle."
  bundle=""
  count=0
  for dir in "$work/unpack"/*/; do
    [[ -d "$dir" ]] || continue
    count=$((count + 1))
    bundle="${dir%/}"
  done
  [[ "$count" -eq 1 && -x "$bundle/respec" ]] ||
    die "the bundle should hold one directory with a respec binary in it."

  support="$HOME/Library/Application Support/Respec"
  app="$support/app"
  mkdir -p "$support"
  rm -rf "$app"
  mv "$bundle" "$app"

  # 4. Install the worker and register the login agent.
  "$app/respec" setup </dev/null

  echo
  echo "Respec is installed. Bookmark this address: $address"
}

main "$@"
