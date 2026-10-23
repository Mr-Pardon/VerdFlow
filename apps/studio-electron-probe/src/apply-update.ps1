param([Parameter(Mandatory = $true)][string]$PlanPath)
$ErrorActionPreference = 'Stop'
$plan = Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
$id = [string]$plan.id
if ($id -notmatch '^[a-f0-9]{32}$') { throw 'Invalid update identity' }
$install = [IO.Path]::GetFullPath([string]$plan.installRoot).TrimEnd('\')
$parent = [IO.Path]::GetDirectoryName($install)
$stage = [IO.Path]::GetFullPath([string]$plan.stageRoot).TrimEnd('\')
$backup = Join-Path $parent ".verdflow-backup-$id"
if (!$parent -or $stage -ne (Join-Path $parent ".verdflow-update-$id")) { throw 'Invalid update paths' }
foreach ($directory in @($install, $stage)) {
    $item = Get-Item -LiteralPath $directory
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked update directory' }
}
$marker = Join-Path $stage '.verdflow-update-ready.json'
$ready = Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json
if ($ready.id -ne $id -or $ready.version -ne $plan.targetVersion) { throw 'Update is not prepared' }
if (Test-Path -LiteralPath $backup) { throw 'Backup path already exists' }
$resultPath = Join-Path ([IO.Path]::GetDirectoryName($PlanPath)) 'result.json'
$movedOld = $false
$movedNew = $false
try {
    $owner = Get-Process -Id ([int]$plan.parentPid) -ErrorAction SilentlyContinue
    if (!$owner) { throw 'Application exited before update handoff' }
    try {
        [IO.File]::WriteAllText((Join-Path ([IO.Path]::GetDirectoryName($PlanPath)) 'helper-ready'), [string]$owner.Id)
        if (!$owner.WaitForExit(60000)) { throw 'Application has not exited' }
    } finally { $owner.Dispose() }
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while (!$movedOld) {
        try { Move-Item -LiteralPath $install -Destination $backup; $movedOld = $true }
        catch { if ([DateTime]::UtcNow -ge $deadline) { throw }; Start-Sleep -Milliseconds 250 }
    }
    Move-Item -LiteralPath $stage -Destination $install
    $movedNew = $true
    Remove-Item -LiteralPath (Join-Path $install '.verdflow-update-ready.json') -ErrorAction SilentlyContinue
    [IO.File]::WriteAllText($resultPath, (@{ status = 'applied'; version = $plan.targetVersion } | ConvertTo-Json))
} catch {
    if ($movedOld -and !$movedNew -and !(Test-Path -LiteralPath $install)) {
        Move-Item -LiteralPath $backup -Destination $install
    }
    [IO.File]::WriteAllText($resultPath, (@{ status = 'failed'; message = $_.Exception.Message } | ConvertTo-Json))
    if (!$movedNew -and (Test-Path -LiteralPath (Join-Path $install 'VerdFlow Studio.exe'))) {
        Start-Process -FilePath (Join-Path $install 'VerdFlow Studio.exe') -WorkingDirectory $install
    }
    throw
}
# User data and projects are outside this program-directory swap. Keep the backup;
# restoring program files after a newer database has opened requires a separate decision.
Start-Process -FilePath (Join-Path $install 'VerdFlow Studio.exe') -WorkingDirectory $install
