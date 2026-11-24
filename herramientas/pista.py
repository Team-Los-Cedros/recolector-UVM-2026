"""Lanzador Windows para la práctica. Requiere Python y OpenSSH en la PC."""
import argparse
from datetime import datetime
import os
import re
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "sesiones" / "pista-oficial"
SSH_OPTIONS = ["-o","BatchMode=yes","-o","ConnectTimeout=8",
               "-o","ServerAliveInterval=5","-o","ServerAliveCountMax=2"]


def backup(host):
    target = BASE / "recuperado"
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(["scp",*SSH_OPTIONS,"-r",host+":pista-uvm/sesiones",str(target)],check=True)
    print("Copia de la Pi:",target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modo",nargs="?",choices=["robot","demo","recuperar"],default="robot")
    parser.add_argument("--host", default="pi", help="Alias SSH o pi@IP")
    args = parser.parse_args()
    if args.host.startswith("-"):
        parser.error("Host no válido")
    script = HERE / "pista_uvm.py"
    if args.modo == "demo":
        return subprocess.call([sys.executable,str(script),"--demo","--destino",str(BASE/"demo")])
    if args.modo == "recuperar":
        backup(args.host)
        return 0
    ssh = ["ssh",*SSH_OPTIONS,args.host]
    subprocess.run([*ssh,"mkdir -p ~/pista-uvm && python3 -c 'import serial'"],check=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    remote = f"pista-uvm/control-{stamp}.py"
    subprocess.run(["scp",*SSH_OPTIONS,str(script),args.host+":"+remote], check=True)
    BASE.mkdir(parents=True, exist_ok=True)
    transcript = BASE / ("consola-"+stamp+".txt")
    print("Registro en esta PC:",transcript, flush=True)
    print("Video en http://127.0.0.1:18080/stream (mientras dure esta consola)",flush=True)
    with transcript.open("xb") as log:
        # Sin pseudo-terminal: el robot recibe una orden por línea y EOF al cerrar.
        with subprocess.Popen(["ssh","-T",*SSH_OPTIONS,"-L","127.0.0.1:18080:127.0.0.1:8080",
                               args.host,"python3 -u "+remote],stdout=subprocess.PIPE) as proc:
            try:
                for data in proc.stdout:
                    log.write(data)
                    log.flush()
                    os.fsync(log.fileno())
                    message = data.decode("utf-8","replace").rstrip()
                    print(message,flush=True)
                    ready = re.fullmatch(r"RESPALDO INICIAL ([0-9-]+)",message)
                    if ready:
                        target = BASE / "antes" / stamp
                        target.mkdir(parents=True)
                        subprocess.run(["scp",*SSH_OPTIONS,"-r",args.host+":pista-uvm/sesiones/"+ready[1],
                                        str(target)],check=True)
            except BaseException:
                proc.terminate()
                raise
            status = proc.wait()
    backup(args.host)
    return status


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError,subprocess.CalledProcessError,KeyboardInterrupt) as exc:
        print("Sesión interrumpida.",exc,"Al reconectar: python herramientas/pista.py recuperar",file=sys.stderr)
        sys.exit(1)
