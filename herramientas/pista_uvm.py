"""Consola de práctica UVM. Ejecutar en la Pi, o usar --demo en la PC."""
import argparse
from contextlib import ExitStack
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

VISION = Path("/home/pi/WRO-RM-2026/vision")
COLORS = "blanco amarillo verde rojo azul negro".split()
HELP = """
Lecturas: estado | piso | eco | cal | cero (encoders)
Calibrar: c COLOR [i|d] | gt MS_ABRIR MS_CERRAR | gp PWM_ABRIR PWM_CERRAR PWM_SUJETAR
          tg 1|4|16|60 | tt MS | guardar (EEPROM)
Borde: margen LIBRE_CM VELOCIDAD_MAX_CM_S RESERVA_CM INERCIA_CM (medidos)
Control: w/s/a/d [ms] = adelante/atrás/izquierda/derecha a PWM 80
         abrir/cerrar [ms] = pulso a PWM 150 | p = parar todo | Enter = parar
Cámara: ver COLOR | muestra | foto | geometria CX Y1:MM1 Y2:MM2 Y3:MM3 ... (cerca a lejos)
Registro: nota TEXTO | ayuda | fin
"""

def durable(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        try:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            f.close()
            temp.unlink(missing_ok=True)
            raise
    if path.exists():
        os.chmod(temp, path.stat().st_mode & 0o777)
    os.replace(temp, path)
    if os.name == "posix":
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


class Session:
    def __init__(self, folder, port=None):
        self.folder = folder
        self.port = port
        self.start = time.monotonic()
        self.floor = {}
        self.free = 0.0
        self.speed = self.reserve = self.coast = 0.0
        self.journal = folder / "bitacora.jsonl"

    def log(self, event, value):
        record = {"hora": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                  "seg": round(time.monotonic()-self.start, 3), "evento": event, "dato": value}
        with self.journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        print(f"[{record['seg']:.1f}s] {event} > {value}", flush=True)

    def send(self, cmd, wait=.4):
        self.log("TX", cmd)
        if self.port is None:
            return []
        self.port.write((cmd + "\n").encode())
        end = time.monotonic() + wait
        lines = []
        while time.monotonic() < end:
            line = self.port.readline().decode(errors="replace").strip()
            if line:
                lines.append(line)
        self.log("RX", "\n".join(lines))
        return lines

    def stream(self, cmd):
        self.send("p")
        try:
            return self.send(cmd, 1.65)
        finally:
            self.send("p")

    def drive(self, command, ms):
        cost = self.speed * ms / 1000 + self.coast
        if self.free - cost <= self.reserve:
            raise ValueError("Pulso bloqueado por margen. Mide de nuevo o acorta el pulso.")
        self.send("p")
        initial = self.send("t", .55)
        floor = self.floor.copy()
        seen = set()
        def observe(line):
            match = re.match(r"TCS (IZQ|DER) C=\d+ .* -> (\w+) \(conf ([\d.]+)\)", line)
            if line.startswith("TCS "):
                if not match or "SATURADO" in line:
                    return "lectura de piso inválida"
                side, color, conf = match.groups()
                if color == "negro" or float(conf) <= 2:
                    return f"{side} {color} confianza {conf}"
                if color == "blanco" and floor.get(side) in COLORS[1:5]:
                    return f"{side} pasó de zona de color a blanco"
                seen.add(side)
                floor[side] = color
            return ""
        try:
            for line in initial:
                reason = observe(line)
                if reason:
                    raise ValueError(reason)
            if self.port and len(seen) != 2:
                raise ValueError("Faltan lecturas de ambos TCS.")
            self.free -= cost
            self.log("margen restante cm", round(self.free,2))
            self.log("TX", command)
            if self.port:
                self.port.write((command+"\n").encode())
                deadline = time.monotonic() + ms/1000 + .16
                samples = []
                last = dict.fromkeys(seen,time.monotonic())
                while time.monotonic() < deadline:
                    line = self.port.readline().decode(errors="replace").strip()
                    if any(time.monotonic()-t > .32 for t in last.values()):
                        self.port.write(b"p\n")
                        self.free = 0
                        self.log("freno","se perdio el flujo TCS")
                        break
                    if line:
                        for side in seen:
                            if line.startswith("TCS "+side+" "):
                                last[side] = time.monotonic()
                        samples.append(line)
                        reason = observe(line)
                        if reason:
                            self.port.write(b"p\n")
                            self.log("freno",reason)
                            self.free = 0
                            break
                self.floor = floor
                self.log("piso en movimiento",samples)
        except BaseException:
            self.free = 0
            raise
        finally:
            if self.port:
                self.port.write(b"p\n")
            self.send("p")
        state = self.send("estado")
        if self.port and not any("# Garra: SLOT3" in line for line in state):
            raise RuntimeError("Sin estado de la MegaPi después del pulso.")

    def camera(self, url):
        with urlopen("http://127.0.0.1:8080/" + url, timeout=5) as r:
            return r.read()

    def command(self, line):
        self.log("operador", line)
        args = line.lower().split() or ["p"]
        cmd, values = args[0], args[1:]
        if cmd == "nota" and values:
            return
        if cmd == "ayuda":
            print(HELP)
        elif cmd == "margen" and len(values) == 4:
            free, speed, reserve, coast = map(float, values)
            if not all(math.isfinite(v) and v > 0 for v in (free,speed,reserve,coast)) or free <= reserve:
                raise ValueError("Usa cuatro medidas positivas y libre mayor que reserva.")
            self.floor.clear()
            self.free, self.speed, self.reserve, self.coast = free,speed,reserve,coast
        elif cmd in ("w","s","a","d","abrir","cerrar") and len(values) <= 1:
            ms = number(values[0] if values else 150, 20, 750)
            movement = {"w":"m 80 80", "s":"m -80 -80", "a":"m -80 80",
                        "d":"m 80 -80", "abrir":"gm 150", "cerrar":"gm -150"}
            pulse = f"{movement[cmd]} {ms}"
            if cmd in ("abrir","cerrar"):
                if self.free <= self.reserve:
                    raise ValueError("Mide el margen con la garra en su máxima extensión antes del pulso.")
                self.send(pulse, ms/1000 + .12)
                self.free = 0  # Cambió el contorno físico de las mordazas.
                self.send("estado")
            else:
                self.drive(pulse, ms)
        elif cmd == "eco" and not values:
            lines = self.stream("uv")
            cm = [float(m[1]) for s in lines if (m := re.search(r"P7\): ([\d.]+) cm", s))]
            misses = sum("SIN ECO" in s or "FUERA DE RANGO" in s for s in lines)
            self.log("eco", f"{len(cm)} válidos, {misses} sin eco/fuera de rango" +
                     (f", min/mediana/max cm {min(cm)}/{statistics.median(cm)}/{max(cm)}" if cm else ""))
        elif cmd == "piso" and not values:
            self.stream("t")
        elif cmd in ("cal","estado","p","cero") and not values:
            self.send("er" if cmd == "cero" else cmd)
        elif cmd == "guardar" and not values:
            result = self.send("guardar", 1)
            if self.port and "# Guardado en EEPROM" not in result:
                raise RuntimeError("Sin confirmación de EEPROM. Revisar antes de continuar.")
            self.send("cal")
            self.send("estado")
        elif cmd == "c" and len(values) in (1,2) and values[0] in COLORS and (
                len(values) == 1 or values[1] in ("i","d")):
            self.send(" ".join(args), 2.5)
        elif cmd in ("gt","gp","tt","tg"):
            limits = {"gt":[(1,3000)]*2, "gp":[(0,255)]*3, "tt":[(3,614)], "tg":[(1,60)]}
            if len(values) != len(limits[cmd]) or (cmd == "tg" and values[0] not in ("1","4","16","60")):
                raise ValueError("Parámetros incorrectos. Escribe ayuda.")
            for value, bounds in zip(values, limits[cmd]):
                number(value, *bounds)
            self.send(" ".join(args), 1)
        elif cmd == "ver" and len(values)==1 and values[0] in COLORS[1:5]:
            if self.port:
                self.log("vision", self.camera("modo?m=ARTEFACTO&c="+values[0].upper()).decode())
        elif cmd == "muestra" and not values:
            if self.port:
                items = json.loads(self.camera("telemetria?ultimos=20"))["items"]
                recent = [r for r in items if r.get("tipo")=="vision" and 0 <= time.time()-r["t_epoch"] < 2]
                if not recent:
                    raise ValueError("Sin detección reciente. Abre el stream y repite.")
                self.log("detección",recent[-1])
        elif cmd == "foto" and not values:
            if self.port:
                data = self.camera("raw")
                if not (data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9")):
                    raise ValueError("Sin fotograma completo. Abre el stream y repite.")
                photo = self.folder / (datetime.now().strftime("%H%M%S-%f")+".jpg")
                durable(photo, data)
                self.log("foto", photo.name)
        elif cmd == "geometria":
            path = VISION / "config.json" if self.port else self.folder / "config-demo.json"
            cfg = json.loads(path.read_text(encoding="utf8"))
            geometry = measured_geometry(values, cfg["camara"])
            cfg["geometria"].update(geometry)
            data = json.dumps(cfg, indent=2, ensure_ascii=False).encode()
            durable(self.folder / ("geometria-"+datetime.now().strftime("%H%M%S-%f")+".json"), data)
            durable(path, data)
            self.log("geometria guardada", geometry)
            print("Se aplicará al reiniciar visión. Termina con fin y vuelve a entrar.")
        else:
            raise ValueError("Orden inválida. Escribe ayuda.")

def number(value, low, high):
    n = int(value)
    if not low <= n <= high:
        raise ValueError(f"Valor fuera de {low}..{high}")
    return n


def measured_geometry(values, camera):
    if len(values) < 4:
        raise ValueError("Indica centro X y al menos tres pares Y:mm de cerca a lejos.")
    cx = number(values[0], 0, camera["ancho"]-1)
    points = []
    for pair in values[1:]:
        y, mm = pair.split(":")
        point = [number(y, 0, camera["alto"]-1), number(mm, 1, 4000)]
        if points and (point[0] >= points[-1][0] or point[1] <= points[-1][1]):
            raise ValueError("De cerca a lejos, Y debe bajar y mm aumentar.")
        points.append(point)
    return {"cx_garra": cx, "tabla_distancia": points}


def stop_process(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(6)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def practice(folder, demo):
    s = Session(folder)
    with ExitStack() as stack:
        if demo:
            durable(folder/"config-demo.json", b'{"camara":{"ancho":640,"alto":360},"geometria":{}}')
            print("DEMO: registra órdenes. Las lecturas y fotos requieren el robot.")
        else:
            import serial
            import fcntl
            lock = stack.enter_context((folder.parent/".control.lock").open("a"))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            cfg_path = VISION/"config.json"
            config = cfg_path.read_bytes()
            durable(folder/"vision-antes.json", config)
            device = json.loads(config)["serial"]["puerto"]
            if not Path(device).exists():
                device = "/dev/ttyUSB0"
            if not Path(device).exists():
                raise RuntimeError("MegaPi USB ausente")
            active = subprocess.run(["systemctl","is-active","--quiet","wro-vision"]).returncode == 0
            if active:
                stack.callback(subprocess.run, ["sudo","-n","systemctl","start","wro-vision"], check=True)
                subprocess.run(["sudo","-n","systemctl","stop","wro-vision"], check=True)
            if shutil.which("avrdude"):
                backup = folder/"eeprom-antes.hex"
                try:
                    read = subprocess.run(["avrdude","-p","m2560","-c","wiring","-P",device,"-b","115200",
                                       "-D","-U","eeprom:r:"+str(backup)+":i"], timeout=30)
                    success = read.returncode == 0
                except subprocess.TimeoutExpired:
                    success = False
                if not success or not backup.is_file() or not backup.read_bytes().strip().endswith(b":00000001FF"):
                    backup.unlink(missing_ok=True)
                    s.log("aviso","Sin copia binaria EEPROM. Revisar cal y estado antes de guardar.")
            else:
                s.log("aviso","avrdude ausente. Respaldo de EEPROM disponible solo como lecturas cal y estado.")
            s.port = stack.enter_context(serial.Serial(device,115200,timeout=.07,write_timeout=1,exclusive=True))
            time.sleep(3.5)
            s.port.reset_input_buffer()
            if not any("# Garra: SLOT3" in line for line in s.send("estado",1)):
                raise RuntimeError("El USB no confirmó el menú UVM. Comprueba el sketch.")
            stack.callback(s.port.write, b"p\n")
            s.send("p")
            s.send("cal",1)
            print("RESPALDO INICIAL", folder.name, flush=True)
            out = stack.enter_context((folder/"vision.log").open("w"))
            run_cfg = json.loads(config)
            run_cfg.setdefault("telemetria", {}).update(habilitado=True, guardar_archivo=True,
                                                        directorio_logs=str(folder.resolve()))
            run_path = folder/"vision-practica.json"
            durable(run_path,json.dumps(run_cfg).encode())
            camera = subprocess.Popen([sys.executable,"vision_server.py","--sin-serial","--web","--config",str(run_path)],
                                      cwd=VISION, stdout=out,stderr=out)
            stack.callback(stop_process,camera)
            stack.callback(lambda: durable(folder/"vision-despues.json",cfg_path.read_bytes()))
        print(HELP)
        while True:
            print("pista > ", flush=True)
            try:
                line = input().strip()
            except EOFError:
                break
            if line.lower() == "fin":
                break
            try:
                s.command(line)
            except ValueError as exc:
                s.log("aviso",str(exc))
            except OSError as exc:
                if line.split()[:1] not in (["foto"], ["ver"], ["muestra"]):
                    raise
                s.log("aviso de cámara", str(exc))
        s.send("p")
        s.log("fin","sesión cerrada")


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--demo",action="store_true")
    args.add_argument("--destino",type=Path)
    options = args.parse_args()
    root = options.destino or (Path.cwd()/"sesiones/demo" if options.demo else Path.home()/"pista-uvm/sesiones")
    folder = root / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True)
    print("Sesión:", folder, flush=True)
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    for name in ("SIGTERM","SIGHUP"):
        if hasattr(signal,name):
            signal.signal(getattr(signal,name),interrupted)
    practice(folder,options.demo)


if __name__ == "__main__":
    main()
