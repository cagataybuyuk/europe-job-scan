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
if ($Repo -ne 'cagataybuyuk/europe-job-scan') { throw "Unexpected repository: $Repo" }
gh auth status | Out-Null

$Branch = (git branch --show-current).Trim()
if ($Branch -ne 'main') { throw 'Switch to canonical main before bootstrap.' }
$LocalSha = (git rev-parse HEAD).Trim()
$RemoteSha = (git ls-remote origin refs/heads/main).Split("`t")[0].Trim()
if ($LocalSha -ne $RemoteSha) { throw 'Update local main with git pull --ff-only before bootstrap.' }
if ($RemoteSha -notmatch '^[0-9a-f]{40}$') { throw 'Could not resolve immutable main SHA.' }

npm install

Write-Host ''
Write-Host 'STEP 1/7 — Enable the Apps Script API for your Google account.' -ForegroundColor Cyan
Write-Host 'This is a free Apps Script user setting; it is not Google Cloud billing.'
Start-Process 'https://script.google.com/home/usersettings'
Read-Host 'Enable Google Apps Script API on that page, then press Enter here'

$ClaspRc = Join-Path $HOME '.clasprc.json'
if (-not (Test-Path $ClaspRc)) {
  Write-Host ''
  Write-Host 'STEP 2/7 — One-time Google OAuth for clasp.' -ForegroundColor Cyan
  Write-Host 'Complete the browser authorization yourself. Never paste the credential into chat.'
  npx clasp login --no-localhost
}
if (-not (Test-Path $ClaspRc)) { throw '.clasprc.json was not created by clasp login.' }

$ScriptId = ''
try { $ScriptId = (gh variable get EJS_APPS_SCRIPT_ZERO_COST_ID_TEST 2>$null).Trim() } catch { $ScriptId = '' }
if (-not $ScriptId) {
  Write-Host ''
  Write-Host 'STEP 3/7 — Creating standalone TEST Apps Script project automatically.' -ForegroundColor Cyan
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
  gh variable set EJS_APPS_SCRIPT_ZERO_COST_ID_TEST --body $ScriptId
}

Write-Host ''
Write-Host 'STEP 4/7 — Store CI credentials directly in GitHub encrypted secrets.' -ForegroundColor Cyan
Get-Content $ClaspRc -Raw | gh secret set EJS_CLASPRC_JSON_TEST

$RandomBytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Fill($RandomBytes)
$HmacSecret = [Convert]::ToBase64String($RandomBytes).TrimEnd('=').Replace('+','-').Replace('/','_')
$HmacSecret | gh secret set EJS_HMAC_SHARED_SECRET_TEST

$SecretFile = $null
if (Get-Command Set-Clipboard -ErrorAction SilentlyContinue) {
  Set-Clipboard -Value $HmacSecret
  $ClipboardNote = 'The HMAC value is on your clipboard.'
} else {
  $SecretFile = Join-Path ([System.IO.Path]::GetTempPath()) ("ejs-gd004-hmac-" + [guid]::NewGuid().ToString('N') + '.txt')
  Set-Content -Path $SecretFile -Value $HmacSecret -NoNewline
  $ClipboardNote = "Temporary local HMAC file: $SecretFile"
}

Write-Host ''
Write-Host 'STEP 5/7 — Set the same value as one TEST Script Property.' -ForegroundColor Cyan
Write-Host "Script ID: $ScriptId"
Write-Host 'Property key: EJS_HMAC_SHARED_SECRET_TEST'
Write-Host $ClipboardNote
Start-Process ("https://script.google.com/d/{0}/edit" -f $ScriptId)
Read-Host 'Project Settings -> Script Properties: paste/save the value, then press Enter here'
if ($SecretFile) { Remove-Item $SecretFile -Force -ErrorAction SilentlyContinue }
$HmacSecret = $null
$RandomBytes = $null
if (Get-Command Set-Clipboard -ErrorAction SilentlyContinue) { Set-Clipboard -Value '' }

Write-Host ''
Write-Host 'STEP 6/7 — Deploy exact canonical main SHA through GitHub Actions.' -ForegroundColor Cyan
gh workflow run deploy-test-apps-script-zero-cost.yml -f expected_sha=$RemoteSha
Start-Sleep -Seconds 3
$DeployRun = (gh run list --workflow deploy-test-apps-script-zero-cost.yml --branch main --limit 1 --json databaseId --jq '.[0].databaseId').Trim()
if (-not $DeployRun) { throw 'Could not resolve deployment workflow run.' }
gh run watch $DeployRun --exit-status

Write-Host ''
Write-Host 'STEP 7/7 — One-time Google runtime consent + synthetic TEST queue staging.' -ForegroundColor Cyan
Write-Host 'In the Apps Script editor run ejsGhAuthorizationProbeV1 once and approve the requested Google permissions.'
Write-Host 'It must report hmac_secret_configured=true and zero side effects.'
Write-Host 'Then run ejsGhInitializeSyntheticTestV1 once. It stages only a synthetic TEST row.'
Start-Process ("https://script.google.com/d/{0}/edit" -f $ScriptId)
Read-Host 'After both functions succeed, press Enter here'

$QueueRowText = (Read-Host 'Enter the NON-SECRET queue_row printed by ejsGhInitializeSyntheticTestV1').Trim()
[int]$QueueRow = 0
if (-not [int]::TryParse($QueueRowText, [ref]$QueueRow) -or $QueueRow -lt 2 -or $QueueRow -gt 201) {
  throw 'queue_row must be an integer between 2 and 201.'
}
$ExecutionId = (Read-Host 'Enter the NON-SECRET execution_id printed by ejsGhInitializeSyntheticTestV1').Trim()
if ($ExecutionId -notmatch '^execution:gd004:synthetic:[0-9a-f]{40}$') {
  throw 'Unexpected synthetic execution_id.'
}
if (-not $ExecutionId.EndsWith($RemoteSha)) { throw 'Initializer execution_id does not match deployed main SHA.' }

Write-Host ''
Write-Host 'Running live TEST-004B/C signed queue smoke...' -ForegroundColor Cyan
gh workflow run gd004-test-control-plane.yml -f expected_sha=$RemoteSha -f queue_row=$QueueRow -f execution_id=$ExecutionId
Start-Sleep -Seconds 3
$SmokeRun = (gh run list --workflow gd004-test-control-plane.yml --branch main --limit 1 --json databaseId --jq '.[0].databaseId').Trim()
if (-not $SmokeRun) { throw 'Could not resolve signed queue workflow run.' }
gh run watch $SmokeRun --exit-status

Write-Host ''
Write-Host 'GD-004 zero-cost owner trust bootstrap and live TEST-004B/C completed.' -ForegroundColor Green
Write-Host 'No Google Cloud billing project was created. No live employer write/upload/submit authority was enabled.'
