param(
  [string]$ApplicationUrl = 'https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=eae41664-19fb-4412-96f8-43f15d52332b&ccId=19000101_000001&jobId=955507&source=LR&lang=en_US',
  [string]$ExpectedManifestFingerprint = '',
  [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($ExpectedManifestFingerprint) -or
    $ExpectedManifestFingerprint.Length -ne 64 -or
    $ExpectedManifestFingerprint -match '[^0-9a-f]') {
  throw 'ExpectedManifestFingerprint is required and must be exactly 64 lowercase hex characters.'
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

function Read-ExactLocalText([string]$Prompt, [string]$FieldName) {
  $value = Read-Host $Prompt
  if ([string]::IsNullOrWhiteSpace($value)) {
    throw "$FieldName cannot be empty."
  }
  if ($value -ne $value.Trim()) {
    throw "$FieldName cannot contain leading or trailing whitespace."
  }
  return $value.Normalize([Text.NormalizationForm]::FormC)
}

$token = [Guid]::NewGuid().ToString('N')
$tempRoot = [IO.Path]::GetTempPath()
$statePath = Join-Path $tempRoot ("ejs-adp-same-page-fill-state-$token.json")
$bootstrapReportPath = Join-Path $tempRoot ("ejs-adp-same-page-fill-bootstrap-$token.json")
$sessionStoragePath = Join-Path $tempRoot ("ejs-adp-same-page-fill-session-$token.json")
$postLoginUrlPath = Join-Path $tempRoot ("ejs-adp-same-page-fill-postlogin-$token.txt")
$manifestPath = Join-Path $tempRoot ("ejs-adp-same-page-fill-manifest-$token.json")
$profilePath = Join-Path $tempRoot ("ejs-adp-same-page-fill-profile-$token.json")
$safeFillReportPath = Join-Path $tempRoot ("ejs-adp-same-page-fill-report-$token.json")

$first = $null
$last = $null
$email = $null
$profileJson = $null

try {
  $python = Resolve-EjsPython3
  $pythonPrefixArgs = @($python.PrefixArgs)
  Write-Host "Using Python launcher: $($python.DisplayName)"
  Write-Host 'Enter the exact identity values intended for this ADP application.'
  Write-Host 'A non-empty browser value that differs from your input will block instead of being overwritten.'

  $first = Read-ExactLocalText 'First name' 'First name'
  $last = Read-ExactLocalText 'Last name' 'Last name'
  $email = Read-ExactLocalText 'Email' 'Email'
  if ($email -notmatch '^[^\s@]+@[^\s@]+\.[^\s@]+$') {
    throw 'Email format is invalid.'
  }

  $profileJson = @{
    profile_version = 'adp-same-page-local-canary-v1'
    first_name = $first
    last_name = $last
    email = $email
  } | ConvertTo-Json -Compress
  [IO.File]::WriteAllText(
    $profilePath,
    $profileJson,
    (New-Object System.Text.UTF8Encoding($false))
  )

  Write-Host 'A verified ADP browser will open.'
  Write-Host 'Complete Apply / identity / verification manually.'
  Write-Host 'When Personal Information opens, do not edit fields and do not click Next.'
  Write-Host 'The executor may fill only a blank reviewed First Name or Last Name control.'

  & $python.Source @pythonPrefixArgs -m ejs.services.adp_verified_session_bootstrap --url $ApplicationUrl --storage-state-out $statePath --report-out $bootstrapReportPath --session-storage-out $sessionStoragePath --postlogin-url-out $postLoginUrlPath --same-page-manifest-out $manifestPath --same-page-safe-fill-profile $profilePath --same-page-safe-fill-expected-manifest-fingerprint $ExpectedManifestFingerprint --same-page-safe-fill-report-out $safeFillReportPath --timeout-seconds $TimeoutSeconds

  if ($LASTEXITCODE -ne 0) {
    throw "ADP same-page safe-fill bootstrap failed with exit code $LASTEXITCODE."
  }
  if (-not (Test-Path -LiteralPath $safeFillReportPath)) {
    throw 'ADP same-page safe-fill report was not created.'
  }

  $report = Get-Content -Raw -LiteralPath $safeFillReportPath | ConvertFrom-Json
  if ($report.safe_fill_status -ne 'verified') {
    throw 'ADP same-page safe-fill did not reach verified status.'
  }
  if ($report.navigation_click_attempts -ne 0 -or
      $report.phone_write_attempts -ne 0 -or
      $report.address_write_attempts -ne 0 -or
      $report.consent_action_attempts -ne 0 -or
      $report.next_click_attempts -ne 0 -or
      $report.file_upload_attempts -ne 0 -or
      $report.submit_attempts -ne 0) {
    throw 'ADP same-page safe-fill exceeded its reviewed authority boundary.'
  }

  Write-Host 'ADP same-page identity safe-fill verified. No navigation, phone/address, upload, or submit action was performed.'
  [pscustomobject]@{
    safe_fill_status = $report.safe_fill_status
    executor_version = $report.executor_version
    observed_manifest_fingerprint = $report.observed_manifest_fingerprint
    form_value_write_attempts = $report.form_value_write_attempts
    form_value_write_successes = $report.form_value_write_successes
    email_readback_match = $report.email_readback_only.readback_match
    next_click_attempts = $report.next_click_attempts
    file_upload_attempts = $report.file_upload_attempts
    submit_attempts = $report.submit_attempts
    raw_values_exposed = $false
  } | ConvertTo-Json -Compress
} finally {
  foreach ($Path in @(
    $statePath,
    $bootstrapReportPath,
    $sessionStoragePath,
    $postLoginUrlPath,
    $manifestPath,
    $profilePath,
    $safeFillReportPath
  )) {
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
  }
  Remove-Variable first, last, email, profileJson -ErrorAction SilentlyContinue
}
)]
  [string]$ExpectedManifestFingerprint,
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

