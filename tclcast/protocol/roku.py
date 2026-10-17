"""Controle Roku via ECP (External Control Protocol), porta 8060.

Permite detectar e controlar Roku / TVs com Roku (incl. TCL Roku).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.request import Request, urlopen

ECP_PORT = 8060

# Mapeamento de teclas amigáveis -> ECP keypress
KEYS = {
    "UP": "Up", "DOWN": "Down", "LEFT": "Left", "RIGHT": "Right",
    "OK": "Select", "BACK": "Back", "HOME": "Home", "MENU": "Backspace",
    "POWER": "Power", "REV": "Rev", "FWD": "Fwd",
    "PLAY": "Play", "REPLAY": "Replay",
    "VOL_UP": "VolumeUp", "VOL_DOWN": "VolumeDown", "MUTE": "VolumeMute",
    "CH_UP": "ChannelUp", "CH_DOWN": "ChannelDown",
    "INFO": "Info", "BACKSPACE": "Backspace",
}


@dataclass
class RokuDevice:
    ip: str
    name: str
    port: int = ECP_PORT

    @property
    def base(self) -> str:
        return f"http://{self.ip}:{self.port}"


def discover(timeout: float = 3.0) -> list[RokuDevice]:
    """Descobre Roku via mDNS/SSDP é complexo; aqui usamos varredura simples
    da rede + ECP query/device-info. Para produção, integrar zeroconf."""
    # Varredura mínima: retorna lista vazia; o chamador pode adicionar IP manual.
    return []


def query_device_info(dev: RokuDevice) -> dict:
    req = Request(f"{dev.base}/query/device-info", headers={"User-Agent": "tclcast"})
    with urlopen(req, timeout=3) as r:
        return _parse_xmlish(r.read().decode("utf-8", errors="ignore"))


def query_apps(dev: RokuDevice) -> list[dict]:
    req = Request(f"{dev.base}/query/apps", headers={"User-Agent": "tclcast"})
    with urlopen(req, timeout=3) as r:
        return _parse_apps(r.read().decode("utf-8", errors="ignore"))


def keypress(dev: RokuDevice, key: str) -> None:
    ecp_key = KEYS.get(key.upper(), key)
    req = Request(f"{dev.base}/keypress/{ecp_key}", data=b"",
                  headers={"User-Agent": "tclcast"})
    urlopen(req, timeout=3).read()


def launch(dev: RokuDevice, app_id: str, content_id: str = "") -> None:
    url = f"{dev.base}/launch/{app_id}"
    if content_id:
        from urllib.parse import quote
        url += f"?contentId={quote(content_id)}"
    req = Request(url, data=b"", headers={"User-Agent": "tclcast"})
    urlopen(req, timeout=3).read()


def _parse_xmlish(text: str) -> dict:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if "<" in line and ">" in line and "=" not in line:
            try:
                tag = line[1:line.index(">")]
                val = line[line.index(">") + 1: line.rindex("<")]
                out[tag] = val
            except (ValueError, IndexError):
                pass
    return out


def _parse_apps(text: str) -> list[dict]:
    apps: list[dict] = []
    cur: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("<app"):
            cur = {}
            for part in line.split():
                if "=" in part and part.count('"') >= 2:
                    k, v = part.split("=", 1)
                    cur[k] = v.strip('"')
        elif line.startswith("</app>") and cur:
            apps.append(cur)
            cur = {}
    return apps
