#!/usr/bin/env python3
"""
Servidor de vision: reconoce o sigue un artefacto y le dice a la MegaPi hacia
donde moverse.

PROTOCOLO (texto ASCII, lineas terminadas en \n, 115200 baudios)

  Pi -> MegaPi   (una linea por frame, a la frecuencia de serial.hz_envio)
      T <found> <color> <ex> <ey> <area> <dist> <fps> <confianza>
      ejemplo: T 1 ROJO -34 187 950 145 24 88
               T 0 NINGUNO 0 0 0 -1 24 0

      found : 1 si se ve el objeto, 0 si no
      ex    : error horizontal en pixeles respecto al centro de la garra.
              NEGATIVO = el objeto esta a la IZQUIERDA -> el robot gira a la izquierda
              POSITIVO = esta a la DERECHA             -> el robot gira a la derecha
      ey    : fila del borde inferior del objeto (mayor = mas cerca)
      area  : area del contorno en pixeles
      dist  : distancia estimada en mm segun la tabla de calibracion (-1 si no aplica)

  MegaPi -> Pi
      C <COLOR>   sigue ese color. C AUTO reconoce el artefacto centrado.
      X           escanea LOS CINCO colores en el frame actual y responde
                    X ROJO 1 -34 145 VERDE 0 0 -1 NEGRO 1 88 260 ...
                  Sirve para saber cual de los cinco colores NO salio en la
                  ronda y dejar su hueco vacio en la zona de descarga.
      S 0|1       apaga o enciende el envio continuo
      P           ping -> la Pi responde  K <version> <colores...>
      # <texto>   cualquier linea que empiece por '#' se trata como log de la
                  MegaPi y solo se imprime; asi puedes depurar el Arduino por
                  el mismo cable sin romper el protocolo.

Uso:
    python3 vision_server.py                  # produccion (con serial)
    python3 vision_server.py --sin-serial     # sin la MegaPi conectada
    python3 vision_server.py --web            # + video en http://<ip>:8080
"""

import argparse
from collections import deque
import datetime
import glob
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time

# Marca antes de cargar OpenCV. En una Pi 3B cargarlo desde la microSD tarda
# varios segundos y es el grueso del arranque del servicio. time.monotonic()
# cuenta desde que arranco el sistema, asi que estos numeros se comparan
# directamente con los de "journalctl -o short-monotonic".
_T_ANTES_IMPORTS = time.monotonic()

import cv2
import numpy as np

from vision_core import (Camara, ContadorFPS, Deteccion, Detector,
                         GestorExclusividadCamara, cargar_config,
                         colores_disponibles, dibujar_overlay)

_T_TRAS_IMPORTS = time.monotonic()

VERSION = "3.0"


# ---------------------------------------------------------------------------
# Enlace serial con la MegaPi
# ---------------------------------------------------------------------------

