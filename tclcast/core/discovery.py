"""Descoberta unificada de dispositivos (TCL + DLNA) em background.

Estratégia:
1. SSDP (DLNA) acha a TV — é o método confiável
2. Para cada device, tenta TCP 6553 (protocolo TCL) — se conectar, é TCL
3. TCL tem mais recursos (captura, espelhamento, remote completo)
"""
from __future__ import annotations

import logging
import socket
import threading
from typing import Callable

from ..core.device import CastDevice, DeviceKind
from ..protocol.dlna import discover as dlna_discover
from ..protocol.tcl_const import TCP_PORT_COMMAND

log = logging.getLogger(__name__)


def _is_tcl(ip: str, timeout: float = 2.0) -> bool:
    """Verifica se um IP responde ao protocolo TCL (TCP 6553)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((ip, TCP_PORT_COMMAND))
        s.close()
        return True
    except (OSError, socket.timeout):
        return False


def is_host_alive(ip: str, timeout: float = 1.5) -> bool:
    """Verifica se o host está no ar (portas típicas de TV/router).

    Distingue "TV desligada" (nenhuma porta responde = sem rota/ARP falho)
    de "host vivo mas sem o protocolo TCL" (ex.: DHCP mudou o IP da TV).
    """
    for port in (TCP_PORT_COMMAND, 80, 8008, 9000):
        try:
            s = socket.create_connection((ip, port), timeout=timeout)
            s.close()
            return True
        except OSError:
            continue
    return False


class DiscoveryManager:
    """Descobre TVs e emite resultados incrementalmente."""

    def __init__(self):
        self._devices: dict[str, CastDevice] = {}
        self._lock = threading.Lock()
        self.on_device: Callable[[CastDevice], None] | None = None

    @property
    def devices(self) -> list[CastDevice]:
        with self._lock:
            return list(self._devices.values())

    def _emit(self, dev: CastDevice) -> None:
        with self._lock:
            if dev.ip in self._devices:
                return
            self._devices[dev.ip] = dev
        if self.on_device:
            self.on_device(dev)

    def scan_async(self) -> threading.Thread:
        t = threading.Thread(target=self._scan_all, daemon=True)
        t.start()
        return t

    def _scan_all(self) -> None:
        # SSDP (DLNA) — método confiável para achar a TV
        try:
            for d in dlna_discover():
                # Verifica se é TCL tentando TCP 6553
                if _is_tcl(d.ip):
                    dev = CastDevice(d.friendly_name, d.ip, DeviceKind.TCL)
                    self._emit(dev)
                else:
                    dev = CastDevice(d.friendly_name, d.ip, DeviceKind.DLNA)
                    dev.attach_dlna(d)
                    self._emit(dev)
        except Exception as e:
            log.warning("descoberta SSDP falhou: %s", e)

    def add_manual(self, ip: str, kind: DeviceKind, name: str = "") -> CastDevice:
        dev = CastDevice(name or ip, ip, kind)
        self._emit(dev)
        return dev
