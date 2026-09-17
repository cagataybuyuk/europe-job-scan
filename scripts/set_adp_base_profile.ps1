param(
  [string]$Repo = "cagataybuyuk/europe-job-scan",
  [string]$Environment = "gd004-safe-fill-upload-canary"
)

$ErrorActionPreference = "Stop"

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

$first = Read-ExactText "First name" "First name"
$last = Read-ExactText "Last name" "Last name"
$email = Read-ExactText "Email" "Email"

if ($email -notmatch '^[^\s@]+@[^\s@]+\.[^\s@]+$') {
  throw "Email format is invalid."
}

# Common UTF-8/Windows mojibake markers. These should never appear in a freshly
# typed Turkish name. Failing here is safer than silently storing corrupted text.
$mojibakePattern = '[ÃÄÅÂ�]'
if ($first -match $mojibakePattern -or $last -match $mojibakePattern) {
  throw "Name input looks encoding-corrupted. Re-type the name directly in this prompt."
}

$base = @{
  first_name = $first
  last_name = $last
  email = $email
} | ConvertTo-Json -Compress

try {
  gh secret set EJS_ADP_CANARY_PROFILE_JSON `
    --repo $Repo `
    --env $Environment `
    --body $base
  if ($LASTEXITCODE -ne 0) {
    throw "gh secret set failed with exit code $LASTEXITCODE"
  }
  Write-Host "ADP base profile secret updated successfully with Unicode-safe argument passing."
} finally {
  Remove-Variable base, first, last, email, mojibakePattern -ErrorAction SilentlyContinue
}
