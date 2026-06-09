param(
    [switch]$NoStart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = [System.IO.Path]::GetFullPath((Join-Path $ScriptDir ".."))
$ApiDir = Join-Path $Root "apps\api"
$WebDir = Join-Path $Root "apps\web"
$StorageDir = Join-Path $ApiDir "storage"
$Ports = @(3000, 3001, 8001)

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message"
}

function Get-FullPath {
    param([string]$Path)
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
}

function Test-IsPathWithin {
    param(
        [string]$Path,
        [string]$Parent
    )
    $full = Get-FullPath $Path
    $base = Get-FullPath $Parent
    return $full.Equals($base, [System.StringComparison]::OrdinalIgnoreCase) -or
        $full.StartsWith($base + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-WorkspaceTarget {
    param([string]$Path)
    $full = Get-FullPath $Path
    if (-not (Test-IsPathWithin $full $Root)) {
        throw "Refusing to remove path outside workspace: $full"
    }
    if ((Test-Path -LiteralPath $StorageDir) -and (Test-IsPathWithin $full $StorageDir)) {
        throw "Refusing to remove protected storage path: $full"
    }
    return $full
}

function Remove-WorkspaceItem {
    param([string]$Path)

    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) {
        return
    }

    try {
        $full = Assert-WorkspaceTarget $item.FullName
    }
    catch {
        Write-Host "Skipped protected path: $Path"
        return
    }

    Write-Host "Removing: $full"
    Remove-Item -LiteralPath $full -Recurse -Force -ErrorAction SilentlyContinue
}

function Stop-DevProcesses {
    Write-Step "Stopping old BibexPy dev servers"

    $allProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $childrenByParent = @{}
    foreach ($proc in $allProcesses) {
        $parentId = [int]$proc.ParentProcessId
        if (-not $childrenByParent.ContainsKey($parentId)) {
            $childrenByParent[$parentId] = [System.Collections.Generic.List[int]]::new()
        }
        [void]$childrenByParent[$parentId].Add([int]$proc.ProcessId)
    }

    $protectedPids = [System.Collections.Generic.HashSet[int]]::new()
    $current = $allProcesses | Where-Object { [int]$_.ProcessId -eq [int]$PID } | Select-Object -First 1
    while ($null -ne $current) {
        [void]$protectedPids.Add([int]$current.ProcessId)
        $parentId = [int]$current.ParentProcessId
        if ($parentId -le 0 -or $protectedPids.Contains($parentId)) {
            break
        }
        $current = $allProcesses | Where-Object { [int]$_.ProcessId -eq $parentId } | Select-Object -First 1
    }

    $pidSet = [System.Collections.Generic.HashSet[int]]::new()

    function Add-ProcessTree {
        param([int]$ProcessId)
        if ($ProcessId -le 0 -or $protectedPids.Contains($ProcessId)) {
            return
        }
        if ($pidSet.Add($ProcessId) -and $childrenByParent.ContainsKey($ProcessId)) {
            foreach ($childId in $childrenByParent[$ProcessId]) {
                Add-ProcessTree -ProcessId $childId
            }
        }
    }

    $devProcessNames = @("cmd.exe", "node.exe", "npm.exe", "python.exe")
    foreach ($proc in $allProcesses) {
        if (($devProcessNames -contains $proc.Name) -and $proc.CommandLine) {
            if ($proc.CommandLine.IndexOf($Root, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                Add-ProcessTree -ProcessId ([int]$proc.ProcessId)
            }
        }
    }

    try {
        $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $Ports -contains $_.LocalPort })
        foreach ($listener in $listeners) {
            Add-ProcessTree -ProcessId ([int]$listener.OwningProcess)
        }
    }
    catch {
        Write-Host "Port scan unavailable; process-path matching was still used."
    }

    $targets = @($pidSet | Sort-Object -Descending)
    if ($targets.Count -eq 0) {
        Write-Host "No old BibexPy dev server processes found."
        return
    }

    foreach ($targetPid in $targets) {
        $process = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
        if ($null -eq $process) {
            continue
        }
        Write-Host "Stopping PID $targetPid ($($process.ProcessName))"
        Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue
    }

    Start-Sleep -Milliseconds 700
}

function Clear-GeneratedFiles {
    Write-Step "Clearing generated files and caches"
    Write-Host "Protected storage: $StorageDir"

    $cleanupTargets = @(
        (Join-Path $WebDir ".next"),
        (Join-Path $WebDir "out"),
        (Join-Path $WebDir ".turbo"),
        (Join-Path $WebDir "node_modules\.cache"),
        (Join-Path $WebDir "tsconfig.tsbuildinfo"),
        (Join-Path $ApiDir ".pytest_cache"),
        (Join-Path $Root ".pytest_cache"),
        (Join-Path $Root ".mypy_cache"),
        (Join-Path $Root ".ruff_cache"),
        (Join-Path $Root "build"),
        (Join-Path $Root "dist"),
        (Join-Path $Root "packages\bibex_core\bibex_core.egg-info")
    )

    foreach ($target in $cleanupTargets) {
        Remove-WorkspaceItem -Path $target
    }

    $pythonRoots = @(
        $ApiDir,
        (Join-Path $Root "packages\bibex_core"),
        (Join-Path $Root "BibexPy")
    )

    foreach ($scanRoot in $pythonRoots) {
        if (-not (Test-Path -LiteralPath $scanRoot)) {
            continue
        }

        Get-ChildItem -LiteralPath $scanRoot -Force -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
            Where-Object { -not (Test-IsPathWithin $_.FullName $StorageDir) } |
            ForEach-Object { Remove-WorkspaceItem -Path $_.FullName }

        Get-ChildItem -LiteralPath $scanRoot -Force -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object {
                ($_.Extension -in @(".pyc", ".pyo")) -and
                (-not (Test-IsPathWithin $_.FullName $StorageDir))
            } |
            ForEach-Object { Remove-WorkspaceItem -Path $_.FullName }
    }
}

function Start-DevServers {
    Write-Step "Starting BibexPy dev servers"

    if (-not (Test-Path -LiteralPath $ApiDir)) {
        throw "Missing API directory: $ApiDir"
    }
    if (-not (Test-Path -LiteralPath $WebDir)) {
        throw "Missing web directory: $WebDir"
    }

    if (-not (Test-Path -LiteralPath (Join-Path $WebDir "node_modules"))) {
        Write-Host "Warning: apps\web\node_modules is missing. Run npm install in apps\web if frontend fails."
    }

    $apiCommand = "cd /d `"$ApiDir`" && set `"PYTHONPATH=$Root\packages;%PYTHONPATH%`" && python -m uvicorn main:app --reload --host 127.0.0.1 --port 8001"
    $webCommand = "cd /d `"$WebDir`" && set `"NEXT_PUBLIC_API_BASE=http://localhost:8001`" && npm run dev -- --hostname 127.0.0.1 --port 3000"

    Start-Process -FilePath "cmd.exe" -ArgumentList @("/k", $apiCommand) -WorkingDirectory $ApiDir -WindowStyle Normal
    Start-Process -FilePath "cmd.exe" -ArgumentList @("/k", $webCommand) -WorkingDirectory $WebDir -WindowStyle Normal

    Write-Host "Backend:  http://localhost:8001/docs"
    Write-Host "Frontend: http://localhost:3000"
}

Stop-DevProcesses
Clear-GeneratedFiles

if ($NoStart) {
    Write-Step "NoStart specified; dev servers were not launched"
    exit 0
}

Start-DevServers
