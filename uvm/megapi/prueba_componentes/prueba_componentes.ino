// ============================================================================
//  PRUEBA Y CALIBRACION DE COMPONENTES - Robot recolector UVM 2026 (MegaPi)
// ============================================================================
//  Menu por el Monitor Serie (115200 baudios, fin de linea "Nueva linea").
//  Escribe "h" para ver los comandos.
//
//  Que se prueba:
//    - 2 sensores de color TCS34725 bajo los brazos (I2C por software, uno por
//      sensor, porque los dos tienen la direccion fija 0x29):
//          Derecho:   SDA = A3, SCL = A2   (conector del SLOT4)
//          Izquierdo: SDA = A4, SCL = A1   (conector del SLOT4)
//      Alimentar el modulo por VIN = 5 V y GND.
//    - Garra Ultimate 2.0: motor DC en el SLOT3 (canal B por defecto, se puede
//      cambiar a A con "gport a").
//    - Giroscopio MPU6050 (I2C del hardware, 0x68), solo eje Z.
//    - Sensor ultrasonico Makeblock en el puerto 7.
//    - Motores de traccion con encoder: SLOT1 (izq) y SLOT2 (der).
//    - Boton de arranque en el pin 4.
//
//  La calibracion de colores y los parametros de la garra se guardan en la
//  EEPROM (comando "guardar") y se cargan solos al encender.
//
//  OJO: NO usar ningun programa que mueva el "motor 4" de mBlock (PORT4A).
//  Ese canal saca senales por A3, A2 y el pin 4: chocaria con el TCS derecho
//  y con el boton de arranque.
// ============================================================================

#include <Arduino.h>
#include <Wire.h>
#include <EEPROM.h>
#include <math.h>
#include <MeMegaPi.h>

// ---------------------------------------------------------------- Pines -----
#define PIN_TCS_DER_SDA  A3
#define PIN_TCS_DER_SCL  A2
#define PIN_TCS_IZQ_SDA  A4
#define PIN_TCS_IZQ_SCL  A1
#define BOTON_PIN        4

#define MPU_ADDR         0x68
#define TCS_ADDR         0x29

// --------------------------------------------------------------- Colores ----
enum { C_BLANCO, C_AMARILLO, C_VERDE, C_ROJO, C_AZUL, C_NEGRO, N_COLORES };
const char *const NOMBRE_COLOR[N_COLORES] = {
  "blanco", "amarillo", "verde", "rojo", "azul", "negro"
};

// Peso de cada parte de la distancia al clasificar:
//  croma = proporcion R/C, G/C, B/C (no depende de la altura ni del brillo)
//  luz   = logaritmo del canal C (separa blanco de amarillo claro y negro de azul)
#define PESO_CROMA   100.0f
#define PESO_LUZ       1.0f

// ------------------------------------------------- Configuracion EEPROM -----
#define EE_MAGIC   0x5A17
#define EE_VERSION 1

struct CentroColor {
  float r, g, b, l;     // croma (r,g,b) y ln(C)
  uint8_t ok;
};

struct Config {
  uint16_t magic;
  uint8_t  version;
  uint8_t  tcsAtime;          // registro ATIME: tiempo = (256 - ATIME) * 2,4 ms
  uint8_t  tcsGanancia;       // 0=1x 1=4x 2=16x 3=60x
  CentroColor cal[2][N_COLORES];   // [0]=izquierdo [1]=derecho
  uint8_t  garraCanalB;       // 1 = PORT3B (pines 42/43, PWM 9), 0 = PORT3A (47/48, PWM 6)
  int8_t   garraSentido;      // +1 o -1: invierte abrir/cerrar
  uint8_t  pwmAbrir, pwmCerrar, pwmSujetar;
  uint16_t msAbrir, msCerrar;
};

Config cfg;

void configPorDefecto()
{
  memset(&cfg, 0, sizeof(cfg));
  cfg.magic = EE_MAGIC;
  cfg.version = EE_VERSION;
  cfg.tcsAtime = 0xF6;        // 24 ms
  cfg.tcsGanancia = 1;        // 4x
  cfg.garraCanalB = 1;
  cfg.garraSentido = 1;
  cfg.pwmAbrir = 150;
  cfg.pwmCerrar = 150;
  cfg.pwmSujetar = 60;
  cfg.msAbrir = 800;
  cfg.msCerrar = 800;
}

void cargarConfig()
{
  EEPROM.get(0, cfg);
  if (cfg.magic != EE_MAGIC || cfg.version != EE_VERSION) {
    configPorDefecto();
    Serial.println(F("# EEPROM vacia o de otra version: valores por defecto"));
  } else {
    Serial.println(F("# Configuracion cargada de la EEPROM"));
  }
}

