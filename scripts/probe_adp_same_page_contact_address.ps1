param(
  [Parameter(Mandatory = $true)]
  [string]$ApplicationUrl,
  [Parameter(Mandatory = $true)]
  [string]$ExpectedManifestFingerprint,
  [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($ExpectedManifestFingerprint.Length -ne 64 -or $ExpectedManifestFingerprint -match '[^0-9a-f]') {
  throw 'ExpectedManifestFingerprint must be exactly 64 lowercase hex characters.'
}

$python = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $python) {
  throw 'Python launcher py was not found.'
}

$token = [Guid]::NewGuid().ToString('N')
$tempRoot = [IO.Path]::GetTempPath()
$statePath = Join-Path $tempRoot ("ejs-adp-contact-state-$token.json")
$reportPath = Join-Path $tempRoot ("ejs-adp-contact-bootstrap-$token.json")
$manifestPath = Join-Path $tempRoot ("ejs-adp-contact-manifest-$token.json")
$contractPath = Join-Path $tempRoot ("ejs-adp-contact-contract-$token.json")

try {
  Write-Host 'A verified ADP browser will open for a read-only contact/address contract capture.'
  Write-Host 'When Personal Information opens, do not edit fields and do not click Next.'
  Write-Host 'No candidate field value will be read or written by this probe.'

  & $python.Source -3 -m ejs.services.adp_verified_session_bootstrap --url $ApplicationUrl --storage-state-out $statePath --report-out $reportPath --same-page-manifest-out $manifestPath --same-page-contact-address-contract-out $contractPath --same-page-contact-address-expected-manifest-fingerprint $ExpectedManifestFingerprint --timeout-seconds $TimeoutSeconds
  if ($LASTEXITCODE -ne 0) {
    throw "ADP contact/address contract bootstrap failed with exit code $LASTEXITCODE."
  }

  $report = Get-Content -Raw -LiteralPath $contractPath | ConvertFrom-Json
  if ($report.form_value_write_attempts -ne 0 -or
      $report.phone_write_attempts -ne 0 -or
      $report.address_write_attempts -ne 0 -or
      $report.next_click_attempts -ne 0 -or
      $report.file_upload_attempts -ne 0 -or
      $report.submit_attempts -ne 0 -or
      $report.input_values_read -ne $false) {
    throw 'ADP contact/address contract violated the read-only boundary.'
  }

  Write-Host 'ADP contact/address contract captured read-only.'
  $report | ConvertTo-Json -Depth 14 -Compress
} finally {
  foreach ($Path in @($statePath, $reportPath, $manifestPath, $contractPath)) {
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
  }
}
