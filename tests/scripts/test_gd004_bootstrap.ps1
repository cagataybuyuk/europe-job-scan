$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$SourcePath = Join-Path $PSScriptRoot '../../scripts/gd004/bootstrap_zero_cost_user_trust.ps1'
$Tokens = $null
$Errors = $null
$Ast = [System.Management.Automation.Language.Parser]::ParseFile($SourcePath, [ref]$Tokens, [ref]$Errors)
if ($Errors.Count) { throw 'Bootstrap syntax errors' }

$AdpHelperRelativePaths = @(
  '../../scripts/set_adp_base_profile.ps1',
  '../../scripts/set_adp_profile_v2_extension.ps1',
  '../../scripts/bootstrap_adp_verified_session.ps1',
  '../../scripts/probe_adp_persistent_profile.ps1',
  '../../scripts/lib/invoke_native_utf8_stdin.ps1'
)
foreach ($RelativePath in $AdpHelperRelativePaths) {
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

$VerifiedSessionHelperText = Get-Content -Raw (Join-Path $PSScriptRoot '../../scripts/bootstrap_adp_verified_session.ps1')
foreach ($RequiredSnippet in @(
  'function Resolve-EjsPython3',
  "@{ Name = 'py'; PrefixArgs = @('-3') }",
  "@{ Name = 'python3'; PrefixArgs = @() }",
  "@{ Name = 'python'; PrefixArgs = @() }",
  'winget install -e --id Python.Python.3.12',
  '& $python.Source @pythonPrefixArgs -m ejs.services.adp_verified_session_bootstrap'
)) {
  if ($VerifiedSessionHelperText -notlike ('*' + $RequiredSnippet + '*')) {
    throw "Verified-session helper is missing launcher contract: $RequiredSnippet"
  }
}
if ($VerifiedSessionHelperText -match 'Get-Command python -CommandType Application -ErrorAction Stop') {
  throw 'Verified-session helper must not hard-bind to the Windows Store python alias'
}
Write-Host 'PASS: ADP verified-session helper resolves a real Python 3 launcher fail-closed'

$PersistentProbeHelperText = Get-Content -Raw (Join-Path $PSScriptRoot '../../scripts/probe_adp_persistent_profile.ps1')
foreach ($RequiredSnippet in @(
  'ejs.services.adp_persistent_profile_probe',
  '--user-data-dir $profilePath',
  'No GitHub secret was changed'
)) {
  if ($PersistentProbeHelperText -notlike ('*' + $RequiredSnippet + '*')) {
    throw "Persistent-profile helper is missing diagnostic contract: $RequiredSnippet"
  }
}
if ($PersistentProbeHelperText -match 'Invoke-GhSecretSetUtf8|gh secret') {
  throw 'Persistent-profile diagnostic must never provision GitHub secrets'
}
Write-Host 'PASS: ADP persistent-profile helper is diagnostic-only and secret-write-free'

# Execute the actual helper body with a fake native Python boundary. A failed
# local replay must never call the secret writer; all temporary paths are cleaned.
& {
  $HelperAst = [System.Management.Automation.Language.Parser]::ParseInput($VerifiedSessionHelperText, [ref]$null, [ref]$null)
  $TryStatement = $HelperAst.EndBlock.Statements | Where-Object { $_ -is [System.Management.Automation.Language.TryStatementAst] } | Select-Object -Last 1
  $HelperBody = [scriptblock]::Create($TryStatement.Extent.Text)
  function Resolve-EjsPython3 {
    return [pscustomobject]@{ Source = 'Invoke-FakeAdpPython'; PrefixArgs = @(); DisplayName = 'fake python' }
  }
  function Invoke-FakeAdpPython {
    $global:LASTEXITCODE = 0
    if ($args -contains '-c') { return }
    if ($args -contains 'ejs.services.adp_verified_session_bootstrap') {
      if ($script:AdpTestMode -eq 'bootstrap-failure') { $global:LASTEXITCODE = 2; return }
      [IO.File]::WriteAllText($statePath, '{"cookies":[],"origins":[]}')
      [IO.File]::WriteAllText($postLoginUrlPath, 'https://workforcenow.adp.com/mascsr/applicant/mdf/recruitment/postLogin.html?cid=test&ccId=test&jobId=test')
      if ($script:AdpTestMode -in @('session-replay-required', 'cookie-session-replay-required')) {
        [IO.File]::WriteAllText($sessionStoragePath, '{"adp-session":"opaque-value"}')
        [IO.File]::WriteAllText($reportPath, '{"visible_control_count":3,"session_storage_entry_count":1}')
      } else {
        [IO.File]::WriteAllText($reportPath, '{"visible_control_count":3}')
      }
      return
    }
    if ($args -contains 'ejs.services.adp_verified_session_inspector') {
      if ($args -notcontains '--playwright-managed') { throw 'Local replay must use Playwright-managed fallback' }
      if ($args -notcontains '--direct-reuse-url-file') { throw 'Local replay must receive captured canonical postLogin URL' }
      if ($script:AdpTestMode -eq 'replay-failure') { $global:LASTEXITCODE = 2; return }
      if ($script:AdpTestMode -in @('session-replay-required', 'cookie-session-replay-required')) {
        if ($args -contains '--session-storage-json') {
          [IO.File]::WriteAllText($sessionReuseReportPath, '{"inspector_status":"inspected","session_reused":true}')
          $global:LASTEXITCODE = 0
        } else {
          $ErrorCode = if ($script:AdpTestMode -eq 'cookie-session-replay-required') {
            'ADP_VERIFIED_SESSION_COOKIE_STATE_NOT_REUSED'
          } else {
            'ADP_VERIFIED_SESSION_NOT_RECOGNIZED_IDENTITY_SURFACE'
          }
          [IO.File]::WriteAllText($reuseReportPath, ('{"inspector_status":"blocked","error_code":"' + $ErrorCode + '","session_reused":false}'))
          $global:LASTEXITCODE = 2
        }
        return
      }
      $ReportJson = if ($script:AdpTestMode -eq 'false-success') {
        '{"inspector_status":"blocked","session_reused":false}'
      } elseif ($script:AdpTestMode -eq 'canonical-url-success') {
        '{"inspector_status":"inspected","session_reused":true,"reuse_route":"authenticated_postlogin_direct","direct_reuse_url_evidence":{"direct_reuse_url_source":"captured_post_verification"}}'
      } else { '{"inspector_status":"inspected","session_reused":true}' }
      [IO.File]::WriteAllText($reuseReportPath, $ReportJson)
      return
    }
    throw 'Unexpected Python command'
  }
  function Invoke-GhSecretSetUtf8 { $script:AdpTestSecretWrites += 1 }
  function Remove-EjsTempFile { param($Path, [switch]$Sensitive) Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue }
  $Repo = 'test/repo'
  $Environment = 'test'
  $ApplicationUrl = 'https://workforcenow.adp.com/test'
  $ExpectedNavigationSurfaceFingerprint = 'a' * 64
  $EntryOrdinal = 0
  $TimeoutSeconds = 60
  foreach ($Mode in @('bootstrap-failure', 'replay-failure', 'false-success', 'session-replay-required', 'cookie-session-replay-required', 'canonical-url-success', 'success')) {
    $script:AdpTestMode = $Mode
    $script:AdpTestSecretWrites = 0
    $Prefix = Join-Path ([IO.Path]::GetTempPath()) ('ejs-adp-test-' + [Guid]::NewGuid().ToString('N'))
    $statePath = $Prefix + '-state.json'
    $reportPath = $Prefix + '-bootstrap.json'
    $reuseReportPath = $Prefix + '-reuse.json'
    $Caught = $false
    try { & $HelperBody } catch {
      $Caught = $true
      if ($Mode -eq 'success') { throw }
      if ($_.Exception.Message -notmatch 'bootstrap failed|not reusable|not proven|sessionStorage|canonical postLogin URL') { throw }
    }
    if ($Mode -ne 'success' -and -not $Caught) { throw "Failed open in $Mode" }
    $ExpectedWrites = if ($Mode -eq 'success') { 1 } else { 0 }
    if ($script:AdpTestSecretWrites -ne $ExpectedWrites) { throw "Incorrect secret write count for $Mode" }
    $sessionStoragePath = "$statePath.session-storage.json"
    $sessionReuseReportPath = "$reuseReportPath.session-storage.json"
    $postLoginUrlPath = "$statePath.postlogin-url.txt"
    foreach ($Path in @($statePath, $sessionStoragePath, $postLoginUrlPath, $reportPath, $reuseReportPath, $sessionReuseReportPath)) {
      if (Test-Path -LiteralPath $Path) { throw "Temporary file not cleaned in $Mode" }
    }
  }
  $global:LASTEXITCODE = 0
}
Write-Host 'PASS: local ADP replay gates secret provisioning and cleans temporary files'

foreach ($RelativePath in @(
  '../../scripts/set_adp_base_profile.ps1',
  '../../scripts/set_adp_profile_v2_extension.ps1',
  '../../scripts/bootstrap_adp_verified_session.ps1'
)) {
  $HelperText = Get-Content -Raw (Join-Path $PSScriptRoot $RelativePath)
  if ($HelperText -match '--body') { throw "$RelativePath must not pass JSON through --body" }
  if ($HelperText -notmatch 'Invoke-GhSecretSetUtf8') { throw "$RelativePath must use UTF-8 stdin transport" }
}

$Utf8HelperPath = Join-Path $PSScriptRoot '../../scripts/lib/invoke_native_utf8_stdin.ps1'
. $Utf8HelperPath

foreach ($AllowedSecretName in @(
  'EJS_ADP_CANARY_PROFILE_JSON',
  'EJS_ADP_CANARY_PROFILE_V2_EXTENSION_JSON',
  'EJS_ADP_VERIFIED_STORAGE_STATE_JSON'
)) {
  Assert-EjsAdpSecretNameAllowed -SecretName $AllowedSecretName
}
$RejectedSecretName = $false
try {
  Assert-EjsAdpSecretNameAllowed -SecretName 'EJS_UNREVIEWED_SECRET'
} catch {
  $RejectedSecretName = $_.Exception.Message -match 'reviewed ADP allowlist'
}
if (-not $RejectedSecretName) { throw 'Unreviewed ADP secret name was accepted' }
Write-Host 'PASS: reviewed ADP secret names are exact-allowlisted'

$EchoSource = @'
using System;
using System.IO;
public static class StdinEcho {
  public static int Main() {
    Stream input = Console.OpenStandardInput();
    MemoryStream output = new MemoryStream();
    input.CopyTo(output);
    Console.Write(Convert.ToBase64String(output.ToArray()));
    return 0;
  }
}
'@
$EchoExe = Join-Path $env:RUNNER_TEMP ('ejs-stdin-echo-' + [Guid]::NewGuid().ToString('N') + '.exe')
try {
  Add-Type -TypeDefinition $EchoSource -OutputAssembly $EchoExe -OutputType ConsoleApplication
  $Payload = '{"first_name":"' + [char]0x00C7 + 'a' + [char]0x011F + 'atay","last_name":"B' + [char]0x00FC + 'y' + [char]0x00FC + 'k"}'
  $ExpectedBytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($Payload)
  $ExpectedBase64 = [Convert]::ToBase64String($ExpectedBytes)
  $RoundTrip = Invoke-NativeUtf8Stdin -FileName $EchoExe -Payload $Payload
  if ($RoundTrip.ExitCode -ne 0) { throw 'UTF-8 stdin echo process failed' }
  if ($RoundTrip.Output.Trim() -ne $ExpectedBase64) { throw 'UTF-8 stdin bytes changed in transport' }
  [Array]::Clear($ExpectedBytes, 0, $ExpectedBytes.Length)
} finally {
  Remove-Item $EchoExe -Force -ErrorAction SilentlyContinue
}
Write-Host 'PASS: ADP secret JSON reaches native stdin as exact UTF-8 without BOM'

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

Set-Clipboard -Value 'bootstrap-test-placeholder'
Clear-BootstrapClipboard
if ((Get-Clipboard -Raw) -ne ' ') { throw 'Clipboard was not overwritten' }

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
