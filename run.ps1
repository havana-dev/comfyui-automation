param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ClientArgs
)
# comfy_client.py uses only the Python standard library. Prefer the environment made by
# install.cmd / setup.ps1, then ComfyUI Desktop's bundled Python (its install list is only read).
$candidates = @(Join-Path $PSScriptRoot '.venv-mcp\Scripts\python.exe')
$desktopList = if ($env:APPDATA) { Join-Path $env:APPDATA 'Comfy Desktop\installations.json' } else { $null }
if ($desktopList -and (Test-Path -LiteralPath $desktopList -PathType Leaf)) {
    try {
        # Assign first: Windows PowerShell 5.1 emits a JSON array as one object, so @(pipeline) would not unroll it.
        $installs = Get-Content -LiteralPath $desktopList -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($install in @($installs)) {
            if ($install.status -eq 'installed' -and $install.installPath) {
                $candidates += (Join-Path $install.installPath 'standalone-env\python.exe')
            }
        }
    } catch { } # An unreadable Desktop list only removes that candidate.
}
$pythonPath = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $pythonPath) {
    Write-Host 'Python을 찾지 못했습니다. 먼저 install.cmd(또는 setup.ps1)로 설치를 완료하세요.' -ForegroundColor Red
    exit 1
}
& $pythonPath -B (Join-Path $PSScriptRoot 'comfy_client.py') @ClientArgs
exit $LASTEXITCODE
