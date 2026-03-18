import fnmatch
import hashlib
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

TEXT_ENCODING = "utf-8"

LANG_EXT_MAP = {
    "python": "*.py",
    "javascript": "*.js",
    "typescript": "*.ts",
    "rust": "*.rs",
    "java": "*.java",
    "kotlin": "*.kt",
    "go": "*.go",
    "c": "*.c",
    "cpp": "*.cpp",
    "css": "*.css",
    "html": "*.html",
}

SKIP_DIRS = {
    ".venv",
    "venv",
    "node_modules",
    ".git",
    "__pycache__",
    ".tox",
    "dist",
    "build",
}


def resolve_workspace(target_dir: str) -> Path:
    if not target_dir.strip():
        raise ValueError("target_dir required")

    workspace = Path(target_dir).expanduser().resolve()
    if not workspace.exists():
        raise FileNotFoundError("target_dir not found")
    if not workspace.is_dir():
        raise NotADirectoryError("target_dir not a directory")
    return workspace


def display_path(path: Path, workspace: Path) -> str:
    try:
        rel_path = path.relative_to(workspace)
    except ValueError:
        return str(path)
    return str(rel_path) or "."


def resolve_path(path: str, *, workspace: Path, must_exist: bool = False) -> Path:
    if not path.strip():
        raise ValueError("path required")

    raw_path = Path(path).expanduser()
    target = raw_path if raw_path.is_absolute() else workspace / raw_path
    resolved = target.resolve()

    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("path outside target_dir") from exc

    if must_exist and not resolved.exists():
        raise FileNotFoundError("path not found")

    return resolved


def resolve_dir(path: str = ".", *, workspace: Path) -> Path:
    target = resolve_path(path, workspace=workspace, must_exist=True)
    if not target.is_dir():
        raise NotADirectoryError("path not a directory")
    return target


def should_skip(path: Path, base: Path) -> bool:
    try:
        parts = path.relative_to(base).parts
    except ValueError:
        return True
    return any(part in SKIP_DIRS for part in parts)


def matches_globs(path: Path, base: Path, globs: Optional[list[str]]) -> bool:
    if not globs:
        return True

    rel_path = path.relative_to(base).as_posix()
    return any(
        fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(path.name, pattern)
        for pattern in globs
    )


def collect_files(target: Path, file_globs: list[str]) -> list[Path]:
    files = []
    seen = set()
    for pattern in file_globs:
        for file_path in target.rglob(pattern):
            if not file_path.is_file():
                continue
            if should_skip(file_path, target):
                continue
            if file_path in seen:
                continue
            seen.add(file_path)
            files.append(file_path)
    return sorted(files, key=lambda item: item.relative_to(target).as_posix())


