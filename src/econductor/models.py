from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from econductor.config import atomic_json, data_dir, hardware


@dataclass(frozen=True)
class Preset:
    id: str
    repo: str
    description: str
    estimated_gb: float
    working_gb: float
    minimum_ram_gb: int
    comfortable_ram_gb: int
    experimental: bool = False


PRESETS = {
    p.id: p
    for p in (
        Preset("light", "mlx-community/Qwen3.5-4B-4bit", "Fastest general starting point", 3, 5, 8, 16),
        Preset("balanced", "mlx-community/Qwen3.5-9B-4bit", "General analysis and writing", 6, 9, 16, 24),
        Preset(
            "precision", "mlx-community/Qwen3.5-9B-8bit", "9B with less quantization", 11, 14, 24, 32
        ),
        Preset("large", "mlx-community/Qwen3.5-27B-4bit", "Complex general analysis", 17, 22, 32, 48),
        Preset(
            "coder",
            "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit",
            "Coding and tool use; full MoE weights must fit",
            18,
            23,
            32,
            48,
        ),
        Preset(
            "moe",
            "mlx-community/Qwen3.5-35B-A3B-4bit",
            "Larger general MoE; full weights must fit",
            21,
            28,
            36,
            48,
        ),
        Preset(
            "coder-next",
            "mlx-community/Qwen3-Coder-Next-4bit",
            "Larger coding-focused MoE",
            45,
            55,
            64,
            96,
        ),
        Preset(
            "frontier",
            "mlx-community/Qwen3.5-122B-A10B-4bit",
            "High-capacity general MoE; slower",
            70,
            84,
            96,
            128,
        ),
        Preset(
            "max",
            "mlx-community/Qwen3-235B-A22B-Instruct-2507-3bit",
            "Experimental 128 GB option; very slow/tight",
            104,
            116,
            128,
            128,
            True,
        ),
    )
}


def registry() -> dict:
    path = data_dir() / "models.json"
    return json.loads(path.read_text()) if path.exists() else {}


def validate_model(path: Path) -> None:
    for name in ("config.json", "tokenizer_config.json", "tokenizer.json"):
        file = path / name
        if not file.is_file():
            raise ValueError(f"Incomplete model: missing {name}")
        json.loads(file.read_text())
    config = json.loads((path / "config.json").read_text())
    if "quantization" not in config and "quantization_config" not in config:
        raise ValueError("Use a converted MLX model with quantization configuration.")
    index = path / "model.safetensors.index.json"
    weights = (
        {path / p for p in json.loads(index.read_text())["weight_map"].values()}
        if index.exists()
        else set(path.glob("*.safetensors"))
    )
    if not weights or any(not w.is_file() or w.stat().st_size < 16 for w in weights):
        raise ValueError("Incomplete model weights. Resume the explicit download.")
    for weight in weights:
        # Check the safetensors header and declared tensor extent without loading weights.
        with weight.open("rb") as handle:
            header_size = int.from_bytes(handle.read(8), "little")
            if not 0 < header_size <= 100_000_000:
                raise ValueError(f"Invalid safetensors header: {weight.name}")
            header = json.loads(handle.read(header_size))
        extent = max(
            (v["data_offsets"][1] for k, v in header.items() if k != "__metadata__"), default=0
        )
        if weight.stat().st_size < 8 + header_size + extent:
            raise ValueError(f"Truncated weight file: {weight.name}")


def local_model(model: str) -> Path | None:
    record = registry().get(model)
    if not record:
        return None
    path = Path(record["path"]).resolve()
    try:
        validate_model(path)
    except (ValueError, OSError, KeyError):
        return None
    return path


def register(model: str, path: Path, *, revision: str | None = None) -> None:
    if not model or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in model
    ):
        raise ValueError("Model names may contain only letters, numbers, '-' and '_'.")
    validate_model(path)
    records = registry()
    records[model] = {
        "path": str(path.resolve()),
        "revision": revision,
        "validation": "not evaluated",
    }
    atomic_json(data_dir() / "models.json", records)


def model_rows(info: dict | None = None) -> list[dict]:
    """List presets with conservative, advisory fit labels for this Mac."""
    info = hardware() if info is None else info
    memory = info.get("memory_bytes")
    free_disk = info.get("free_disk_bytes")
    ram_gb = memory / 1024**3 if isinstance(memory, int) else None
    free_disk_gb = free_disk / 1000**3 if isinstance(free_disk, int) else None
    records = registry()
    rows = []
    for preset in PRESETS.values():
        downloaded = local_model(preset.id) is not None
        if ram_gb is None:
            memory_fit = "unknown"
        elif ram_gb < preset.minimum_ram_gb:
            memory_fit = "below minimum"
        elif ram_gb < preset.comfortable_ram_gb or preset.experimental:
            memory_fit = "tight/experimental" if preset.experimental else "tight"
        else:
            memory_fit = "comfortable"
        if downloaded:
            disk_fit = "downloaded"
        elif free_disk_gb is None:
            disk_fit = "unknown"
        elif free_disk_gb < preset.estimated_gb + 1:
            disk_fit = "low disk"
        else:
            disk_fit = "enough disk"
        rows.append(
            {
                **asdict(preset),
                "downloaded": downloaded,
                "validation": records.get(preset.id, {}).get("validation", "not evaluated"),
                "memory_fit": memory_fit,
                "disk_fit": disk_fit,
            }
        )
    rows += [
        {
            "id": name,
            "repo": value["path"],
            "description": "Local model",
            "estimated_gb": 0,
            "working_gb": 0,
            "minimum_ram_gb": 0,
            "comfortable_ram_gb": 0,
            "experimental": False,
            "downloaded": local_model(name) is not None,
            "validation": value.get("validation", "not evaluated"),
            "memory_fit": "unknown",
            "disk_fit": "unknown",
        }
        for name, value in records.items()
        if name not in PRESETS
    ]
    return rows


def get_token() -> str | None:
    import keyring

    try:
        return keyring.get_password("Econductor", "huggingface")
    except keyring.errors.KeyringError:
        return None


def save_token(token: str) -> None:
    import keyring

    keyring.set_password("Econductor", "huggingface", token)


def download(model: str) -> Path:
    """Explicit online helper only. Never import this inside inference/analysis workers."""
    from huggingface_hub import HfApi, snapshot_download

    if model not in PRESETS:
        raise ValueError("Unknown preset; use 'models add' for a local model.")
    preset = PRESETS[model]
    destination = data_dir() / "models" / model
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = get_token() or False  # Never silently use a global HF token.
    info = HfApi(token=token).model_info(preset.repo, files_metadata=True)
    size = sum(
        f.size or 0
        for f in info.siblings
        if f.rfilename.endswith((".safetensors", ".json", ".jinja", ".txt"))
    )
    existing = sum(p.stat().st_size for p in destination.rglob("*") if p.is_file())
    needed = max(0, size - existing) + 512 * 1024**2
    if shutil.disk_usage(destination).free < needed:
        raise ValueError(
            f"Insufficient disk space; need approximately {needed / 1024**3:.1f} GiB free."
        )
    snapshot_download(
        preset.repo,
        revision=info.sha,
        local_dir=destination,
        token=token,
        allow_patterns=["*.safetensors", "*.json", "*.jinja", "*.txt"],
    )
    register(model, destination, revision=info.sha)
    return destination
