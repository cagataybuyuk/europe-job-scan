$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$SourcePath = Join-Path $PSScriptRoot '../../scripts/gd004/bootstrap_zero_cost_user_trust.ps1'
$Tokens = $null
$Errors = $null
$Ast = [System.Management.Automation.Language.Parser]::ParseFile($SourcePath, [ref]$Tokens, [ref]$Errors)
if ($Errors.Count) { throw 'Bootstrap syntax errors' }
# Load the actual pure helpers without running OAuth or changing accounts.
foreach ($Name in @('Assert-NativeSuccess', 'New-HmacSecret')) {
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
