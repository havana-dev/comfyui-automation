param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ClientArgs
)
$pythonPath = 'E:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\standalone-env\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python not found: $pythonPath"
}
& $pythonPath -B (Join-Path $PSScriptRoot 'comfy_client.py') @ClientArgs
exit $LASTEXITCODE
