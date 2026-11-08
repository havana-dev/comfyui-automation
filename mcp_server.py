"""STDIO MCP adapter. Start with the project's .venv-mcp Python."""
import asyncio
import json
import logging
import sys
import threading
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from character_store import CharacterStore

from comfy_client import ComfyClient, ComfyError, ROOT, load_workflow
from comfy_client import generate_image as generate_local_image
from comfy_client import edit_image as edit_local_image

logger = logging.getLogger(__name__)
generation_lock = threading.Lock()
startup_lock = threading.Lock()
backend_process = None


def backend_settings():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))["backend"]


def ensure_backend(client, timeout=180):
    """Start only on connection refusal; never replace an existing listener."""
    global backend_process
    address = urlsplit(client.base_url)
    if address.port != 8188:
        raise ComfyError("Automatic startup requires local port 8188.")
    with startup_lock:
        try:
            with socket.create_connection((address.hostname, 8188), timeout=5):
                listening = True
        except ConnectionRefusedError:
            listening = False
        except OSError as exc:
            raise ComfyError(f"Port check failed; backend not started: {exc}") from exc
        if not listening and (backend_process is None or backend_process.poll() is not None):
            settings = backend_settings()
            base = Path(settings["base_dir"])
            python = base / "ComfyUI/.venv/Scripts/python.exe"
            main = base / "ComfyUI/main.py"
            models = Path(settings["models_config"])
            for path in (python, main, models):
                if not path.is_file():
                    raise ComfyError(f"Desktop file missing: {path}. Nothing will be installed.")
            command = [str(python), "-s", str(main), "--feature-flag", "show_signin_button=true",
                       "--enable-assets", "--enable-manager", "--extra-model-paths-config", str(models),
                       "--input-directory", settings["input_dir"],
                       "--output-directory", settings["output_dir"],
                       "--listen", address.hostname, "--port", "8188", "--disable-auto-launch"]
            with (ROOT / "backend.log").open("ab") as log:
                backend_process = subprocess.Popen(command, cwd=base, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
                    close_fds=True)
        deadline = time.monotonic() + timeout
        while True:
            try:
                stats = client.request("/system_stats")
                if isinstance(stats, dict) and "system" in stats and "devices" in stats:
                    return
            except (ComfyError, ValueError):
                pass
            if not listening and backend_process is not None and backend_process.poll() is not None:
                raise ComfyError("Backend exited. See backend.log; no image submitted.")
            if time.monotonic() >= deadline:
                raise ComfyError("Backend readiness timed out. See backend.log; no image submitted.")
            time.sleep(1)