def atomic_write(path: Path, content: str, encoding: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Optional[Path] = None

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding=encoding,
            delete=False,
            dir=path.parent,
            newline="",
        ) as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        temp_path.replace(path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def expand_ast_patterns(lang: str, pattern: str) -> list[str]:
    patterns = [pattern]

    stripped = pattern.strip()
    if lang == "python" and stripped.startswith("def ") and "->" not in stripped:
        head, sep, tail = pattern.rpartition(":")
        if sep:
            patterns.append(f"{head} -> $RET:{tail}")

    return patterns


def describe_entry(path: Path, base: Path) -> dict[str, Any]:
    is_dir = path.is_dir()
    size = None if is_dir else path.stat().st_size
    return {
        "path": str(path.relative_to(base)),
        "name": path.name,
        "is_dir": is_dir,
        "size": size,
    }


def timestamp_to_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def compute_file_version(raw_bytes: bytes) -> str:
    return hashlib.sha256(raw_bytes).hexdigest()


def compute_line_hash(line: str) -> str:
    return hashlib.sha256(line.encode(TEXT_ENCODING)).hexdigest()[:12]


def read_file_state(path: Path, encoding: str) -> dict[str, Any]:
    raw_bytes = path.read_bytes()
    return {
        "raw_bytes": raw_bytes,
        "content": raw_bytes.decode(encoding, errors="ignore"),
        "version": compute_file_version(raw_bytes),
    }


def build_line_hashes(content: str, start_line: int, end_line: int) -> list[dict[str, Any]]:
    lines = content.splitlines()
    if start_line < 1 or end_line < start_line:
        return []
    selected = lines[start_line - 1 : end_line]
    return [
        {
            "line": start_line + offset,
            "hash": compute_line_hash(line),
            "text": line,
        }
        for offset, line in enumerate(selected)
    ]


def build_conflict_response(
    target: Path,
    expected_version: Optional[str],
    current_version: Optional[str],
    latest_content: Optional[str] = None,
    *,
    display_root: Optional[Path] = None,
    retry_suggested: bool = True,
    retry_attempted: bool = False,
    retry_applied: bool = False,
    reason: str = "version mismatch",
) -> dict[str, Any]:
    result = {
        "ok": False,
        "error": reason,
        "conflict": True,
        "path": display_path(target, display_root) if display_root else str(target),
        "expected_version": expected_version,
        "current_version": current_version,
        "retry_suggested": retry_suggested,
        "retry_attempted": retry_attempted,
        "retry_applied": retry_applied,
    }
    if latest_content is not None:
        result["latest_content"] = latest_content
    return result


def detect_newline_style(content: str) -> str:
    if "\r\n" in content:
        return "\r\n"
    if "\n" in content:
        return "\n"
    if "\r" in content:
        return "\r"
    return "\n"


def normalize_newlines(text: str, newline_style: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline_style)


def find_unique_hash_sequence(lines: list[str], expected_hashes: list[str]) -> Optional[tuple[int, int]]:
    if not expected_hashes:
        return None

    line_hashes = [compute_line_hash(line) for line in lines]
    width = len(expected_hashes)
    matches = [
        (index + 1, index + width)
        for index in range(0, len(line_hashes) - width + 1)
        if line_hashes[index : index + width] == expected_hashes
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def find_unique_hash_line(lines: list[str], expected_hash: str) -> Optional[int]:
    matches = [
        index + 1
        for index, line in enumerate(lines)
        if compute_line_hash(line) == expected_hash
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def line_offsets(content: str) -> list[int]:
    offsets = [0]
    position = 0
    for segment in content.splitlines(keepends=True):
        position += len(segment)
        offsets.append(position)
    return offsets


def resolve_anchored_edit(
    lines: list[str],
    edit: dict[str, Any],
    *,
    allow_reanchor: bool,
) -> dict[str, Any]:
    op = str(edit.get("op", "replace"))
    if op in {"replace", "delete"}:
        start_line = int(edit.get("start_line", 0))
        end_line = int(edit.get("end_line", start_line))
        expected_hashes = edit.get("expected_hashes")
        if expected_hashes is None and "expected_hash" in edit:
            expected_hashes = [edit["expected_hash"]]
        if not isinstance(expected_hashes, list) or not expected_hashes:
            raise ValueError("expected_hashes required for replace/delete")
        if start_line < 1 or end_line < start_line:
            raise ValueError("invalid start_line/end_line")
        if len(expected_hashes) != end_line - start_line + 1:
            raise ValueError("expected_hashes length mismatch")

        direct_slice = lines[start_line - 1 : end_line] if end_line <= len(lines) else []
        direct_hashes = [compute_line_hash(line) for line in direct_slice]
        if len(direct_slice) == len(expected_hashes) and direct_hashes == expected_hashes:
            resolved_start = start_line
            resolved_end = end_line
            reanchored = False
        elif allow_reanchor:
            found = find_unique_hash_sequence(lines, expected_hashes)
            if not found:
                raise LookupError("anchor mismatch")
            resolved_start, resolved_end = found
            reanchored = True
        else:
            raise LookupError("anchor mismatch")

        replacement = "" if op == "delete" else str(edit.get("new_text", ""))
        return {
            "op": op,
            "start_line": resolved_start,
            "end_line": resolved_end,
            "replacement": replacement,
            "reanchored": reanchored,
        }

    if op in {"insert_before", "insert_after"}:
        line = int(edit.get("line", 0))
        expected_hash = edit.get("expected_hash")
        if line < 1:
            raise ValueError("line must be >= 1")
        if not expected_hash:
            raise ValueError("expected_hash required for insert")

        direct_ok = line <= len(lines) and compute_line_hash(lines[line - 1]) == expected_hash
        if direct_ok:
            resolved_line = line
            reanchored = False
        elif allow_reanchor:
            found_line = find_unique_hash_line(lines, str(expected_hash))
            if not found_line:
                raise LookupError("anchor mismatch")
            resolved_line = found_line
            reanchored = True
        else:
            raise LookupError("anchor mismatch")

        return {
            "op": op,
            "line": resolved_line,
            "replacement": str(edit.get("new_text", "")),
            "reanchored": reanchored,
        }

    raise ValueError("unsupported op")
