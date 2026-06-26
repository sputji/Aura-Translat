param(
    [switch]$SkipBuildExe
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$issPath = Join-Path $PSScriptRoot 'Aura-Translat.iss'
$buildExeScript = Join-Path $root 'build-exe.ps1'

if (-not (Test-Path $issPath)) {
    throw "Script Inno introuvable: $issPath"
}

if (-not $SkipBuildExe) {
    if (-not (Test-Path $buildExeScript)) {
        throw "Script de build EXE introuvable: $buildExeScript"
    }
    Write-Host "[1/2] Build EXE (PyInstaller) en cours..."
    & powershell -ExecutionPolicy Bypass -File $buildExeScript
    if ($LASTEXITCODE -ne 0) {
        throw "Echec build EXE avant creation de l'installeur."
    }
}

$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if (-not $iscc) {
    $defaultPath = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
    if (Test-Path $defaultPath) {
        $iscc = Get-Item $defaultPath
    }
}

if (-not $iscc) {
    $registryRoots = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )

    $inno = Get-ItemProperty $registryRoots -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -like '*Inno Setup*' } |
        Select-Object -First 1

    if ($inno -and $inno.InstallLocation) {
        $regPath = Join-Path $inno.InstallLocation 'ISCC.exe'
        if (Test-Path $regPath) {
            $iscc = Get-Item $regPath
        }
    }
}

if (-not $iscc) {
    throw "Inno Setup Compiler non trouve. Installe Inno Setup 6 puis relance ce script."
}

Push-Location $root
try {
    Write-Host "[2/2] Compilation Inno Setup en cours..."
    if ($iscc -is [System.Management.Automation.CommandInfo]) {
        & $iscc.Source $issPath
    }
    else {
        & $iscc.FullName $issPath
    }
}
finally {
    Pop-Location
}