function Read-ExactLocalText([string]$Prompt, [string]$FieldName) {
  $value = Read-Host $Prompt
  if ([string]::IsNullOrWhiteSpace($value)) {
    throw "$FieldName cannot be empty."
  }
  if ($value -ne $value.Trim()) {
    throw "$FieldName cannot contain leading or trailing whitespace."
  }
  return $value.Normalize([Text.NormalizationForm]::FormC)
}

$token = [Guid]::NewGuid().ToString('N')
$tempRoot = [IO.Path]::GetTempPath()
$statePath = Join-Path $tempRoot ("ejs-adp-same-page-fill-state-$token.json")
$bootstrapReportPath = Join-Path $tempRoot ("ejs-adp-same-page-fill-bootstrap-$token.json")
$sessionStoragePath = Join-Path $tempRoot ("ejs-adp-same-page-fill-session-$token.json")
$postLoginUrlPath = Join-Path $tempRoot ("ejs-adp-same-page-fill-postlogin-$token.txt")
$manifestPath = Join-Path $tempRoot ("ejs-adp-same-page-fill-manifest-$token.json")
$profilePath = Join-Path $tempRoot ("ejs-adp-same-page-fill-profile-$token.json")
$safeFillReportPath = Join-Path $tempRoot ("ejs-adp-same-page-fill-report-$token.json")

$first = $null
$last = $null
$email = $null
$profileJson = $null

