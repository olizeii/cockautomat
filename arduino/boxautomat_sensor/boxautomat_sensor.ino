/*
 * Boxautomat – Sensor-Firmware für Seeed XIAO ESP32-S3 mit H3LIS331 (±100/200/400 g)
 * ==================================================================================
 *
 * Hardware (Beschriftung auf dem XIAO in Klammern):
 *   Adafruit H3LIS331 (I2C)  VIN -> 3V3, GND -> GND, SDA -> GPIO 5 (D4), SCL -> GPIO 6 (D5)
 *                            (oder per STEMMA-QT-Kabel; Adresse 0x18 oder 0x19 wird selbst erkannt)
 *   Scoreboard-Schalter      GPIO 2 (D1) -> Schalter -> GND  (interner Pull-up)
 *                            Kippschalter: Mittelkontakt an D1, ein Außenkontakt an GND
 *   Status-LED               gelbe Onboard-LED blitzt bei jedem Schlag
 *
 * Arduino IDE: Board "XIAO_ESP32S3", Werkzeuge -> "USB CDC On Boot: Enabled" (Standard beim XIAO).
 *   Keine zusätzliche Bibliothek nötig.
 *   Anderes ESP32-S3-Board: unten I2C_SDA / I2C_SCL / SWITCH_PIN auf dessen GPIO-Nummern ändern.
 *
 * Funktionsweise:
 *   - Liest den Beschleunigungssensor mit 1000 Hz (Messbereich siehe RANGE_G).
 *   - Die Erdbeschleunigung (Schwerkraft) wird laufend als "Ruhelage"
 *     herausgerechnet, d.h. es zählt nur die Beschleunigung durch den Schlag.
 *   - Überschreitet die Beschleunigung die Auslöseschwelle, beginnt ein
 *     Messfenster (PEAK_WINDOW_MS). Darin wird der Spitzenwert gesucht.
 *   - Danach wird der Score berechnet, über USB gesendet und für
 *     DEADTIME_MS keine weitere Messung gestartet (Nachschwingen ignorieren).
 *
 * Kalibrierung (Schwelle und 999-Punkte-Wert hängen davon ab, wo der Sensor sitzt):
 *   - startet automatisch, wenn noch nichts gespeichert ist
 *   - oder: Scoreboard-Schalter schnell 6x hin- und herkippen (innerhalb 4 s)
 *   - oder: Befehl "k" vom PC
 *   Ablauf: 1. Ruhe messen (nicht berühren)  2. 3x leicht klopfen  3. 3x voll draufhauen
 *   Schwelle  = 70 % des schwächsten leichten Klopfers (mindestens über dem Rauschen)
 *   999 Punkte = Mittelwert der harten Schläge + 30 % Reserve (höchstens 95 % von RANGE_G)
 *   Das Ergebnis wird im Flash (Preferences) gespeichert und gilt ab sofort.
 *
 * Serielles Protokoll (115200 Baud, eine Nachricht pro Zeile):
 *   ESP32 -> PC
 *     READY <version>          nach dem Start (und auf "?")
 *     CFG <trigger_mg> <max_mg> <eeprom|default> <range_g>   aktive Einstellung
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
 *     CAL INFO <hz> - <x_mg> <y_mg> <z_mg>  Abtastrate, (keine Temperatur), Lage (Schwerkraft)
 *     CAL DONE <trigger_mg> <max_mg> <clips>        fertig und gespeichert
 *     CAL ABORT                             abgebrochen, alte Werte bleiben
 *   PC -> ESP32 (optional, z.B. über den Arduino Serial Monitor)
 *     ?    Version und aktuelle Einstellungen ausgeben
 *     c    Ruhelage neu messen (Sack dabei nicht berühren)
 *     k    Kalibrierung starten
 *     x    Kalibrierung abbrechen
 *     r    Rohwert-Stream an/aus (für den Serial Plotter beim Einstellen)
 *     t    Test-Schlag mit Zufallswert senden (ohne Sensor testen)
 */

#include <Wire.h>
#include <Preferences.h>

// ---------------------------------------------------------------------------
// Einstellungen – hier tunen!
// ---------------------------------------------------------------------------
const char*         FW_VERSION      = "3.0";
const unsigned long BAUDRATE        = 115200;

