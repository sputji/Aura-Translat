from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


_CONFIG_FILENAME = "app_config.json"

logger = logging.getLogger(__name__)

_VALID_STT_MODELS = {"tiny", "base", "small", "medium"}
_VALID_STT_DEVICES = {"auto", "cpu", "cuda"}
_VALID_COMPUTE_TYPES = {"int8", "int8_float16", "float16", "float32"}
_VALID_SPEEDS = {"slow", "normal", "fast"}
_VALID_AUDIO_SOURCES = {"system", "url"}
_VALID_URL_LIVE_MODES = {"low-latency", "high-quality"}
_LANGUAGE_CODE_MAP = {
    "english": "en",
    "french": "fr",
    "francais": "fr",
    "français": "fr",
    "spanish": "es",
    "german": "de",
    "italian": "it",
}


def _default_translation_system_prompt() -> str:
    return (
        "Tu es un traducteur simultane ultra-rapide. "
        "Respecte strictement la langue cible demandee. "
        "Rends uniquement la traduction brute, sans commentaire ni note."
    )


@dataclass
class AppMetadata:
    name: str = "Aura-Translat"
    version: str = "1.0.1"
    developer: str = "Nicolas"
    company: str = "Aura Neo"
    website: str = "https://auraneo.fr"


@dataclass
class LanguagesConfig:
    source: str = "English"
    target: str = "Francais"


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    chunk_seconds: float = 1.8
    silence_threshold: float = 0.001
    input_device: str = ""
    source_mode: str = "system"
    stream_url: str = ""
    url_live_mode: str = "low-latency"


@dataclass
class STTConfig:
    model_size: str = "base"
    language: str = "en"
    device: str = "auto"
    compute_type: str = "int8"
    cache_dir: str = ""


@dataclass
class TranslationConfig:
    backend: str = "ollama"
    host: str = "http://127.0.0.1:11434"
    model: str = "llama3.2:3b"
    live_model: str = "llama3.2:3b"
    file_model: str = "llama3.1:8b"
    system_prompt: str = field(default_factory=_default_translation_system_prompt)


@dataclass
class OverlayConfig:
    background_color: str = "#202124"
    background_opacity: float = 0.85
    text_color: str = "#FFFFFF"
    font_size: int = 26
    font_bold: bool = False
    display_speed: str = "normal"
    max_visible_lines: int = 4
    width: int = 1200
    height: int = 180
    position: str = "bottom-center"
    bottom_margin: int = 42
    always_on_top: bool = True
    frameless: bool = True
    auto_scroll: bool = True


@dataclass
class ShortcutsConfig:
    toggle_pause: str = "Ctrl+Shift+P"
    open_settings: str = "Ctrl+Shift+S"
    toggle_overlay: str = "Ctrl+Shift+H"


