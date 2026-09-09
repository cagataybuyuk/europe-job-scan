$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Require-Command([string]$Name) {
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "Required command '$Name' was not found. Install it before continuing."
  }
}

Require-Command git
Require-Command node
Require-Command npm
Require-Command npx
Require-Command gh

$RepoRoot = (git rev-parse --show-toplevel).Trim()
if (-not $RepoRoot) { throw 'Run this script from a clone of cagataybuyuk/europe-job-scan.' }
Set-Location $RepoRoot

$Repo = (gh repo view --json nameWithOwner --jq '.nameWithOwner').Trim()
if ($Repo -ne 'cagataybuyuk/europe-job-scan') {
  throw "Unexpected repository: $Repo"
}

gh auth status | Out-Null
npm install

Write-Host ''
Write-Host 'STEP 1/6 — Enable the Apps Script API for your Google account.' -ForegroundColor Cyan
Write-Host 'This is a free Apps Script user setting; it is not Google Cloud billing.'
Start-Process 'https://script.google.com/home/usersettings'
Read-Host 'Enable Google Apps Script API on that page, then press Enter here'

$ClaspRc = Join-Path $HOME '.clasprc.json'
if (-not (Test-Path $ClaspRc)) {
  Write-Host ''
  Write-Host 'STEP 2/6 — One-time Google OAuth for clasp.' -ForegroundColor Cyan
  Write-Host 'A browser authorization flow will open / provide a URL. Do not paste the resulting credential into chat.'
  npx clasp login --no-localhost
}
if (-not (Test-Path $ClaspRc)) { throw '.clasprc.json was not created by clasp login.' }

$ScriptId = ''
try {
  $ScriptId = (gh variable get EJS_APPS_SCRIPT_ZERO_COST_ID_TEST 2>$null).Trim()
} catch {
  $ScriptId = ''
}

if (-not $ScriptId) {
  Write-Host ''
  Write-Host 'STEP 3/6 — Creating the standalone TEST Apps Script project automatically.' -ForegroundColor Cyan
  $TempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("ejs-gd004-" + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $TempDir | Out-Null
  try {
    Push-Location $TempDir
    npx clasp create-script --title 'Europe Job Scan Control Plane - TEST' --type standalone
    $MappingPath = Join-Path $TempDir '.clasp.json'
    if (-not (Test-Path $MappingPath)) { throw 'clasp did not create .clasp.json.' }
    $Mapping = Get-Content $MappingPath -Raw | ConvertFrom-Json
    $ScriptId = [string]$Mapping.scriptId
    if (-not $ScriptId) { throw 'Script ID could not be read from .clasp.json.' }
  } finally {
    Pop-Location
    Remove-Item $TempDir -Recurse -Force -ErrorAction SilentlyContinue
  }
  $ScriptId | gh variable set EJS_APPS_SCRIPT_ZERO_COST_ID_TEST --body $ScriptId
}

Write-Host ''
Write-Host 'STEP 4/6 — Storing CI credentials directly in GitHub encrypted secrets.' -ForegroundColor Cyan
Get-Content $ClaspRc -Raw | gh secret set EJS_CLASPRC_JSON_TEST

$RandomBytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Fill($RandomBytes)
$HmacSecret = [Convert]::ToBase64String($RandomBytes).TrimEnd('=').Replace('+','-').Replace('/','_')
$HmacSecret | gh secret set EJS_GH_HMAC_SECRET_TEST

if (Get-Command Set-Clipboard -ErrorAction SilentlyContinue) {
  Set-Clipboard -Value $HmacSecret
  $ClipboardNote = 'The value has been copied to your clipboard.'
} else {
  $ClipboardNote = 'Copy the value from the temporary file shown below.'
}

$SecretFile = Join-Path ([System.IO.Path]::GetTempPath()) ("ejs-gd004-hmac-" + [guid]::NewGuid().ToString('N') + '.txt')
Set-Content -Path $SecretFile -Value $HmacSecret -NoNewline

Write-Host ''
Write-Host 'STEP 5/6 — Set the same HMAC value as one Apps Script Script Property.' -ForegroundColor Cyan
Write-Host "Script ID: $ScriptId"
Write-Host "Property key: EJS_HMAC_SHARED_SECRET_TEST"
Write-Host $ClipboardNote
if (-not (Get-Command Set-Clipboard -ErrorAction SilentlyContinue)) {
  Write-Host "Temporary secret file: $SecretFile"
}
Write-Host 'In Apps Script: Project Settings -> Script Properties -> Add script property.'
Start-Process ("https://script.google.com/d/{0}/edit" -f $ScriptId)
Read-Host 'Paste/save the property value, then press Enter here'
Remove-Item $SecretFile -Force -ErrorAction SilentlyContinue
$HmacSecret = $null
$RandomBytes = $null

$MainSha = (git ls-remote origin refs/heads/main).Split("`t")[0].Trim()
if ($MainSha -notmatch '^[0-9a-f]{40}$') { throw "Could not resolve immutable main SHA: $MainSha" }

Write-Host ''
Write-Host 'STEP 6/6 — Deploying canonical source through GitHub Actions.' -ForegroundColor Cyan
gh workflow run deploy-test-apps-script-zero-cost.yml -f expected_sha=$MainSha
Start-Sleep -Seconds 3
$DeployRun = (gh run list --workflow deploy-test-apps-script-zero-cost.yml --branch main --limit 1 --json databaseId --jq '.[0].databaseId').Trim()
if (-not $DeployRun) { throw 'Could not resolve deployment workflow run.' }
gh run watch $DeployRun --exit-status

Write-Host ''
Write-Host 'The source is now deployed. One Google runtime authorization remains.' -ForegroundColor Yellow
Write-Host 'Open the Apps Script editor, select ejsGhAuthorizationProbeV1, click Run, and approve the requested Google permission.'
Write-Host 'The function is read-only and should return hmac_secret_configured=true.'
Start-Process ("https://script.google.com/d/{0}/edit" -f $ScriptId)
Read-Host 'After ejsGhAuthorizationProbeV1 succeeds, press Enter here'

Write-Host ''
Write-Host 'Running TEST-004B/C signed runtime smoke...' -ForegroundColor Cyan
gh workflow run gd004-test-control-plane.yml -f expected_sha=$MainSha
Start-Sleep -Seconds 3
$SmokeRun = (gh run list --workflow gd004-test-control-plane.yml --branch main --limit 1 --json databaseId --jq '.[0].databaseId').Trim()
if (-not $SmokeRun) { throw 'Could not resolve control-plane smoke workflow run.' }
gh run watch $SmokeRun --exit-status

Write-Host ''
Write-Host 'GD-004 zero-cost trust bootstrap and TEST-004B/C workflow completed.' -ForegroundColor Green
Write-Host 'No Google Cloud billing project was created.'
