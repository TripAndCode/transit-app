"""poetry.lock keeps Linux torch on the CPU-only index.

PyPI's Linux torch wheel pulls the CUDA runtime and triton, several GB that no
host here can use. A relock that resolves Linux torch from PyPI would still
pass every other check, so the lock itself is asserted: any torch entry that
can install on Linux comes from `pytorch-cpu` with a `+cpu` version, and every
nvidia-*/triton entry is excluded on Linux.
"""

from __future__ import annotations

from pathlib import Path

import tomllib
from packaging.markers import Marker

LOCK = Path(__file__).resolve().parents[2] / "poetry.lock"

_LINUX_ENVIRONMENTS = [
    {
        "sys_platform": "linux",
        "platform_system": "Linux",
        "platform_machine": machine,
        "python_version": python_version,
        "implementation_name": "cpython",
        "os_name": "posix",
        "platform_python_implementation": "CPython",
    }
    for machine in ("x86_64", "aarch64")
    for python_version in ("3.11", "3.12", "3.13", "3.14")
]


def _packages() -> list[dict]:
    return tomllib.loads(LOCK.read_text(encoding="utf-8"))["package"]


def _marker_strings(package: dict) -> list[str]:
    markers = package.get("markers")
    if markers is None:
        return []
    return list(markers.values()) if isinstance(markers, dict) else [markers]


def _installs_on_linux(package: dict) -> bool:
    """Whether any Linux environment satisfies the entry's markers (an entry
    without markers installs everywhere)."""
    strings = _marker_strings(package)
    if not strings:
        return True
    return any(Marker(m).evaluate(env) for m in strings for env in _LINUX_ENVIRONMENTS)


def test_linux_torch_comes_from_the_cpu_index():
    linux_torch = [p for p in _packages() if p["name"] == "torch" and _installs_on_linux(p)]
    assert linux_torch, "no torch entry installs on Linux; the lock no longer pins one"
    for package in linux_torch:
        assert package.get("source", {}).get("reference") == "pytorch-cpu", package["version"]
        assert package["version"].endswith("+cpu"), package["version"]


def test_cuda_runtime_wheels_never_install_on_linux():
    cuda = [p for p in _packages() if p["name"].startswith("nvidia-") or p["name"] == "triton"]
    leaking = [f"{p['name']} {p['version']}" for p in cuda if _installs_on_linux(p)]
    assert leaking == []