// ============================================================================
//  I2C POR SOFTWARE (colector abierto: la linea solo se baja; la sube la
//  resistencia de pull-up del modulo + la interna del ATmega)
// ============================================================================
class I2CSoft
{
public:
  I2CSoft(uint8_t sda, uint8_t scl) : _sda(sda), _scl(scl) {}

  void begin()
  {
    sdaAlto(); sclAlto();
    delayMicroseconds(10);
    // Si un esclavo quedo sujetando SDA, se libera con 9 pulsos de reloj.
    for (uint8_t i = 0; i < 9 && digitalRead(_sda) == LOW; i++) {
      sclBajo(); espera(); sclAlto(); espera();
    }
    stop();
  }

  // Con el bus en reposo las dos lineas deben estar en 1. Si una esta en 0,
  // hay un cable suelto, un corto a GND o el modulo sin alimentacion; en ese
  // caso todo "parece" responder (un ACK es un 0), asi que hay que detectarlo.
  // Devuelve 0 = bien, 1 = SDA en 0, 2 = SCL en 0, 3 = ambas.
  uint8_t lineasBajas()
  {
    sdaAlto(); pinMode(_scl, INPUT_PULLUP);
    delayMicroseconds(10);
    return (digitalRead(_sda) == LOW ? 1 : 0) | (digitalRead(_scl) == LOW ? 2 : 0);
  }

  bool escribirReg(uint8_t dir, uint8_t reg, uint8_t valor)
  {
    if (lineasBajas()) return false;
    start();
    bool ok = escribir(dir << 1) && escribir(reg) && escribir(valor);
    stop();
    return ok;
  }

  bool leerRegs(uint8_t dir, uint8_t reg, uint8_t *buf, uint8_t n)
  {
    if (lineasBajas()) return false;
    start();
    bool ok = escribir(dir << 1) && escribir(reg);
    if (ok) {
      start();                       // start repetido
      ok = escribir((dir << 1) | 1);
      if (ok) for (uint8_t i = 0; i < n; i++) buf[i] = leer(i < n - 1);
    }
    stop();
    return ok;
  }

private:
  uint8_t _sda, _scl;

  static inline void espera() { delayMicroseconds(4); }
  // Bajar: primero poner el registro a 0 (quita la pull-up interna), luego salida.
  inline void sdaBajo() { digitalWrite(_sda, LOW); pinMode(_sda, OUTPUT); }
  inline void sdaAlto() { pinMode(_sda, INPUT_PULLUP); }
  inline void sclBajo() { digitalWrite(_scl, LOW); pinMode(_scl, OUTPUT); }
  inline void sclAlto()
  {
    pinMode(_scl, INPUT_PULLUP);
    uint16_t t = 0;                   // respeta el "clock stretching" (max ~1 ms)
    while (digitalRead(_scl) == LOW && ++t < 250) delayMicroseconds(4);
  }

  void start()
  {
    sdaAlto(); espera(); sclAlto(); espera();
    sdaBajo(); espera(); sclBajo(); espera();
  }

  void stop()
  {
    sdaBajo(); espera(); sclAlto(); espera(); sdaAlto(); espera();
  }

  bool escribir(uint8_t b)
  {
    for (uint8_t i = 0; i < 8; i++) {
      if (b & 0x80) sdaAlto(); else sdaBajo();
      espera(); sclAlto(); espera(); sclBajo();
      b <<= 1;
    }
    sdaAlto(); espera(); sclAlto(); espera();
    bool ack = (digitalRead(_sda) == LOW);
    sclBajo(); espera();
    return ack;
  }

  uint8_t leer(bool ack)
  {
    uint8_t v = 0;
    sdaAlto();
    for (uint8_t i = 0; i < 8; i++) {
      espera(); sclAlto(); espera();
      v = (v << 1) | (digitalRead(_sda) == HIGH ? 1 : 0);
      sclBajo();
    }
    if (ack) sdaBajo(); else sdaAlto();
    espera(); sclAlto(); espera(); sclBajo();
    sdaAlto();
    return v;
  }
};

// ============================================================================
//  SENSOR DE COLOR TCS34725
// ============================================================================
#define TCS_CMD        0x80
#define TCS_AUTOINC    0x20
#define TCS_ENABLE     0x00
#define TCS_ATIME      0x01
#define TCS_CONTROL    0x0F
#define TCS_ID         0x12
#define TCS_STATUS     0x13
#define TCS_CDATAL     0x14

