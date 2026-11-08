# ComfyUI 이미지 자동화 — 1단계

Python 표준 라이브러리로 로컬 ComfyUI API를 호출합니다. pip 설치가 필요 없으며 ComfyUI 설치/모델/원본 소스를 변경하지 않습니다.

## 확인한 환경 (2026-11-08)

- Desktop 실행 기록과 프로세스 기록의 주소: `http://127.0.0.1:8188`
- Desktop 설치: `E:\Comfy-Desktop\ComfyUI-Installs\ComfyUI`
- 별도 분석용 원본: `E:\Dev\ComfyUI`
- 모델: `E:\Comfy-Desktop\ComfyUI-Shared\models` 아래 diffusion_models/z_image_turbo_bf16.safetensors, text_encoders/qwen_3_4b.safetensors, vae/ae.safetensors 확인.
- 대상 저장 폴더: `E:\AI\Outputs`
- 사용자 실행으로 `run.ps1 check`, `run.ps1 generate` 및 `E:\AI\Outputs` 이미지 저장 성공을 확인했습니다. 에이전트 실행 환경의 localhost 접속 제한은 별개입니다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| comfy_client.py | API 확인, 모델/노드 목록 검증, 생성 요청, 완료 대기, PNG 다운로드 |
| result_store.py | 출력 하위 경로 검증, 생성 메타데이터 JSON 저장 |
| config.json | 서버 주소, 출력 경로, 워크플로, 대기 제한 |
| workflows/z_image_turbo_api.json | 설치된 공식 템플릿의 노드/설정을 수동으로 API 형식으로 변환 |
| run.ps1 | 기존 Desktop Python으로 실행 |
| test_client.py | 서버 없이 요청 구성·오류·저장 동작 검증 |

사용자 저장 워크플로는 발견되지 않아 설치된 공식 `comfyui_workflow_templates_json/templates/image_z_image_turbo.json`을 기반으로 했습니다. 서브그래프를 펼쳤으며 8 steps, CFG 1, res_multistep/simple, shift 3 설정을 유지했습니다. 일반 UI JSON을 직접 API로 보내지 않습니다. 현재 프롬프트/시드/크기 변경은 제공된 워크플로의 노드 ID에 맞춰져 있습니다.

## 실행

ComfyUI Desktop을 켠 상태에서 Windows PowerShell을 열고:

```powershell
cd E:\Dev\comfyui-automation
.\run.ps1 check
.\run.ps1 generate --prompt "A cozy library with warm sunlight, detailed illustration" --seed 42
```

PowerShell 스크립트 실행 정책 때문에 실행되지 않으면 정책을 변경하지 말고 Python을 직접 실행합니다:

```powershell
& 'E:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\standalone-env\python.exe' -B 'E:\Dev\comfyui-automation\comfy_client.py' check
& 'E:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\standalone-env\python.exe' -B 'E:\Dev\comfyui-automation\comfy_client.py' generate --prompt 'A cozy library with warm sunlight' --seed 42
```

`check`는 `/system_stats`, `/object_info`를 확인하고 대상 폴더의 쓰기 가능 여부를 검사합니다. 생성은 `/prompt`에 한 번 제출하고 `/history/{prompt_id}`를 조회합니다. 완료 후 `/view`로 PNG를 받아 `E:\AI\Outputs\{prompt_id}_0.png`에 저장합니다. ComfyUI 기본 output에도 원본이 남습니다. 기존 output 경로는 변경하지 않습니다.

기본 1024×1024, 1장입니다. `--width 768 --height 1024` 등으로 변경할 수 있습니다. 시드를 생략하면 무작위 값을 사용합니다. 한국어 입력을 그대로 전달하며 번역/프롬프트 확장은 하지 않습니다.

## 실패와 복구