class EnlaceSerial:
    def __init__(self, cfg_serial, log):
        self.cfg = cfg_serial
        self.log = log
        self.ser = None
        self._rx = ""
        self._proximo_intento = 0.0
        self._conectado_antes = False
        self._aviso_espera = False

    def _resolver_puerto(self):
        puerto = self.cfg.get("puerto", "/dev/ttyUSB0")
        if os.path.exists(puerto):
            return puerto
        patron = self.cfg.get("patron_by_id")
        candidatos = sorted(glob.glob(patron)) if patron else []
        return candidatos[0] if candidatos else puerto

    def abrir(self):
        import serial  # pyserial

        puerto = self._resolver_puerto()
        baud = self.cfg.get("baudios", 115200)

        try:
            if self.cfg.get("evitar_reset_dtr"):
                # Sin togglear DTR la MegaPi NO se reinicia al abrir el puerto.
                # Util si el Arduino ya esta a mitad de una rutina.
                self.ser = serial.Serial()
                self.ser.port = puerto
                self.ser.baudrate = baud
                self.ser.timeout = 0
                self.ser.dtr = False
                self.ser.rts = False
                self.ser.open()
            else:
                self.ser = serial.Serial(puerto, baud, timeout=0)
                espera = self.cfg.get("espera_reset_s", 2.5)
                # Abrir el puerto USB resetea el ATmega2560. Hay que esperar a que
                # arranque o los primeros mensajes se pierden.
                self.log("esperando %.1fs el reinicio de la MegaPi..." % espera)
                time.sleep(espera)
                self.ser.reset_input_buffer()
        except Exception:
            # Si no abrio hay que dejarlo en None. Si se queda aqui el objeto
            # cerrado, el siguiente enviar() lanza "port is not open", se marca
            # una desconexion y el ciclo se repite: una linea de log por segundo
            # mientras la MegaPi este apagada, escribiendo en la SD sin parar.
            self.ser = None
            raise

        self.log("serial abierto en %s @ %d" % (puerto, baud))
        self._conectado_antes = True
        self._proximo_intento = 0.0
        self._aviso_espera = False
        return self

    @property
    def conectado(self):
        return self.ser is not None and self.ser.is_open

    def _marcar_desconectado(self, error):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None
        self._rx = ""
        self._proximo_intento = time.monotonic() + self.cfg.get("reintento_s", 1.0)
        self.log("serial desconectado: %s" % error)

    def asegurar_conectado(self):
        if self.conectado:
            return True
        if time.monotonic() < self._proximo_intento:
            return False
        try:
            self.abrir()
            return True
        except Exception as e:
            self._proximo_intento = time.monotonic() + self.cfg.get("reintento_s", 1.0)
            # Se avisa una sola vez: con la MegaPi apagada esto se reintenta
            # cada segundo y antes llenaba el journal.
            if not self._aviso_espera:
                self._aviso_espera = True
                self.log("esperando a la MegaPi en %s (%s)" % (self._resolver_puerto(), e))
            return False

    def enviar(self, linea):
        if self.ser is None:
            return
        try:
            self.ser.write((linea + "\n").encode("ascii", "ignore"))
        except Exception as e:
            self._marcar_desconectado(e)

    def responder(self, linea):
        """Manda una respuesta (K, E, F, X) precedida de una linea vacia.

        En setup() la MegaPi pasa mas de un segundo sin leer el serial y su
        HardwareSerial solo guarda 63 bytes, asi que se queda con una trama T
        cortada, sin fin de linea. Si la K llegaba justo detras, se pegaba a
        esa trama: la MegaPi leia "T 0 NINGUK 3.0 ..." y avisaba que la Pi no
        contesta. Con la camara despierta casi nunca pasaba porque a 20 Hz
        siempre llegaba antes una T completa; dormida (2 Hz), si. La linea
        vacia cierra la trama cortada, y todos los sketches ignoran las lineas
        vacias (if (nBufVis > 0)).
        """
        self.enviar("")
        self.enviar(linea)

    def leer_lineas(self):
        """Lectura no bloqueante; devuelve las lineas completas recibidas."""
        if self.ser is None:
            return []
        try:
            n = self.ser.in_waiting
            if n:
                self._rx += self.ser.read(n).decode("ascii", "ignore")
        except Exception as e:
            self._marcar_desconectado(e)
            return []

        if "\n" not in self._rx:
            return []
        partes = self._rx.split("\n")
        self._rx = partes[-1]
        return [p.strip() for p in partes[:-1] if p.strip()]

    def cerrar(self):
        if self.ser is not None:
            self.ser.close()
            self.ser = None


# ---------------------------------------------------------------------------
# Buffer de video y registro de telemetria
# ---------------------------------------------------------------------------

class BufferFrame:
    """Ultima foto para la web: con overlay y cruda, ya en JPEG.

    seq cuenta las fotos: cada cliente de /stream espera a que cambie en vez de
    mandar a ciegas cada 50 ms, que repetia fotos o se las saltaba. La version
    reducida (mitad de resolucion, calidad baja) solo se calcula si un cliente
    lento la pide, y una sola vez por foto aunque la pidan varios.
    """
    def __init__(self):
        self.lock = threading.Lock()
        self.cambio = threading.Condition(self.lock)
        self.jpeg = None
        self.raw_jpeg = None
        self.seq = 0
        self._lock_reducida = threading.Lock()
        self._reducida = None
        self._seq_reducida = -1

    def set(self, frame, frame_raw=None):
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
        raw = None
        if frame_raw is not None:
            ok_r, buf_r = cv2.imencode(".jpg", frame_raw, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if ok_r:
                raw = buf_r.tobytes()
        if ok:
            with self.cambio:
                self.jpeg = buf.tobytes()
                if raw is not None:
                    self.raw_jpeg = raw
                self.seq += 1
                self.cambio.notify_all()

    def get(self):
        with self.lock:
            return self.jpeg

    def get_raw(self):
        with self.lock:
            return self.raw_jpeg

    def esperar_nueva(self, seq_anterior, timeout=1.0, reducida=False):
        """(seq, jpeg) de una foto posterior a seq_anterior, o (seq_anterior, None)."""
        with self.cambio:
            if self.seq == seq_anterior:
                self.cambio.wait(timeout)
            if self.jpeg is None or self.seq == seq_anterior:
                return seq_anterior, None
            seq, jpeg = self.seq, self.jpeg
        if reducida:
            jpeg = self._reducir(seq, jpeg)
        return seq, jpeg

    def esperar_raw_nueva(self, timeout=2.5):
        """La proxima foto cruda. Sin nadie mirando no se codifica, asi que la
        guardada puede ser vieja; esta peticion ya cuenta como actividad."""
        with self.cambio:
            seq = self.seq
            limite = time.monotonic() + timeout
            while self.seq == seq:
                resto = limite - time.monotonic()
                if resto <= 0:
                    break
                self.cambio.wait(resto)
            return self.raw_jpeg

    def _reducir(self, seq, jpeg):
        with self._lock_reducida:
            if self._seq_reducida != seq:
                # IMREAD_REDUCED_COLOR_2 decodifica ya a mitad de tamano: es
                # mucho mas barato que decodificar entero y redimensionar.
                img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_REDUCED_COLOR_2)
                ok, buf = (cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 45])
                           if img is not None else (False, None))
                self._reducida = buf.tobytes() if ok else jpeg
                self._seq_reducida = seq
            return self._reducida


