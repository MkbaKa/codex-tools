from typing import Any, Optional

import anyio
from fastmcp import Context

from .common import (
    TEXT_ENCODING,
    atomic_write,
    build_conflict_response,
    build_line_hashes,
    compute_file_version,
    describe_entry,
    detect_newline_style,
    display_path,
    line_offsets,
    matches_globs,
    normalize_newlines,
    read_file_state,
    resolve_anchored_edit,
    resolve_dir,
    resolve_path,
    resolve_workspace,
    should_skip,
    timestamp_to_iso,
)
from .runtime import mcp


async def _run_with_progress(ctx: Context, func: Any, *args: Any) -> dict[str, Any]:
    await ctx.report_progress(progress=0, total=1)
    try:
        return await anyio.to_thread.run_sync(func, *args)
    finally:
        await ctx.report_progress(progress=1, total=1)


def _read_file_impl(
    target_dir: str,
    path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    include_line_hashes: bool = False,
    encoding: str = TEXT_ENCODING,
) -> dict[str, Any]:
    if start_line is not None and start_line < 1:
        return {"ok": False, "error": "start_line must be >= 1"}
    if end_line is not None and end_line < 1:
        return {"ok": False, "error": "end_line must be >= 1"}
    if start_line is not None and end_line is not None and start_line > end_line:
        return {"ok": False, "error": "start_line must be <= end_line"}

    try:
        workspace = resolve_workspace(target_dir)
        target = resolve_path(path, workspace=workspace, must_exist=True)
        if not target.is_file():
            return {"ok": False, "error": "path not a file"}
        state = read_file_state(target, encoding)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    content = state["content"]
    lines = content.splitlines(keepends=True)
    total_lines = len(lines)

    if total_lines == 0:
        selected = ""
        actual_start = 0
        actual_end = 0
    else:
        slice_start = start_line or 1
        slice_end = end_line or total_lines
        selected = "".join(lines[slice_start - 1 : slice_end])
        if selected:
            actual_start = slice_start
            actual_end = min(slice_end, total_lines)
        else:
            actual_start = 0
            actual_end = 0

    result = {
        "ok": True,
        "path": display_path(target, workspace),
        "content": selected,
        "encoding": encoding,
        "version": state["version"],
        "size_bytes": len(state["raw_bytes"]),
        "total_lines": total_lines,
        "start_line": actual_start,
        "end_line": actual_end,
    }
    if include_line_hashes and actual_start and actual_end:
        result["line_hashes"] = build_line_hashes(content, actual_start, actual_end)
    return result


@mcp.tool()
async def read_file(
    target_dir: str,
    path: str,
    ctx: Context,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    include_line_hashes: bool = False,
    encoding: str = TEXT_ENCODING,
) -> dict[str, Any]:
    """Read a text file under `target_dir` with optional line slicing."""
    return await _run_with_progress(
        ctx,
        _read_file_impl,
        target_dir,
        path,
        start_line,
        end_line,
        include_line_hashes,
        encoding,
    )


