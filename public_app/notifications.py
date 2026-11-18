"""Windows notifications (toasts) for the stereo display.

Uses Windows.UI.Notifications.Management.UserNotificationListener. The
Notification Center is checked every 1.5 s; only notifications that arrive
after the app started are reported. Windows asks the user once for
permission (Settings > Privacy > Notifications) when needed.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass

try:  # pragma: no cover - depends on the Windows SDK projection
    from winrt.windows.ui.notifications import KnownNotificationBindings, NotificationKinds
    from winrt.windows.ui.notifications.management import UserNotificationListener
except Exception:  # noqa: BLE001
    UserNotificationListener = None
    KnownNotificationBindings = None
    NotificationKinds = None

ACCESS_ALLOWED = 1
# Our own tray messages must never loop back to the display.
IGNORED_APPS = ("philips usb pc link", "python")


@dataclass(frozen=True)
class WindowsNotification:
    app: str
    title: str
    body: str


def notifications_available() -> bool:
    return UserNotificationListener is not None


class NotificationWatcher:
    def __init__(
        self,
        on_notification: Callable[[WindowsNotification], None],
        *,
        poll_s: float = 1.5,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self._on_notification = on_notification
        self._on_status = on_status
        self._poll_s = poll_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.allowed: bool | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running or not notifications_available():
            if not notifications_available():
                self._status("Notificacoes do Windows indisponiveis nesta versao do Windows.")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="WindowsNotifications", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None

    def _status(self, message: str) -> None:
        if self._on_status is not None:
            try:
                self._on_status(message)
            except Exception:  # noqa: BLE001
                pass

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._watch())
        except Exception as exc:  # noqa: BLE001
            self._status(f"Notificacoes do Windows: parou ({exc}).")
        finally:
            loop.close()

    async def _watch(self) -> None:
        listener = UserNotificationListener.current
        status = int(listener.get_access_status())
        if status != ACCESS_ALLOWED:
            try:
                status = int(await listener.request_access_async())
            except Exception:  # noqa: BLE001
                pass
        self.allowed = status == ACCESS_ALLOWED
        if not self.allowed:
            self._status(
                "Notificacoes do Windows: acesso negado. Libere em Configuracoes > Privacidade > "
                "Notificacoes (acesso as notificacoes)."
            )
            return
        self._status("Notificacoes do Windows no visor: ligadas.")
        seen: set[int] | None = None
        while not self._stop.is_set():
            try:
                items = list(await listener.get_notifications_async(NotificationKinds.TOAST))
            except Exception:  # noqa: BLE001
                items = []
            current = {int(item.id) for item in items}
            if seen is None:
                seen = current  # notifications already there when the app started
            else:
                for item in items:
                    if int(item.id) in seen:
                        continue
                    parsed = parse_notification(item)
                    if parsed is not None:
                        try:
                            self._on_notification(parsed)
                        except Exception:  # noqa: BLE001
                            pass
                seen = current | {i for i in seen if i in current}
            await asyncio.sleep(self._poll_s)


def parse_notification(item) -> WindowsNotification | None:
    try:
        app = item.app_info.display_info.display_name if item.app_info else ""
    except Exception:  # noqa: BLE001
        app = ""
    if any(name in (app or "").lower() for name in IGNORED_APPS):
        return None
    texts: list[str] = []
    try:
        binding = item.notification.visual.get_binding(KnownNotificationBindings.toast_generic)
        if binding is not None:
            texts = [str(element.text or "") for element in binding.get_text_elements()]
    except Exception:  # noqa: BLE001
        texts = []
    texts = [text for text in texts if text.strip()]
    title = texts[0] if texts else ""
    body = " ".join(texts[1:]) if len(texts) > 1 else ""
    if not (app or title):
        return None
    return WindowsNotification(app=app or "Windows", title=title, body=body)
