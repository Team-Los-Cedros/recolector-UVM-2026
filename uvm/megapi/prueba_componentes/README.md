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
| Ultrasónico Makeblock | Puerto 7 |

Los pines del SLOT4 que siguen libres son A5, 2 y 5 (A5 dio problemas con el sensor izquierdo). No carguen programas de mBlock que usen el "motor 4": ese canal usa A3, A2 y el pin 4, y chocaría con el TCS derecho y con el botón.

## Cargar

Desde Arduino IDE: placa **Arduino Mega or Mega 2560**, abrir `prueba_componentes.ino` y subir. Luego el Monitor Serie a **115200** con **"Nueva línea"**.

Si la Pi tiene abierto el puerto serie (servicio `wro-vision`), hay que detenerlo antes de subir.

## Pruebas en casa

1. `estado`: los dos TCS y el giroscopio deben decir OK.
2. **Ultrasónico**, con el robot quieto. Pongan un cartón plano perpendicular al sensor, a 10, 20 y 40 cm medidos desde su cara. Usen `u` para una lectura o `uv` para observar varias. Anoten la distancia real y el rango de lecturas. Luego retiren el cartón y apunten a un espacio despejado para comprobar `SIN ECO`. Si hay lecturas cortas sin cartón, revisen si el sensor apunta al suelo o a la garra. `uv` vuelve a desactivar la lectura continua y `p` detiene todas las lecturas.
3. **Sensores de color**, si tienen una muestra de la pista:
   1. `t` muestra las lecturas en vivo.
   2. Si sale `SATURADO` sobre blanco, bajen la ganancia (`tg 1`) o el tiempo (`tt 12`).
   3. Pongan los dos sensores sobre cada color y calibren: `c blanco`, `c amarillo`, `c verde`, `c rojo`, `c azul`, `c negro`. Si solo uno está sobre el color, añadan `i` o `d` (ej. `c rojo d`).
   4. Comprueben con `t`: la confianza debería ser mayor que 2.
   5. `guardar`.
4. **Garra:**
   1. `gm 100 300` y `gm -100 300` muestran qué signo abre. Si `ga` cierra, usen `ginv`.
   2. Si no se mueve nada, prueben `gport a`.
   3. Ajusten el tiempo hasta que abra o cierre por completo sin forzar: `gt 700 700`.
   4. Ajusten las potencias con `gp 150 150 60`. La última es la fuerza con que sujeta la pieza: lo justo para que no se caiga al moverse; demasiada calienta el motor.
   5. `guardar`.
5. **Movimiento, con el robot levantado:**
   1. `m 80 0 1000` debe girar la rueda izquierda hacia delante y `m 0 80 1000` la derecha.
   2. Con `e`, los dos encoders deben aumentar.
6. **Giroscopio:**
   1. `y` muestra el rumbo.
   2. Giren el robot a mano 90°: debe marcar cerca de ±90.
   3. Quieto, no debe derivar más de ~1° por minuto.

`p` o una línea vacía paran todo.

## Verificación del ultrasónico en casa (21 de noviembre de 2026)

Se comprobó el puerto 7 con el robot quieto y un cartón a distancias aproximadas.

| Distancia de referencia | Lecturas del ultrasónico |
|---|---|
| Sin cartón, al inicio | `SIN ECO` |
| 10 cm | 10,5–10,6 cm, sin pérdidas de eco |
| 20 cm, primera prueba | Principalmente 20,7–21,5 cm, con dos pérdidas de eco y algunas lecturas más altas al final |
| 20 cm, segunda prueba | 19,5–19,6 cm, sin pérdidas de eco |
| 40 cm | Principalmente 37–40 cm, con una lectura aislada de 42,9 cm |

Queda verificar la detección con la pieza real en la pista y con la garra en sus posiciones de trabajo.

## Orden de prueba en la pista de competencia

1. Sobre la pista real, comprueben los colores de ambos TCS con `t`. Si la clasificación falla, recalibren los colores afectados. Si cambian la ganancia o el tiempo de integración, repitan la calibración de los seis colores y guarden con `guardar`.
2. Con el robot quieto, usen `uv` frente a la pieza real a varias distancias y posiciones. Anoten desde dónde la detecta de forma estable y cuándo pierde el eco. Repitan con la garra en las posiciones que se usarán en la ronda.
3. Prueben un tramo recto corto a baja potencia. Registren el giro y los grados de rueda con `y` y `e`. Después comprueben una recogida y suelta con la pieza real antes de pasar a una corrida completa.
