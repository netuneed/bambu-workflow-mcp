[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$WheelPath,
    [string]$Python = 'python',
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'BambuWorkflow\app'),
    [string]$ConfigPath,
    [string]$Version,
    [string]$Wheelhouse,
    [string]$SkillRoot,
    [switch]$InstallSkill,
    [switch]$Resume,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$bundleRoot = Split-Path -Parent $PSScriptRoot
$packageName = 'bambu-workflow-mcp'
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Get-AbsolutePath([string]$PathValue) {
    return [System.IO.Path]::GetFullPath($PathValue)
}

function Assert-ChildPath([string]$ParentPath, [string]$ChildPath) {
    $parentAbsolute = (Get-AbsolutePath $ParentPath).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    $childAbsolute = Get-AbsolutePath $ChildPath
    if (-not $childAbsolute.StartsWith($parentAbsolute, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing a path outside the intended installation root."
    }
}

function Invoke-Python([string]$ExecutablePath, [string[]]$CommandArguments) {
    & $ExecutablePath @CommandArguments
    if ($LASTEXITCODE -ne 0) { throw "Python operation failed with exit code $LASTEXITCODE." }
}

function Write-JsonFile([string]$PathValue, $Value) {
    [System.IO.File]::WriteAllText($PathValue, ($Value | ConvertTo-Json -Depth 10), $utf8)
}

function Get-RuntimeLocations([string]$ExecutablePath, [string]$RootPath, [string]$DirectoryPath) {
    if (-not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
        throw 'Runtime Python is missing; physical paths cannot be checked automatically. No existing files were removed.'
    }
    $code = 'import json,os,sys; print(json.dumps({"python":os.path.realpath(sys.executable),"root":os.path.realpath(sys.argv[1]),"directory":os.path.realpath(sys.argv[2])}))'
    $result = & $ExecutablePath -I -c $code $RootPath $DirectoryPath
    if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve the physical runtime location. Existing files were preserved.' }
    return ($result | ConvertFrom-Json)
}

function Get-InstalledInfo([string]$ExecutablePath) {
    # Schema inspection is offline and does not call server.main() or recover workers.
    $code = 'import asyncio,json,os,sys; from bambu_workflow import __version__; from bambu_workflow.server import mcp; print(json.dumps({"version":__version__,"python":os.path.realpath(sys.executable),"tool_count":len(asyncio.run(mcp.list_tools()))}))'
    $result = & $ExecutablePath -I -c $code
    if ($LASTEXITCODE -ne 0) { throw 'Installed runtime verification failed. Existing files were preserved.' }
    return ($result | ConvertFrom-Json)
}

$rootAbsolute = Get-AbsolutePath $InstallRoot
if ($Uninstall) {
    if (-not $Version) { throw 'Uninstall requires an explicit -Version.' }
} else {
    if (-not $WheelPath) {
        $wheelDirectory = Join-Path $bundleRoot 'wheel'
        if (-not (Test-Path -LiteralPath $wheelDirectory -PathType Container)) {
            throw 'Supply -WheelPath, or run this installer from the extracted release bundle.'
        }
        $candidates = @(Get-ChildItem -LiteralPath $wheelDirectory -Filter 'bambu_workflow_mcp-*.whl' -File)
        if ($candidates.Count -ne 1) { throw 'The release bundle must contain exactly one project wheel.' }
        $WheelPath = $candidates[0].FullName
    }
    $WheelPath = Get-AbsolutePath $WheelPath
    if (-not (Test-Path -LiteralPath $WheelPath -PathType Leaf)) { throw 'The project wheel does not exist.' }
    $wheelName = [System.IO.Path]::GetFileName($WheelPath)
    if ($wheelName -notmatch '^bambu_workflow_mcp-([0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.+]*)-[^-]+-[^-]+-[^-]+\.whl$') {
        throw 'Wheel filename does not identify a Bambu Workflow MCP release.'
    }
    $wheelVersion = $Matches[1]
    if ($Version -and $Version -ne $wheelVersion) { throw '-Version differs from the wheel version.' }
    $Version = $wheelVersion
}
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.+-]*$' -or $Version.Contains('..')) {
    throw 'Version must be a single safe version identifier.'
}
$versionDirectory = Get-AbsolutePath (Join-Path $rootAbsolute $Version)
Assert-ChildPath $rootAbsolute $versionDirectory
if (Test-Path -LiteralPath $versionDirectory) {
    $targetItem = Get-Item -LiteralPath $versionDirectory -Force
    if ($targetItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        throw 'Refusing to install into or remove a version directory that is a junction/symbolic link.'
    }
}
$markerPath = Join-Path $versionDirectory '.bambu-workflow-runtime.json'
$runtimePython = Join-Path $versionDirectory '.venv\Scripts\python.exe'

