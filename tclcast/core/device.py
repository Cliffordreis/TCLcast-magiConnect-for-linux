"""Abstração unificada de dispositivo de casting.

Unifica TCL (protocolo proprietário), DLNA e Roku atrás de uma interface comum
para a GUI.
"""
from __future__ import annotations

import logging
import threading
from enum import Enum
from typing import Optional

from ..protocol import tcl_const as C
from ..protocol.dlna import DlnaControl, DlnaDevice
from ..protocol.roku import RokuDevice, keypress as roku_key, launch as roku_launch
from ..protocol.tcl_client import TclClient
from ..protocol import tcl_const

log = logging.getLogger(__name__)


class DeviceKind(Enum):
    TCL = "tcl"
    DLNA = "dlna"
    ROKU = "roku"


class CastDevice:
    """Device de casting unificado."""

    def __init__(self, name: str, ip: str, kind: DeviceKind):
        self.name = name
        self.ip = ip
        self.kind = kind
        self._tcl: Optional[TclClient] = None
        self._dlna: Optional[DlnaControl] = None
        self._roku: Optional[RokuDevice] = None
        self._lock = threading.Lock()
        self._connected = False

    # ------------------------------------------------------------------ #
    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        # A parte de rede roda FORA do lock: connect() pode levar segundos
        # (TV desligada etc.) e não pode segurar quem quer desconectar/sair.
        if self.kind == DeviceKind.TCL:
            client = TclClient(self.ip, phone_name="tclcast-linux")
            client.connect()
            with self._lock:
                self._tcl = client
                self._connected = True
        elif self.kind == DeviceKind.DLNA:
            # DlnaControl é stateless (HTTP por chamada); marcar conectado
            with self._lock:
                self._dlna = DlnaControl(self._dlna_dev())
                self._connected = True
        elif self.kind == DeviceKind.ROKU:
            with self._lock:
                self._roku = RokuDevice(self.ip, self.name)
                self._connected = True

    def _dlna_dev(self) -> DlnaDevice:
        # Preservado pelo discovery; reatribuído via attach_dlna
        return getattr(self, "_dl", None) or DlnaDevice(
            friendly_name=self.name, location=f"http://{self.ip}:1400/xml",
            usn=f"uuid:{self.ip}", st="", ip=self.ip)

    def attach_dlna(self, dev: DlnaDevice) -> None:
        self._dl = dev
        self._dlna = DlnaControl(dev)

    def attach_tcl(self, client: TclClient) -> None:
        self._tcl = client

    def disconnect(self) -> None:
        with self._lock:
            if self._tcl:
                self._tcl.close()
                self._tcl = None
            self._connected = False

    # ------------------------------------------------------------------ #
    # Controle remoto
    # ------------------------------------------------------------------ #
    def send_key(self, key: str) -> None:
        if self.kind == DeviceKind.TCL and self._tcl:
            self._tcl.send_key(key)
        elif self.kind == DeviceKind.ROKU and self._roku:
            roku_key(self._roku, key)
        elif self.kind == DeviceKind.DLNA:
            log.warning("DLNA não suporta controle remoto por teclas")

    def set_volume(self, level: int) -> None:
        if self.kind == DeviceKind.TCL and self._tcl:
            self._tcl.set_volume(level)
        elif self.kind == DeviceKind.DLNA and self._dlna:
            self._dlna.set_volume(level)
        elif self.kind == DeviceKind.ROKU and self._roku:
            log.warning("Roku: use as teclas VOL_UP/VOL_DOWN")

    def set_mute(self, mute: bool) -> None:
        if self.kind == DeviceKind.TCL and self._tcl:
            self._tcl.set_mute(mute)
        elif self.kind == DeviceKind.DLNA and self._dlna:
            self._dlna.set_mute(mute)

    # ------------------------------------------------------------------ #
    # Mídia
    # ------------------------------------------------------------------ #
    def play_url(self, url: str, title: str = "") -> None:
        """Envia uma URL de vídeo para a TV reproduzir."""
        if self.kind == DeviceKind.TCL and self._tcl:
            self._tcl.play_media(url, tcl_const.TYPE_VIDEO, title)
        elif self.kind == DeviceKind.DLNA and self._dlna:
            self._dlna.set_uri(url, title)
            self._dlna.play()
        elif self.kind == DeviceKind.ROKU and self._roku:
            # Roku: lança app de streaming com contentId (YouTube etc.)
            roku_launch(self._roku, "837", url)  # 837 = YouTube

    def play_media(self, url: str, media_type: str = tcl_const.TYPE_VIDEO,
                   title: str = "") -> None:
        """Reproduz mídia com tipo explícito (Video/Audio/Image/m3u8)."""
        if self.kind == DeviceKind.TCL and self._tcl:
            self._tcl.play_media(url, media_type, title)
        else:
            self.play_url(url, title)

    def play_local(self, local_path: str, server_url: str, title: str = "",
                   media_type: str = tcl_const.TYPE_VIDEO) -> None:
        """Serve um arquivo local por HTTP e envia o URL para a TV."""
        self.play_url(server_url, title or local_path.rsplit("/", 1)[-1])

    def pause(self) -> None:
        if self.kind == DeviceKind.TCL and self._tcl:
            self._tcl.pause()
        elif self.kind == DeviceKind.DLNA and self._dlna:
            self._dlna.pause()
        elif self.kind == DeviceKind.ROKU and self._roku:
            roku_key(self._roku, "PLAY")

    def resume(self) -> None:
        if self.kind == DeviceKind.TCL and self._tcl:
            self._tcl.resume()

    def stop(self) -> None:
        if self.kind == DeviceKind.TCL and self._tcl:
            self._tcl.stop()
        elif self.kind == DeviceKind.DLNA and self._dlna:
            self._dlna.stop()

    def seek(self, position_ms: int) -> None:
        if self.kind == DeviceKind.TCL and self._tcl:
            self._tcl.seek(position_ms)

    def play_next(self) -> None:
        if self.kind == DeviceKind.TCL and self._tcl:
            self._tcl.play_next()

    def play_previous(self) -> None:
        if self.kind == DeviceKind.TCL and self._tcl:
            self._tcl.play_previous()

    # ------------------------------------------------------------------ #
    # Texto na TV, player, apps (recursos "extra" vs. app original)
    # ------------------------------------------------------------------ #
    def send_text(self, text: str) -> None:
        if self.kind == DeviceKind.TCL and self._tcl:
            self._tcl.send_text(text)

    def query_player(self, cb) -> None:
        if self.kind == DeviceKind.TCL and self._tcl:
            self._tcl.query_player(cb)
        else:
            cb({})

    def get_apps(self, cb) -> None:
        if self.kind == DeviceKind.TCL and self._tcl:
            self._tcl.get_apps(cb)
        else:
            cb([])

    def open_app(self, package: str) -> None:
        if self.kind == DeviceKind.TCL and self._tcl:
            self._tcl.open_app(package)

    def uninstall_app(self, package: str, name: str) -> None:
        if self.kind == DeviceKind.TCL and self._tcl:
            self._tcl.uninstall_app(package, name)

    def get_storage(self, cb) -> None:
        if self.kind == DeviceKind.TCL and self._tcl:
            self._tcl.get_storage(cb)
        else:
            cb({})
