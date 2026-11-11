# Parse exact options before any Python process or installer code is started.
$PythonPath = $null
$CheckOnly = $false
$showHelp = $false
$optionError = $null
for ($argumentIndex = 0; $argumentIndex -lt $args.Count; $argumentIndex++) {
    switch ($args[$argumentIndex]) {
        '-PythonPath' {
            $argumentIndex++
            if ($argumentIndex -ge $args.Count -or -not $args[$argumentIndex] -or $args[$argumentIndex].StartsWith('-')) {
                $optionError = '-PythonPath 뒤에 Python 실행 파일 경로가 필요합니다.'
            } else { $PythonPath = $args[$argumentIndex] }
        }
        '-CheckOnly' { $CheckOnly = $true }
        { $_ -in @('-Help', '--help', '-h', '-?') } { $showHelp = $true }
        default { $optionError = "지원하지 않는 옵션: $_" }
    }
}
if ($optionError -or $showHelp) {
    if ($optionError) { Write-Output $optionError }
    Write-Output '사용법: .\setup.ps1 [-PythonPath "C:\경로\python.exe"] [-CheckOnly]'
    Write-Output '-CheckOnly: 파일·설정·패키지를 변경하지 않고 MCP/ComfyUI/Qwen 진단만 실행합니다.'
    Write-Output '-Help / --help: 사용법만 표시합니다.'
    if ($optionError) { exit 2 }
    exit 0
}
$ErrorActionPreference = 'Stop'
# UTF-8 BOM keeps Korean messages readable in Windows PowerShell 5.1.
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$utf8 = New-Object System.Text.UTF8Encoding
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
try {
    $candidates = @()
    if ($PythonPath) { $candidates += $PythonPath }
    $candidates += (Join-Path $PSScriptRoot '.venv-mcp\Scripts\python.exe')
    if (-not $PythonPath) {
        # ComfyUI Desktop ships its own Python 3.13; read its install list only, never modify it.
        $desktopList = if ($env:APPDATA) { Join-Path $env:APPDATA 'Comfy Desktop\installations.json' } else { $null }
        if ($desktopList -and (Test-Path -LiteralPath $desktopList -PathType Leaf)) {
            try {
                $installs = Get-Content -LiteralPath $desktopList -Raw -Encoding UTF8 | ConvertFrom-Json
                foreach ($install in @($installs)) {
                    if ($install.status -eq 'installed' -and $install.installPath) {
                        $candidates += (Join-Path $install.installPath 'standalone-env\python.exe')
                    }
                }
            } catch { } # An unreadable Desktop list must not prevent other candidates.
        }
        $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
        # A launcher may offer/install a missing runtime; never invoke it for diagnostics.
        if ($launcher -and -not $CheckOnly) {
            try {
                $found = & $launcher.Source -3.13 -B -c 'import sys; print(sys.executable)' 2>$null
                if ($LASTEXITCODE -eq 0) { $candidates += $found }
            } catch { } # A launcher without 3.13 must not prevent other candidates.
        }
        $python = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($python -and $python.Source -notlike '*\WindowsApps\*') { $candidates += $python.Source }
        $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe')
    }
    $selected = $null
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        try {
            & $candidate -B -c 'import sys, venv; sys.exit(0 if sys.version_info[:2] == (3,13) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { $selected = $candidate; break }
        } catch { } # Preserve broken candidates; continue looking for a working Python.
    }
    if (-not $selected) {
        throw 'Python 3.13을 찾지 못했습니다. ComfyUI Desktop을 설치·실행했는지 확인하거나, https://www.python.org/downloads/windows/ 에서 Python 3.13을 설치한 뒤 다시 실행하세요. 경로 직접 지정: .\setup.ps1 -PythonPath "C:\경로\python.exe"'
    }
    if ($CheckOnly) {
        & $selected -B (Join-Path $PSScriptRoot 'setup_windows.py') --diagnose
    } else {
        & $selected -B (Join-Path $PSScriptRoot 'setup_windows.py')
    }
    exit $LASTEXITCODE
} catch {
    Write-Host ("설치를 완료하지 못했습니다: " + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
