# tclcast — Especificação do protocolo (engenharia reversa do MagiConnect 2.0.201)

Alvo analisado: `com.magiconnect.tcast` v2.0.201 (versionCode 202010), APK SHA-256
`3b2e7db0694dafe8bcd1e243b3f9bd0d604d222d31cb30608c5a49319253dd4a`.
Ferramenta: REA + JADX 1.5.6 (25.367 classes decompiladas).

O app usa **duas pilhas** de casting: um protocolo **proprietário TCL** (tcastsdk)
e uma camada de **protocolos abertos** (ConnectSDK).

---

## 1. Protocolo proprietário TCL (tcastsdk)

### 1.1 Descoberta (UDP)
- Porta: **6537** (`Config.broadcastPort` e `broadcastMonitorPort`, ambos 6537 em
  `TCLDeviceManager` linha 232–233).
- Mensagem no formato IPMsg (`IpMessageProtocol`):
  `version:packetNo:senderName:senderType:commandNo:additionalSection`
  - `packetNo` = timestamp em milissegundos (string).
  - `senderName` usa `&#058` para escapar `:`.
- Comandos UDP relevantes (`IpMessageConst`): `IPMSG_BR_ENTRY=1`, `IPMSG_ANSENTRY=3`,
  `IPMSG_BR_EXIT=2`, `ISONLINE=150`.
- Broadcast para `broadCastHostInWifi` e `broadCastHostInAp` (gateway DHCP) na porta 6537.

### 1.2 Conexão de comandos (TCP)
- Porta: **6553** (`IpMessageConst.PORT_COMMAND`).
- Handshake (`ProtocolHandlerV6.handleSocketConnectedEvent`): envia comando **159**
  (`GET_CLIENTTYPE`) com payload `159>>{phonename}>>1>>{uuid}>>1>>1`.
  A resposta 159 marca `hasHandshake = true`.
- Versão de protocolo < 6 usa `ProtocolHandlerV5`; >= 6 usa `V6`.
  O `protocolVersion` vem do anúncio UDP (`TCLDeviceInfo.getProtocolVersion()`).

### 1.3 Framing TCP (`AbstractPacketWriter.writeMyUTF` / `PacketReader.readMyUTF`)
- Envelope: **4 bytes int big-endian = comprimento** seguidos do payload UTF-8.
  - `writeInt(length)` (big-endian) + `write(bytes, 0, length)`.
- Payload opcionalmente **AES** criptografado quando `algorithmType == 1` e
  `NativeUtil.getKey() != null` (`SecurityUtil.AES.encrypt`, chave UTF-8). Numa
  implementação limpa, tratar como "sem criptografia" por padrão e suportar o modo
  claro.
- Máx. 1 MiB por pacote (`MAX_READ_LENGTH = 1048576`).
- **Heartbeat (crítico p/ manter a conexão viva):**
  - O **cliente deve enviar `150>>` proativamente** quando o writer fica ocioso
    por ~5s (`AbstractPacketWriter.consume()` → `poll(5000)` → `onWriterIdle()`
    → `TCLDevice.heartbeat()` → `HeartbeatRequest` = `"150>>"`).
  - Enviado **cifrado** após handshake (`setIgnoreAlgorithmType(false)`).
  - A TV **responde ao beat com `150>>YES`** (verificado ao vivo 17/10/2026).
  - **NUNCA responder a uma mensagem `150>>...` vinda da TV**: ela É a resposta
    ao nosso beat. Responder `150>>1>>1` faz a TV tratar a resposta como um novo
    pedido, gerando um **loop infinito de beeps** (~100 msg/s — bug real do
    tclcast, corrigido em 17/10/2026; degradava o canal de texto da TV depois
    de um tempo, até reconectar na mão).
  - Sem **nenhum** sinal da TV após o beat: a conexão está **zumbi** (TV
    dormiu/trocou de rede). O app original desconecta após 20s
    (`onBeatTimeout` → `disConnect(2002, "beatTimeout")`); o tclcast usa 10s
    (`_BEAT_TIMEOUT`), derruba o socket e o watchdog da GUI reconecta sozinho —
    **regra universal para todos os comandos** (teclas, texto, mídia).
  - No app original, qualquer pacote recebido completa o heartbeat pendente
    (`PacketReader.responseAllHeartBeat()`); no tclcast, qualquer frame
    recebido atualiza `_last_rx` (mesmo efeito).
  - Sem heartbeat ativo, **a TV derruba o socket após ~1–2 min de inatividade**
    (Errno 32 "pipe quebrado" no próximo envio).

