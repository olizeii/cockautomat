/*
 * Boxautomat – Sensor-Firmware für Arduino Nano
 * ==============================================
 *
 * Hardware:
 *   MPU-6050 (GY-521)   VCC -> 5V, GND -> GND, SDA -> A4, SCL -> A5
 *   Scoreboard-Schalter Pin D2 -> Schalter -> GND  (interner Pull-up)
 *   Status-LED          Onboard-LED (D13) blitzt bei jedem Schlag
 *
 * Funktionsweise:
 *   - Liest den Beschleunigungssensor mit ca. 1 kHz (±16 g Messbereich).
 *   - Die Erdbeschleunigung (Schwerkraft) wird laufend als "Ruhelage"
 *     herausgerechnet, d.h. es zählt nur die Beschleunigung durch den Schlag.
 *   - Überschreitet die Beschleunigung TRIGGER_G, beginnt ein Messfenster
 *     (PEAK_WINDOW_MS). Darin wird der Spitzenwert gesucht.
 *   - Danach wird der Score berechnet, über USB gesendet und für
 *     DEADTIME_MS keine weitere Messung gestartet (Nachschwingen ignorieren).
 *
 * Serielles Protokoll (115200 Baud, eine Nachricht pro Zeile):
 *   Arduino -> PC
 *     READY <version>          nach dem Start / Kalibrierung fertig
 *     H <score> <peak_mg>      Schlag erkannt (score 0..999, Spitzenwert in milli-g)
 *     S1 / S0                  Scoreboard-Schalter an / aus
 *     R <mg>                   Rohwert-Stream (nur wenn mit "r" eingeschaltet)
 *     ERR <text>               Fehler, z.B. Sensor nicht gefunden
 *     INFO <text>              Antwort auf "?"
 *   PC -> Arduino (optional, z.B. über den Arduino Serial Monitor)
 *     ?    aktuelle Einstellungen ausgeben
 *     c    Ruhelage neu kalibrieren (Sack dabei nicht berühren)
 *     r    Rohwert-Stream an/aus (für den Serial Plotter beim Einstellen)
 *     t    Test-Schlag mit Zufallswert senden (ohne Sensor testen)
 */

#include <Wire.h>

// ---------------------------------------------------------------------------
// Einstellungen – hier tunen!
// ---------------------------------------------------------------------------
const char*         FW_VERSION      = "2.0";
const unsigned long BAUDRATE        = 115200;

const uint8_t       MPU_ADDR        = 0x68;  // 0x69 falls AD0 auf HIGH liegt
const uint8_t       SWITCH_PIN      = 2;
const uint8_t       LED_PIN         = LED_BUILTIN;

const float         TRIGGER_G       = 2.0;   // ab welcher Beschleunigung ein Schlag zählt
const unsigned long PEAK_WINDOW_MS  = 120;   // so lange nach dem Auslösen wird der Peak gesucht
const unsigned long DEADTIME_MS     = 1200;  // Sperrzeit nach einem Schlag
const float         MAX_SCORE_G     = 15.5;  // dieser Spitzenwert ergibt 999 Punkte
const int           MAX_SCORE       = 999;
const float         SCORE_EXPONENT  = 0.8;   // <1: kleine Schläge bekommen etwas mehr Punkte

const float         BASELINE_ALPHA  = 0.002; // wie schnell sich die Ruhelage nachführt
const unsigned long SWITCH_DEBOUNCE_MS = 40;

// MPU-6050 Register
const uint8_t REG_PWR_MGMT_1  = 0x6B;
const uint8_t REG_CONFIG      = 0x1A;
const uint8_t REG_SMPLRT_DIV  = 0x19;
const uint8_t REG_ACCEL_CONFIG= 0x1C;
const uint8_t REG_ACCEL_XOUT_H= 0x3B;
const uint8_t REG_WHO_AM_I    = 0x75;
const float   LSB_PER_G       = 2048.0;      // bei ±16 g

// ---------------------------------------------------------------------------
// Zustand
// ---------------------------------------------------------------------------
enum State { IDLE, MEASURING, DEADTIME };
State state = IDLE;

