# 🥊 Boxautomat – DIY Punch Machine

Ein selbstgebauter Boxautomat: Ein **Seeed XIAO ESP32-S3** misst mit einem **H3LIS331**-Beschleunigungssensor
(bis ±400 g) die Schlagkraft und schickt sie per USB an einen **Ubuntu-Laptop**. Dort läuft auf dem **Zweitbildschirm**
eine Arcade-Oberfläche in Python/pygame:

- **Idle:** Pixel-Art-Boxvideo in Endlosschleife, unten eine Leiste mit Highscore und blinkendem „HAU DRAUF“
- **Schlag:** Blitz und Funkenregen, die Punktzahl zählt in einer leuchtenden 7-Segment-Anzeige hoch, darunter
  ein LED-Kraftbalken mit Rekordmarke. Danach eine Bewertung („GANZ NETT“ … „KNOCKOUT“) und „NEUER REKORD“
  mit Konfetti bzw. „PLATZ x“
- **Danach:** zurück in den Loop, mit „Ready Fight“-Overlay
- **Scoreboard-Schalter:** Top 10 im Synthwave-Look, der letzte Schlag ist markiert
- Über allem liegt ein Röhrenmonitor-Effekt (Scanlines, dunkle Ecken), abschaltbar mit `crt_effect = false`

```
┌──────────────┐  USB (115200 Baud)  ┌──────────────────────────────┐   HDMI   ┌───────────────┐
│   ESP32-S3   │ ──── "H 734 52100" ─▶│ Laptop: python -m boxautomat │ ───────▶ │ Zweitbildschirm│
│  + H3LIS331  │ ──── "S1" / "S0" ──▶ │  (pygame + OpenCV)           │          │  (Vollbild)    │
└──────────────┘                     └──────────────────────────────┘          └───────────────┘
```

## Projektstruktur

```
arduino/boxautomat_sensor/   Firmware ESP32-S3 + H3LIS331 (Schlagerkennung, Kalibrierung, Schalter)
arduino/boxautomat_simple/   alte einfache Variante: Arduino Nano + MPU-6050 (nur Schlag, kein Schalter)
boxautomat/                  Python-App
  app.py                     Fenster, Hauptschleife, Tastatur
  game.py                    Zustandsautomat IDLE → COUNTING → RESULT → IDLE (+ SCOREBOARD)
  serial_link.py             USB-Verbindung im Hintergrund-Thread, automatischer Reconnect
  protocol.py                Parser für die Zeilen vom Arduino
  video.py                   Video-Loop & transparentes Overlay (OpenCV)
  ui.py                      Zeichnen von Score, Balken, Scoreboard
  highscores.py              Top-10 als JSON (absturzsicher gespeichert)
assets/                      Videos, Schriften, Icon
tests/                       pytest – läuft auch ohne Bildschirm und Arduino
config.example.toml          alle Einstellungen
install.sh / run_boxautomat.sh
```

## 1. Hardware verkabeln

| Adafruit H3LIS331 | XIAO ESP32-S3 |
|---|---|
| VIN | 3V3 |
| GND | GND |
| SDA | D4 (GPIO 5) |
| SCL | D5 (GPIO 6) |

Die I²C-Adresse (0x18 oder 0x19) erkennt die Firmware selbst.

Scoreboard-Schalter: **D1 (GPIO 2) → Schalter → GND** (kein Widerstand nötig, interner Pull-up).
Beim Kippschalter mit 3 Beinen: mittleres Bein an D1, ein äußeres an GND.

Anderes ESP32-S3-Board: oben im Sketch `I2C_SDA`, `I2C_SCL` und `SWITCH_PIN` auf die **GPIO-Nummern**
ändern (nicht die D-Beschriftung). Nicht nehmen: GPIO 0, 3, 45, 46 (Startverhalten) und 19/20 (USB).

Messbereich: `RANGE_G` oben im Sketch, 100, 200 (Standard) oder 400 g. Kleiner = feinere Auflösung bei
leichten Schlägen, größer = mehr Reserve für harte.

