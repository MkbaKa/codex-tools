# codex-tools

一个面向 Coding Agent 的轻量级 MCP Server，提供文本搜索、AST 搜索/改写，以及更适合 Agent 工作流的文件读取与安全编辑工具。

## 功能概览

当前提供以下工具：

- `rg_search`
  使用 ripgrep 做文本搜索，适合关键词、字面量、配置项定位。
- `ast_search`
  基于 ast-grep 做结构化搜索，适合按代码形状查找目标。
- `ast_rewrite`
  基于 AST 的源码重写，适合做小规模结构替换。
- `read_file`
  读取文件内容，支持按行切片，并可返回 `version` 和 `line_hashes`。
- `write_file`
  用于新建文件、整文件替换或追加；支持 `expected_version` 冲突校验。
- `patch_file`
  适合已知旧文本的小范围精确替换；冲突时可自动重读后安全重放。
- `anchored_edit`
  基于行 hash 的锚点编辑工具，适合先 `read_file(include_line_hashes=True)` 再修改；在安全条件下支持冲突后重定位。
- `list_dir`
  列出目录内容，支持递归和 glob 过滤。
- `file_info`
  返回文件或目录的基础元信息，并对文件返回内容版本号。

## 推荐编辑流程

对于大多数代码修改，推荐按下面的顺序使用：

1. 用 `read_file` 读取目标文件。
2. 小范围文本修改优先用 `patch_file`。
3. 行级编辑优先用 `anchored_edit`，并在读取时开启 `include_line_hashes=True`。
4. 仅在新建文件、整文件替换或追加时使用 `write_file`。

这套流程的目标是减少整文件覆盖，并尽量在文件被外部修改后安全重试。

## 版本与冲突控制

`read_file` 会返回文件级 `version`，可用于后续写入校验。

- `write_file(expected_version=...)`
  当文件版本不一致时拒绝覆盖，并可返回最新内容。
- `patch_file(expected_version=...)`
  当版本不一致但旧文本仍能唯一命中时，会自动在最新内容上重放修改。
- `anchored_edit(expected_version=...)`
  当版本不一致时，会尝试基于 `line_hashes` 重新定位目标行；只有在唯一匹配时才会重放。

## 项目结构

```text
codex_tools/
  common.py        # 路径、版本、hash、冲突处理等共享逻辑
  runtime.py       # MCP 实例与 ripgrep 实例
  search_tools.py  # 文本搜索、AST 搜索、AST 改写
  file_tools.py    # 文件读写、patch、anchored_edit、目录与元信息
main.py            # 入口与兼容导出
```

## 运行方式

项目要求：

- Python `>= 3.13`
- 依赖见 `pyproject.toml`

如果使用 `uv`：

```bash
uv sync
uv run python main.py
```

如果使用普通 Python 环境：

```bash
python main.py
```

服务通过 stdio 方式运行，适合被支持 MCP 的客户端直接接入。

## 开发说明

- 搜索工具在默认情况下会跳过 `.venv`、`.git`、`__pycache__`、`dist`、`build` 等目录。
- 文件工具默认限制在当前工作区内，避免误操作到工作区外路径。
- `anchored_edit` 支持 `replace`、`delete`、`insert_before`、`insert_after`。

## License

本项目使用 [MIT License](./LICENSE)。
