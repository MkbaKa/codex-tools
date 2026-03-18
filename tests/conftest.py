import shutil
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def workspace_root() -> Path:
    root = Path(__file__).resolve().parents[1] / ".test-workspaces" / uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def sample_workspace(workspace_root: Path) -> Path:
    (workspace_root / "notes.txt").write_text(
        "alpha\nbeta\ngamma\n",
        encoding="utf-8",
        newline="\n",
    )
    (workspace_root / "pkg").mkdir()
    (workspace_root / "pkg" / "module.py").write_text(
        "def greet(name):\n    return f'hi {name}'\n",
        encoding="utf-8",
        newline="\n",
    )
    (workspace_root / "pkg" / "other.py").write_text(
        "class Greeter:\n    def run(self):\n        return greet('x')\n",
        encoding="utf-8",
        newline="\n",
    )
    (workspace_root / "config.json").write_text(
        '{"name": "demo"}\n',
        encoding="utf-8",
        newline="\n",
    )
    return workspace_root
