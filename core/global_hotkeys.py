from __future__ import annotations

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class GlobalHotkeyManager:
    def __init__(self) -> None:
        self._enabled = False
        self._keyboard = None
        self._hook_ids: list[int] = []
        self._lock = threading.Lock()

        try:
            import keyboard  # type: ignore

            self._keyboard = keyboard
            self._enabled = True
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Global hotkeys indisponibles (module keyboard): %s", exc)
            self._enabled = False

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def configure(
        self,
        toggle_pause_seq: str,
        open_settings_seq: str,
        toggle_overlay_seq: str,
        on_toggle_pause: Callable[[], None],
        on_open_settings: Callable[[], None],
        on_toggle_overlay: Callable[[], None],
    ) -> bool:
        if not self._enabled or self._keyboard is None:
            return False

        with self._lock:
            self._clear_locked()
            self._register_locked(toggle_pause_seq, on_toggle_pause)
            self._register_locked(open_settings_seq, on_open_settings)
            self._register_locked(toggle_overlay_seq, on_toggle_overlay)

        logger.info(
            "Global hotkeys actifs: pause=%s, settings=%s, overlay=%s",
            toggle_pause_seq,
            open_settings_seq,
            toggle_overlay_seq,
        )
        return True

    def stop(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._clear_locked()

    def _register_locked(self, sequence: str, callback: Callable[[], None]) -> None:
        assert self._keyboard is not None
        combo = (sequence or "").strip().lower()
        if not combo:
            return

        hook_id = self._keyboard.add_hotkey(combo, callback, suppress=False, trigger_on_release=False)
        self._hook_ids.append(hook_id)

    def _clear_locked(self) -> None:
        if self._keyboard is None:
            self._hook_ids.clear()
            return

        for hook_id in self._hook_ids:
            try:
                self._keyboard.remove_hotkey(hook_id)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.debug("Impossible de retirer hotkey globale", exc_info=True)
        self._hook_ids.clear()
