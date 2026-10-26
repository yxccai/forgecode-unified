param([Parameter(ValueFromRemainingArguments=$true)][string[]]$ForgeArgs)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    Write-Error '请先在本目录运行 ./setup.ps1'
}
Push-Location -LiteralPath $taskRoot
$taskEnvironment = @{}
foreach ($taskName in @('PYTHONDONTWRITEBYTECODE','PYTHONIOENCODING','TEMP','TMP')) {
    $taskEnvironment[$taskName] = [System.Environment]::GetEnvironmentVariable($taskName, 'Process')
}
$taskExitCode = 1
try {
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:TEMP = Join-Path $taskRoot 'artifacts/tmp'
    $env:TMP = $env:TEMP
    New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
    & $taskPython -m forgecode @ForgeArgs
    $taskExitCode = $LASTEXITCODE
} finally {
    Pop-Location
    foreach ($taskName in $taskEnvironment.Keys) {
        [System.Environment]::SetEnvironmentVariable($taskName, $taskEnvironment[$taskName], 'Process')
    }
}
exit $taskExitCode
