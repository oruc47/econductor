from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from platformdirs import user_data_path


def data_dir() -> Path:
    override = os.environ.get("ECONDUCTOR_HOME")
    return Path(override).expanduser().resolve() if override else user_data_path("Econductor")


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


@dataclass
class Settings:
    configured: bool = False
    model: str = "balanced"
    context_tokens: int = 16384
    generation_tokens: int = 4096
    timeout_seconds: int = 300
    stata_path: str | None = None
    duckdb_memory: str = "2GB"

    @classmethod
    def load(cls) -> Settings:
        path = data_dir() / "settings.json"
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        settings = cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})
        if not 2048 <= settings.context_tokens <= 131072:
            raise ValueError("context_tokens must be between 2048 and 131072")
        if not 256 <= settings.generation_tokens < settings.context_tokens:
            raise ValueError("generation_tokens must be smaller than context_tokens")
        if not 1 <= settings.timeout_seconds <= 86400:
            raise ValueError("timeout_seconds must be between 1 and 86400")
        return settings

    def save(self) -> None:
        atomic_json(data_dir() / "settings.json", asdict(self))


def find_stata(explicit: str | None = None) -> str | None:
    candidates = [explicit] if explicit else []
    candidates += [shutil.which(name) for name in ("stata-mp", "stata-se", "stata")]
    candidates += [str(p) for p in Path("/Applications").glob("Stata*/**/MacOS/stata*")]
    for value in candidates:
        if value and Path(value).is_file() and os.access(value, os.X_OK):
            return str(Path(value).resolve())
    return None


def find_rscript() -> str | None:
    candidates = [shutil.which("Rscript")]
    candidates += [
        "/Library/Frameworks/R.framework/Resources/bin/Rscript",
        "/opt/homebrew/bin/Rscript",
        "/usr/local/bin/Rscript",
    ]
    for value in candidates:
        if value and Path(value).is_file() and os.access(value, os.X_OK):
            return str(Path(value).resolve())
    return None


def hardware() -> dict:
    info = {"platform": platform.system(), "architecture": platform.machine()}
    if platform.system() == "Darwin":
        info["macos_version"] = platform.mac_ver()[0]
        for key, name in (("hw.memsize", "memory_bytes"), ("machdep.cpu.brand_string", "chip")):
            result = subprocess.run(
                ["/usr/sbin/sysctl", "-n", key], capture_output=True, text=True, check=False
            )
            value = result.stdout.strip()
            info[name] = int(value) if value.isdigit() else value
    info["free_disk_bytes"] = shutil.disk_usage(
        data_dir().parent if data_dir().parent.exists() else Path.home()
    ).free
    return info
