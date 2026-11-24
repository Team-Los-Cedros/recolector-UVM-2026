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

## Verificación de la garra en vacío (21 de noviembre de 2026)

Las distancias en píxeles corresponden a la separación entre las marcas
naranjas de las mordazas con la webcam fija en el encuadre de la prueba.

| Prueba | Resultado |
|---|---|
| Apertura desde el cierre completo | Un pulso de 2015 ms a PWM 150 dejó 140,4 px de separación. El límite provisional en ese encuadre es 145 px. |
| Cierre de un solo pulso desde 128,7 px | En aproximadamente 1920 ms a PWM 150, las puntas se tocaron. |
| Apertura máxima observada | Cerca de 158 px. Desde esa posición, varios pulsos de cierre dejaron las mordazas casi inmóviles. |
| Cierre después de reacoplar el mecanismo a mano | Desde 152,2 px, cinco pulsos de cierre juntaron las puntas. |

Se guardó el tiempo de apertura de **2015 ms** en la EEPROM de la MegaPi y se
verificó su lectura tras reiniciarla. Se mantuvieron el canal B del SLOT3,
el sentido +1, los PWM de apertura y cierre en 150, el PWM de sujeción en 60
y el tiempo de cierre en **800 ms**. La calibración de color quedó intacta.

El tiempo de apertura guardado se probó partiendo del cierre completo.
Queda pendiente calibrar y guardar el cierre desde la apertura de trabajo,
validar la fuerza de sujeción con la pieza y comprobar un procedimiento
repetible de recuperación del atasco.

## Verificación del giroscopio en casa (21 de noviembre de 2026)

Se usó `prueba_componentes.ino` y se registró el rumbo con `y`. Los giros se hicieron a mano sobre una superficie plana, con las ruedas apoyadas. El rumbo inicial fue de 0,00°.

| Prueba | Resultado |
|---|---:|
| Giro horario de 90° | −91,01° de cambio |
| Giro antihorario de 90° de regreso | +90,03° de cambio |
| Rumbo al volver a la referencia inicial | −0,98° |
| Rumbo al cabo de 60 s en reposo | −1,20° |
| Deriva neta en 60 s | −0,22° |

El giroscopio marcó cerca de ±90° y la magnitud de la deriva quedó por debajo de la referencia de ≈1° por minuto. El contador de errores del giroscopio permaneció en cero. La calibración de color quedó intacta.

## Tracción y giros con los motores en marcha (21 de noviembre de 2026)

Pruebas sobre la pista con tracción izquierda en SLOT1, derecha en SLOT2 y garra en SLOT3. El sketch de componentes recibió pulsos de movimiento desde la Pi. Las pruebas por posición se hicieron por etapas con lecturas entre pulsos.

| Prueba | Resultado observado |
|---|---|
| Reposo durante 15 s | 0° en ambos encoders, cambio de rumbo de +0,03° y cero fallos de lectura. |
| Tres rectas de 1 s a PWM 80 | Izquierda/derecha en grados de rueda: 240,7/235,8; 244,6/243,6; 246,5/245,5. El giroscopio acumuló 16, 17 y 21 fallos respectivamente. |
| Dos avances consecutivos filmados | El punto final quedó a unos 1–2 px de la prolongación del primer tramo en ambas cámaras fijas. La referencia fue la dirección del primer tramo. |
| Parada por promedio de 100° | Izquierda +102,7°, derecha +91,9°. Al regresar al origen quedaron +6,8° y −2,9°. Conviene comprobar cada rueda al decidir la parada. |
| Primera estimación con regla | Avances aparentes de 9,7 y 14,4 cm con promedios de encoder de 178,1° y 268,6°. |
| Repetición con cámara baja y consignas de 185° y 278° | Primera parada: 182,9°/183,9° y 9,5 cm aparentes. Segunda parada: 274,9°/277,8° y 14,1 cm aparentes. La escala estaba más cerca de la cámara que la rueda. |
| Regreso y repetición de 278° | La posición longitudinal de la rueda en la imagen quedó a 3 px del inicio y a 3,5 px de la parada anterior. En el último avance los encoders marcaron 277,8°/279,8°. |
| Giro corto y regreso | Dos pulsos opuestos de 350 ms a PWM 80 dieron −22,55° y +23,39° en el giroscopio. En otra prueba con consigna de +30° y retorno a 0°, el sensor indicó +29,46° y +1,06°. |
| Un giro de 90° y su regreso | Hacia el objeto claro: −89,24°, encoders −317,0°/+317,0°, 7 fallos del giroscopio. De regreso hacia la pieza oscura: +90,20°, encoders +331,6°/−314,0°, 34 fallos. Cada ángulo es relativo al comienzo de su maniobra. La cámara elevada estimó 1,5° de diferencia entre las orientaciones inicial y final. |

