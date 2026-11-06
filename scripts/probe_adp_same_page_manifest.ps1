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

  throw 'No usable Python 3 interpreter was found.'
}

$token = [Guid]::NewGuid().ToString('N')
$tempRoot = [IO.Path]::GetTempPath()
$statePath = Join-Path $tempRoot ("ejs-adp-manifest-state-$token.json")
$reportPath = Join-Path $tempRoot ("ejs-adp-manifest-bootstrap-$token.json")
$sessionStoragePath = Join-Path $tempRoot ("ejs-adp-manifest-session-$token.json")
$postLoginUrlPath = Join-Path $tempRoot ("ejs-adp-manifest-postlogin-$token.txt")
$manifestPath = Join-Path $tempRoot ("ejs-adp-same-page-manifest-$token.json")

try {
  $python = Resolve-EjsPython3
  $pythonPrefixArgs = @($python.PrefixArgs)
  Write-Host "Using Python launcher: $($python.DisplayName)"

  & $python.Source @pythonPrefixArgs -c "import playwright" 2>$null
  if ($LASTEXITCODE -ne 0) {
    throw ('Playwright is not installed in the selected Python environment. Run: ' + $python.DisplayName + ' -m pip install -e ".[test]"')
  }

  Write-Host 'A verified ADP browser will open for a read-only same-page manifest capture.'
  Write-Host 'Complete Apply / identity / verification manually.'
  Write-Host 'Enter the email verification code only in the ADP browser, never in this terminal.'
  Write-Host 'When Personal Information opens, do not edit fields and do not click Next.'

  & $python.Source @pythonPrefixArgs -m ejs.services.adp_verified_session_bootstrap --url $ApplicationUrl --storage-state-out $statePath --report-out $reportPath --session-storage-out $sessionStoragePath --postlogin-url-out $postLoginUrlPath --same-page-manifest-out $manifestPath --timeout-seconds $TimeoutSeconds
  if ($LASTEXITCODE -ne 0) {
    throw "ADP same-page manifest bootstrap failed with exit code $LASTEXITCODE."
  }

  if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw 'ADP same-page manifest was not created.'
  }

  $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
  if ($manifest.same_page_verified_surface -ne $true) {
    throw 'ADP same-page manifest did not prove the verified surface.'
  }
  if ($manifest.form_value_write_attempts -ne 0 -or
      $manifest.file_upload_attempts -ne 0 -or
      $manifest.submit_attempts -ne 0) {
    throw 'ADP same-page manifest violated the read-only boundary.'
  }

  Write-Host 'ADP same-page manifest captured read-only. No GitHub secret was changed.'
  $manifest | ConvertTo-Json -Depth 12 -Compress
} finally {
  foreach ($Path in @($statePath, $reportPath, $sessionStoragePath, $postLoginUrlPath, $manifestPath)) {
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
  }
}
