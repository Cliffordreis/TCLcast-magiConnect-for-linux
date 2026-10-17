"""Constantes do protocolo proprietário TCL (tcastsdk).

Extraídas de com.magiconnect.tcast v2.0.201 via REA + JADX.
Classes fonte: IpMessageConst, IpMessageProtocol, CommonProxy.
"""

# --- Descoberta (UDP) -------------------------------------------------------
UDP_PORT = 6537                 # Config.broadcastPort / broadcastMonitorPort
IPMSG_BR_ENTRY = 1
IPMSG_ANSENTRY = 3
IPMSG_BR_EXIT = 2
ISONLINE = 150

# --- Conexão (TCP) ----------------------------------------------------------
TCP_PORT_COMMAND = 6553         # IpMessageConst.PORT_COMMAND
HTTPSERVER_PORT = 7983          # IpMessageConst.HTTPSERVER_PORT (servidor local do app)
GET_TVVERSION_PORT = 6550

# Separação de campos de payload (CommonProxy.gap)
GAP = ">>"

# --- Códigos de comando -----------------------------------------------------
# Handshake / estado
GET_CLIENTTYPE = 159

# Controle remoto
KEY = 149
INPUTSTR = 155
VOICE_INPUT = 157
VOICE_INPUT_STRING = 233
SET_SYSTEM_VOLUME = 184
GET_SYSTEM_VOLUME = 183

# Mídia
MEDIA_STOP = 129                # MEDIA_STOP
MEDIA_SET_VOLUME = 130
MEDIA_SET_MUTE = 131
MEDIA_SEEK = 132                # SeekToCmd: "132>>{segundos}"
MEDIA_PREPARE_PLAY = 133
MEDIA_PLAY = 134                # PlaySingleCmd
MEDIA_PAUSE = 135
MEDIA_GET_VOLUME = 136
MEDIA_GET_MUTE = 140
MEDIA_GET_MEDIA_DURATION = 141  # → resposta "146>>{segundos}"
MEDIA_GET_CUR_PLAY_POSITION = 142  # → resposta "147>>{segundos}"
MEDIA_GET_PLAY_STATE = 139      # → resposta "145>>{STATE}"
MEDIA_GET_PLAY_STATUS = 138     # → resposta "144>>OK"
MEDIA_GET_CURRENT_TRANSPORT_ACTIONS = 143  # → resposta "148>>{PLAY|...}"
MEDIA_PLAY_PREVIOUS = 176
MEDIA_PLAY_NEXT = 177
MEDIA_TENTPLAY = 224            # play de mídia/URL via JSON

PREPARE_PLAY = 133              # PreparePlayCmd
PLAY_SINGLE = 134               # PlaySingleCmd
STOP = 129                      # StopCmd (alias p/ MEDIA_STOP)
SEEK_TO = 132                   # SeekToCmd (alias p/ MEDIA_SEEK)
SNAP_SHOT = 225                 # ShotPicCmd (captura de tela da TV)

# Estados de player (strings vindas da TV em respostas 145>>{STATE})
STATE_PLAYING = "PLAYING"
STATE_PAUSED = "PAUSED_PLAYBACK"
STATE_STOPPED = "STOPPED"
STATE_COMPLETED = "COMPLETED"
STATE_TRANSITIONING = "TRANSITIONING"
STATE_EXIT = "PLAYER_EXIT"
STATE_ERROR = "ERROR_OCCURRED"

# Texto na tela (RemoteControlInputCmd: "155>>{texto}")
INPUT_TEXT = 155

# Mouse / AirMouse (RemoteControlMouseCmd: "151>>{x}>>{y}")
MOUSE = 151

# Gerenciador de apps da TV (AppManagerProxy / AppManagerCmd: "223>>{info}")
#   lista:    "223>>GET"            -> "223>>GET>>{pkg}::{nome}::{icon}::{..}>{..}"
#   abrir:    "223>>OPEN>>{pkg}"
#   remover:  "223>>UNINSTALL>>{pkg}>>{nome}"
TV_APP = 223
TV_APP_PARAMS = 252             # StartOpenOrInstallAppCmd

# Info da TV (GetTVStorageCmd: "239>>" -> "239>>0>>{total}>>{livre}")
TV_STORAGE = 239

# Brilho (TCLRenoteSetBrightCmd: "280>>{tag}")
SET_BRIGHTNESS = 280

# Tipos de mídia (PreparePlayCmd.TYPE_*)
TYPE_VIDEO = "Video"
TYPE_AUDIO = "Audio"
TYPE_IMAGE = "Image"
TYPE_M3U8 = "m3u8"
TYPE_SHARE = "share"

# --- Teclas do remoto (TR_KEY_*) -> payload de KEY=149 ----------------------
# RemoteControlKeyCmd.pack() = "149>>{keyCode}"
KEYCODES = {
    "0": 10, "1": 1, "2": 2, "3": 3, "4": 4,
    "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
    "UP": 11,
    "DOWN": 12,
    "LEFT": 13,
    "RIGHT": 14,
    "OK": 15,
    "BACK": 16,
    "SUBMENU": 18,
    "MENU": 19,        # TR_KEY_MAINMENU
    "POWER": 20,
    "VOL_UP": 21,
    "VOL_DOWN": 22,
    "MUTE": 23,
    "EPG": 24,
    "DISPLAY": 25,
    "PLAYBACK": 26,
    "CH_UP": 27,
    "CH_DOWN": 28,
    "SOURCE": 29,
    "SCALE": 30,
    "PICTURE": 31,
    "FAVORITE": 32,
    "SEARCH": 33,
    "RED": 34,
    "GREEN": 35,
    "YELLOW": 36,
    "BLUE": 37,
    "BACKSPACE": 38,
    "MOUSELEFT": 39,
    "MOUSERIGHT": 40,
    "INFOWINDOW": 41,
    "SMARTTV": 45,
    "ENTER": 68,
    "DOT": 75,
    "3D": 17,
}
