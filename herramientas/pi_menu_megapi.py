import sys, time, serial
# uso: python3 mp.py "cmd1" "cmd2" ...   (espera 1.5 s por comando, o 0.5 s si empieza con ~N = segundos)
s = serial.Serial('/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0', 115200, timeout=0.2)
time.sleep(3)  # abrir el puerto reinicia la MegaPi
s.reset_input_buffer()
def leer(t):
    end = time.time() + t
    while time.time() < end:
        l = s.readline()
        if l:
            print(l.decode(errors='replace').rstrip())
for c in sys.argv[1:]:
    w = 1.5
    if c.startswith('~'):
        n, c = c[1:].split(' ', 1)
        w = float(n)
    print('>>', c)
    s.write((c + '\n').encode())
    leer(w)
s.write(b'p\n')
s.close()