// Pins als GPIO-Nummern (XIAO ESP32-S3: D4 = 5, D5 = 6, D1 = 2)
const int           I2C_SDA         = 5;     // D4
const int           I2C_SCL         = 6;     // D5
const int           SWITCH_PIN      = 2;     // D1
#ifdef LED_BUILTIN
const int           LED_PIN         = LED_BUILTIN;
#else
const int           LED_PIN         = -1;    // Board ohne Onboard-LED
#endif
#if defined(ARDUINO_XIAO_ESP32S3)
const bool          LED_ACTIVE_LOW  = true;  // die gelbe XIAO-LED leuchtet bei LOW
#else
const bool          LED_ACTIVE_LOW  = false;
#endif

const int           RANGE_G         = 200;   // Messbereich: 100, 200 oder 400 (g)

// Standardwerte, solange nicht kalibriert wurde
const float         DEFAULT_TRIGGER_G = 3.0;   // ab welcher Beschleunigung ein Schlag zählt
const float         DEFAULT_MAX_G     = 60.0;  // dieser Spitzenwert ergibt 999 Punkte

const unsigned long PEAK_WINDOW_MS  = 120;   // so lange nach dem Auslösen wird der Peak gesucht
const unsigned long DEADTIME_MS     = 1200;  // Sperrzeit nach einem Schlag
const unsigned long CAL_DEADTIME_MS = 700;   // Sperrzeit während der Kalibrierung
const int           MAX_SCORE       = 999;
const float         SCORE_EXPONENT  = 0.8;   // <1: kleine Schläge bekommen etwas mehr Punkte

const float         BASELINE_ALPHA  = 0.002; // wie schnell sich die Ruhelage nachführt
// Der H3LIS331 rauscht bei 1000 Hz stark (einzelne Werte springen um bis zu ~2 g, obwohl er
// stillsteht). Gleitender Mittelwert über so viele Messwerte (je 1 ms) glättet das,
// ein Schlag dauert deutlich länger und bleibt erhalten.
const uint8_t       SMOOTH_SAMPLES  = 5;
const unsigned long SWITCH_DEBOUNCE_MS = 40;

// Kalibrierung
const uint8_t       CAL_HITS        = 3;     // Anzahl leichte / harte Schläge
const float         CAL_RESERVE     = 1.3;   // 999 = Mittel der harten Schläge * Reserve
const float         CAL_MAX_G       = RANGE_G * 0.95f;  // mehr kann der Sensor nicht messen
const float         CAL_MOVING_G    = 3.0;   // so viel Bewegung ist bei "Ruhe" zu viel
const float         CAL_MIN_DETECT_G = 0.5;  // kleinste Auslöseschwelle beim Kalibrieren
const uint8_t       TOGGLE_COUNT    = 6;     // so oft Schalter kippen ...
const unsigned long TOGGLE_WINDOW_MS = 4000; // ... innerhalb dieser Zeit -> Kalibrierung
const unsigned long CAL_TIMEOUT_MS  = 120000UL; // 2 min ohne Fortschritt -> abbrechen

// H3LIS331DL Register
const uint8_t REG_WHO_AM_I    = 0x0F;        // Antwort 0x32
const uint8_t REG_CTRL1       = 0x20;
const uint8_t REG_CTRL4       = 0x23;
const uint8_t REG_STATUS      = 0x27;
const uint8_t REG_OUT_X_L     = 0x28;
const uint8_t AUTO_INCREMENT  = 0x80;        // mehrere Register am Stück lesen
const int16_t RAW_CLIP        = 2040;        // 12-Bit-Wert: ab hier ist eine Achse am Anschlag

// ---------------------------------------------------------------------------
// Gespeicherte Einstellung (Flash)
// ---------------------------------------------------------------------------
const uint8_t ACC_ADDRS[]      = {0x18, 0x19};  // 0x19, wenn SDO/SA0 auf 3V3 liegt
uint8_t       accAddr          = 0x18;          // wird in accInit() erkannt

const uint8_t SETTINGS_VERSION = 2;
Preferences prefs;

