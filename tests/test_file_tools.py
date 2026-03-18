from pathlib import Path

import pytest
from fastmcp import Client

from codex_tools import mcp
from tests.helpers import call_tool


@pytest.mark.asyncio
async def test_read_file_and_file_info(sample_workspace: Path) -> None:
    async with Client(mcp) as client:
        info = await call_tool(
            client,
            "file_info",
            {"target_dir": str(sample_workspace), "path": "notes.txt"},
        )
        assert info["ok"] is True
        assert info["path"] == "notes.txt"
        assert info["is_file"] is True

        result = await call_tool(
            client,
            "read_file",
            {
                "target_dir": str(sample_workspace),
                "path": "notes.txt",
                "start_line": 1,
                "end_line": 2,
                "include_line_hashes": True,
            },
        )
        assert result["content"] == "alpha\nbeta\n"
        assert [item["line"] for item in result["line_hashes"]] == [1, 2]


@pytest.mark.asyncio
async def test_write_patch_and_anchored_edit_round_trip(sample_workspace: Path) -> None:
    async with Client(mcp) as client:
        created = await call_tool(
            client,
            "write_file",
            {
                "target_dir": str(sample_workspace),
                "path": "draft.txt",
                "content": "one\ntwo\nthree\n",
                "mode": "create",
            },
        )
        assert created["ok"] is True

        patched = await call_tool(
            client,
            "patch_file",
            {
                "target_dir": str(sample_workspace),
                "path": "draft.txt",
                "old_string": "two",
                "new_string": "TWO",
            },
        )
        assert patched["ok"] is True

        read_back = await call_tool(
            client,
            "read_file",
            {
                "target_dir": str(sample_workspace),
                "path": "draft.txt",
                "include_line_hashes": True,
            },
        )
        third_line_hash = next(
            item["hash"] for item in read_back["line_hashes"] if item["line"] == 3
        )

        edited = await call_tool(
            client,
            "anchored_edit",
            {
                "target_dir": str(sample_workspace),
                "path": "draft.txt",
                "edits": [
                    {
                        "op": "replace",
                        "start_line": 3,
                        "end_line": 3,
                        "expected_hashes": [third_line_hash],
                        "new_text": "THREE\n",
                    }
                ],
            },
        )
        assert edited["ok"] is True

        final_state = await call_tool(
            client,
            "read_file",
            {"target_dir": str(sample_workspace), "path": "draft.txt"},
        )
        assert final_state["content"] == "one\nTWO\nTHREE\n"


@pytest.mark.asyncio
async def test_list_dir_returns_filtered_entries(sample_workspace: Path) -> None:
    async with Client(mcp) as client:
        listing = await call_tool(
            client,
            "list_dir",
            {
                "target_dir": str(sample_workspace),
                "path": ".",
                "recursive": True,
                "globs": ["*.py"],
            },
        )
        paths = {entry["path"].replace("\\", "/") for entry in listing["entries"]}
        assert paths == {"pkg/module.py", "pkg/other.py"}


@pytest.mark.asyncio
async def test_file_info_reports_progress(sample_workspace: Path) -> None:
    events: list[tuple[float, float | None, str | None]] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        events.append((progress, total, message))

    async with Client(mcp) as client:
        result = await call_tool(
            client,
            "file_info",
            {"target_dir": str(sample_workspace), "path": "notes.txt"},
            progress_handler=on_progress,
        )
        assert result["ok"] is True

    assert events
    assert events[0][:2] == (0, 1)
    assert events[-1][:2] == (1, 1)
