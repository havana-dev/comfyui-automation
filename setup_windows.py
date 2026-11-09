"""Windows installer; stdlib only. Importing this module never installs anything."""
import sys

# Also protect direct --diagnose calls made without Python's -B flag.
sys.dont_write_bytecode = True

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener
import uuid

NAME = "comfyui-local"
MODELS = {
    "UNETLoader": ("unet_name", "qwen_image_2.1_int8_convrot.safetensors", "diffusion_models"),
    "CLIPLoader": ("clip_name", "qwen3vl_8b_int8_convrot.safetensors", "text_encoders"),
    "VAELoader": ("vae_name", "qwen_image_2.1_vae_bf16.safetensors", "vae"),
}
MODEL_GUIDE = "https://docs.comfy.org/tutorials/image/qwen/qwen-image-2-1"
DESKTOP_GUIDE = "https://docs.comfy.org/installation/desktop/windows"


class SetupError(Exception):
    pass


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("중복된 JSON 키")
            result[key] = value
        return result
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=pairs)
        if not isinstance(data, dict):
            raise ValueError("JSON 최상위 값이 객체가 아님")
        return data
    except (ValueError, UnicodeError) as exc:
        raise SetupError(f"설정 파일 형식을 읽지 못했습니다. 원본은 변경하지 않습니다: {path}") from exc


def snapshot(path):
    return path.read_bytes() if path.exists() else None


def backup(path, original):
    if original is None:
        return None
    destination = path.with_name(path.name + f".backup-{uuid.uuid4().hex}.bak")
    with destination.open("xb") as stream:
        stream.write(original)
    print(f"설정 원본 백업: {destination}")
    return destination


