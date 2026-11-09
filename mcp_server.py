"""STDIO MCP adapter. Start with the project's .venv-mcp Python."""
import asyncio
import hashlib
import json
import logging
import os
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
from character_draft import prepare_draft, create_draft

from comfy_client import ComfyClient, ComfyError, ROOT, prepare_generation_workflow
from comfy_client import generate_image as generate_local_image
from comfy_client import edit_image as edit_local_image
from character_scene import prepare_scene, create_scene, prepare_qwen_graph, read_reference, BINDINGS as QWEN_BINDINGS

logger = logging.getLogger(__name__)
generation_lock = threading.Lock()
startup_lock = threading.Lock()
backend_process = None


def backend_settings():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig")).get("backend", {})


def ensure_backend(client, timeout=180):
    """Start only on connection refusal; never replace an existing listener."""
    global backend_process
    override = os.environ.get("COMFYUI_MCP_AUTO_START")
    if override not in (None, "0", "1"):
        raise ComfyError("COMFYUI_MCP_AUTO_START는 0 또는 1이어야 합니다.")
    settings = backend_settings() if override is None else {}
    auto_start = settings.get("auto_start", True) if override is None else override == "1"
    if not isinstance(auto_start, bool):
        raise ComfyError("backend.auto_start는 true 또는 false여야 합니다.")
    if not auto_start:
        stats = client.request("/system_stats")
        if not isinstance(stats, dict) or "system" not in stats or "devices" not in stats:
            raise ComfyError("ComfyUI API 응답이 아닙니다. Desktop의 로컬 API 주소를 확인하세요.")
        return
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
        "For a new scene of a registered character, call get_character, translate the user's scene and known profile facts into an English scene_description, and use create_character_scene. It validates the official reference_v1.png; never use a previous output for an independent scene. Do not require the user to write English. Identity/style preservation is best effort. "
        "generate_image saves PNG and JSON files on this Windows PC and returns paths. "
        "It can take several minutes. On timeout, do not automatically resubmit: "
        "the ComfyUI job may still be running. For named characters, call get_character first. For a new character without reference images, use create_character_draft (Qwen Image 2.1 T2I) with user-provided character_settings and an English prompt composed by you from the Korean request. Do not ask the user to write English. Drafts are pending approval, not registered characters; never call save_character or register reference_v1.png before explicit user approval. Never invent permanent appearance, age, era or style. Translate the Korean request and known profile facts into an English prompt yourself, honoring current outfit/style requests without overwriting the profile. For other general text-only generation, use generate_image. Unknown facts stay unspecified. For requested edits, use edit_image with the intended edit source; generate_image does not use references. Registered names, aliases, IDs and folder names resolve to the same character folder. Unknown names remain folder labels. Character is not a prompt addition."
    ),
)


def load_settings():
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
    timeout = float(config.get("timeout_seconds", 900))
    if not 0 < timeout < float("inf"):
        raise ValueError("timeout_seconds must be a finite positive number")
    workflow = prepare_generation_workflow()
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


def edit_sync(reference_image, instruction, character, seed, style_reference_image=None):
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
        # Legacy edit_engine/edit_workflow settings are accepted but no longer select engines.
        workflow = prepare_qwen_graph()
        workflow["461"]["inputs"]["filename_prefix"] = "Qwen_edit"
        bindings = QWEN_BINDINGS
        metadata = {"engine": "Qwen Image 2.1"}
        additional_references = None
        if style_reference_image is not None:
            if not instruction.strip():
                raise ComfyError("Edit instruction is required.")
            style_path, style_bytes = read_reference(style_reference_image)
            workflow["edit:style"] = {"class_type": "LoadImage", "inputs": {"image": ""}}
            workflow["459:474"]["inputs"]["images.image_2"] = ["edit:style", 0]
            additional_references = {"edit:style": (style_path, style_bytes)}
            metadata.update(style_reference_image=str(style_path),
                            style_reference_sha256=hashlib.sha256(style_bytes).hexdigest(),
                            edit_instruction=instruction)
            instruction = (
                "Edit image_1 using the following instruction: " + instruction + "\n"
                "Reference roles: image_1 is the editing target and sole source of character identity. "
                "Preserve image_1's facial features, skin tone, hair color, hairstyle, body, clothing "
                "and accessories except for changes explicitly requested above. "
                "Use image_2 ONLY as an illustration style reference: transfer its delicate linework, "
                "coloring technique, fine shading and detailed semi-realistic anime rendering. "
                "Do not copy the person, face, hair color, hairstyle, clothing, jewelry, hair ornaments, "
                "objects or composition from image_2. Keep image_1's composition and background "
                "unless explicitly asked to change them. Apply the style to image_1, not the reverse.")
        timeout = float(config.get("timeout_seconds", 900))
        if not 0 < timeout < float("inf"):
            raise ComfyError("timeout_seconds must be finite and positive")
        client = ComfyClient(config["base_url"])
        ensure_backend(client)
        return edit_local_image(client, workflow, bindings, reference_image, instruction,
                                config["output_dir"], timeout, seed,
                                character=profile["folder_name"] if profile else character,
                                additional_references=additional_references,
                                metadata_extra=metadata)
    finally:
        generation_lock.release()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
