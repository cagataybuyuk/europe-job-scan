param(
  [string]$ApplicationUrl = 'https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=eae41664-19fb-4412-96f8-43f15d52332b&ccId=19000101_000001&jobId=955507&source=LR&lang=en_US',
  [string]$ExpectedManifestFingerprint = '56f71967ac378836d170ab91b63ced2136a8988e5ce218b7750b25d80fcb51ac',
  [string]$ExpectedContactContractFingerprint = '53b635e9fa5d36f3d8e320f8a406204a093592901226b8ba9886253ceae285ad',
  [string]$ExpectedCountrySurfaceFingerprint = '7b4b13d36c10eb0be71cfe5154de1e80c06c5395a3a00098127d9a9efcf0515f',
  [string]$ExpectedStateSurfaceFingerprint = '33e4bba11523aeb783c47335d9764d6785eccfa9b6da3aaa4e09b59674a2a4aa',
  [switch]$AllowReviewedTurkishAsciiNameOverwrite,
  [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

foreach ($Fingerprint in @(
  $ExpectedManifestFingerprint,
  $ExpectedContactContractFingerprint,
  $ExpectedCountrySurfaceFingerprint,
  $ExpectedStateSurfaceFingerprint
)) {
  if ([string]::IsNullOrWhiteSpace($Fingerprint) -or
      $Fingerprint.Length -ne 64 -or
      $Fingerprint -match '[^0-9a-f]') {
    throw 'Reviewed fingerprints must be exactly 64 lowercase hex characters.'
  }
}

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
    $Probe = & $Command.Source @PrefixArgs -c "import sys; print(sys.version_info.major)" 2>$null
    if ($LASTEXITCODE -eq 0 -and (($Probe | Out-String).Trim()) -eq '3') {
      return [pscustomobject]@{
        Source = $Command.Source
        PrefixArgs = $PrefixArgs
        DisplayName = if ($PrefixArgs.Count) { "$($Candidate.Name) $($PrefixArgs -join ' ')" } else { $Candidate.Name }
      }
    }
  }
  throw 'No usable Python 3 interpreter was found.'
}

function Read-ExactLocalText([string]$Prompt, [string]$FieldName, [bool]$Required = $true) {
  $value = Read-Host $Prompt
  if ($null -eq $value) { $value = '' }
  if ($value -ne $value.Trim()) {
    throw "$FieldName cannot contain leading or trailing whitespace."
  }
  if ($Required -and [string]::IsNullOrWhiteSpace($value)) {
    throw "$FieldName cannot be empty."
  }
  return $value.Normalize([Text.NormalizationForm]::FormC)
}

$token = [Guid]::NewGuid().ToString('N')
$tempRoot = [IO.Path]::GetTempPath()
$statePath = Join-Path $tempRoot ("ejs-adp-personal-info-state-$token.json")
$bootstrapReportPath = Join-Path $tempRoot ("ejs-adp-personal-info-bootstrap-$token.json")
$profilePath = Join-Path $tempRoot ("ejs-adp-personal-info-profile-$token.json")
$safeFillReportPath = Join-Path $tempRoot ("ejs-adp-personal-info-safe-fill-$token.json")

$profileJson = $null

