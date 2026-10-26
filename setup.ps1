$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
$taskEnvironment = @{}
foreach ($taskName in @('PIP_CACHE_DIR','TEMP','TMP')) {
    $taskEnvironment[$taskName] = [System.Environment]::GetEnvironmentVariable($taskName, 'Process')
}
try {
    if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw '需要 Python 3.12 或更新版本' }
    }
    $env:PIP_CACHE_DIR = Join-Path $PSScriptRoot 'artifacts/pip-cache'
    $env:TEMP = Join-Path $PSScriptRoot 'artifacts/tmp'
    $env:TMP = $env:TEMP
    New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
    & ./.venv/Scripts/python.exe -m pip install -e . --config-settings editable_mode=compat --timeout 30 --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw '依赖安装未完成；请检查 pip 输出' }
    Write-Output '安装完成。离线学习：./start.ps1 learn'
} finally {
    Pop-Location
    foreach ($taskName in $taskEnvironment.Keys) {
        [System.Environment]::SetEnvironmentVariable($taskName, $taskEnvironment[$taskName], 'Process')
    }
}
