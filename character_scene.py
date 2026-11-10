"""Official-reference scene preparation; no profile writes or automatic generation."""
import hashlib
import re
import struct
import zlib
from pathlib import Path

from comfy_client import ComfyError, edit_image, load_workflow, ROOT
from result_store import output_folder
from trpg_style import apply_trpg_style

BINDINGS = {"reference_image": ["470", "image"],
            "instruction": ["459:474", "prompt"], "seed": ["459:458", "seed"]}

# reference_v1.png is mandatory; reference_v2.png ... are optional extra official views.
# Each Qwen reference adds VRAM and time, so counts are capped. The defaults are a safe
# estimate for a 16 GB GPU; config.json "references" can raise them up to the node limit.
QWEN_MAX_IMAGES = 16  # TextEncodeQwenImage21 images autogrow: image_1 ... image_16
DEFAULT_REFERENCE_LIMITS = {"max_official_references": 4, "max_total_references": 8}
MAX_DETAIL_USE_LENGTH = 200
OFFICIAL_REFERENCE_NAME = re.compile(r"reference_v([1-9][0-9]*)\.png", re.IGNORECASE)


def reference_limits(config=None):
    """Read optional config.json "references" limits; fail closed on invalid values."""
    section = (config or {}).get("references", {})
    if not isinstance(section, dict):
        raise ComfyError('config.json "references" must be an object.')
    unknown = set(section) - set(DEFAULT_REFERENCE_LIMITS)
    if unknown:
        raise ComfyError(f'Unknown config.json "references" keys: {sorted(unknown)}')
    limits = {**DEFAULT_REFERENCE_LIMITS, **section}
    for key, value in limits.items():
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= QWEN_MAX_IMAGES:
            raise ComfyError(f'config.json references.{key} must be an integer from 1 to {QWEN_MAX_IMAGES}.')
    if limits["max_official_references"] > limits["max_total_references"]:
        raise ComfyError("references.max_official_references cannot exceed references.max_total_references.")
    return limits


# Shortcut arguments keep their old meaning by adding an extra reference with this description.
POSE_USE = "the body pose only"
STYLE_USE = "the illustration style only (linework, coloring, shading and rendering)"


def read_extra_references(extra_references):
    """Validate the user's [{"image": path, "use": what to take}] list before any backend work."""
    if extra_references is None:
        return []
    if not isinstance(extra_references, list):
        raise ComfyError('extra_references must be a list of {"image": path, "use": description}.')
    extras = []
    for item in extra_references:
        if not isinstance(item, dict) or set(item) != {"image", "use"}:
            raise ComfyError('Each extra reference must be exactly {"image": path, "use": description}.')
        use = item["use"]
        if not isinstance(use, str) or not use.strip() or len(use.strip()) > MAX_DETAIL_USE_LENGTH:
            raise ComfyError(f"Extra reference 'use' must be 1-{MAX_DETAIL_USE_LENGTH} characters.")
        if not isinstance(item["image"], str):
            raise ComfyError("Extra reference 'image' must be a PNG path.")
        path, data = read_reference(item["image"])
        extras.append((path, data, use.strip()))
    return extras


