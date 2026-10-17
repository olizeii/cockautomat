// Boxautomat – einfache Variante mit der MPU6050-Bibliothek (z.B. Electronic Cats).
// Unabhängig von arduino/boxautomat_sensor: sendet pro Schlag genau eine Zahl 1…999,
// dazu SENSOR_OK / SENSOR_ERROR. 115200 Baud. Kein Scoreboard-Schalter.

#include <Wire.h>
#include <MPU6050.h>

MPU6050 mpu;

// ---- Einstellungen (zum Kalibrieren anpassen) ----
const float LSB_PER_G = 2048.0;              // gilt für ±16 g
const float TRIGGER_G = 2.0;                 // ab so viel g über Ruhe zählt es als Schlag
const float MAX_G     = 15.0;                // dieser Spitzenwert ergibt 999 Punkte
const unsigned long PEAK_WINDOW_MS = 100;    // so lange wird nach dem Maximum gesucht
const unsigned long DEADTIME_MS    = 1000;   // Sperrzeit gegen Nachschwingen

float restX, restY, restZ;                   // Ruhelage (enthält die Schwerkraft)
int16_t ax, ay, az;

enum State { WAITING, MEASURING, DEADTIME };
State state = WAITING;
unsigned long stateStart = 0;
float peak = 0;

void calibrate() {
  long sx = 0, sy = 0, sz = 0;
  for (int i = 0; i < 200; i++) {
    mpu.getAcceleration(&ax, &ay, &az);
    sx += ax; sy += ay; sz += az;
    delay(2);
  }
  restX = sx / 200.0; restY = sy / 200.0; restZ = sz / 200.0;
}

// Beschleunigung in g, ohne Schwerkraft/Ruhelage
float readG() {
  mpu.getAcceleration(&ax, &ay, &az);
  float dx = ax - restX, dy = ay - restY, dz = az - restZ;
  return sqrt(dx * dx + dy * dy + dz * dz) / LSB_PER_G;
}

int toScore(float g) {
  float f = (g - TRIGGER_G) / (MAX_G - TRIGGER_G);
  f = constrain(f, 0.0, 1.0);
  return 1 + (int)(f * 998 + 0.5);           // 1 … 999
}

void setup() {
  Serial.begin(115200);
  Wire.begin();
  Wire.setClock(400000);                     // schnelleres I2C -> mehr Messungen pro Schlag

  mpu.initialize();
  while (!mpu.testConnection()) {            // ohne Sensor nicht weitermachen
    Serial.println("SENSOR_ERROR");
    delay(2000);
    mpu.initialize();
  }
  mpu.setFullScaleAccelRange(3);             // 3 = ±16 g (Standard wäre ±2 g!)
  delay(100);
  calibrate();                               // beim Start still halten
  Serial.println("SENSOR_OK");
}

void loop() {
  float g = readG();
  unsigned long now = millis();

  switch (state) {
    case WAITING:
      if (g >= TRIGGER_G) {                  // Schlag beginnt
        state = MEASURING;
        stateStart = now;
        peak = g;
      } else if (g < 0.3) {                  // in Ruhe die Ruhelage langsam nachführen
        restX += (ax - restX) * 0.01;
        restY += (ay - restY) * 0.01;
        restZ += (az - restZ) * 0.01;
      }
      break;

    case MEASURING:
      if (g > peak) peak = g;
      if (now - stateStart >= PEAK_WINDOW_MS) {
        Serial.println(toScore(peak));       // genau EINE Zahl pro Schlag
        state = DEADTIME;
        stateStart = now;
      }
      break;

    case DEADTIME:
      if (now - stateStart >= DEADTIME_MS && g < TRIGGER_G / 2) {
        state = WAITING;
      }
      break;
  }
}
