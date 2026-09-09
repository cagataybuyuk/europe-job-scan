param(
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^[A-Za-z0-9_-]{20,}$')]
    [string]$ScriptId
)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
foreach ($EjsCommand in @('git', 'node', 'npm', 'npx', 'gh')) {
    if (-not (Get-Command $EjsCommand -ErrorAction SilentlyContinue)) {
        throw "Install $EjsCommand before running this bootstrap."
    }
}
gh auth status
if ($LASTEXITCODE -ne 0) { throw 'Run gh auth login first.' }
$EjsBranch = git branch --show-current
if ($EjsBranch -ne 'main') { throw 'Use the canonical main branch.' }
$EjsSha = git rev-parse HEAD
$EjsRemoteSha = gh api repos/cagataybuyuk/europe-job-scan/commits/main --jq '.sha'
if ($LASTEXITCODE -ne 0 -or $EjsSha -ne $EjsRemoteSha) {
    throw 'Update local main with git pull --ff-only first.'
}
npm install
if ($LASTEXITCODE -ne 0) { throw 'npm install failed.' }
npx clasp login
if ($LASTEXITCODE -ne 0) { throw 'Complete the owner Google OAuth flow.' }
$EjsClaspPath = Join-Path $env:USERPROFILE '.clasprc.json'
if (-not (Test-Path $EjsClaspPath)) { throw 'clasp credential file was not created.' }
Get-Content $EjsClaspPath -Raw | gh secret set EJS_CLASPRC_JSON_TEST --repo cagataybuyuk/europe-job-scan
if ($LASTEXITCODE -ne 0) { throw 'Could not store clasp credential in GitHub.' }
gh variable set EJS_APPS_SCRIPT_ZERO_COST_ID_TEST --body $ScriptId --repo cagataybuyuk/europe-job-scan
if ($LASTEXITCODE -ne 0) { throw 'Could not store TEST Script ID.' }
$EjsBytes = New-Object byte[] 32
$EjsRng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try { $EjsRng.GetBytes($EjsBytes) } finally { $EjsRng.Dispose() }
$EjsSecret = [BitConverter]::ToString($EjsBytes).Replace('-', '').ToLowerInvariant()
$EjsSecret | gh secret set EJS_HMAC_SHARED_SECRET_TEST --repo cagataybuyuk/europe-job-scan
if ($LASTEXITCODE -ne 0) { throw 'Could not store HMAC secret in GitHub.' }
Set-Clipboard -Value $EjsSecret
$EjsSecret = $null
[Array]::Clear($EjsBytes, 0, $EjsBytes.Length)
Write-Host 'Paste the clipboard into Apps Script property EJS_HMAC_SHARED_SECRET_TEST, save, then clear clipboard.'
gh workflow run deploy-test-apps-script-zero-cost.yml -f "expected_sha=$EjsSha" --repo cagataybuyuk/europe-job-scan
if ($LASTEXITCODE -ne 0) { throw 'TEST deployment dispatch failed.' }
Write-Host 'After deployment, run ejsGhInitializeSyntheticTestV1 in the editor and publish the Web App. See docs/GD004_ZERO_COST_APPS_SCRIPT_BOOTSTRAP.md.'
