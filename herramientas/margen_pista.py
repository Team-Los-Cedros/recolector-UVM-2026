"""Medir separación al borde con una vista fija de las cuatro esquinas de la pista."""
import argparse
from datetime import datetime
import json
from pathlib import Path

import cv2
import numpy as np
from pista_uvm import durable


def clearances(points, width, height):
    corners = np.float32(points[:4])
    if not cv2.isContourConvex(corners) or cv2.contourArea(corners) < 100:
        raise ValueError("Marca las cuatro esquinas en orden alrededor del perímetro.")
    target = np.float32([(0,0),(width,0),(width,height),(0,height)])
    transform = cv2.getPerspectiveTransform(corners,target)
    projected = cv2.perspectiveTransform(np.float32([points[4:]]),transform)[0]
    return [float(min(x,width-x,y,height-y)) for x,y in projected]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ancho_cm",type=float)
    parser.add_argument("fondo_cm",type=float)
    parser.add_argument("--foto",type=Path,help="Imagen cenital del robot quieto")
    parser.add_argument("--camara",type=int,default=0)
    args = parser.parse_args()
    if not all(np.isfinite(x) and x > 0 for x in (args.ancho_cm,args.fondo_cm)):
        parser.error("Dimensiones interiores positivas en centímetros.")
    if args.foto:
        frame = cv2.imread(str(args.foto))
    else:
        cap = cv2.VideoCapture(args.camara,cv2.CAP_DSHOW)
        try:
            frame = None
            for _ in range(15):
                ok, frame = cap.read()
                if not ok:
                    break
        finally:
            cap.release()
    if frame is None:
        parser.error("Sin imagen. Comprueba --camara o usa --foto.")
    folder = Path(__file__).resolve().parents[1]/"sesiones/pista-oficial/mapa"/datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True)
    ok, jpg = cv2.imencode(".jpg",frame)
    if not ok:
        raise RuntimeError("Falló la captura")
    durable(folder/"pista.jpg",jpg.tobytes())
    # Mantener la misma escala en X e Y durante toda la selección.
    factor = min(1., 1120/frame.shape[1], 720/frame.shape[0])
    preview = cv2.resize(frame,None,fx=factor,fy=factor)
    points = []
    name = "Margen hasta la linea negra"
    def save():
        result = dict(ancho_cm=args.ancho_cm,fondo_cm=args.fondo_cm,puntos_px=points)
        if len(points) >= 5:
            result["libre_cm"] = min(clearances(points,args.ancho_cm,args.fondo_cm))
        durable(folder/"medidas.json",json.dumps(result,indent=2).encode())
        return result.get("libre_cm")
    def click(event,x,y,flags,param):
        if event == cv2.EVENT_LBUTTONDOWN:
            points.append([round(x/factor),round(y/factor)])
            try:
                save()
            except ValueError as exc:
                print(exc)
                points.pop()
    cv2.namedWindow(name)
    cv2.setMouseCallback(name,click)
    print("Primer lado = ancho. Pulsa las cuatro esquinas interiores del marco en orden.")
    print("Después marca los extremos de la huella del robot en el suelo, incluida la garra.")
    try:
        while cv2.getWindowProperty(name,cv2.WND_PROP_VISIBLE) >= 1:
            view = preview.copy()
            for i,pt in enumerate(points):
                xy = tuple(round(v*factor) for v in pt)
                cv2.circle(view,xy,4,(0,190,255),-1)
                cv2.putText(view,str(i+1),xy,0,.6,(0,0,255),2)
            cv2.imshow(name,view)
            key = cv2.waitKey(40) & 255
            if key == 27:
                break
            if key == 8 and points:
                points.pop()
                save()
            if key in (10,13):
                if len(points) < 8:
                    print("Marca cuatro esquinas y al menos cuatro extremos del robot.")
                    continue
                print(f"Distancia mínima estimada al marco: {save():.1f} cm. Registro: {folder}")
                break
    finally:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
