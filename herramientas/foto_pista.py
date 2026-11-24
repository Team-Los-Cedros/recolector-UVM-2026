import cv2, sys, os
out = sys.argv[1]
# prueba índices; guarda una foto por cámara encontrada
for i in range(4):
    cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
    if not cap.isOpened():
        continue
    ok = False
    for _ in range(15):
        ok, f = cap.read()
    if ok:
        p = f"{out}_{i}.jpg"
        cv2.imwrite(p, f)
        print(i, f.shape, p)
    cap.release()
