<div align="center">

# TCLcast

### MagiConnect for Linux

![ícone do tclcast](docs/tclcast.png)

Cliente **Linux** de casting para TV, reescrito a partir do app Android
**MagiConnect / T-Cast** (`com.magiconnect.tcast` v2.0.201) através de
engenharia reversa com REA + JADX.

</div>

GUI em **Python + PySide6**. Análise e spec em [`SPEC.md`](SPEC.md).

## O que faz

- **Descoberta de TVs** — protocolo proprietário TCL (UDP 6537) + DLNA/UPnP (SSDP).
- **Controle remoto** — direcional, OK, volume, mudo, canais, power (com
  confirmação), source, teclas de cor, **menu rápido da TV** (☰ brilho/imagem/som).
- **Reconexão automática** — guarda o último IP e reconecta sozinho ao abrir;
  heartbeat mantém a conexão viva e detecta conexão zumbi (beat sem resposta em
  10s → derruba e reconecta sozinho; vale para **todos** os comandos).
- **Fila de reprodução local** — o PC vira servidor HTTP (porta 7983, com
  Range/206) e a TV baixa, igual ao app original. Escolha **vários arquivos**:
  tocam em sequência com **avanço automático**, faixa anterior/próxima,
  play/pause/stop.
- **Player com progresso** — barra de posição/duração real da TV (poll 145/146/147),
  seek arrastando a barra ou ±10s, play/pause com estado ao vivo.
- **Digitar na TV** — campo de texto no PC → envia para a TV (busca, login, etc.).
- **Apps da TV (popup)** — lista os apps instalados (223>>GET), **abrir** e
  **desinstalar** com confirmação, + armazenamento da TV.
- **Captura de tela** — salva em `screenshots/` (dev) ou na pasta de imagens do
  usuário (instalado), JPEG 1080p, com atalho para abrir a pasta.
- **Controle rápido** — atalho para controles resumidos que conseguem ser acessados no icone da barra de tarefas
- **Início automático** — opção nas Configurações (engrenagem) para abrir junto
  com o sistema, já na bandeja.
- **Multprotocolo** — TVs TCL (protocolo próprio), qualquer TV **DLNA**, e **Roku**
  (ECP porta 8060).

## Requisitos

- Linux **x86_64** — Zorin/Ubuntu 20.04+, Debian 11+ e mais novos (qualquer
  distro com glibc ≥ 2.28). Nada além disso: Python, PySide6 e ffmpeg já vão
  embutidos nos pacotes.
- TV na mesma rede do PC.
- Para DLNA: TV com "DLNA renderer"/"Screen sharing" ativado.

## Baixar

O pacote `.deb` mais recente está nos **Releases**:
https://github.com/Cliffordreis/TCLcast-magiConnect-for-linux-/releases

Baixe o `tclcast_1.0.3_amd64.deb` e instale:

```bash
sudo apt install ./tclcast_1.0.3_amd64.deb
```

- Ubuntu / Zorin / Debian 11+ — o pacote já traz Python, PySide6 e ffmpeg
  embutidos (nada a instalar depois).
- Instala em `/opt/tclcast`, cria o atalho no menu de aplicativos e o autostart
  em `~/.config/autostart/`; desinstala com `sudo apt remove tclcast`.

Fluxo: abra o app → **Configurações** (engrenagem) → *Iniciar junto com o
computador (na bandeja)*. O **X da janela encerra o app** — para ele seguir na
bandeja ao fechar, marque *Continuar na bandeja ao fechar a janela* nas
Configurações. Ao clicar no ícone da bandeja, o **Controle rápido** aparece com
animação junto ao ícone — painel em formato de controle (teclas de sistema,
direcional em cruz, volume/canais e atalhos: Captura, Apps, Espelhar, App
completo e Sair do tclcast), arrastável e sempre por cima.

Fluxo: **Procurar TVs** → selecione o dispositivo → conecte → use o remote,
o player, o campo de texto, **Enviar arquivos…** (fila) ou "Apps da TV".
Também dá para adicionar um **IP manual** (para TVs TCL que não respondem ao
broadcast).

