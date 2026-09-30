from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import sysconfig
import tempfile
from dataclasses import dataclass
from pathlib import Path

from econductor.config import data_dir

PRIVATE_NAMES = {".git", ".aws", ".ssh", ".codex", ".agents", ".env", ".gnupg"}
DATA_SUFFIXES = {
    ".csv",
    ".tsv",
    ".parquet",
    ".dta",
    ".xlsx",
    ".xls",
    ".rds",
    ".rdata",
    ".sav",
    ".sas7bdat",
    ".duckdb",
    ".db",
    ".sqlite",
}


def safe_environment(work: Path, *, python_path: bool = True) -> dict[str, str]:
    # An allowlist prevents accidental inheritance of API tokens, startup hooks and proxies.
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin",
        "HOME": str(work),
        "TMPDIR": str(work),
        "LANG": "en_US.UTF-8",
        "HF_HUB_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "DO_NOT_TRACK": "1",
        "MPLCONFIGDIR": str(work),
        "MPLBACKEND": "Agg",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "R_ENVIRON_USER": "/dev/null",
        "R_PROFILE_USER": "/dev/null",
        "OPENBLAS_NUM_THREADS": "4",
        "OMP_NUM_THREADS": "4",
    }
    if python_path:
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    return env


@dataclass
class PathGuard:
    project: Path

    def __post_init__(self) -> None:
        self.project = self.project.resolve(strict=True)
        if self.project == Path.home() or self.project == Path("/"):
            raise ValueError(
                "Choose a specific research project, not your home or filesystem root."
            )
        state = self.project / ".econductor"
        if state.is_symlink():
            raise ValueError(".econductor must not be a symlink")

    @property
    def state(self) -> Path:
        return self.project / ".econductor"

    def resolve(self, value: str, *, write: bool = False, source_approved: bool = False) -> Path:
        path = (self.project / value).resolve()
        if not path.is_relative_to(self.project):
            raise ValueError(
                "Path escapes the selected project. Use a path inside the project; copy external data into it if needed. External symlinks are denied."
            )
        relative = path.relative_to(self.project)
        if any(p in PRIVATE_NAMES or p.startswith(".env.") for p in relative.parts):
            raise ValueError("Private configuration and credentials are not accessible.")
        if ".econductor" in relative.parts and not path.is_relative_to(self.state / "artifacts"):
            raise ValueError("Agent session/configuration files are not accessible as tools.")
        if write and not source_approved and path.suffix.lower() in DATA_SUFFIXES and path.exists():
            raise ValueError("Existing source data is protected. Write a new output instead.")
        return path

    def initialize(self) -> None:
        self.state.mkdir(mode=0o700, exist_ok=True)
        for name in ("sessions", "artifacts", "cache", "work"):
            path = self.state / name
            if path.is_symlink():
                raise ValueError(f"Unsafe state directory: {path}")
            path.mkdir(mode=0o700, exist_ok=True)


def _q(value: Path | str) -> str:
    return json.dumps(str(value))


def runtime_roots() -> list[Path]:
    # Do not authorize the whole home directory, Homebrew prefix, or uv cache.
    roots = [Path(sys.base_prefix), Path(sys.prefix), Path(__file__).resolve().parents[1]]
    roots += [
        Path(p).resolve()
        for p in (sysconfig.get_path("stdlib"), sysconfig.get_path("platstdlib"))
        if p
    ]
    for name in (
        "/Library/Frameworks/R.framework",
        "/opt/R",
        "/opt/homebrew/Cellar/r",
        "/usr/local/Cellar/r",
    ):
        if Path(name).exists():
            roots.append(Path(name))
    worker_runtime = data_dir() / "runtime" / "python"
    if worker_runtime.exists():
        roots.append(worker_runtime)
    return list(dict.fromkeys(roots))


