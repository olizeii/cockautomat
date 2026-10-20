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
 *   - Überschreitet die Beschleunigung die Auslöseschwelle, beginnt ein
 *     Messfenster (PEAK_WINDOW_MS). Darin wird der Spitzenwert gesucht.
 *   - Danach wird der Score berechnet, über USB gesendet und für
 *     DEADTIME_MS keine weitere Messung gestartet (Nachschwingen ignorieren).
 *
 * Kalibrierung (Schwelle und 999-Punkte-Wert hängen davon ab, wo der Sensor sitzt):
 *   - startet automatisch, wenn im EEPROM noch nichts gespeichert ist
 *   - oder: Scoreboard-Schalter schnell 6x hin- und herkippen (innerhalb 4 s)
 *   - oder: Befehl "k" vom PC
 *   Ablauf: 1. Ruhe messen (nicht berühren)  2. 3x leicht klopfen  3. 3x voll draufhauen
 *   Schwelle  = 70 % des schwächsten leichten Klopfers (mindestens über dem Rauschen)
 *   999 Punkte = Mittelwert der harten Schläge + 30 % Reserve (höchstens 16 g)
 *   Das Ergebnis wird im EEPROM gespeichert und gilt ab sofort.
 *
 * Serielles Protokoll (115200 Baud, eine Nachricht pro Zeile):
 *   Arduino -> PC
 *     READY <version>          nach dem Start
 *     CFG <trigger_mg> <max_mg> <eeprom|default>   aktive Einstellung
 *     H <score> <peak_mg> <clip>   Schlag (score 1..999, Spitze in milli-g, clip 1 = übersteuert)
 *     S1 / S0                  Scoreboard-Schalter an / aus
 *     R <mg>                   Rohwert-Stream (nur wenn mit "r" eingeschaltet)
 *     ERR <text>               Fehler, z.B. Sensor nicht gefunden
 *     INFO <text>              Meldungen / Antwort auf "?"
 *     CAL BEGIN <first|switch|command>      Kalibrierung startet
 *     CAL STATE <REST|LIGHT|HARD> <n> <von> aktueller Schritt (auch jede Sekunde)
 *     CAL REST <noise_mg>                   Ruhemessung fertig, Rauschen
 *     CAL HIT <LIGHT|HARD> <nr> <peak_mg> <clip>   Schlag gezählt
 *     CAL REJECT <MOVING|WEAK>              nicht gezählt (Bewegung in Ruhe / harter Schlag zu schwach)
 *     CAL LIVE <mg>                         Live-Wert (Maximum der letzten 40 ms)
 *     CAL INFO <hz> <temp_dC> <x_mg> <y_mg> <z_mg>  Abtastrate, Temperatur, Lage (Schwerkraft)
 *     CAL DONE <trigger_mg> <max_mg> <clips>        fertig und gespeichert
 *     CAL ABORT                             abgebrochen, alte Werte bleiben
 *   PC -> Arduino (optional, z.B. über den Arduino Serial Monitor)
 *     ?    aktuelle Einstellungen ausgeben
 *     c    Ruhelage neu messen (Sack dabei nicht berühren)
 *     k    Kalibrierung starten
 *     x    Kalibrierung abbrechen
 *     r    Rohwert-Stream an/aus (für den Serial Plotter beim Einstellen)
 *     t    Test-Schlag mit Zufallswert senden (ohne Sensor testen)
 */

#include <Wire.h>
#include <EEPROM.h>

// ---------------------------------------------------------------------------
// Einstellungen – hier tunen!
// ---------------------------------------------------------------------------
const char*         FW_VERSION      = "2.1";
const unsigned long BAUDRATE        = 115200;

const uint8_t       MPU_ADDR        = 0x68;  // 0x69 falls AD0 auf HIGH liegt
const uint8_t       SWITCH_PIN      = 2;
const uint8_t       LED_PIN         = LED_BUILTIN;

