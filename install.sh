#!/bin/sh
# Install the current GitHub main branch for a user who does not have a clone.
set -eu

if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
  echo 'Econductor requires an Apple Silicon Mac.' >&2
  exit 1
fi
if ! (: </dev/tty) 2>/dev/null; then
  echo 'Run this installer in Terminal.app so the setup wizard can read your answers.' >&2
  exit 1
fi

install_dir="$HOME/.local/share/econductor"
if [ -e "$install_dir" ] || [ -L "$install_dir" ]; then
  if [ ! -f "$install_dir/bootstrap.sh" ]; then
    echo "Installation path already exists and is not Econductor: $install_dir" >&2
    exit 1
  fi
  echo "Econductor is already downloaded at $install_dir; running setup again."
else
  tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/econductor-install.XXXXXX")
  trap 'rm -rf "$tmp_dir"' EXIT HUP INT TERM
  archive="$tmp_dir/econductor.tar.gz"
  stage="$tmp_dir/econductor"
  mkdir "$stage"
  echo 'Downloading Econductor from GitHub...'
  curl -fLsS --retry 3 \
    'https://github.com/oruc47/econductor/archive/refs/heads/main.tar.gz' \
    -o "$archive"
  tar -xzf "$archive" -C "$stage" --strip-components=1
  if [ ! -f "$stage/pyproject.toml" ] || [ ! -f "$stage/uv.lock" ] ||
     [ ! -f "$stage/bootstrap.sh" ] || [ ! -f "$stage/src/econductor/cli.py" ]; then
    echo 'The downloaded archive is missing required Econductor files.' >&2
    exit 1
  fi
  mkdir -p "$(dirname "$install_dir")"
  mv "$stage" "$install_dir"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo 'Installing uv...'
  original_path=$PATH
  # Download first so a network failure cannot be hidden by a shell pipeline.
  if [ "${tmp_dir:-}" = '' ]; then
    tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/econductor-install.XXXXXX")
    trap 'rm -rf "$tmp_dir"' EXIT HUP INT TERM
  fi
  curl -fLsS --retry 3 'https://astral.sh/uv/install.sh' -o "$tmp_dir/uv-install.sh"
  UV_INSTALL_DIR="$HOME/.local/bin" sh "$tmp_dir/uv-install.sh"
  # The official installer may change the next shell's PATH. Ensure uv's
  # command directory is configured even if it did not.
  if ! PATH="$original_path" "$HOME/.local/bin/uv" tool update-shell; then
    echo 'If econductor is not found in a new Terminal, run uv tool update-shell.' >&2
  fi
  PATH="$HOME/.local/bin:$PATH"
  export PATH
  if ! command -v uv >/dev/null 2>&1; then
    echo 'uv installation did not produce an executable in ~/.local/bin.' >&2
    exit 1
  fi
fi

echo "Installing Econductor from $install_dir..."
"$install_dir/bootstrap.sh" </dev/tty
