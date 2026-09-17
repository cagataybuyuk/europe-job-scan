param(
  [string]$Repo = "cagataybuyuk/europe-job-scan",
  [string]$Environment = "gd004-safe-fill-upload-canary"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot 'lib/invoke_native_utf8_stdin.ps1')

$country = (Read-Host "Phone country ISO-2 (orn: TR)").Trim().ToUpperInvariant()
if ($country -notmatch '^[A-Z]{2}$') {
  throw "Phone country must be exactly two uppercase ASCII letters (for example TR)."
}

$phone = (Read-Host "Phone national number - country code and trunk prefix excluded").Trim()
if ($phone -notmatch '^\d+$') {
  throw "Phone national number must contain digits only."
}

if ($country -eq 'TR') {
  if ($phone -notmatch '^5\d{9}$') {
    throw "For TR mobile numbers, enter exactly 10 digits starting with 5, without +90 and without the leading 0 (example: 5XXXXXXXXX)."
  }
} elseif ($phone.Length -lt 4 -or $phone.Length -gt 15) {
  throw "Phone national number length must be between 4 and 15 digits."
}

$answer = (Read-Host "ADP icin Turkce karakterli ad/soyadi ASCII'ye cevirmeyi onayliyor musun? [E/H]").Trim()
if ($answer -notmatch '^(e|evet|h|hayir|hayır|y|yes|n|no)$') {
  throw "Please answer E/H (or yes/no)."
}
$approval = $answer -match '^(e|evet|y|yes)$'

$extension = @{
  phone_country_iso2 = $country
  phone_national_number = $phone
  adp_ascii_name_policy_approved = [bool]$approval
} | ConvertTo-Json -Compress

try {
  Invoke-GhSecretSetUtf8 `
    -SecretName 'EJS_ADP_CANARY_PROFILE_V2_EXTENSION_JSON' `
    -Json $extension `
    -Repo $Repo `
    -Environment $Environment
  Write-Host "ADP profile-v2 extension secret updated successfully with byte-safe UTF-8 stdin."
} finally {
  Remove-Variable extension, phone, country, answer, approval -ErrorAction SilentlyContinue
}
