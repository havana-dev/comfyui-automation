"""Official-reference scene preparation; no profile writes or automatic generation."""
import hashlib
import struct
import zlib
from pathlib import Path

from comfy_client import ComfyError, edit_image, load_workflow, ROOT
from result_store import output_folder
from trpg_style import apply_trpg_style

BINDINGS = {"reference_image": ["470", "image"],
            "instruction": ["459:474", "prompt"], "seed": ["459:458", "seed"]}


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


def prepare_scene(profile, output_dir, scene_description, pose_image=None):
    if not scene_description.strip():
        raise ComfyError("Scene description is required; Codex should compose it in English from the user's request and saved profile.")
    folder = output_folder(output_dir, profile["folder_name"])
    expected = folder / "reference_v1.png"
    # Require registration AND the canonical character-folder path, independent of order.
    matches = {Path(p).resolve() for p in profile.get("reference_images", [])
               if Path(p).name.casefold() == "reference_v1.png"}
    if expected.resolve() != expected or expected not in matches:
        raise ComfyError("Official reference must be registered as reference_v1.png inside this character's output folder; generated results are not accepted.")
    reference, data = read_reference(expected)
    graph = prepare_qwen_graph()
    pose = None
    if pose_image is not None:
        pose_path, pose_data = read_reference(pose_image)
        pose = (pose_path, pose_data)
        graph["scene:pose"] = {"class_type": "LoadImage", "inputs": {"image": ""}}
        graph["459:474"]["inputs"]["images.image_2"] = ["scene:pose", 0]
    prompt = ("Create one new scene featuring the same character from reference image 1. "
              "Use image 1 for facial identity, hair, accessories, clothing details and illustration style. "
              "Keep these features as close as possible while changing the setting and pose as requested. "
              "Do not copy the portrait composition. Scene: " + scene_description.strip())
    if pose:
        prompt += " Use reference image 2 only for body pose and layout, not its identity, clothes or art style."
    prompt = apply_trpg_style(prompt)
    metadata = {"engine": "Qwen Image 2.1", "official_reference_image": str(reference),
                "official_reference_sha256": hashlib.sha256(data).hexdigest(),
                "character_id": profile["id"], "scene_description": scene_description,
                "character_profile": {k: profile.get(k) for k in
                    ("name_ko", "name_en", "appearance", "age", "era", "default_style")},
                "pose_image": str(pose[0]) if pose else None,
                "pose_sha256": hashlib.sha256(pose[1]).hexdigest() if pose else None}
    return graph, reference, data, pose, prompt, metadata


def create_scene(client, prepared, output_dir, timeout, seed, character):
    graph, reference, data, pose, prompt, metadata = prepared
    return edit_image(client, graph, BINDINGS, reference, prompt, output_dir, timeout, seed,
                      character=character, reference_bytes=data,
                      additional_references={"scene:pose": pose} if pose else None,
                      metadata_extra=metadata, operation="create_character_scene")
