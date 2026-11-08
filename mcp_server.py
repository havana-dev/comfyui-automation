"""STDIO MCP adapter. Start with the project's .venv-mcp Python."""
import asyncio
import json
import logging
import sys
import threading

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from comfy_client import ComfyClient, ComfyError, ROOT, load_workflow
from comfy_client import generate_image as generate_local_image

logger = logging.getLogger(__name__)
generation_lock = threading.Lock()
mcp = FastMCP(
    "comfyui-local",
    instructions=(
        "Use comfyui_status to check the local ComfyUI server. "
        "Only generate an image when the user requests one. "
        "generate_image saves PNG and JSON files on this Windows PC and returns paths. "
        "It can take several minutes. On timeout, do not automatically resubmit: "
        "the ComfyUI job may still be running. Character is a folder label, not a prompt addition."
    ),
)


def load_settings():
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
    timeout = float(config.get("timeout_seconds", 900))
    if not 0 < timeout < float("inf"):
        raise ValueError("timeout_seconds must be a finite positive number")
    workflow = load_workflow(ROOT / config["workflow"])
    return config, workflow, timeout


def status_sync():
    config, workflow, _ = load_settings()
    client = ComfyClient(config["base_url"])
    stats = client.check(workflow)
    return {
        "ready": True,
        "base_url": client.base_url,
        "devices": stats.get("devices", []),
        "output_dir": config["output_dir"],
        "output_write_checked": False,
        "generation_in_progress": generation_lock.locked(),
    }


def generate_sync(prompt, character, seed):
    if not generation_lock.acquire(blocking=False):
        raise ComfyError("This MCP process is already generating an image. Wait for it to finish.")
    try:
        config, workflow, timeout = load_settings()
        logger.info("Starting image generation")
        result = generate_local_image(
            ComfyClient(config["base_url"]), workflow, prompt, config["output_dir"],
            timeout=timeout, seed=seed, character=character,
        )
        logger.info("Saved generation %s", result["prompt_id"])
        return result
    finally:
        generation_lock.release()


async def run_tool(function, *args):
    try:
        # Keep protocol handling responsive while the existing synchronous client waits.
        return await asyncio.to_thread(function, *args)
    except (ComfyError, OSError, ValueError, KeyError) as exc:
        logger.error("%s: %s", function.__name__, exc)
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
async def comfyui_status() -> dict:
    """Check local ComfyUI connectivity and workflow/model availability, without generating or writing files. Output write permission is checked only during generation."""
    return await run_tool(status_sync)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
async def generate_image(prompt: str, character: str | None = None, seed: int | None = None) -> dict:
    """Generate one 1024x1024 Z-Image-Turbo image on the local PC. prompt is passed unchanged. character is an optional Windows folder name; seed is optional (random if omitted). Returns prompt_id, seed, files (PNG paths), metadata_file (JSON path), character. Can take up to 15 minutes; do not automatically retry after timeout."""
    return await run_tool(generate_sync, prompt, character, seed)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s", force=True)
    mcp.run(transport="stdio")
