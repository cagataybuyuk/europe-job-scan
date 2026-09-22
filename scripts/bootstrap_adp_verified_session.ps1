param(
  [string]$Repo = 'cagataybuyuk/europe-job-scan',
  [string]$Environment = 'gd004-safe-fill-upload-canary',
  [string]$ApplicationUrl = 'https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=eae41664-19fb-4412-96f8-43f15d52332b&ccId=19000101_000001&jobId=960970&source=LR&lang=en_US',
  [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$LibPath = Join-Path $PSScriptRoot 'lib/invoke_native_utf8_stdin.ps1'
. $LibPath

$token = [Guid]::NewGuid().ToString('N')
$tempRoot = [IO.Path]::GetTempPath()
$statePath = Join-Path $tempRoot ("ejs-adp-storage-$token.json")
$reportPath = Join-Path $tempRoot ("ejs-adp-bootstrap-report-$token.json")

try {
  $python = Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1
  & $python.Source -c "import playwright" 2>$null
  if ($LASTEXITCODE -ne 0) {
    throw 'Playwright is not installed in the active Python environment. Run: python -m pip install -e ".[test]"'
  }

  Write-Host 'A browser window will open. Complete the ADP flow manually.'
  Write-Host 'Enter the email verification code only in the ADP browser, never in this terminal.'
  & $python.Source -m ejs.services.adp_verified_session_bootstrap --url $ApplicationUrl --storage-state-out $statePath --report-out $reportPath --timeout-seconds $TimeoutSeconds
  if ($LASTEXITCODE -ne 0) {
    throw "ADP verified-session bootstrap failed with exit code $LASTEXITCODE."
  }

  if (-not (Test-Path -LiteralPath $statePath)) {
    throw 'ADP verified-session bootstrap did not create storage state.'
  }

  $stateJson = [IO.File]::ReadAllText($statePath)
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  $byteCount = $utf8NoBom.GetByteCount($stateJson)
  if ($byteCount -le 2) {
    throw 'ADP verified-session storage state is empty.'
  }
  if ($byteCount -gt 47000) {
    throw "ADP verified-session state is $byteCount bytes and exceeds the reviewed GitHub secret budget."
  }
  try {
    $parsed = $stateJson | ConvertFrom-Json
  } catch {
    throw 'ADP verified-session storage state is not valid JSON.'
  }
  if ($null -eq $parsed.cookies -or $null -eq $parsed.origins) {
    throw 'ADP verified-session storage state is missing Playwright cookies/origins.'
  }

  Invoke-GhSecretSetUtf8 -SecretName 'EJS_ADP_VERIFIED_STORAGE_STATE_JSON' -Json $stateJson -Repo $Repo -Environment $Environment

  $report = if (Test-Path -LiteralPath $reportPath) {
    Get-Content -Raw -LiteralPath $reportPath | ConvertFrom-Json
  } else {
    $null
  }
  $controlCount = if ($null -ne $report) { [int]$report.visible_control_count } else { 0 }
  Write-Host "ADP verified-session secret updated successfully with byte-safe UTF-8 stdin. Post-verification visible controls: $controlCount"
} finally {
  $stateJson = $null
  $parsed = $null
  Remove-EjsTempFile -Path $statePath -Sensitive
  Remove-EjsTempFile -Path $reportPath
}