float triggerG = DEFAULT_TRIGGER_G;
float maxG     = DEFAULT_MAX_G;
bool  fromEeprom = false;
float gPerDigit = 0.098;                     // wird in accInit() gesetzt

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
float calDetectG = CAL_MIN_DETECT_G;         // Auslöseschwelle während der Kalibrierung
float calLight[CAL_HITS], calHard[CAL_HITS];
uint8_t calCount = 0, calClips = 0;
float liveMax = 0;
uint16_t sampleCount = 0, sampleRate = 0;
float smoothBuf[SMOOTH_SAMPLES][3];
uint8_t smoothIdx = 0, smoothFill = 0;
unsigned long rateSince = 0;

// ---------------------------------------------------------------------------
// LED
// ---------------------------------------------------------------------------
void led(bool on) {
  if (LED_PIN >= 0) digitalWrite(LED_PIN, (on != LED_ACTIVE_LOW) ? HIGH : LOW);
}

// ---------------------------------------------------------------------------
// Einstellungen speichern / laden
// ---------------------------------------------------------------------------
bool loadSettings() {
  prefs.begin("boxautomat", true);
  uint8_t version = prefs.getUChar("ver", 0);
  float t = prefs.getFloat("trig", 0);
  float m = prefs.getFloat("max", 0);
  prefs.end();
  if (version != SETTINGS_VERSION) return false;
  if (!(t > 0.05f && t < 100.0f && m > t && m <= 400.0f)) return false;
  triggerG = t;
  maxG = fminf(m, CAL_MAX_G);    // falls RANGE_G inzwischen kleiner ist
  return true;
}

void saveSettings() {
  prefs.begin("boxautomat", false);
  prefs.putUChar("ver", SETTINGS_VERSION);
  prefs.putFloat("trig", triggerG);
  prefs.putFloat("max", maxG);
  prefs.end();
}

void sendConfig() {
  Serial.print(F("CFG "));
  Serial.print((long)(triggerG * 1000.0f));
  Serial.print(' ');
  Serial.print((long)(maxG * 1000.0f));
  Serial.print(fromEeprom ? F(" eeprom ") : F(" default "));
  Serial.println(RANGE_G);
}

