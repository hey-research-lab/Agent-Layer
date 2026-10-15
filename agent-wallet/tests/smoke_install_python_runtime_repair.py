"""Smoke test repair of a partially created shared Python runtime."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch


def _installer_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "install_agent_wallet.py"
    spec = importlib.util.spec_from_file_location("installer_under_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    installer = _installer_module()
    temp_root = Path(tempfile.mkdtemp(prefix="agent-wallet-runtime-repair-"))
    try:
        runtime_root = temp_root / "runtime" / "releases" / "staging"
        release_venv = runtime_root / "agent-wallet" / ".runtime-venv"
        shared_venv = temp_root / "runtime" / "shared" / "python" / "test-fingerprint" / "venv"
        python_bin = shared_venv / "bin" / "python"
        python_bin.parent.mkdir(parents=True)
        python_bin.touch()

        with (
            patch.object(installer, "_shared_dependency_links_supported", return_value=True),
            patch.object(installer, "_python_runtime_fingerprint", return_value="test-fingerprint"),
            patch.object(installer, "_venv_interpreter_runs", return_value=True),
            patch.object(installer, "_python_runtime_is_healthy", return_value=False),
            patch.object(installer, "_bootstrap_venv_pip") as bootstrap_pip,
            patch.object(installer, "_pip_install_editable") as pip_install,
        ):
            _, created, plan = installer._ensure_python_runtime(
                release_venv,
                temp_root / "package",
                runtime_root,
            )

        assert created is False
        assert plan["action"] == "repair"
        assert bootstrap_pip.call_args.args[0].resolve() == python_bin.resolve()
        assert pip_install.call_args.args[0].resolve() == python_bin.resolve()
        assert pip_install.call_args.args[1] == temp_root / "package"
        assert release_venv.resolve() == shared_venv.resolve()
    finally:
        shutil.rmtree(temp_root)

    _check_broken_interpreter_is_rebuilt(installer)
    _check_real_venv_uses_symlinks_and_starts(installer)
    print("smoke_install_python_runtime_repair: ok")


def _check_broken_interpreter_is_rebuilt(installer) -> None:
    """A cached venv whose interpreter cannot start is rebuilt, not pip-repaired."""
    temp_root = Path(tempfile.mkdtemp(prefix="agent-wallet-runtime-rebuild-"))
    try:
        runtime_root = temp_root / "runtime" / "releases" / "staging"
        release_venv = runtime_root / "agent-wallet" / ".runtime-venv"
        shared_venv = temp_root / "runtime" / "shared" / "python" / "test-fingerprint" / "venv"
        stale = shared_venv / "bin" / "python"
        stale.parent.mkdir(parents=True)
        stale.write_text("broken", encoding="utf-8")
        (shared_venv / "marker").write_text("old", encoding="utf-8")

        def fake_create(path: Path) -> Path:
            python_bin = path / "bin" / "python"
            python_bin.parent.mkdir(parents=True)
            python_bin.touch()
            return python_bin

        with (
            patch.object(installer, "_shared_dependency_links_supported", return_value=True),
            patch.object(installer, "_python_runtime_fingerprint", return_value="test-fingerprint"),
            patch.object(installer, "_venv_interpreter_runs", return_value=False),
            patch.object(installer, "_create_venv", side_effect=fake_create) as create,
            patch.object(installer, "_bootstrap_venv_pip") as bootstrap_pip,
            patch.object(installer, "_pip_install_editable") as pip_install,
        ):
            _, created, plan = installer._ensure_python_runtime(
                release_venv, temp_root / "package", runtime_root
            )

        assert created is True and plan["action"] == "rebuild"
        assert not (shared_venv / "marker").exists(), "the broken venv must be removed first"
        assert create.call_count == 1 and bootstrap_pip.called and pip_install.called
    finally:
        shutil.rmtree(temp_root)


def _check_real_venv_uses_symlinks_and_starts(installer) -> None:
    """The created venv starts; on POSIX its interpreter is a symlink, like python -m venv."""
    temp_root = Path(tempfile.mkdtemp(prefix="agent-wallet-real-venv-"))
    try:
        python_bin = installer._create_venv(temp_root / "venv")
        if os.name != "nt":
            assert python_bin.is_symlink(), "a copied interpreter breaks relocatable Pythons such as uv's"
        result = subprocess.run(
            [str(python_bin), "-c", "import encodings, ssl, venv; print('ok')"],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0 and result.stdout.strip() == "ok", result.stderr
    finally:
        shutil.rmtree(temp_root)


if __name__ == "__main__":
    main()