float baseX = 0, baseY = 0, baseZ = 0;       // Ruhelage (Schwerkraft) in g
float peakG = 0;
unsigned long stateSince = 0;

bool sensorOk = false;
unsigned long lastSensorRetry = 0;

bool streamRaw = false;
unsigned long lastRawSent = 0;

bool switchState = false;
bool switchRaw = false;
unsigned long switchChangedAt = 0;
unsigned long lastSwitchReport = 0;

// ---------------------------------------------------------------------------
// MPU-6050
// ---------------------------------------------------------------------------
bool mpuWrite(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool mpuInit() {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(REG_WHO_AM_I);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(MPU_ADDR, (uint8_t)1) != 1) return false;
  Wire.read();  // WHO_AM_I: Klone liefern verschiedene Werte, daher nur Antwort prüfen

  bool ok = true;
  ok &= mpuWrite(REG_PWR_MGMT_1, 0x01);   // aufwecken, Takt vom Gyro-PLL
  ok &= mpuWrite(REG_SMPLRT_DIV, 0x00);   // max. Abtastrate
  ok &= mpuWrite(REG_CONFIG, 0x00);       // Tiefpass aus (260 Hz) -> kurze Spitzen bleiben erhalten
  ok &= mpuWrite(REG_ACCEL_CONFIG, 0x18); // ±16 g
  delay(50);
  return ok;
}

// liest Beschleunigung in g; false bei I2C-Fehler
bool mpuRead(float &x, float &y, float &z) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(REG_ACCEL_XOUT_H);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(MPU_ADDR, (uint8_t)6) != 6) return false;
  int16_t rx = (Wire.read() << 8) | Wire.read();
  int16_t ry = (Wire.read() << 8) | Wire.read();
  int16_t rz = (Wire.read() << 8) | Wire.read();
  x = rx / LSB_PER_G;
  y = ry / LSB_PER_G;
  z = rz / LSB_PER_G;
  return true;
}

void calibrate() {
  float sx = 0, sy = 0, sz = 0;
  int n = 0;
  for (int i = 0; i < 300; i++) {
    float x, y, z;
    if (mpuRead(x, y, z)) { sx += x; sy += y; sz += z; n++; }
    delay(2);
  }
  if (n > 0) { baseX = sx / n; baseY = sy / n; baseZ = sz / n; }
}

// ---------------------------------------------------------------------------
// Score
// ---------------------------------------------------------------------------
int computeScore(float g) {
  float rel = (g - TRIGGER_G) / (MAX_SCORE_G - TRIGGER_G);
  if (rel < 0) rel = 0;
  if (rel > 1) rel = 1;
  int score = (int)(pow(rel, SCORE_EXPONENT) * MAX_SCORE + 0.5);
  if (score < 1) score = 1;   // ein erkannter Schlag gibt immer mindestens 1 Punkt
  return score;
}

void sendHit(int score, float g) {
  Serial.print(F("H "));
  Serial.print(score);
  Serial.print(' ');
  Serial.println((long)(g * 1000.0));
  digitalWrite(LED_PIN, HIGH);
}

// ---------------------------------------------------------------------------
// Schalter
// ---------------------------------------------------------------------------
void updateSwitch(unsigned long now) {
  bool raw = digitalRead(SWITCH_PIN) == LOW;  // gegen GND geschaltet
  if (raw != switchRaw) { switchRaw = raw; switchChangedAt = now; }
  if (raw != switchState && now - switchChangedAt > SWITCH_DEBOUNCE_MS) {
    switchState = raw;
    Serial.println(switchState ? F("S1") : F("S0"));
    lastSwitchReport = now;
  }
  // alle 2 s wiederholen, damit der PC nach einem Reconnect den Zustand kennt
  if (now - lastSwitchReport > 2000) {
    Serial.println(switchState ? F("S1") : F("S0"));
    lastSwitchReport = now;
  }
}