class SensorColor
{
public:
  SensorColor(const char *nombre, uint8_t sda, uint8_t scl)
    : nombre(nombre), _bus(sda, scl) {}

  const char *nombre;
  bool ok = false;
  uint8_t id = 0;
  uint16_t c = 0, r = 0, g = 0, b = 0;
  uint16_t errores = 0;

  bool begin(uint8_t atime, uint8_t ganancia)
  {
    _bus.begin();
    ok = false;
    if (!_bus.leerRegs(TCS_ADDR, TCS_CMD | TCS_ID, &id, 1)) return false;
    // 0x44 = TCS34721/34725, 0x4D = TCS34723/34727. Algunos clones dan otro valor:
    // se acepta igual y solo se informa.
    if (!_bus.escribirReg(TCS_ADDR, TCS_CMD | TCS_ENABLE, 0x01)) return false;  // PON
    delay(3);
    _bus.escribirReg(TCS_ADDR, TCS_CMD | TCS_ATIME, atime);
    _bus.escribirReg(TCS_ADDR, TCS_CMD | TCS_CONTROL, ganancia & 0x03);
    ok = _bus.escribirReg(TCS_ADDR, TCS_CMD | TCS_ENABLE, 0x03);              // PON + AEN
    _cuentaMax = (uint32_t)(256 - atime) * 1024UL;
    if (_cuentaMax > 65535UL) _cuentaMax = 65535UL;
    return ok;
  }

  bool leer()
  {
    uint8_t d[8];
    if (!_bus.leerRegs(TCS_ADDR, TCS_CMD | TCS_AUTOINC | TCS_CDATAL, d, 8)) {
      errores++;
      ok = false;
      return false;
    }
    ok = true;
    c = d[0] | (d[1] << 8);
    r = d[2] | (d[3] << 8);
    g = d[4] | (d[5] << 8);
    b = d[6] | (d[7] << 8);
    return true;
  }

  uint8_t lineasBajas() { return _bus.lineasBajas(); }

  bool saturado() const { return (uint32_t)c >= (_cuentaMax * 95UL) / 100UL; }

  // Croma y luz de la ultima lectura
  void rasgos(float &fr, float &fg, float &fb, float &fl) const
  {
    float cc = c > 0 ? (float)c : 1.0f;
    fr = r / cc; fg = g / cc; fb = b / cc;
    fl = log(cc);
  }

private:
  I2CSoft _bus;
  uint32_t _cuentaMax = 10240;
};

SensorColor tcs[2] = {
  SensorColor("IZQ", PIN_TCS_IZQ_SDA, PIN_TCS_IZQ_SCL),
  SensorColor("DER", PIN_TCS_DER_SDA, PIN_TCS_DER_SCL),
};

// Devuelve el indice del color mas cercano (o -1 sin calibracion) y la
// "confianza" = distancia al segundo / distancia al primero (>2 es claro).
int8_t clasificar(uint8_t s, float &confianza)
{
  float fr, fg, fb, fl;
  tcs[s].rasgos(fr, fg, fb, fl);
  float mejor = 1e9, segundo = 1e9;
  int8_t idx = -1;
  for (uint8_t k = 0; k < N_COLORES; k++) {
    const CentroColor &cc = cfg.cal[s][k];
    if (!cc.ok) continue;
    float dr = fr - cc.r, dg = fg - cc.g, db = fb - cc.b, dl = fl - cc.l;
    float d = PESO_CROMA * (dr * dr + dg * dg + db * db) + PESO_LUZ * dl * dl;
    if (d < mejor) { segundo = mejor; mejor = d; idx = k; }
    else if (d < segundo) { segundo = d; }
  }
  confianza = (idx >= 0 && segundo < 1e8) ? sqrt(segundo / (mejor + 1e-6f)) : 0.0f;
  return idx;
}

void iniciarTCS()
{
  for (uint8_t s = 0; s < 2; s++) {
    tcs[s].begin(cfg.tcsAtime, cfg.tcsGanancia);
    Serial.print(F("# TCS ")); Serial.print(tcs[s].nombre);
    if (tcs[s].ok) {
      Serial.print(F(": OK, ID=0x")); Serial.println(tcs[s].id, HEX);
    } else {
      uint8_t bajas = tcs[s].lineasBajas();
      if (bajas & 1) Serial.print(F(": SDA en 0 V"));
      if (bajas & 2) Serial.print(F(": SCL en 0 V"));
      if (bajas) Serial.println(F(" (cable suelto/cruzado, corto a GND o modulo sin VIN)"));
      else       Serial.println(F(": NO RESPONDE (lineas bien: revisa que SDA/SCL no esten cruzados)"));
    }
  }
}