class RitmoVideo:
    """Adapta el video de UN cliente web a lo que aguanta su red.

    La senal es cuanto tarda cada foto en entrar al socket. Con red buena es
    casi instantaneo; si la red no da abasto, el buffer de envio se llena y
    write() se bloquea. Entonces se baja un nivel: menos fotos por segundo y, al
    final, fotos reducidas. Para subir hacen falta 3 s seguidos de envios
    rapidos, y si una subida no aguanta, la siguiente espera el doble (hasta
    30 s): asi no se queda probando y atascandose cada pocos segundos en una red
    que no da para mas. Cada cliente tiene el suyo, y nada de esto toca la
    vision del robot: la deteccion y las tramas T no dependen de la web.
    """
    #          (segundos entre fotos, foto reducida)
    NIVELES = ((0.05, False),   # 20 fps
               (0.10, False),   # 10 fps
               (0.20, False),   #  5 fps
               (0.20, True),    #  5 fps, mitad de resolucion y calidad baja
               (0.33, True),    #  3 fps
               (0.50, True))    #  2 fps

    def __init__(self, fijo=False):
        self.fijo = fijo
        self.nivel = 0
        self._t_bajada = -1e9
        self._t_subida = -1e9
        self._t_rapidos = None
        self._espera_subida = 3.0

    @property
    def intervalo(self):
        return self.NIVELES[self.nivel][0]

    @property
    def reducida(self):
        return self.NIVELES[self.nivel][1]

    def registrar(self, segundos_envio, ahora=None):
        if self.fijo:
            return
        ahora = time.monotonic() if ahora is None else ahora
        if segundos_envio > self.intervalo:
            # La red no aguanta este ritmo. Como mucho un escalon por segundo:
            # hay que dar tiempo a que se vacie lo que ya estaba en cola.
            self._t_rapidos = None
            if self.nivel < len(self.NIVELES) - 1 and ahora - self._t_bajada >= 1.0:
                if ahora - self._t_subida < 5.0:
                    # La ultima subida no aguanto: antes de volver a probar,
                    # esperar el doble.
                    self._espera_subida = min(30.0, self._espera_subida * 2)
                self.nivel += 1
                self._t_bajada = ahora
        elif segundos_envio < self.intervalo * 0.25:
            if self._t_rapidos is None:
                self._t_rapidos = ahora
            elif self.nivel > 0 and ahora - self._t_rapidos >= self._espera_subida:
                self.nivel -= 1
                self._t_rapidos = ahora
                self._t_subida = ahora
        else:
            self._t_rapidos = None
        if (self._espera_subida > 3.0 and self._t_subida > self._t_bajada
                and ahora - self._t_subida > 10.0):
            self._espera_subida = 3.0      # la ultima subida aguanto: red mejor

    def resumen(self):
        return {"fps": round(1.0 / self.intervalo), "reducida": self.reducida,
                "nivel": self.nivel, "fijo": self.fijo}


