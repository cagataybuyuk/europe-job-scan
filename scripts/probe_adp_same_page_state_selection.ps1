param(
  [string]$ApplicationUrl = 'https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=eae41664-19fb-4412-96f8-43f15d52332b&ccId=19000101_000001&jobId=955507&source=LR&lang=en_US',
  [string]$ExpectedManifestFingerprint = '56f71967ac378836d170ab91b63ced2136a8988e5ce218b7750b25d80fcb51ac',
  [string]$ExpectedContactContractFingerprint = '53b635e9fa5d36f3d8e320f8a406204a093592901226b8ba9886253ceae285ad',
  [string]$ExpectedCountrySurfaceFingerprint = '7b4b13d36c10eb0be71cfe5154de1e80c06c5395a3a00098127d9a9efcf0515f',
  [string]$ExpectedStateAfterCountryContractFingerprint = 'b746a514d2d0194cb6da0ac177afb2485122a31fcf452e9e0fbb9e8f31e22f51',
  [string]$ExpectedStateSurfaceFingerprint = '33e4bba11523aeb783c47335d9764d6785eccfa9b6da3aaa4e09b59674a2a4aa',
  [string]$ReviewedStateLabel = (([char]0x0130).ToString() + 'stanbul'),
  [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

foreach ($Fingerprint in @(
  $ExpectedManifestFingerprint,
  $ExpectedContactContractFingerprint,
  $ExpectedCountrySurfaceFingerprint,
  $ExpectedStateAfterCountryContractFingerprint,
  $ExpectedStateSurfaceFingerprint
)) {
  if ([string]::IsNullOrWhiteSpace($Fingerprint) -or
      $Fingerprint.Length -ne 64 -or
      $Fingerprint -match '[^0-9a-f]') {
    throw 'Reviewed fingerprints must be exactly 64 lowercase hex characters.'
  }
}
if ([string]::IsNullOrWhiteSpace($ReviewedStateLabel)) {
  throw 'ReviewedStateLabel is required.'
}

$python = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $python) {
  throw 'Python launcher py was not found.'
}

$token = [Guid]::NewGuid().ToString('N')
$tempRoot = [IO.Path]::GetTempPath()
$statePath = Join-Path $tempRoot ("ejs-adp-state-selection-state-$token.json")
$reportPath = Join-Path $tempRoot ("ejs-adp-state-selection-bootstrap-$token.json")
$selectionPath = Join-Path $tempRoot ("ejs-adp-state-selection-report-$token.json")

try {
  Write-Host 'A verified ADP browser will open for the bounded State-selection canary.'
  Write-Host 'Complete Apply / identity / verification manually.'
  Write-Host 'When Personal Information opens, do not edit fields and do not click Next.'
  Write-Host ("The canary may select reviewed Turkey and then State/Territory '{0}'." -f $ReviewedStateLabel)
  Write-Host 'Phone, address text, consent, Next, upload and submit remain disabled.'

  & $python.Source -3 -m ejs.services.adp_verified_session_bootstrap --url $ApplicationUrl --storage-state-out $statePath --report-out $reportPath --same-page-state-selection-report-out $selectionPath --same-page-state-selection-expected-manifest-fingerprint $ExpectedManifestFingerprint --same-page-state-selection-expected-contact-contract-fingerprint $ExpectedContactContractFingerprint --same-page-state-selection-expected-country-surface-fingerprint $ExpectedCountrySurfaceFingerprint --same-page-state-selection-expected-contract-fingerprint $ExpectedStateAfterCountryContractFingerprint --same-page-state-selection-expected-option-surface-fingerprint $ExpectedStateSurfaceFingerprint --same-page-state-selection-reviewed-label $ReviewedStateLabel --timeout-seconds $TimeoutSeconds
  if ($LASTEXITCODE -ne 0) {
    throw "ADP State-selection canary failed with exit code $LASTEXITCODE."
  }

  if (-not (Test-Path -LiteralPath $selectionPath)) {
    throw 'ADP State-selection report was not created.'
  }
  $report = Get-Content -Raw -LiteralPath $selectionPath | ConvertFrom-Json

  if ($report.selection_status -notin @('verified', 'already_committed') -or
      $report.state_selection_attempts -notin @(0,1) -or
      $report.state_selection_successes -notin @(0,1) -or
      $report.state_readback_match -ne $true -or
      $report.phone_write_attempts -ne 0 -or
      $report.address_text_write_attempts -ne 0 -or
      $report.consent_action_attempts -ne 0 -or
      $report.next_click_attempts -ne 0 -or
      $report.file_upload_attempts -ne 0 -or
      $report.submit_attempts -ne 0 -or
      $report.raw_values_exposed -ne $false) {
    throw 'ADP State-selection canary exceeded its reviewed authority or failed readback.'
  }

  Write-Host 'ADP State-selection canary passed within reviewed authority.'
  $report | ConvertTo-Json -Depth 12 -Compress
} finally {
  foreach ($Path in @($statePath, $reportPath, $selectionPath)) {
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
  }
}