def worker_python() -> Path:
    """Create a regular-file Python launcher for macOS's sandboxed workers.

    uv's venv Python is a symlink. macOS 26 rejects exec of that symlink under
    the strict profile, so workers use a hardlink or copy in a private venv.
    """
    runtime = data_dir() / "runtime"
    venv = runtime / "python"
    for directory in (runtime, venv, venv / "bin"):
        if directory.is_symlink():
            raise RuntimeError(f"Unsafe runtime directory: {directory}")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    source = Path(sys.executable).resolve(strict=True)
    launcher = venv / "bin" / "python3"
    if launcher.is_symlink() or not launcher.exists() or not os.path.samefile(launcher, source):
        temporary = launcher.with_name("python3.tmp")
        temporary.unlink(missing_ok=True)
        try:
            os.link(source, temporary)
        except OSError:
            shutil.copy2(source, temporary)
        temporary.replace(launcher)

    config = venv / "pyvenv.cfg"
    site_packages = venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    if site_packages.is_symlink():
        raise RuntimeError(f"Unsafe runtime directory: {site_packages}")
    site_packages.mkdir(mode=0o700, parents=True, exist_ok=True)
    dependency_path = Path(sysconfig.get_path("purelib")).resolve(strict=True)
    contents = f"home = {Path(sys.base_prefix).resolve() / 'bin'}\ninclude-system-site-packages = false\n"
    packages = site_packages / "econductor-dependencies.pth"
    for path, value in ((config, contents), (packages, f"{dependency_path}\n")):
        if path.is_symlink():
            raise RuntimeError(f"Unsafe runtime file: {path}")
        if not path.exists() or path.read_text() != value:
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(value)
            temporary.replace(path)
    return launcher


def sandbox_profile(
    read_roots: list[Path],
    write_roots: list[Path],
    *,
    project: Path | None = None,
    stata: Path | None = None,
    gpu: bool = False,
) -> str:
    reads = [
        Path("/System"),
        Path("/usr/lib"),
        Path("/usr/share"),
        Path("/usr/bin"),
        Path("/bin"),
        Path("/sbin"),
        Path("/usr/sbin"),
        Path("/Library/Apple"),
        Path("/private/var/db/dyld"),
    ]
    runtime = runtime_roots()
    reads += runtime + read_roots + write_roots
    executable_roots = [
        Path("/System"),
        Path("/usr/lib"),
        Path("/usr/bin"),
        Path("/bin"),
        *runtime,
        *write_roots,
    ]
    if stata:
        bundle = next((parent for parent in stata.parents if parent.suffix == ".app"), None)
        stata_root = bundle.parent if bundle else stata.parent.parent
        reads.append(stata_root)
        executable_roots.append(stata_root)
    lines = [
        "(version 3)",
        "(deny default)",
        '(import "system.sb")',
        "(allow syscall*)",
        "(deny network*)",
        "(allow process-fork)",
        "(allow process-exec)",
        "(allow process-info-pidinfo (target self))",
        "(allow process-info-codesignature (target self))",
        "(allow signal (target self))",
        "(allow sysctl-read)",
        "(allow system-fcntl)",
        '(allow file-read* (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random"))',
        '(allow file-write* (literal "/dev/null"))',
        '(allow mach-lookup (global-name "com.apple.MTLCompilerService") (global-name "com.apple.cvmsServ") (global-name "com.apple.system.logger"))',
        '(allow file-read* file-test-existence file-read-metadata (literal "/private/var/select/sh"))',
        "(system-graphics)",
    ]
    if stata:
        lines += [
            '(allow system-mac-syscall (require-all (mac-policy-name "AMFI") (mac-syscall-number 90)))',
            '(allow file-read* file-test-existence file-read-metadata (literal "/private/etc/ssl/openssl.cnf"))',
        ]
    if gpu:
        # Metal enumerates device properties before MLX can load a GPU.
        lines.append("(allow iokit-get-properties)")
    lines += [f"(allow file-read* (subpath {_q(p.resolve())}))" for p in reads]
    lines += [f"(allow file-test-existence (subpath {_q(p.resolve())}))" for p in reads]
    lines += [f"(allow file-read-metadata (subpath {_q(p.resolve())}))" for p in reads]
    lines += [
        f"(allow file-read-metadata file-test-existence (path-ancestors {_q(p.resolve())}))"
        for p in reads
    ]
    lines += [
        f"(allow file-map-executable (subpath {_q(p.resolve())}))"
        for p in executable_roots
    ]
    lines += [f"(allow file-write* (subpath {_q(p.resolve())}))" for p in write_roots]
    # These explicit denials override broad project/runtime permissions.
    for root in [Path.home(), *read_roots, *write_roots]:
        for name in PRIVATE_NAMES | {
            "Library/Keychains",
            "Library/Application Support/Econductor/settings.json",
        }:
            lines.append(f"(deny file-read* file-write* (subpath {_q(root / name)}))")
        escaped = str(root).replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'(deny file-read* file-write* (regex #"^{escaped}/(.*/)?\\.env(\\..*)?$"))')
    if project:
        lines.append(
            f"(deny file-read* file-write* (subpath {_q(project / '.econductor' / 'sessions')}))"
        )
    return "\n".join(lines)