### 1.4 Payload dos comandos
Separador: **`>>`** (`CommonProxy.gap`).
Formato: `cmd>>arg1>>arg2...` (`TCLCommand.pack()`).
Resposta da TV pode ser `cmd>>...` ou JSON `{"cmd":N,...}` / `{"response_code":N,...}`
(`ProtocolHandlerV6.handleSocketMessage`).

### 1.5 Códigos de comando (`IpMessageConst`)
Controle:
- `KEY = 149` → `149>>{keyCode}` (RemoteControlKeyCmd)
- `INPUTSTR = 155` (texto), `VOICE_INPUT = 157`, `VOICE_INPUT_STRING = 233`
- `SET_SYSTEM_VOLUME = 184`, `GET_SYSTEM_VOLUME = 183`
- `MEDIA_SET_VOLUME = 130` → `130>>{volume}`
- `MEDIA_SET_MUTE = 131` → `131>>{isMute}`
- `MEDIA_GET_VOLUME = 136`, `MEDIA_GET_MUTE = 140`

Mídia (valores **validados na TV real TCL-LA-TC8000-S1**):
- `MEDIA_STOP = 129` → `129>>` (para o player)
- `MEDIA_SET_VOLUME = 130` → `130>>{volume}`; `MEDIA_SET_MUTE = 131` → `131>>{true|false}`
- `MEDIA_SEEK = 132` → `132>>{ms}` (**milissegundos**, `SeekToCmd.position`)
- `PreparePlay = 133` → `133>>{type}` (type ∈ {Video, Audio, Image, m3u8, share})
- `PlaySingle = 134` → `134>>{info}` — **info usa separador `->`, 8 campos**
  (`VideoInfo.getInfo()`): `url->title->player->album->coverPath->isPortrait->width->height`
  com defaults `"null"`/`false` quando só url/title são preenchidos.
  **Retomar** play pausado = `134>>` **com info vazio** (`TCLVideoPlayerProxy.start()`).
- `Pause = 135` → `135>>` (`PauseCmd`)
- `MEDIA_GET_PLAY_STATE = 139` → resposta **`145>>{STATE}`**
  (PLAYING / PAUSED_PLAYBACK / STOPPED / COMPLETED / TRANSITIONING / …;
  quando **ociosa** a TV responde `TRANSITIONING` sem player ativo)
- `MEDIA_GET_MEDIA_DURATION = 141` → resposta `146>>{ms}`
- `MEDIA_GET_CUR_PLAY_POSITION = 142` → resposta `147>>{ms}`
- `MEDIA_GET_CURRENT_TRANSPORT_ACTIONS = 143` → resposta `148>>{PLAY|…}`
- `MEDIA_PLAY_PREVIOUS = 176`, `MEDIA_PLAY_NEXT = 177` (testados ao vivo: sem
  playlist no player da TV, os dois não têm efeito — no app original a fila é
  gerenciada pelo **celular**; o tclcast toca vários arquivos em sequência pelo
  PC e troca de faixa reenviando o play)
- `MEDIA_TENTPLAY = 224` → `5>>{type}>>>>>>{json}` (json: name/url/webUrl)
- `GET_CLIENTTYPE = 159` (handshake)

Consulta com wrapper: consultas como `183`/`184`/`242`/`186` respondem no formato
**`1000>>{cmd}>>{valor}`** (ex.: `1000>>183>>unknown`). Consultas de player (139/141/142)
respondem direto sem o wrapper `1000`.

