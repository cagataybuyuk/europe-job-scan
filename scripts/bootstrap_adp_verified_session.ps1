param(
  [string]$Repo = 'cagataybuyuk/europe-job-scan',
  [string]$Environment = 'gd004-safe-fill-upload-canary',
  [string]$ApplicationUrl = 'https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=eae41664-19fb-4412-96f8-43f15d52332b&ccId=19000101_000001&jobId=955507&source=LR&lang=en_US',
  [int]$TimeoutSeconds = 900,
  [string]$ExpectedNavigationSurfaceFingerprint = '567e7890f5a01f151dbeeb23851ad7cf5fb8100a32c3e0386227d17cd507d313',
  [int]$EntryOrdinal = 0
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$LibPath = Join-Path $PSScriptRoot 'lib/invoke_native_utf8_stdin.ps1'
. $LibPath

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
$statePath = Join-Path $tempRoot ("ejs-adp-storage-$token.json")
$reportPath = Join-Path $tempRoot ("ejs-adp-bootstrap-report-$token.json")
$reuseReportPath = Join-Path $tempRoot ("ejs-adp-reuse-report-$token.json")

try {
  $sessionStoragePath = "$statePath.session-storage.json"
  $sessionReuseReportPath = "$reuseReportPath.session-storage.json"
  $python = Resolve-EjsPython3
  $pythonPrefixArgs = @($python.PrefixArgs)
  Write-Host "Using Python launcher: $($python.DisplayName)"

  & $python.Source @pythonPrefixArgs -c "import playwright" 2>$null
  if ($LASTEXITCODE -ne 0) {
    throw ('Playwright is not installed in the selected Python environment. Run: ' + $python.DisplayName + ' -m pip install -e ".[test]"')
  }

  Write-Host 'A browser window will open. Complete the ADP flow manually.'
  Write-Host 'Enter the email verification code only in the ADP browser, never in this terminal.'
  & $python.Source @pythonPrefixArgs -m ejs.services.adp_verified_session_bootstrap --url $ApplicationUrl --storage-state-out $statePath --report-out $reportPath --session-storage-out $sessionStoragePath --timeout-seconds $TimeoutSeconds
  if ($LASTEXITCODE -ne 0) {
    throw "ADP verified-session bootstrap failed with exit code $LASTEXITCODE."
  }

  if (-not (Test-Path -LiteralPath $statePath)) {
    throw 'ADP verified-session bootstrap did not create storage state.'
  }

  # A disappearing verification screen is not proof of a reusable session.
  # This uses the existing reviewed read-only inspector: at most one Apply click.
  Write-Host 'Testing the candidate session in a fresh local browser before updating the secret.'
  & $python.Source @pythonPrefixArgs -m ejs.services.adp_verified_session_inspector --url $ApplicationUrl --expected-navigation-surface-fingerprint $ExpectedNavigationSurfaceFingerprint --entry-ordinal $EntryOrdinal --storage-state-json $statePath --output $reuseReportPath --playwright-managed
  $plainReplayExit = $LASTEXITCODE
  $reuseReport = if (Test-Path -LiteralPath $reuseReportPath) {
    Get-Content -Raw -LiteralPath $reuseReportPath | ConvertFrom-Json
  } else {
    $null
  }

  if ($plainReplayExit -ne 0) {
    $plainErrorCode = if ($null -ne $reuseReport) { [string]$reuseReport.error_code } else { '' }
    $bootstrapReport = if (Test-Path -LiteralPath $reportPath) {
      Get-Content -Raw -LiteralPath $reportPath | ConvertFrom-Json
    } else {
      $null
    }
    $sessionEntryCount = 0
    if ($null -ne $bootstrapReport -and
        $bootstrapReport.PSObject.Properties.Name -contains 'session_storage_entry_count') {
      $sessionEntryCount = [int]$bootstrapReport.session_storage_entry_count
    }

    $sessionDiagnosticReplayCodes = @(
      'ADP_VERIFIED_SESSION_NOT_RECOGNIZED_IDENTITY_SURFACE',
      'ADP_VERIFIED_SESSION_COOKIE_STATE_NOT_REUSED'
    )
    if ($sessionDiagnosticReplayCodes -contains $plainErrorCode -and
        $sessionEntryCount -gt 0 -and
        (Test-Path -LiteralPath $sessionStoragePath)) {
      $diagnosticReason = if ($plainErrorCode -eq 'ADP_VERIFIED_SESSION_COOKIE_STATE_NOT_REUSED') {
        'cookie-state reuse was blocked'
      } else {
        'the verified identity was not recognized'
      }
      Write-Host "Plain fresh-browser replay showed that $diagnosticReason. Testing transient sessionStorage reuse without exposing its contents. Captured entries: $sessionEntryCount"
      & $python.Source @pythonPrefixArgs -m ejs.services.adp_verified_session_inspector --url $ApplicationUrl --expected-navigation-surface-fingerprint $ExpectedNavigationSurfaceFingerprint --entry-ordinal $EntryOrdinal --storage-state-json $statePath --session-storage-json $sessionStoragePath --output $sessionReuseReportPath --playwright-managed
      $sessionReplayExit = $LASTEXITCODE
      $sessionReuseReport = if (Test-Path -LiteralPath $sessionReuseReportPath) {
        Get-Content -Raw -LiteralPath $sessionReuseReportPath | ConvertFrom-Json
      } else {
        $null
      }
      if ($sessionReplayExit -eq 0 -and
          $null -ne $sessionReuseReport -and
          $sessionReuseReport.inspector_status -eq 'inspected' -and
          $sessionReuseReport.session_reused -eq $true) {
        throw 'ADP local replay succeeds only when transient sessionStorage is restored. Existing GitHub secret was not changed. Implement protected sessionStorage transport before GitHub-hosted reuse.'
      }
      $sessionErrorCode = if ($null -ne $sessionReuseReport) { [string]$sessionReuseReport.error_code } else { 'NO_SESSION_REPLAY_REPORT' }
      throw "ADP session was not reusable even with transient sessionStorage replay ($sessionErrorCode). Existing GitHub secret was not changed."
    }

    throw 'ADP session was not reusable in a fresh local browser. Existing GitHub secret was not changed. See the inspector error_code above.'
  }

  if ($null -eq $reuseReport -or $reuseReport.inspector_status -ne 'inspected' -or $reuseReport.session_reused -ne $true) {
    throw 'ADP local session reuse was not proven. Existing GitHub secret was not changed.'
  }

  $stateJson = [IO.File]::ReadAllText($statePath)
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  $byteCount = $utf8NoBom.GetByteCount($stateJson)
  if ($byteCount -le 2) {
    throw 'ADP verified-session storage state is empty.'
  }
  if ($byteCount -gt 47000) {
    throw "ADP verified-session state is $byteCount bytes and exceeds the reviewed GitHub secret budget."
  }
  try {
    $parsed = $stateJson | ConvertFrom-Json
  } catch {
    throw 'ADP verified-session storage state is not valid JSON.'
  }
  if ($null -eq $parsed.cookies -or $null -eq $parsed.origins) {
    throw 'ADP verified-session storage state is missing Playwright cookies/origins.'
  }

  Invoke-GhSecretSetUtf8 -SecretName 'EJS_ADP_VERIFIED_STORAGE_STATE_JSON' -Json $stateJson -Repo $Repo -Environment $Environment

  $report = if (Test-Path -LiteralPath $reportPath) {
    Get-Content -Raw -LiteralPath $reportPath | ConvertFrom-Json
  } else {
    $null
  }
  $controlCount = if ($null -ne $report) { [int]$report.visible_control_count } else { 0 }
  Write-Host "ADP verified-session secret updated successfully with byte-safe UTF-8 stdin. Post-verification visible controls: $controlCount"
} finally {
  $stateJson = $null
  $parsed = $null
  Remove-EjsTempFile -Path $statePath -Sensitive
  Remove-EjsTempFile -Path $sessionStoragePath -Sensitive
  Remove-EjsTempFile -Path $reportPath
  Remove-EjsTempFile -Path $reuseReportPath
  Remove-EjsTempFile -Path $sessionReuseReportPath
}
