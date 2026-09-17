$ErrorActionPreference = 'Stop'

function Invoke-NativeUtf8Stdin {
  param(
    [Parameter(Mandatory = $true)][string]$FileName,
    [string[]]$ArgumentList = @(),
    [Parameter(Mandatory = $true)][string]$Payload
  )

  $previousOutputEncoding = $OutputEncoding
  try {
    # Windows PowerShell 5.1 encodes text sent to a native process using
    # $OutputEncoding. Set it explicitly to UTF-8 without a BOM so JSON stays
    # off the command line while quotes and non-ASCII identity text survive.
    $OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $nativeOutput = @($Payload | & $FileName @ArgumentList 2>&1)
    $exitCode = $LASTEXITCODE
    $outputText = ($nativeOutput | ForEach-Object { [string]$_ }) -join [Environment]::NewLine

    return [pscustomobject]@{
      ExitCode = $exitCode
      Output = $outputText
    }
  } finally {
    $OutputEncoding = $previousOutputEncoding
  }
}

function Invoke-GhSecretSetUtf8 {
  param(
    [Parameter(Mandatory = $true)][string]$SecretName,
    [Parameter(Mandatory = $true)][string]$Json,
    [Parameter(Mandatory = $true)][string]$Repo,
    [Parameter(Mandatory = $true)][string]$Environment
  )

  if ($SecretName -notmatch '^[A-Z0-9_]+$') {
    throw 'Secret name contains unsupported characters.'
  }
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
