param(
  [string]$ApplicationUrl = 'https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=eae41664-19fb-4412-96f8-43f15d52332b&ccId=19000101_000001&jobId=955507&source=LR&lang=en_US',
  [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Resolve-EjsPython3 {
  $Candidates = @(
    @{ Name = 'py'; PrefixArgs = @('-3') },
    @{ Name = 'python3'; PrefixArgs = @() },
    @{ Name = 'python'; PrefixArgs = @() }
  )

  foreach ($Candidate in $Candidates) {
    $Command = Get-Command $Candidate.Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $Command) { continue }

    $PrefixArgs = @($Candidate.PrefixArgs)
    $ProbeOutput = & $Command.Source @PrefixArgs -c "import sys; print(sys.executable if sys.version_info.major == 3 else '')" 2>$null
    if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace(($ProbeOutput | Out-String))) {
      return [pscustomobject]@{
        Source = $Command.Source
        PrefixArgs = $PrefixArgs
        DisplayName = if ($PrefixArgs.Count -gt 0) { "$($Candidate.Name) $($PrefixArgs -join ' ')" } else { $Candidate.Name }
      }
    }
  }

  throw @'
No usable Python 3 interpreter was found.
Install Python 3.12, then reopen PowerShell:
  winget install -e --id Python.Python.3.12
The Windows Store python alias is not sufficient.
'@
}

$token = [Guid]::NewGuid().ToString('N')
$tempRoot = [IO.Path]::GetTempPath()
$statePath = Join-Path $tempRoot ("ejs-adp-live-handoff-state-$token.json")
$reportPath = Join-Path $tempRoot ("ejs-adp-live-handoff-bootstrap-$token.json")
$sessionStoragePath = Join-Path $tempRoot ("ejs-adp-live-handoff-session-$token.json")
$postLoginUrlPath = Join-Path $tempRoot ("ejs-adp-live-handoff-postlogin-$token.txt")
$handoffReportPath = Join-Path $tempRoot ("ejs-adp-live-handoff-probe-$token.json")

try {
  $python = Resolve-EjsPython3
  $pythonPrefixArgs = @($python.PrefixArgs)
  Write-Host "Using Python launcher: $($python.DisplayName)"

  & $python.Source @pythonPrefixArgs -c "import playwright" 2>$null
  if ($LASTEXITCODE -ne 0) {
    throw ('Playwright is not installed in the selected Python environment. Run: ' + $python.DisplayName + ' -m pip install -e ".[test]"')
  }

  Write-Host 'A verified ADP browser will open.'
  Write-Host 'Complete the application-entry and verification flow manually.'
  Write-Host 'Enter the email verification code only in the ADP browser, never in this terminal.'
  Write-Host 'When Personal Information opens, do not edit fields and do not click Next.'
  Write-Host 'After capture, a second fresh browser will open while the verified source browser remains alive.'

  & $python.Source @pythonPrefixArgs -m ejs.services.adp_verified_session_bootstrap --url $ApplicationUrl --storage-state-out $statePath --report-out $reportPath --session-storage-out $sessionStoragePath --postlogin-url-out $postLoginUrlPath --live-handoff-report-out $handoffReportPath --timeout-seconds $TimeoutSeconds
  if ($LASTEXITCODE -ne 0) {
    throw "ADP live-handoff bootstrap failed with exit code $LASTEXITCODE."
  }

  if (-not (Test-Path -LiteralPath $handoffReportPath)) {
    throw 'ADP live-handoff diagnostic did not create a probe report.'
  }

  $handoff = Get-Content -Raw -LiteralPath $handoffReportPath | ConvertFrom-Json
  if ($handoff.live_handoff_reuse_proven -eq $true) {
    Write-Host 'ADP live handoff reuse was proven while the verified source browser remained open.'
    Write-Host 'No GitHub secret was changed.'
    Write-Host 'This isolates browser/context close as the likely session-invalidating boundary.'
  } else {
    throw 'ADP live handoff reuse was not proven while the verified source browser remained open. No GitHub secret was changed.'
  }
} finally {
  foreach ($Path in @($statePath, $reportPath, $sessionStoragePath, $postLoginUrlPath, $handoffReportPath)) {
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
  }
}
