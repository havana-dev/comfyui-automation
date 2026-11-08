"""Unregistered Qwen T2I candidates. Never writes character profiles or references."""
import json
import secrets
import uuid
from datetime import datetime, timezone

from comfy_client import ComfyError, ROOT, load_workflow, prepare_output
from result_store import output_folder, save_metadata


def prepare_draft(character_settings, prompt, seed=None):
    if not isinstance(character_settings, dict) or not character_settings:
        raise ComfyError("character_settings must contain user-provided character facts.")
    if character_settings.get("reference_images") or character_settings.get("reference_image"):
        raise ComfyError("Drafts are text-only; use scene or edit tools for reference images.")
    try:
        settings = json.loads(json.dumps(character_settings, ensure_ascii=False, allow_nan=False))
    except (ValueError, TypeError) as exc:
        raise ComfyError("character_settings must be JSON-compatible.") from exc
    if not isinstance(prompt, str) or not prompt.strip():
        raise ComfyError("An English prompt composed by the calling assistant is required.")
    seed = secrets.randbelow(2**63) if seed is None else seed
    if type(seed) is not int or not 0 <= seed < 2**64:
        raise ComfyError("seed must be an integer in [0, 2**64).")
    graph = load_workflow(ROOT / "workflows/qwen21_text_to_image_api.json")
    required = {
        "13": ("ResolutionSelector", ("aspect_ratio", "megapixels", "multiple")),
        "459:451": ("UNETLoader", ("unet_name",)),
        "459:452": ("TextEncodeQwenImage21", ("prompt", "negative_prompt", "clip")),
        "459:453": ("CLIPLoader", ("clip_name",)),
        "459:454": ("VAELoader", ("vae_name",)),
        "459:456": ("EmptyLatentImage", ("width", "height", "batch_size")),
        "459:457": ("VAEDecode", ("samples", "vae")),
        "459:458": ("KSampler", ("seed", "positive", "negative", "latent_image", "model")),
        "459:480": ("QwenImage21Cache", ("model",)),
        "461": ("SaveImageAdvanced", ("images", "format", "filename_prefix")),
    }
    for node_id, (kind, fields) in required.items():
        node = graph.get(node_id, {})
        if node.get("class_type") != kind or any(f not in node.get("inputs", {}) for f in fields):
            raise ComfyError(f"Qwen T2I workflow binding mismatch: {node_id} must be {kind} with {fields}")
    # Bypass the sample prompt, switch, preview and optional rewriting model.
    graph["459:452"]["inputs"].update(prompt=prompt.strip(), negative_prompt="")
    graph["459:458"]["inputs"]["seed"] = seed
    graph["459:456"]["inputs"]["batch_size"] = 1
    graph["461"]["inputs"].update(format="png", filename_prefix="Qwen_character_draft")
    active = {}

    def include(node_id):
        if node_id in active:
            return
        if node_id not in graph:
            raise ComfyError(f"Qwen T2I workflow has a missing dependency: {node_id}")
        active[node_id] = graph[node_id]
        for value in graph[node_id]["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                include(value[0])

    include("461")
    if set(active) != set(required):
        raise ComfyError("Qwen T2I workflow has unexpected or disconnected dependencies.")
    return active, settings, prompt.strip(), seed


def create_draft(client, prepared, output_dir, timeout=900):
    graph, settings, prompt, seed = prepared
    client.check(graph)
    draft_id = "draft-" + uuid.uuid4().hex
    folder = output_folder(output_dir, draft_id, "drafts")
    # Exclusive reservation prevents collisions from touching existing PNG/JSON files.
    folder.mkdir(parents=True, exist_ok=False)
    prepare_output(folder)
    response = client.request("/prompt", {"prompt": graph, "client_id": str(uuid.uuid4())})
    if response.get("node_errors") or not response.get("prompt_id"):
        raise ComfyError(f"Draft submission failed: {response}")
    prompt_id = response["prompt_id"]
    entry = client.wait(prompt_id, timeout)
    images = [image for output in entry.get("outputs", {}).values()
              for image in output.get("images", []) if image.get("type") == "output"]
    if len(images) != 1:
        raise ComfyError(f"Expected one draft image: prompt_id={prompt_id}, received={len(images)}")
    files = client.save(prompt_id, entry, folder)
    try:
        metadata = save_metadata(folder, prompt_id, graph, files, None, entry,
                                 datetime.now(timezone.utc).isoformat(), prompt=prompt, seed=seed,
                                 extra={"operation": "create_character_draft", "engine": "Qwen Image 2.1 T2I",
                                        "draft_id": draft_id, "status": "pending_approval",
                                        "character_settings": settings})
    except (OSError, ValueError) as exc:
        raise ComfyError(f"Draft image saved but metadata failed: prompt_id={prompt_id}, files={files}: {exc}") from exc
    return {"prompt_id": prompt_id, "seed": seed, "files": files, "metadata_file": metadata,
            "draft_id": draft_id, "status": "pending_approval", "character": None}
