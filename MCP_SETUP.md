# ComfyUI STDIO MCP 설정

## 간편 설치 v1 (Windows ZIP)

### 준비와 실행

1. GitHub ZIP을 **모두 압축 해제**하고 계속 사용할 쓰기 가능한 폴더에 둡니다. Git은 필요 없습니다.
2. [ComfyUI Desktop](https://docs.comfy.org/installation/desktop/windows)에서 로컬 인스턴스를 실행하고 API 주소/포트를 확인합니다. MCP에 PyTorch·CUDA를 설치할 필요는 없습니다.
3. Claude Desktop은 설치 후 한 번 실행합니다. Claude Code를 선택한다면 [Windows 네이티브 Claude Code](https://code.claude.com/docs/en/setup)를 설치하고 `claude` 명령을 사용할 수 있어야 합니다. npm의 `.cmd` 실행기는 v1에서 자동 등록하지 않습니다.
4. 압축을 푼 프로젝트 폴더의 **`install.cmd`를 더블클릭**합니다. PowerShell에서 직접 실행하려면 다음 한 명령과 같습니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

`install.cmd`는 같은 폴더의 `setup.ps1`을 실행하고, 결과를 읽을 수 있도록 창을 멈춰 둡니다.
실행 정책은 이 PowerShell 프로세스에만 적용하며 시스템 정책·PATH·Git 설정은 변경하지 않습니다.
조직 정책으로 실행이 금지되어 있다면 관리자에게 문의하세요.

Python 3.13은 다음 순서로 찾습니다: `-PythonPath`로 지정한 경로 → 기존 `.venv-mcp` →
ComfyUI Desktop에 포함된 Python(`%APPDATA%\Comfy Desktop\installations.json`의 설치 폴더 아래 `standalone-env\python.exe`) →
Python Launcher(`py -3.13`) → PATH의 `python.exe` → 기본 설치 경로. ComfyUI Desktop을 쓰면 Python을 따로 설치할 필요가 없습니다.
그래도 찾지 못하면 [Python 3.13](https://www.python.org/downloads/windows/)을 설치하거나
`setup.ps1 -PythonPath 'C:\실제경로\python.exe'`로 3.13 실행 파일을 지정하세요.
ComfyUI 폴더 질문의 기본값도 같은 Desktop 설치 목록에서 채웁니다. 설치 목록은 읽기만 하며 변경하지 않습니다.

설치하지 않고 기존 환경만 읽기 전용으로 진단하려면 다음 명령을 사용합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -CheckOnly
```

`-CheckOnly`는 파일·폴더 생성, 설정/백업 저장, 패키지 설치, Claude 등록을 수행하지 않습니다.
기존 `.venv-mcp`에서 MCP initialize / tools/list와 ComfyUI GET 진단만 실행합니다.
설정이나 가상환경이 없으면 미확인/실패로 표시하며 자동으로 만들지 않습니다.
읽기 전용 모드에서는 Python Launcher도 호출하지 않습니다. Python을 찾지 못하면 `-PythonPath`로 기존 3.13 실행 파일을 지정하세요.
출력 JSON의 `mcp_connected`(STDIO 연결), `connected`(ComfyUI API), `models_ready`(Qwen 모델·노드)를
각각 확인하세요. Claude 앱 자체의 연결 확인은 별도입니다.
읽기 전용 진단의 종료 코드는 **0: 모든 필수 진단 통과, 1: 필수 진단 실패 또는 미확인**입니다.
Python 직접 호출은 `.\.venv-mcp\Scripts\python.exe -B setup_windows.py --diagnose`입니다.

PowerShell은 `-PythonPath`, `-CheckOnly`, `-Help`/`--help`(`-h`, `-?`)를 지원하고,
Python 보조 파일은 `--diagnose`, `--help`/`-h`를 지원합니다.
도움말은 설치 없이 종료하며, 미지원 옵션·위치 인자는 설치 전에 사용법과 함께 거부합니다(종료 코드 2).

### 설치기가 하는 일과 보존 규칙

- 한국어로 Desktop / Code / 둘 다를 선택합니다. 새 `.venv-mcp`만 필요할 때 생성하고,
  MCP 패키지가 없을 때 해당 가상환경에 `requirements-mcp.txt`를 설치합니다.
  기존 호환 환경은 재사용합니다. 불완전하거나 호환되지 않는 환경은 교체·삭제·자동 업그레이드하지 않습니다.
- `config.json`이 없을 때만 예제를 바탕으로 ComfyUI 폴더·API 주소·출력 폴더를 확인받아 만듭니다.
  결과 경로는 절대 경로로 저장합니다. 기존 `config.json`은 바이트 단위로 보존합니다.
- `check_mcp_tools.py`로 initialize / tools/list를 확인합니다. tools/call과 이미지 생성은 하지 않습니다.
- Desktop은 `claude_desktop_config.json`의 다른 MCP·기타 설정을 보존하여 병합합니다.
  현재 Claude 앱(Microsoft Store판 포함)은 설정을 `%LOCALAPPDATA%\Claude-Data\claude_<ID>\`에 두므로 그 위치를 먼저 사용하고,
  그 폴더가 없는 이전 버전에서만 `%APPDATA%\Claude\`를 사용합니다. 최신 앱에서 `%APPDATA%\Claude`에는 이전 파일만 남아 있어 그곳에 쓰면 적용되지 않습니다.
  기존 파일은 같은 폴더의 `파일명.backup-고유ID.bak`로 원본 전체를 백업합니다.
- Code는 공식 `claude mcp add --transport stdio --scope ...`를 사용합니다.
  기본 `local`은 이 프로젝트만, 선택 `user`는 모든 프로젝트에 적용됩니다. 변경 전
  `%USERPROFILE%\.claude.json`을 같은 방식으로 백업합니다. 사용자 지정 `CLAUDE_CONFIG_DIR`은 v1 자동 등록 대상이 아닙니다.
- 동일 설정으로 재실행하면 등록 파일과 백업을 추가 변경하지 않습니다. 같은 이름이 다른 경로·환경을 가리키면
  덮어쓰지 않고 해당 앱 등록만 실패로 표시합니다. JSON 형식 오류도 원본을 보존한 채 중단합니다.
  CLI 실패 시 백업 경로를 표시하며, 동시에 발생한 다른 변경을 덮어쓸 수 있는 자동 복원은 하지 않습니다.
- 새 등록 항목에 `COMFYUI_MCP_AUTO_START=0`을 지정하여 **이미 실행 중인 로컬 API에만 연결**합니다.
  기존 config와 다른 클라이언트의 8188 자동 시작은 그대로 유지됩니다.
  수동 설정의 `backend.auto_start=false`도 지원하며, 옵션/환경변수가 없으면 기존 자동 시작이 기본입니다.
  환경변수는 `0` / `1`만 지원하고 config보다 우선합니다.
- ComfyUI 진단은 GET `/system_stats`, `/object_info`만 사용합니다. 백엔드 실행·모델 설치·업로드·이미지 생성은 하지 않습니다.
  출력 폴더의 실제 쓰기 권한과 GPU 생성 성공은 이 진단으로 검증하지 않습니다.

필수 모델은 선택한 인스턴스가 사용하는 모델 루트의 다음 위치에 둡니다. 공유 모델 폴더를 쓰면 그 폴더를 기준으로 합니다.
자동 다운로드, 기존 파일 이동·교체, 모델 YAML 수정은 하지 않습니다.

| 하위 폴더 | 필수 파일 |
|---|---|
| `diffusion_models` | `qwen_image_2.1_int8_convrot.safetensors` |
| `text_encoders` | `qwen3vl_8b_int8_convrot.safetensors` |
| `vae` | `qwen_image_2.1_vae_bf16.safetensors` |

[공식 모델 다운로드 안내](https://docs.comfy.org/tutorials/image/qwen/qwen-image-2-1)를 참고하세요.
실제로 사용하는 T2I·편집 그래프의 노드와 모델 선택 목록을 검사하므로 비활성 프롬프트 확장 모델은 요구하지 않습니다.
필수 노드 누락 시 ComfyUI 버전과 시작 로그의 로딩 실패를 확인하세요. 설치기가 업데이트하지는 않습니다.

결과는 **앱별 MCP 등록 / ComfyUI 연결 / Qwen 모델·노드 준비**로 나눠 표시합니다.
등록 완료는 앱에서 실제 연결까지 확인했다는 뜻이 아닙니다. Desktop을 완전히 종료 후 재시작하고,
Code는 이 프로젝트에서 시작하여 `/mcp`로 확인하세요. 서버가 꺼져 있으면 모델 준비는 미확인입니다.
일반 설치의 종료 코드는 `0` 등록·진단 완료, `1` 설치/등록 실패, `2` 등록 완료 후 ComfyUI/Qwen 준비 미완료입니다.
프로젝트 폴더를 옮기면 절대 경로 등록이 달라지므로 기존 항목의 경로를 직접 확인해야 합니다.
백업에는 기존 설정의 비밀값도 포함될 수 있으므로 개인 PC에 보관하고 공유하지 마세요.

### AGENTS.md와 Claude 지침

Codex는 프로젝트의 `AGENTS.md`를 지침으로 읽습니다. Claude Code도 지원 버전과 설정에 따라 직접 읽지만,
v1은 호환성을 위해 Code 등록 후 **없는 경우에만** `CLAUDE.md`를 만들고 `@AGENTS.md`를 넣습니다.
기존 `CLAUDE.md`는 수정하지 않으며 연결이 필요하면 사용자가 직접 한 줄 추가하도록 안내합니다.
`user` scope로 MCP를 등록해도 이 프로젝트 지침이 다른 모든 프로젝트에 자동 적용되지는 않습니다.

일반 Claude Desktop 채팅에는 로컬 `AGENTS.md`가 자동 적용되지 않습니다.
동일 규칙을 원하면 내용을 프로젝트 지침에 복사하거나 대화에 첨부하세요. 짧게 시작하려면 다음 지침을 사용합니다.

> 이미지를 요청했을 때만 comfyui-local을 사용한다. 한국어 요청은 AI가 영어 프롬프트로 구성한다.
> 캐릭터 이름은 먼저 get_character로 조회한다. 신규 캐릭터는 create_character_draft,
> 등록 캐릭터의 새 장면은 create_character_scene, 부분 수정은 edit_image를 사용한다.
> 사용자 승인·등록 요청 전에는 save_character로 저장하거나 공식 이미지를 변경하지 않는다.
> 사용자가 다른 화풍을 지정하지 않으면 delicate semi-realistic 2D anime illustration,
> intricate refined linework, natural facial proportions, soft coloring을 사용한다.
> 신규 초안은 별도 구도 요청이 없으면 허리 위 프로필로 요청한다.
> 생성은 요청당 한 번만 수행하며 시간 초과 후 자동 재시도하지 않는다. 결과 PNG·JSON 경로를 알려준다.

MCP는 현재 이미지 경로를 반환하므로 Desktop이 로컬 PNG를 자동으로 볼 수 있다고 가정하지 마세요.

구현 검증은 `test_setup_windows.py`에서 임시 프로젝트·설정·모의 CLI/API만 사용합니다.
실제 사용자 Claude 설정에 등록하거나 패키지·모델을 다운로드하는 테스트는 하지 않습니다.

## 구현 범위

기존 클라이언트와 저장 함수를 재사용하는 Qwen Image 2.1 전용 STDIO 서버입니다.
서버 시작과 tools/list에서는 ComfyUI 접속이나 이미지 생성을 하지 않습니다.
기존 config.json은 변경 없이 사용하며 이전 workflow/edit_engine/edit_workflow 설정은 무시합니다.
comfyui_status와 CLI check는 Qwen T2I 노드와 모델을 검사합니다.

공식 Python MCP SDK의 FastMCP/STDIO를 사용합니다. 독립 패키지 fastmcp가 아닙니다. SDK v1 유지보수 계열 API에 맞춰 `mcp>=1.28,<2`로 제한했습니다. 공식 문서상 현재 주 안정 계열은 v2이며, 이 코드는 v2 API로 자동 전환하지 않습니다. Python은 기존 Desktop의 3.13을 기반으로 프로젝트 전용 .venv-mcp를 만들며, ComfyUI의 패키지를 설치하거나 변경하지 않습니다.

| 도구 | 입력 | 동작 / 반환 |
|---|---|---|
| comfyui_status | 없음 | 서버와 워크플로/모델 목록 확인. ready, devices, output_dir 등 반환. 파일 생성 및 쓰기 권한 검사는 하지 않음 |
| generate_image | prompt 필수, character/seed 선택 | Qwen 2.1 T2I 생성/저장 함수 실행. prompt_id, seed, files(PNG 경로 목록), metadata_file(JSON 경로), character 반환 |
| edit_image | reference_image/instruction/character/seed/style_reference_image | Qwen 편집. 스타일 참조는 선택적이며 명시된 경우만 사용 |
| create_character_draft | character_settings, prompt, seed 선택 | Qwen T2I 승인 대기 초안. 자동 등록 없음 |
| create_character_scene | character, scene_description, seed/extra_references(이미지별로 가져올 것을 적은 목록)/pose_image·style_reference_image(바로가기) 선택 | 공식 reference_v1.png(필수)와 등록된 reference_v2.png…를 검증하여 Qwen 새 장면 생성. 장수 제한은 config.json references (기본 공식 4장, 전체 8장, 최대 16장) |
| get_character | name | 설정과 참조 경로 조회 |
| save_character | name 및 선택 설정 | 명시적으로 제공한 설정만 저장 |

생성 크기는 기존 기본값인 1024×1024, 1장입니다. character는 `<output_dir>\캐릭터명` 저장용이며 프롬프트에 자동 삽입하지 않습니다. output-subdir는 이번 MCP 도구 인자에 노출하지 않았으며 CLI에서는 계속 사용할 수 있습니다. 반환값은 경로이며 이미지 바이너리/미리보기 전송은 하지 않습니다.

로그는 stderr, stdout은 SDK의 MCP 프로토콜 전용입니다. main()을 호출하지 않고 기존 함수를 import하므로 CLI의 결과 print는 실행되지 않습니다. 동기 API 작업은 별도 스레드에서 실행합니다. 같은 MCP 프로세스의 동시 생성은 거부하지만, Claude와 ChatGPT가 각각 서버를 시작하면 서로 다른 프로세스이므로 ComfyUI의 대기열에 함께 제출할 수 있습니다.

## 설치 및 도구 목록 확인 (이미지 생성 없음)

전용 가상환경 준비, SDK 설치, tools/list 확인은 `install.cmd`(`setup.ps1`)가 함께 수행합니다.
이미 가상환경이 있으면 재사용하며, ComfyUI가 꺼져 있어도 목록 조회는 가능합니다.
설치 후 변경 없이 다시 확인만 하려면 `setup.ps1 -CheckOnly`를 사용합니다.

`install.cmd`로 `.venv-mcp`를 만든 뒤에는 아래 Python 명령으로 같은 작업을 직접 실행할 수도 있습니다:

```powershell
.\.venv-mcp\Scripts\python.exe -B -m ensurepip --upgrade
.\.venv-mcp\Scripts\python.exe -B -m pip install -r requirements-mcp.txt
.\.venv-mcp\Scripts\python.exe -B check_mcp_tools.py
.\.venv-mcp\Scripts\python.exe -B -m unittest -v test_client test_mcp_adapter
```

목록 테스트는 공식 SDK 클라이언트로 실제 서버 자식 프로세스를 띄운 뒤 initialize와 tools/list만 호출합니다. 도구 7개의 이름과 생성 도구 입력 스키마를 검사합니다. 성공하면 `ok: true`, `image_generation_requested: false`, 7개 도구의 스키마가 표시됩니다. 테스트 클라이언트가 터미널에 표시하는 JSON은 서버의 프로토콜 stdout과 별개입니다.

전체 모의 테스트: `.\.venv-mcp\Scripts\python.exe -B -m unittest discover -v`.
STDIO 목록 검증: `.\.venv-mcp\Scripts\python.exe -B check_mcp_tools.py` (tools/call 없음).
실제 GPU의 Qwen T2I, 단일/스타일 참조 편집, 캐릭터 장면은 별도 생성 검증이 필요합니다.

## Windows Claude Desktop

1. 위 SDK 설치와 목록 테스트를 완료합니다.
2. Claude Desktop → Settings → Developer → Edit Config를 엽니다.
3. Edit Config가 연 `claude_desktop_config.json`(최신 앱은 `%LOCALAPPDATA%\Claude-Data\claude_<ID>\`, 이전 버전은 `%APPDATA%\Claude\`)의 기존 `mcpServers` 안에 `claude_desktop_config.example.json`의 `comfyui-local` 항목을 병합합니다. 기존 다른 서버 설정을 덮어쓰지 마세요.
4. Claude Desktop을 완전히 종료하고 재시작합니다.
5. 먼저 “comfyui_status 도구로 연결 상태만 확인해줘. 이미지는 생성하지 마.”라고 요청합니다.

설정에 사용할 값 (경로는 압축을 푼 실제 폴더로 바꾸세요):

```json
{
  "mcpServers": {
    "comfyui-local": {
      "command": "C:\\path\\to\\comfyui-automation\\.venv-mcp\\Scripts\\python.exe",
      "args": ["-B", "-u", "C:\\path\\to\\comfyui-automation\\mcp_server.py"],
      "env": {"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    }
  }
}
```

등록 예시만 작성했으며 실제 Claude 설정 파일은 수정하지 않았습니다. SDK 설치 전에 등록하면 시작 시 import 오류가 납니다. Claude의 응답 제한으로 도구가 중단돼도 ComfyUI 작업이 계속될 수 있으므로 즉시 재요청하지 말고 Desktop 기록/출력 폴더를 확인하세요. 로그의 prompt_id가 있으면 기존 CLI fetch로 이미지를 회수할 수 있으나 fetch는 JSON을 새로 만들지 않습니다.

## ChatGPT Desktop과 웹의 차이 (2026-11-08 공식 문서 확인)

### Desktop의 로컬 Codex 호스트

현재 ChatGPT Learn 문서는 ChatGPT Desktop, Codex CLI, IDE가 로컬 MCP 설정을 공유하고 STDIO와 Streamable HTTP를 지원한다고 안내합니다. 따라서 이 프로젝트에는 STDIO를 그대로 사용하면 됩니다. 사용자 설치 버전의 UI에서 실제 등록/연결까지 확인한 것은 아닙니다.

Settings → MCP servers → Add server에서 이름 `comfyui-local`, 방식 STDIO, 위 JSON과 동일한 command/args를 입력하고 Save → Restart합니다. `/mcp`에서 연결 상태를 확인합니다.

또는 `chatgpt_mcp_config.example.toml` 항목을 `%USERPROFILE%\.codex\config.toml`에 병합할 수 있습니다. 기존 파일을 통째로 교체하거나 같은 테이블을 중복 추가하지 마세요. 예시의 tool_timeout_sec=1200은 기존 생성 대기 900초보다 길게 잡았습니다. 기본 MCP 도구 제한 60초는 모델 첫 로딩 때 부족할 수 있습니다. 실제 로컬 설정은 수정하지 않았습니다.

### 일반 ChatGPT 웹의 커스텀 MCP/플러그인 연결

웹 연결 UI는 로컬 Python 실행 명령을 등록하는 방식이 아닙니다. 공식 도움말은 HTTPS MCP endpoint 또는 Secure MCP Tunnel을 안내합니다. 계정/워크스페이스 정책도 적용됩니다. 현재 STDIO 서버를 웹의 URL 칸에 넣을 수 없으며, 이 연결 방식은 이번에 구현하지 않았습니다. `http://127.0.0.1:8188`은 ComfyUI API 주소이고 MCP endpoint가 아닙니다.

## 공식 참고 자료

- Python SDK v1 범위: https://py.sdk.modelcontextprotocol.io/v1/
- 서버/STDIO: https://py.sdk.modelcontextprotocol.io/v1/server/
- ClientSession / stdio_client / list_tools: https://py.sdk.modelcontextprotocol.io/v1/client/
- Claude Desktop 로컬 등록: https://modelcontextprotocol.io/docs/develop/connect-local-servers
- ChatGPT Desktop / Codex STDIO 설정: https://learn.chatgpt.com/docs/extend/mcp?surface=app
- ChatGPT 웹 커스텀 연결: https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt
