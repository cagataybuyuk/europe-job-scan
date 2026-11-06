param(
  [string]$ProfilePath = (Join-Path (Get-Location) '.ejs-local\candidate-profile.json')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

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

Write-Host 'Creating the EJS local private candidate profile.'
Write-Host 'This is a one-time local setup. The file is gitignored and raw values are not printed.'

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

$addressCountry = (Read-ExactLocalText 'Address country ISO-2' 'Address country').ToUpperInvariant()
$line1 = Read-ExactLocalText 'Address Line 1' 'Address Line 1'
$line2 = Read-ExactLocalText 'Address Line 2 (optional; press Enter if empty)' 'Address Line 2' $false
$line3 = Read-ExactLocalText 'Address Line 3 (optional; press Enter if empty)' 'Address Line 3' $false
$city = Read-ExactLocalText 'City' 'City'
$state = Read-ExactLocalText 'State / Territory (exact ATS-visible label)' 'State / Territory'
$postal = Read-ExactLocalText 'Postal Code' 'Postal Code'

$payload = @{
  profile_version = 'ejs-local-candidate-profile-v1'
  first_name = $first
  last_name = $last
  email = $email
  phone_country_iso2 = $phoneCountry
  phone_national_number = $phone
  adp_ascii_name_policy_approved = $true
  address_country_iso2 = $addressCountry
  address_line1 = $line1
  address_line2 = $line2
  address_line3 = $line3
  city = $city
  state_or_territory = $state
  postal_code = $postal
} | ConvertTo-Json -Depth 4

$parent = Split-Path -Parent $ProfilePath
if (-not [string]::IsNullOrWhiteSpace($parent)) {
  New-Item -ItemType Directory -Force -Path $parent | Out-Null
}
$utf8NoBom = New-Object System.Text.UTF8Encoding -ArgumentList $false
[IO.File]::WriteAllText($ProfilePath, $payload, $utf8NoBom)

Write-Host "Local private candidate profile created: $ProfilePath"
Write-Host 'The .ejs-local directory is ignored by Git. Do not commit or share this file.'