// ============================================================================
//  GIROSCOPIO MPU6050 (solo eje Z, I2C del hardware)
// ============================================================================
float gyroBias = 0, rumbo = 0, wPrev = 0;
bool  gyroOk = false;
uint32_t tGyro = 0;
uint16_t gyroErr = 0;

bool mpuEscribir(uint8_t reg, uint8_t v)
{
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg); Wire.write(v);
  return Wire.endTransmission(true) == 0;
}

bool mpuLeerZ(int16_t &z)
{
  Wire.beginTransmission(MPU_ADDR);
  Wire.write((uint8_t)0x47);
  if (Wire.endTransmission(false) != 0) { Wire.clearWireTimeoutFlag(); return false; }
  if (Wire.requestFrom((uint8_t)MPU_ADDR, (uint8_t)2, (uint8_t)true) != 2) {
    Wire.clearWireTimeoutFlag(); return false;
  }
  uint8_t hi = Wire.read(), lo = Wire.read();
  z = (int16_t)(((uint16_t)hi << 8) | lo);
  return true;
}

bool calibrarGyro()
{
  long suma = 0; int16_t mn = 32767, mx = -32768; uint16_t n = 0;
  for (uint16_t i = 0; i < 300; i++) {
    int16_t v;
    if (mpuLeerZ(v)) { suma += v; n++; if (v < mn) mn = v; if (v > mx) mx = v; }
    delay(2);
  }
  if (n < 150) return false;
  gyroBias = (float)suma / n;
  rumbo = 0; wPrev = 0; tGyro = micros();
  if (mx - mn >= 250) Serial.println(F("# AVISO: el robot se movio durante la calibracion del giro"));
  return true;
}

void iniciarGyro()
{
  Wire.begin();
  Wire.setClock(400000UL);
  Wire.setWireTimeout(4000UL, true);
  mpuEscribir(0x6B, 0x80); delay(120);
  gyroOk = mpuEscribir(0x6B, 0x01);
  if (gyroOk) {
    mpuEscribir(0x1A, 0x02);   // DLPF 98 Hz
    mpuEscribir(0x19, 0x00);
    mpuEscribir(0x1B, 0x08);   // +-500 grados/s -> 65,5 LSB por grado/s
    delay(50);
    gyroOk = calibrarGyro();
  }
  Serial.println(gyroOk ? F("# Giroscopio: OK") : F("# Giroscopio: NO RESPONDE"));
}

void actualizarGyro()
{
  if (!gyroOk) return;
  uint32_t ahora = micros();
  if (ahora - tGyro < 2000UL) return;
  int16_t z;
  if (!mpuLeerZ(z)) { gyroErr++; return; }
  float dt = (ahora - tGyro) * 1e-6f;
  if (dt > 0.05f) dt = 0.05f;
  tGyro = ahora;
  float w = ((float)z - gyroBias) / 65.5f;
  rumbo += 0.5f * (w + wPrev) * dt;
  wPrev = w;
}

// ============================================================================
//  MOTORES DE TRACCION (mismos ajustes que ruta_torres/prueba_wwl8_opt)
// ============================================================================
MeEncoderOnBoard Encoder_1(SLOT1);   // izquierdo: PWM 12 (Timer1)
MeEncoderOnBoard Encoder_2(SLOT2);   // derecho:   PWM  8 (Timer4)

void isr_encoder1() { if (PINC & _BV(PC6)) Encoder_1.pulsePosPlus(); else Encoder_1.pulsePosMinus(); }
void isr_encoder2() { if (PIND & _BV(PD7)) Encoder_2.pulsePosPlus(); else Encoder_2.pulsePosMinus(); }

uint32_t tFinMotores = 0;
bool motoresActivos = false;

// Positivo = hacia delante en las dos ruedas (el derecho va invertido de fabrica).
// Grados de rueda leidos directamente del contador de pulsos (8 pulsos x 46:1).
// getCurPos() de la libreria solo se actualiza si se llama Encoder.loop().
float gradosRueda(MeEncoderOnBoard &e)
{
  long v;
  noInterrupts(); v = e.getPulsePos(); interrupts();
  return v * (360.0f / (8.0f * 46.0f));
}

void motores(int16_t izq, int16_t der)
{
  Encoder_1.setMotorPwm(constrain(izq, -255, 255));
  Encoder_2.setMotorPwm(constrain(-der, -255, 255));
}

void pararMotores()
{
  motores(0, 0);
  motoresActivos = false;
}