// Standardwerte, solange nicht kalibriert wurde
const float         DEFAULT_TRIGGER_G = 2.0;   // ab welcher Beschleunigung ein Schlag zählt
const float         DEFAULT_MAX_G     = 15.5;  // dieser Spitzenwert ergibt 999 Punkte

const unsigned long PEAK_WINDOW_MS  = 120;   // so lange nach dem Auslösen wird der Peak gesucht
const unsigned long DEADTIME_MS     = 1200;  // Sperrzeit nach einem Schlag
const unsigned long CAL_DEADTIME_MS = 700;   // Sperrzeit während der Kalibrierung
const int           MAX_SCORE       = 999;
const float         SCORE_EXPONENT  = 0.8;   // <1: kleine Schläge bekommen etwas mehr Punkte

const float         BASELINE_ALPHA  = 0.002; // wie schnell sich die Ruhelage nachführt
const unsigned long SWITCH_DEBOUNCE_MS = 40;

// Kalibrierung
const uint8_t       CAL_HITS        = 3;     // Anzahl leichte / harte Schläge
const float         CAL_RESERVE     = 1.3;   // 999 = Mittel der harten Schläge * Reserve
const float         CAL_MAX_G       = 16.0;  // mehr kann der Sensor nicht messen
const float         CAL_MOVING_G    = 0.6;   // so viel Bewegung ist bei "Ruhe" zu viel
const uint8_t       TOGGLE_COUNT    = 6;     // so oft Schalter kippen ...
const unsigned long TOGGLE_WINDOW_MS = 4000; // ... innerhalb dieser Zeit -> Kalibrierung
const unsigned long CAL_TIMEOUT_MS  = 120000UL; // 2 min ohne Fortschritt -> abbrechen

// MPU-6050 Register
const uint8_t REG_PWR_MGMT_1  = 0x6B;
const uint8_t REG_CONFIG      = 0x1A;
const uint8_t REG_SMPLRT_DIV  = 0x19;
const uint8_t REG_ACCEL_CONFIG= 0x1C;
const uint8_t REG_ACCEL_XOUT_H= 0x3B;
const uint8_t REG_TEMP_OUT_H  = 0x41;
const uint8_t REG_WHO_AM_I    = 0x75;
const float   LSB_PER_G       = 2048.0;      // bei ±16 g
const int16_t RAW_CLIP        = 32700;       // ab hier ist eine Achse am Anschlag

// ---------------------------------------------------------------------------
// Gespeicherte Einstellung (EEPROM)
// ---------------------------------------------------------------------------
struct Settings {
  uint16_t magic;
  uint8_t  version;
  float    triggerG;
  float    maxG;
  uint8_t  check;
};
const uint16_t SETTINGS_MAGIC = 0xB0C5;
const int      SETTINGS_ADDR  = 0;

float triggerG = DEFAULT_TRIGGER_G;
float maxG     = DEFAULT_MAX_G;
bool  fromEeprom = false;

// ---------------------------------------------------------------------------
// Zustand
// ---------------------------------------------------------------------------
enum State { IDLE, MEASURING, DEADTIME };
State state = IDLE;

float baseX = 0, baseY = 0, baseZ = 0;       // Ruhelage (Schwerkraft) in g
float peakG = 0;
bool  peakClipped = false;
unsigned long stateSince = 0;

bool sensorOk = false;
unsigned long lastSensorRetry = 0;
bool lastClipped = false;                    // letzte Messung hatte eine Achse am Anschlag

bool streamRaw = false;
unsigned long lastRawSent = 0;

bool switchState = false;
bool switchRaw = false;
unsigned long switchChangedAt = 0;
unsigned long lastSwitchReport = 0;
unsigned long toggleTimes[TOGGLE_COUNT];
uint8_t toggleIdx = 0;