def sandbox_command(command: list[str], profile: str) -> list[str]:
    if platform.system() != "Darwin" or not Path("/usr/bin/sandbox-exec").exists():
        raise RuntimeError("Offline execution requires macOS sandbox-exec. Execution is disabled.")
    return ["/usr/bin/sandbox-exec", "-p", profile, *command]


def _check_worker_policy(*, gpu: bool) -> tuple[bool, str]:
    runner = worker_python()
    program = """import json, socket, sys
from pathlib import Path
approved, secret, work, blocked, port, gpu_enabled = sys.argv[1:]
results = {}
for name, path in (("approved_read", approved), ("outside_read", secret)):
    try:
        Path(path).read_text()
        results[name] = True
    except OSError:
        results[name] = False
for name, path in (("approved_write", Path(work) / "output.txt"), ("outside_write", Path(blocked))):
    try:
        Path(path).write_text("probe")
        results[name] = True
    except OSError:
        results[name] = False
try:
    with socket.socket() as connection:
        results["network_blocked"] = connection.connect_ex(("127.0.0.1", int(port))) != 0
except OSError:
    results["network_blocked"] = True
if gpu_enabled == "1":
    import mlx.core as mx
    results["metal_ready"] = int(mx.sum(mx.array([1, 2, 3]))) == 6
print(json.dumps(results))"""
    with tempfile.TemporaryDirectory(prefix="econductor-sandbox-", dir="/private/tmp") as temporary:
        root = Path(temporary)
        project = root / "project"
        work = project / "work"
        work.mkdir(parents=True)
        approved = project / "data.txt"
        approved.write_text("approved")
        secret = root / "outside.txt"
        secret.write_text("private")
        profile = sandbox_profile([project], [work], project=project, gpu=gpu)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            result = subprocess.run(
                sandbox_command(
                    [
                        str(runner),
                        "-I",
                        "-c",
                        program,
                        str(approved),
                        str(secret),
                        str(work),
                        str(root / "blocked.txt"),
                        str(listener.getsockname()[1]),
                        "1" if gpu else "0",
                    ],
                    profile,
                ),
                env=safe_environment(work),
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        if result.returncode:
            detail = result.stderr.strip() or f"worker exited with status {result.returncode}"
            return False, f"Sandboxed Python worker failed: {detail}"
        try:
            actual = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False, "Sandboxed Python worker returned unreadable policy results."
        expected = {
            "approved_read": True,
            "outside_read": False,
            "approved_write": True,
            "outside_write": False,
            "network_blocked": True,
        }
        if gpu:
            expected["metal_ready"] = True
        if actual != expected:
            return False, f"Sandbox policy check failed: {actual!r}"
        if gpu:
            return True, "macOS sandbox verified: data and Metal workers run; network and outside files blocked."
        return True, "Data worker sandbox verified."


def check_sandbox() -> tuple[bool, str]:
    try:
        profile = sandbox_profile([], [])
        if "(deny default)" not in profile or "(deny network*)" not in profile:
            return False, "Sandbox profile does not enforce default-deny network access."
        result = subprocess.run(
            sandbox_command(["/usr/bin/true"], profile),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            ok, reason = _check_worker_policy(gpu=False)
            return _check_worker_policy(gpu=True) if ok else (ok, reason)
        detail = result.stderr.strip() or f"sandbox-exec probe exited with status {result.returncode}"
        minimal_profile = "\n".join(
            [
                "(version 3)",
                "(deny default)",
                '(import "system.sb")',
                "(allow syscall*)",
                "(deny network*)",
                "(allow process-exec)",
                '(allow file-read* file-test-existence file-map-executable '
                '(subpath "/System") (subpath "/usr/lib") '
                '(subpath "/usr/bin") (subpath "/bin") (subpath "/private/var/db/dyld"))',
            ]
        )
        minimal = subprocess.run(
            sandbox_command(["/usr/bin/true"], minimal_profile),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if minimal.returncode:
            signal_name = (
                signal.Signals(-minimal.returncode).name
                if minimal.returncode < 0
                else f"exit {minimal.returncode}"
            )
            minimal_detail = minimal.stderr.strip() or signal_name
            return False, f"macOS could not apply even a minimal default-deny sandbox ({minimal_detail}); Econductor execution remains disabled."
        return False, f"Minimal default-deny sandbox works, but Econductor's full profile failed ({detail}); execution remains disabled."
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        return False, str(error)


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()
