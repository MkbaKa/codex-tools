import re
from pathlib import Path
from typing import Any, Optional

import anyio
from ast_grep_py import SgRoot
from fastmcp import Context

from .common import LANG_EXT_MAP, collect_files, expand_ast_patterns, resolve_workspace
from .runtime import grep, mcp


def _rg_search_file(query: str, file_path: Path, remaining: int) -> list[str]:
    return grep.search(
        query,
        path=str(file_path),
        output_mode="content",
        n=True,
        head_limit=remaining,
    )


async def _complete_progress(ctx: Context, total: int) -> None:
    progress_total = total or 1
    await ctx.report_progress(progress=progress_total, total=progress_total)


@mcp.tool()
async def rg_search(
    target_dir: str,
    query: str,
    ctx: Context,
    globs: Optional[list[str]] = None,
    max_lines: int = 200,
) -> dict[str, Any]:
    """用 ripgrep 搜索文本，返回结构化结果。"""
    if not query.strip():
        return {"ok": False, "error": "empty query"}
    if max_lines < 1:
        return {"ok": False, "error": "max_lines must be >= 1"}

    try:
        target = resolve_workspace(target_dir)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    file_globs = globs or ["*"]
    files = collect_files(target, file_globs)
    total_files = len(files)
    progress_total = total_files or 1
    await ctx.report_progress(progress=0, total=progress_total)

    try:
        lines = []
        remaining = max_lines
        for index, file_path in enumerate(files, start=1):
            if remaining <= 0:
                break
            result = await anyio.to_thread.run_sync(_rg_search_file, query, file_path, remaining)
            lines.extend(result)
            remaining = max_lines - len(lines)
            await ctx.report_progress(progress=index, total=progress_total)
    except Exception as e:
        await _complete_progress(ctx, total_files)
        return {"ok": False, "error": str(e)}

    await _complete_progress(ctx, total_files)
    return {"ok": True, "matches": lines, "rc": 0 if lines else 1}


def _ast_search_file(file_path: Path, relative_path: str, lang: str, pattern: str) -> list[str]:
    try:
        source = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    sg = SgRoot(source, lang)
    root = sg.root()
    nodes = []
    for candidate in expand_ast_patterns(lang, pattern):
        nodes = root.find_all(pattern=candidate)
        if nodes:
            break
    return [f"{relative_path}: {node.text()}" for node in nodes]


@mcp.tool()
async def ast_search(
    target_dir: str,
    pattern: str,
    lang: str,
    ctx: Context,
    globs: Optional[list[str]] = None,
    max_lines: int = 200,
) -> dict[str, Any]:
    """AST 级别的代码搜索，基于 ast-grep 的模式匹配。"""
    if not pattern.strip() or not lang.strip():
        return {"ok": False, "error": "pattern/lang required"}
    if max_lines < 1:
        return {"ok": False, "error": "max_lines must be >= 1"}

    try:
        target = resolve_workspace(target_dir)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    file_globs = globs or ([LANG_EXT_MAP[lang]] if lang in LANG_EXT_MAP else ["*"])
    files = collect_files(target, file_globs)
    total_files = len(files)
    progress_total = total_files or 1
    await ctx.report_progress(progress=0, total=progress_total)

    matches = []
    try:
        for index, file_path in enumerate(files, start=1):
            if len(matches) >= max_lines:
                break
            file_matches = await anyio.to_thread.run_sync(
                _ast_search_file,
                file_path,
                file_path.relative_to(target).as_posix(),
                lang,
                pattern,
            )
            remaining = max_lines - len(matches)
            matches.extend(file_matches[:remaining])
            await ctx.report_progress(progress=index, total=progress_total)
    except Exception as e:
        await _complete_progress(ctx, total_files)
        return {"ok": False, "error": str(e)}

    await _complete_progress(ctx, total_files)
    return {"ok": True, "matches": matches}


def _ast_rewrite_impl(
    source: str,
    lang: str,
    pattern: str,
    replacer: str,
) -> dict[str, Any]:
    if not source.strip() or not lang.strip():
        return {"ok": False, "error": "source/lang required"}
    if not pattern.strip() or not replacer.strip():
        return {"ok": False, "error": "pattern/replacer required"}

    try:
        sg = SgRoot(source, lang)
        root = sg.root()
        nodes = root.find_all(pattern=pattern)
        if not nodes:
            return {"ok": True, "result": source, "changed": False}

        edits = []
        for node in nodes:
            result_text = replacer
            for var in re.findall(r"\$(\w+)", replacer):
                match = node.get_match(var)
                if match:
                    result_text = result_text.replace("$" + var, match.text())
            edits.append(node.replace(result_text))

        result = root.commit_edits(edits)
        return {"ok": True, "result": result, "changed": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def ast_rewrite(
    source: str,
    lang: str,
    pattern: str,
    replacer: str,
    ctx: Context,
) -> dict[str, Any]:
    """AST 级别的代码重写，用 pattern 匹配代码并用 replacer 替换。"""
    await ctx.report_progress(progress=0, total=1)
    result = await anyio.to_thread.run_sync(_ast_rewrite_impl, source, lang, pattern, replacer)
    await ctx.report_progress(progress=1, total=1)
    return result