def _write_file_impl(
    target_dir: str,
    path: str,
    content: str,
    mode: str = "replace",
    expected_version: Optional[str] = None,
    include_latest_on_conflict: bool = True,
    encoding: str = TEXT_ENCODING,
) -> dict[str, Any]:
    if mode not in {"create", "replace", "append"}:
        return {"ok": False, "error": "mode must be one of create/replace/append"}

    try:
        workspace = resolve_workspace(target_dir)
        target = resolve_path(path, workspace=workspace)
        if target.exists() and target.is_dir():
            return {"ok": False, "error": "path is a directory"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    existed = target.exists()

    try:
        if mode == "create" and existed:
            return {"ok": False, "error": "path already exists"}

        current_state = read_file_state(target, encoding) if existed else None
        current_version = current_state["version"] if current_state else None
        if expected_version is not None and expected_version != current_version:
            latest_content = current_state["content"] if include_latest_on_conflict and current_state else None
            return build_conflict_response(
                target,
                expected_version,
                current_version,
                latest_content,
                display_root=workspace,
            )

        if mode == "append":
            previous = current_state["content"] if current_state else ""
            final_content = previous + content
        else:
            final_content = content

        atomic_write(target, final_content, encoding)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    return {
        "ok": True,
        "path": display_path(target, workspace),
        "mode": mode,
        "created": not existed,
        "previous_version": current_version,
        "version": compute_file_version(final_content.encode(encoding)),
        "bytes_written": len(final_content.encode(encoding)),
    }


@mcp.tool()
async def write_file(
    target_dir: str,
    path: str,
    content: str,
    ctx: Context,
    mode: str = "replace",
    expected_version: Optional[str] = None,
    include_latest_on_conflict: bool = True,
    encoding: str = TEXT_ENCODING,
) -> dict[str, Any]:
    """Use for file creation, full replacement, or append under `target_dir`. Prefer `patch_file` or `anchored_edit` for most partial edits."""
    return await _run_with_progress(
        ctx,
        _write_file_impl,
        target_dir,
        path,
        content,
        mode,
        expected_version,
        include_latest_on_conflict,
        encoding,
    )


def _patch_file_impl(
    target_dir: str,
    path: str,
    old_string: str,
    new_string: str,
    expected_version: Optional[str] = None,
    retry_on_conflict: bool = True,
    include_latest_on_conflict: bool = True,
    encoding: str = TEXT_ENCODING,
) -> dict[str, Any]:
    if not old_string:
        return {"ok": False, "error": "old_string required"}

    try:
        workspace = resolve_workspace(target_dir)
        target = resolve_path(path, workspace=workspace, must_exist=True)
        if not target.is_file():
            return {"ok": False, "error": "path not a file"}
        state = read_file_state(target, encoding)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    content = state["content"]
    current_version = state["version"]
    if expected_version is not None and expected_version != current_version:
        retry_attempted = retry_on_conflict
        if retry_on_conflict:
            retry_occurrences = content.count(old_string)
            if retry_occurrences == 1:
                updated = content.replace(old_string, new_string, 1)
                try:
                    atomic_write(target, updated, encoding)
                except Exception as e:
                    return {"ok": False, "error": str(e)}
                return {
                    "ok": True,
                    "path": display_path(target, workspace),
                    "occurrences": retry_occurrences,
                    "previous_version": current_version,
                    "version": compute_file_version(updated.encode(encoding)),
                    "bytes_written": len(updated.encode(encoding)),
                    "retried_on_conflict": True,
                }

        latest_content = content if include_latest_on_conflict else None
        return build_conflict_response(
            target,
            expected_version,
            current_version,
            latest_content,
            display_root=workspace,
            retry_attempted=retry_attempted,
            retry_applied=False,
        )

    occurrences = content.count(old_string)
    if occurrences != 1:
        return {
            "ok": False,
            "error": "old_string must match exactly once",
            "occurrences": occurrences,
            "version": current_version,
        }

    updated = content.replace(old_string, new_string, 1)

    try:
        atomic_write(target, updated, encoding)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    return {
        "ok": True,
        "path": display_path(target, workspace),
        "occurrences": occurrences,
        "previous_version": current_version,
        "version": compute_file_version(updated.encode(encoding)),
        "bytes_written": len(updated.encode(encoding)),
        "retried_on_conflict": False,
    }


@mcp.tool()
async def patch_file(
    target_dir: str,
    path: str,
    old_string: str,
    new_string: str,
    ctx: Context,
    expected_version: Optional[str] = None,
    retry_on_conflict: bool = True,
    include_latest_on_conflict: bool = True,
    encoding: str = TEXT_ENCODING,
) -> dict[str, Any]:
    """Preferred tool for small textual edits under `target_dir` when you know the exact old text to replace."""
    return await _run_with_progress(
        ctx,
        _patch_file_impl,
        target_dir,
        path,
        old_string,
        new_string,
        expected_version,
        retry_on_conflict,
        include_latest_on_conflict,
        encoding,
    )


def _anchored_edit_impl(
    target_dir: str,
    path: str,
    edits: list[dict[str, Any]],
    expected_version: Optional[str] = None,
    retry_on_conflict: bool = True,
    include_latest_on_conflict: bool = True,
    encoding: str = TEXT_ENCODING,
) -> dict[str, Any]:
    if not edits:
        return {"ok": False, "error": "edits required"}

    try:
        workspace = resolve_workspace(target_dir)
        target = resolve_path(path, workspace=workspace, must_exist=True)
        if not target.is_file():
            return {"ok": False, "error": "path not a file"}
        state = read_file_state(target, encoding)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    content = state["content"]
    current_version = state["version"]
    version_mismatch = expected_version is not None and expected_version != current_version
    lines = content.splitlines()
    newline_style = detect_newline_style(content)
    offsets = line_offsets(content)

    resolved_edits = []
    reanchored_count = 0
    for index, edit in enumerate(edits):
        try:
            resolved = resolve_anchored_edit(
                lines,
                edit,
                allow_reanchor=retry_on_conflict,
            )
        except ValueError as e:
            return {"ok": False, "error": str(e), "edit_index": index}
        except LookupError:
            response = build_conflict_response(
                target,
                expected_version,
                current_version,
                content if include_latest_on_conflict else None,
                display_root=workspace,
                retry_attempted=retry_on_conflict,
                retry_applied=False,
                reason="anchor mismatch",
            )
            response["edit_index"] = index
            return response

        resolved["index"] = index
        resolved_edits.append(resolved)
        if resolved["reanchored"]:
            reanchored_count += 1

    spans = []
    line_count = len(offsets) - 1
    for resolved in resolved_edits:
        replacement = normalize_newlines(resolved["replacement"], newline_style)
        op = resolved["op"]
        if op in {"replace", "delete"}:
            start_line = resolved["start_line"]
            end_line = resolved["end_line"]
            if end_line > line_count:
                return {"ok": False, "error": "resolved range outside file", "edit_index": resolved["index"]}
            char_start = offsets[start_line - 1]
            char_end = offsets[end_line]
        elif op == "insert_before":
            line = resolved["line"]
            if line > line_count:
                return {"ok": False, "error": "resolved line outside file", "edit_index": resolved["index"]}
            char_start = offsets[line - 1]
            char_end = char_start
        else:
            line = resolved["line"]
            if line > line_count:
                return {"ok": False, "error": "resolved line outside file", "edit_index": resolved["index"]}
            char_start = offsets[line]
            char_end = char_start

        spans.append(
            {
                "index": resolved["index"],
                "char_start": char_start,
                "char_end": char_end,
                "replacement": replacement,
            }
        )

    ordered = sorted(spans, key=lambda item: (item["char_start"], item["char_end"], item["index"]))
    previous = None
    for current in ordered:
        if previous is not None:
            overlap = current["char_start"] < previous["char_end"]
            same_start = current["char_start"] == previous["char_start"]
            if overlap or same_start:
                return {
                    "ok": False,
                    "error": "overlapping edits are not supported",
                    "edit_index": current["index"],
                }
        previous = current

    updated = content
    for span in sorted(
        ordered,
        key=lambda item: (item["char_start"], item["char_end"], item["index"]),
        reverse=True,
    ):
        updated = updated[: span["char_start"]] + span["replacement"] + updated[span["char_end"] :]

    try:
        atomic_write(target, updated, encoding)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    return {
        "ok": True,
        "path": display_path(target, workspace),
        "edits_applied": len(resolved_edits),
        "reanchored_edits": reanchored_count,
        "previous_version": current_version,
        "version": compute_file_version(updated.encode(encoding)),
        "bytes_written": len(updated.encode(encoding)),
        "version_mismatch": version_mismatch,
        "retried_on_conflict": bool(version_mismatch and retry_on_conflict) or reanchored_count > 0,
    }


@mcp.tool()
async def anchored_edit(
    target_dir: str,
    path: str,
    edits: list[dict[str, Any]],
    ctx: Context,
    expected_version: Optional[str] = None,
    retry_on_conflict: bool = True,
    include_latest_on_conflict: bool = True,
    encoding: str = TEXT_ENCODING,
) -> dict[str, Any]:
    """Preferred tool for line-based edits under `target_dir` after reading with `include_line_hashes=True`; supports re-anchoring on safe conflicts."""
    return await _run_with_progress(
        ctx,
        _anchored_edit_impl,
        target_dir,
        path,
        edits,
        expected_version,
        retry_on_conflict,
        include_latest_on_conflict,
        encoding,
    )


def _list_dir_impl(
    target_dir: str,
    path: str = ".",
    recursive: bool = False,
    globs: Optional[list[str]] = None,
    max_entries: int = 200,
) -> dict[str, Any]:
    if max_entries < 1:
        return {"ok": False, "error": "max_entries must be >= 1"}

    try:
        workspace = resolve_workspace(target_dir)
        target = resolve_dir(path, workspace=workspace)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    if recursive:
        candidates = [item for item in target.rglob("*") if not should_skip(item, target)]
    else:
        candidates = list(target.iterdir())

    entries = []
    for item in sorted(candidates, key=lambda value: value.relative_to(target).as_posix()):
        if not matches_globs(item, target, globs):
            continue
        entries.append(describe_entry(item, target))
        if len(entries) >= max_entries:
            break

    return {
        "ok": True,
        "path": display_path(target, workspace),
        "entries": entries,
        "count": len(entries),
    }


@mcp.tool()
async def list_dir(
    target_dir: str,
    ctx: Context,
    path: str = ".",
    recursive: bool = False,
    globs: Optional[list[str]] = None,
    max_entries: int = 200,
) -> dict[str, Any]:
    """List files and directories under `target_dir`."""
    return await _run_with_progress(ctx, _list_dir_impl, target_dir, path, recursive, globs, max_entries)


def _file_info_impl(target_dir: str, path: str = ".") -> dict[str, Any]:
    try:
        workspace = resolve_workspace(target_dir)
        target = resolve_path(path, workspace=workspace)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    info = {
        "ok": True,
        "path": display_path(target, workspace),
        "absolute_path": str(target),
        "exists": target.exists(),
    }

    if not target.exists():
        return info

    stat = target.stat()
    info.update(
        {
            "is_file": target.is_file(),
            "is_dir": target.is_dir(),
            "size": stat.st_size,
            "modified_at": timestamp_to_iso(stat.st_mtime),
            "created_at": timestamp_to_iso(stat.st_ctime),
        }
    )
    if target.is_file():
        info["version"] = compute_file_version(target.read_bytes())
    return info


@mcp.tool()
async def file_info(target_dir: str, ctx: Context, path: str = ".") -> dict[str, Any]:
    """Return metadata for a file or directory under `target_dir`."""
    return await _run_with_progress(ctx, _file_info_impl, target_dir, path)