// Kalibrierung
enum CalStep { CAL_OFF, CAL_REST, CAL_LIGHT, CAL_HARD };
CalStep calStep = CAL_OFF;
bool calPending = false;                     // beim Start nach dem Sensor kalibrieren
const __FlashStringHelper* calPendingReason = nullptr;
unsigned long calBeganAt = 0, calStepSince = 0, calLastProgress = 0;
unsigned long calLastState = 0, calLastLive = 0, calLastInfo = 0;
float calSumX, calSumY, calSumZ;
uint16_t calN;
float calNoise = 0;
float calDetectG = 0.3;                      // Auslöseschwelle während der Kalibrierung
float calLight[CAL_HITS], calHard[CAL_HITS];
uint8_t calCount = 0, calClips = 0;
float liveMax = 0;
uint16_t loopCount = 0, sampleRate = 0;
unsigned long rateSince = 0;

// ---------------------------------------------------------------------------
// EEPROM
// ---------------------------------------------------------------------------
uint8_t settingsCheck(const Settings &s) {
  const uint8_t *p = (const uint8_t *)&s;
  uint8_t c = 0x5A;
  for (uint8_t i = 0; i < sizeof(Settings) - 1; i++) c = (uint8_t)((c << 1) | (c >> 7)) ^ p[i];
  return c;
}

bool loadSettings() {
  Settings s;
  EEPROM.get(SETTINGS_ADDR, s);
  if (s.magic != SETTINGS_MAGIC || s.version != 1 || s.check != settingsCheck(s)) return false;
  if (!(s.triggerG > 0.05 && s.triggerG < 10 && s.maxG > s.triggerG && s.maxG <= 20)) return false;
  triggerG = s.triggerG;
  maxG = s.maxG;
  return true;
}

void saveSettings() {
  Settings s;
  s.magic = SETTINGS_MAGIC;
  s.version = 1;
  s.triggerG = triggerG;
  s.maxG = maxG;
  s.check = settingsCheck(s);
  EEPROM.put(SETTINGS_ADDR, s);   // put schreibt nur geänderte Bytes
}

void sendConfig() {
  Serial.print(F("CFG "));
  Serial.print((long)(triggerG * 1000.0));
  Serial.print(' ');
  Serial.print((long)(maxG * 1000.0));
  Serial.println(fromEeprom ? F(" eeprom") : F(" default"));
}

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

// liest Beschleunigung in g; false bei I2C-Fehler. lastClipped: eine Achse am Anschlag
bool mpuRead(float &x, float &y, float &z) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(REG_ACCEL_XOUT_H);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(MPU_ADDR, (uint8_t)6) != 6) return false;
  int16_t rx = (Wire.read() << 8) | Wire.read();
  int16_t ry = (Wire.read() << 8) | Wire.read();
  int16_t rz = (Wire.read() << 8) | Wire.read();
  lastClipped = abs((long)rx) >= RAW_CLIP || abs((long)ry) >= RAW_CLIP || abs((long)rz) >= RAW_CLIP;
  x = rx / LSB_PER_G;
  y = ry / LSB_PER_G;
  z = rz / LSB_PER_G;
  return true;
}

// Chiptemperatur in 1/10 °C (nur Info für den Kalibrier-Bildschirm)
int mpuTempDeci() {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(REG_TEMP_OUT_H);
  if (Wire.endTransmission(false) != 0) return 0;
  if (Wire.requestFrom(MPU_ADDR, (uint8_t)2) != 2) return 0;
  int16_t raw = (Wire.read() << 8) | Wire.read();
  return (int)(raw / 34.0 + 365.3);
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
  float rel = (g - triggerG) / (maxG - triggerG);
  if (rel < 0) rel = 0;
  if (rel > 1) rel = 1;
  int score = (int)(pow(rel, SCORE_EXPONENT) * MAX_SCORE + 0.5);
  if (score < 1) score = 1;   // ein erkannter Schlag gibt immer mindestens 1 Punkt
  return score;
}

void sendHit(int score, float g, bool clipped) {
  Serial.print(F("H "));
  Serial.print(score);
  Serial.print(' ');
  Serial.print((long)(g * 1000.0));
  Serial.println(clipped ? F(" 1") : F(" 0"));
  digitalWrite(LED_PIN, HIGH);
}