Texto na TV (VERIFICADO AO VIVO em 17/10/2026, navegador BrowseHere com campo
de URL focado e teclado da TV aberto):
- `INPUTSTR = 155` → `155>>{texto}` (`RemoteControlInputCmd`) — formato **idêntico**
  ao MagiConnect (`TCLRemoteControlProxy.sendInputStr`). **FUNCIONA**.
  Semântica descoberta empiricamente:
  1. cada `155` **substitui** o conteúdo do campo focado pelo texto enviado
     (não anexa, não digita "letra a letra") — por isso o app envia sempre o
     texto completo a cada edição;
  2. o canal de entrada da TV **"esfria"**: a **primeira** mensagem `155` de
     cada sessão TCP **ou após ~1 min sem texto** é **descartada** pela TV
     (testado: single após 1 min e após 3 min na mesma sessão não aplica;
     com a mensagem seguinte aplica). Por isso `send_text()` envia o **mesmo
     texto duas vezes** (com ~0,3 s): a 1ª cópia abre o canal (ou aplica, se
     já estava aberto — a substituição é idempotente), a 2ª garante.
     (O bug original "o teclado não envia letras" era a 1ª mensagem engolida
     numa conexão recém-aberta; o "depois de um tempo não pega mais" era o
     canal frio — o warm-up único por sessão não cobria.)
  3. DEGRADAVA "depois de um tempo": o cliente antigo respondia `150>>1>>1` à
     resposta do heartbeat da TV, criando um **loop infinito de beeps**
     (~100 msg/s) que sobrecarregava a TV. Nunca responder `150>>...` da TV
     (ver §1.3); corrigido em 17/10/2026.
  No IME do Google TV (busca do YouTube) a TV recebe o comando mas o campo
  não aceita texto externo — limitação do receptor; o MagiConnect falha igual.
- `KeyboardInputCmd = 249` → `249` (sem payload) → **não existe nesta TV**:
  a TV responde `1000>>249>>unknown`. Não é necessário para digitar.
- `VOICE_INPUT_STRING = 233` → `233pcm_text>>{texto}` (`VoiceControlStringCmd`)
  (sem `>>` depois do 233) — não testado a fundo; não é necessário para digitar.
- Referência MagiConnect (decompilado): `CustomDialog` chama `onTextChanged` a
  cada tecla e envia `155>>{texto acumulado completo}`; ENTER do diálogo envia
  `enterKeyAction()` = `149>>68`.

AirMouse (não confirmado nesta TV):
- `MOUSE = 151` → `151>>{x}>>{y}` (`RemoteControlMouseCmd`) — aceito sem erro,
  mas sem cursor visível no home desta TV.

Armazenamento:
- `TV_STORAGE = 239` → `239>>` responde **`239>>0>>{total_mb}>>{livre_mb}`** (ex.: `239>>0>>8460>>2341`).

Brilho: `SET_BRIGHTNESS = 280` → `280>>{tag}` (`TCLRenoteSetBrightCmd`, não testado).

### 1.6 Teclado remoto (`TR_KEY_*`, mapeados para `KEY=149`)
```
UP=11 DOWN=12 LEFT=13 RIGHT=14 OK=15 BACK=16 MENU=19 POWER=20
VOL_UP=21 VOL_DOWN=22 MUTE=23 CH_UP=27 CH_DOWN=28 SOURCE=29
HOME(mainmenu)=19 SUBMENU=18 ENTER=68 PLAYBACK=26 DISPLAY=25 EPG=24
RED=34 GREEN=35 YELLOW=36 BLUE=37 BACKSPACE=38
MOUSELEFT=39 MOUSERIGHT=40 INFOWINDOW=41 SMARTTV=45
0..9 = 10..1 (TR_KEY_0..TR_KEY_9 → valores 10,1..9)
```

Teclas validadas ao vivo: `149>>18` (**SUBMENU**) abre o **menu rápido do
Google TV** (brilho, pré-definição de imagem, som, saída de áudio, rede) —
é o botão ☰ do tclcast; `BACK=16` fecha. `MENU=19` (TR_KEY_MAINMENU) é outra
tecla (não é o menu rápido).

### 1.7 Servidor de mídia local (HTTP)
- `TCLFileServer`/`NanoHTTPD` roda no **celular** na porta **7983**
  (`IpMessageConst.HTTPSERVER_PORT`). A TV faz download do arquivo local por HTTP
  (`http://{phone_ip}:7983/...`).
- `GET_TVVERSION_PORT = 6550`.

