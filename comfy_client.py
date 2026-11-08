"""Local ComfyUI client. Standard library only; never installs models."""
import argparse
import json
import secrets
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, ProxyHandler, build_opener

from result_store import output_folder, save_metadata

ROOT = Path(__file__).resolve().parent


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, base_url):
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ComfyError("로컬 HTTP 주소만 지원합니다.")
        self.base_url = base_url.rstrip("/")
        self.opener = build_opener(ProxyHandler({}))

    def request(self, path, payload=None, binary=False):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = Request(self.base_url + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=30) as response:
                body = response.read()
        except HTTPError as exc:
            raise ComfyError(f"API HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}") from exc
        except (URLError, OSError) as exc:
            raise ComfyError(f"API 연결 실패 ({self.base_url}): {exc}. Desktop 실행 상태/포트/실행 환경의 접근 권한을 확인하세요.") from exc
        return body if binary else json.loads(body)

    def upload_image(self, path):
        """Upload a uniquely named copy; never overwrite the input image."""
        boundary = uuid.uuid4().hex
        filename = f"edit_reference_{uuid.uuid4().hex}.png"
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
                f'filename="{filename}"\r\nContent-Type: image/png\r\n\r\n').encode()
        body += Path(path).read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        req = Request(self.base_url + "/upload/image", data=body,
                      headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with self.opener.open(req, timeout=30) as response:
                result = json.loads(response.read())
        except (URLError, OSError, ValueError) as exc:
            raise ComfyError(f"Reference upload failed: {exc}") from exc
        if not result.get("name"):
            raise ComfyError(f"Reference upload failed: {result}")
        return "/".join(part for part in (result.get("subfolder"), result["name"]) if part)

    def check(self, workflow):
        stats = self.request("/system_stats")
        info = self.request("/object_info")
        issues = []
        for node_id, node in workflow.items():
            kind = node["class_type"]
            if kind not in info:
                issues.append(f"노드 미설치: {kind} ({node_id})")
                continue
            schema = info[kind].get("input", {})
            fields = {**schema.get("required", {}), **schema.get("optional", {})}
            for key, value in node["inputs"].items():
                spec = fields.get(key)
                if spec and isinstance(spec[0], list) and not isinstance(value, list) and value not in spec[0]:
                    issues.append(f"사용 불가: {kind}.{key}={value}; 서버 목록={spec[0]}")
        if issues:
            raise ComfyError("워크플로 준비 실패:\n" + "\n".join(issues))
        return stats

    def wait(self, prompt_id, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            entry = self.request("/history/" + prompt_id).get(prompt_id)
            if entry:
                status = entry.get("status", {})
                for kind, detail in status.get("messages", []):
                    if kind in ("execution_error", "execution_interrupted"):
                        raise ComfyError(f"생성 실패 ({prompt_id}): {json.dumps(detail, ensure_ascii=False)}")
                if status.get("status_str") == "error":
                    raise ComfyError(f"생성 실패 ({prompt_id}): {status}")
                if status.get("completed"):
                    return entry
            time.sleep(1)
        raise ComfyError(f"대기 시간 초과: {prompt_id}. 서버 작업은 계속될 수 있습니다. 재생성 대신 fetch --prompt-id {prompt_id} 로 결과를 회수하세요.")

    def save(self, prompt_id, entry, output_dir):
        images = [image for output in entry.get("outputs", {}).values()
                  for image in output.get("images", []) if image.get("type") == "output"]
        if not images:
            raise ComfyError("완료됐지만 저장 이미지가 없습니다. SaveImage 노드를 확인하세요.")
        folder = prepare_output(output_dir)
        saved = []
        for index, image in enumerate(images):
            query = urlencode({key: image.get(key, "") for key in ("filename", "subfolder", "type")})
            body = self.request("/view?" + query, binary=True)
            if not body.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ComfyError("서버가 예상한 PNG 이미지를 반환하지 않았습니다.")
            target = folder / f"{prompt_id}_{index}.png"
            if target.exists():
                if target.read_bytes() != body:
                    raise ComfyError(f"기존 파일과 내용이 다릅니다: {target}")
            else:
                temporary = folder / f".{uuid.uuid4().hex}.part"
                try:
                    temporary.write_bytes(body)
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
            saved.append(str(target))
        return saved


def prepare_output(path):
    folder = Path(path).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    probe = folder / f".write-test-{uuid.uuid4().hex}"
    try:
        with probe.open("xb") as handle:
            handle.write(b"")
    finally:
        probe.unlink(missing_ok=True)
    return folder


def load_workflow(path):
    workflow = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(workflow, dict) or not workflow or any(
        not isinstance(node, dict) or "class_type" not in node or "inputs" not in node
        for node in workflow.values()
    ):
        raise ComfyError("API 형식 워크플로가 필요합니다. 일반 UI JSON은 사용할 수 없습니다.")
    return workflow


def character_folder(output_dir, character=None, output_subdir=None):
    from character_store import CharacterStore
    profile = CharacterStore(ROOT / "characters", output_dir).get(character) if character is not None else None
    return output_folder(output_dir, profile["folder_name"] if profile else character, output_subdir)


def generate_image(client, workflow, prompt, output_dir, timeout=900, seed=None, width=1024, height=1024, *, character=None, output_subdir=None):
    if not prompt.strip():
        raise ComfyError("프롬프트를 입력하세요.")
    if any(size < 64 or size > 2048 or size % 16 for size in (width, height)):
        raise ComfyError("이미지 크기는 64~2048 사이의 16 배수여야 합니다.")
    seed = secrets.randbelow(2**63) if seed is None else seed
    if not 0 <= seed < 2**64:
        raise ComfyError("seed는 0 이상 2**64 미만이어야 합니다.")
    workflow = json.loads(json.dumps(workflow))
    workflow["27"]["inputs"]["text"] = prompt
    workflow["3"]["inputs"]["seed"] = seed
    workflow["13"]["inputs"].update(width=width, height=height)
    folder = character_folder(output_dir, character, output_subdir)
    client.check(workflow)
    prepare_output(folder)  # Fail before enqueueing if destination is unwritable.
    result = client.request("/prompt", {"prompt": workflow, "client_id": str(uuid.uuid4())})
    if result.get("node_errors") or not result.get("prompt_id"):
        raise ComfyError(f"요청 검증 실패: {result}")
    prompt_id = result["prompt_id"]
    print(f"prompt_id={prompt_id} seed={seed}", file=sys.stderr, flush=True)
    entry = client.wait(prompt_id, timeout)
    completed_at = datetime.now(timezone.utc).isoformat()
    files = client.save(prompt_id, entry, folder)
    try:
        metadata_file = save_metadata(folder, prompt_id, workflow, files, character, entry, completed_at)
    except OSError as exc:
        raise ComfyError(f"이미지는 저장됐지만 메타데이터 저장에 실패했습니다. prompt_id={prompt_id}, files={files}: {exc}") from exc
    return {"prompt_id": prompt_id, "seed": seed, "files": files, "metadata_file": metadata_file, "character": character}


def edit_image(client, workflow, bindings, reference_image, instruction, output_dir,
               timeout=900, seed=None, *, character=None):
    """Run a configured official Klein API graph once, with no retries."""
    if not instruction.strip():
        raise ComfyError("Edit instruction is required.")
    reference = Path(reference_image).resolve()
    if not reference.is_file() or not reference.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
        raise ComfyError(f"Reference PNG missing or invalid: {reference}")
    seed = secrets.randbelow(2**63) if seed is None else seed
    if not isinstance(seed, int) or not 0 <= seed < 2**64:
        raise ComfyError("seed must be an integer in [0, 2**64).")
    graph = json.loads(json.dumps(workflow))
    try:
        image_node, image_field = bindings["reference_image"]
        text_node, text_field = bindings["instruction"]
        seed_node, seed_field = bindings["seed"]
        for node, field in ((image_node, image_field), (text_node, text_field), (seed_node, seed_field)):
            if field not in graph[node]["inputs"]:
                raise KeyError(f"{node}.{field}")
        if graph[image_node]["class_type"] != "LoadImage" or image_field != "image":
            raise ValueError("reference_image must bind to LoadImage.image")
    except (KeyError, ValueError, TypeError) as exc:
        raise ComfyError(f"Edit workflow bindings are not ready: {exc}") from exc
    graph[text_node]["inputs"][text_field] = instruction
    graph[seed_node]["inputs"][seed_field] = seed
    # The unique upload does not exist in LoadImage's enum until uploaded.
    preflight = json.loads(json.dumps(graph))
    del preflight[image_node]["inputs"][image_field]
    client.check(preflight)
    folder = prepare_output(character_folder(output_dir, character) / "edits" / uuid.uuid4().hex)
    graph[image_node]["inputs"][image_field] = client.upload_image(reference)
    response = client.request("/prompt", {"prompt": graph, "client_id": str(uuid.uuid4())})
    if response.get("node_errors") or not response.get("prompt_id"):
        raise ComfyError(f"Edit submission failed: {response}")
    prompt_id = response["prompt_id"]
    entry = client.wait(prompt_id, timeout)
    files = client.save(prompt_id, entry, folder)
    try:
        metadata = save_metadata(folder, prompt_id, graph, files, character, entry,
                                 datetime.now(timezone.utc).isoformat(),
                                 prompt=instruction, seed=seed,
                                 extra={"operation": "edit_image", "reference_image": str(reference)})
    except OSError as exc:
        raise ComfyError(f"Images saved but edit metadata failed: prompt_id={prompt_id}, files={files}: {exc}") from exc
    return {"prompt_id": prompt_id, "seed": seed, "files": files,
            "metadata_file": metadata, "character": character, "reference_image": str(reference)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "generate", "fetch"])
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-id")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--character", help="저장용 캐릭터 이름 (프롬프트에는 추가하지 않음)")
    parser.add_argument("--output-subdir", help="output_dir 아래 상대 경로, 예: trpg/london")
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8-sig"))
        client = ComfyClient(config["base_url"])
        timeout = float(config.get("timeout_seconds", 900))
        if timeout <= 0:
            raise ComfyError("timeout_seconds는 양수여야 합니다.")
        if args.command == "fetch":
            if not args.prompt_id:
                parser.error("fetch에는 --prompt-id가 필요합니다.")
            prompt_id = str(uuid.UUID(args.prompt_id))
            entry = client.wait(prompt_id, timeout)
            folder = character_folder(config["output_dir"], args.character, args.output_subdir)
            result = {"prompt_id": prompt_id, "files": client.save(prompt_id, entry, folder)}
        else:
            workflow = load_workflow(args.config.resolve().parent / config["workflow"])
            if args.command == "check":
                stats = client.check(workflow)
                prepare_output(config["output_dir"])
                result = {"ready": True, "base_url": client.base_url, "devices": stats.get("devices"), "output_dir": config["output_dir"]}
            else:
                if not args.prompt:
                    parser.error("generate에는 --prompt가 필요합니다.")
                result = generate_image(client, workflow, args.prompt, config["output_dir"], timeout, args.seed, args.width, args.height, character=args.character, output_subdir=args.output_subdir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ComfyError, OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