try {
  $python = Resolve-EjsPython3
  $pythonPrefixArgs = @($python.PrefixArgs)
  Write-Host "Using Python launcher: $($python.DisplayName)"
  Write-Host 'Enter the exact identity values intended for this ADP application.'
  Write-Host 'A non-empty browser value that differs from your input will block instead of being overwritten.'

  $first = Read-ExactLocalText 'First name' 'First name'
  $last = Read-ExactLocalText 'Last name' 'Last name'
  $email = Read-ExactLocalText 'Email' 'Email'
  if ($email -notmatch '^[^\s@]+@[^\s@]+\.[^\s@]+$') {
    throw 'Email format is invalid.'
  }

  $profileJson = @{
    profile_version = 'adp-same-page-local-canary-v1'
    first_name = $first
    last_name = $last
    email = $email
  } | ConvertTo-Json -Compress
  [IO.File]::WriteAllText(
    $profilePath,
    $profileJson,
    (New-Object System.Text.UTF8Encoding($false))
  )

  Write-Host 'A verified ADP browser will open.'
  Write-Host 'Complete Apply / identity / verification manually.'
  Write-Host 'When Personal Information opens, do not edit fields and do not click Next.'
  Write-Host 'The executor may fill only a blank reviewed First Name or Last Name control.'

  & $python.Source @pythonPrefixArgs -m ejs.services.adp_verified_session_bootstrap --url $ApplicationUrl --storage-state-out $statePath --report-out $bootstrapReportPath --session-storage-out $sessionStoragePath --postlogin-url-out $postLoginUrlPath --same-page-manifest-out $manifestPath --same-page-safe-fill-profile $profilePath --same-page-safe-fill-expected-manifest-fingerprint $ExpectedManifestFingerprint --same-page-safe-fill-report-out $safeFillReportPath --timeout-seconds $TimeoutSeconds

  if ($LASTEXITCODE -ne 0) {
    throw "ADP same-page safe-fill bootstrap failed with exit code $LASTEXITCODE."
  }
  if (-not (Test-Path -LiteralPath $safeFillReportPath)) {
    throw 'ADP same-page safe-fill report was not created.'
  }

  $report = Get-Content -Raw -LiteralPath $safeFillReportPath | ConvertFrom-Json
  if ($report.safe_fill_status -ne 'verified') {
    throw 'ADP same-page safe-fill did not reach verified status.'
  }
  if ($report.navigation_click_attempts -ne 0 -or
      $report.phone_write_attempts -ne 0 -or
      $report.address_write_attempts -ne 0 -or
      $report.consent_action_attempts -ne 0 -or
      $report.next_click_attempts -ne 0 -or
      $report.file_upload_attempts -ne 0 -or
      $report.submit_attempts -ne 0) {
    throw 'ADP same-page safe-fill exceeded its reviewed authority boundary.'
  }

  Write-Host 'ADP same-page identity safe-fill verified. No navigation, phone/address, upload, or submit action was performed.'
  [pscustomobject]@{
    safe_fill_status = $report.safe_fill_status
    executor_version = $report.executor_version
    observed_manifest_fingerprint = $report.observed_manifest_fingerprint
    form_value_write_attempts = $report.form_value_write_attempts
    form_value_write_successes = $report.form_value_write_successes
    email_readback_match = $report.email_readback_only.readback_match
    next_click_attempts = $report.next_click_attempts
    file_upload_attempts = $report.file_upload_attempts
    submit_attempts = $report.submit_attempts
    raw_values_exposed = $false
  } | ConvertTo-Json -Compress
} finally {
  foreach ($Path in @(
    $statePath,
    $bootstrapReportPath,
    $sessionStoragePath,
    $postLoginUrlPath,
    $manifestPath,
    $profilePath,
    $safeFillReportPath
  )) {
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
  }
  Remove-Variable first, last, email, profileJson -ErrorAction SilentlyContinue
}
) {
  throw 'ExpectedManifestFingerprint is required and must be exactly 64 lowercase hex characters.'
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

function Read-ExactLocalText([string]$Prompt, [string]$FieldName) {
  $value = Read-Host $Prompt
  if ([string]::IsNullOrWhiteSpace($value)) {
    throw "$FieldName cannot be empty."
  }
  if ($value -ne $value.Trim()) {
    throw "$FieldName cannot contain leading or trailing whitespace."
  }
  return $value.Normalize([Text.NormalizationForm]::FormC)
}

$token = [Guid]::NewGuid().ToString('N')
$tempRoot = [IO.Path]::GetTempPath()
$statePath = Join-Path $tempRoot ("ejs-adp-same-page-fill-state-$token.json")
$bootstrapReportPath = Join-Path $tempRoot ("ejs-adp-same-page-fill-bootstrap-$token.json")
$sessionStoragePath = Join-Path $tempRoot ("ejs-adp-same-page-fill-session-$token.json")
$postLoginUrlPath = Join-Path $tempRoot ("ejs-adp-same-page-fill-postlogin-$token.txt")
$manifestPath = Join-Path $tempRoot ("ejs-adp-same-page-fill-manifest-$token.json")
$profilePath = Join-Path $tempRoot ("ejs-adp-same-page-fill-profile-$token.json")
$safeFillReportPath = Join-Path $tempRoot ("ejs-adp-same-page-fill-report-$token.json")

$first = $null
$last = $null
$email = $null
$profileJson = $null

try {
  $python = Resolve-EjsPython3
  $pythonPrefixArgs = @($python.PrefixArgs)
  Write-Host "Using Python launcher: $($python.DisplayName)"
  Write-Host 'Enter the exact identity values intended for this ADP application.'
  Write-Host 'A non-empty browser value that differs from your input will block instead of being overwritten.'

  $first = Read-ExactLocalText 'First name' 'First name'
  $last = Read-ExactLocalText 'Last name' 'Last name'
  $email = Read-ExactLocalText 'Email' 'Email'
  if ($email -notmatch '^[^\s@]+@[^\s@]+\.[^\s@]+$') {
    throw 'Email format is invalid.'
  }

  $profileJson = @{
    profile_version = 'adp-same-page-local-canary-v1'
    first_name = $first
    last_name = $last
    email = $email
  } | ConvertTo-Json -Compress
  [IO.File]::WriteAllText(
    $profilePath,
    $profileJson,
    (New-Object System.Text.UTF8Encoding($false))
  )

  Write-Host 'A verified ADP browser will open.'
  Write-Host 'Complete Apply / identity / verification manually.'
  Write-Host 'When Personal Information opens, do not edit fields and do not click Next.'
  Write-Host 'The executor may fill only a blank reviewed First Name or Last Name control.'

  & $python.Source @pythonPrefixArgs -m ejs.services.adp_verified_session_bootstrap --url $ApplicationUrl --storage-state-out $statePath --report-out $bootstrapReportPath --session-storage-out $sessionStoragePath --postlogin-url-out $postLoginUrlPath --same-page-manifest-out $manifestPath --same-page-safe-fill-profile $profilePath --same-page-safe-fill-expected-manifest-fingerprint $ExpectedManifestFingerprint --same-page-safe-fill-report-out $safeFillReportPath --timeout-seconds $TimeoutSeconds

  if ($LASTEXITCODE -ne 0) {
    throw "ADP same-page safe-fill bootstrap failed with exit code $LASTEXITCODE."
  }
  if (-not (Test-Path -LiteralPath $safeFillReportPath)) {
    throw 'ADP same-page safe-fill report was not created.'
  }

  $report = Get-Content -Raw -LiteralPath $safeFillReportPath | ConvertFrom-Json
  if ($report.safe_fill_status -ne 'verified') {
    throw 'ADP same-page safe-fill did not reach verified status.'
  }
  if ($report.navigation_click_attempts -ne 0 -or
      $report.phone_write_attempts -ne 0 -or
      $report.address_write_attempts -ne 0 -or
      $report.consent_action_attempts -ne 0 -or
      $report.next_click_attempts -ne 0 -or
      $report.file_upload_attempts -ne 0 -or
      $report.submit_attempts -ne 0) {
    throw 'ADP same-page safe-fill exceeded its reviewed authority boundary.'
  }

  Write-Host 'ADP same-page identity safe-fill verified. No navigation, phone/address, upload, or submit action was performed.'
  [pscustomobject]@{
    safe_fill_status = $report.safe_fill_status
    executor_version = $report.executor_version
    observed_manifest_fingerprint = $report.observed_manifest_fingerprint
    form_value_write_attempts = $report.form_value_write_attempts
    form_value_write_successes = $report.form_value_write_successes
    email_readback_match = $report.email_readback_only.readback_match
    next_click_attempts = $report.next_click_attempts
    file_upload_attempts = $report.file_upload_attempts
    submit_attempts = $report.submit_attempts
    raw_values_exposed = $false
  } | ConvertTo-Json -Compress
} finally {
  foreach ($Path in @(
    $statePath,
    $bootstrapReportPath,
    $sessionStoragePath,
    $postLoginUrlPath,
    $manifestPath,
    $profilePath,
    $safeFillReportPath
  )) {
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
  }
  Remove-Variable first, last, email, profileJson -ErrorAction SilentlyContinue
}
)]
  [string]$ExpectedManifestFingerprint,
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