- 연결 실패: Desktop 실행 여부와 로그의 포트 확인. 권한 오류는 실행 환경의 네트워크 제한일 수도 있습니다.
- 모델/노드 누락: 서버에 등록된 목록과 맞지 않는 이름을 표시하고 제출 전에 중단합니다. 자동 설치/다운로드는 하지 않습니다.
- CUDA 메모리 부족 등: 서버의 execution_error 정보를 표시합니다.
- 출력 폴더 권한 없음: 생성 요청 전에 중단합니다.
- 900초 초과: 서버 작업을 자동 취소하거나 재제출하지 않습니다. 출력된 ID로 `fetch`를 실행하세요.
- 제출 중 연결이 끊겨 ID를 못 받은 경우: Desktop 대기열/기록을 먼저 확인하세요. 자동 재시도하지 않습니다.
- 완료 후 다운로드 실패: 동일 ID의 `fetch`로 다시 저장할 수 있습니다.

```powershell
.\run.ps1 fetch --prompt-id '실제-응답의-UUID'
& 'E:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\standalone-env\python.exe' -B -m unittest -v
```

## 캐릭터별 저장과 메타데이터

```powershell
.\run.ps1 generate --prompt "Platinum blonde detective with teal eyes, London, portrait illustration" --seed 42 --character "Lucy" --output-subdir "trpg/london"
```

이 명령은 `E:\AI\Outputs\trpg\london\Lucy` 아래에 `{prompt_id}_0.png`와 `{prompt_id}.json`을 저장합니다. 여러 장이면 같은 JSON의 files 목록에 모두 기록합니다.

- `--character`: 캐릭터 이름이자 마지막 폴더명입니다. 프롬프트에 자동 삽입하지 않습니다. 한글/공백도 사용할 수 있습니다.
- `--output-subdir`: config.json의 output_dir 아래 상대 경로입니다. 중첩 폴더를 지원하며 절대 경로, 상위 경로 이동, Windows 금지 문자는 거부합니다.
- 두 옵션 모두 생략하면 기존 출력 폴더와 PNG 파일명을 유지하며 JSON만 추가됩니다.
- JSON: prompt_id, character, prompt, seed, models(모델/텍스트 인코더/VAE 파일명), generated_at, generated_at_source, files, workflow를 저장합니다. 실제 제출한 워크플로를 포함하므로 크기·샘플러·steps도 확인할 수 있습니다.
- generated_at은 시간대가 포함된 UTC ISO 8601입니다. 서버 execution_success 시각을 우선 사용하며, 없으면 클라이언트가 완료를 확인한 시각임을 generated_at_source에 표시합니다. 캐시 결과일 수 있으므로 GPU 계산 시작 시각을 의미하지 않습니다.
- 반환 JSON에 metadata_file 경로가 추가됩니다. 이미지 저장 후 JSON 저장이 실패하면 이미지 경로와 요청 ID를 포함한 오류를 표시합니다.
- `fetch`도 동일한 두 경로 옵션을 지원합니다. 이번 변경에서 메타데이터 자동 저장은 정상 완료한 `generate`에만 적용됩니다. `fetch`는 이미지 회수만 수행하며 메타데이터를 새로 만들지 않습니다. 기존 캐릭터 폴더로 회수하려면 동일한 경로 옵션을 전달하세요.

## MCP 서버

STDIO 서버 `mcp_server.py`와 이미지 생성 없이 목록만 조회하는 `check_mcp_tools.py`를 추가했습니다. 기존 CLI는 그대로 사용할 수 있습니다. SDK 설치, Claude Desktop/ChatGPT Desktop 등록 예시 및 검증 상태는 [MCP_SETUP.md](MCP_SETUP.md)를 참고하세요.

MCP 서버는 comfyui_status와 generate_image 도구를 제공합니다. 기존 comfy_client.generate_image 함수를 재사용하며, CLI 인자 처리는 main(), HTTP 호출은 ComfyClient, 출력 경로/메타데이터 처리는 result_store.py가 계속 담당합니다.