**Configuración provisional para rectas cortas:** 18,5 grados de rueda por centímetro, con consignas ensayadas de 185° para 10 cm y 278° para 15 cm. La comparación depende de la perspectiva. Validar la repetibilidad con una pieza en la garra antes de usar el factor en la ronda. Para el giro, repetir los 90° en ambos sentidos y registrar los fallos de lectura por maniobra antes de ajustar la escala del giroscopio.

**Cámara del robot:** brillo guardado en la Pi de −24 a +32. Con el robot quieto la mediana de luminosidad del fotograma pasó de 44 a 91 sobre 255. Comprobar la detección de las piezas con este ajuste.

Fotos de la regla: [inicio](registros/2026-11-21/regla-inicio.jpg), [10 cm](registros/2026-11-21/regla-10.jpg) y [15 cm](registros/2026-11-21/regla-15.jpg). Fotos del giro: [inicio](registros/2026-11-21/giro-inicio.jpg), [90°](registros/2026-11-21/giro-90.jpg) y [regreso](registros/2026-11-21/giro-regreso.jpg).

### Repetición de los giros y avance con carga de prueba

Dos ciclos adicionales con PWM 80, parada por rumbo y la misma cámara elevada. El error visual es la diferencia de orientación en la imagen entre el inicio y el regreso de cada ciclo.

| Ciclo | Ida / regreso según el giroscopio | Fallos de lectura, ida / regreso | Diferencia visual al regresar |
|---|---|---|---|
| 2 | −89,28° / +89,98° | 18 / 31 | 0,7° |
| 3 | −89,62° / +89,94° | 28 / 33 | 0,4° |

Las fotos [al iniciar](registros/2026-11-21/serie-0.jpg), [tras el ciclo 2](registros/2026-11-21/serie-2.jpg) y [tras el ciclo 3](registros/2026-11-21/serie-4.jpg) dan una diferencia acumulada de aproximadamente 1,0° en estos dos ciclos.

La garra recogió un módulo puente H L298N usado como carga de prueba bajo observación de la cámara elevada. Con consigna de 278° por rueda y tolerancia de 4°, los encoders marcaron −274,9°/−274,9° en retroceso y +274,9°/+274,9° en avance. El módulo permaneció entre las mordazas en ambas paradas. En el avance, el giroscopio terminó en +0,48° y registró 22 fallos. Los 15 cm son nominales según el factor provisional. Esta prueba confirma el transporte con carga; la distancia física con carga queda pendiente de medir con escala.

Con brillo +32, el modo ARTEFACTO/AUTO indicó «sin objeto» en la captura previa al acercamiento, con el L298N visible. Este ensayo mecánico no evalúa la detección de objetos de la competencia. El siguiente ensayo de visión usará un objeto de color de práctica. [Captura del detector](registros/2026-11-21/deteccion-en-aproximacion.jpg) · [Recogida](registros/2026-11-21/serie-agarre.jpg) · [Tras retroceder](registros/2026-11-21/serie-carga0.jpg) · [Tras avanzar](registros/2026-11-21/serie-carga15.jpg)

Al terminar, se abrió la garra y el robot retrocedió al entorno del cruce donde comenzaron los giros. La cámara confirmó la separación del módulo. [Posición final](registros/2026-11-21/fin-traccion.jpg)

### Recogida y entrega de la torre amarilla y el carrete blanco