if ($Uninstall) {
    if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) { throw 'Refusing to remove a directory without the runtime ownership marker.' }
    $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
    if ($marker.package -ne $packageName -or $marker.version -ne $Version) { throw 'The runtime ownership marker does not match the requested package/version.' }
    # Windows/MSIX can redirect an AppData write. Resolve BOTH the intended root
    # and the target, then recheck their actual relationship before any removal.
    $physical = Get-RuntimeLocations $runtimePython $rootAbsolute $versionDirectory
    Assert-ChildPath $physical.root $physical.directory
    $rootAbsolute = Get-AbsolutePath $physical.root
    $versionDirectory = Get-AbsolutePath $physical.directory
    $runtimePython = Get-AbsolutePath $physical.python
    $running = @(Get-CimInstance Win32_Process | Where-Object {
        ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($versionDirectory + '\', [System.StringComparison]::OrdinalIgnoreCase)) -or
        ($_.CommandLine -and ($_.CommandLine.IndexOf($versionDirectory + '\', [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                            $_.CommandLine.IndexOf($versionDirectory.Replace('\', '/') + '/', [System.StringComparison]::OrdinalIgnoreCase) -ge 0))
    })
    if ($running.Count -gt 0) { throw 'This runtime still has active processes. Stop its MCP/worker normally before uninstalling.' }
    $codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
    $codexConfig = Join-Path $codexHome 'config.toml'
    if (Test-Path -LiteralPath $codexConfig -PathType Leaf) {
        $registration = Get-Content -LiteralPath $codexConfig -Raw
        foreach ($variant in @($versionDirectory, $versionDirectory.Replace('\', '\\'), $versionDirectory.Replace('\', '/'))) {
            if ($registration.IndexOf($variant, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                throw 'Codex still references this version. Change/remove that registration first.'
            }
        }
    }
    if ($PSCmdlet.ShouldProcess($versionDirectory, 'Remove only this marked application version; preserve config, state, secrets and other versions')) {
        # The fully resolved target was checked against InstallRoot above; no shell delegation.
        Remove-Item -LiteralPath $versionDirectory -Recurse -Force
        Write-Output "Removed application version $Version. Configuration, jobs and OS keyring were retained."
    }
    return
}

# Windows PowerShell 5's Get-FileHash script can inherit WhatIf into its
# ForEach-Object property lookup and produce no hash. This read-only .NET path
# remains deterministic in both actual installation and a dry run.
$hashStream = [System.IO.File]::OpenRead($WheelPath)
$sha256 = [System.Security.Cryptography.SHA256]::Create()
try { $wheelHash = [System.BitConverter]::ToString($sha256.ComputeHash($hashStream)).Replace('-', '').ToLowerInvariant() }
finally { $hashStream.Dispose(); $sha256.Dispose() }
$checksums = Join-Path $bundleRoot 'SHA256SUMS.txt'
if (Test-Path -LiteralPath $checksums -PathType Leaf) {
    $expectedLine = @(Get-Content -LiteralPath $checksums | Where-Object { $_ -match ('  wheel/' + [regex]::Escape($wheelName) + '$') })
    if ($expectedLine.Count -ne 1 -or $expectedLine[0].Substring(0, 64).ToLowerInvariant() -ne $wheelHash) {
        throw 'The wheel does not match the release checksum manifest.'
    }
}
$previous = $null
if (Test-Path -LiteralPath $versionDirectory) {
    if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) { throw 'The destination already exists without this installer ownership marker.' }
    $previous = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
    if ($previous.package -ne $packageName -or $previous.version -ne $Version) { throw 'The destination belongs to another package/version.' }
    if ($previous.wheel_sha256 -ne $wheelHash) { throw 'This version directory contains a different wheel. Use a new version; existing files were preserved.' }
}
# An explicit first-install selection survives a later invocation from another
# shell. Environment defaults must not silently replace a stored private config.
if (-not $ConfigPath -and $previous -and $previous.PSObject.Properties['config_path']) {
    $ConfigPath = $previous.config_path
}
if (-not $ConfigPath) { $ConfigPath = $env:BAMBU_WORKFLOW_CONFIG }
if ($ConfigPath) { $ConfigPath = Get-AbsolutePath $ConfigPath }
if ($WhatIfPreference) {
    Write-Output ([ordered]@{ version = $Version; runtime = $runtimePython; wheel_sha256 = $wheelHash; config = $ConfigPath; codex_modified = $false } | ConvertTo-Json)
    return
}
if (-not $PSCmdlet.ShouldProcess($versionDirectory, 'Install an isolated application version; preserve all existing versions and printer data')) { return }

if (Test-Path -LiteralPath $versionDirectory) {
    if ($previous.status -eq 'installed') {
        $installedInfo = Get-InstalledInfo $runtimePython
        if ($installedInfo.version -ne $Version) { throw 'The existing runtime reports a different version.' }
        $previous | Add-Member -NotePropertyName config_path -NotePropertyValue $ConfigPath -Force
        Write-JsonFile $markerPath $previous
        Write-Output ([ordered]@{ installed = $true; already_present = $true; version = $Version; python = $installedInfo.python; args = @('-m', 'bambu_workflow.server'); config = $ConfigPath; tool_count = $installedInfo.tool_count; wheel_sha256 = $wheelHash; codex_modified = $false } | ConvertTo-Json)
        return
    }
    if (-not $Resume) { throw 'An incomplete installation exists. Re-run with -Resume to complete it without deleting files.' }
}

$pythonCommand = Get-Command $Python -ErrorAction Stop
$pythonInfoText = & $pythonCommand.Source -c 'import json,sys; print(json.dumps({"major":sys.version_info.major,"minor":sys.version_info.minor,"base":sys._base_executable}))'
if ($LASTEXITCODE -ne 0) { throw 'The selected Python interpreter could not be inspected.' }
$pythonInfo = $pythonInfoText | ConvertFrom-Json
if ($pythonInfo.major -ne 3 -or $pythonInfo.minor -lt 12) { throw 'Python 3.12 or newer is required.' }
$basePython = Get-AbsolutePath $pythonInfo.base
New-Item -ItemType Directory -Path $versionDirectory -Force | Out-Null
$marker = [ordered]@{ package = $packageName; version = $Version; status = 'installing'; wheel_sha256 = $wheelHash; base_python = $basePython; config_path = $ConfigPath; created_utc = [DateTime]::UtcNow.ToString('o') }
Write-JsonFile $markerPath $marker
if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
    Invoke-Python $basePython @('-m', 'venv', (Join-Path $versionDirectory '.venv'))
}
$physical = Get-RuntimeLocations $runtimePython $rootAbsolute $versionDirectory
Assert-ChildPath $physical.root $physical.directory
$rootAbsolute = Get-AbsolutePath $physical.root
$versionDirectory = Get-AbsolutePath $physical.directory
$runtimePython = Get-AbsolutePath $physical.python
$markerPath = Join-Path $versionDirectory '.bambu-workflow-runtime.json'
$pipArguments = @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', '--quiet')
$constraints = Join-Path $bundleRoot 'requirements.lock.txt'
if (Test-Path -LiteralPath $constraints -PathType Leaf) { $pipArguments += @('--constraint', $constraints) }
if ($Wheelhouse) { $pipArguments += @('--no-index', '--find-links', (Get-AbsolutePath $Wheelhouse)) }
$pipArguments += $WheelPath
Invoke-Python $runtimePython $pipArguments
Invoke-Python $runtimePython @('-m', 'pip', 'check')

foreach ($directory in @('docs', 'skills')) {
    $sourceDirectory = Join-Path $bundleRoot $directory
    if (Test-Path -LiteralPath $sourceDirectory -PathType Container) {
        Copy-Item -LiteralPath $sourceDirectory -Destination $versionDirectory -Recurse -Force
    }
}
$toolsDirectory = Join-Path $versionDirectory 'tools'
New-Item -ItemType Directory -Path $toolsDirectory -Force | Out-Null
foreach ($file in @('call_mcp.py', 'install.ps1')) {
    $sourceFile = Join-Path $PSScriptRoot $file
    if (Test-Path -LiteralPath $sourceFile -PathType Leaf) { Copy-Item -LiteralPath $sourceFile -Destination (Join-Path $toolsDirectory $file) -Force }
}
foreach ($file in @('README.md', 'LICENSE', 'config.example.json', 'requirements.lock.txt', 'SHA256SUMS.txt')) {
    $sourceFile = Join-Path $bundleRoot $file
    if (Test-Path -LiteralPath $sourceFile -PathType Leaf) { Copy-Item -LiteralPath $sourceFile -Destination (Join-Path $versionDirectory $file) -Force }
}
if ($InstallSkill) {
    $skillSource = Join-Path $versionDirectory 'skills\bambu-print'
    if (-not (Test-Path -LiteralPath (Join-Path $skillSource 'SKILL.md') -PathType Leaf)) { throw 'The release does not contain the bambu-print skill.' }
    $codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
    $skillsRoot = if ($SkillRoot) { Get-AbsolutePath $SkillRoot } else { Get-AbsolutePath (Join-Path $codexHome 'skills') }
    New-Item -ItemType Directory -Path $skillsRoot -Force | Out-Null
    $skillTarget = Join-Path $skillsRoot 'bambu-print'
    Assert-ChildPath $skillsRoot $skillTarget
    if (Test-Path -LiteralPath $skillTarget) {
        $backupRoot = Get-AbsolutePath (Join-Path (Split-Path -Parent $skillsRoot) 'skill-backups')
        New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
        $backup = Join-Path $backupRoot ('bambu-print-' + [DateTime]::UtcNow.ToString('yyyyMMddHHmmssfff'))
        Assert-ChildPath $backupRoot $backup
        Copy-Item -LiteralPath $skillTarget -Destination $backup -Recurse
    }
    Copy-Item -LiteralPath $skillSource -Destination $skillsRoot -Recurse -Force
}
# Offline software verification only. Importing tool schemas does not call main(),
# recover jobs, contact a printer or read the user's access code.
$verified = Get-InstalledInfo $runtimePython
if ($verified.version -ne $Version -or $verified.tool_count -lt 1) { throw 'Installed runtime did not expose the expected version and MCP tool schemas.' }
$marker.status = 'installed'
$marker.completed_utc = [DateTime]::UtcNow.ToString('o')
Write-JsonFile $markerPath $marker
Write-Output ([ordered]@{ installed = $true; version = $Version; python = $verified.python; args = @('-m', 'bambu_workflow.server'); config = $ConfigPath; tool_count = $verified.tool_count; wheel_sha256 = $wheelHash; codex_modified = $false } | ConvertTo-Json)