try {
  $python = Resolve-EjsPython3
  $pythonPrefixArgs = @($python.PrefixArgs)
  Write-Host "Using Python launcher: $($python.DisplayName)"
  Write-Host 'Enter exact Personal Information values locally. Raw values will not be printed in the result.'

  $first = Read-ExactLocalText 'First name' 'First name'
  $last = Read-ExactLocalText 'Last name' 'Last name'
  $email = Read-ExactLocalText 'Email' 'Email'
  if ($email -notmatch '^[^ @]+@[^ @]+[.][^ @]+$') {
    throw 'Email format is invalid.'
  }

  $phoneCountry = (Read-ExactLocalText 'Mobile phone country ISO-2 (for example TR)' 'Mobile phone country').ToUpperInvariant()
  if ($phoneCountry -notmatch '^[A-Z]{2}$') {
    throw 'Mobile phone country must be exactly two uppercase ASCII letters.'
  }
  $phone = Read-ExactLocalText 'Mobile national number - digits only, without country code' 'Mobile national number'
  if ($phone -notmatch '^[0-9]{4,20}$') {
    throw 'Mobile national number must contain 4-20 digits only.'
  }
  if ($phoneCountry -eq 'TR' -and $phone -notmatch '^5[0-9]{9}$') {
    throw 'For TR mobile, enter 10 digits starting with 5, without +90 and without a leading 0.'
  }

  $addressCountry = (Read-ExactLocalText 'Address country ISO-2 (reviewed runtime currently supports TR)' 'Address country').ToUpperInvariant()
  if ($addressCountry -ne 'TR') {
    throw 'This reviewed ADP canary currently supports address country TR only.'
  }
  $line1 = Read-ExactLocalText 'Address Line 1' 'Address Line 1'
  $line2 = Read-ExactLocalText 'Address Line 2 (optional; press Enter if empty)' 'Address Line 2' $false
  $line3 = Read-ExactLocalText 'Address Line 3 (optional; press Enter if empty)' 'Address Line 3' $false
  $city = Read-ExactLocalText 'City' 'City'
  $state = Read-ExactLocalText 'State / Territory (enter the exact ADP visible label, e.g. İstanbul)' 'State / Territory'
  $postal = Read-ExactLocalText 'Postal Code' 'Postal Code'

  $profileJson = @{
    profile_version = 'adp-personal-information-local-canary-v1'
    first_name = $first
    last_name = $last
    email = $email
    phone_country_iso2 = $phoneCountry
    phone_national_number = $phone
    adp_ascii_name_policy_approved = [bool]$AllowReviewedTurkishAsciiNameOverwrite
    address_country_iso2 = $addressCountry
    address_line1 = $line1
    address_line2 = $line2
    address_line3 = $line3
    city = $city
    state_or_territory = $state
    postal_code = $postal
  } | ConvertTo-Json -Compress

  $utf8NoBom = New-Object System.Text.UTF8Encoding -ArgumentList $false
  [IO.File]::WriteAllText($profilePath, $profileJson, $utf8NoBom)

  Write-Host 'A verified ADP browser will open.'
  Write-Host 'Complete Apply / identity / verification manually.'
  Write-Host 'When Personal Information opens, do not edit fields and do not click Next.'
  Write-Host 'The executor may verify/fill identity, required Mobile Number, Turkey, and reviewed address fields only.'
  Write-Host 'Home Phone, consent, Next, upload and submit remain disabled.'

  $args = @(
    '-m', 'ejs.services.adp_verified_session_bootstrap',
    '--url', $ApplicationUrl,
    '--storage-state-out', $statePath,
    '--report-out', $bootstrapReportPath,
    '--same-page-personal-information-profile', $profilePath,
    '--same-page-personal-information-expected-manifest-fingerprint', $ExpectedManifestFingerprint,
    '--same-page-personal-information-expected-contact-contract-fingerprint', $ExpectedContactContractFingerprint,
    '--same-page-personal-information-expected-country-surface-fingerprint', $ExpectedCountrySurfaceFingerprint,
    '--same-page-personal-information-expected-state-surface-fingerprint', $ExpectedStateSurfaceFingerprint,
    '--same-page-personal-information-report-out', $safeFillReportPath,
    '--timeout-seconds', [string]$TimeoutSeconds
  )
  if ($AllowReviewedTurkishAsciiNameOverwrite) {
    $args += '--same-page-personal-information-allow-reviewed-turkish-ascii-name-overwrite'
  }

  & $python.Source @pythonPrefixArgs @args
  if ($LASTEXITCODE -ne 0) {
    throw "ADP Personal Information safe-fill bootstrap failed with exit code $LASTEXITCODE."
  }

  if (-not (Test-Path -LiteralPath $safeFillReportPath)) {
    throw 'ADP Personal Information safe-fill report was not created.'
  }
  $report = Get-Content -Raw -LiteralPath $safeFillReportPath | ConvertFrom-Json

  if ($report.safe_fill_status -ne 'verified' -or
      $report.home_phone_write_attempts -ne 0 -or
      $report.consent_action_attempts -ne 0 -or
      $report.navigation_click_attempts -ne 0 -or
      $report.state_selection_attempts -notin @(0,1) -or
      $report.state_selection_successes -notin @(0,1) -or
      $report.next_click_attempts -ne 0 -or
      $report.file_upload_attempts -ne 0 -or
      $report.submit_attempts -ne 0) {
    throw 'ADP Personal Information safe-fill exceeded its reviewed authority boundary.'
  }

  Write-Host 'ADP Personal Information safe-fill verified. Next/upload/submit were not performed.'
  [pscustomobject]@{
    safe_fill_status = $report.safe_fill_status
    executor_version = $report.executor_version
    form_value_write_attempts = $report.form_value_write_attempts
    form_value_write_successes = $report.form_value_write_successes
    phone_write_attempts = $report.phone_write_attempts
    phone_write_successes = $report.phone_write_successes
    address_write_attempts = $report.address_write_attempts
    address_write_successes = $report.address_write_successes
    country_selection_attempts = $report.country_selection_attempts
    country_selection_successes = $report.country_selection_successes
    state_selection_attempts = $report.state_selection_attempts
    state_selection_successes = $report.state_selection_successes
    email_readback_match = $report.identity_result.email_readback_match
    home_phone_write_attempts = $report.home_phone_write_attempts
    next_click_attempts = $report.next_click_attempts
    file_upload_attempts = $report.file_upload_attempts
    submit_attempts = $report.submit_attempts
    raw_values_exposed = $false
  } | ConvertTo-Json -Compress
} finally {
  foreach ($Path in @($statePath, $bootstrapReportPath, $profilePath, $safeFillReportPath)) {
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
  }
  Remove-Variable profileJson, first, last, email, phoneCountry, phone, addressCountry, line1, line2, line3, city, state, postal, utf8NoBom -ErrorAction SilentlyContinue
}
