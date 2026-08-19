"""Assembles the region builder's Lambda package.

The builder needs DuckDB and Pillow — tens of megabytes of native wheels — plus
the extraction pipeline and the shared runtime package. CDK's usual answer is
Docker bundling, which this project does not use, so the package is staged here
with pip's cross-platform download instead. That works from any host, needs no
container runtime, and is entirely deterministic given a lock of versions.

The result is a plain directory that `lambda_.Code.from_asset` uploads:

    <staging>/
      handler.py          the builder entry point
      place.py            Overture division lookup
      data/pipeline/      the extraction pipeline
      stepwise/           the shared runtime package (container, catalog, config)
      duckdb/ PIL/ ...    native dependencies for the Lambda's architecture
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

#: Pinned so a rebuild is reproducible and a surprise upstream release cannot
#: change what deploys.
RUNTIME_DEPENDENCIES = ("duckdb==1.5.5", "pillow==12.3.0", "requests==2.34.2")

#: Matches the Lambda's architecture. Graviton is cheaper per GB-second and
#: every dependency here publishes an aarch64 wheel.
#:
#: The glibc floor is 2.28, not the older 2.17/manylinux2014, because that is
#: what DuckDB ships. Lambda's Python 3.13 runtime is Amazon Linux 2023 with
#: glibc 2.34, so the newer tag is satisfied comfortably.
PIP_PLATFORM = "manylinux_2_28_aarch64"
UV_PLATFORM = "aarch64-manylinux_2_28"
TARGET_PYTHON = "3.13"

#: Files that would only bloat the upload.
EXCLUDE = shutil.ignore_patterns(
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".pytest_cache",
    "cache",
    "data",
    "*.spw",
    "*.parquet",
    "tests",
    "*.dist-info",
    "*.egg-info",
)


def build(root: Path, staging: Path, install_dependencies: bool = True) -> Path:
    """Stage the builder package and return its directory.

    ``install_dependencies`` is off in tests, where synthesising the stack
    should not spend thirty seconds downloading wheels.
    """
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    for name in ("handler.py", "place.py"):
        shutil.copy2(root / "builder" / name, staging / name)

    shutil.copytree(root / "data" / "pipeline", staging / "data" / "pipeline", ignore=EXCLUDE)
    (staging / "data" / "__init__.py").write_text('"""Namespace for the extraction pipeline."""\n')

    # The runtime package, minus the baked datasets: the builder produces those
    # rather than reading them, and they would add 25 MB to every upload.
    shutil.copytree(root / "api" / "stepwise", staging / "stepwise", ignore=EXCLUDE)

    if install_dependencies:
        _install(staging)

    total = sum(f.stat().st_size for f in staging.rglob("*") if f.is_file())
    print(f"builder bundle staged at {staging} ({total / 1e6:.1f} MB)")
    return staging


def _install(staging: Path) -> None:
    """Download dependency wheels for the Lambda's platform, not this host's.

    Prefers `uv`, which this project already uses and which is what CI has;
    falls back to `pip` so the bundle can be staged from a bare Python. Either
    way the install is cross-platform — wheels for Linux aarch64 are fetched
    regardless of the host — which is how this avoids needing Docker.
    """
    for command in (_uv_command(staging), _pip_command(staging)):
        if command is None:
            continue
        try:
            subprocess.run(command, check=True)
            return
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            print(f"  dependency install via {command[0]} failed: {exc}")
    raise RuntimeError(
        "could not stage builder dependencies; install `uv` or ensure pip is available"
    )


def _uv_command(staging: Path) -> list[str] | None:
    """The `uv pip install` form, if uv is on PATH."""
    uv = shutil.which("uv")
    if uv is None:
        return None
    return [
        uv,
        "pip",
        "install",
        "--quiet",
        "--target",
        str(staging),
        "--python-platform",
        UV_PLATFORM,
        "--python-version",
        TARGET_PYTHON,
        "--only-binary",
        ":all:",
        *RUNTIME_DEPENDENCIES,
    ]


def _pip_command(staging: Path) -> list[str]:
    """The `pip install` form, used when uv is unavailable.

    Nothing is trimmed from the result afterwards. An earlier version tried to
    delete `tests/` and `*.dist-info` directories, in unreachable code after
    this return — so it never ran, and the 27 MB package it produces is
    comfortably inside Lambda's 250 MB unzipped limit anyway. Deleting
    `*.dist-info` would also break any dependency that reads its own version
    through `importlib.metadata`.
    """
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--quiet",
        "--target",
        str(staging),
        "--platform",
        PIP_PLATFORM,
        "--python-version",
        TARGET_PYTHON.replace(".", ""),
        "--implementation",
        "cp",
        # Wheels only: a source distribution would build for the host and
        # produce a package that cannot run on Lambda.
        "--only-binary=:all:",
        "--upgrade",
        *RUNTIME_DEPENDENCIES,
    ]