// ---------------------------------------------------------------------------
// Befehle vom PC
// ---------------------------------------------------------------------------
void handleCommands() {
  while (Serial.available()) {
    char c = Serial.read();
    switch (c) {
      case '?':
        Serial.print(F("INFO fw=")); Serial.print(FW_VERSION);
        Serial.print(F(" sensor=")); Serial.print(sensorOk ? F("ok") : F("missing"));
        Serial.print(F(" trigger_g=")); Serial.print(TRIGGER_G);
        Serial.print(F(" max_g=")); Serial.print(MAX_SCORE_G);
        Serial.print(F(" base=")); Serial.print(baseX); Serial.print(',');
        Serial.print(baseY); Serial.print(','); Serial.println(baseZ);
        break;
      case 'c':
        Serial.println(F("INFO kalibriere..."));
        calibrate();
        Serial.println(F("INFO kalibriert"));
        break;
      case 'r':
        streamRaw = !streamRaw;
        break;
      case 't': {
        float g = random((long)(TRIGGER_G * 10), (long)(MAX_SCORE_G * 10)) / 10.0;
        sendHit(computeScore(g), g);
        break;
      }
      default:
        break;
    }
  }
}

// ---------------------------------------------------------------------------
void setup() {
  pinMode(SWITCH_PIN, INPUT_PULLUP);
  pinMode(LED_PIN, OUTPUT);
  Serial.begin(BAUDRATE);
  Wire.begin();
  Wire.setClock(400000);
#if defined(WIRE_HAS_TIMEOUT)
  Wire.setWireTimeout(3000, true);  // hängt der I2C-Bus (Wackelkontakt), nicht einfrieren
#endif
  randomSeed(analogRead(A0));

  sensorOk = mpuInit();
  if (sensorOk) {
    calibrate();
  } else {
    Serial.println(F("ERR MPU6050 nicht gefunden - Verkabelung SDA=A4 SCL=A5 pruefen"));
  }
  switchRaw = switchState = digitalRead(SWITCH_PIN) == LOW;
  Serial.print(F("READY "));
  Serial.println(FW_VERSION);
  Serial.println(switchState ? F("S1") : F("S0"));
  lastSwitchReport = millis();
}

void loop() {
  unsigned long now = millis();
  handleCommands();
  updateSwitch(now);

  // Sensor fehlt -> alle 2 s neu versuchen
  if (!sensorOk) {
    if (now - lastSensorRetry > 2000) {
      lastSensorRetry = now;
      sensorOk = mpuInit();
      if (sensorOk) { calibrate(); Serial.println(F("INFO MPU6050 gefunden")); }
      else Serial.println(F("ERR MPU6050 nicht gefunden"));
    }
    return;
  }

  float x, y, z;
  if (!mpuRead(x, y, z)) {
    sensorOk = false;
    state = IDLE;
    Serial.println(F("ERR I2C Lesefehler"));
    return;
  }

  // Beschleunigung ohne Schwerkraft
  float dx = x - baseX, dy = y - baseY, dz = z - baseZ;
  float g = sqrt(dx * dx + dy * dy + dz * dz);

  if (streamRaw && now - lastRawSent >= 10) {
    lastRawSent = now;
    Serial.print(F("R "));
    Serial.println((long)(g * 1000.0));
  }

  switch (state) {
    case IDLE:
      if (g >= TRIGGER_G) {
        state = MEASURING;
        stateSince = now;
        peakG = g;
      } else {
        // Ruhelage langsam nachführen (Sack hängt evtl. leicht schief)
        baseX += BASELINE_ALPHA * (x - baseX);
        baseY += BASELINE_ALPHA * (y - baseY);
        baseZ += BASELINE_ALPHA * (z - baseZ);
      }
      break;

    case MEASURING:
      if (g > peakG) peakG = g;
      if (now - stateSince >= PEAK_WINDOW_MS) {
        sendHit(computeScore(peakG), peakG);
        state = DEADTIME;
        stateSince = now;
      }
      break;

    case DEADTIME:
      if (now - stateSince > 80) digitalWrite(LED_PIN, LOW);
      if (now - stateSince >= DEADTIME_MS && g < TRIGGER_G * 0.5) {
        state = IDLE;
      }
      break;
  }
}
