"""Descoberta de TVs TCL via UDP (protocolo IPMsg) com callback incremental.

Cada dispositivo encontrado é emitido imediatamente via on_device, sem esperar
o timeout total — corrige a UX de "ficar em Procurando…".
"""
from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from typing import Callable

from . import tcl_const as C


@dataclass
class DiscoveredDevice:
    ip: str
    name: str
    sender_type: str
    command: int
    version: str
    additional: str
    port: int = C.TCP_PORT_COMMAND

    @property
    def protocol_version(self) -> int:
        try:
            return int(self.version)
        except (TypeError, ValueError):
            return 6


def build_ipmsg(version: str, packet_no: str, sender_name: str,
                sender_type: str, command: int, additional: str = "") -> str:
    """Monta a string IPMsg: version:packetNo:senderName:senderType:cmd:additional."""
    escaped_name = sender_name.replace(":", "&#058")
    parts = [version, packet_no, escaped_name, sender_type, str(command), additional]
    # Usa ':' como separador real; campos usam GAP apenas no payload TCP.
    out = ":".join(parts)
    return out


def parse_ipmsg(raw: str) -> DiscoveredDevice | None:
    """Interpreta uma mensagem IPMsg recebida."""
    parts = raw.strip().split(":")
    if len(parts) < 5:
        return None
    try:
        command = int(parts[4])
    except ValueError:
        return None
    additional = ":".join(parts[5:]) if len(parts) > 5 else ""
    name = parts[2].replace("&#058", ":")
    return DiscoveredDevice(
        ip="", name=name, sender_type=parts[3], command=command,
        version=parts[0], additional=additional,
    )


class TclScanner:
    """Descobre TVs TCL na rede local via broadcast UDP 6537.

    `on_device` é chamado para cada TV assim que ela responde — sem esperar o
    timeout total.
    """

    def __init__(self, sender_name: str = "tclcast-linux",
                 sender_type: str = "phone", timeout: float = 4.0,
                 on_device: Callable[[DiscoveredDevice], None] | None = None):
        self.sender_name = sender_name
        self.sender_type = sender_type
        self.timeout = timeout
        self.on_device = on_device

    def scan(self, broadcast_ip: str = "255.255.255.255") -> list[DiscoveredDevice]:
        found: dict[str, DiscoveredDevice] = {}
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(0.5)  # poll curto para responder rápido
            # Anuncia entrada (BR_ENTRY) e pede status (ISONLINE)
            for cmd in (C.IPMSG_BR_ENTRY, C.ISONLINE):
                msg = build_ipmsg("1", str(int(time.time() * 1000)),
                                  self.sender_name, self.sender_type, cmd)
                try:
                    sock.sendto(msg.encode("utf-8"), (broadcast_ip, C.UDP_PORT))
                except OSError:
                    pass

            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                try:
                    data, addr = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                parsed = parse_ipmsg(data.decode("utf-8", errors="ignore"))
                if parsed and parsed.ip == "":
                    parsed.ip = addr[0]
                    if addr[0] not in found:
                        found[addr[0]] = parsed
                        if self.on_device:
                            self.on_device(parsed)  # incremental
        finally:
            sock.close()
        return list(found.values())