**Validado ao vivo (o player da TV usa `stagefright/1.2 (Linux;Android 12)`):**
- A TV envia **`GET` com `Range: bytes=0-`** e exige resposta **206 Partial Content**
  com `Content-Range` — sem isso o player aborta (BrokenPipe) e a TV mostra
  *"não foi possível reproduzir este recurso de mídia"*.
  `TCLFileServer.serveFile` também responde 206 (e adiciona `ETag`).
- A TV baixa o arquivo **inteiro** antes de tocar (1 request por reprodução).
- **Codec obrigatório:** H.264 **`profile high`, `pix_fmt yuv420p`** (+ AAC, `-movflags +faststart`).
  Vídeo gerado em `yuv444p`/High 4:4:4 é **rejeitado** pelo decoder da TV
  (falha silenciosa: estado 145>>TRANSITIONING→STOPPED em ~3 s).
- Fluxo de play validado (`TCLVideoPlayerProxy.play`):
  `129>>` (stop) → `133>>Video` → `134>>{info com "->"}`
  → eventos `145>>TRANSITIONING` → `145>>PLAYING`.
  Respostas de posição/duração em **milissegundos** (`147>>pos`, `146>>dur`).

### 1.8 Captura de tela da TV (ShotPic, cmd 225)
Fluxo do `TCLShotPicProxy` (testado contra a TV real TCL-LA-TC8000-S1 v14):

1. `isSupportLink()` = `protocolVersion > 1` → para TVs modernas:
   - Envia `ShotPicCmd` = **`225>>`** pela porta 6553 (cifrado).
   - TV responde **`225>>{code}>>{url}`**:
     `0`=ok, `1`=fail, `2`=não suporta, `3`=armazenamento cheio, `4`=direitos autorais.
   - Em sucesso a url é `http://{tv_ip}:{porta_aleatória}/data/user_de/0/
     com.tcl.ttvs/files/screenShot/{n}.PNG`.
2. Download HTTP direto (`DownloadPicThread`: `URLConnection`, sem headers).

**Peculiaridades reais (importantes para implementação):**
- O **servidor HTTP da TV é efêmero**: a 1ª captura costuma falhar com
  *connection refused* (a porta da url ainda não está escutando). Solução:
  **repetir o cmd 225** (nova url/porta) até o download dar certo — o app
  original não trate isso, mas na prática funciona pois o servidor "acorda"
  e passa a servir as capturas seguintes.
- O arquivo baixado é **JPEG mesmo com sufixo `.PNG`** na url
  (magic `FF D8 FF E0 … JFIF`). Detectar o formato real pelos magic bytes
  (PNG `89 50 4E 47` / JPEG `FF D8 FF`) e usar a extensão correta.
- Tamanho típico: ~0,9–1,1 MiB, 1920×1080.

**Caminho alternativo (TVs antigas, `protocolVersion <= 1`):**
`TVScreenShotThread` — socket direto na **porta 6554**:
```java
socket.connect(ip, 6554);        // connect timeout 5s, soTimeout 10s
writeMyUTF(dos, "JSReq");        // framing: 4B BE length + "JSReq"
int len = dis.readInt();         // 4B BE = tamanho do PNG
byte[] img = new byte[len];      // lê exatamente len bytes → imagem pura
```
(Nessa TV a porta 6554 fica fechada — só TVs antigas usam.)

### 1.9 Gerenciador de apps da TV (cmd 223, `AppManagerProxy`)
Comandos do cliente (`AppManagerCmd` = `223>>{info}`), **validados na TV real**:

| Ação | Payload | Observação |
|---|---|---|
| Listar | `223>>GET` | TV responde `223>>GET>>{app}>…` |
| Abrir | `223>>OPEN>>{pkg}` | |
| Remover | `223>>UNINSTALL>>{pkg}>>{nome}` | TV pode pedir confirmação local |
| Info | `223>>PACKAGEINFO>>{pkg}` | não testado |
| Instalar | `223>>INSTALL>>{pkg}::{nome}::{url}::…` (`AppItem.getInstallStr`, sep. `::`) | fora de escopo |

