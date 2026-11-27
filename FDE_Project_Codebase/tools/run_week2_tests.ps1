$ErrorActionPreference = "Stop"

$codebaseRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $codebaseRoot
$resultsRoot = Join-Path $workspaceRoot "Test results"
$runName = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$runDirectory = Join-Path $resultsRoot $runName

New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null

function Resolve-Python {
    param([string]$ProjectDirectory)

    $candidates = @(
        (Join-Path $ProjectDirectory ".venv\Scripts\python.exe"),
        (Join-Path $codebaseRoot ".venv\Scripts\python.exe"),
        (Join-Path $workspaceRoot ".venv\Scripts\python.exe")
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $pythonCommand) {
        return $pythonCommand.Source
    }

    return $null
}

$suites = @(
    @{ Name = "vastra-modern-api"; Directory = Join-Path $codebaseRoot "vastra-modern-api" },
    @{ Name = "vastra-data-platform"; Directory = Join-Path $codebaseRoot "vastra-data-platform" },
    @{ Name = "vastra-ml-p2"; Directory = Join-Path $codebaseRoot "vastra-ml-p2" }
)

$summary = @(
    "Week 2 automated test results",
    "Run started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')",
    "Results directory: $runDirectory",
    ""
)
$failedSuites = 0

foreach ($suite in $suites) {
    $logPath = Join-Path $runDirectory "$($suite.Name).log"
    $python = Resolve-Python -ProjectDirectory $suite.Directory

    Write-Host "`n=== $($suite.Name) ==="
    if (-not (Test-Path -LiteralPath $suite.Directory -PathType Container)) {
        $message = "Project directory not found: $($suite.Directory)"
        Set-Content -LiteralPath $logPath -Value $message -Encoding UTF8
        Write-Error $message -ErrorAction Continue
        $summary += "$($suite.Name): FAILED (project directory missing)"
        $failedSuites++
        continue
    }

    if ($null -eq $python) {
        $message = "Python was not found. Create/activate a project environment or add Python to PATH."
        Set-Content -LiteralPath $logPath -Value $message -Encoding UTF8
        Write-Error $message -ErrorAction Continue
        $summary += "$($suite.Name): FAILED (Python not found)"
        $failedSuites++
        continue
    }

    Write-Host "Python: $python"
    Write-Host "Running: python -m pytest -q"

    Push-Location -LiteralPath $suite.Directory
    try {
        $LASTEXITCODE = 0
        & $python -m pytest -q *> $logPath
        $exitCode = $LASTEXITCODE
    }
    catch {
        $_ | Out-String | Set-Content -LiteralPath $logPath -Encoding UTF8
        $exitCode = 1
    }
    finally {
        Pop-Location
    }

    Get-Content -LiteralPath $logPath | ForEach-Object { Write-Host $_ }
    if ($exitCode -eq 0) {
        $summary += "$($suite.Name): PASSED (exit code 0; log: $($suite.Name).log)"
    }
    else {
        $summary += "$($suite.Name): FAILED (exit code $exitCode; log: $($suite.Name).log)"
        $failedSuites++
    }
}

$summary += ""
$summary += "Suites failed: $failedSuites of $($suites.Count)"
$summary | Set-Content -LiteralPath (Join-Path $runDirectory "summary.txt") -Encoding UTF8

Write-Host "`nSummary: $(Join-Path $runDirectory 'summary.txt')"
if ($failedSuites -gt 0) {
    exit 1
}

exit 0
