$ErrorActionPreference = 'Stop'

function Remove-EjsTempFile {
  param(
    [string]$Path,
    [switch]$Sensitive
  )
  if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return }
  if ($Sensitive) {
    try {
      $length = (Get-Item -LiteralPath $Path).Length
      if ($length -gt 0) {
        [IO.File]::WriteAllBytes($Path, (New-Object byte[] $length))
      }
    } catch {
      # Deletion is still required even when best-effort overwrite is blocked.
    }
  }
  Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
}

function Invoke-NativeUtf8Stdin {
  param(
    [Parameter(Mandatory = $true)][string]$FileName,
    [string[]]$ArgumentList = @(),
    [Parameter(Mandatory = $true)][string]$Payload
  )

  $token = [Guid]::NewGuid().ToString('N')
  $tempRoot = [IO.Path]::GetTempPath()
  $stdinPath = Join-Path $tempRoot ("ejs-stdin-$token.tmp")
  $stdoutPath = Join-Path $tempRoot ("ejs-stdout-$token.tmp")
  $stderrPath = Join-Path $tempRoot ("ejs-stderr-$token.tmp")
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)

  try {
    [IO.File]::WriteAllText($stdinPath, $Payload, $utf8NoBom)
    $startParams = @{
      FilePath = $FileName
      RedirectStandardInput = $stdinPath
      RedirectStandardOutput = $stdoutPath
      RedirectStandardError = $stderrPath
      NoNewWindow = $true
      Wait = $true
      PassThru = $true
    }
    if ($ArgumentList -and $ArgumentList.Count -gt 0) {
      $startParams.ArgumentList = $ArgumentList
    }
    $process = Start-Process @startParams

    $stdout = if (Test-Path -LiteralPath $stdoutPath) { [IO.File]::ReadAllText($stdoutPath) } else { '' }
    $stderr = if (Test-Path -LiteralPath $stderrPath) { [IO.File]::ReadAllText($stderrPath) } else { '' }
    $combined = @($stdout.Trim(), $stderr.Trim()) | Where-Object { $_ } | ForEach-Object { [string]$_ }

    return [pscustomobject]@{
      ExitCode = $process.ExitCode
      Output = ($combined -join [Environment]::NewLine)
    }
  } finally {
    Remove-EjsTempFile -Path $stdinPath -Sensitive
    Remove-EjsTempFile -Path $stdoutPath
    Remove-EjsTempFile -Path $stderrPath
  }
}

function Assert-EjsAdpSecretNameAllowed {
  param(
    [Parameter(Mandatory = $true)][string]$SecretName
  )

  $allowedSecretNames = @(
    'EJS_ADP_CANARY_PROFILE_JSON',
    'EJS_ADP_CANARY_PROFILE_V2_EXTENSION_JSON',
    'EJS_ADP_VERIFIED_STORAGE_STATE_JSON'
  )
  if ($allowedSecretNames -notcontains $SecretName) {
    throw 'Secret name is not in the reviewed ADP allowlist.'
  }
}

function Invoke-GhSecretSetUtf8 {
  param(
    [Parameter(Mandatory = $true)][string]$SecretName,
    [Parameter(Mandatory = $true)][string]$Json,
    [Parameter(Mandatory = $true)][string]$Repo,
    [Parameter(Mandatory = $true)][string]$Environment
  )

  Assert-EjsAdpSecretNameAllowed -SecretName $SecretName
  if ($Repo -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    throw 'Repository must be in owner/name form using safe characters only.'
  }
  if ($Environment -notmatch '^[A-Za-z0-9_.-]+$') {
    throw 'Environment contains unsupported characters.'
  }

  $gh = Get-Command gh -CommandType Application -ErrorAction Stop | Select-Object -First 1
  $arguments = @('secret', 'set', $SecretName, '--repo', $Repo, '--env', $Environment)
  $result = Invoke-NativeUtf8Stdin -FileName $gh.Source -ArgumentList $arguments -Payload $Json
  if ($result.ExitCode -ne 0) {
    $message = $result.Output.Trim()
    if (-not $message) { $message = 'no diagnostic output returned' }
    throw "gh secret set failed with exit code $($result.ExitCode): $message"
  }
}