// ---------------------------------------------------------------------------
// H3LIS331
// ---------------------------------------------------------------------------
bool accWrite(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(accAddr);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

int accReadReg(uint8_t reg) {
  Wire.beginTransmission(accAddr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return -1;
  if (Wire.requestFrom(accAddr, (uint8_t)1) != 1) return -1;
  return Wire.read();
}

bool accInit() {
  int who = -1;
  for (uint8_t a : ACC_ADDRS) {            // beide möglichen Adressen probieren
    accAddr = a;
    who = accReadReg(REG_WHO_AM_I);
    if (who >= 0) break;
  }
  if (who < 0) return false;
  Serial.print(F("INFO H3LIS331 an Adresse 0x"));
  Serial.println(accAddr, HEX);
  if (who != 0x32) {
    Serial.print(F("INFO unerwartete Sensor-ID 0x"));
    Serial.println(who, HEX);
  }
  uint8_t fs;
  if (RANGE_G <= 100)      { fs = 0x00; gPerDigit = 0.049f; }
  else if (RANGE_G <= 200) { fs = 0x10; gPerDigit = 0.098f; }
  else                     { fs = 0x30; gPerDigit = 0.195f; }
  bool ok = true;
  ok &= accWrite(REG_CTRL1, 0x3F);        // Normalbetrieb, 1000 Hz, X/Y/Z an
  ok &= accWrite(REG_CTRL4, 0x80 | fs);   // Block Data Update + Messbereich
  delay(20);
  return ok;
}

// liest Beschleunigung in g. -1 = I2C-Fehler, 0 = noch kein neuer Messwert, 1 = neu.
// lastClipped: eine Achse am Anschlag
int accRead(float &x, float &y, float &z) {
  int status = accReadReg(REG_STATUS);
  if (status < 0) return -1;
  if (!(status & 0x08)) return 0;         // ZYXDA: neue Daten?
  Wire.beginTransmission(accAddr);
  Wire.write(REG_OUT_X_L | AUTO_INCREMENT);
  if (Wire.endTransmission(false) != 0) return -1;
  if (Wire.requestFrom(accAddr, (uint8_t)6) != 6) return -1;
  int16_t raw[3];
  for (uint8_t i = 0; i < 3; i++) {
    uint8_t lo = Wire.read();
    uint8_t hi = Wire.read();
    raw[i] = (int16_t)((hi << 8) | lo) >> 4;   // 12 Bit, linksbündig
  }
  lastClipped = abs(raw[0]) >= RAW_CLIP || abs(raw[1]) >= RAW_CLIP || abs(raw[2]) >= RAW_CLIP;
  x = raw[0] * gPerDigit;
  y = raw[1] * gPerDigit;
  z = raw[2] * gPerDigit;
  return 1;
}

void calibrate() {
  float sx = 0, sy = 0, sz = 0;
  int n = 0;
  unsigned long start = millis();
  while (n < 300 && millis() - start < 1000) {
    float x, y, z;
    if (accRead(x, y, z) == 1) { sx += x; sy += y; sz += z; n++; }
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
  int score = (int)(powf(rel, SCORE_EXPONENT) * MAX_SCORE + 0.5f);
  if (score < 1) score = 1;   // ein erkannter Schlag gibt immer mindestens 1 Punkt
  return score;
}

void sendHit(int score, float g, bool clipped) {
  Serial.print(F("H "));
  Serial.print(score);
  Serial.print(' ');
  Serial.print((long)(g * 1000.0f));
  Serial.println(clipped ? F(" 1") : F(" 0"));
  led(true);
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
  float floorG = fmaxf(calNoise * 1.3f, CAL_MIN_DETECT_G);
  triggerG = fmaxf(minLight * 0.7f, floorG);
  maxG = fminf(meanHard * CAL_RESERVE, CAL_MAX_G);
  if (maxG < triggerG * 2) maxG = triggerG * 2;
  fromEeprom = true;
  saveSettings();

  Serial.print(F("CAL DONE "));
  Serial.print((long)(triggerG * 1000.0f));
  Serial.print(' ');
  Serial.print((long)(maxG * 1000.0f));
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
    for (uint8_t i = 0; i < CAL_HITS; i++) maxLight = fmaxf(maxLight, calLight[i]);
    if (peak < maxLight * 1.5f) {
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
  Serial.print((long)(peak * 1000.0f));
  Serial.println(clipped ? F(" 1") : F(" 0"));
  led(true);

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
    float dx = x - baseX, dy = y - baseY, dz = z - baseZ;   // Live-Wert zur alten Ruhelage
    return sqrtf(dx * dx + dy * dy + dz * dz);
  }
  if (calN > 0) {
    baseX = calSumX / calN; baseY = calSumY / calN; baseZ = calSumZ / calN;
    calN = 0;
    calNoise = 0;
  }
  float dx = x - baseX, dy = y - baseY, dz = z - baseZ;
  float g = sqrtf(dx * dx + dy * dy + dz * dz);
  if (g > CAL_MOVING_G) {
    Serial.println(F("CAL REJECT MOVING"));
    calEnterStep(CAL_REST, now);
    return g;
  }
  if (g > calNoise) calNoise = g;
  if (t >= 2500) {
    calDetectG = fmaxf(calNoise * 1.5f, CAL_MIN_DETECT_G);
    Serial.print(F("CAL REST "));
    Serial.println((long)(calNoise * 1000.0f));
    calEnterStep(CAL_LIGHT, now);
  }
  return g;
}

void calStream(float g, unsigned long now) {
  if (g > liveMax) liveMax = g;
  if (now - calLastLive >= 40) {
    calLastLive = now;
    Serial.print(F("CAL LIVE "));
    Serial.println((long)(liveMax * 1000.0f));
    liveMax = 0;
  }
  if (now - calLastInfo >= 1000) {
    calLastInfo = now;
    Serial.print(F("CAL INFO "));
    Serial.print(sampleRate);
    Serial.print(F(" - "));             // der H3LIS331 hat keinen Temperatursensor
    Serial.print((long)(baseX * 1000.0f)); Serial.print(' ');
    Serial.print((long)(baseY * 1000.0f)); Serial.print(' ');
    Serial.println((long)(baseZ * 1000.0f));
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
        Serial.print(F("READY "));
        Serial.println(FW_VERSION);
        Serial.print(F("INFO fw=")); Serial.print(FW_VERSION);
        Serial.print(F(" sensor=")); Serial.print(sensorOk ? F("ok") : F("missing"));
        Serial.print(F(" range_g=")); Serial.print(RANGE_G);
        Serial.print(F(" trigger_g=")); Serial.print(triggerG);
        Serial.print(F(" max_g=")); Serial.print(maxG);
        Serial.print(F(" quelle=")); Serial.print(fromEeprom ? F("gespeichert") : F("standard"));
        Serial.print(F(" base=")); Serial.print(baseX); Serial.print(',');
        Serial.print(baseY); Serial.print(','); Serial.println(baseZ);
        sendConfig();
        Serial.println(switchState ? F("S1") : F("S0"));
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
        float g = random((long)(triggerG * 10), (long)(maxG * 10)) / 10.0f;
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
  if (LED_PIN >= 0) pinMode(LED_PIN, OUTPUT);
  led(false);
  Serial.begin(BAUDRATE);
#if ARDUINO_USB_CDC_ON_BOOT && ARDUINO_USB_MODE
  Serial.setTxTimeoutMs(0);   // ohne PC am USB nicht bei jedem print hängen bleiben
#endif
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(400000);
  Wire.setTimeOut(20);        // hängt der I2C-Bus (Wackelkontakt), nicht einfrieren

  fromEeprom = loadSettings();
  Serial.print(F("READY "));
  Serial.println(FW_VERSION);
  sendConfig();

  sensorOk = accInit();
  if (sensorOk) {
    calibrate();
  } else {
    Serial.println(F("ERR H3LIS331 nicht gefunden - Verkabelung SDA=D4 SCL=D5 und 3V3/GND pruefen"));
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
      sensorOk = accInit();
      if (sensorOk) { calibrate(); Serial.println(F("INFO H3LIS331 gefunden")); }
      else Serial.println(F("ERR H3LIS331 nicht gefunden"));
    }
    return;
  }
  if (calPending) {
    calPending = false;
    calBegin(calPendingReason, now);
  }

  float x, y, z;
  int got = accRead(x, y, z);
  if (got < 0) {
    sensorOk = false;
    state = IDLE;
    if (calStep != CAL_OFF) calAbort(now);
    Serial.println(F("ERR I2C Lesefehler"));
    return;
  }
  if (got == 0) return;         // noch kein neuer Messwert (Sensor liefert 1000 pro Sekunde)

  // gleitender Mittelwert gegen das Sensorrauschen
  smoothBuf[smoothIdx][0] = x;
  smoothBuf[smoothIdx][1] = y;
  smoothBuf[smoothIdx][2] = z;
  smoothIdx = (smoothIdx + 1) % SMOOTH_SAMPLES;
  if (smoothFill < SMOOTH_SAMPLES) smoothFill++;
  x = y = z = 0;
  for (uint8_t i = 0; i < smoothFill; i++) {
    x += smoothBuf[i][0];
    y += smoothBuf[i][1];
    z += smoothBuf[i][2];
  }
  x /= smoothFill;
  y /= smoothFill;
  z /= smoothFill;

  // Abtastrate messen (für den Kalibrier-Bildschirm)
  sampleCount++;
  if (now - rateSince >= 1000) {
    sampleRate = sampleCount;
    sampleCount = 0;
    rateSince = now;
  }

  if (calStep == CAL_REST) {
    calStream(calRest(x, y, z, now), now);
    return;
  }

  // Beschleunigung ohne Schwerkraft
  float dx = x - baseX, dy = y - baseY, dz = z - baseZ;
  float g = sqrtf(dx * dx + dy * dy + dz * dz);

  bool calibrating = calStep != CAL_OFF;
  float threshold = calibrating ? calDetectG : triggerG;
  if (calibrating) {
    calStream(g, now);
    if (now - calLastProgress > CAL_TIMEOUT_MS) { calAbort(now); return; }
  }

  if (streamRaw && now - lastRawSent >= 10) {
    lastRawSent = now;
    Serial.print(F("R "));
    Serial.println((long)(g * 1000.0f));
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
      if (now - stateSince > 80) led(false);
      unsigned long dead = calStep != CAL_OFF ? CAL_DEADTIME_MS : DEADTIME_MS;
      float th = calStep != CAL_OFF ? calDetectG : triggerG;
      if (now - stateSince >= dead && g < th * 0.5f) {
        state = IDLE;
      }
      break;
    }
  }
}
