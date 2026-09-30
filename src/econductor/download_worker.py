"""Explicit online operation; input is a preset name, never a project or prompt."""

import os
import sys

os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["DO_NOT_TRACK"] = "1"
os.environ["HF_HUB_OFFLINE"] = "0"

from econductor.models import download

if __name__ == "__main__":
    try:
        print(download(sys.argv[1]), flush=True)
    except Exception as error:
        print(f"Download failed: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        sys.exit(1)