Tipps zur Montage:
- Sensor **fest** mit dem Schlagpolster verschrauben/verkleben – jedes Spiel verfälscht die Werte.
- I²C-Kabel kurz halten (< 30 cm) und verdrillen. Bei langen Kabeln stört der Schlag gern den Bus.
- USB-Kabel mit Zugentlastung befestigen. Falls es doch mal wackelt, verbindet sich die Software jetzt von selbst neu.

## 2. ESP32-S3 flashen

1. In der Arduino IDE das Board-Paket **esp32** von Espressif installieren (Boardverwalter)
2. `arduino/boxautomat_sensor/boxautomat_sensor.ino` öffnen, Board **XIAO_ESP32S3**
3. **Werkzeuge → USB CDC On Boot → Enabled** (beim XIAO schon Standard), sonst bleibt der
   Serielle Monitor leer.
4. Hochladen. Eine zusätzliche Bibliothek braucht es nicht.
5. Serieller Monitor auf **115200 Baud**: Nach einem Reset oder dem Befehl `?` erscheint `READY 3.0`.
   Bei einem Schlag kommt `H <punkte> <spitzenwert_mg> <clip>`.
6. Beim allerersten Start ist noch nichts gespeichert, deshalb beginnt sofort die
   **Kalibrierung** (siehe Abschnitt 5). Am besten läuft dabei schon die App, sie führt durch die Schritte.

Befehle im Seriellen Monitor: `?` zeigt die Einstellungen, `c` misst die Ruhelage neu, `k` startet
die Kalibrierung, `x` bricht sie ab, `r` schaltet den Rohwert-Stream für den **Seriellen Plotter**
ein/aus, `t` sendet einen Test-Schlag.

## 3. Software installieren (Ubuntu)

```bash
git clone https://github.com/olizeii/cockautomat.git ~/boxautomat
cd ~/boxautomat
./install.sh               # oder ./install.sh --autostart
```

Das Skript erledigt Folgendes:
- legt die Python-Umgebung `.venv` an und installiert die Pakete
- fügt dich zur Gruppe `dialout` hinzu (danach **einmal ab- und wieder anmelden**)
- bietet an, `brltty` zu entfernen, das unter Ubuntu Nano-Klone mit CH340-Chip blockiert
- übernimmt deine alte `highscore.json` aus `~/Dokumente/boxautomat`
- legt den Starter im Startmenü und auf dem Schreibtisch an

## 4. Starten

```bash
./run_boxautomat.sh                         # normal: Vollbild auf Bildschirm 1
./run_boxautomat.sh --windowed --no-serial  # Test am Laptop ohne Arduino
./run_boxautomat.sh --debug                 # jede Arduino-Zeile landet im Log
```

| Taste | Funktion |
|---|---|
| `Leertaste` | Test-Schlag mit Zufallswert |
| `1`–`9` | Test-Schlag mit 111 … 999 |
| `Tab` | Scoreboard ein/aus |
| `K` | Kalibrierung starten / abbrechen (mit `--no-serial`: Demo-Ablauf) |
| `Esc` / `Q` | Beenden |

Das Log liegt in `logs/boxautomat.log`.

## 5. Einstellen / Kalibrieren

**Bildschirm:** In `config.toml` unter `[display]` die Option `display_index` setzen (0 = Laptop, 1 = Zweitbildschirm).
Wenn Vollbild auf deinem Desktop Probleme macht, nimm `mode = "borderless"`. devilspie2 wird nicht mehr gebraucht.
`video_fit = "contain"` zeigt das ganze Video (bei 5:4-Monitoren mit Rand oben und unten, dort stehen Titel und
Highscore). `video_fit = "cover"` füllt den Bildschirm und schneidet dafür links und rechts ab.

**Schlagkraft-Kalibrierung:** Wie viel g ein Schlag ergibt, hängt stark davon ab, wo der Sensor sitzt:
Je weiter weg vom Drehpunkt (Scharnier / Drehwirbel), desto größer der Ausschlag. Deshalb misst der
ESP32 Schwelle und 999-Punkte-Wert selbst und speichert sie im Flash.

Kalibrierung starten:
- automatisch beim ersten Start (noch nichts gespeichert)
- **Scoreboard-Schalter 6× schnell hin- und herkippen** (innerhalb von 4 Sekunden), genauso abbrechen
- Taste `K` in der App oder `k` im Seriellen Monitor

