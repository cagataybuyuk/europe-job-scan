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
$statePath = Join-Path $tempRoot ("ejs-adp-state-after-country-state-$token.json")
$reportPath = Join-Path $tempRoot ("ejs-adp-state-after-country-bootstrap-$token.json")
$contractPath = Join-Path $tempRoot ("ejs-adp-state-after-country-contract-$token.json")

try {
  Write-Host 'A verified ADP browser will open for the State-after-Country structural canary.'
  Write-Host 'Complete Apply / identity / verification manually.'
  Write-Host 'When Personal Information opens, do not edit fields and do not click Next.'
  Write-Host 'The canary may select reviewed Turkey, then it will inspect State without reading or writing the State value.'
  Write-Host 'Phone, address text, consent, Next, upload and submit remain disabled.'

  & $python.Source -3 -m ejs.services.adp_verified_session_bootstrap --url $ApplicationUrl --storage-state-out $statePath --report-out $reportPath --same-page-state-after-country-contract-out $contractPath --same-page-state-after-country-expected-manifest-fingerprint $ExpectedManifestFingerprint --same-page-state-after-country-expected-contact-contract-fingerprint $ExpectedContactContractFingerprint --same-page-state-after-country-expected-option-surface-fingerprint $ExpectedCountrySurfaceFingerprint --timeout-seconds $TimeoutSeconds
  if ($LASTEXITCODE -ne 0) {
    throw "ADP State-after-Country contract failed with exit code $LASTEXITCODE."
  }

  if (-not (Test-Path -LiteralPath $contractPath)) {
    throw 'ADP State-after-Country contract report was not created.'
  }
  $report = Get-Content -Raw -LiteralPath $contractPath | ConvertFrom-Json

  if ($report.country_selection_attempts -notin @(0,1) -or
      $report.country_selection_successes -notin @(0,1) -or
      $report.state_selection_attempts -ne 0 -or
      $report.phone_write_attempts -ne 0 -or
      $report.address_text_write_attempts -ne 0 -or
      $report.consent_action_attempts -ne 0 -or
      $report.next_click_attempts -ne 0 -or
      $report.file_upload_attempts -ne 0 -or
      $report.submit_attempts -ne 0 -or
      $report.state_candidate_values_read -ne $false) {
    throw 'ADP State-after-Country contract exceeded its reviewed authority.'
  }

  Write-Host 'ADP State-after-Country contract captured. State value was not read or written.'
  $report | ConvertTo-Json -Depth 18 -Compress
} finally {
  foreach ($Path in @($statePath, $reportPath, $contractPath)) {
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
  }
}
