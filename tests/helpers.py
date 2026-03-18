from typing import Any, Optional

from fastmcp import Client


async def call_tool(
    client: Client,
    name: str,
    arguments: dict[str, Any],
    *,
    progress_handler: Optional[Any] = None,
) -> dict[str, Any]:
    result = await client.call_tool(name, arguments, progress_handler=progress_handler)
    assert not result.is_error
    assert result.data is not None
    return result.data
