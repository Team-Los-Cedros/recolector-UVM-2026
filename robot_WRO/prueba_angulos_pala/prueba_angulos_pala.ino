// ============================================================================
//  PRUEBA DE ANGULOS DE LA PALA
//
//  Mueve la pala (servo del pin 5) por todos los angulos que usa
//  prueba_wwl1_NACIONAL_rojas, para ajustar la camara respecto de la pala con
//  un objeto en la garra.
//
//  CON EL BOTON (sin computadora, la Pi puede seguir conectada por USB):
//    - pulsacion corta : pasa al angulo siguiente de la lista
//    - pulsacion larga : abre / cierra la garra (mas de 0,8 s)
//
//  POR EL MONITOR SERIE (115200, "Nueva linea"):
//    n / p      angulo siguiente / anterior de la lista
//    + / -      suma / resta 1 grado al angulo actual
//    a / c      abrir / cerrar la garra
//    l          muestra la lista de angulos
//    <numero>   va a ese angulo (0-180), por ejemplo: 95
//
//  La pala siempre se mueve de a un grado (MS_POR_GRADO) para que la torre
//  no se caiga de la garra. Todo lo que imprime empieza con "# " para que la
//  Pi lo trate como telemetria.
//  OJO: abrir el monitor serie reinicia la MegaPi (vuelve a 0 con la garra abierta).
// ============================================================================

#include <Arduino.h>
#include <Wire.h>
#include <MeMegaPi.h>

#define BOTON_PIN        4
#define PIN_SERVO_PALA   5
#define MS_POR_GRADO    25     // velocidad de la pala (25 ms por grado = 40 grados/s)
#define MS_PULSO_LARGO 800     // pulsacion mas larga que esto = abrir/cerrar garra

// Mismos valores que prueba_wwl1_NACIONAL_rojas (seccion 11)
const int GARRA_ABIERTA_S1 = 0;
const int GARRA_CERRADA_S1 = 108;
const int GARRA_CERRADA_S2 = 58;
const int GARRA_ABIERTA_S2 = 180;

struct AnguloPala {
  int         angulo;
  const char *uso;
};

const AnguloPala ANGULOS[] = {
  {   0, "inicio / tope de subir_pala()" },
  {  33, "anguloTransportar = recolectar(1)" },
  {  40, "PALA_TRAS_SOLTAR" },
  {  70, "posicionar()" },
  {  73, "anguloDepositar" },
  {  95, "PALA_VER_DESTINO" },
  {  96, "recolectar(4)" },
  { 105, "anguloBajar = bajar_pala()" },
  { 111, "recolectar(3)" },
  { 114, "final de parabrisas()" },
  { 115, "parabrisas()" },
  { 120, "recolectar(2)" },
  { 122, "barrer() (122.5 en el codigo)" },
};
const int N_ANGULOS = sizeof(ANGULOS) / sizeof(ANGULOS[0]);

MePort placaExpansora(7);   // RJ25 puerto 7 -> pines A10 y A11
Servo  servoGarra1;
Servo  servoGarra2;
Servo  miServo;

int  indice       = 0;      // posicion en la lista
int  anguloActual = 0;
bool garraCerrada = false;

void moverPala(int destino)
{
  destino = constrain(destino, 0, 180);
  while (anguloActual != destino) {
    anguloActual += (destino > anguloActual) ? 1 : -1;
    miServo.write(anguloActual);
    delay(MS_POR_GRADO);
  }
  miServo.write(anguloActual);
}

void mostrarActual(void)
{
  Serial.print(F("# PALA "));
  Serial.print(anguloActual);
  if (ANGULOS[indice].angulo == anguloActual) {
    Serial.print(F("  ["));
    Serial.print(indice + 1);
    Serial.print('/');
    Serial.print(N_ANGULOS);
    Serial.print(F("] "));
    Serial.print(ANGULOS[indice].uso);
  } else {
    Serial.print(F("  (fuera de la lista)"));
  }
  Serial.print(F("  garra "));
  Serial.println(garraCerrada ? F("cerrada") : F("abierta"));
}

void irAIndice(int i)
{
  indice = (i + N_ANGULOS) % N_ANGULOS;
  moverPala(ANGULOS[indice].angulo);
  mostrarActual();
}

void alternarGarra(void)
{
  garraCerrada = !garraCerrada;
  servoGarra1.write(garraCerrada ? GARRA_CERRADA_S1 : GARRA_ABIERTA_S1);
  servoGarra2.write(garraCerrada ? GARRA_CERRADA_S2 : GARRA_ABIERTA_S2);
  delay(300);
  mostrarActual();
}

void mostrarLista(void)
{
  for (int i = 0; i < N_ANGULOS; i++) {
    Serial.print(F("#   "));
    Serial.print(i + 1);
    Serial.print(F(". "));
    Serial.print(ANGULOS[i].angulo);
    Serial.print(F("  "));
    Serial.println(ANGULOS[i].uso);
  }
}

void revisarBoton(void)
{
  if (digitalRead(BOTON_PIN) == HIGH) return;
  delay(30);                                   // antirrebote
  if (digitalRead(BOTON_PIN) == HIGH) return;

  uint32_t t0 = millis();
  while (digitalRead(BOTON_PIN) == LOW) delay(5);
  if (millis() - t0 >= MS_PULSO_LARGO) alternarGarra();
  else                                 irAIndice(indice + 1);
  delay(150);
}

// Lee una linea del monitor serie. Las lineas que manda la Pi ("T ...") se ignoran.
void revisarSerial(void)
{
  static char linea[24];
  static byte largo = 0;

  while (Serial.available()) {
    char ch = Serial.read();
    if (ch != '\n' && ch != '\r') {
      if (largo < sizeof(linea) - 1) linea[largo++] = ch;
      continue;
    }
    if (largo == 0) continue;
    linea[largo] = '\0';
    largo = 0;

    if (linea[1] != '\0' && !isDigit(linea[0])) continue;   // no es un comando

    switch (linea[0]) {
      case 'n': irAIndice(indice + 1); break;
      case 'p': irAIndice(indice - 1); break;
      case '+': moverPala(anguloActual + 1); mostrarActual(); break;
      case '-': moverPala(anguloActual - 1); mostrarActual(); break;
      case 'a': if (garraCerrada)  alternarGarra(); break;
      case 'c': if (!garraCerrada) alternarGarra(); break;
      case 'l': mostrarLista(); break;
      default:
        if (isDigit(linea[0])) {
          moverPala(atoi(linea));
          for (int i = 0; i < N_ANGULOS; i++)   // si esta en la lista, lo marca
            if (ANGULOS[i].angulo == anguloActual) indice = i;
          mostrarActual();
        }
    }
  }
}

void setup()
{
  Serial.begin(115200);
  pinMode(BOTON_PIN, INPUT_PULLUP);

  miServo.attach(PIN_SERVO_PALA);
  servoGarra1.attach(placaExpansora.pin1());   // A10
  servoGarra2.attach(placaExpansora.pin2());   // A11
  miServo.write(0);
  servoGarra1.write(GARRA_ABIERTA_S1);
  servoGarra2.write(GARRA_ABIERTA_S2);
  delay(700);

  Serial.println(F("# PRUEBA DE ANGULOS DE LA PALA"));
  Serial.println(F("# boton: corto = siguiente, largo = garra | serie: n p + - a c l <numero>"));
  mostrarLista();
  mostrarActual();
}

void loop()
{
  revisarBoton();
  revisarSerial();
}