Ablauf (die App zeigt dabei Live-Kurve, alle Spitzenwerte, Rauschen, Abtastrate, Messbereich,
Lage des Sensors sowie alte und neue Werte):
1. **Ruhe:** etwa 3 Sekunden nicht berühren. Dabei werden Ruhelage und Rauschen gemessen.
2. **3× leicht klopfen:** Die Schwelle wird 70 % des schwächsten Klopfers.
3. **3× voll draufhauen:** 999 Punkte = Mittelwert + 30 % Reserve (höchstens 95 % des Messbereichs). Ein harter Schlag,
   der nicht mindestens 1,5× so stark ist wie der stärkste leichte, zählt nicht.

Meldet die App **„UEBERSTEUERT“** oder **„AM MESSLIMIT“**, kommt der Sensor bei harten Schlägen an
seine Grenze. Dann `RANGE_G` im Sketch erhöhen (200 → 400) bzw. bei 400 g den Sensor näher an den
Drehpunkt setzen und neu kalibrieren.
Nach einer Neukalibrierung sind alte Highscores nicht mehr ganz vergleichbar.

Weitere Konstanten oben im Sketch: `SCORE_EXPONENT` (< 1 gibt schwachen Schlägen mehr Punkte),
`PEAK_WINDOW_MS` (wie lange nach dem Auslösen der Spitzenwert gesucht wird), `DEADTIME_MS`
(Sperrzeit gegen Nachschwingen), `CAL_RESERVE`, `TOGGLE_COUNT` / `TOGGLE_WINDOW_MS`.

## 6. Fehlersuche

| Problem | Lösung |
|---|---|
| „NO SENSOR“ blinkt unten rechts | USB-Kabel prüfen, `ls /dev/ttyUSB* /dev/ttyACM*`, Gruppe `dialout`, brltty entfernen |
| `ERR H3LIS331 nicht gefunden` im Log | SDA an D4, SCL an D5? Vertauscht? 3V3/GND? Bei anderem Board die GPIO-Nummern `I2C_SDA`/`I2C_SCL` im Sketch anpassen |
| Im Seriellen Monitor kommt nichts | „USB CDC On Boot: Enabled“ einstellen und neu hochladen |
| Schalter zeigt immer `S1` | der Pin ist dauerhaft mit GND verbunden: Schalterbeine prüfen (mittleres Bein an den Pin) |
| Fenster auf falschem Bildschirm | `display_index` in `config.toml` ändern |
| Video ruckelt | `fps = 30` setzen oder das Video auf Bildschirmauflösung herunterrechnen |
| Bildschirm geht nach Minuten aus | wird von `run_boxautomat.sh` über `xset` abgeschaltet; zusätzlich in den Ubuntu-Einstellungen „Bildschirm ausschalten: Nie“ wählen |

## Entwicklung

```bash
.venv/bin/pip install pytest
SDL_VIDEODRIVER=dummy .venv/bin/python -m pytest
```

Die Tests prüfen das Protokoll, den Zustandsautomaten, die Highscores und die USB-Verbindung
(mit einem simulierten Arduino). Außerdem starten sie die komplette App ohne Bildschirm.

### Serielles Protokoll

| Richtung | Zeile | Bedeutung |
|---|---|---|
| Arduino → PC | `READY <version>` | gestartet |
| | `CFG <trigger_mg> <max_mg> <eeprom\|default> <range_g>` | aktive Einstellung |
| | `H <score> <peak_mg> <clip>` | Schlag (`clip` 1 = Sensor am Anschlag) |
| | `CAL BEGIN/STATE/REST/HIT/REJECT/LIVE/INFO/DONE/ABORT …` | Kalibrierung (Details oben im Sketch) |
| | `S1` / `S0` | Scoreboard-Schalter an/aus (wird alle 2 s wiederholt) |
| | `ERR …` / `INFO …` | Meldungen (landen im Log) |
| | `<zahl>` | altes Format: nur Score, wird weiterhin verstanden |
| PC → Arduino | `?` `c` `k` `x` `r` `t` | siehe oben |

## Lizenzen

Schrift DSEG7 © keshikan, SIL Open Font License 1.1 (`assets/fonts/DSEG7-LICENSE.txt`).
Schrift „ArcadeClassic“ von Jakob Fischer (Freeware).
