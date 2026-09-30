#!/bin/sh
set -eu
cd "$(dirname "$0")"
repo_dir=$(pwd -P)
if ! command -v uv >/dev/null 2>&1; then
  echo 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/'
  exit 1
fi
if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
  echo 'Econductor v1 requires an Apple Silicon Mac.'
  exit 1
fi
uv sync --frozen

# Keep the locked project environment, but expose its command in uv's user bin.
# This lets `econductor` use the caller's current directory as the project.
launcher_dir=$(uv tool dir --bin)
launcher="$launcher_dir/econductor"
target="$repo_dir/.venv/bin/econductor"
mkdir -p "$launcher_dir"
if [ ! -x "$target" ]; then
  echo "Econductor was not installed at $target" >&2
  exit 1
fi
if [ -L "$launcher" ]; then
  if [ "$(readlink "$launcher")" != "$target" ]; then
    echo "An Econductor launcher already points elsewhere: $launcher" >&2
    echo "Remove that launcher or use its existing installation before rerunning setup." >&2
    exit 1
  fi
elif [ -e "$launcher" ]; then
  echo "An Econductor command already exists at $launcher; it was not replaced." >&2
  exit 1
else
  ln -s "$target" "$launcher"
fi

case ":$PATH:" in
  *":$launcher_dir:"*) ;;
  *)
    if ! uv tool update-shell; then
      echo "Add $launcher_dir to PATH to run econductor from any folder." >&2
    fi
    echo 'Open a new Terminal window after setup so the command is on PATH.'
    ;;
esac

"$launcher" setup
echo 'Ready. In a research-project folder, run: econductor'