function Read-ExactLocalText([string]$Prompt, [string]$FieldName) {
  $value = Read-Host $Prompt
  if ([string]::IsNullOrWhiteSpace($value)) {
    throw "$FieldName cannot be empty."
  }
  if ($value -ne $value.Trim()) {
    throw "$FieldName cannot contain leading or trailing whitespace."
  }
  return $value.Normalize([Text.NormalizationForm]::FormC)
}

$token = [Guid]::NewGuid().ToString('N')
$tempRoot = [IO.Path]::GetTempPath()
$statePath = Join-Path $tempRoot ("ejs-adp-same-page-fill-state-$token.json")
$bootstrapReportPath = Join-Path $tempRoot ("ejs-adp-same-page-fill-bootstrap-$token.json")
$sessionStoragePath = Join-Path $tempRoot ("ejs-adp-same-page-fill-session-$token.json")
$postLoginUrlPath = Join-Path $tempRoot ("ejs-adp-same-page-fill-postlogin-$token.txt")
$manifestPath = Join-Path $tempRoot ("ejs-adp-same-page-fill-manifest-$token.json")
$profilePath = Join-Path $tempRoot ("ejs-adp-same-page-fill-profile-$token.json")
$safeFillReportPath = Join-Path $tempRoot ("ejs-adp-same-page-fill-report-$token.json")