Formato da resposta de **lista** (testado: **22 apps**, payload ~6 KiB):
```
223>>GET>>{pkg}::{nome}::{iconUrl}::{code}::{versão}::{size1}::{size2}::{size3}::{type}>…
```
- campos separados por **`::`**, apps separados por **`>`**;
- `iconUrl` = `http://{tv_ip}:{porta_aleatória}/…/screenShot/…png` (ícone do app);
- parser em `TclClient._parse_apps()`.

Relacionados: `TV_APP_PARAMS = 252` (`StartOpenOrInstallAppCmd` = `252>>{json}`),
`INTENT_LAUNCHER_CMD = 272` (`RemoteLauncherAppCmd` = `272>>{appJson}`),
`QUERY_RECENT_APP_CMD = 273` (sem resposta nesta TV).

---

## 2. Protocolos abertos (ConnectSDK, biblioteca MIT)

Disponíveis no app (classes `com.connectsdk.service.*`):
- **DLNA / UPnP** (`DLNAService`, `SSDPDiscoveryProvider`):
  - Descoberta SSDP `M-SEARCH * HTTP/1.1`, `ST: urn:schemas-upnp-org:device:MediaRenderer:1`,
    notificações `ssdp:alive` / `ssdp:byebye`.
  - Serviços: `AVTransport`, `RenderingControl`, `ConnectionManager`.
  - Ações: SetAVTransportURI, Play, Pause, Stop, Seek, GetPositionInfo,
    SetVolume, GetVolume, SetMute.
- **DIAL** (`DIALService`): `urn:dial-multiscreen-org:service:dial:1` — lançar apps
  (YouTube etc.) por HTTP.
- **Roku ECP** (`RokuService`): porta **8060** — `/keypress/{key}`, `/launch/{app}`,
  `/query/apps`, `/query/device-info`, `/query/icon/{id}`.
- **WebOS TV** (`WebOSTVService`), **FireTV** (`FireTVService`),
  **Chromecast** (`CastService`), **Netcast LG** (`NetcastTVService`).
- **AirPlay** (`service/airplay`, com SRP auth) — só iOS/Apple TV.

---

## 3. Mapeamento para a versão Linux (tclcast)

| Recurso do app | Implementação Linux |
|---|---|
| Descoberta TCL | UDP 6537 (protocolo IPMsg acima) |
| Descoberta TV genérica | SSDP (UPnP) + mDNS/Zeroconf |
| Conexão/comandos TCL | TCP 6553, framing int32-BE + UTF-8 |
| Controle remoto | `KEY=149` + códigos TR_KEY_* |
| Mídia local | HTTP server local (porta 7983, **com Range/206**) + `129/133/134` |
| Player com progresso | `139/141/142/143` → `145/146/147/148`, seek `132>>{ms}`, pause `135`, retomar `134>>` |
| Digitar na TV | `155>>{texto}` (`INPUTSTR`) |
| Apps da TV (popup) | `223>>GET/OPEN/UNINSTALL` (lista, abrir, remover) |
| Armazenamento da TV | `239>>` → `239>>0>>{total}>>{livre}` MB |
| Menu rápido da TV | `149>>18` (TR_KEY_SUBMENU — brilho/imagem/som/rede) |
| Fila de reprodução | playlist gerenciada pelo PC; fim de faixa detectado pela queda de duração/posição (`145/146/147`) → próximo arquivo |
| Volume/mute | `MEDIA_SET_VOLUME=130`, `MEDIA_SET_MUTE=131` |
| TVs não-TCL | DLNA/UPnP (AVTransport/RenderingControl) + Roku ECP + DIAL |
| Espelhamento de tela | X11: `x11grab`; Wayland: portal GNOME → PipeWire → gst → ffmpeg → HLS |

### Espelhamento de tela (implementado para X11 e Wayland)
Sessão testada: **Wayland** (GNOME 46 / Zorin 18.1, `DISPLAY=:0` XWayland),
ffmpeg 6.1.1, GStreamer 1.24.2 (`gstreamer1.0-pipewire` 1.0.5), PipeWire 1.0.5.

- **X11**: ffmpeg + `x11grab` direto, capturando a **tela inteira** (sem
  `-video_size` o tamanho vem do XRandR) com redução a no máximo 1920 de
  largura.