**Cámara.** El ajuste automático de `vision/calibrar_camara.py` elevó la luminancia media de 89 a 122 sobre 255 con el robot quieto. Más tarde se inclinó la cámara hacia abajo y se aplicó otro ajuste. La configuración final de la Pi quedó en 640 × 360 MJPG, exposición manual de 300 (30 ms), ganancia 40, brillo +32, contraste 63, saturación 68 y balance de blancos automático. La nueva vista mostró las puntas de las mordazas y permitió comprobar la entrada de las piezas. Quedó pendiente recalibrar el centro de la garra y la tabla de distancia para la nueva inclinación. [Antes](registros/2026-11-21/recoleccion-luz-antes.jpg) · [Ajuste automático](registros/2026-11-21/recoleccion-luz-auto.jpg) · [Vista inclinada](registros/2026-11-21/recoleccion-camara-inclinada.jpg)

**Torre amarilla.** El modo `TORRE_REC` encontró la pieza amarilla con tapa blanca y negra. El primer agarre se soltó al retroceder. En la prueba siguiente hicieron falta más profundidad entre las mordazas, un cierre más largo para recuperar el mecanismo tras abrirlo demasiado y ayuda manual para recolocar la pieza. Tras recolocarla, dos pulsos de cierre a PWM −150 de 750 y 700 ms permitieron comprobar la sujeción con un retroceso. El robot transportó y soltó la torre a la izquierda del bloque azul. El bloque azul giró ligeramente durante la maniobra. [Entrega de la torre](registros/2026-11-21/recoleccion-amarillo-entregado.jpg)

**Carrete blanco.** Se alineó con la cámara del robot y se verificó la entrada entre las mordazas. Con la apertura de ese ensayo, el cierre se hizo con `gm -150` en pulsos de 750, 450 y 220 ms. El carrete acompañó al robot durante el retroceso y el traslado. Se soltó cerca de la torre con `gm 150 680` y se comprobó que quedó en la pista al retirar el robot. [Entrada del carrete](registros/2026-11-21/recoleccion-carrete-entre-mordazas.jpg) · [Entrega del carrete](registros/2026-11-21/recoleccion-carrete-entregado.jpg)

**Para repetir.** Conservar una apertura de trabajo que deje pasar la pieza sin llevar el mecanismo a su extremo. Comprobar que la pieza entró lo suficiente entre las mordazas, ajustar el cierre desde esa apertura y verificar la sujeción con un retroceso corto antes del traslado. Los tiempos anotados corresponden a las aperturas y piezas del ensayo. La conducción se hizo por Wi-Fi con pulsos supervisados desde la cámara del robot y las dos cámaras de la PC.

## Orden de prueba en la pista de competencia

1. Sobre la pista real, comprueben los colores de ambos TCS con `t`. Si la clasificación falla, recalibren los colores afectados. Si cambian la ganancia o el tiempo de integración, repitan la calibración de los seis colores y guarden con `guardar`.
2. Con el robot quieto, usen `uv` frente a la pieza real a varias distancias y posiciones. Anoten desde dónde la detecta de forma estable y cuándo pierde el eco. Repitan con la garra en las posiciones que se usarán en la ronda.
3. Prueben un tramo recto corto a baja potencia. Registren el giro y los grados de rueda con `y` y `e`. Después comprueben una recogida y suelta con la pieza real antes de pasar a una corrida completa.
### Evidencia de las sesiones anteriores

El 9 de noviembre se guardó el negro derecho en EEPROM (C=471). En el vértice, el lado derecho clasificó negro y el izquierdo azul con confianza 1,8. El negro izquierdo había dado confianza 1,1–1,5. Falta comprobar la transición física sobre ambos lados bajo la luz oficial. En otra prueba de tracción hubo una desconexión USB de la MegaPi y la Pi tenía un indicador histórico de subtensión. Revisar alimentación al comenzar. [Vista del vértice entre amarillo y azul](registros/2026-11-09/vertice-amarillo-azul.jpg) · [Calibración de colores guardada](respaldo/calibracion_colores.txt) · [Apertura de la garra desde cierre completo](registros/2026-11-21/garra-apertura-desde-cierre.jpg)

La [hoja de pista UVM](../../PISTA.md) reúne el mando, las medidas prioritarias y el procedimiento de recuperación.

En la sesión del 21 de noviembre el botón del pin 4 registró cinco pulsaciones y cinco liberaciones en 15 segundos. En la prueba estacionaria de blanco y negro ambos TCS reconocieron las dos muestras, y el derecho alcanzó confianza 2,8 cuando quedó centrado sobre el negro. La siguiente prueba de borde es dinámica sobre el ancho y el brillo oficiales.
