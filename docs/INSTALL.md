# Persistent installation on Windows and macOS

Version 0.5.0 installs separately from the checkout. Existing configuration, job history, and OS-keyring secrets stay in place. Python 3.12+ and official Bambu Studio are installed separately. Live Blender export additionally needs the user's Blender MCP.

## Windows release

Extract the release ZIP and run its installer:

```powershell
.\scripts\install.ps1 -Python 'C:\Path\To\Python\python.exe' -InstallSkill
```

The usual runtime is `%LOCALAPPDATA%\BambuWorkflow\app\0.5.0`, with `.venv\Scripts\python.exe`, `tools\call_mcp.py`, docs, and the entire skill folder. The installer reports the actual launcher path. Packaged Windows apps can redirect AppData into `LocalCache`, so use that reported path.

The project wheel is checked against `SHA256SUMS.txt`. Dependencies download separately; `requirements.lock.txt` pins the tested runtime versions. An offline wheel directory can be supplied with `-Wheelhouse 'C:\Path\To\wheelhouse'`. The installer checks metadata and a printer-free MCP catalog. It does not connect a printer, recover old jobs, switch Codex registration, or alter global Python.

If PowerShell execution policy blocks the script, use a process-only setting after verifying the downloaded release:

```powershell
powershell -NoProfile -ExecutionPolicy RemoteSigned -File .\scripts\install.ps1 -Python 'C:\Path\To\Python\python.exe' -InstallSkill
```

A downloaded script may also need `Unblock-File -LiteralPath '.\scripts\install.ps1'`. This does not bypass `Restricted` or organizational policy. See [PowerShell execution policies](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_execution_policies).

Configure a new installation, using the reported launcher:

```powershell
$runtimePython = Join-Path $env:LOCALAPPDATA 'BambuWorkflow\app\0.5.0\.venv\Scripts\python.exe'
& $runtimePython -m bambu_workflow.cli configure
& $runtimePython -m bambu_workflow.cli install-codex
```

`configure` asks for device identity, LAN code, nozzle/plate facts, and Bambu Studio. It refuses to overwrite an existing file and stores the code in OS keyring. Add separate printer entries with unique IDs when needed. [Setup](SETUP.md).

## macOS release

From the extracted macOS ZIP:

```sh
sh scripts/install.sh --python /path/to/python3.12 --install-skill
```

Without `--python`, the installer uses `python3` on PATH. Runtime: `~/Library/Application Support/BambuWorkflow/app/0.5.0/.venv/bin/python`. It verifies the project wheel, creates a private environment, and checks metadata/catalog without printer access or job recovery. It does not change Codex registration, Keychain settings, Gatekeeper, or system Python. Access codes later use macOS Keychain through `configure`.

```sh
runtimePython="$HOME/Library/Application Support/BambuWorkflow/app/0.5.0/.venv/bin/python"
"$runtimePython" -m bambu_workflow.cli configure
"$runtimePython" -m bambu_workflow.cli install-codex
```

Bambu Studio's usual executable is `/Applications/BambuStudio.app/Contents/MacOS/BambuStudio`. Profiles may be under `Contents/Resources/profiles/BBL` or `Contents/resources/profiles/BBL`, in `/Applications` or `~/Applications`. Discovery checks actual paths; set `slicer.profile_root` when necessary. Bundle paths are reflected in upstream [BuildMac.sh](https://github.com/bambulab/BambuStudio/blob/master/BuildMac.sh) and [CMakeLists.txt](https://github.com/bambulab/BambuStudio/blob/master/CMakeLists.txt). A path/help response is not proof of successful slicing.

Use `--install-root`, `--wheel-path`, or `--wheelhouse` for alternate runtime root, project wheel, or offline dependencies. The environment's launcher may be a symlink; keep the venv launcher path rather than replacing it with global Python. Open Bambu Studio through the owner's normal macOS approval flow.

## Build from Git

Windows:

```powershell
git clone https://github.com/netuneed/bambu-workflow-mcp.git
Set-Location bambu-workflow-mcp
python -m venv .build
& .\.build\Scripts\python.exe -m pip install build
& .\.build\Scripts\python.exe -m build --wheel
& .\scripts\install.ps1 -WheelPath '.\dist\bambu_workflow_mcp-0.5.0-py3-none-any.whl' -Python '.\.build\Scripts\python.exe' -InstallSkill
```

macOS:

```sh
git clone https://github.com/netuneed/bambu-workflow-mcp.git
cd bambu-workflow-mcp
python3 -m venv .build
.build/bin/python -m pip install build
.build/bin/python -m build --wheel
sh scripts/install.sh --python .build/bin/python --wheel-path dist/bambu_workflow_mcp-0.5.0-py3-none-any.whl --install-skill
```

The build environment is temporary; installed MCP operation does not depend on it or the checkout. Use a wheel name matching the version actually built.

## Skill and MCP registration

`-InstallSkill`/`--install-skill` copies the whole `bambu-print` folder, including references. Both platforms use `$CODEX_HOME/skills` when set, otherwise `~/.agents/skills`. Existing installations can retain a different discovered directory through the explicit skill-root option. Choose the directory discovered by the target client. Override with `-SkillRoot` or `--skill-root`, for example:

```powershell
.\scripts\install.ps1 -InstallSkill -SkillRoot (Join-Path $env:USERPROFILE '.agents\skills')
```

Keep one discoverable copy. An existing skill is backed up to adjacent `skill-backups`, outside discovery. The public skill is generic and English. Personal printer/material preferences belong in local configuration or a local companion reference; preserve them deliberately when updating rather than publishing them with the skill.

The MCP registration uses the reported venv Python, arguments `["-m","bambu_workflow.server"]`, and the existing absolute config path in `env.BAMBU_WORKFLOW_CONFIG`. Access codes do not belong in registration. `install-codex` refuses to silently replace an existing different runtime. Update that one entry while preserving all other servers. Reload MCP or open a new chat to discover new tools. See the [official Codex MCP documentation](https://developers.openai.com/codex/mcp/).

## Upgrade, rollback, and removal

Every version has a separate directory. A repeated install of identical bytes reuses it; an interrupted install needs `-Resume`/`--resume`. A different wheel with the same version is rejected. Unowned directories and symbolic-link redirection are refused. Old versions remain available for rollback.

When upgrading, supply the existing configuration explicitly:

```powershell
.\scripts\install.ps1 -Python 'C:\Path\To\Python\python.exe' -ConfigPath 'C:\Path\To\Existing\BambuWorkflow\config.json' -InstallSkill
```

macOS uses `--config /absolute/path/config.json`. Retain the same path in registration. No automatic config, database, or secret migration occurs. For rollback, point Codex at the previous venv launcher and preserve that config path. Do not run two copies of one job.

To remove one Windows runtime version, first remove its Codex reference and normally stop its MCP/workers, then preview removal:

```powershell
.\scripts\install.ps1 -Uninstall -Version 0.5.0 -WhatIf
.\scripts\install.ps1 -Uninstall -Version 0.5.0
```

Removal checks its ownership marker, location, and active processes. It preserves configuration, jobs, artifacts, secrets, and other versions.

## Printer-free catalog check

The installed helper lists tools by default without printer access or recovery:

```powershell
$client = Join-Path $env:LOCALAPPDATA 'BambuWorkflow\app\0.5.0\tools\call_mcp.py'
& $runtimePython $client --python $runtimePython --config 'C:\Path\To\BambuWorkflow\config.json'
```

Explicit printer tools can contact hardware. For preparation requests and wait/recovery semantics, see [USAGE.md](USAGE.md).

## Optional Support Fins CLI

The adapter uses external Support Fins v1.0.5, not a bundled copy. Install Node.js 20.10+ separately, then run the downloader with absolute paths outside this repository/runtime package:

```powershell
python .\scripts\install_support_fins.py --directory 'C:\LocalTools\support-fins' --node 'C:\Program Files\nodejs\node.exe'
```

On macOS use `python3`, an external user tools directory, and the actual absolute Node executable. The helper checks Node's version, downloads the pinned release artifact, verifies its size/SHA256, and prints a `support_fins` configuration object. It does not execute geometry generation, install Node, edit personal configuration, or contact a printer. Add the returned object to the existing configuration; it has `command` (absolute Node path), `cli_path` (absolute pinned `.mjs` path), and `sha256`.

The artifact SHA256 is `2ad0469d91d378baade31d785f2fba825d6fe7fbab115f17c6175ea6ab3b97f7`; reviewed source commit is `514549694e816999a5e4c6674829eba7ea852019`. Arbitrary scripts and unreviewed latest versions are refused. The workflow's generation adapter accepts STL and writes an unsliced 3MF plus receipt for subsequent Bambu Studio preparation/review. Existing 3MF project preservation is an upstream feature outside this adapter's local acceptance. [Support workflow and blockers](../skills/bambu-print/references/support-review.md).
