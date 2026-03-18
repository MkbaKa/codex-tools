import re
from typing import Any, Optional

from ast_grep_py import SgRoot

from .common import LANG_EXT_MAP, collect_files, expand_ast_patterns, resolve_dir
from .runtime import grep, mcp


@mcp.tool()
def rg_search(
    target_dir: str,
    query: str,
    globs: Optional[list[str]] = None,
    max_lines: int = 200,
) -> dict[str, Any]:
    """用 ripgrep 搜索文本，返回结构化结果。"""
    if not query.strip():
        return {"ok": False, "error": "empty query"}
    if max_lines < 1:
        return {"ok": False, "error": "max_lines must be >= 1"}

    try:
        target = resolve_dir(target_dir)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    file_globs = globs or ["*"]
    files = collect_files(target, file_globs)

    try:
        lines = []
        remaining = max_lines
        for file_path in files:
            if remaining <= 0:
                break
            result = grep.search(
                query,
                path=str(file_path),
                output_mode="content",
                n=True,
                head_limit=remaining,
            )
            lines.extend(result)
            remaining = max_lines - len(lines)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    return {"ok": True, "matches": lines, "rc": 0 if lines else 1}


@mcp.tool()
def ast_search(
    target_dir: str,
    pattern: str,
    lang: str,
    globs: Optional[list[str]] = None,
    max_lines: int = 200,
) -> dict[str, Any]:
    """AST 级别的代码搜索，基于 ast-grep 的模式匹配。"""
    if not pattern.strip() or not lang.strip():
        return {"ok": False, "error": "pattern/lang required"}
    if max_lines < 1:
        return {"ok": False, "error": "max_lines must be >= 1"}

    try:
        target = resolve_dir(target_dir)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    file_globs = globs or ([LANG_EXT_MAP[lang]] if lang in LANG_EXT_MAP else ["*"])
    files = collect_files(target, file_globs)

    matches = []
    for file_path in files:
        if len(matches) >= max_lines:
            break
        try:
            source = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        sg = SgRoot(source, lang)
        root = sg.root()
        nodes = []
        for candidate in expand_ast_patterns(lang, pattern):
            nodes = root.find_all(pattern=candidate)
            if nodes:
                break
        for node in nodes:
            rel_path = file_path.relative_to(target)
            matches.append(f"{rel_path}: {node.text()}")
            if len(matches) >= max_lines:
                break

    return {"ok": True, "matches": matches}


@mcp.tool()
def ast_rewrite(
    source: str,
    lang: str,
    pattern: str,
    replacer: str,
) -> dict[str, Any]:
    """AST 级别的代码重写，用 pattern 匹配代码并用 replacer 替换。"""
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