mcp = FastMCP(
    "comfyui-local",
    instructions=(
        "Use comfyui_status to check the local ComfyUI server. "
        "Only generate an image when the user requests one. "
        "generate_image saves PNG and JSON files on this Windows PC and returns paths. "
        "It can take several minutes. On timeout, do not automatically resubmit: "
        "the ComfyUI job may still be running. For named characters, call get_character first. If missing, call save_character with only user-provided facts; never invent permanent appearance, age, era or style. Translate the Korean request and known profile facts into an English prompt yourself, honoring current outfit/style requests without overwriting the profile. Then call generate_image with that prompt and the profile folder_name as character. Unknown facts stay unspecified. For requested edits, use edit_image with the registered reference_images[0]; generate_image does not use references. Registered names, aliases, IDs and folder names resolve to the same character folder. Unknown names remain folder labels. Character is not a prompt addition."
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
    ensure_backend(client)
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
        client = ComfyClient(config["base_url"])
        ensure_backend(client)
        result = generate_local_image(
            client, workflow, prompt, config["output_dir"],
            timeout=timeout, seed=seed, character=character,
        )
        logger.info("Saved generation %s", result["prompt_id"])
        return result
    finally:
        generation_lock.release()


def edit_sync(reference_image, instruction, character, seed):
    if not generation_lock.acquire(blocking=False):
        raise ComfyError("This MCP process is already generating an image. Wait for it to finish.")
    try:
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
        profile = character_store().get(character) if character else None
        if reference_image is None:
            references = profile.get("reference_images", []) if profile else []
            if not references:
                raise ComfyError("No registered reference image; provide reference_image or a character with reference_images[0].")
            reference_image = references[0]
        settings = config.get("edit_workflow", {})
        path = ROOT / settings.get("path", "workflows/flux2_klein_4b_fp8_edit_api.json")
        if not path.is_file():
            raise ComfyError(f"FLUX.2 Klein 4B FP8 edit workflow not ready: {path}. Nothing will be downloaded or generated.")
        workflow = load_workflow(path)
        bindings = settings.get("bindings", {})
        if not all(key in bindings for key in ("reference_image", "instruction", "seed")):
            raise ComfyError("Edit workflow bindings missing: reference_image, instruction, seed are required.")
        timeout = float(config.get("timeout_seconds", 900))
        if not 0 < timeout < float("inf"):
            raise ComfyError("timeout_seconds must be finite and positive")
        client = ComfyClient(config["base_url"])
        ensure_backend(client)
        return edit_local_image(client, workflow, bindings, reference_image, instruction,
                                config["output_dir"], timeout, seed,
                                character=profile["folder_name"] if profile else character)
    finally:
        generation_lock.release()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
async def edit_image(reference_image: str | None = None, instruction: str = "",
                     character: str | None = None, seed: int | None = None) -> dict:
    """Edit one reference PNG using configured FLUX.2 Klein 4B FP8 API workflow.
    If reference_image is omitted, use character.reference_images[0]. Preserve originals.
    Returns separate PNG and JSON paths. Never automatically retry after timeout.
    """
    return await run_tool(edit_sync, reference_image, instruction, character, seed)


async def run_tool(function, *args):
    try:
        # Keep protocol handling responsive while the existing synchronous client waits.
        return await asyncio.to_thread(function, *args)
    except (ComfyError, OSError, ValueError, KeyError) as exc:
        logger.error("%s: %s", function.__name__, exc)
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
async def comfyui_status() -> dict:
    """Start or reuse local ComfyUI and check workflow/model availability without generating images. Output write permission is checked only during generation."""
    return await run_tool(status_sync)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
async def generate_image(prompt: str, character: str | None = None, seed: int | None = None) -> dict:
    """Generate one 1024x1024 Z-Image-Turbo image on the local PC. prompt is passed unchanged. character is an optional registered name, alias, ID or Windows folder name; seed is optional (random if omitted). Returns prompt_id, seed, files (PNG paths), metadata_file (JSON path), character. Can take up to 15 minutes; do not automatically retry after timeout."""
    return await run_tool(generate_sync, prompt, character, seed)


def character_store():
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
    return CharacterStore(ROOT / "characters", config["output_dir"])


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
async def get_character(name: str) -> dict:
    """Look up a Korean/English name or alias. No backend startup or generation."""
    profile = await run_tool(lambda: character_store().get(name))
    return {"found": profile is not None, "profile": profile}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
async def save_character(name: str, name_ko: str | None = None, name_en: str | None = None,
                         aliases: list[str] | None = None, appearance: str | None = None,
                         age: str | None = None, era: str | None = None,
                         default_style: str | None = None,
                         reference_images: list[str] | None = None) -> dict:
    """Create/update only supplied facts and create an image folder. Omitted fields are preserved.
    Age is free text. Reference paths are stored only; edit_image can load them for editing.
    Use returned folder_name as generate_image.character after composing an English prompt.
    """
    fields = dict(name_ko=name_ko, name_en=name_en, aliases=aliases, appearance=appearance,
                  age=age, era=era, default_style=default_style, reference_images=reference_images)
    return await run_tool(lambda: character_store().save(name, **fields))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s", force=True)
    mcp.run(transport="stdio")