// ---------------------------------------------------------------------------
// Kalibrierung
// ---------------------------------------------------------------------------
const __FlashStringHelper* calStepName(CalStep s) {
  switch (s) {
    case CAL_REST:  return F("REST");
    case CAL_LIGHT: return F("LIGHT");
    default:        return F("HARD");
  }
}

void calSendState(unsigned long now) {
  Serial.print(F("CAL STATE "));
  Serial.print(calStepName(calStep));
  Serial.print(' ');
  Serial.print(calStep == CAL_REST ? 0 : calCount);
  Serial.print(' ');
  Serial.println(calStep == CAL_REST ? 1 : CAL_HITS);
  calLastState = now;
}

void calEnterStep(CalStep s, unsigned long now) {
  calStep = s;
  calStepSince = now;
  calLastProgress = now;
  calCount = 0;
  calSumX = calSumY = calSumZ = 0;
  calN = 0;
  calSendState(now);
}

void calBegin(const __FlashStringHelper* reason, unsigned long now) {
  Serial.print(F("CAL BEGIN "));
  Serial.println(reason);
  calBeganAt = now;
  calClips = 0;
  calNoise = 0;
  toggleIdx = 0;
  state = IDLE;
  calEnterStep(CAL_REST, now);
}

void calAbort(unsigned long now) {
  calStep = CAL_OFF;
  state = DEADTIME;            // kurz sperren, falls der Sack noch schwingt
  stateSince = now;
  Serial.println(F("CAL ABORT"));
  sendConfig();
}

void calFinish(unsigned long now) {
  float minLight = calLight[0], meanHard = 0;
  for (uint8_t i = 0; i < CAL_HITS; i++) {
    if (calLight[i] < minLight) minLight = calLight[i];
    meanHard += calHard[i] / CAL_HITS;
  }
  float floorG = max(calNoise * 2.5, 0.3);
  triggerG = max(minLight * 0.7, floorG);
  maxG = min(meanHard * CAL_RESERVE, CAL_MAX_G);
  if (maxG < triggerG * 2) maxG = triggerG * 2;
  fromEeprom = true;
  saveSettings();

  Serial.print(F("CAL DONE "));
  Serial.print((long)(triggerG * 1000.0));
  Serial.print(' ');
  Serial.print((long)(maxG * 1000.0));
  Serial.print(' ');
  Serial.println(calClips);
  sendConfig();
  calStep = CAL_OFF;
  state = DEADTIME;
  stateSince = now;
}

// Ein Schlag während der Kalibrierung wurde fertig gemessen
void calOnPeak(float peak, bool clipped, unsigned long now) {
  if (calStep == CAL_HARD) {
    float maxLight = 0;
    for (uint8_t i = 0; i < CAL_HITS; i++) maxLight = max(maxLight, calLight[i]);
    if (peak < maxLight * 1.5) {
      Serial.println(F("CAL REJECT WEAK"));
      return;
    }
    calHard[calCount] = peak;
    if (clipped) calClips++;
  } else {
    calLight[calCount] = peak;
  }
  calCount++;
  calLastProgress = now;
  Serial.print(F("CAL HIT "));
  Serial.print(calStepName(calStep));
  Serial.print(' ');
  Serial.print(calCount);
  Serial.print(' ');
  Serial.print((long)(peak * 1000.0));
  Serial.println(clipped ? F(" 1") : F(" 0"));
  digitalWrite(LED_PIN, HIGH);

  if (calCount >= CAL_HITS) {
    if (calStep == CAL_LIGHT) calEnterStep(CAL_HARD, now);
    else calFinish(now);
  } else {
    calSendState(now);
  }
}

