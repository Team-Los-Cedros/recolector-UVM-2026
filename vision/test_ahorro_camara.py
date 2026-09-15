"""Prueba de integracion del ahorro, sin Raspberry, sin camara y sin MegaPi.

Levanta vision_server.main() de verdad con cv2 y pyserial simulados y comprueba
la secuencia de competencia:

  - se duerme sola cuando no hay nadie en la web ni la MegaPi habla,
  - contesta "P" en menos de 300 ms aun dormida (es el timeout real de
    visionComprobar() en la MegaPi),
  - despierta con ese mismo "P" (el que manda el boton de inicio),
  - despierta si alguien abre la web,
  - /ahorro?on=0 la deja encendida para siempre.

El testigo que se mide es el ritmo de tramas "T" por el serial: despierta manda
unas 20 por segundo, dormida solo 2.
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import types
import urllib.request

RUTA_VISION = os.path.dirname(os.path.abspath(__file__))
SCRATCH = tempfile.mkdtemp(prefix="wro_prueba_")
CRLF = chr(13) + chr(10)
PUERTO_WEB = 8099
sys.path.insert(0, RUTA_VISION)

# --------------------------------------------------------------------------
# cv2 simulado
# --------------------------------------------------------------------------
cv2 = types.ModuleType("cv2")


class _Frame:
    shape = (360, 640, 3)

    def copy(self):
        return self


class _Buf:
    def __init__(self, n):
        self.n = n

    def tobytes(self):
        return b"j" * self.n       # normal 48 KB, reducida 6 KB


class CapturaFalsa:
    def isOpened(self):
        return True

    def set(self, *a):
        return True

    def read(self):
        time.sleep(0.005)
        return True, _Frame()

    def release(self):
        pass


for nombre, valor in (("CAP_V4L2", 200), ("CAP_PROP_FOURCC", 6),
                      ("CAP_PROP_FRAME_WIDTH", 3), ("CAP_PROP_FRAME_HEIGHT", 4),
                      ("CAP_PROP_FPS", 5), ("CAP_PROP_BUFFERSIZE", 38),
                      ("ROTATE_180", 1), ("IMWRITE_JPEG_QUALITY", 1)):
    setattr(cv2, nombre, valor)
cv2.setNumThreads = lambda n: None
cv2.VideoWriter_fourcc = lambda *a: 0
cv2.rotate = lambda f, c: f
REABRIR = [0.0]      # segundos que tarda en abrir la camara (real: 1-2 s)


def _captura(dev, api=None):
    time.sleep(REABRIR[0])
    return CapturaFalsa()


cv2.VideoCapture = _captura
CODIFICADAS = [0]


def _imencode(ext, img, params=None):
    CODIFICADAS[0] += 1
    calidad = params[1] if params else 95
    return True, _Buf(49152 if calidad >= 60 else 6144)


cv2.imencode = _imencode
cv2.imdecode = lambda buf, flags: _Frame()
cv2.__getattr__ = lambda nombre: (lambda *a, **k: None)   # dibujos: no-op
sys.modules["cv2"] = cv2

# --------------------------------------------------------------------------
# pyserial simulado: RX es lo que manda la MegaPi, TX lo que responde la Pi
# --------------------------------------------------------------------------
RX = bytearray()
TX = bytearray()
CANDADO = threading.Lock()


class SerialFalso:
    def __init__(self, port=None, baudrate=None, timeout=None):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.dtr = False
        self.rts = False
        self.is_open = port is not None

    def open(self):
        self.is_open = True

    @property
    def in_waiting(self):
        with CANDADO:
            return len(RX)

    def read(self, n):
        with CANDADO:
            datos = bytes(RX[:n])
            del RX[:n]
        return datos

    def write(self, datos):
        with CANDADO:
            TX.extend(datos)
        return len(datos)

    def reset_input_buffer(self):
        with CANDADO:
            del RX[:]

    def close(self):
        self.is_open = False


serial_mod = types.ModuleType("serial")
serial_mod.Serial = SerialFalso
sys.modules["serial"] = serial_mod

# numpy simulado: en PAUSA no se detecta nada, solo hace falta para que
# Detector.__init__ construya sus tablas sin reventar.
try:
    import numpy  # noqa: F401
except ImportError:
    np = types.ModuleType("numpy")
    np.array = lambda x, dtype=None: list(x)
    np.float32 = "float32"
    np.uint8 = "uint8"
    np.__getattr__ = lambda nombre: (lambda *a, **k: None)
    sys.modules["numpy"] = np


def megapi_lineas(datos):
    """Replica visionActualizar() de la MegaPi: bufVis de 128 y corta en fin de linea."""
    lineas, buf = [], bytearray()
    for b in datos:
        if b in (10, 13):
            if buf:
                lineas.append(bytes(buf).decode("ascii", "ignore"))
                buf = bytearray()
        elif len(buf) < 127:
            buf.append(b)
        else:
            buf = bytearray()
    return lineas


def megapi_manda(linea):
    with CANDADO:
        RX.extend((linea + "\n").encode())


def vaciar_tx():
    with CANDADO:
        texto = TX.decode("ascii", "ignore")
        del TX[:]
    return texto


def contar_tramas(segundos):
    """Cuenta tramas T en una ventana. Despierta ~20/s, dormida ~2/s."""
    vaciar_tx()
    time.sleep(segundos)
    return vaciar_tx().count("T ")


# --------------------------------------------------------------------------
# Config de prueba: los tiempos del ahorro se acortan para no esperar 90 s
# --------------------------------------------------------------------------
with open(os.path.join(RUTA_VISION, "config.json"), encoding="utf-8") as f:
    cfg = json.load(f)
cfg["serial"] = {"habilitado": True, "puerto": "PUERTO_FALSO", "baudios": 115200,
                 "reintento_s": 0.5, "evitar_reset_dtr": True, "hz_envio": 20}
cfg["web"] = {"habilitado": True, "puerto": PUERTO_WEB}
cfg["telemetria"] = {"habilitado": True, "guardar_archivo": False}
cfg["ahorro"] = {"habilitado": True, "segundos_sin_web": 1.0,
                 "segundos_sin_megapi": 2.0, "hz_dormida": 2}
RUTA_CFG = os.path.join(SCRATCH, "config_prueba.json")
with open(RUTA_CFG, "w", encoding="utf-8") as f:
    json.dump(cfg, f)

import vision_server  # noqa: E402

fallos = []


def comprobar(cond, texto):
    print(("  OK   " if cond else "  FALLA") + "  " + texto)
    if not cond:
        fallos.append(texto)


def web(ruta):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (PUERTO_WEB, ruta),
                                timeout=3) as r:
        return json.loads(r.read().decode("utf-8"))


sys.argv = ["vision_server.py", "--config", RUTA_CFG]
threading.Thread(target=vision_server.main, daemon=True).start()
time.sleep(3.0)          # arranque + que se cumplan los 2 s de silencio

print("1) Se duerme sola cuando no hay nadie")
n = contar_tramas(2.0)
comprobar(n <= 8, "ritmo de espera: %d tramas en 2 s (dormida ~4)" % n)

print("2) Contesta el boton de inicio a tiempo, estando dormida")
vaciar_tx()
t0 = time.time()
megapi_manda("P")
respuesta = ""
while time.time() - t0 < 1.0:
    respuesta += vaciar_tx()
    if "K " in respuesta:
        break
    time.sleep(0.005)
ms = (time.time() - t0) * 1000
comprobar("K " in respuesta, "responde K al ping")
comprobar(ms < 300, "responde en %.0f ms (la MegaPi espera 300)" % ms)

print("3) Ese mismo ping la despierta")
# La ventana se mide justo despues del ping: con segundos_sin_megapi=2 (solo en
# esta prueba) se volveria a dormir enseguida. En el robot son 90 s.
vaciar_tx()
time.sleep(1.0)
n = vaciar_tx().count("T ")
comprobar(n > 12, "vuelve al ritmo normal: %d tramas en 1 s (despierta ~20)" % n)

print("3b) Durante una ronda no se duerme entre comando y comando")
# Se imita la ronda: la MegaPi manda algo de vez en cuando (M PAUSA, M TORRE_REC,
# tramas de log "# ...") con huecos mas cortos que segundos_sin_megapi.
vaciar_tx()
for _ in range(4):
    megapi_manda("# avanzando")
    time.sleep(1.5)
n = vaciar_tx().count("T ")
comprobar(n > 100, "sigue a ritmo normal toda la ronda: %d tramas en 6 s" % n)

print("4) Se vuelve a dormir y la web la despierta")
time.sleep(3.5)
n = contar_tramas(1.5)
comprobar(n <= 6, "dormida otra vez: %d tramas en 1,5 s" % n)
vaciar_tx()
t0 = time.time()
estado = None
while time.time() - t0 < 1.5:      # como la pagina abierta: consulta seguido
    estado = web("/estado")
    time.sleep(0.25)
n = vaciar_tx().count("T ")
comprobar(n > 10, "con la web abierta vuelve al ritmo normal: %d tramas" % n)
comprobar(estado is not None and estado.get("camara", {}).get("estado") == "CONECTADA",
          "la web informa camara CONECTADA para poder ajustar el angulo")

print("5) El interruptor /ahorro?on=0 la deja encendida")
web("/ahorro?on=0")
time.sleep(4.0)                    # sin web ni MegaPi: antes ya estaria dormida
n = contar_tramas(2.0)
comprobar(n > 25, "sigue despierta: %d tramas en 2 s" % n)
estado = web("/estado")
comprobar(estado["ahorro"]["habilitado"] is False, "el estado refleja ahorro apagado")

print("6) Con el video abierto no se duerme, aunque la red se atasque")
# Cliente que pide /stream y deja de leer. El servidor se queda bloqueado en
# wfile.write() y no puede refrescar la marca de actividad: antes la camara se
# dormia en mitad del video y el navegador veia la imagen congelada.
web("/ahorro?on=1")
cli = socket.create_connection(("127.0.0.1", PUERTO_WEB), timeout=5)
cli.sendall(("GET /stream HTTP/1.1" + CRLF + "Host: local" + CRLF + CRLF).encode())
cli.recv(4096)                     # cabeceras y un trozo; a partir de aqui no lee
time.sleep(6.0)                    # supera sin_web (1 s) y sin_megapi (2 s)
estado = web("/estado")
comprobar(estado.get("streams", 0) >= 1,
          "el servidor cuenta el video abierto (streams=%s)" % estado.get("streams"))
cam = estado.get("camara", {}).get("estado")
comprobar(cam == "CONECTADA", "la camara sigue despierta con el video abierto (%s)" % cam)

print("7) Al cerrar el navegador se vuelve a dormir")
cli.close()
time.sleep(6.0)                    # sin consultar la web, que cuenta como actividad
estado = web("/estado")
comprobar(estado.get("streams", 0) == 0, "ya no cuenta videos abiertos")
cam = estado.get("camara", {}).get("estado")
comprobar(cam == "DORMIDA", "se duerme al cerrar el video (%s)" % cam)

print("8) La MegaPi se reinicia con la camara dormida: su setup() recibe la K")
# En setup() la MegaPi no lee el serial ~1,3 s (delay de servos + calibrar el
# giroscopio). HardwareSerial guarda solo los primeros 63 bytes: quedan tramas T
# y una cortada SIN salto de linea. Si la K llega pegada a esa trama cortada, la
# MegaPi ve una linea que empieza por T y la K se pierde ("la Pi no contesta").
# Despierta no pasaba porque a 20 Hz casi siempre llega antes una T completa.
time.sleep(3.0)
comprobar(contar_tramas(1.0) <= 4, "precondicion: camara dormida")
vaciar_tx()                                  # reset de la MegaPi
time.sleep(1.3)                              # bootloader + setup(): no lee
anillo = vaciar_tx().encode("ascii")[:63]
if anillo.endswith(bytes([10])):
    anillo = anillo[:-1]                     # el caso comun: corte a mitad de linea
megapi_manda("# Sesgo giro Z = -105.6")
megapi_manda("P")
time.sleep(0.4)                              # visionComprobar(400)
vistas = megapi_lineas(anillo + vaciar_tx().encode("ascii"))
comprobar(any(l.startswith("K") for l in vistas),
          "la MegaPi reconoce la K (ve: %s)" % [l[:24] for l in vistas])

print("9) Despertada por un log, contesta el P aunque la camara tarde en abrir")
time.sleep(3.0)
comprobar(contar_tramas(1.0) <= 4, "precondicion: camara dormida otra vez")
REABRIR[0] = 1.5
vaciar_tx()
megapi_manda("# Sesgo giro Z = -105.6")      # esto la despierta
time.sleep(0.15)                             # ya procesado: el P llega en otra vuelta
t0 = time.time()
megapi_manda("P")
resp = ""
while time.time() - t0 < 1.5:
    resp += vaciar_tx()
    if "K " in resp:
        break
    time.sleep(0.005)
ms = (time.time() - t0) * 1000
comprobar("K " in resp and ms < 250,
          "contesta en %.0f ms mientras la camara se reabre (la MegaPi espera 300)" % ms)
REABRIR[0] = 0.0

def cliente_video(nombre, lento, duracion, salida):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if lento:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8192)
    s.settimeout(3)
    s.connect(("127.0.0.1", PUERTO_WEB))
    salida[nombre + "_puerto"] = s.getsockname()[1]
    s.sendall(("GET /stream HTTP/1.1" + CRLF + "Host: local" + CRLF + CRLF).encode())
    t0 = time.time()
    total = 0
    while time.time() - t0 < duracion:
        try:
            d = s.recv(4096 if lento else 1048576)
        except socket.timeout:
            continue
        if not d:
            break
        total += len(d)
        if lento:
            time.sleep(0.1)        # unos 40 kB/s: una red que no da abasto
    salida[nombre] = total
    s.close()


def video_de(estado, puerto):
    for cliente, v in (estado.get("video") or {}).items():
        if cliente.endswith(":%d" % puerto):
            return v
    return None


print("10) Con red lenta el video baja de ritmo y calidad, solo para ese cliente")
salida = {}
hilos = [threading.Thread(target=cliente_video, args=("lento", True, 24, salida)),
         threading.Thread(target=cliente_video, args=("rapido", False, 24, salida))]
for h in hilos:
    h.start()
time.sleep(4.0)
n = contar_tramas(2.0)
comprobar(n > 25, "la vision del robot sigue a su ritmo con dos videos: %d tramas en 2 s" % n)
time.sleep(8.0)
muestras_lento, muestras_rapido = [], []
for _ in range(12):                          # 6 s de muestras, no una sola foto
    e = web("/estado")
    muestras_lento.append(video_de(e, salida.get("lento_puerto", -1)))
    muestras_rapido.append(video_de(e, salida.get("rapido_puerto", -1)))
    time.sleep(0.5)
reducidas = sum(1 for v in muestras_lento if v and v["reducida"])
max_fps_lento = max((v["fps"] for v in muestras_lento if v), default=99)
a_20 = sum(1 for v in muestras_rapido if v and v["fps"] == 20 and not v["reducida"])
comprobar(reducidas >= 9 and max_fps_lento <= 5,
          "el cliente lento va con foto reducida y como mucho 5 fps (%d/12 reducidas, max %s fps)"
          % (reducidas, max_fps_lento))
comprobar(a_20 >= 9, "el cliente rapido sigue a 20 fps normales (%d/12 muestras)" % a_20)
for h in hilos:
    h.join()
comprobar(salida.get("lento", 0) > 0 and salida.get("rapido", 0) > salida.get("lento", 0),
          "los dos recibieron video (lento %d kB, rapido %d kB)"
          % (salida.get("lento", 0) // 1024, salida.get("rapido", 0) // 1024))

print("11) Sin nadie mirando no se comprime ninguna foto")
time.sleep(3.0)                              # cerrar videos y caducar la actividad web
antes = CODIFICADAS[0]
vaciar_tx()
for _ in range(8):                           # robot en ronda: la MegaPi habla
    megapi_manda("# en ronda")
    time.sleep(0.25)
n = vaciar_tx().count("T ")
comprobar(n > 25, "la vision sigue mandando tramas: %d en 2 s" % n)
comprobar(CODIFICADAS[0] - antes == 0,
          "no comprime JPEG sin nadie mirando (%d codificaciones)" % (CODIFICADAS[0] - antes))
antes = CODIFICADAS[0]
for _ in range(4):
    web("/estado")
    time.sleep(0.25)
comprobar(CODIFICADAS[0] - antes > 5,
          "al abrir la web vuelve a comprimir (%d codificaciones)" % (CODIFICADAS[0] - antes))

print("12) La pagina pide solo los ultimos 12 registros de telemetria")
d = web("/telemetria?ultimos=12")
comprobar(len(d["items"]) == 12 and d["total"] > 12,
          "?ultimos=12 devuelve %d de %d" % (len(d["items"]), d["total"]))
completo = web("/telemetria")
comprobar(len(completo["items"]) == completo["total"],
          "sin parametro sigue devolviendo todo (%d)" % completo["total"])
with urllib.request.urlopen("http://127.0.0.1:%d/" % PUERTO_WEB, timeout=3) as r:
    pagina = r.read().decode("utf-8")
comprobar("/telemetria?ultimos=12" in pagina, "la pagina ya no pide el historial completo")

print("13) RitmoVideo: baja rapido, sube despacio y no insiste si la subida falla")
RV = vision_server.RitmoVideo
r1 = RV()
t = 100.0
for _ in range(6):
    r1.registrar(1.0, t)
    t += 1.1
comprobar(r1.nivel == 5, "con envios de 1 s baja hasta 2 fps reducidas (nivel %d)" % r1.nivel)
for _ in range(20):
    r1.registrar(0.001, t)
    t += 0.2
comprobar(r1.nivel == 4, "con red buena sube un escalon cada 3 s (nivel %d)" % r1.nivel)
r2 = RV()
r2.nivel = 3
t = 50.0
r2.registrar(0.001, t)
t += 3.1
r2.registrar(0.001, t)
comprobar(r2.nivel == 2, "prueba subir tras 3 s rapidos (nivel %d)" % r2.nivel)
t += 1.1
r2.registrar(1.0, t)
comprobar(r2.nivel == 3 and r2._espera_subida == 6.0,
          "la subida no aguanto: vuelve y la proxima espera %.0f s" % r2._espera_subida)
r3 = RV(fijo=True)
r3.registrar(5.0, 1.0)
comprobar(r3.nivel == 0, "?fijo=1 no se adapta")

print()
print("RESULTADO:", "TODO BIEN" if not fallos else "FALLAN %d: %s" % (len(fallos), fallos))
sys.exit(1 if fallos else 0)