def write_json(path, data, original):
    """Compare before replacing; new files are created exclusively, never clobbered."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if snapshot(path) != original:
        raise SetupError(f"다른 프로그램이 설정을 변경했습니다. 다시 실행하세요: {path}")
    if original is None:
        with path.open("xb") as stream:
            stream.write(encoded)
        return
    backup(path, original)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".setup-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
        if snapshot(path) != original:
            raise SetupError(f"설정이 동시에 변경되어 저장하지 않았습니다: {path}")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)  # Only our newly-created temporary file.


def launcher(root, python):
    return {"command": str(python), "args": ["-B", "-u", str(root / "mcp_server.py")],
            "env": {"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "COMFYUI_MCP_AUTO_START": "0"}}


def matches(existing, desired):
    return isinstance(existing, dict) and existing.get("type", "stdio") == "stdio" and all(
        (isinstance(existing.get(key), dict) and all(existing[key].get(k) == v for k, v in value.items()))
        if isinstance(value, dict) else existing.get(key) == value
        for key, value in desired.items())


def server_map(config):
    servers = config.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise SetupError("mcpServers 형식이 잘못되어 원본을 변경하지 않습니다.")
    return servers


def register_desktop(path, entry):
    original = snapshot(path)
    data = read_json(path) if original is not None else {}
    servers = server_map(data)
    if NAME in servers:
        if matches(servers[NAME], entry):
            return "이미 등록됨 (변경 없음)"
        raise SetupError("Desktop에 다른 comfyui-local 설정이 있습니다. 기존 설정을 보존했습니다. Developer 설정에서 경로를 확인하세요.")
    data.setdefault("mcpServers", {})[NAME] = entry
    write_json(path, data, original)
    return "등록 완료 (Claude Desktop 완전 종료 후 재시작 필요)"


def run_command(args, *, cwd, timeout=120):
    try:
        result = subprocess.run([str(arg) for arg in args], cwd=cwd, timeout=timeout,
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupError("명령을 실행하지 못했거나 시간이 초과되었습니다. 경로·권한·네트워크를 확인하세요.") from exc
    if result.returncode:
        # Do not echo CLI config / unrelated credentials into the installer output.
        raise SetupError(f"명령이 실패했습니다 (종료 코드 {result.returncode}). 실행 파일: {args[0]}")
    return result.stdout


def local_project(data, root):
    projects = data.get("projects", {})
    if not isinstance(projects, dict):
        raise SetupError("Claude Code projects 설정 형식이 잘못되었습니다.")
    key = os.path.normcase(os.path.normpath(str(root)))
    for candidate, project in projects.items():
        if os.path.normcase(os.path.normpath(candidate)) == key:
            if not isinstance(project, dict):
                raise SetupError("Claude Code 프로젝트 설정 형식이 잘못되었습니다.")
            return project
    return {}


def preserves(before, after):
    """The CLI may add keys; every pre-existing value must survive unchanged."""
    if isinstance(before, dict):
        return isinstance(after, dict) and all(k in after and preserves(v, after[k]) for k, v in before.items())
    return before == after


def register_code(root, config_path, entry, scope="local", *, cli=None, runner=run_command):
    if scope not in ("local", "user"):
        raise SetupError("v1은 Claude Code local 또는 user scope만 지원합니다.")
    cli = cli or shutil.which("claude.exe") or shutil.which("claude")
    if not cli or Path(cli).suffix.lower() in (".cmd", ".bat", ".ps1"):
        raise SetupError("Windows 네이티브 Claude Code 실행 파일이 필요합니다. https://code.claude.com/docs/en/setup 설치 후 다시 실행하세요.")
    original = snapshot(config_path)
    before = read_json(config_path) if original is not None else {}
    project_file = root / ".mcp.json"
    shared = read_json(project_file) if project_file.exists() else {}
    maps = [server_map(local_project(before, root)), server_map(shared), server_map(before)]
    existing = [servers[NAME] for servers in maps if NAME in servers]
    if any(not matches(item, entry) for item in existing):
        raise SetupError("Claude Code의 다른 scope에 comfyui-local 설정이 있습니다. 기존 설정을 보존했습니다. claude mcp get comfyui-local 로 확인하세요.")
    if existing:
        return "이미 등록됨 (변경 없음)"
    backup_path = backup(config_path, original)
    if snapshot(config_path) != original:
        raise SetupError("Claude Code 설정이 동시에 변경되었습니다. 다시 실행하세요.")
    try:
        # --env is variadic: an option must separate its values from the server name.
        runner([cli, "mcp", "add", "--scope", scope,
                "--env", "PYTHONUTF8=1", "PYTHONDONTWRITEBYTECODE=1", "COMFYUI_MCP_AUTO_START=0",
                "--transport", "stdio", NAME, "--", entry["command"], *entry["args"]], cwd=root)
        after = read_json(config_path)
        selected = local_project(after, root) if scope == "local" else after
        if not matches(server_map(selected).get(NAME), entry) or not preserves(before, after):
            raise SetupError("CLI 실행 후 등록 내용 또는 기존 설정 보존을 확인하지 못했습니다.")
    except (SetupError, OSError) as exc:
        raise SetupError(f"Claude Code 등록을 확인하지 못했습니다. 자동 복원으로 다른 변경을 덮어쓰지 않습니다. 백업: {backup_path or '기존 파일 없음'} / {exc}") from exc
    return f"등록 완료 ({scope} scope, 프로젝트에서 /mcp로 연결 확인)"


def link_instructions(root):
    path = root / "CLAUDE.md"
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write("@AGENTS.md\n")
        return "CLAUDE.md 생성: @AGENTS.md 연결"
    except FileExistsError:
        return "기존 CLAUDE.md 보존. AGENTS.md 연결이 없다면 @AGENTS.md 한 줄을 직접 추가하세요."


def probe_python(python, root, runner=run_command):
    source = "import sys,json,importlib.metadata as m; print(json.dumps({'version':list(sys.version_info[:2]),'venv':sys.prefix!=sys.base_prefix,'mcp':next((d.version for d in m.distributions() if d.metadata['Name'].lower()=='mcp'),None)}))"
    try:
        return json.loads(runner([python, "-B", "-c", source], cwd=root, timeout=30))
    except (ValueError, SetupError) as exc:
        raise SetupError(f"Python 환경을 확인하지 못했습니다: {python}") from exc


def prepare_environment(root, base_python, runner=run_command):
    environment = root / ".venv-mcp"
    python = environment / "Scripts" / "python.exe"
    if environment.exists():
        if not python.is_file():
            raise SetupError("기존 .venv-mcp가 불완전합니다. 삭제·재생성하지 않았습니다. 별도 폴더로 보관한 뒤 다시 실행하세요.")
    else:
        runner([base_python, "-B", "-m", "venv", environment], cwd=root)
    info = probe_python(python, root, runner)
    if info.get("version") != [3, 13] or not info.get("venv"):
        raise SetupError(".venv-mcp는 Python 3.13 가상환경이어야 합니다. 기존 환경은 교체하지 않았습니다.")
    version = info.get("mcp")
    if version is not None:
        try:
            major, minor = (int(part) for part in version.split(".")[:2])
        except ValueError as exc:
            raise SetupError("기존 MCP 패키지 버전을 확인하지 못했습니다.") from exc
        if major != 1 or minor < 28:
            raise SetupError("기존 MCP SDK 버전이 호환되지 않습니다. 자동 업그레이드·다운그레이드는 하지 않았습니다.")
    else:
        print("프로젝트 전용 가상환경에 MCP 패키지를 설치합니다. 인터넷 연결이 필요합니다.")
        try:
            runner([python, "-B", "-m", "pip", "install", "--disable-pip-version-check", "-r", root / "requirements-mcp.txt"], cwd=root, timeout=300)
        except SetupError as exc:
            raise SetupError("MCP 패키지 설치 실패. 인터넷·프록시·pip 상태를 확인한 뒤 다시 실행하세요.") from exc
    return python


def validate_url(url):
    if not isinstance(url, str):
        raise SetupError("ComfyUI 주소는 문자열이어야 합니다. config.json의 base_url을 확인하세요.")
    try:
        address = urlsplit(url)
        valid = (address.scheme == "http" and address.hostname in ("127.0.0.1", "localhost", "::1")
                 and not address.username and not address.password and address.path in ("", "/")
                 and not address.query and not address.fragment and address.port != 0)
    except ValueError:
        valid = False
    if not valid:
        raise SetupError("ComfyUI 주소는 http://127.0.0.1:8188 같은 로컬 HTTP API 주소여야 합니다.")
    return url.rstrip("/")


def ask(prompt, default="", input_fn=input):
    value = input_fn(f"{prompt}" + (f" [{default}]" if default else "") + ": ").strip()
    return value or default


def confirm(prompt, input_fn=input):
    return ask(prompt + " (y/N)", input_fn=input_fn).lower() in ("y", "yes", "예", "네")


def prepare_config(root, input_fn=input):
    path = root / "config.json"
    if path.exists():
        config = read_json(path)
        validate_url(config.get("base_url", ""))
        if not isinstance(config.get("output_dir"), str) or not config["output_dir"].strip():
            raise SetupError("기존 config.json의 output_dir를 확인하세요. 파일은 변경하지 않았습니다.")
        if not isinstance(config.get("backend", {}), dict):
            raise SetupError("기존 config.json의 backend 형식을 확인하세요. 파일은 변경하지 않았습니다.")
        print("기존 config.json을 그대로 사용합니다 (변경 없음).")
        return config
    print(f"ComfyUI Desktop이 없다면 먼저 설치하세요: {DESKTOP_GUIDE}")
    print("Desktop에서 선택한 로컬 설치/인스턴스 폴더를 입력하세요. ComfyUI 또는 main.py가 있는 폴더입니다.")
    instance = Path(ask("ComfyUI 폴더", input_fn=input_fn).strip('"')).expanduser()
    if not ((instance / "main.py").is_file() or (instance / "ComfyUI" / "main.py").is_file()):
        raise SetupError("ComfyUI/main.py 또는 main.py를 찾지 못했습니다. Desktop에서 실제 설치 폴더를 확인하세요.")
    instance = instance.resolve()
    url = validate_url(ask("실행 중인 ComfyUI의 API 주소 (Desktop 로그에서 포트 확인)", "http://127.0.0.1:8188", input_fn))
    output = Path(ask("이미지 결과를 보관할 폴더", str(root / "outputs"), input_fn).strip('"')).expanduser().resolve()
    if output.exists() and not output.is_dir():
        raise SetupError("출력 경로가 폴더가 아닙니다. 다른 경로를 선택하세요.")
    print(f"ComfyUI 폴더: {instance}\nAPI: {url}\n결과 저장: {output}\nComfyUI 자동 시작: 사용 안 함")
    if not confirm("이 경로로 새 config.json을 만들까요?", input_fn):
        raise SetupError("경로 설정을 취소했습니다. config.json을 만들지 않았습니다.")
    config = read_json(root / "config.example.json")
    config.update(base_url=url, output_dir=str(output))
    config["backend"] = {"auto_start": False, "base_dir": str(instance)}
    write_json(path, config, None)
    return config


def active_requirements(root):
    """Use prepared graphs, not inactive template prompt-enhancer branches."""
    from character_draft import prepare_draft
    from character_scene import prepare_qwen_graph
    import character_draft
    import character_scene
    # Explicit root lets tests use copied workflows without touching user data.
    old_draft, old_scene = character_draft.ROOT, character_scene.ROOT
    try:
        character_draft.ROOT = character_scene.ROOT = root
        draft, *_ = prepare_draft({"appearance": "diagnostic only"}, "diagnostic only", seed=0)
        scene = prepare_qwen_graph()
        return sorted({node["class_type"] for graph in (draft, scene) for node in graph.values()})
    finally:
        character_draft.ROOT, character_scene.ROOT = old_draft, old_scene


def get_json(url):
    with build_opener(ProxyHandler({})).open(url, timeout=8) as response:
        return json.loads(response.read())


def diagnose(root, config, *, request=get_json):
    result = {"connected": False, "models_ready": False, "missing_nodes": [], "missing_models": [], "error": None}
    try:
        url = validate_url(config["base_url"])
        stats = request(url + "/system_stats")
        if not isinstance(stats, dict) or "system" not in stats or "devices" not in stats:
            raise SetupError("ComfyUI API 응답이 아닙니다. 주소와 포트를 확인하세요.")
        result["connected"] = True
        info = request(url + "/object_info")
        if not isinstance(info, dict):
            raise SetupError("노드 목록 응답 형식을 확인하지 못했습니다.")
        result["missing_nodes"] = [name for name in active_requirements(root) if name not in info]
        for node, (field, filename, folder) in MODELS.items():
            schema = info.get(node, {}).get("input", {})
            spec = {**schema.get("required", {}), **schema.get("optional", {})}.get(field)
            if not spec or not isinstance(spec[0], list) or filename not in spec[0]:
                result["missing_models"].append((filename, folder))
        result["models_ready"] = not result["missing_models"] and not result["missing_nodes"]
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError, ImportError, SetupError, RuntimeError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def report_diagnosis(config, result):
    print("ComfyUI 연결: " + ("성공" if result["connected"] else "미확인 — Desktop에서 인스턴스를 실행하고 주소·포트를 확인하세요."))
    print("Qwen 모델·노드 준비: " + ("완료 (실제 GPU 생성은 미검증)" if result["models_ready"] else "미완료 또는 미확인"))
    backend = Path(config.get("backend", {}).get("base_dir", "."))
    model_root = (backend if (backend / "main.py").is_file() else backend / "ComfyUI") / "models"
    for name in result["missing_nodes"]:
        print(f"  누락 노드: {name} — ComfyUI 버전 및 시작 로그의 노드 로딩 오류를 확인하세요.")
    for filename, folder in result["missing_models"]:
        print(f"  누락 모델: {filename}\n    기본 설치 위치 예시: {model_root / folder / filename}")
    if not result["models_ready"]:
        print(f"모델 설치 안내: {MODEL_GUIDE}\nDesktop이 공유 모델 폴더를 사용한다면 그 폴더의 diffusion_models / text_encoders / vae에 저장하세요. 기존 YAML·모델은 변경하지 않습니다.")
    if result["error"]:
        print("진단 내용: " + result["error"])


def read_only_diagnosis(root):
    """No prepare/install/register calls, even when config or venv is missing."""
    sys.dont_write_bytecode = True
    try:
        config = read_json(root / "config.json")
        result = diagnose(root, config)
    except (SetupError, OSError) as exc:
        result = {"connected": False, "models_ready": False, "missing_nodes": [],
                  "missing_models": [], "error": f"설정을 읽지 못했습니다 (새로 만들지 않음): {exc}"}
    result.update(mcp_connected=False, mcp_error=None)
    python = root / ".venv-mcp" / "Scripts" / "python.exe"
    try:
        if not python.is_file():
            raise SetupError("기존 .venv-mcp Python이 없습니다. 진단 모드에서는 생성·설치하지 않습니다.")
        listed = json.loads(run_command([python, "-B", root / "check_mcp_tools.py"], cwd=root, timeout=30))
        if not isinstance(listed, dict) or listed.get("ok") is not True or listed.get("image_generation_requested") is not False:
            raise SetupError("MCP 도구 목록 응답을 확인하지 못했습니다.")
        result["mcp_connected"] = True
    except (SetupError, OSError, ValueError) as exc:
        result["mcp_error"] = f"MCP STDIO 연결 확인 실패 (패키지 설치 안 함): {exc}"
    result["MCP 연결"] = "성공 (STDIO 도구 목록 확인, Claude 앱 연결은 별도 확인)" if result["mcp_connected"] else "실패 또는 미확인"
    result["ComfyUI 연결"] = "성공" if result["connected"] else "실패 또는 미확인"
    result["Qwen 모델·노드 준비"] = "완료 (실제 GPU 생성은 미검증)" if result["models_ready"] else "미완료 또는 미확인"
    return result


def main():
    root = Path(__file__).resolve().parent
    if os.name != "nt" or sys.version_info[:2] != (3, 13):
        raise SetupError("이 설치기는 Windows의 Python 3.13에서 실행해야 합니다.")
    target = ask("설치 대상: 1 Claude Desktop / 2 Claude Code / 3 둘 다", "1")
    if target not in ("1", "2", "3"):
        raise SetupError("설치 대상은 1, 2, 3 중 하나를 선택하세요.")
    # Config prompts run before package downloads. Existing config is never rewritten.
    config = prepare_config(root)
    python = prepare_environment(root, Path(sys.executable))
    print("이미지 생성 없이 MCP 도구 목록을 확인합니다.")
    try:
        listed = json.loads(run_command([python, "-B", root / "check_mcp_tools.py"], cwd=root, timeout=60))
        if listed.get("ok") is not True or listed.get("image_generation_requested") is not False:
            raise ValueError("unexpected check result")
    except (ValueError, SetupError) as exc:
        raise SetupError("MCP 도구 목록을 확인하지 못했습니다. SDK 의존성·프로젝트 파일을 확인하세요. 앱 등록은 수행하지 않았습니다.") from exc
    print(f"MCP 도구 목록: {len(listed['tools'])}개 확인")
    # API graph checks use only stdlib; MCP was independently checked above.
    diagnosis = diagnose(root, config)
    entry = launcher(root, python)
    outcomes = {}
    if target in ("1", "3"):
        try:
            directory = Path(os.environ["APPDATA"]) / "Claude"
            if not directory.is_dir():
                raise SetupError("Claude Desktop을 설치하고 한 번 실행한 뒤 다시 시도하세요: https://claude.ai/download")
            outcomes["Claude Desktop MCP 등록"] = register_desktop(directory / "claude_desktop_config.json", entry)
        except (SetupError, OSError, KeyError) as exc:
            outcomes["Claude Desktop MCP 등록"] = "실패 — " + str(exc)
    if target in ("2", "3"):
        try:
            if os.environ.get("CLAUDE_CONFIG_DIR"):
                raise SetupError("사용자 지정 CLAUDE_CONFIG_DIR은 v1 자동 등록 대상이 아닙니다. 기본 환경에서 설치하거나 공식 CLI로 직접 등록하세요.")
            scope = ask("Claude Code 범위: 1 이 프로젝트만(local) / 2 모든 프로젝트(user)", "1")
            if scope not in ("1", "2"):
                raise SetupError("등록 범위는 1 또는 2를 선택하세요.")
            outcomes["Claude Code MCP 등록"] = register_code(root, Path.home() / ".claude.json", entry, "local" if scope == "1" else "user")
        except (SetupError, OSError) as exc:
            outcomes["Claude Code MCP 등록"] = "실패 — " + str(exc)
        else:
            try:
                print(link_instructions(root))
            except OSError:
                print("MCP 등록은 완료했지만 CLAUDE.md를 만들지 못했습니다. 쓰기 권한을 확인하고 @AGENTS.md 연결을 직접 추가하세요.")
    print("\n설치 결과")
    for name, outcome in outcomes.items():
        print(f"{name}: {outcome}")
    report_diagnosis(config, diagnosis)
    print("AGENTS.md는 Codex 프로젝트 지침입니다. Claude Code는 이 폴더에서 시작하고 CLAUDE.md의 @AGENTS.md 연결을 확인하세요.")
    print("일반 Claude Desktop 채팅에는 AGENTS.md가 자동 적용되지 않습니다. 같은 규칙이 필요하면 파일 내용을 프로젝트 지침에 복사하거나 대화에 첨부하세요. MCP_SETUP.md의 짧은 지침도 사용할 수 있습니다.")
    print("이미지 생성·모델 다운로드는 수행하지 않았습니다. ZIP 폴더를 옮기면 등록 경로가 달라지므로 다시 확인하세요.")
    return 1 if any(value.startswith("실패") for value in outcomes.values()) else (0 if diagnosis["models_ready"] else 2)


def cli(argv=None):
    parser = argparse.ArgumentParser(
        description="Windows comfyui-local 설치. --diagnose는 변경 없는 읽기 전용 진단입니다.",
        allow_abbrev=False)
    parser.add_argument("--diagnose", action="store_true", help="설치·설정 변경 없이 기존 환경만 진단")
    options = parser.parse_args(argv)
    try:
        if options.diagnose:
            project = Path(__file__).resolve().parent
            result = read_only_diagnosis(project)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["mcp_connected"] and result["connected"] and result["models_ready"] and not result["error"] else 1
        return main()
    except (SetupError, OSError, EOFError, KeyboardInterrupt) as exc:
        print(f"설치를 완료하지 못했습니다: {exc}\n기존 파일·모델·캐릭터 데이터는 삭제하지 않았습니다.", file=sys.stderr)
        print("MCP 등록: 미완료\nComfyUI 연결: 미확인\nQwen 모델·노드 준비: 미확인", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(cli())