// Ruhemessung: 1 s Lage mitteln, dann 1,5 s Rauschen messen. Gibt den Live-Wert zurück.
float calRest(float x, float y, float z, unsigned long now) {
  unsigned long t = now - calStepSince;
  if (t < 1000) {
    calSumX += x; calSumY += y; calSumZ += z; calN++;
    return 0;
  }
  if (calN > 0) {
    baseX = calSumX / calN; baseY = calSumY / calN; baseZ = calSumZ / calN;
    calN = 0;
    calNoise = 0;
  }
  float dx = x - baseX, dy = y - baseY, dz = z - baseZ;
  float g = sqrt(dx * dx + dy * dy + dz * dz);
  if (g > CAL_MOVING_G) {
    Serial.println(F("CAL REJECT MOVING"));
    calEnterStep(CAL_REST, now);
    return g;
  }
  if (g > calNoise) calNoise = g;
  if (t >= 2500) {
    calDetectG = max(calNoise * 3.0, 0.25);
    Serial.print(F("CAL REST "));
    Serial.println((long)(calNoise * 1000.0));
    calEnterStep(CAL_LIGHT, now);
  }
  return g;
}

void calStream(float g, unsigned long now) {
  if (g > liveMax) liveMax = g;
  if (now - calLastLive >= 40) {
    calLastLive = now;
    Serial.print(F("CAL LIVE "));
    Serial.println((long)(liveMax * 1000.0));
    liveMax = 0;
  }
  if (now - calLastInfo >= 1000) {
    calLastInfo = now;
    Serial.print(F("CAL INFO "));
    Serial.print(sampleRate);
    Serial.print(' ');
    Serial.print(mpuTempDeci());
    Serial.print(' ');
    Serial.print((long)(baseX * 1000.0)); Serial.print(' ');
    Serial.print((long)(baseY * 1000.0)); Serial.print(' ');
    Serial.println((long)(baseZ * 1000.0));
  }
  if (now - calLastState >= 1000) calSendState(now);
}

// ---------------------------------------------------------------------------
// Schalter
// ---------------------------------------------------------------------------
// true, wenn der Schalter gerade TOGGLE_COUNT-mal schnell gekippt wurde
bool toggledFast(unsigned long now) {
  toggleTimes[toggleIdx % TOGGLE_COUNT] = now;
  toggleIdx++;
  if (toggleIdx < TOGGLE_COUNT) return false;
  unsigned long oldest = toggleTimes[toggleIdx % TOGGLE_COUNT];
  if (now - oldest > TOGGLE_WINDOW_MS) return false;
  toggleIdx = 0;
  return true;
}

