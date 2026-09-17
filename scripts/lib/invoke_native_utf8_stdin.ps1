$ErrorActionPreference = 'Stop'

function Invoke-NativeUtf8Stdin {
  param(
    [Parameter(Mandatory = $true)][string]$FileName,
    [string]$Arguments = '',
    [Parameter(Mandatory = $true)][string]$Payload
  )

  $startInfo = New-Object System.Diagnostics.ProcessStartInfo
  $startInfo.FileName = $FileName
  $startInfo.Arguments = $Arguments
  $startInfo.UseShellExecute = $false
  $startInfo.RedirectStandardInput = $true
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true
  $startInfo.CreateNoWindow = $true

  $process = New-Object System.Diagnostics.Process
  $process.StartInfo = $startInfo
  [void]$process.Start()

  $bytes = [System.Text.Encoding]::UTF8.GetBytes($Payload)
  try {
    $process.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
    $process.StandardInput.BaseStream.Flush()
    $process.StandardInput.Close()

    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()

    return [pscustomobject]@{
      ExitCode = $process.ExitCode
      StdOut = $stdout
      StdErr = $stderr
    }
  } finally {
    if ($bytes) {
      [Array]::Clear($bytes, 0, $bytes.Length)
    }
    $process.Dispose()
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
  $arguments = "secret set $SecretName --repo $Repo --env $Environment"
  $result = Invoke-NativeUtf8Stdin -FileName $gh.Source -Arguments $arguments -Payload $Json
  if ($result.ExitCode -ne 0) {
    $message = ($result.StdErr | Out-String).Trim()
    if (-not $message) { $message = 'no stderr returned' }
    throw "gh secret set failed with exit code $($result.ExitCode): $message"
  }
}