// ============================================================================
//  GARRA (motor DC en el SLOT3)
// ============================================================================
MeMegaPiDCMotor garraA(PORT3A);
MeMegaPiDCMotor garraB(PORT3B);

enum EstadoGarra { G_QUIETA, G_ABRIENDO, G_CERRANDO, G_SUJETANDO, G_MANUAL };
EstadoGarra estadoGarra = G_QUIETA;
uint32_t tFinGarra = 0;

void garraPwm(int16_t v)
{
  v = constrain(v, -255, 255);
  if (cfg.garraCanalB) { garraA.run(0); garraB.run(v); }
  else                 { garraB.run(0); garraA.run(v); }
}

void garraParar() { garraPwm(0); estadoGarra = G_QUIETA; }

void garraAbrir()
{
  garraPwm((int16_t)cfg.pwmAbrir * cfg.garraSentido);
  estadoGarra = G_ABRIENDO;
  tFinGarra = millis() + cfg.msAbrir;
}

void garraCerrar()
{
  garraPwm(-(int16_t)cfg.pwmCerrar * cfg.garraSentido);
  estadoGarra = G_CERRANDO;
  tFinGarra = millis() + cfg.msCerrar;
}

void actualizarGarra()
{
  if (estadoGarra == G_QUIETA || estadoGarra == G_SUJETANDO) return;
  if ((int32_t)(millis() - tFinGarra) < 0) return;
  if (estadoGarra == G_CERRANDO && cfg.pwmSujetar > 0) {
    // Fuerza baja sostenida para que la pieza no se suelte al moverse.
    garraPwm(-(int16_t)cfg.pwmSujetar * cfg.garraSentido);
    estadoGarra = G_SUJETANDO;
  } else {
    garraParar();
  }
}

// ============================================================================
//  CALIBRACION DE COLORES
// ============================================================================
void calibrarColor(uint8_t color, int8_t soloSensor)
{
  for (uint8_t s = 0; s < 2; s++) {
    if (soloSensor >= 0 && s != soloSensor) continue;
    float sr = 0, sg = 0, sb = 0, sl = 0;
    uint8_t n = 0, sat = 0;
    for (uint8_t i = 0; i < 20; i++) {
      delay(30);                            // > tiempo de integracion
      if (!tcs[s].leer()) continue;
      if (tcs[s].saturado()) sat++;
      float fr, fg, fb, fl;
      tcs[s].rasgos(fr, fg, fb, fl);
      sr += fr; sg += fg; sb += fb; sl += fl; n++;
    }
    Serial.print(F("# Cal ")); Serial.print(tcs[s].nombre); Serial.print(' ');
    Serial.print(NOMBRE_COLOR[color]);
    if (n < 10) { Serial.println(F(": FALLO (sensor sin respuesta)")); continue; }
    CentroColor &cc = cfg.cal[s][color];
    cc.r = sr / n; cc.g = sg / n; cc.b = sb / n; cc.l = sl / n; cc.ok = 1;
    Serial.print(F(": r=")); Serial.print(cc.r, 3);
    Serial.print(F(" g=")); Serial.print(cc.g, 3);
    Serial.print(F(" b=")); Serial.print(cc.b, 3);
    Serial.print(F(" C=")); Serial.print((long)exp(cc.l));
    if (sat) Serial.print(F("  AVISO: SATURADO, baja la ganancia o el tiempo"));
    Serial.println();
  }
  Serial.println(F("# (escribe \"guardar\" para conservarlo al apagar)"));
}

void mostrarCalibracion()
{
  Serial.print(F("# ATIME=0x")); Serial.print(cfg.tcsAtime, HEX);
  Serial.print(F(" (")); Serial.print((256 - cfg.tcsAtime) * 2.4f, 1);
  Serial.print(F(" ms)  ganancia="));
  const uint8_t gan[4] = {1, 4, 16, 60};
  Serial.print(gan[cfg.tcsGanancia & 3]); Serial.println('x');
  for (uint8_t s = 0; s < 2; s++) {
    for (uint8_t k = 0; k < N_COLORES; k++) {
      const CentroColor &cc = cfg.cal[s][k];
      Serial.print(F("# ")); Serial.print(tcs[s].nombre); Serial.print(' ');
      Serial.print(NOMBRE_COLOR[k]); Serial.print(F(": "));
      if (!cc.ok) { Serial.println(F("sin calibrar")); continue; }
      Serial.print(cc.r, 3); Serial.print(' ');
      Serial.print(cc.g, 3); Serial.print(' ');
      Serial.print(cc.b, 3); Serial.print(F(" C="));
      Serial.println((long)exp(cc.l));
    }
  }
}