class RegistroTelemetria:
    """Almacena registros de telemetria en memoria y archivo JSONL para depuracion."""
    def __init__(self, cfg_telemetria=None, max_memoria=3000):
        self.lock = threading.Lock()
        self.items = deque(maxlen=max_memoria)
        self.cfg = cfg_telemetria or {}
        self.habilitado = self.cfg.get("habilitado", True)
        self.archivo_log = None
        self.archivo_path = None

        if self.habilitado and self.cfg.get("guardar_archivo", True):
            try:
                dir_logs = Path(__file__).parent / self.cfg.get("directorio_logs", "logs")
                dir_logs.mkdir(parents=True, exist_ok=True)
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                self.archivo_path = dir_logs / f"telemetria_{ts}.jsonl"
                self.archivo_log = open(self.archivo_path, "a", encoding="utf-8")
            except Exception as e:
                print(f"[telemetria] Error al inicializar archivo de logs: {e}", flush=True)

    def registrar(self, tipo, datos):
        if not self.habilitado:
            return
        ahora = time.time()
        registro = {
            "t_epoch": round(ahora, 4),
            "t_iso": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "tipo": tipo,
            **datos
        }
        with self.lock:
            self.items.append(registro)

        if self.archivo_log:
            try:
                self.archivo_log.write(json.dumps(registro, ensure_ascii=False) + "\n")
                self.archivo_log.flush()
            except Exception:
                pass

    def obtener_todos(self):
        with self.lock:
            return list(self.items)

    def obtener_ultimos(self, n):
        with self.lock:
            return len(self.items), list(self.items)[-n:]

    def limpiar(self):
        with self.lock:
            self.items.clear()

    def cerrar(self):
        if self.archivo_log:
            try:
                self.archivo_log.close()
            except Exception:
                pass


