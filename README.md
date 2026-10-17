# 🥊 Boxautomat – DIY Punch Machine

Ein selbstgebauter Boxautomat: Ein **Arduino Nano** misst mit einem **MPU-6050**-Beschleunigungssensor
die Schlagkraft und schickt sie per USB an einen **Ubuntu-Laptop**. Dort läuft auf dem **Zweitbildschirm**
eine Arcade-Oberfläche in Python/pygame:

- **Idle:** Hintergrundvideo in Endlosschleife (+ Highscore-Anzeige)
- **Schlag:** Video stoppt, die Punktzahl zählt in großer 7-Segment-Optik hoch, mit Kraftbalken, „NEUER REKORD“ bzw. „PLATZ x“
- **Danach:** zurück in den Loop, mit „Ready Fight“-Overlay
- **Scoreboard-Schalter:** zeigt die Top 10

```
┌──────────────┐  USB (115200 Baud)  ┌──────────────────────────────┐   HDMI   ┌───────────────┐
│ Arduino Nano │ ──── "H 734 12100" ─▶│ Laptop: python -m boxautomat │ ───────▶ │ Zweitbildschirm│
│  + MPU-6050  │ ──── "S1" / "S0" ──▶ │  (pygame + OpenCV)           │          │  (Vollbild)    │
└──────────────┘                     └──────────────────────────────┘          └───────────────┘
```

## Projektstruktur

```
arduino/boxautomat_sensor/   Arduino-Sketch (Schlagerkennung, Schalter)
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

| MPU-6050 (GY-521) | Arduino Nano |
|---|---|
| VCC | 5V |
| GND | GND |
| SDA | A4 |
| SCL | A5 |

Scoreboard-Schalter: **D2 → Schalter → GND** (kein Widerstand nötig, interner Pull-up).

Tipps zur Montage:
- Sensor **fest** mit dem Schlagpolster verschrauben/verkleben – jedes Spiel verfälscht die Werte.
- I²C-Kabel kurz halten (< 30 cm) und verdrillen. Bei langen Kabeln stört der Schlag gern den Bus.
- USB-Kabel mit Zugentlastung befestigen. Falls es doch mal wackelt, verbindet sich die Software jetzt von selbst neu.

## 2. Arduino flashen

1. `arduino/boxautomat_sensor/boxautomat_sensor.ino` in der Arduino IDE öffnen
2. Board **Arduino Nano**, Prozessor **ATmega328P** (bei Klonen meist **„Old Bootloader“**)
3. Hochladen
4. Serieller Monitor auf **115200 Baud**: Es sollte `READY 2.0` erscheinen. Bei einem Schlag kommt `H <punkte> <spitzenwert_mg>`.

Befehle im Seriellen Monitor: `?` zeigt die Einstellungen, `c` kalibriert neu, `r` schaltet den
Rohwert-Stream für den **Seriellen Plotter** ein/aus, `t` sendet einen Test-Schlag.

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
| `Esc` / `Q` | Beenden |

Das Log liegt in `logs/boxautomat.log`.

## 5. Einstellen / Kalibrieren

**Bildschirm:** In `config.toml` unter `[display]` die Option `display_index` setzen (0 = Laptop, 1 = Zweitbildschirm).
Wenn Vollbild auf deinem Desktop Probleme macht, nimm `mode = "borderless"`. devilspie2 wird nicht mehr gebraucht.

**Schlagkraft:** Die Punkte rechnet der Arduino aus. Die Werte stehen oben im Sketch:

| Konstante | Bedeutung |
|---|---|
| `TRIGGER_G` | ab dieser Beschleunigung zählt es als Schlag. Erhöhen, wenn schon Anstupsen auslöst |
| `MAX_SCORE_G` | dieser Spitzenwert ergibt 999 Punkte. Erhöhen, wenn zu oft 999 kommt |
| `SCORE_EXPONENT` | < 1 gibt schwachen Schlägen mehr Punkte, > 1 macht hohe Scores seltener |
| `PEAK_WINDOW_MS` | wie lange nach dem Auslösen der Spitzenwert gesucht wird |
| `DEADTIME_MS` | Sperrzeit, damit das Nachschwingen nicht als zweiter Schlag zählt |

So gehst du vor: `--debug` starten, ein paar leichte und harte Schläge machen und im Log die
`Peak … mg`-Werte ansehen. `MAX_SCORE_G` setzt du etwas über den härtesten realistischen Schlag.
Der MPU-6050 misst maximal ±16 g. Liegt man darüber, sollte der Sensor weiter weg von der
Schlagfläche sitzen, z. B. am Arm statt direkt am Polster.

## 6. Fehlersuche

| Problem | Lösung |
|---|---|
| „NO SENSOR“ blinkt oben links | USB-Kabel prüfen, `ls /dev/ttyUSB* /dev/ttyACM*`, Gruppe `dialout`, brltty entfernen |
| `ERR MPU6050 nicht gefunden` im Log | SDA/SCL vertauscht? 5V/GND? Bei AD0 = HIGH im Sketch `MPU_ADDR = 0x69` setzen |
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
| Arduino → PC | `READY <version>` | gestartet & kalibriert |
| | `H <score> <peak_mg>` | Schlag |
| | `S1` / `S0` | Scoreboard-Schalter an/aus (wird alle 2 s wiederholt) |
| | `ERR …` / `INFO …` | Meldungen (landen im Log) |
| | `<zahl>` | altes Format: nur Score, wird weiterhin verstanden |
| PC → Arduino | `?` `c` `r` `t` | siehe oben |

## Lizenzen

Schrift DSEG7 © keshikan, SIL Open Font License 1.1 (`assets/fonts/DSEG7-LICENSE.txt`).
Schrift „ArcadeClassic“ von Jakob Fischer (Freeware).