void mostrarGarra()
{
  Serial.print(F("# Garra: SLOT3 canal ")); Serial.print(cfg.garraCanalB ? 'B' : 'A');
  Serial.print(F("  sentido=")); Serial.print(cfg.garraSentido);
  Serial.print(F("  abrir pwm=")); Serial.print(cfg.pwmAbrir);
  Serial.print(F(" ms=")); Serial.print(cfg.msAbrir);
  Serial.print(F("  cerrar pwm=")); Serial.print(cfg.pwmCerrar);
  Serial.print(F(" ms=")); Serial.print(cfg.msCerrar);
  Serial.print(F("  sujetar pwm=")); Serial.println(cfg.pwmSujetar);
}

// ============================================================================
//  SALIDA CONTINUA (para leer en vivo o desde la Pi)
// ============================================================================
MeUltrasonicSensor ultrasonico(PORT_7);
bool verTCS = false, verGyro = false, verEnc = false, verUltra = false;
uint32_t tVer = 0;

void imprimirUltrasonico()
{
  // distanceCm() de Makeblock devuelve la distancia maxima cuando no hay eco.
  const unsigned long usPorCm = 58;
  const unsigned long eco = ultrasonico.measure(400 * usPorCm);
  Serial.print(F("# Ultrasonico (P7): "));
  if (eco == 0) {
    Serial.println(F("SIN ECO"));
  } else if (eco < 3 * usPorCm || eco > 400 * usPorCm) {
    Serial.println(F("FUERA DE RANGO"));
  } else {
    Serial.print((float)eco / usPorCm, 1);
    Serial.println(F(" cm"));
  }
}

void imprimirTCS(uint8_t s)
{
  SensorColor &t = tcs[s];
  Serial.print(F("TCS ")); Serial.print(t.nombre);
  if (!t.leer()) { Serial.println(F(" ERROR")); return; }
  Serial.print(F(" C=")); Serial.print(t.c);
  Serial.print(F(" R=")); Serial.print(t.r);
  Serial.print(F(" G=")); Serial.print(t.g);
  Serial.print(F(" B=")); Serial.print(t.b);
  float conf;
  int8_t k = clasificar(s, conf);
  Serial.print(F(" -> "));
  Serial.print(k >= 0 ? NOMBRE_COLOR[k] : "?");
  Serial.print(F(" (conf ")); Serial.print(conf, 1); Serial.print(')');
  if (t.saturado()) Serial.print(F(" SATURADO"));
  Serial.println();
}

void imprimirFlujo()
{
  if (millis() - tVer < 150) return;
  tVer = millis();
  if (verTCS) { imprimirTCS(0); imprimirTCS(1); }
  if (verGyro) {
    Serial.print(F("GYRO rumbo=")); Serial.print(rumbo, 2);
    Serial.print(F(" w=")); Serial.print(wPrev, 2);
    Serial.print(F(" err=")); Serial.println(gyroErr);
  }
  if (verEnc) {
    Serial.print(F("ENC izq=")); Serial.print(gradosRueda(Encoder_1), 1);
    Serial.print(F(" der=")); Serial.print(-gradosRueda(Encoder_2), 1);
    Serial.println(F(" (grados de rueda)"));
  }
  if (verUltra) imprimirUltrasonico();
}

// ============================================================================
//  MENU
// ============================================================================
void ayuda()
{
  Serial.println(F(
    "\n=== PRUEBA DE COMPONENTES - Recolector UVM 2026 ===\n"
    " estado            estado de todos los componentes\n"
    " p  (o Enter)      PARAR todo (motores, garra y lecturas en vivo)\n"
    "--- Sensores de color ---\n"
    " t                 ver TCS en vivo (valores crudos y color)\n"
    " c <color> [i|d]   calibrar color: blanco amarillo verde rojo azul negro\n"
    "                   (ambos sensores, o solo i=izquierdo / d=derecho)\n"
    " cal               mostrar calibracion\n"
    " tg <1|4|16|60>    ganancia     tt <ms>  tiempo de integracion (2-614)\n"
    "--- Garra (SLOT3) ---\n"
    " ga / gc / gs      abrir / cerrar / soltar motor\n"
    " gm <pwm> <ms>     mover la garra a mano (pwm -255..255)\n"
    " gp <abrir> <cerrar> <sujetar>   potencias (0-255)\n"
    " gt <ms_abrir> <ms_cerrar>       tiempos\n"
    " ginv              invertir abrir/cerrar   gport <a|b>  canal del SLOT3\n"
    "--- Movimiento (ROBOT LEVANTADO) ---\n"
    " m <izq> <der> <ms>  mover ruedas (pwm -255..255, + = adelante)\n"
    " e                 ver encoders en vivo     er  poner a cero\n"
    " y                 ver giroscopio en vivo   yc  recalibrar (quieto)\n"
    "--- Ultrasonico (P7) ---\n"
    " u                 medir una vez en cm\n"
    " uv                activar/desactivar lecturas en vivo\n"
    "--- EEPROM ---\n"
    " guardar           guardar calibracion y garra\n"
    " borrar            volver a valores por defecto (no guarda)\n"));
}

