from pathlib import Path

import pytest
from fastmcp import Client

from codex_tools import mcp
from tests.helpers import call_tool


@pytest.mark.asyncio
async def test_rg_search_and_ast_search(sample_workspace: Path) -> None:
    async with Client(mcp) as client:
        rg_result = await call_tool(
            client,
            "rg_search",
            {
                "target_dir": str(sample_workspace),
                "query": "greet",
                "globs": ["*.py"],
            },
        )
        assert rg_result["ok"] is True
        assert any("module.py" in item and "greet" in item for item in rg_result["matches"])

        ast_result = await call_tool(
            client,
            "ast_search",
            {
                "target_dir": str(sample_workspace),
                "pattern": "def greet($$$)",
                "lang": "python",
                "globs": ["*.py"],
            },
        )
        assert ast_result["ok"] is True
        assert any("pkg/module.py" in item and "def greet" in item for item in ast_result["matches"])


@pytest.mark.asyncio
async def test_ast_rewrite_rewrites_python_source() -> None:
    async with Client(mcp) as client:
        result = await call_tool(
            client,
            "ast_rewrite",
            {
                "source": "result = old_call()\n",
                "lang": "python",
                "pattern": "old_call()",
                "replacer": "new_call()",
            },
        )
        assert result["ok"] is True
        assert result["changed"] is True
        assert result["result"] == "result = new_call()\n"


@pytest.mark.asyncio
async def test_rg_search_reports_progress(sample_workspace: Path) -> None:
    events: list[tuple[float, float | None, str | None]] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        events.append((progress, total, message))

    async with Client(mcp) as client:
        result = await call_tool(
            client,
            "rg_search",
            {
                "target_dir": str(sample_workspace),
                "query": "return",
                "globs": ["*.py"],
            },
            progress_handler=on_progress,
        )
        assert result["ok"] is True

    assert events
    assert events[0][0] == 0
    assert events[-1][0] == events[-1][1]
    assert events[-1][1] is not None