def prepare_qwen_graph():
    """Bind the exported Qwen API graph, excluding sample/enhancer/UI branches."""
    path = ROOT / "workflows/qwen21_character_scene_api.json"
    if not path.is_file():
        raise ComfyError(f"Qwen character scene workflow not ready: {path}")
    graph = load_workflow(path)
    required = {
        "470": ("LoadImage", ("image",)),
        "459:474": ("TextEncodeQwenImage21", ("prompt", "images.image_1", "clip", "vae")),
        "459:458": ("KSampler", ("seed", "positive", "negative", "latent_image")),
        "459:451": ("UNETLoader", ("unet_name",)),
        "459:453": ("CLIPLoader", ("clip_name",)),
        "459:454": ("VAELoader", ("vae_name",)),
        "459:469": ("QwenImage21Cache", ("model",)),
        "459:456": ("EmptyLatentImage", ("batch_size",)),
        "461": ("SaveImageAdvanced", ("images", "format", "filename_prefix")),
    }
    for node_id, (kind, fields) in required.items():
        node = graph.get(node_id, {})
        if node.get("class_type") != kind or any(f not in node.get("inputs", {}) for f in fields):
            raise ComfyError(f"Qwen workflow binding mismatch: {node_id} must be {kind} with {fields}")
    inputs = graph["459:474"]["inputs"]
    # Never retain template references, even if additional samples are later exported.
    for field in list(inputs):
        if field.startswith("images."):
            del inputs[field]
    inputs.update({"prompt": "", "images.image_1": ["470", 0]})
    graph["459:456"]["inputs"]["batch_size"] = 1
    graph["461"]["inputs"].update(format="png", filename_prefix="Qwen_character_scene")
    # Only keep dependencies of the save node. The prompt enhancer and comparison
    # outputs would otherwise execute sample prompts or report unused models.
    active = {}

    def include(node_id):
        if node_id in active:
            return
        if node_id not in graph:
            raise ComfyError(f"Qwen workflow has a missing dependency: {node_id}")
        active[node_id] = graph[node_id]
        for value in graph[node_id]["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                include(value[0])

    include("461")
    if not required.keys() <= active.keys():
        raise ComfyError("Qwen workflow output is disconnected from required scene nodes")
    return active


def read_reference(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ComfyError(f"Reference PNG not found: {path}")
    data = path.read_bytes()
    try:
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Expected PNG")
        offset, kinds = 8, []
        while offset + 12 <= len(data):
            size = struct.unpack(">I", data[offset:offset + 4])[0]
            end = offset + size + 12
            if end > len(data):
                raise ValueError("Truncated PNG chunk")
            chunk = data[offset + 4:end - 4]
            crc = struct.unpack(">I", data[end - 4:end])[0]
            if zlib.crc32(chunk) & 0xffffffff != crc:
                raise ValueError("PNG CRC mismatch")
            kind = chunk[:4]
            if not kinds and (kind != b"IHDR" or size != 13):
                raise ValueError("Missing PNG header")
            kinds.append(kind)
            offset = end
            if kind == b"IEND":
                break
        if not kinds or kinds[-1] != b"IEND" or b"IDAT" not in kinds or offset != len(data):
            raise ValueError("Incomplete PNG")
    except (ValueError, struct.error) as exc:
        raise ComfyError(f"Invalid reference PNG: {path}: {exc}") from exc
    return path, data


def official_references(profile, output_dir, max_official=DEFAULT_REFERENCE_LIMITS["max_official_references"]):
    """Return the registered reference_vN.png files in this character's folder, v1 first.

    Only canonical names inside the character's own output folder count; previous
    outputs or files elsewhere are never treated as official, whatever the list order.
    """
    folder = output_folder(output_dir, profile["folder_name"])
    expected = folder / "reference_v1.png"
    registered = {}
    for item in profile.get("reference_images", []):
        path = Path(item).resolve()
        match = OFFICIAL_REFERENCE_NAME.fullmatch(path.name)
        if match and path.parent == expected.parent:
            registered[int(match.group(1))] = folder / f"reference_v{int(match.group(1))}.png"
    if expected.resolve() != expected or 1 not in registered:
        raise ComfyError("Official reference must be registered as reference_v1.png inside this character's output folder; generated results are not accepted.")
    versions = sorted(registered)[:max_official]
    return [read_reference(registered[version]) for version in versions]


def prepare_scene(profile, output_dir, scene_description, pose_image=None, style_reference_image=None,
                  extra_references=None, limits=None):
    if not scene_description.strip():
        raise ComfyError("Scene description is required; Codex should compose it in English from the user's request and saved profile.")
    limits = reference_limits({"references": limits} if limits is not None else None)
    references = official_references(profile, output_dir, limits["max_official_references"])
    reference, data = references[0]
    # Shortcuts first, then the user's own list, each with a user-readable description of what to take.
    extras = []
    if pose_image is not None:
        extras.append((*read_reference(pose_image), POSE_USE))
    if style_reference_image is not None:
        extras.append((*read_reference(style_reference_image), STYLE_USE))
    extras += read_extra_references(extra_references)
    total = len(references) + len(extras)
    if total > limits["max_total_references"]:
        raise ComfyError(
            f"Too many reference images: {len(references)} official + {len(extras)} extra = {total}, "
            f"but references.max_total_references is {limits['max_total_references']}. Use fewer references "
            f"or raise the limit in config.json (max {QWEN_MAX_IMAGES}).")
    graph = prepare_qwen_graph()
    images = graph["459:474"]["inputs"]
    additional = {}
    slot = 1  # image_1 is already bound to node 470 (reference_v1)
    for index, extra in enumerate(references[1:], start=2):
        slot += 1
        node_id = f"scene:reference{index}"
        graph[node_id] = {"class_type": "LoadImage", "inputs": {"image": ""}}
        images[f"images.image_{slot}"] = [node_id, 0]
        additional[node_id] = extra
    identity_slots = slot
    extra_slots = []
    for index, (path, raw, use) in enumerate(extras, start=1):
        slot += 1
        node_id = f"scene:extra{index}"
        graph[node_id] = {"class_type": "LoadImage", "inputs": {"image": ""}}
        images[f"images.image_{slot}"] = [node_id, 0]
        additional[node_id] = (path, raw)
        extra_slots.append((slot, use))
    if identity_slots == 1:
        prompt = ("Create one new scene featuring the same character from reference image 1. "
                  "Use image 1 for facial identity, hair, accessories, clothing details and illustration style. ")
    else:
        prompt = (f"Create one new scene featuring the same character shown in reference images 1 to {identity_slots}. "
                  f"Images 1 to {identity_slots} are official views of this one person; combine them for facial identity, "
                  "hair, accessories, clothing details and illustration style. Image 1 is the primary reference when they differ. ")
    prompt += ("Keep these features as close as possible while changing the setting and pose as requested. "
               "Do not copy the portrait composition. Scene: " + scene_description.strip())
    for extra_slot, use in extra_slots:
        prompt += f" From reference image {extra_slot}, take only: {use}."
    if extra_slots:
        # One shared guard: the user decides what to take; everything else stays out.
        prompt += (" From these extra reference images, take nothing beyond what is listed for each one: never copy "
                   "their person, face, identity, background, location, objects, text or composition unless listed. "
                   "The setting comes from the scene description.")
    prompt = apply_trpg_style(prompt)
    metadata = {"engine": "Qwen Image 2.1", "official_reference_image": str(reference),
                "official_reference_sha256": hashlib.sha256(data).hexdigest(),
                "official_reference_images": [{"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}
                                              for path, raw in references],
                "character_id": profile["id"], "scene_description": scene_description,
                "character_profile": {k: profile.get(k) for k in
                    ("name_ko", "name_en", "appearance", "age", "era", "default_style")},
                "pose_image": str(Path(pose_image).resolve()) if pose_image is not None else None,
                "style_reference_image": str(Path(style_reference_image).resolve()) if style_reference_image is not None else None,
                "extra_references": [{"slot": extra_slot, "path": str(path), "use": use,
                                      "sha256": hashlib.sha256(raw).hexdigest()}
                                     for (path, raw, use), (extra_slot, _) in zip(extras, extra_slots)],
                "reference_limits": limits}
    return graph, reference, data, additional, prompt, metadata


def create_scene(client, prepared, output_dir, timeout, seed, character):
    graph, reference, data, additional, prompt, metadata = prepared
    return edit_image(client, graph, BINDINGS, reference, prompt, output_dir, timeout, seed,
                      character=character, reference_bytes=data,
                      additional_references=additional or None,
                      metadata_extra=metadata, operation="create_character_scene")