void estado()
{
  Serial.println(F("# ---- ESTADO ----"));
  for (uint8_t s = 0; s < 2; s++) {
    bool ok = tcs[s].leer();
    Serial.print(F("# TCS ")); Serial.print(tcs[s].nombre);
    Serial.print(ok ? F(": OK  C=") : F(": SIN RESPUESTA  C="));
    Serial.print(tcs[s].c);
    Serial.print(F("  SDA=")); Serial.print(tcs[s].lineasBajas() & 1 ? F("0V") : F("ok"));
    Serial.print(F(" SCL="));  Serial.print(tcs[s].lineasBajas() & 2 ? F("0V") : F("ok"));
    Serial.print(F("  errores=")); Serial.println(tcs[s].errores);
  }
  Serial.print(F("# Giroscopio: ")); Serial.print(gyroOk ? F("OK") : F("NO"));
  Serial.print(F("  rumbo=")); Serial.print(rumbo, 2);
  Serial.print(F("  errores=")); Serial.println(gyroErr);
  Serial.print(F("# Encoders: izq=")); Serial.print(gradosRueda(Encoder_1), 1);
  Serial.print(F(" der=")); Serial.println(-gradosRueda(Encoder_2), 1);
  Serial.print(F("# Boton (pin 4): "));
  Serial.println(digitalRead(BOTON_PIN) == LOW ? F("PULSADO") : F("suelto"));
  mostrarGarra();
}

int8_t buscarColor(const char *txt)
{
  if (!txt) return -1;
  for (uint8_t k = 0; k < N_COLORES; k++)
    if (strncmp(txt, NOMBRE_COLOR[k], 3) == 0) return k;   // basta con 3 letras
  return -1;
}

void pararTodo()
{
  pararMotores();
  garraParar();
  verTCS = verGyro = verEnc = verUltra = false;
  Serial.println(F("# PARADO"));
}

