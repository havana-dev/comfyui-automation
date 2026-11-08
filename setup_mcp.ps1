$ErrorActionPreference = 'Stop'
$basePython = 'E:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\standalone-env\python.exe'
$mcpPython = Join-Path $PSScriptRoot '.venv-mcp\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $mcpPython)) {
    & $basePython -B -m venv (Join-Path $PSScriptRoot '.venv-mcp')
    if ($LASTEXITCODE -ne 0) { throw 'MCP virtual environment creation failed.' }
}
& $mcpPython -B -m ensurepip --upgrade
if ($LASTEXITCODE -ne 0) { throw 'pip setup failed.' }
& $mcpPython -B -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot 'requirements-mcp.txt')
if ($LASTEXITCODE -ne 0) { throw 'MCP SDK installation failed. Check the package index/network.' }
& $mcpPython -B (Join-Path $PSScriptRoot 'check_mcp_tools.py')
if ($LASTEXITCODE -ne 0) { throw 'STDIO tool discovery test failed.' }