void updateSwitch(unsigned long now) {
  bool raw = digitalRead(SWITCH_PIN) == LOW;  // gegen GND geschaltet
  if (raw != switchRaw) { switchRaw = raw; switchChangedAt = now; }
  if (raw != switchState && now - switchChangedAt > SWITCH_DEBOUNCE_MS) {
    switchState = raw;
    Serial.println(switchState ? F("S1") : F("S0"));
    lastSwitchReport = now;
    if (toggledFast(now) && sensorOk) {
      if (calStep == CAL_OFF) calBegin(F("switch"), now);
      else if (now - calBeganAt > TOGGLE_WINDOW_MS) calAbort(now);
    }
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
void handleCommands(unsigned long now) {
  while (Serial.available()) {
    char c = Serial.read();
    switch (c) {
      case '?':
        Serial.print(F("INFO fw=")); Serial.print(FW_VERSION);
        Serial.print(F(" sensor=")); Serial.print(sensorOk ? F("ok") : F("missing"));
        Serial.print(F(" trigger_g=")); Serial.print(triggerG);
        Serial.print(F(" max_g=")); Serial.print(maxG);
        Serial.print(F(" quelle=")); Serial.print(fromEeprom ? F("eeprom") : F("standard"));
        Serial.print(F(" base=")); Serial.print(baseX); Serial.print(',');
        Serial.print(baseY); Serial.print(','); Serial.println(baseZ);
        sendConfig();
        break;
      case 'c':
        Serial.println(F("INFO kalibriere Ruhelage..."));
        calibrate();
        Serial.println(F("INFO Ruhelage gemessen"));
        break;
      case 'k':
        if (sensorOk && calStep == CAL_OFF) calBegin(F("command"), now);
        break;
      case 'x':
        if (calStep != CAL_OFF) calAbort(now);
        break;
      case 'r':
        streamRaw = !streamRaw;
        break;
      case 't': {
        float g = random((long)(triggerG * 10), (long)(maxG * 10)) / 10.0;
        sendHit(computeScore(g), g, false);
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

  fromEeprom = loadSettings();
  Serial.print(F("READY "));
  Serial.println(FW_VERSION);
  sendConfig();

  sensorOk = mpuInit();
  if (sensorOk) {
    calibrate();
  } else {
    Serial.println(F("ERR MPU6050 nicht gefunden - Verkabelung SDA=A4 SCL=A5 pruefen"));
  }
  if (!fromEeprom) {            // noch nie kalibriert -> sobald der Sensor da ist
    calPending = true;
    calPendingReason = F("first");
  }
  switchRaw = switchState = digitalRead(SWITCH_PIN) == LOW;
  Serial.println(switchState ? F("S1") : F("S0"));
  lastSwitchReport = millis();
  rateSince = millis();
}

void loop() {
  unsigned long now = millis();
  handleCommands(now);
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
  if (calPending) {
    calPending = false;
    calBegin(calPendingReason, now);
  }

  float x, y, z;
  if (!mpuRead(x, y, z)) {
    sensorOk = false;
    state = IDLE;
    if (calStep != CAL_OFF) calAbort(now);
    Serial.println(F("ERR I2C Lesefehler"));
    return;
  }

  // Abtastrate messen (für den Kalibrier-Bildschirm)
  loopCount++;
  if (now - rateSince >= 1000) {
    sampleRate = loopCount;
    loopCount = 0;
    rateSince = now;
  }

  if (calStep == CAL_REST) {
    calStream(calRest(x, y, z, now), now);
    return;
  }

  // Beschleunigung ohne Schwerkraft
  float dx = x - baseX, dy = y - baseY, dz = z - baseZ;
  float g = sqrt(dx * dx + dy * dy + dz * dz);

  bool calibrating = calStep != CAL_OFF;
  float threshold = calibrating ? calDetectG : triggerG;
  if (calibrating) {
    calStream(g, now);
    if (now - calLastProgress > CAL_TIMEOUT_MS) { calAbort(now); return; }
  }

  if (streamRaw && now - lastRawSent >= 10) {
    lastRawSent = now;
    Serial.print(F("R "));
    Serial.println((long)(g * 1000.0));
  }

  switch (state) {
    case IDLE:
      if (g >= threshold) {
        state = MEASURING;
        stateSince = now;
        peakG = g;
        peakClipped = lastClipped;
      } else {
        // Ruhelage langsam nachführen (Sack hängt evtl. leicht schief)
        baseX += BASELINE_ALPHA * (x - baseX);
        baseY += BASELINE_ALPHA * (y - baseY);
        baseZ += BASELINE_ALPHA * (z - baseZ);
      }
      break;

    case MEASURING:
      if (g > peakG) peakG = g;
      peakClipped |= lastClipped;
      if (now - stateSince >= PEAK_WINDOW_MS) {
        state = DEADTIME;
        stateSince = now;
        if (calibrating) calOnPeak(peakG, peakClipped, now);
        else sendHit(computeScore(peakG), peakG, peakClipped);
      }
      break;

    case DEADTIME: {
      if (now - stateSince > 80) digitalWrite(LED_PIN, LOW);
      unsigned long dead = calStep != CAL_OFF ? CAL_DEADTIME_MS : DEADTIME_MS;
      float th = calStep != CAL_OFF ? calDetectG : triggerG;
      if (now - stateSince >= dead && g < th * 0.5) {
        state = IDLE;
      }
      break;
    }
  }
}
