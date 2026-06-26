$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root '.venv\Scripts\python.exe'
$pyinstaller = Join-Path $root '.venv\Scripts\pyinstaller.exe'
$icon = Join-Path $root 'icon\Aura-Translat.ico'

if (-not (Test-Path $python)) {
    throw "Python venv introuvable: $python"
}
if (-not (Test-Path $pyinstaller)) {
    throw "PyInstaller introuvable: $pyinstaller"
}

# Ensure build venv has all declared dependencies before packaging.
& $python -m pip install --disable-pip-version-check -r (Join-Path $root 'requirements.txt')

if ($LASTEXITCODE -ne 0) {
    throw "Echec installation des dependances depuis requirements.txt"
}

& $pyinstaller `
    --noconfirm `
    --clean `
    --name Aura-Translat `
    --onefile `
    --windowed `
    --icon $icon `
    --runtime-tmpdir "." `
    --collect-data faster_whisper `
    --collect-data huggingface_hub `
    --collect-all imageio_ffmpeg `
    --collect-all yt_dlp `
    --hidden-import imageio_ffmpeg `
    --hidden-import yt_dlp `
    --add-data "config;config" `
    --add-data "icon;icon" `
    main.py
