param(
  [string]$ApplicationUrl = 'https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=eae41664-19fb-4412-96f8-43f15d52332b&ccId=19000101_000001&jobId=955507&source=LR&lang=en_US',
  [string]$ExpectedManifestFingerprint = '56f71967ac378836d170ab91b63ced2136a8988e5ce218b7750b25d80fcb51ac',
  [string]$ExpectedContactContractFingerprint = '53b635e9fa5d36f3d8e320f8a406204a093592901226b8ba9886253ceae285ad',
  [string]$ExpectedCountrySurfaceFingerprint = '7b4b13d36c10eb0be71cfe5154de1e80c06c5395a3a00098127d9a9efcf0515f',
  [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

foreach ($Fingerprint in @(
  $ExpectedManifestFingerprint,
  $ExpectedContactContractFingerprint,
  $ExpectedCountrySurfaceFingerprint
)) {
  if ([string]::IsNullOrWhiteSpace($Fingerprint) -or
      $Fingerprint.Length -ne 64 -or
      $Fingerprint -match '[^0-9a-f]') {
    throw 'Reviewed fingerprints must be exactly 64 lowercase hex characters.'
  }
}

$python = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $python) {
  throw 'Python launcher py was not found.'
}

$token = [Guid]::NewGuid().ToString('N')
$tempRoot = [IO.Path]::GetTempPath()
$statePath = Join-Path $tempRoot ("ejs-adp-country-keyboard-state-$token.json")
$reportPath = Join-Path $tempRoot ("ejs-adp-country-keyboard-bootstrap-$token.json")
$canaryPath = Join-Path $tempRoot ("ejs-adp-country-keyboard-canary-$token.json")

try {
  Write-Host 'A verified ADP browser will open for an isolated Country keyboard-selection canary.'
  Write-Host 'Complete Apply / identity / verification manually.'
  Write-Host 'When Personal Information opens, do not edit fields and do not click Next.'
  Write-Host 'The canary may write only the reviewed Turkey Country field and commit it with keyboard input.'
  Write-Host 'Phone, address text, consent, Next, upload and submit remain disabled.'

  & $python.Source -3 -m ejs.services.adp_verified_session_bootstrap --url $ApplicationUrl --storage-state-out $statePath --report-out $reportPath --same-page-country-keyboard-selection-report-out $canaryPath --same-page-country-keyboard-expected-manifest-fingerprint $ExpectedManifestFingerprint --same-page-country-keyboard-expected-contact-contract-fingerprint $ExpectedContactContractFingerprint --same-page-country-keyboard-expected-option-surface-fingerprint $ExpectedCountrySurfaceFingerprint --timeout-seconds $TimeoutSeconds
  if ($LASTEXITCODE -ne 0) {
    throw "ADP Country keyboard canary failed with exit code $LASTEXITCODE."
  }

  if (-not (Test-Path -LiteralPath $canaryPath)) {
    throw 'ADP Country keyboard canary report was not created.'
  }
  $report = Get-Content -Raw -LiteralPath $canaryPath | ConvertFrom-Json
  if ($report.selection_status -notin @('verified', 'already_committed') -or
      $report.phone_write_attempts -ne 0 -or
      $report.address_text_write_attempts -ne 0 -or
      $report.consent_action_attempts -ne 0 -or
      $report.next_click_attempts -ne 0 -or
      $report.file_upload_attempts -ne 0 -or
      $report.submit_attempts -ne 0) {
    throw 'ADP Country keyboard canary exceeded its reviewed authority.'
  }

  Write-Host 'ADP Country keyboard canary verified. No other candidate field was modified.'
  [pscustomobject]@{
    selection_status = $report.selection_status
    canary_version = $report.canary_version
    open_click_attempts = $report.open_click_attempts
    text_write_attempts = $report.text_write_attempts
    keyboard_commit_attempts = $report.keyboard_commit_attempts
    country_selection_successes = $report.country_selection_successes
    readback_mode = $report.readback_evidence.mode
    phone_write_attempts = $report.phone_write_attempts
    address_text_write_attempts = $report.address_text_write_attempts
    next_click_attempts = $report.next_click_attempts
    file_upload_attempts = $report.file_upload_attempts
    submit_attempts = $report.submit_attempts
    raw_values_exposed = $false
  } | ConvertTo-Json -Compress
} finally {
  foreach ($Path in @($statePath, $reportPath, $canaryPath)) {
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
  }
}
