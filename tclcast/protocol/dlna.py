"""Descoberta e controle DLNA/UPnP (MediaRenderer).

Implementa SSDP (M-SEARCH) e ações SOAP de AVTransport/RenderingControl,
permitindo enviar mídia e controlar qualquer TV com DLNA — não só TCL.
"""
from __future__ import annotations

import socket
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.request import Request, urlopen

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
MEDIA_RENDERER = "urn:schemas-upnp-org:device:MediaRenderer:1"

SOAP_ENVELOPE = """<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
 s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
<s:Body><u:{action} xmlns:u="{service}">{args}</u:{action}></s:Body>
</s:Envelope>"""

AVTRANSPORT = "urn:schemas-upnp-org:service:AVTransport:1"
RENDERING = "urn:schemas-upnp-org:service:RenderingControl:1"


@dataclass
class DlnaDevice:
    friendly_name: str
    location: str          # URL do description.xml
    usn: str
    st: str
    ip: str
    control_url: str = ""
    ev_url: str = ""
    service_type: str = AVTRANSPORT

    @property
    def uuid(self) -> str:
        return self.usn.split("::")[0].replace("uuid:", "")


def discover(timeout: float = 3.0) -> list[DlnaDevice]:
    """Procura MediaRenderers via SSDP M-SEARCH."""
    search = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
        'MAN: "ssdp:discover"\r\n'
        f"MX: {int(timeout)}\r\n"
        f"ST: {MEDIA_RENDERER}\r\n\r\n"
    ).encode("utf-8")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    devices: dict[str, DlnaDevice] = {}
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
        sock.settimeout(timeout)
        sock.sendto(search, (SSDP_ADDR, SSDP_PORT))
        deadline = timeout
        import time
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                break
            text = data.decode("utf-8", errors="ignore")
            headers = _parse_ssdp(text)
            loc = headers.get("location")
            usn = headers.get("usn", "")
            if not loc or usn in devices:
                continue
            dev = DlnaDevice(
                friendly_name=headers.get("server", "DLNA Device"),
                location=loc, usn=usn, st=headers.get("st", MEDIA_RENDERER),
                ip=addr[0],
            )
            _fill_control_urls(dev)
            devices[usn] = dev
    finally:
        sock.close()
    return list(devices.values())


def _parse_ssdp(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.split("\r\n")[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _fill_control_urls(dev: DlnaDevice) -> None:
    """Baixa o description.xml e extrai controlURL de AVTransport."""
    try:
        req = Request(dev.location, headers={"User-Agent": "tclcast/0.1"})
        with urlopen(req, timeout=3) as r:
            xml = r.read()
        root = ET.fromstring(xml)
        ns = {"d": "urn:schemas-upnp-org:device-1-0"}
        dev.friendly_name = _text(root, ".//d:friendlyName", dev.friendly_name)
        for svc in root.iter("{urn:schemas-upnp-org:service-1-0}service"):
            stype = _text(svc, ".//{urn:schemas-upnp-org:service-1-0}serviceType", "")
            ctrl = _text(svc, ".//{urn:schemas-upnp-org:service-1-0}controlURL", "")
            if "AVTransport" in stype:
                dev.control_url = _abs(dev.location, ctrl)
                dev.service_type = stype
            elif "RenderingControl" in stype and not dev.control_url:
                dev.control_url = _abs(dev.location, ctrl)
                dev.service_type = stype
    except Exception:
        pass


def _text(root, path: str, default: str) -> str:
    el = root.find(path, {"d": "urn:schemas-upnp-org:device-1-0"})
    return el.text.strip() if el is not None and el.text else default


def _abs(base: str, rel: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base, rel)


def _soap_call(dev: DlnaDevice, service: str, action: str,
               args: dict[str, str]) -> str:
    arg_xml = "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
    body = SOAP_ENVELOPE.format(action=action, service=service, args=arg_xml)
    req = Request(dev.control_url, data=body.encode("utf-8"), headers={
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPAction": f'"{service}#{action}"',
        "User-Agent": "tclcast/0.1",
    })
    with urlopen(req, timeout=5) as r:
        return r.read().decode("utf-8", errors="ignore")


class DlnaControl:
    """Controle de um MediaRenderer DLNA."""

    def __init__(self, dev: DlnaDevice):
        self.dev = dev

    def set_uri(self, url: str, title: str = "") -> None:
        meta = (
            '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
            f'<item id="0" parentID="-1" restricted="1">'
            f"<dc:title>{title}</dc:title>"
            "<upnp:class>object.item.videoItem</upnp:class>"
            f"<res protocolInfo=\"http-get:*:video/mp4:*\">{url}</res>"
            "</item></DIDL-Lite>"
        )
        _soap_call(self.dev, AVTRANSPORT, "SetAVTransportURI",
                   {"InstanceID": "0", "CurrentURI": url, "CurrentURIMetaData": meta})

    def play(self) -> None:
        _soap_call(self.dev, AVTRANSPORT, "Play", {"InstanceID": "0", "Speed": "1"})

    def pause(self) -> None:
        _soap_call(self.dev, AVTRANSPORT, "Pause", {"InstanceID": "0"})

    def stop(self) -> None:
        _soap_call(self.dev, AVTRANSPORT, "Stop", {"InstanceID": "0"})

    def seek(self, seconds: float) -> None:
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        _soap_call(self.dev, AVTRANSPORT, "Seek",
                   {"InstanceID": "0", "Unit": "ABS_TIME",
                    "Target": f"{h:02d}:{m:02d}:{s:02d}.000"})

    def set_volume(self, level: int) -> None:
        _soap_call(self.dev, RENDERING, "SetVolume",
                   {"InstanceID": "0", "Channel": "Master",
                    "DesiredVolume": str(max(0, min(100, level)))})

    def set_mute(self, mute: bool) -> None:
        _soap_call(self.dev, RENDERING, "SetMute",
                   {"InstanceID": "0", "Channel": "Master",
                    "DesiredMute": "1" if mute else "0"})