def arrancar_web(buffer_frame, telemetria, estado_global, puerto=8080,
                 callback_modo=None, marcar_actividad=None, callback_ahorro=None,
                 marcar_stream=None, marcar_video=None):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    PAGINA = (b"<!DOCTYPE html><html><head><meta charset='utf-8'><title>WRO Vision & Telemetria</title>"
              b"<style>body{background:#18181b;color:#f4f4f5;font-family:ui-monospace,monospace;margin:0;padding:15px;text-align:center}"
              b".container{max-width:800px;margin:0 auto}"
              b"img{width:100%;max-width:640px;height:auto;border:2px solid #3f3f46;border-radius:6px;image-rendering:pixelated}"
              b".panel{background:#27272a;border-radius:6px;padding:12px;margin-top:10px;text-align:left;font-size:13px}"
              b".tag{display:inline-block;padding:2px 8px;border-radius:4px;background:#3f3f46;font-weight:bold;margin-right:6px}"
              b"#logs{max-height:180px;overflow-y:auto;background:#09090b;padding:8px;border-radius:4px;margin-top:8px;font-size:12px}"
              b"</style></head><body><div class='container'>"
              b"<h2>WRO RoboMission 2026 - Vision & Telemetria</h2>"
              b"<img src='/stream'>"
              b"<div class='panel'>"
              b"<div><span class='tag'>STREAM</span> <a href='/stream' target='_blank' style='color:#38bdf8'>/stream</a> | "
              b"<span class='tag'>RAW</span> <a href='/raw' target='_blank' style='color:#a78bfa'>/raw</a> | "
              b"<span class='tag'>TELEMETRIA</span> <a href='/telemetria' target='_blank' style='color:#4ade80'>/telemetria (JSON)</a> | "
              b"<span class='tag'>ESTADO</span> <a href='/estado' target='_blank' style='color:#fbbf24'>/estado</a></div>"
              b"<div style='margin-top:8px;'><span class='tag'>MODO</span> "
              b"<a href='/modo?m=PAUSA' style='color:#a1a1aa'>PAUSA</a> | "
              b"<a href='/modo?m=ARTEFACTO&c=AUTO' style='color:#38bdf8'>AUTO</a> | "
              b"<a href='/modo?m=TORRE_REC' style='color:#facc15;font-weight:bold'>TORRE_REC</a> | "
              b"<a href='/modo?m=TORRE_DEST' style='color:#f97316;font-weight:bold'>TORRE_DEST</a></div>"
              b"<div style='margin-top:8px;'><span class='tag'>CAMARA</span> "
              b"<span id='cam'>...</span> &nbsp;&nbsp; "
              b"<a href='/ahorro?on=1' style='color:#4ade80'>ahorro ON</a> | "
              b"<a href='/ahorro?on=0' style='color:#f87171'>siempre encendida</a></div>"
              b"<div id='logs'>Cargando telemetria en vivo...</div>"
              b"</div></div>"
              b"<script>"
              b"async function updateLogs(){try{let r=await fetch('/telemetria?ultimos=12');let d=await r.json();let l=document.getElementById('logs');"
              b"if(d.items){l.innerHTML=d.items.slice(-12).reverse().map(e=>`[${e.t_iso.split('T')[1].slice(0,8)}] <b>${e.tipo.toUpperCase()}</b>: `+JSON.stringify(e)).join('<br>');}"
              b"let st=await (await fetch('/estado')).json();"
              b"document.getElementById('cam').textContent=(st.camara?st.camara.estado:'?')+((st.ahorro&&st.ahorro.habilitado)?' | ahorro activo':' | ahorro apagado')"
              b"+' | video: '+(Object.values(st.video||{}).map(v=>v.fps+' fps'+(v.reducida?' reducido':'')).join(', ')||'-');"
              b"}catch(err){}setTimeout(updateLogs, 800);}updateLogs();"
              b"</script></body></html>")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            # Cualquier visita cuenta como "hay alguien mirando". La pagina
            # consulta /telemetria y /estado cada 800 ms, asi que mientras este
            # abierta en un navegador la camara no se duerme y se puede ajustar
            # el angulo con la imagen en vivo.
            if marcar_actividad:
                marcar_actividad()
            if self.path == "/stream" or self.path.startswith("/stream?"):
                fijo = "fijo=1" in self.path
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                # Buffer de envio chico: si la red no da abasto, write() se
                # bloquea enseguida (asi se detecta) y quedan pocas fotos viejas
                # en cola (asi el retraso no crece sin limite). /stream?fijo=1
                # desactiva la adaptacion.
                try:
                    self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 32 * 1024)
                except OSError:
                    pass
                ritmo = RitmoVideo(fijo)
                cliente = "%s:%s" % tuple(self.client_address[:2])
                # Mientras haya un /stream abierto la camara NO se duerme. Hay
                # que contarlos en vez de mirar el reloj: con red lenta
                # wfile.write() se queda bloqueado varios segundos, la marca de
                # actividad se queda vieja y la camara se dormia en mitad del
                # video. El navegador se quedaba con la ultima imagen congelada
                # y al desbloquearse la escritura, despertaba: saltos y desfase.
                if marcar_stream:
                    marcar_stream(1)
                if marcar_video:
                    marcar_video(cliente, ritmo.resumen())
                seq = -1
                try:
                    while True:
                        if marcar_actividad:
                            marcar_actividad()
                        seq, jpeg = buffer_frame.esperar_nueva(seq, timeout=1.0,
                                                               reducida=ritmo.reducida)
                        if jpeg is None:
                            continue
                        t0 = time.monotonic()
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                         b"Content-Length: %d\r\n\r\n" % len(jpeg))
                        self.wfile.write(jpeg)
                        self.wfile.write(b"\r\n")
                        envio = time.monotonic() - t0
                        nivel_antes = ritmo.nivel
                        ritmo.registrar(envio)
                        if marcar_video and ritmo.nivel != nivel_antes:
                            marcar_video(cliente, ritmo.resumen())
                        time.sleep(max(0.0, ritmo.intervalo - envio))
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    if marcar_stream:
                        marcar_stream(-1)
                    if marcar_video:
                        marcar_video(cliente, None)

            elif self.path == "/raw":
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                raw = buffer_frame.esperar_raw_nueva()
                if raw:
                    self.wfile.write(raw)

            elif self.path.startswith("/modo"):
                qs = parse_qs(urlparse(self.path).query)
                m = qs.get("m", [None])[0]
                c = qs.get("c", [None])[0]
                if m and callback_modo:
                    callback_modo(m, c)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(estado_global, ensure_ascii=False).encode("utf-8"))

            elif self.path.startswith("/ahorro"):
                # Interruptor de emergencia: /ahorro?on=0 deja la camara
                # encendida siempre hasta que se reinicie el servicio.
                qs = parse_qs(urlparse(self.path).query)
                on = qs.get("on", [None])[0]
                if on is not None and callback_ahorro:
                    callback_ahorro(on not in ("0", "false", "no"))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(estado_global, ensure_ascii=False).encode("utf-8"))

            elif self.path == "/telemetria" or self.path.startswith("/telemetria?"):
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                # /telemetria?ultimos=N devuelve solo los N ultimos. La pagina
                # muestra 12 y consulta cada 800 ms: pedirlo todo eran ~640 kB
                # por consulta (3000 registros), mas que el propio video.
                qs = parse_qs(urlparse(self.path).query)
                try:
                    ultimos = int(qs.get("ultimos", ["0"])[0])
                except ValueError:
                    ultimos = 0
                if ultimos > 0:
                    total, items = telemetria.obtener_ultimos(ultimos)
                else:
                    items = telemetria.obtener_todos()
                    total = len(items)
                resp = json.dumps({"total": total, "items": items}, ensure_ascii=False)
                self.wfile.write(resp.encode("utf-8"))

            elif self.path == "/telemetria/reset":
                telemetria.limpiar()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"status":"ok","message":"telemetria limpiada"}')

            elif self.path == "/estado":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                resp = json.dumps(estado_global, ensure_ascii=False)
                self.wfile.write(resp.encode("utf-8"))

            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(PAGINA)

    srv = ThreadingHTTPServer(("0.0.0.0", puerto), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---------------------------------------------------------------------------
# Bucle principal
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--sin-serial", action="store_true",
                    help="no abre el puerto serial; imprime las tramas en pantalla")
    ap.add_argument("--web", action="store_true",
                    help="publica el video con overlay en http://<ip>:8080")
    ap.add_argument("--puerto-web", type=int, default=None)
    ap.add_argument("--color", default=None, help="color inicial a buscar")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    def log(msg):
        print("[vision] %s" % msg, flush=True)

    log("arranque: OpenCV cargado en %.1f s (listo a los %.1f s de encender)"
        % (_T_TRAS_IMPORTS - _T_ANTES_IMPORTS, _T_TRAS_IMPORTS))

    cfg = cargar_config(args.config)
    disponibles = colores_disponibles(cfg)
    color = (args.color or cfg["deteccion"].get("color_inicial", disponibles[0])).upper()
    modo = cfg["deteccion"].get("modo_inicial", "PAUSA").upper()
    if color not in disponibles and color != "AUTO":
        log("color '%s' no definido; usando %s" % (color, disponibles[0]))
        color = disponibles[0]
    if modo not in ("PAUSA", "ARTEFACTO", "DESTINO", "FILA"):
        modo = "PAUSA"

    detector = Detector(cfg)
    telemetria = RegistroTelemetria(cfg.get("telemetria", {}))
    
    gestor = GestorExclusividadCamara(nombre_script="vision_server.py", gestionar_servicio=False)
    gestor.adquirir()
    
    _t_cam = time.monotonic()
    cam = Camara(cfg["camara"]).abrir()
    log("camara %dx%d abierta en %.1f s, modo %s %s" %
        (cam.ancho, cam.alto, time.monotonic() - _t_cam, modo, color))

    enlace = None
    if cfg["serial"].get("habilitado", True) and not args.sin_serial:
        enlace = EnlaceSerial(cfg["serial"], log)
        if not enlace.asegurar_conectado():
            log("AVISO: MegaPi ausente; se reconectara automaticamente")

    cfg_web = cfg.get("web", {})
    habilitar_web = args.web or cfg_web.get("habilitado", True)
    puerto_web = args.puerto_web or cfg_web.get("puerto", 8080)

    estado_global = {
        "version": VERSION,
        "modo": modo,
        "color_actual": color,
        "fps": 0,
        "serial_conectado": enlace is not None and enlace.conectado,
        "enviando": True
    }

    buffer_frame = None
    def set_modo(nuevo_m, nuevo_c=None):
        nonlocal modo, color
        m = nuevo_m.upper()
        if m in ("PAUSA", "FILA", "TORRE_REC", "TORRE_DEST"):
            modo = m
            color = m
        elif m in ("ARTEFACTO", "DESTINO"):
            modo = m
            if nuevo_c:
                color = nuevo_c.upper()
        estado_global["modo"] = modo
        estado_global["color_actual"] = color
        log("modo cambiado -> %s %s" % (modo, color))

    # ---- Ahorro de energia -------------------------------------------------
    # La camara se suelta cuando no la necesita nadie: sin nadie en la web y con
    # la MegaPi callada. Despierta con cualquier cosa que mande la MegaPi (al
    # pulsar el boton de inicio manda "P") y con cualquier visita a la web.
    # segundos_sin_megapi es deliberadamente largo: durante una ronda pueden
    # pasar decenas de segundos entre un comando y el siguiente, y la camara NO
    # debe dormirse en mitad de la carrera.
    cfg_ahorro = cfg.get("ahorro", {})
    ahorro = {
        "habilitado": bool(cfg_ahorro.get("habilitado", True)),
        "seg_sin_web": float(cfg_ahorro.get("segundos_sin_web", 15.0)),
        "seg_sin_megapi": float(cfg_ahorro.get("segundos_sin_megapi", 90.0)),
        "hz_dormida": float(cfg_ahorro.get("hz_dormida", 2.0)),
    }
    estado_global["ahorro"] = ahorro
    t_web = 0.0
    t_megapi = time.monotonic()
    streams = {"n": 0}
    estado_global["streams"] = 0

    def marcar_web():
        nonlocal t_web
        t_web = time.monotonic()

    def marcar_stream(delta):
        streams["n"] = max(0, streams["n"] + delta)
        estado_global["streams"] = streams["n"]

    # Ritmo de video de cada cliente, para verlo en /estado y en la pagina. Se
    # reemplaza el diccionario entero en vez de modificarlo: /estado lo
    # serializa desde otro hilo y no puede cambiar de tamano mientras tanto.
    estado_global["video"] = {}

    def marcar_video(cliente, resumen):
        d = dict(estado_global["video"])
        if resumen is None:
            d.pop(cliente, None)
        else:
            d[cliente] = resumen
        estado_global["video"] = d

    def set_ahorro(activo):
        ahorro["habilitado"] = bool(activo)
        log("ahorro de camara %s" % ("ACTIVADO" if activo else "DESACTIVADO"))

    if habilitar_web:
        buffer_frame = BufferFrame()
        arrancar_web(buffer_frame, telemetria, estado_global, puerto_web,
                     callback_modo=set_modo, marcar_actividad=marcar_web,
                     callback_ahorro=set_ahorro, marcar_stream=marcar_stream,
                     marcar_video=marcar_video)
        log("servidor web y telemetria en http://<ip-de-la-pi>:%d" % puerto_web)

    fps = ContadorFPS()
    hz_envio = cfg["serial"].get("hz_envio", 20)
    periodo_envio = 0.0 if hz_envio <= 0 else 0.9 / hz_envio
    ultimo_envio = 0.0
    enviando = True

    try:
        while True:
            if enlace:
                enlace.asegurar_conectado()
                estado_global["serial_conectado"] = enlace.conectado

            # ---- dormir o despertar la camara ---------------------------
            ahora_m = time.monotonic()
            hay_web = (streams["n"] > 0
                       or (ahora_m - t_web) < ahorro["seg_sin_web"])
            hay_megapi = (ahora_m - t_megapi) < ahorro["seg_sin_megapi"]
            debe_dormir = (ahorro["habilitado"] and modo == "PAUSA"
                           and not hay_web and not hay_megapi)
            if debe_dormir and not cam.dormida:
                log("camara dormida (nadie en la web y la MegaPi lleva %.0f s callada)"
                    % ahorro["seg_sin_megapi"])
                cam.dormir()
            elif not debe_dormir and cam.dormida:
                log("camara despierta")
                cam.despertar()
            durmiendo = cam.dormida

            # Mientras la camara no entregue fotos (dormida, o reabriendose
            # tras despertar, 1-2 s) no se espera por ellas: el bucle corre a
            # ~20 Hz solo para atender el serial. Si se quedara 0,5 s dentro de
            # leer(), el "P" que llega despues de un log de la MegaPi se
            # contestaria tarde y visionComprobar() (300 ms) fallaria.
            sin_fotos = durmiendo or cam.estado()["estado"] != "CONECTADA"
            frame = cam.leer(solo_nuevos=True, timeout=0.05 if sin_fotos else 0.5)
            det = Deteccion()
            color_detectado = "NINGUNO"
            roi_y0 = 0
            if frame is None:
                f = fps.fps
            elif modo == "TORRE_REC":
                det, _mask, roi_y0 = detector.detectar_torre_recolectar(frame)
                color_detectado = "TORRE_REC"
                f = fps.tick()
            elif modo == "TORRE_DEST":
                det, _mask, roi_y0 = detector.detectar_torre_destino(frame)
                color_detectado = "TORRE_DEST"
                f = fps.tick()
            elif modo == "ARTEFACTO" and color == "AUTO":
                det, color_detectado, _mask, roi_y0 = detector.detectar_auto(frame)
                f = fps.tick()
            elif modo == "ARTEFACTO":
                det, _mask, roi_y0 = detector.detectar(frame, color)
                color_detectado = color
                f = fps.tick()
            elif modo == "DESTINO" and color in disponibles:
                det, _mask, roi_y0 = detector.detectar_destino(frame, color)
                color_detectado = color
                f = fps.tick()
            else:
                f = fps.tick()

            estado_global["fps"] = int(f)
            estado_global["modo"] = modo
            estado_global["color_actual"] = color
            estado_global["camara"] = cam.estado()

            ahora = time.time()
            periodo = (1.0 / ahorro["hz_dormida"]) if durmiendo else periodo_envio
            if enviando and (ahora - ultimo_envio) >= periodo:
                ultimo_envio = ahora
                trama = "T %d %s %d %d %d %d %d %d" % (
                    1 if det.encontrado else 0, color_detectado,
                    det.ex, det.ey, det.area, det.dist_mm, int(f),
                    det.confianza)
                if enlace:
                    enlace.enviar(trama)
                if args.verbose or enlace is None:
                    print(trama, flush=True)

                # Registrar telemetria de vision. Dormida no se escribe nada:
                # asi la SD queda quieta mientras se espera y cortar la
                # corriente con el suiche es mucho menos arriesgado.
                if not durmiendo:
                    telemetria.registrar("vision", {
                        "found": 1 if det.encontrado else 0,
                        "modo": modo,
                        "color": color_detectado,
                        "ex": det.ex,
                        "ey": det.ey,
                        "area": det.area,
                        "dist_mm": det.dist_mm,
                        "fps": int(f),
                        "confianza": det.confianza
                    })

            # Comandos y logs que llegan de la MegaPi
            if enlace:
                for linea in enlace.leer_lineas():
                    # Cualquier cosa que mande la MegaPi despierta la camara. Al
                    # pulsar el boton de inicio manda "P", varios segundos antes
                    # del primer visionCentrar(), asi da tiempo a reabrirla.
                    t_megapi = time.monotonic()
                    if cam.dormida:
                        cam.despertar()
                    if linea.startswith("#"):
                        msg_mega = linea[1:].strip()
                        log("MegaPi: %s" % msg_mega)
                        telemetria.registrar("megapi", {"mensaje": msg_mega})
                        continue
                    
                    partes = linea.split()
                    cmd = partes[0].upper()
                    telemetria.registrar("comando", {"comando": cmd, "linea": linea})

                    if cmd == "C" and len(partes) >= 2:
                        nuevo = partes[1].upper()
                        if nuevo in ("TORRE_REC", "TORRE_DEST"):
                            modo = nuevo
                            color = nuevo
                            log("modo -> %s" % modo)
                            enlace.responder("K COLOR %s" % color)
                        elif nuevo in disponibles or nuevo == "AUTO":
                            color = nuevo
                            modo = "ARTEFACTO"
                            log("modo -> ARTEFACTO %s" % color)
                            enlace.responder("K COLOR %s" % color)
                        else:
                            enlace.responder("E COLOR")
                    elif cmd == "M" and len(partes) >= 2:
                        nuevo_modo = partes[1].upper()
                        nuevo_color = partes[2].upper() if len(partes) >= 3 else color
                        valido = nuevo_modo in ("PAUSA", "FILA", "TORRE_REC", "TORRE_DEST") or (
                            nuevo_modo in ("ARTEFACTO", "DESTINO") and
                            nuevo_color in disponibles + (["AUTO"] if nuevo_modo == "ARTEFACTO" else []))
                        if valido:
                            modo = nuevo_modo
                            if nuevo_modo in ("TORRE_REC", "TORRE_DEST"):
                                color = nuevo_modo
                            else:
                                color = nuevo_color
                            log("modo -> %s %s" % (modo, color))
                            enlace.responder("K MODO %s %s" % (modo, color))
                        else:
                            enlace.responder("E MODO")
                    elif cmd == "F":
                        fila = detector.detectar_fila_artefactos(frame) if frame is not None else []
                        enlace.responder("F %d%s" % (
                            len(fila), "" if not fila else " " +
                            " ".join(d.color for d in fila)))
                    elif cmd == "X":
                        # Si la camara aun esta despertando no hay frame: se
                        # contesta vacio en vez de tumbar el servidor.
                        partes_resp = []
                        if frame is not None:
                            for c in disponibles:
                                dc, _, _ = detector.detectar(frame, c)
                                partes_resp.append("%s %d %d %d" % (
                                    c, 1 if dc.encontrado else 0, dc.ex, dc.dist_mm))
                        enlace.responder("X " + " ".join(partes_resp))
                    elif cmd == "S" and len(partes) >= 2:
                        enviando = partes[1] != "0"
                        estado_global["enviando"] = enviando
                        enlace.responder("K STREAM %d" % (1 if enviando else 0))
                    elif cmd == "P":
                        enlace.responder("K %s MODOS=PAUSA,ARTEFACTO,FILA,DESTINO,TORRE_REC,TORRE_DEST COLORES=AUTO,%s" %
                                      (VERSION, ",".join(disponibles)))

            # La foto con overlay solo sirve para la web: sin nadie mirando no se
            # dibuja ni se comprime (eran dos JPEG por foto). Esa CPU queda para
            # la vision del robot durante las rondas.
            if buffer_frame is not None and frame is not None and hay_web:
                etiqueta = "%s/%s" % (modo, color_detectado if color == "AUTO" else color)
                buffer_frame.set(dibujar_overlay(frame.copy(), det, etiqueta, f,
                                                 roi_y0, detector.cx_garra), frame)

    except KeyboardInterrupt:
        log("detenido por el usuario")
    finally:
        telemetria.cerrar()
        cam.cerrar()
        gestor.liberar()
        if enlace:
            enlace.cerrar()


if __name__ == "__main__":
    sys.exit(main())
