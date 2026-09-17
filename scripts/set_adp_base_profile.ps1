param(
  [string]$Repo = "cagataybuyuk/europe-job-scan",
  [string]$Environment = "gd004-safe-fill-upload-canary"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot 'lib/invoke_native_utf8_stdin.ps1')

function Read-ExactText([string]$Prompt, [string]$FieldName) {
  $value = Read-Host $Prompt
  if ([string]::IsNullOrWhiteSpace($value)) {
    throw "$FieldName cannot be empty."
  }
  if ($value -ne $value.Trim()) {
    throw "$FieldName cannot contain leading or trailing whitespace."
  }
  return $value.Normalize([Text.NormalizationForm]::FormC)
}

function Test-ContainsMojibakeMarker([string]$Value) {
  $markerCodePoints = @(0x00C2, 0x00C3, 0x00C4, 0x00C5, 0xFFFD)
  foreach ($character in $Value.ToCharArray()) {
    if ($markerCodePoints -contains [int][char]$character) {
      return $true
    }
  }
  return $false
}

$first = Read-ExactText "First name" "First name"
$last = Read-ExactText "Last name" "Last name"
$email = Read-ExactText "Email" "Email"

if ($email -notmatch '^[^\s@]+@[^\s@]+\.[^\s@]+$') {
  throw "Email format is invalid."
}

if ((Test-ContainsMojibakeMarker $first) -or (Test-ContainsMojibakeMarker $last)) {
  throw "Name input looks encoding-corrupted. Re-type the name directly in this prompt."
}

$base = @{
  first_name = $first
  last_name = $last
  email = $email
} | ConvertTo-Json -Compress

try {
  Invoke-GhSecretSetUtf8 `
    -SecretName 'EJS_ADP_CANARY_PROFILE_JSON' `
    -Json $base `
    -Repo $Repo `
    -Environment $Environment
  Write-Host "ADP base profile secret updated successfully with byte-safe UTF-8 stdin."
} finally {
  Remove-Variable base, first, last, email -ErrorAction SilentlyContinue
}
