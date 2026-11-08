"""Launch the real STDIO server and list tools only. No tools/call or image generation."""
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def check():
    root = Path(__file__).resolve().parent
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-B", "-u", str(root / "mcp_server.py")],
        cwd=str(root.parent),  # Prove that config paths do not depend on caller cwd.
        env={"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    async with stdio_client(parameters, errlog=sys.stderr) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as session:
            initialized = await session.initialize()
            result = await session.list_tools()
            tools = {tool.name: tool for tool in result.tools}
            if set(tools) != {"comfyui_status", "generate_image", "get_character", "save_character"}:
                raise AssertionError(f"Unexpected tools: {list(tools)}")
            schema = tools["generate_image"].inputSchema
            if set(schema["properties"]) != {"prompt", "character", "seed"}:
                raise AssertionError(f"Unexpected schema: {schema}")
            if schema.get("required") != ["prompt"]:
                raise AssertionError(f"Unexpected required inputs: {schema}")
            print(json.dumps({
                "ok": True,
                "server": initialized.serverInfo.name,
                "image_generation_requested": False,
                "tools": [tool.model_dump(mode="json", exclude_none=True) for tool in result.tools],
            }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(check())
