param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8001
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$memPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $memPython)) {
    throw 'Project Python environment is missing: .venv\Scripts\python.exe'
}

foreach ($memKeyName in @('DASHSCOPE_API_KEY', 'MEMORY_API_KEY')) {
    $memKeyValue = [Environment]::GetEnvironmentVariable($memKeyName, 'Process')
    if ([string]::IsNullOrWhiteSpace($memKeyValue)) {
        $memKeyValue = [Environment]::GetEnvironmentVariable($memKeyName, 'User')
    }
    if ([string]::IsNullOrWhiteSpace($memKeyValue)) {
        throw "Missing environment variable: $memKeyName. Set it before starting the service."
    }
    [Environment]::SetEnvironmentVariable($memKeyName, $memKeyValue, 'Process')
}
Remove-Variable memKeyValue

$env:MEMORY_RETRIEVAL_MODE = 'context'
$env:MEMORY_DB_PATH = Join-Path $PSScriptRoot 'work\learning-c.db'
$env:MEMORY_API_BASE = "http://127.0.0.1:$Port"
Write-Host "Mem C | mode=context | $env:MEMORY_API_BASE"
Write-Host "Database: $env:MEMORY_DB_PATH"
& $memPython -m uvicorn app:app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
