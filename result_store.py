"""Output paths and generation metadata; independent of CLI and ComfyUI HTTP."""
import json
import re
import uuid
from datetime import datetime, timezone
from ntpath import isreserved
from pathlib import Path, PureWindowsPath


def output_folder(output_dir, character=None, output_subdir=None):
    root = Path(output_dir).resolve()
    parts = []
    if output_subdir is not None:
        path = PureWindowsPath(output_subdir)
        if not output_subdir.strip() or path.drive or path.root:
            raise ValueError("output-subdir는 출력 폴더 아래의 상대 경로여야 합니다.")
        parts.extend(output_subdir.replace("\\", "/").split("/"))
    if character is not None:
        parts.append(character)
    for part in parts:
        if (not part.strip() or part in (".", "..") or part.endswith((".", " "))
                or re.search(r'[<>:"/\\|?*\x00-\x1f]', part)
                or isreserved(part)):
            raise ValueError(f"Windows 폴더명으로 사용할 수 없습니다: {part!r}")
    folder = root.joinpath(*parts).resolve()
    if not folder.is_relative_to(root):
        raise ValueError("출력 폴더 밖으로 저장할 수 없습니다.")
    return folder


def save_metadata(folder, prompt_id, workflow, files, character, entry, completed_at, *, prompt=None, seed=None, extra=None):
    # Prefer the server completion timestamp, including when the result was cached.
    generated_at = completed_at
    time_source = "client_observed_completion"
    for kind, detail in entry.get("status", {}).get("messages", []):
        if kind == "execution_success" and isinstance(detail.get("timestamp"), (int, float)):
            generated_at = datetime.fromtimestamp(detail["timestamp"] / 1000, timezone.utc).isoformat()
            time_source = "server_execution_success"
    model_fields = {"UNETLoader": "unet_name", "CLIPLoader": "clip_name", "VAELoader": "vae_name", "CheckpointLoaderSimple": "ckpt_name"}
    models = [
        {"node_id": node_id, "loader": node["class_type"], "filename": node["inputs"][model_fields[node["class_type"]]]}
        for node_id, node in workflow.items() if node["class_type"] in model_fields
    ]
    metadata = {
        "schema_version": 1,
        "prompt_id": prompt_id,
        "character": character,
        "prompt": workflow["27"]["inputs"]["text"] if prompt is None else prompt,
        "seed": workflow["3"]["inputs"]["seed"] if seed is None else seed,
        "models": models,
        "generated_at": generated_at,
        "generated_at_source": time_source,
        "files": files,
        "workflow": workflow,
    }
    metadata.update(extra or {})
    target = Path(folder) / f"{prompt_id}.json"
    temporary = target.with_name(f".{uuid.uuid4().hex}.part")
    try:
        temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return str(target)
