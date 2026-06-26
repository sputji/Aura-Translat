# Aura-Translat

![Version](https://img.shields.io/badge/version-0.1.4-0a7ea4)
![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-2f6db0)
![Stack](https://img.shields.io/badge/stack-Python%20%2B%20PyQt6-3f8c3a)

Application desktop de sous-titrage et traduction en direct, optimisee pour un usage local et reactif.

## Points forts

- Overlay live avec controles integres: pause/reprise, masque, parametres
- Double indicateur visuel: LIVE/PAUSE + stabilite traduction (STABLE/TEMP/INSTABLE)
- Capture audio systeme ou URL live (YouTube/Twitch/autres sources compatibles)
- Traduction de fichiers media (audio/video) avec export TXT + SRT
- Score de confiance par segment dans les exports
- Lecteur VLC integre avec controles en icones
- Raccourcis persistants + hotkeys globaux (actifs meme si l'overlay est masque)

## Architecture rapide

| Bloc | Role |
| --- | --- |
| `core/pipeline.py` | Pipeline live (capture -> STT -> traduction -> overlay) |
| `core/stt_engine.py` | Transcription locale via faster-whisper |
| `core/ollama_client.py` | Traduction locale Ollama + autostart/fallback |
| `core/media_translation.py` | Traitement fichiers et exports TXT/SRT |
| `ui/overlay_window.py` | Overlay transparent et badges runtime |
| `ui/settings_window.py` | Configuration complete (audio, STT, modeles, overlay, raccourcis) |
| `ui/vlc_player_window.py` | Lecture media embarquee via VLC |

## Modeles de traduction

Aura-Translat separe les modeles selon l'usage:

- `translation.live_model`: utilise par la traduction en direct
- `translation.file_model`: utilise pour la traduction de fichiers
- `translation.model`: cle legacy maintenue pour compatibilite (alignee sur le modele live)

## Demarrage local (developpement)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## Build et installeur

```powershell
# Build EXE
.\build-exe.ps1

# Rebuild EXE + compile installeur Inno Setup
.\installer\build-installer.ps1
```

Installeur genere:

- `dist-installer/Aura-Translat-installer-0.1.4.exe`

## Prerequis runtime

- Windows 10/11
- Ollama installe localement
- VLC Desktop (pour le lecteur integre)
- ffmpeg/yt-dlp geres par dependances Python pour le mode URL

## Notes fiabilite

- Si l'URL est vide en mode URL, l'application rebascule automatiquement en mode audio systeme
- Le pipeline applique une barriere anti-bruit avant STT (energie + zero-crossing + ratio)
- En cas de traduction instable/timeout, le dernier sous-titre valide est conserve

## Documentation complementaire

- `../Architecture Aura Neo/Aura-Translat/README.md`
- `../Architecture Aura Neo/Aura-Translat/ARCHITECTURE.md`
- `../Architecture Aura Neo/Aura-Translat/FONCTIONNEMENT.md`