- **Wayland**: `tclcast/core/portal_capture.py` (roda com o python do **sistema**,
  precisa de `python3-gi`) faz o fluxo completo e entrega NV12@30fps no stdout:
  1. `org.freedesktop.portal.ScreenCast`: `CreateSession` → `SelectSources`
     (`types=MONITOR`, `cursor_mode=2`, `persist_mode=2`) → `Start`.
     Detalhes que importam:
     - o 1.18.x devolve **request handle** no CreateSession; o session handle
       vem no sinal `Response` — inscrever no **caminho previsto**
       (`/request/{nome-único}/{handle_token}`) **antes** da chamada evita
       perder a resposta;
     - `parent_window` precisa ser uma **janela X11 oculta** (`x11:0x{id}`,
       GDK_BACKEND=x11) — o xdg-desktop-portal-gnome 46.2 sofre SEGV com parent
       vazio (flameshot #4663);
     - `persist_mode=2` é o que faz o GNOME devolver o `restore_token`
       (guardado em `~/.config/tclcast/portal_token`) — depois da 1ª
       autorização não pede mais o diálogo.
  2. O `pipewiresrc` do gstreamer1.0-pipewire 1.0.5 **não consegue mirar o nó
     pela conexão fd do portal** (com `fd=` + `path=id`/`target-object=serial`
     dá `stream error: target not found`; com `fd=` sem target o WirePlumber
     **não cria o link** — fica sem dados, sem erro). O que funciona é uma
     **conexão normal** com `target-object=<serial do nó>` (obtido via
     `pw-dump` pelo id devolvido no `Start`; fallback: nome do nó — ex.:
     `gnome-shell`) — aí o WirePlumber faz o link normalmente.
  3. `pipewiresrc target-object=… do-timestamp=true keepalive-time=100 !
     queue(leaky) ! videoconvert ! videorate ! video/x-raw,NV12,WxH,30/1 !
     fdsink` → `ffmpeg -f rawvideo -pix_fmt nv12` no **tamanho nativo** (só
     reduz se passar de 1920 de largura).
  4. **A TV reproduz com o tipo "Video"** — não "m3u8"! O tipo `m3u8` abre o
     player de vídeo online da TCL (`TvPlayDetailViewModel`/ottera, com a
     biblioteca de anúncios `ads.interactivemedia` embutida) e mostra um
     **anúncio pré-roll** antes do stream; o tipo "Video"
     (`TCLVideoPlayerProxy.play()`) usa o player normal, sem anúncio — é o
     mesmo caminho dos arquivos locais.
  5. Bloco HLS (compartilhado X11/portal): x264 `veryfast` + `tune
     zerolatency` + `crf 20` + `-maxrate 12M`, keyframe a cada 0,5 s
     (`-g fps/2`, `-sc_threshold 0`), segmentos de 0,5 s e janela de 4
     segmentos (`-hls_time 0.5 -hls_list_size 4` + `delete_segments+
     omit_endlist`). Segmentos curtos + janela pequena = menos atraso.
  6. `ScreenMirror` fica em modo portal com acompanhamento por polling
     (`ready` só fica True com segmento **não-vazio** na playlist; o ffmpeg
     cria um `.ts` de 0 bytes ao abrir) e a GUI usa um `QTimer` para não
     travar enquanto o diálogo do GNOME está aberto.

Validado ao vivo: captura da tela real → segmentos em ~2 s (Wayland); a TV
reproduz 1080p nítido (texto legível), **sem anúncio** e com **~3 s de
atraso** estável (2,7–3,7 s medidos com um relógio na tela; piso estrutural
do HLS: o player começa ~3 s antes da borda ao vivo).

**Diferenças em relação ao app original (recursos que o MagiConnect não expõe):**
o tclcast implementa player com barra de progresso/seek, fila de reprodução local
com avanço automático, digitação de texto do PC na TV (quando o campo focado
aceita o receptor TCL — ver §Texto na TV), gerenciador de apps
(lista/abrir/remover em popup), armazenamento da TV, menu rápido (☰) e um layout
com ícones. O app original só tem remoto básico + cast de mídia + captura única.

### Bandeja, início automático e empacotamento (v1.0)

- **Bandeja + Controle rápido**: o ícone (`QSystemTrayIcon`) tem menu curto
  (Controle rápido, Abrir o app completo, separador, Configurações, Sair); o
  clique simples abre o **painel `QuickRemote`** — janela sem moldura,
  arrastável, sempre por cima, que entra/sai com animação deslizando a partir
  do ícone (posição ancorada nele: `tray.geometry()` quando disponível, senão
  o cursor). Formato de controle: cabeçalho (power/fechar), teclas de sistema
  (Início/Menu/Voltar/Fonte), direcional em cruz com OK, colunas de volume
  (V+/Mudo/V−) e canal (CH+/CH−) e atalhos (Captura, Apps, Espelhar —
  sincronizado com `ScreenMirror.running` —, App completo e Sair do tclcast;
  sem controles de mídia, que só existem na janela completa). Largura fixa com
  folga exata (324px): menos que isso o `QGridLayout` comprime as colunas
  laterais do direcional de forma assimétrica. Os handlers dos botões são
  embrulhados (`lambda *_, h=handler: h()`) porque o `clicked(bool)` do
  PySide 6.9 vaza o argumento para lambdas com default (bug "bool has no
  attribute strip" que só aparecia no pacote). **O X da janela encerra o app**
  (padrão); a opção "Continuar na bandeja ao fechar a janela" (Configurações →
  `tray_on_close` no `config.json`) faz o X esconder e o app seguir conectado/
  espelhando. "Sair" encerra de verdade e inclui `QApplication.quit()` —
  fechar uma janela OCULTA não dispara `lastWindowClosed` e deixaria o loop
  do Qt preso (bug corrigido na 1.0.2).
- **Encerramento robusto e sem travar**: `_shutdown()` roda cada etapa (parar
  espelhamento, parar o servidor HTTP de mídia, desconectar) isolada e sem
  bloquear a interface; `MediaServer.stop()` encerra numa thread daemon (não
  trava em conexões keep-alive da TV); `TclClient` envia por **fila
  assíncrona** (thread escritora — a GUI nunca faz I/O de rede) e `close()`
  usa `shutdown()` + sentinela, sem joins; `CastDevice.connect()` roda a rede
  fora do lock.
- **Instância única**: `QLocalServer`/`QLocalSocket` (nome `tclcast-{uid}`,
  sobrescrevível por `TCLCAST_SOCKET` em testes). A segunda instância envia
  `show` (deixa a janela visível) ou `hide` (veio do autostart; ignora) e sai.
- **Autostart/menu** (`core/autostart.py`): escreve `~/.config/autostart/` e
  `~/.local/share/applications/tclcast.desktop` com o alvo do modo atual —
  `$APPIMAGE` (AppImage), `sys.executable -m tclcast` (instalado) ou venv +
  `Path=` (dev). Reescrito na abertura (`refresh_shortcuts`) para sobreviver a
  mudanças de pasta do AppImage.
- **Empacotamento** (`packaging/build.py`): `.deb` é o formato principal
  (AppImage opcional/legado via `--appimage`/`--both`) com CPython
  portátil (python-build-standalone) + PySide6-Essentials 6.9
  (manylinux_2_28) + pycryptodome + ffmpeg estático embutido. Piso de
  compatibilidade: **glibc ≥ 2.28** (Ubuntu 20.04+/Debian 11+). O .deb instala
  em `/opt/tclcast` + symlink `/usr/bin/tclcast` (o lançador resolve o próprio
  caminho com `readlink -f`, então funciona chamado via symlink); o AppImage
  usa `AppRun`, `tclcast.desktop` e ícone próprios.
- **Caminhos quando instalado**: capturas em `~/Imagens/tclcast` (pasta de
  imagens XDG); config em `~/.config/tclcast/` como sempre.

### Não reproduzível / fora de escopo
- Serviços de nuvem TCL (`*.leiniao.com`, `*.my7v.com`, `tcloudfamily.com`), QR de
  pareamento na nuvem, Firebase/FCM, anúncios (AdMob/Facebook), Google Play Billing,
  Google Cast SDK licenciado, catálogo de vídeos online proprietário, TikTok/Twitch
  integrados. São serviços proprietários que exigem credenciais/servidores da TCL.