## Desenvolvimento (código-fonte)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m tclcast            # abre a janela
.venv/bin/python -m tclcast --tray     # inicia só na bandeja
```

(`./tclcast.sh` faz o mesmo, criando o venv na primeira execução.)

Para gerar os pacotes:

```bash
.venv/bin/python packaging/build.py    # dist/tclcast_<versao>_amd64.deb
```

O build usa um **CPython portátil** (python-build-standalone) + PySide6
manylinux_2_28 e embute o **ffmpeg estático** — o resultado roda com **glibc
≥ 2.28** sem instalar dependências. O `.deb` é o formato principal;
`--appimage`/`--both` ainda geram o AppImage (opcional/legado).

## Arquitetura

```
tclcast/
├── protocol/           # camada de rede (sem GUI)
│   ├── tcl_const.py    # constantes do protocolo TCL (códigos, teclas, portas)
│   ├── tcl_discovery.py# descoberta UDP 6537 (IPMsg)
│   ├── tcl_client.py   # cliente TCP 6553, framing int32-BE + UTF-8
│   ├── dlna.py         # SSDP + SOAP AVTransport/RenderingControl
│   └── roku.py         # ECP porta 8060
├── core/
│   ├── device.py       # abstração unificada (TCL | DLNA | Roku)
│   ├── discovery.py    # varredura em background
│   ├── media_server.py # servidor HTTP local (porta 7983, com Range/206)
│   ├── screen_mirror.py# espelhamento → HLS (X11: x11grab; Wayland: portal)
│   ├── portal_capture.py # helper do xdg-desktop-portal (roda no python do sistema)
│   └── autostart.py    # início automático + atalho no menu (XDG)
└── ui/
    ├── icons.py        # ícones SVG (renderizados para QIcon)
    ├── theme.py        # tema escuro (QSS)
    ├── quick_remote.py # painel "Controle rápido" (bandeja)
    └── main_window.py  # GUI PySide6, bandeja e configurações

packaging/
└── build.py            # gera o .deb (CPython portátil + PySide6; AppImage opcional)
```

## Limitações e escopo

Reescrito do zero com base no comportamento observado. **Não** inclui (por serem
serviços proprietários da TCL / de terceiros):

- Pareamento por QR na nuvem TCL, Firebase/FCM, anúncios, Google Play Billing,
  Google Cast SDK licenciado, catálogo de vídeos online proprietário.

**Espelhamento de tela (X11 e Wayland)**: em sessão **X11** o botão "Espelhar
tela do PC" usa ffmpeg + `x11grab` direto. Em **Wayland** ele usa o portal do
GNOME (`xdg-desktop-portal` → PipeWire → GStreamer → ffmpeg → HLS): na primeira
vez o GNOME pede para escolher a tela e clicar em "Compartilhar"; o token fica
salvo (`~/.config/tclcast/portal_token`) e não pede de novo. O stream sai em
1080p nítido com ~3 s de atraso (piso do HLS no player da TV) e **sem
anúncio** (reprodução como tipo "Video", não "m3u8"). Detalhes técnicos
(quirks do gstreamer1.0-pipewire 1.0.5, `restore_token`, janela X11 oculta para
o `parent_window`) em [`SPEC.md`](SPEC.md) (§espelhamento).

Protocolo detalhado e tabela de comandos: ver [`SPEC.md`](SPEC.md).

## Desenvolvimento com IAs

Este projeto foi desenvolvido com auxílio de IAs generativas como ferramentas de
pair programming e revisão de código:

- **Longcat 2.5** (Xiaomi) — análise de engenharia reversa, design da GUI e
  implementação inicial dos protocolos
- **MiMo 2.6** (Xiaomi) — modelo principal na maior parte das iterações:
  implementação do painel, empacotamento, debug e validação
- **DeepSeek 4.1 Flash** — consultas rápidas de documentação e exemplos de API
  Qt/Python

Nenhuma chave de API, dado de rede pessoal ou credencial é armazenada neste
repositório (ver auditoria de privacidade abaixo).

## Auditoria de privacidade

Verificado manualmente e via varredura automatizada antes da publicação:

- Nenhum IP real de rede local (o único `192.168.0.10` presente é um
  *placeholder* de exemplo no campo "IP manual" da GUI)
- Nenhum caminho absoluto de `/home/<usuário>` ou outro dado pessoal
- Nenhuma chave de API, token, senha ou credencial
- Nenhum hostname, MAC address ou identificador de dispositivo
- `dist/`, `packaging/cache/`, `.venv/` e `squashfs-root/` ignorados via
  `.gitignore` (artefatos de build binários)