$first = $null
$last = $null
$email = $null
$profileJson = $null

try {
  $python = Resolve-EjsPython3
  $pythonPrefixArgs = @($python.PrefixArgs)
  Write-Host "Using Python launcher: $($python.DisplayName)"
  Write-Host 'Enter the exact identity values intended for this ADP application.'
  Write-Host 'A non-empty browser value that differs from your input will block instead of being overwritten.'

  $first = Read-ExactLocalText 'First name' 'First name'
  $last = Read-ExactLocalText 'Last name' 'Last name'
  $email = Read-ExactLocalText 'Email' 'Email'
  if ($email -notmatch '^[^\s@]+@[^\s@]+\.[^\s@]+$') {
    throw 'Email format is invalid.'
  }

  $profileJson = @{
    profile_version = 'adp-same-page-local-canary-v1'
    first_name = $first
    last_name = $last
    email = $email
  } | ConvertTo-Json -Compress
  [IO.File]::WriteAllText(
    $profilePath,
    $profileJson,
    (New-Object System.Text.UTF8Encoding($false))
  )

  Write-Host 'A verified ADP browser will open.'
  Write-Host 'Complete Apply / identity / verification manually.'
  Write-Host 'When Personal Information opens, do not edit fields and do not click Next.'
  Write-Host 'The executor may fill only a blank reviewed First Name or Last Name control.'

  & $python.Source @pythonPrefixArgs -m ejs.services.adp_verified_session_bootstrap --url $ApplicationUrl --storage-state-out $statePath --report-out $bootstrapReportPath --session-storage-out $sessionStoragePath --postlogin-url-out $postLoginUrlPath --same-page-manifest-out $manifestPath --same-page-safe-fill-profile $profilePath --same-page-safe-fill-expected-manifest-fingerprint $ExpectedManifestFingerprint --same-page-safe-fill-report-out $safeFillReportPath --timeout-seconds $TimeoutSeconds

  if ($LASTEXITCODE -ne 0) {
    throw "ADP same-page safe-fill bootstrap failed with exit code $LASTEXITCODE."
  }
  if (-not (Test-Path -LiteralPath $safeFillReportPath)) {
    throw 'ADP same-page safe-fill report was not created.'
  }

  $report = Get-Content -Raw -LiteralPath $safeFillReportPath | ConvertFrom-Json
  if ($report.safe_fill_status -ne 'verified') {
    throw 'ADP same-page safe-fill did not reach verified status.'
  }
  if ($report.navigation_click_attempts -ne 0 -or
      $report.phone_write_attempts -ne 0 -or
      $report.address_write_attempts -ne 0 -or
      $report.consent_action_attempts -ne 0 -or
      $report.next_click_attempts -ne 0 -or
      $report.file_upload_attempts -ne 0 -or
      $report.submit_attempts -ne 0) {
    throw 'ADP same-page safe-fill exceeded its reviewed authority boundary.'
  }

  Write-Host 'ADP same-page identity safe-fill verified. No navigation, phone/address, upload, or submit action was performed.'
  [pscustomobject]@{
    safe_fill_status = $report.safe_fill_status
    executor_version = $report.executor_version
    observed_manifest_fingerprint = $report.observed_manifest_fingerprint
    form_value_write_attempts = $report.form_value_write_attempts
    form_value_write_successes = $report.form_value_write_successes
    email_readback_match = $report.email_readback_only.readback_match
    next_click_attempts = $report.next_click_attempts
    file_upload_attempts = $report.file_upload_attempts
    submit_attempts = $report.submit_attempts
    raw_values_exposed = $false
  } | ConvertTo-Json -Compress
} finally {
  foreach ($Path in @(
    $statePath,
    $bootstrapReportPath,
    $sessionStoragePath,
    $postLoginUrlPath,
    $manifestPath,
    $profilePath,
    $safeFillReportPath
  )) {
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
  }
  Remove-Variable first, last, email, profileJson -ErrorAction SilentlyContinue
}