async def edit_image(reference_image: str | None = None, instruction: str = "",
                     character: str | None = None, seed: int | None = None,
                     style_reference_image: str | None = None) -> dict:
    """Edit one specified PNG using Qwen Image 2.1.
    Explicit reference_image is the edit source, not necessarily the official reference.
    Only if omitted, use character.reference_images[0]. Preserve originals.
    Optional style_reference_image (Qwen only) supplies image_2 for linework, coloring
    and detailed semi-realistic anime style only, never its character identity or outfit.
    Choose it only when explicitly requested; no style reference is selected automatically.
    Returns separate PNG and JSON paths. Never automatically retry after timeout.
    """
    return await run_tool(edit_sync, reference_image, instruction, character, seed, style_reference_image)


def create_character_draft_sync(character_settings, prompt, seed):
    if not generation_lock.acquire(blocking=False):
        raise ComfyError("This MCP process is already generating an image. Wait for it to finish.")
    try:
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
        timeout = float(config.get("timeout_seconds", 900))
        if not 0 < timeout < float("inf"):
            raise ComfyError("timeout_seconds must be finite and positive")
        prepared = prepare_draft(character_settings, prompt, seed)
        client = ComfyClient(config["base_url"])
        ensure_backend(client)
        return create_draft(client, prepared, config["output_dir"], timeout)
    finally:
        generation_lock.release()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
async def create_character_draft(character_settings: dict, prompt: str, seed: int | None = None) -> dict:
    """Generate one new character candidate with Qwen Image 2.1 T2I, without references.
    character_settings contains only user-provided facts (e.g. name, appearance, age,
    era, outfit, accessories, style). The calling assistant composes prompt in English
    from those facts and the Korean request; never require English from the user.
    Include the default delicate semi-realistic 2D anime TRPG style unless the user
    requests another style; explicit style instructions override the shared default.
    Saves PNG/JSON under drafts/<temporary ID>, with pending_approval status.
    Does not register a character or reference_v1.png. Explicit user approval is
    required before a separate registration action. Never automatically retry.
    """
    return await run_tool(create_character_draft_sync, character_settings, prompt, seed)


def create_character_scene_sync(character, scene_description, seed, pose_image):
    if not generation_lock.acquire(blocking=False):
        raise ComfyError("This MCP process is already generating an image. Wait for it to finish.")
    try:
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
        profile = character_store().get(character)
        if profile is None:
            raise ComfyError(f"Registered character not found: {character}")
        timeout = float(config.get("timeout_seconds", 900))
        if not 0 < timeout < float("inf"):
            raise ComfyError("timeout_seconds must be finite and positive")
        if seed is not None and (not isinstance(seed, int) or not 0 <= seed < 2**64):
            raise ComfyError("seed must be an integer in [0, 2**64).")
        prepared = prepare_scene(profile, config["output_dir"], scene_description, pose_image)
        client = ComfyClient(config["base_url"])
        ensure_backend(client)
        return create_scene(client, prepared, config["output_dir"], timeout, seed, profile["folder_name"])
    finally:
        generation_lock.release()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
async def create_character_scene(character: str, scene_description: str,
                                 seed: int | None = None, pose_image: str | None = None) -> dict:
    """Create one Qwen Image 2.1 scene from a registered character's official reference_v1.png.
    character accepts a registered name, alias or ID. The calling assistant must first
    get_character and compose scene_description in English from the Korean request plus
    saved appearance, accessories, era and style; the user need not provide English.
    Include the default delicate semi-realistic 2D anime TRPG style unless the user
    requests another style; explicit style instructions override reference style.
    This tool has no internal translator. pose_image is an optional PNG used only for pose.
    No previous output is selected. Returns PNG/JSON paths; both contain reference provenance.
    Best-effort likeness/style, not an identity lock. Never automatically retry.
    """
    return await run_tool(create_character_scene_sync, character, scene_description, seed, pose_image)


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
    """Generate one 1024x1024 Qwen Image 2.1 T2I image on the local PC. prompt is passed unchanged. character is an optional registered name, alias, ID or Windows folder name; seed is optional (random if omitted). Returns prompt_id, seed, files (PNG paths), metadata_file (JSON path), character. Can take up to 15 minutes; do not automatically retry after timeout."""
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