void ejecutar(char *linea)
{
  char *cmd = strtok(linea, " \t");
  if (!cmd || strcmp(cmd, "p") == 0) { pararTodo(); return; }
  char *a1 = strtok(NULL, " \t");
  char *a2 = strtok(NULL, " \t");
  char *a3 = strtok(NULL, " \t");

  if      (!strcmp(cmd, "h") || !strcmp(cmd, "?")) ayuda();
  else if (!strcmp(cmd, "estado")) estado();
  // ---- color ----
  else if (!strcmp(cmd, "t")) verTCS = !verTCS;
  else if (!strcmp(cmd, "c")) {
    int8_t k = buscarColor(a1);
    if (k < 0) { Serial.println(F("# Color no valido: blanco amarillo verde rojo azul negro")); return; }
    int8_t solo = -1;
    if (a2 && a2[0] == 'i') solo = 0;
    if (a2 && a2[0] == 'd') solo = 1;
    calibrarColor(k, solo);
  }
  else if (!strcmp(cmd, "cal")) mostrarCalibracion();
  else if (!strcmp(cmd, "tg") && a1) {
    int v = atoi(a1);
    cfg.tcsGanancia = v >= 60 ? 3 : v >= 16 ? 2 : v >= 4 ? 1 : 0;
    iniciarTCS(); mostrarCalibracion();
    Serial.println(F("# Ojo: al cambiar ganancia hay que recalibrar los colores"));
  }
  else if (!strcmp(cmd, "tt") && a1) {
    float ms = constrain(atof(a1), 2.4f, 614.0f);
    cfg.tcsAtime = (uint8_t)(256 - (int)(ms / 2.4f + 0.5f));
    iniciarTCS(); mostrarCalibracion();
    Serial.println(F("# Ojo: al cambiar el tiempo hay que recalibrar los colores"));
  }
  // ---- garra ----
  else if (!strcmp(cmd, "ga")) garraAbrir();
  else if (!strcmp(cmd, "gc")) garraCerrar();
  else if (!strcmp(cmd, "gs")) garraParar();
  else if (!strcmp(cmd, "gm") && a1 && a2) {
    garraPwm(atoi(a1));
    estadoGarra = G_MANUAL;
    tFinGarra = millis() + constrain(atol(a2), 0L, 3000L);
  }
  else if (!strcmp(cmd, "gp") && a1 && a2 && a3) {
    cfg.pwmAbrir = constrain(atoi(a1), 0, 255);
    cfg.pwmCerrar = constrain(atoi(a2), 0, 255);
    cfg.pwmSujetar = constrain(atoi(a3), 0, 255);
    mostrarGarra();
  }
  else if (!strcmp(cmd, "gt") && a1 && a2) {
    cfg.msAbrir = constrain(atol(a1), 0L, 3000L);
    cfg.msCerrar = constrain(atol(a2), 0L, 3000L);
    mostrarGarra();
  }
  else if (!strcmp(cmd, "ginv")) { cfg.garraSentido = -cfg.garraSentido; mostrarGarra(); }
  else if (!strcmp(cmd, "gport") && a1) {
    garraParar();
    cfg.garraCanalB = (a1[0] == 'b' || a1[0] == 'B');
    mostrarGarra();
  }
  // ---- movimiento ----
  else if (!strcmp(cmd, "m") && a1 && a2 && a3) {
    motores(atoi(a1), atoi(a2));
    motoresActivos = true;
    tFinMotores = millis() + constrain(atol(a3), 0L, 5000L);
  }
  else if (!strcmp(cmd, "e")) verEnc = !verEnc;
  else if (!strcmp(cmd, "er")) { Encoder_1.setPulsePos(0); Encoder_2.setPulsePos(0); }
  else if (!strcmp(cmd, "y")) verGyro = !verGyro;
  else if (!strcmp(cmd, "yc")) {
    Serial.println(calibrarGyro() ? F("# Giro calibrado") : F("# Giro: FALLO"));
  }
  else if (!strcmp(cmd, "u")) imprimirUltrasonico();
  else if (!strcmp(cmd, "uv")) verUltra = !verUltra;
  // ---- EEPROM ----
  else if (!strcmp(cmd, "guardar")) { EEPROM.put(0, cfg); Serial.println(F("# Guardado en EEPROM")); }
  else if (!strcmp(cmd, "borrar")) { configPorDefecto(); iniciarTCS(); Serial.println(F("# Valores por defecto (sin guardar)")); }
  else { Serial.print(F("# Comando desconocido: ")); Serial.println(cmd); }
}

char linea[48];
uint8_t largo = 0;

void leerSerie()
{
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\r') continue;
    if (ch == '\n') {
      linea[largo] = 0;
      for (uint8_t i = 0; i < largo; i++) linea[i] = tolower(linea[i]);
      ejecutar(linea);
      largo = 0;
    } else if (largo < sizeof(linea) - 1) {
      linea[largo++] = ch;
    }
  }
}

// ============================================================================
void setup()
{
  Serial.begin(115200);
  pinMode(BOTON_PIN, INPUT_PULLUP);

  // Frecuencias PWM iguales para los dos motores de traccion (ver DIAGNOSTICO_MEGAPI.md)
  TCCR1A = _BV(WGM10);               TCCR1B = _BV(WGM12) | _BV(CS11);   // 7812 Hz
  TCCR2A = _BV(WGM21) | _BV(WGM20);  TCCR2B = _BV(CS21);                // 3906 Hz
  TCCR4A = _BV(WGM40);               TCCR4B = _BV(WGM42) | _BV(CS41);   // 7812 Hz

  attachInterrupt(digitalPinToInterrupt(Encoder_1.getPortA()), isr_encoder1, RISING);
  attachInterrupt(digitalPinToInterrupt(Encoder_2.getPortA()), isr_encoder2, RISING);
  Encoder_1.setPulse(8);  Encoder_2.setPulse(8);
  Encoder_1.setRatio(46); Encoder_2.setRatio(46);
  Encoder_1.setMotionMode(DIRECT_MODE);
  Encoder_2.setMotionMode(DIRECT_MODE);
  pararMotores();
  garraParar();

  Serial.println(F("\n# Recolector UVM 2026 - prueba de componentes"));
  cargarConfig();
  iniciarTCS();
  iniciarGyro();
  Serial.println(F("# LISTO. Escribe h para ver los comandos."));
}

void loop()
{
  leerSerie();
  actualizarGyro();
  actualizarGarra();
  if (motoresActivos && (int32_t)(millis() - tFinMotores) >= 0) pararMotores();
  if (estadoGarra == G_MANUAL && (int32_t)(millis() - tFinGarra) >= 0) garraParar();
  imprimirFlujo();
}
