"""The open-core boundary (ADR 0004, ADR 0011): the Apache-2.0 core never imports the
Enterprise package. Enterprise code reaches the core only through the extension registry."""

import ast
import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "workmind"
ENTERPRISE = "workmind_ee"


def _imports(tree: ast.AST) -> list[tuple[int, str]]:
    """Every module name an import statement or a dynamic import call names."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.lineno, node.module))
        elif isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name in {"__import__", "importlib.import_module", "import_module"} and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found.append((node.lineno, first.value))
    return found


def test_no_core_module_imports_the_enterprise_package() -> None:
    files = sorted(SRC.rglob("*.py"))
    assert len(files) > 100  # the scan really covers the core
    offenders = [
        f"{path.relative_to(SRC.parent)}:{line} imports {module}"
        for path in files
        for line, module in _imports(ast.parse(path.read_text(), str(path)))
        if module == ENTERPRISE or module.startswith(f"{ENTERPRISE}.")
    ]
    assert offenders == []


def test_the_scan_catches_every_import_form() -> None:
    sample = ast.parse(
        "import workmind_ee\n"
        "from workmind_ee.retention import apply\n"
        "import importlib\n"
        "importlib.import_module('workmind_ee.budgets')\n"
        "__import__('workmind_ee')\n"
    )
    assert [m for _, m in _imports(sample) if m.startswith(ENTERPRISE)] == [
        "workmind_ee",
        "workmind_ee.retention",
        "workmind_ee.budgets",
        "workmind_ee",
    ]


RUNTIME_CHECK = """
import importlib, pkgutil, sys
import workmind
for module in pkgutil.walk_packages(workmind.__path__, "workmind."):
    if not module.name.endswith("__main__"):
        importlib.import_module(module.name)
from workmind.core.config import Settings
from workmind.main import create_app
create_app(Settings(_env_file=None, app_env="test", authz_outbox_worker=False))
loaded = sorted(m for m in sys.modules if m.split(".")[0] == "workmind_ee")
assert not loaded, loaded
print("ok", len([m for m in sys.modules if m.startswith("workmind.")]))
"""


def test_a_community_process_never_loads_the_enterprise_package(tmp_path: Path) -> None:
    """Every core module imported and the app built, with the ee extra installed or not."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LICENSE_", "DEPLOYMENT_ID"))}
    env["APP_ENV"] = "test"
    result = subprocess.run(  # noqa: S603 (our own interpreter and script)
        [sys.executable, "-c", RUNTIME_CHECK],
        cwd=tmp_path,  # no .env found relative to here
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.startswith("ok")
