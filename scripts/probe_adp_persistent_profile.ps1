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
$profilePath = Join-Path $tempRoot ("ejs-adp-profile-$token")
$statePath = Join-Path $tempRoot ("ejs-adp-profile-state-$token.json")
$reportPath = Join-Path $tempRoot ("ejs-adp-profile-bootstrap-$token.json")
$sessionStoragePath = Join-Path $tempRoot ("ejs-adp-profile-session-$token.json")
$postLoginUrlPath = Join-Path $tempRoot ("ejs-adp-profile-postlogin-$token.txt")
$probeReportPath = Join-Path $tempRoot ("ejs-adp-profile-reuse-$token.json")

try {
  $python = Resolve-EjsPython3
  $pythonPrefixArgs = @($python.PrefixArgs)
  Write-Host "Using Python launcher: $($python.DisplayName)"

  & $python.Source @pythonPrefixArgs -c "import playwright" 2>$null
  if ($LASTEXITCODE -ne 0) {
    throw ('Playwright is not installed in the selected Python environment. Run: ' + $python.DisplayName + ' -m pip install -e ".[test]"')
  }

  [void](New-Item -ItemType Directory -Path $profilePath -Force)

  Write-Host 'A temporary persistent Chromium profile will open.'
  Write-Host 'Complete the ADP application-entry and verification flow manually.'
  Write-Host 'Enter the email verification code only in the ADP browser, never in this terminal.'
  Write-Host 'When Personal Information opens, do not edit fields and do not click Next.'

  & $python.Source @pythonPrefixArgs -m ejs.services.adp_verified_session_bootstrap --url $ApplicationUrl --storage-state-out $statePath --report-out $reportPath --session-storage-out $sessionStoragePath --postlogin-url-out $postLoginUrlPath --user-data-dir $profilePath --timeout-seconds $TimeoutSeconds
  if ($LASTEXITCODE -ne 0) {
    throw "ADP persistent-profile bootstrap failed with exit code $LASTEXITCODE."
  }

  if (-not (Test-Path -LiteralPath $postLoginUrlPath)) {
    throw 'ADP persistent-profile bootstrap did not capture the canonical postLogin URL.'
  }

  Write-Host 'Verified profile captured. Reopening the same Chromium profile in read-only diagnostic mode.'

  & $python.Source @pythonPrefixArgs -m ejs.services.adp_persistent_profile_probe --url $ApplicationUrl --user-data-dir $profilePath --direct-reuse-url-file $postLoginUrlPath --output $probeReportPath
  $probeExit = $LASTEXITCODE

  $probeReport = if (Test-Path -LiteralPath $probeReportPath) {
    Get-Content -Raw -LiteralPath $probeReportPath | ConvertFrom-Json
  } else {
    $null
  }

  if ($probeExit -eq 0 -and
      $null -ne $probeReport -and
      $probeReport.profile_reuse_proven -eq $true) {
    Write-Host 'ADP persistent-profile reuse was proven locally. No GitHub secret was changed.'
    Write-Host 'This indicates session state exists beyond the serialized Playwright storage bundle.'
  } else {
    $errorCode = if ($null -ne $probeReport) { [string]$probeReport.error_code } else { 'NO_PROFILE_REUSE_REPORT' }
    throw "ADP persistent-profile reuse was not proven ($errorCode). No GitHub secret was changed."
  }
} finally {
  foreach ($Path in @($statePath, $reportPath, $sessionStoragePath, $postLoginUrlPath, $probeReportPath)) {
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
  }

  if (Test-Path -LiteralPath $profilePath) {
    for ($Attempt = 1; $Attempt -le 5; $Attempt++) {
      Remove-Item -LiteralPath $profilePath -Recurse -Force -ErrorAction SilentlyContinue
      if (-not (Test-Path -LiteralPath $profilePath)) { break }
      Start-Sleep -Milliseconds 500
    }
  }

  if (Test-Path -LiteralPath $profilePath) {
    throw 'Sensitive ADP persistent-profile cleanup failed. Close any remaining diagnostic browser and remove the temporary profile directory.'
  }
}
