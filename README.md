# Aura-Translat

![Version](https://img.shields.io/badge/version-1.0.0-0a7ea4)
![Statut](https://img.shields.io/badge/statut-production-1f9d57)
![Plateforme](https://img.shields.io/badge/plateforme-Windows%2010%2F11-2f6db0)
![Stack](https://img.shields.io/badge/stack-Python%20%2B%20PyQt6-3f8c3a)

Application desktop de sous-titrage et traduction en direct, optimisee pour un usage local, reactif et fiable.

## Vue rapide

- 🎬 Overlay live avec controles integres: pause/reprise, masquer, parametres
- 🟢 Double indicateur visuel: LIVE/PAUSE + stabilite (STABLE/TEMP/INSTABLE)
- 🔊 Capture audio systeme ou URL live (YouTube/Twitch/autres flux compatibles)
- 🌍 Traduction multi-langues configurable (source + cible)
- 🧩 Modeles Ollama separes pour live et fichier, recommandation adaptative
- 📄 Traduction audio/video avec export TXT + SRT (compatible lecteurs tiers) + metadata confiance en fichier dedie
- ⌨️ Raccourcis persistants + hotkeys globaux (meme overlay masque)
- ▶️ Lecteur VLC integre avec commandes en icones

## Architecture rapide

| Bloc | Role |
| --- | --- |
| `core/pipeline.py` | Pipeline live (capture -> STT -> traduction -> overlay) |
| `core/stt_engine.py` | Transcription locale via faster-whisper |
| `core/ollama_client.py` | Traduction Ollama, recommandations modeles, fallback, installation suggeree |
| `core/media_translation.py` | Traitement fichiers et exports TXT/SRT |
| `ui/overlay_window.py` | Overlay transparent et badges runtime |
| `ui/settings_window.py` | Configuration complete (audio, STT, modeles, overlay, raccourcis) |
| `ui/vlc_player_window.py` | Lecture media embarquee via VLC |

## Modeles de traduction

Aura-Translat separe les modeles selon l'usage:

- `translation.live_model`: traduction en direct
- `translation.file_model`: traduction audio/video
- `translation.model`: cle legacy de compatibilite (alignee sur live)

Le bouton `Rafraichir` scanne les modeles Ollama disponibles et adapte automatiquement la recommandation.
Si un modele recommande n'est pas installe, l'application propose `Installer` (ollama pull) ou `Passer`.

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

- `dist-installer/Aura-Translat-installer-1.0.0.exe`

## Prerequis runtime

- Windows 10/11
- Ollama installe localement
- VLC Desktop (lecteur integre)
- ffmpeg/yt-dlp via dependances Python pour le mode URL

## Fiabilite

- URL vide en mode URL: bascule automatique sur audio systeme
- Gate anti-bruit avant STT (energie + zero-crossing + ratio)
- En cas de traduction instable/timeout, conservation du dernier sous-titre valide
- Sanity check au demarrage (audio, STT, Ollama, VLC, ffmpeg)

## Mentions legales

- Proprietaire: AuraNeo - [auraneo.fr](https://auraneo.fr)
- Developpeur: Nicolas
- Copyright: (c) 2026 AuraNeo. Tous droits reserves.
- Cadre juridique: logiciel exploite sous droit francais et droit europeen.
- Fichiers juridiques dedies: `LEGAL/COPYRIGHT.md` et `LEGAL/LEGAL-FR-EU.md`

Note: ces mentions sont informatives dans ce README et ne remplacent pas un avis juridique professionnel.

## Documentation complementaire

- `../Architecture Aura Neo/Aura-Translat/README.md`
- `../Architecture Aura Neo/Aura-Translat/ARCHITECTURE.md`
- `../Architecture Aura Neo/Aura-Translat/FONCTIONNEMENT.md`
- `../Architecture Aura Neo/Aura-Translat/README-PUBLIC.md`
