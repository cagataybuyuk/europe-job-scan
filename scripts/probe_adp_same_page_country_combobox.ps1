param(
  [string]$ApplicationUrl = 'https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=eae41664-19fb-4412-96f8-43f15d52332b&ccId=19000101_000001&jobId=955507&source=LR&lang=en_US',
  [string]$ExpectedManifestFingerprint = '',
  [string]$ExpectedContactContractFingerprint = '',
  [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

foreach ($Fingerprint in @($ExpectedManifestFingerprint, $ExpectedContactContractFingerprint)) {
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
$statePath = Join-Path $tempRoot ("ejs-adp-country-state-$token.json")
$reportPath = Join-Path $tempRoot ("ejs-adp-country-bootstrap-$token.json")
$probePath = Join-Path $tempRoot ("ejs-adp-country-probe-$token.json")

try {
  Write-Host 'A verified ADP browser will open for a one-click Country combobox option probe.'
  Write-Host 'Complete Apply / identity / verification manually.'
  Write-Host 'When Personal Information opens, do not edit fields and do not click Next.'
  Write-Host 'The probe may click Country once to expose options; it will not select an option or write any candidate value.'

  & $python.Source -3 -m ejs.services.adp_verified_session_bootstrap --url $ApplicationUrl --storage-state-out $statePath --report-out $reportPath --same-page-country-combobox-probe-out $probePath --same-page-country-expected-manifest-fingerprint $ExpectedManifestFingerprint --same-page-country-expected-contact-contract-fingerprint $ExpectedContactContractFingerprint --timeout-seconds $TimeoutSeconds
  if ($LASTEXITCODE -ne 0) {
    throw "ADP Country combobox probe failed with exit code $LASTEXITCODE."
  }

  if (-not (Test-Path -LiteralPath $probePath)) {
    throw 'ADP Country combobox probe report was not created.'
  }
  $report = Get-Content -Raw -LiteralPath $probePath | ConvertFrom-Json

  if ($report.combobox_open_click_attempts -ne 1 -or
      $report.country_selection_attempts -ne 0 -or
      $report.form_value_write_attempts -ne 0 -or
      $report.phone_write_attempts -ne 0 -or
      $report.address_write_attempts -ne 0 -or
      $report.next_click_attempts -ne 0 -or
      $report.file_upload_attempts -ne 0 -or
      $report.submit_attempts -ne 0 -or
      $report.input_values_read -ne $false) {
    throw 'ADP Country combobox probe exceeded its reviewed one-click read-only authority.'
  }

  Write-Host 'ADP Country option surface captured. No option was selected and no candidate value was written.'
  $report | ConvertTo-Json -Depth 14 -Compress
} finally {
  foreach ($Path in @($statePath, $reportPath, $probePath)) {
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
  }
}
