# ComfyUI STDIO MCP 설정

## 구현 범위

기존 comfy_client.py, result_store.py, run.ps1, config.json 및 워크플로는 수정하지 않았습니다. 새 mcp_server.py가 기존 generate_image 함수를 호출합니다. 서버 시작과 tools/list에서는 ComfyUI 접속이나 이미지 생성을 하지 않습니다.

공식 Python MCP SDK의 FastMCP/STDIO를 사용합니다. 독립 패키지 fastmcp가 아닙니다. SDK v1 유지보수 계열 API에 맞춰 `mcp>=1.28,<2`로 제한했습니다. 공식 문서상 현재 주 안정 계열은 v2이며, 이 코드는 v2 API로 자동 전환하지 않습니다. Python은 기존 Desktop의 3.13을 기반으로 프로젝트 전용 .venv-mcp를 만들며, ComfyUI의 패키지를 설치하거나 변경하지 않습니다.

| 도구 | 입력 | 동작 / 반환 |
|---|---|---|
| comfyui_status | 없음 | 서버와 워크플로/모델 목록 확인. ready, devices, output_dir 등 반환. 파일 생성 및 쓰기 권한 검사는 하지 않음 |
| generate_image | prompt 필수, character/seed 선택 | 기존 생성/저장 함수 실행. prompt_id, seed, files(PNG 경로 목록), metadata_file(JSON 경로), character 반환 |

생성 크기는 기존 기본값인 1024×1024, 1장입니다. character는 `E:\AI\Outputs\캐릭터명` 저장용이며 프롬프트에 자동 삽입하지 않습니다. output-subdir는 이번 MCP 도구 인자에 노출하지 않았으며 CLI에서는 계속 사용할 수 있습니다. 반환값은 경로이며 이미지 바이너리/미리보기 전송은 하지 않습니다.

로그는 stderr, stdout은 SDK의 MCP 프로토콜 전용입니다. main()을 호출하지 않고 기존 함수를 import하므로 CLI의 결과 print는 실행되지 않습니다. 동기 API 작업은 별도 스레드에서 실행합니다. 같은 MCP 프로세스의 동시 생성은 거부하지만, Claude와 ChatGPT가 각각 서버를 시작하면 서로 다른 프로세스이므로 ComfyUI의 대기열에 함께 제출할 수 있습니다.

## 설치 및 도구 목록 확인 (이미지 생성 없음)

일반 Windows PowerShell에서:

```powershell
cd E:\Dev\comfyui-automation
.\setup_mcp.ps1
```

setup_mcp.ps1은 전용 가상환경 준비, SDK 설치, tools/list 확인만 수행합니다. 이미 가상환경이 있으면 재사용합니다. ComfyUI가 꺼져 있어도 목록 조회는 가능합니다.

PowerShell 실행 정책 때문에 ps1이 막히면 정책 변경 없이 아래 Python 명령을 사용합니다. 가상환경은 현재 프로젝트에 이미 생성돼 있습니다:

```powershell
.\.venv-mcp\Scripts\python.exe -B -m ensurepip --upgrade
.\.venv-mcp\Scripts\python.exe -B -m pip install -r requirements-mcp.txt
.\.venv-mcp\Scripts\python.exe -B check_mcp_tools.py
.\.venv-mcp\Scripts\python.exe -B -m unittest -v test_client test_mcp_adapter
```

목록 테스트는 공식 SDK 클라이언트로 실제 서버 자식 프로세스를 띄운 뒤 initialize와 tools/list만 호출합니다. 도구 2개의 이름과 생성 도구 입력 스키마를 검사합니다. 성공하면 `ok: true`, `image_generation_requested: false`, 두 도구의 스키마가 표시됩니다. 테스트 클라이언트가 터미널에 표시하는 JSON은 서버의 프로토콜 stdout과 별개입니다.

현재 검증 상태: 기존 CLI 모의 테스트 7개 통과. 에이전트 환경에서 pip가 mcp 배포본을 찾지 못해 SDK 설치 실패 (`No matching distribution found`). 따라서 실제 STDIO 목록 조회 및 SDK 어댑터 테스트는 미검증입니다. check_mcp_tools.py 실행 시 SDK 미설치 오류를 확인했습니다. 이미지 생성은 요청하지 않았습니다. 일반 PowerShell 설치 후 위 테스트로 검증을 완료해야 합니다.

## Windows Claude Desktop

1. 위 SDK 설치와 목록 테스트를 완료합니다.
2. Claude Desktop → Settings → Developer → Edit Config를 엽니다.
3. `%APPDATA%\Claude\claude_desktop_config.json`의 기존 `mcpServers` 안에 `claude_desktop_config.example.json`의 `comfyui-local` 항목을 병합합니다. 기존 다른 서버 설정을 덮어쓰지 마세요.
4. Claude Desktop을 완전히 종료하고 재시작합니다.
5. 먼저 “comfyui_status 도구로 연결 상태만 확인해줘. 이미지는 생성하지 마.”라고 요청합니다.

설정에 사용할 값:

```json
{
  "mcpServers": {
    "comfyui-local": {
      "command": "E:\\Dev\\comfyui-automation\\.venv-mcp\\Scripts\\python.exe",
      "args": ["-B", "-u", "E:\\Dev\\comfyui-automation\\mcp_server.py"],
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
