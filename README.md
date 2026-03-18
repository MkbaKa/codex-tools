# codex-tools

一个基于 FastMCP 的轻量级 MCP Server，面向 Coding Agent 提供文本搜索、AST 搜索/改写，以及更适合 Agent 工作流的文件读取与安全编辑工具。

## 功能概览

当前提供以下工具：

- `rg_search`
  使用 ripgrep 做文本搜索，适合关键词、字面量、配置项定位；每次调用通过 `target_dir` 指定本次搜索的工作区根。
- `ast_search`
  基于 ast-grep 做结构化搜索，适合按代码形状查找目标；每次调用通过 `target_dir` 指定本次搜索的工作区根。
- `ast_rewrite`
  基于 AST 的源码重写，适合做小规模结构替换。
- `read_file`
  读取 `target_dir` 下的文件内容，支持按行切片，并可返回 `version` 和 `line_hashes`。
- `write_file`
  用于在 `target_dir` 下新建文件、整文件替换或追加；支持 `expected_version` 冲突校验。
- `patch_file`
  适合在 `target_dir` 下做已知旧文本的小范围精确替换；冲突时可自动重读后安全重放。
- `anchored_edit`
  基于行 hash 的锚点编辑工具，适合先 `read_file(include_line_hashes=True)` 再修改；在安全条件下支持冲突后重定位。
- `list_dir`
  列出 `target_dir` 下目录内容，支持递归和 glob 过滤。
- `file_info`
  返回 `target_dir` 下文件或目录的基础元信息，并对文件返回内容版本号。
- `progress`
  长时间运行的工具会通过 FastMCP progress 上报执行进度；搜索类工具按文件推进，其余文件工具提供阶段式进度。

## 工作区模型

除 `ast_rewrite` 外，所有文件和搜索工具都会显式接收一个 `target_dir` 参数。

- `target_dir` 表示本次调用的工作区根目录。
- `path` 参数相对于 `target_dir` 解析。
- 绝对路径只有在位于 `target_dir` 内部时才允许使用。
- 因此，同一个 MCP Server 可以在不同调用中操作不同项目，而不需要把工作区根固定在 server 自己所在仓库。

## 推荐编辑流程

对于大多数代码修改，推荐按下面的顺序使用：

1. 用 `read_file(target_dir=..., path=...)` 读取目标文件。
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

推荐直接使用 Python 启动：

```bash
python main.py
```

如果使用 `uv`：

```bash
uv sync
uv run python main.py
```

服务通过 stdio 方式运行，适合被支持 MCP 的客户端直接接入。

入口已经显式关闭 FastMCP banner，并将日志级别压到 `ERROR`，避免 stdio 握手时向 `stdout` 输出额外文本。

### MCP 配置示例

一个更通用的示例配置如下：

```json
{
  "type": "stdio",
  "command": "python",
  "args": [
    "C:\\path\\to\\codex-tools\\main.py"
  ]
}
```

如果你的客户端要求固定解释器路径，也可以把 `command` 改成实际的 Python 可执行文件绝对路径。
如果通过相对路径启动，请确保工作目录是项目根目录。

## 测试

项目测试优先使用 FastMCP 提供的 in-memory `Client(mcp)`，而不是手工创建临时文件后再人工调用工具。

推荐命令：

```bash
python -m pytest -q
```

如果使用 `uv` 管理开发依赖，也可以：

```bash
uv run pytest
```

测试会在仓库内自动创建临时工作区，并覆盖搜索、读写、锚点编辑与 progress 回调等行为。

## 开发说明

- 搜索工具在默认情况下会跳过 `.venv`、`.git`、`__pycache__`、`dist`、`build` 等目录。
- 文件工具会将 `path` 限制在每次调用传入的 `target_dir` 内，避免误操作到无关目录。
- `anchored_edit` 支持 `replace`、`delete`、`insert_before`、`insert_after`。
- 如客户端提供 progress token，工具会发送 progress 更新；未提供时不会影响原有调用结果。

## License

本项目使用 [MIT License](./LICENSE)。
