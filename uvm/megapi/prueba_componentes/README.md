# Prueba y calibración de componentes (MegaPi)

Sketch con un menú por el Monitor Serie para comprobar y calibrar cada pieza del robot recolector antes de programar la ronda.

## Conexiones

| Componente | Conexión |
|---|---|
| TCS34725 derecho | SDA → A3, SCL → A2 (conector del SLOT4), VIN → 5 V, GND |
| TCS34725 izquierdo | SDA → A4, SCL → A1 (conector del SLOT4), VIN → 5 V, GND |
| Garra (motor DC) | SLOT3 con su driver (canal B por defecto) |
| Motores de tracción | SLOT1 (izquierdo), SLOT2 (derecho) |
| Giroscopio | Puerto 8 |
| Botón de arranque | Pin 4 |
| Ultrasónico (pendiente) | Puerto 7 |

Los pines del SLOT4 que siguen libres son A5, 2 y 5 (A5 dio problemas con el sensor izquierdo). No carguen programas de mBlock que usen el "motor 4": ese canal usa A3, A2 y el pin 4, y chocaría con el TCS derecho y con el botón.

## Cargar

Desde Arduino IDE: placa **Arduino Mega or Mega 2560**, abrir `prueba_componentes.ino` y subir. Luego el Monitor Serie a **115200** con **"Nueva línea"**.

Si la Pi tiene abierto el puerto serie (servicio `wro-vision`), hay que detenerlo antes de subir.

## Orden de prueba sugerido

1. `estado`: los dos TCS y el giroscopio deben decir OK.
2. **Sensores de color**, con el robot sobre la pista:
   1. `t` muestra las lecturas en vivo.
   2. Si sale `SATURADO` sobre blanco, bajen la ganancia (`tg 1`) o el tiempo (`tt 12`).
   3. Pongan los dos sensores sobre cada color y calibren: `c blanco`, `c amarillo`, `c verde`, `c rojo`, `c azul`, `c negro`. Si solo uno está sobre el color, añadan `i` o `d` (ej. `c rojo d`).
   4. Comprueben con `t`: la confianza debería ser mayor que 2.
   5. `guardar`.
3. **Garra:**
   1. `gm 100 300` y `gm -100 300` muestran qué signo abre. Si `ga` cierra, usen `ginv`.
   2. Si no se mueve nada, prueben `gport a`.
   3. Ajusten el tiempo hasta que abra o cierre por completo sin forzar: `gt 700 700`.
   4. Ajusten las potencias con `gp 150 150 60`. La última es la fuerza con que sujeta la pieza: lo justo para que no se caiga al moverse; demasiada calienta el motor.
   5. `guardar`.
4. **Movimiento, con el robot levantado:**
   1. `m 80 0 1000` debe girar la rueda izquierda hacia delante y `m 0 80 1000` la derecha.
   2. Con `e`, los dos encoders deben aumentar.
5. **Giroscopio:**
   1. `y` muestra el rumbo.
   2. Giren el robot a mano 90°: debe marcar cerca de ±90.
   3. Quieto, no debe derivar más de ~1° por minuto.

`p` o una línea vacía paran todo.
