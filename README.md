# ComfyUI 이미지 자동화 — Qwen Image 2.1

Windows ZIP 배포의 간편 설치는 **[MCP_SETUP.md의 간편 설치 v1](MCP_SETUP.md#간편-설치-v1-windows-zip)**을 참고하세요.
압축을 푼 폴더에서 `powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1`을 실행하면
Claude Desktop / Claude Code / 둘 다를 선택할 수 있습니다. Python 3.13과 실행 중인 로컬 ComfyUI가 필요합니다.

Python 표준 라이브러리로 로컬 ComfyUI API를 호출합니다. pip 설치가 필요 없으며 ComfyUI 설치/모델/원본 소스를 변경하지 않습니다.

일반 생성과 신규 캐릭터 초안은 Qwen Image 2.1 T2I, 편집과 캐릭터 장면은 Qwen Image 2.1 참조 이미지 그래프를 사용합니다.
필요 모델은 `qwen_image_2.1_int8_convrot.safetensors`, `qwen3vl_8b_int8_convrot.safetensors`,
`qwen_image_2.1_vae_bf16.safetensors`이며 기존 ComfyUI 노드 설치가 필요합니다. 자동 설치나 다운로드는 하지 않습니다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| comfy_client.py | API 확인, 모델/노드 목록 검증, 생성 요청, 완료 대기, PNG 다운로드 |
| result_store.py | 출력 하위 경로 검증, 생성 메타데이터 JSON 저장 |
| config.json | 서버 주소, 출력 경로, 워크플로, 대기 제한 |
| workflows/qwen21_text_to_image_api.json | 설치된 공식 템플릿의 노드/설정을 수동으로 API 형식으로 변환 |
| run.ps1 | 기존 Desktop Python으로 실행 |
| test_client.py | 서버 없이 요청 구성·오류·저장 동작 검증 |

일반 생성은 초안 도구의 검증된 Qwen T2I 그래프 준비 함수를 재사용합니다. 샘플 프롬프트,
선택 스위치, 미리보기와 선택적 프롬프트 재작성 모델은 제출 그래프에서 제외합니다.
25 steps, CFG 1, euler_ancestral/simple을 유지하며 일반 생성은 요청한 크기를 latent에 직접 지정합니다.
초안과 장면의 기존 해상도 처리는 유지합니다.

기존 `config.json`은 그대로 사용할 수 있습니다. `workflow`, `edit_engine`, `edit_workflow`는
이전 설정과의 호환을 위해 허용하지만 엔진 선택에는 사용하지 않습니다. 일반 생성과 CLI check는
프로젝트의 Qwen T2I 그래프, 편집은 Qwen 장면 그래프를 사용합니다. 새 설정 예시는 Qwen 경로를 표시합니다.

## 실행

ComfyUI Desktop을 켠 상태에서 Windows PowerShell을 열고:

```powershell
cd <프로젝트 폴더>
.\run.ps1 check
.\run.ps1 generate --prompt "A cozy library with warm sunlight, detailed illustration" --seed 42
```

PowerShell 스크립트 실행 정책 때문에 실행되지 않으면 정책을 변경하지 말고 Python을 직접 실행합니다:

```powershell
& '<python.exe>' -B '<프로젝트 폴더>\comfy_client.py' check
& '<python.exe>' -B '<프로젝트 폴더>\comfy_client.py' generate --prompt 'A cozy library with warm sunlight' --seed 42
```

`check`는 `/system_stats`, `/object_info`를 확인하고 대상 폴더의 쓰기 가능 여부를 검사합니다. 생성은 `/prompt`에 한 번 제출하고 `/history/{prompt_id}`를 조회합니다. 완료 후 `/view`로 PNG를 받아 `<output_dir>\{prompt_id}_0.png`에 저장합니다. ComfyUI 기본 output에도 원본이 남습니다. 기존 output 경로는 변경하지 않습니다.

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
& '<python.exe>' -B -m unittest -v
```

## 캐릭터별 저장과 메타데이터

```powershell
.\run.ps1 generate --prompt "Platinum blonde detective with teal eyes, London, portrait illustration" --seed 42 --character "Lucy" --output-subdir "trpg/london"
```

이 명령은 `<output_dir>\trpg\london\Lucy` 아래에 `{prompt_id}_0.png`와 `{prompt_id}.json`을 저장합니다. 여러 장이면 같은 JSON의 files 목록에 모두 기록합니다.

- `--character`: 등록된 한글/영문 이름·별칭·ID·folder_name은 해당 프로필의 저장 폴더로 연결합니다. 미등록 이름은 기존처럼 마지막 폴더명으로 사용합니다. 프롬프트에 자동 삽입하지 않습니다. 한글/공백도 사용할 수 있습니다.
- `--output-subdir`: config.json의 output_dir 아래 상대 경로입니다. 중첩 폴더를 지원하며 절대 경로, 상위 경로 이동, Windows 금지 문자는 거부합니다.
- 두 옵션 모두 생략하면 기존 출력 폴더와 PNG 파일명을 유지하며 JSON만 추가됩니다.
- JSON: prompt_id, character, prompt, seed, models(모델/텍스트 인코더/VAE 파일명), generated_at, generated_at_source, files, workflow를 저장합니다. 실제 제출한 워크플로를 포함하므로 크기·샘플러·steps도 확인할 수 있습니다.
- generated_at은 시간대가 포함된 UTC ISO 8601입니다. 서버 execution_success 시각을 우선 사용하며, 없으면 클라이언트가 완료를 확인한 시각임을 generated_at_source에 표시합니다. 캐시 결과일 수 있으므로 GPU 계산 시작 시각을 의미하지 않습니다.
- 반환 JSON에 metadata_file 경로가 추가됩니다. 이미지 저장 후 JSON 저장이 실패하면 이미지 경로와 요청 ID를 포함한 오류를 표시합니다.
- `fetch`도 동일한 두 경로 옵션을 지원합니다. 이번 변경에서 메타데이터 자동 저장은 정상 완료한 `generate`에만 적용됩니다. `fetch`는 이미지 회수만 수행하며 메타데이터를 새로 만들지 않습니다. 기존 캐릭터 폴더로 회수하려면 동일한 경로 옵션을 전달하세요.

## MCP 서버

STDIO 서버 `mcp_server.py`와 이미지 생성 없이 목록만 조회하는 `check_mcp_tools.py`를 추가했습니다. 기존 CLI는 그대로 사용할 수 있습니다. SDK 설치, Claude Desktop/ChatGPT Desktop 등록 예시 및 검증 상태는 [MCP_SETUP.md](MCP_SETUP.md)를 참고하세요.

MCP 서버는 comfyui_status와 generate_image 도구를 제공합니다. 기존 comfy_client.generate_image 함수를 재사용하며, CLI 인자 처리는 main(), HTTP 호출은 ComfyClient, 출력 경로/메타데이터 처리는 result_store.py가 계속 담당합니다.


### MCP 백그라운드 자동 시작

두 MCP 도구는 로컬 8188 리스너를 재사용하며 연결 거부일 때만 Desktop의 `ComfyUI/.venv/Scripts/python.exe`로 백엔드를 창 없이 시작합니다. Desktop 로그에서 확인한 공유 모델 YAML과 input/output 및 실행 옵션을 유지합니다. CLI는 변경하지 않습니다.

`/system_stats` 준비 확인 후 기존 워크플로/모델 검증과 생성 함수를 호출합니다. 준비 대기는 180초(진행 중 HTTP 요청은 최대 30초 추가)이며 실패 시 이미지를 제출하지 않습니다. 로그는 프로젝트 `backend.log`에 누적됩니다. 상태 도구도 서버 시작과 로그 쓰기를 할 수 있습니다. 종료나 시간 초과로 백엔드를 강제 종료하지 않습니다.

자동 실행 경로는 Git에서 제외된 config.json의 backend에 base_dir, models_config, input_dir, output_dir로 설정합니다. base_dir 아래 ComfyUI/.venv/Scripts/python.exe와 ComfyUI/main.py를 사용합니다. 재설치나 다운로드는 하지 않습니다. 동일 MCP 프로세스의 시작은 직렬화되지만 독립 프로세스끼리는 시작 경쟁이 가능하므로 MCP 서버는 하나만 등록하세요.

## TRPG 캐릭터 기억

MCP의 `get_character(name)`은 한글/영문 이름 또는 별칭으로 조회합니다(영문 대소문자 무시).
없으면 `save_character(name, ...)`로 프로필과 이미지 폴더를 만듭니다.
프로필은 `characters/<고유 ID>.json`, 이미지는 설정의 `output_dir/<고유 ID>`에 저장됩니다.
관리 필드: `name_ko`, `name_en`, `aliases`, `appearance`, `age`(문자열), `era`,
`default_style`, `reference_images`(경로 목록). 미제공 정보는 null/빈 목록이며,
수정 시 생략한 필드는 보존됩니다. 목록을 비우려면 `[]`를 전달합니다.

채팅 AI는 “예시 캐릭터가 중국 전통의상을 입은 모습 그려줘” 요청에서 이름을 조회하고,
저장된 설정과 이번 의상 요청을 영어 프롬프트로 정리한 다음 기존 `generate_image`를 호출합니다.
이때 `character`에 조회 결과의 `folder_name`을 전달합니다. 일회성 의상 요청을 프로필에
덮어쓰지 않습니다. 기준 이미지 경로는 저장만 하며 실제 이미지 참조 생성은 지원하지 않습니다.
기존 CLI와 직접 `generate_image` 호출의 동작은 동일합니다. MCP를 재시작하면 새 도구가 표시됩니다.

검증(이미지 생성 없음): `.\.venv-mcp\Scripts\python.exe -m unittest discover -v`
및 `.\.venv-mcp\Scripts\python.exe check_mcp_tools.py`.

`--output-subdir`를 명시하면 기존처럼 해당 하위 경로 안에 캐릭터 폴더를 둡니다. 이름을 생략하면 기본 저장 경로를 유지합니다. save_character는 전달된 한글/영문 이름과 별칭 중 기존 프로필과 일치하는 항목으로 갱신 대상을 찾으며, 서로 다른 프로필에 걸친 이름은 거부합니다.

개인 프로필인 `characters/`와 생성 이미지는 Git에서 제외됩니다. 테스트는 임시 가상 캐릭터만 사용합니다.

### Reference editing (MCP)

`edit_image(reference_image=None, instruction="...", character="Example Character", seed=None)`
uses the registered character's `reference_images[0]` when no explicit PNG path is supplied.
It preserves character profiles and source files; results go into the character folder's
`edits/<unique-run>/` directory with PNG and JSON (instruction, seed, reference path, full API graph).
Submission and polling use the existing client and shared generation lock. Timeouts never retry.

Copy `config.example.json` to the ignored `config.json` and set your local output and backend paths.
The example paths under `backend` are placeholders for your existing installation.

Editing defaults to Qwen Image 2.1, reusing the scene graph preparation and client.
The explicit input PNG is uploaded as `images.image_1`; the edit instruction goes directly
to `TextEncodeQwenImage21.prompt` (the actual positive prompt field). No official reference
is substituted when an edit source is supplied. JSON records `reference_image`,
`reference_sha256` of the uploaded bytes, prompt, seed, models and submitted graph.

Optional `style_reference_image` supplies a user-selected PNG as Qwen `images.image_2`.
The edit target remains `images.image_1`. The composed prompt takes only linework,
coloring and detailed semi-realistic anime rendering from image_2, preserving image_1's
identity and appearance except for explicitly requested edits. It forbids copying the
style reference's face, hair, outfit or accessories. No character's reference is selected
automatically. Omitting this parameter preserves the existing single-image behavior.
JSON also records the style reference path/SHA-256 and original `edit_instruction`;
`prompt` contains the full submitted instruction.
Style/identity separation is a prompt instruction, not a guaranteed identity lock.

Unavailable Qwen nodes/models are checked via `/object_info` before uploading or submitting.
Nothing is downloaded or installed. Background backend startup remains unchanged.

### 클라이언트별 이미지 생성 지침

Codex는 이 프로젝트의 `AGENTS.md`를 작업 지침으로 사용합니다. Claude Desktop에서는
`AGENTS.md`의 규칙이 자동 적용되지 않으므로, 같은 동작을 원하면 해당 규칙을 Claude Desktop의
프로젝트 지침이나 대화 지침에 별도로 제공해야 합니다.

### 신규 캐릭터 첫 이미지 (승인 전 초안)

참조 없는 신규 캐릭터의 기본 경로는
`create_character_draft(character_settings, prompt, seed=None)`이며 Qwen Image 2.1 T2I를 사용합니다.
설정은 사용자 제공 사실을 담은 객체이고, 영어 `prompt`는 Codex가 한국어 요청과 설정으로
구성합니다. 서버에 번역기는 없습니다. 예: `character_settings={"appearance": "검은 머리"}`,
`prompt="An illustration of a character with black hair."`.

원본 `workflows/qwen21_text_to_image_api.json`은 변경하지 않습니다. 실행용 그래프의
`459:452` (`TextEncodeQwenImage21`)에 영어 prompt를 직접 전달하고 예시 negative_prompt는
비웁니다. `459:458`의 seed, `459:456`의 batch_size=1, `461`의 PNG 출력을 사용합니다.
해상도는 원본 `13` (`ResolutionSelector`)의 정사각형 1MP 설정을 유지합니다.
샘플 프롬프트·스위치·미리보기·TextGenerate 및 그 전용 모델 분기는 실행에서 제외합니다.
기존 ComfyClient의 검사·단일 제출·대기·PNG 저장과 JSON 저장 함수를 재사용합니다.

출력은 `output_dir/drafts/draft-<UUID>/` 아래 새 폴더에 저장됩니다. JSON에는 설정,
영어 프롬프트, seed, 실제 그래프·모델, 임시 ID와 `pending_approval` 상태를 기록합니다.
공식 캐릭터 데이터나 `reference_v1.png`를 만들지 않으며 승인·등록 요청 전에는
`save_character`를 호출하지 않습니다. 실패·시간 초과 시 자동 재시도하지 않습니다.
일반 `generate_image`도 Qwen T2I를 사용하며 기존 scene/edit/get/save 기능은 유지됩니다.
기존 JSON·모델·캐릭터 데이터는 수정하지 않습니다. MCP 재시작 후 새 도구가 노출됩니다.

### 등록 캐릭터 새 장면 사용법

`create_character_scene(character, scene_description, seed=None, pose_image=None)`은
`workflows/qwen21_character_scene_api.json`을 기본으로 사용합니다. 한국어 한 문장 요청은
Codex가 `get_character`로 이름·별칭을 조회하고 공식 이미지를 확인한 뒤, 저장된 설정과
요청을 영어 `scene_description`으로 구성하여 호출합니다. 서버 자체 번역기는 없습니다.
`generate_image`와 `edit_image`는 Qwen Image 2.1을 사용합니다.

실제 API 연결은 `470: LoadImage.image`, `459:474: TextEncodeQwenImage21.prompt`와
`images.image_1`, `459:458: KSampler.seed`입니다. 등록된 캐릭터 폴더의
`reference_v1.png`를 검증하고 그 바이트를 고유 이름으로 업로드합니다.
사용자가 지정한 `pose_image`만 `images.image_2`로 연결합니다. 이 추가 입력은 설치된
`nodes_qwen.py`의 `images` Autogrow 스키마(`image_1`~`image_16`)에 따릅니다.
실행 그래프에서는 템플릿의 추가 이미지와 샘플 프롬프트·프롬프트 재작성·미리보기 분기를
제외하고 영어 프롬프트를 인코더에 직접 전달합니다. 원본 워크플로 파일은 보존합니다.

결과는 캐릭터 폴더의 `scenes/<고유 실행 ID>/`에 PNG/JSON으로 저장됩니다.
JSON에는 실제 제출 그래프의 모델 파일명, 공식 참조 절대 경로와 SHA-256,
프롬프트, seed, 작업 유형과 엔진을 기록합니다. SHA-256은 사용한 바이트의 기록입니다.
기존 업로드·저장 코드와 `result_store.py`를 재사용합니다.

실제 생성 전에는 MCP 프로세스를 재시작하여 변경을 반영하고, 공식 참조 등록과
Qwen 노드 및 다음 모델의 설치 상태를 확인하세요. 실행 시에도 업로드·제출 전에
노드와 모델을 점검하며, 실패하거나 시간이 초과되어도 자동 재시도하지 않습니다.

- `qwen_image_2.1_int8_convrot.safetensors`
- `qwen3vl_8b_int8_convrot.safetensors`
- `qwen_image_2.1_vae_bf16.safetensors`

워크플로의 `resolution=0`, latent 스위치와 샘플링 설정을 유지하므로 기본 출력 크기는
첫 공식 참조 이미지 크기를 따릅니다. 닮음과 그림체 유지 정도는 실제 생성으로 확인해야 합니다.
모의 테스트: `.\.venv-mcp\Scripts\python.exe -B -m unittest discover -v`.
