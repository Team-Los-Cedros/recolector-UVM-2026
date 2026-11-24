import sys, time, re, serial
# Una sola sesión (la MegaPi se reinicia al abrir el puerto).
# Pasos:  f <ms> [pwm] | g <ms> [pwm] (gira, + = derecha) | l <ms> (lee color) |
#         hasta <color_i> <color_d> <pasos> <ms>  (avanza por tramos hasta que ambos lean ese color)
#         raw <cmd> | cal...
s = serial.Serial('/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0', 115200, timeout=0.1)
time.sleep(3.5)
s.reset_input_buffer()

def tx(c): s.write((c + '\n').encode())
def rd(t):
    out = []; end = time.time() + t
    while time.time() < end:
        l = s.readline()
        if l: out.append(l.decode(errors='replace').rstrip())
    return out

pat = re.compile(r'TCS (IZQ|DER) C=(\d+) .*-> (\w+|\?) \(conf ([\d.]+)\)')
def leer(n=0.8):
    tx('t'); L = rd(n); tx('p'); rd(0.2)
    r = {}
    for l in L:
        m = pat.search(l)
        if m: r[m.group(1)] = (m.group(3), float(m.group(4)), int(m.group(2)))
    return r

def rumbo():
    tx('y'); L = rd(0.5); tx('y'); rd(0.2)
    for l in reversed(L):
        m = re.search(r'rumbo=(-?[\d.]+)', l)
        if m: return float(m.group(1))

a = sys.argv[1:]
i = 0
while i < len(a):
    c = a[i]
    if c == 'f':
        ms = int(a[i+1]); pwm = int(a[i+2]) if i+2 < len(a) and a[i+2].lstrip('-').isdigit() and abs(int(a[i+2]))<=255 and False else 80
        tx(f'm {pwm} {pwm} {ms}'); rd(ms/1000 + 0.5); print('f', ms, leer()); i += 2
    elif c == 'g':
        ms = int(a[i+1]); sg = 1 if ms > 0 else -1; ms = abs(ms)
        tx(f'm {70*sg} {-70*sg} {ms}'); rd(ms/1000 + 0.5)
        print('g', ms*sg, 'rumbo=', rumbo()); i += 2
    elif c == 'l':
        print('l', leer(1.2)); i += 1
    elif c == 'hasta':
        ci, cd, n, ms = a[i+1], a[i+2], int(a[i+3]), int(a[i+4])
        for k in range(n):
            tx(f'm 80 80 {ms}'); rd(ms/1000 + 0.5)
            r = leer()
            print('paso', k+1, r)
            if r.get('IZQ', ('',))[0] == ci and r.get('DER', ('',))[0] == cd:
                print('OK: ambos en', ci, cd); break
        i += 5
    elif c == 'seg':
        # seg <color> <cap_grados_rueda> <ms_por_tramo> <max_tramos>
        col, cap, ms, n = a[i+1], float(a[i+2]), int(a[i+3]), int(a[i+4])
        tx('er'); rd(0.3)
        for k in range(n):
            tx(f'm 80 80 {ms}'); rd(ms/1000 + 0.4)
            tx('e'); L = rd(0.4); tx('p'); rd(0.2)
            e = None
            for l in reversed(L):
                m = re.search(r'ENC izq=(-?[\d.]+) der=(-?[\d.]+)', l)
                if m: e = (float(m.group(1)), float(m.group(2))); break
            r = leer()
            print('tramo', k+1, 'enc', e, r, flush=True)
            if r.get('IZQ', ('',))[0] == col and r.get('DER', ('',))[0] == col:
                print('OK ambos en', col); break
            if e is None or max(abs(e[0]), abs(e[1])) + 0.5*0 >= cap:
                print('STOP por margen de encoders'); break
        i += 5
    elif c == 'rec':
        # rec <color> <cap_grados_rueda> <ms_total> [giro_inicial_grados]
        # avanza en rafagas de 250 ms manteniendo el rumbo del gyro (+ = derecha)
        col, cap, tot = a[i+1], float(a[i+2]), int(a[i+3])
        objetivo = float(a[i+4]) if i+4 < len(a) and re.match(r'^-?\d+(\.\d+)?$', a[i+4]) else 0.0
        i += 5 if (i+4 < len(a) and re.match(r'^-?\d+(\.\d+)?$', a[i+4])) else 4
        tx('er'); rd(0.3)
        t0 = 0
        while t0 < tot:
            tx('estado'); L = rd(0.6)
            rum = enc = None
            for l in L:
                m = re.search(r'rumbo=(-?[\d.]+)', l)
                if m: rum = float(m.group(1))
                m = re.search(r'Encoders: izq=(-?[\d.]+) der=(-?[\d.]+)', l)
                if m: enc = (float(m.group(1)), float(m.group(2)))
            err = (rum - objetivo) if rum is not None else 0
            if rum is None or enc is None:
                print('sin dato de gyro/enc: paro'); break
            if abs(err) > 20: print('DESVIO > 20 grados: paro', rum, enc); break
            if max(abs(enc[0]), abs(enc[1])) >= cap: print('STOP margen encoders', enc, 'rumbo', rum); break
            k = 3.0; corr = max(-30, min(30, k * err))
            pl, pr = int(80 - corr), int(80 + corr)
            tx(f'm {pl} {pr} 250'); rd(0.55)
            t0 += 250
            print(f'rum={rum:.1f} enc={enc} pwm={pl},{pr}', flush=True)
            if t0 % 1000 == 0:
                rc = leer(0.4)
                if rc.get('IZQ', ('',))[0] == col and rc.get('DER', ('',))[0] == col:
                    print('OK ambos en', col, rc, 'enc', enc); break
        print('colores', leer())
    elif c == 'raw':
        tx(a[i+1]); print(rd(1.5)); i += 2
    else:
        i += 1
tx('p'); s.close()
