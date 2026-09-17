$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$SourcePath = Join-Path $PSScriptRoot '../../scripts/gd004/bootstrap_zero_cost_user_trust.ps1'
$Tokens = $null
$Errors = $null
$Ast = [System.Management.Automation.Language.Parser]::ParseFile($SourcePath, [ref]$Tokens, [ref]$Errors)
if ($Errors.Count) { throw 'Bootstrap syntax errors' }

# Parse every user-facing ADP PowerShell helper with the actual Windows
# PowerShell 5.1 parser used by this workflow. This catches UTF-8/no-BOM source
# text that can parse in pwsh but break in powershell.exe.
foreach ($RelativePath in @(
  '../../scripts/set_adp_base_profile.ps1',
  '../../scripts/set_adp_profile_v2_extension.ps1'
)) {
  $HelperPath = Join-Path $PSScriptRoot $RelativePath
  $HelperTokens = $null
  $HelperErrors = $null
  [void][System.Management.Automation.Language.Parser]::ParseFile(
    $HelperPath,
    [ref]$HelperTokens,
    [ref]$HelperErrors
  )
  if ($HelperErrors.Count) {
    $Messages = ($HelperErrors | ForEach-Object { $_.Message }) -join '; '
    throw "PowerShell 5.1 syntax errors in $RelativePath : $Messages"
  }
}
Write-Host 'PASS: ADP user-facing helpers parse on Windows PowerShell 5.1'

# Load the actual pure helpers without running OAuth or changing accounts.
foreach ($Name in @('Assert-NativeSuccess', 'New-HmacSecret', 'Clear-BootstrapClipboard')) {
  $Function = $Ast.Find({ param($Node) $Node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $Node.Name -eq $Name }, $true)
  if (-not $Function) { throw "Missing helper $Name" }
  . ([scriptblock]::Create($Function.Extent.Text))
}
$One = New-HmacSecret
$Two = New-HmacSecret
if ($One -notmatch '^[A-Za-z0-9_-]{43}$' -or $One -eq $Two) { throw 'Invalid HMAC generation' }
$Bytes = [Convert]::FromBase64String($One.Replace('-','+').Replace('_','/') + '=')
if ($Bytes.Length -ne 32) { throw 'Expected 256-bit HMAC' }
$One = $null
$Two = $null
[Array]::Clear($Bytes, 0, $Bytes.Length)
& cmd.exe /c 'exit 0'
Assert-NativeSuccess 'successful command'
& cmd.exe /c 'exit 7'
$Rejected = $false
try { Assert-NativeSuccess 'failed command' } catch { $Rejected = $_.Exception.Message -match 'exit code 7' }
if (-not $Rejected) { throw 'Native failure was accepted' }
& cmd.exe /c 'exit 0'
Write-Host 'PASS: Windows PowerShell HMAC generation and native failure handling'

# Exercise the real Windows clipboard API with harmless data, including cleanup.
Set-Clipboard -Value 'bootstrap-test-placeholder'
Clear-BootstrapClipboard
if ((Get-Clipboard -Raw) -ne ' ') { throw 'Clipboard was not overwritten' }

# Resume must bypass project creation, OAuth, HMAC rotation and the saved-property prompt.
# Stop at the deployment dispatch boundary using a failed native command double.
function git {
  $global:LASTEXITCODE = 0
  if ($args[0] -eq 'rev-parse' -and $args[1] -eq '--show-toplevel') { return (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path }
  if ($args[0] -eq 'branch') { return 'main' }
  if ($args[0] -eq 'ls-remote') { return (('a' * 40) + "`trefs/heads/main") }
  return ('a' * 40)
}
function npm { $global:LASTEXITCODE = 0 }
function npx { throw 'Resume must not invoke clasp' }
function gh {
  $global:LASTEXITCODE = 0
  if ($args[0] -eq 'repo') { return 'cagataybuyuk/europe-job-scan' }
  if ($args[0] -eq 'auth') { return }
  if ($args[0] -eq 'variable' -and $args[1] -eq 'get') { return 'existing-test-script' }
  if ($args[0] -eq 'workflow' -and $args[1] -eq 'run') { $global:LASTEXITCODE = 23; return }
  throw 'Unexpected GitHub operation during resume'
}
function Read-Host { throw 'Resume must not ask for trust setup again' }
function Start-Process { throw 'Resume must not open OAuth or project settings' }
$StoppedAtDispatch = $false
try { & $SourcePath -ResumeAfterTrust } catch {
  $StoppedAtDispatch = $_.Exception.Message -match 'deployment dispatch failed \(exit code 23\)'
}
if (-not $StoppedAtDispatch) { throw 'Resume did not reach and stop at the checked deployment boundary' }
$global:LASTEXITCODE = 0
Write-Host 'PASS: real Windows clipboard cleanup and resume without changing trust'