@dataclass
class AppConfig:
    app: AppMetadata = field(default_factory=AppMetadata)
    languages: LanguagesConfig = field(default_factory=LanguagesConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    stt: STTConfig = field(default_factory=STTConfig)
    translation: TranslationConfig = field(default_factory=TranslationConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    shortcuts: ShortcutsConfig = field(default_factory=ShortcutsConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        return cls(
            app=AppMetadata(**data.get("app", {})),
            languages=LanguagesConfig(**data.get("languages", {})),
            audio=AudioConfig(**data.get("audio", {})),
            stt=STTConfig(**data.get("stt", {})),
            translation=TranslationConfig(**data.get("translation", {})),
            overlay=OverlayConfig(**data.get("overlay", {})),
            shortcuts=ShortcutsConfig(**data.get("shortcuts", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_CONFIG = AppConfig()


def _language_to_stt_code(source_language: str) -> str:
    key = source_language.strip().lower()
    return _LANGUAGE_CODE_MAP.get(key, "auto")


def _sanitize_prompt(prompt: str) -> str:
    normalized = " ".join((prompt or "").split())
    if not normalized:
        return _default_translation_system_prompt()

    lowered = normalized.lower()
    legacy_markers = [
        "texte anglais",
        "en francais",
        "en français",
        "traduis le texte anglais",
    ]
    if any(marker in lowered for marker in legacy_markers):
        return _default_translation_system_prompt()

    return normalized


def _normalize_config(config: AppConfig) -> AppConfig:
    config.app.version = AppMetadata().version
    config.languages.source = config.languages.source.strip() or "English"
    config.languages.target = config.languages.target.strip() or "Francais"

    config.audio.sample_rate = int(config.audio.sample_rate or 16000)
    if config.audio.sample_rate <= 0:
        config.audio.sample_rate = 16000
    config.audio.channels = max(1, int(config.audio.channels or 1))
    config.audio.chunk_seconds = float(config.audio.chunk_seconds or 1.0)
    config.audio.chunk_seconds = min(max(config.audio.chunk_seconds, 0.4), 4.0)
    config.audio.silence_threshold = float(config.audio.silence_threshold or 0.0008)
    config.audio.silence_threshold = min(max(config.audio.silence_threshold, 0.0001), 0.05)
    config.audio.source_mode = (config.audio.source_mode or "system").strip().lower()
    if config.audio.source_mode not in _VALID_AUDIO_SOURCES:
        config.audio.source_mode = "system"
    config.audio.stream_url = (config.audio.stream_url or "").strip()
    config.audio.url_live_mode = (getattr(config.audio, "url_live_mode", "low-latency") or "low-latency").strip().lower()
    if config.audio.url_live_mode not in _VALID_URL_LIVE_MODES:
        config.audio.url_live_mode = "low-latency"

    config.stt.model_size = (config.stt.model_size or "base").strip().lower()
    if config.stt.model_size not in _VALID_STT_MODELS:
        config.stt.model_size = "base"

    config.stt.device = (config.stt.device or "auto").strip().lower()
    if config.stt.device not in _VALID_STT_DEVICES:
        config.stt.device = "auto"

    config.stt.compute_type = (config.stt.compute_type or "int8").strip().lower()
    if config.stt.compute_type not in _VALID_COMPUTE_TYPES:
        config.stt.compute_type = "int8"

    normalized_stt_lang = (config.stt.language or "").strip().lower()
    expected_lang = _language_to_stt_code(config.languages.source)
    if not normalized_stt_lang or normalized_stt_lang == "auto":
        config.stt.language = expected_lang
    elif normalized_stt_lang in _LANGUAGE_CODE_MAP.values():
        config.stt.language = normalized_stt_lang
    else:
        config.stt.language = expected_lang

    config.translation.backend = (config.translation.backend or "ollama").strip().lower()
    config.translation.host = (config.translation.host or "http://127.0.0.1:11434").strip()
    config.translation.model = (config.translation.model or "llama3.1:8b").strip()
    config.translation.live_model = (getattr(config.translation, "live_model", "") or config.translation.model).strip()
    config.translation.file_model = (getattr(config.translation, "file_model", "") or config.translation.model).strip()
    config.translation.system_prompt = _sanitize_prompt(config.translation.system_prompt)

    config.overlay.display_speed = (config.overlay.display_speed or "normal").strip().lower()
    if config.overlay.display_speed not in _VALID_SPEEDS:
        config.overlay.display_speed = "normal"
    config.overlay.max_visible_lines = min(max(int(config.overlay.max_visible_lines or 4), 1), 12)
    config.overlay.font_size = min(max(int(config.overlay.font_size or 26), 12), 72)
    config.overlay.background_opacity = min(max(float(config.overlay.background_opacity or 0.85), 0.1), 1.0)

    config.shortcuts.toggle_pause = (getattr(config.shortcuts, "toggle_pause", "") or "").strip() or "Ctrl+Shift+P"
    config.shortcuts.open_settings = (getattr(config.shortcuts, "open_settings", "") or "").strip() or "Ctrl+Shift+S"
    config.shortcuts.toggle_overlay = (getattr(config.shortcuts, "toggle_overlay", "") or "").strip() or "Ctrl+Shift+H"

    return config


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def _sync_companion_configs(primary_path: Path, config_data: dict[str, Any]) -> None:
    companion_files = [
        primary_path.parent / "app_config.backup.json",
        primary_path.parent / "app_config.export.json",
    ]
    for companion in companion_files:
        try:
            _write_json(companion, config_data)
            logger.debug("Config companion synced: %s", companion)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("Impossible de synchroniser config companion: %s", companion, exc_info=True)


def _config_path(config_path: str | Path | None = None) -> Path:
    if config_path is not None:
        return Path(config_path)
    return Path(__file__).resolve().parent / _CONFIG_FILENAME


def load_app_config(config_path: str | Path | None = None) -> AppConfig:
    path = _config_path(config_path)
    if not path.exists():
        logger.warning("Config absente, creation config par defaut: %s", path)
        save_app_config(DEFAULT_CONFIG, path)
        return _normalize_config(DEFAULT_CONFIG)

    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Config invalide, restauration valeurs par defaut: %s", exc)
        save_app_config(DEFAULT_CONFIG, path)
        return _normalize_config(DEFAULT_CONFIG)

    config = _normalize_config(AppConfig.from_dict(data))

    # Auto-heal legacy prompt/settings inconsistencies on load.
    if config.to_dict() != data:
        logger.info("Config normalisee automatiquement au chargement.")
        save_app_config(config, path)

    return config


def save_app_config(config: AppConfig, config_path: str | Path | None = None) -> None:
    path = _config_path(config_path)
    normalized = _normalize_config(config)
    payload = normalized.to_dict()

    _write_json(path, payload)
    _sync_companion_configs(path, payload)
    logger.debug("Config sauvegardee: %s", path)
