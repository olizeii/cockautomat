#!/usr/bin/env bash
# Boxautomat – Installation auf Ubuntu
#   ./install.sh              Python-Umgebung, Rechte, Startmenü-Eintrag
#   ./install.sh --autostart  zusätzlich beim Login automatisch starten
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
AUTOSTART=0
[ "${1:-}" = "--autostart" ] && AUTOSTART=1

say() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

# 1) Systempakete -------------------------------------------------------------
say "Prüfe Systempakete"
NEED=()
python3 -c "import venv, ensurepip" 2>/dev/null || NEED+=(python3-venv)
command -v xset >/dev/null || NEED+=(x11-xserver-utils)
if [ ${#NEED[@]} -gt 0 ]; then
    echo "Installiere: ${NEED[*]}"
    sudo apt-get update && sudo apt-get install -y "${NEED[@]}"
fi

# 2) Python-Umgebung ------------------------------------------------------------
say "Richte Python-Umgebung (.venv) ein"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements.txt

# 3) Zugriff auf den Arduino ---------------------------------------------------
if ! id -nG "$USER" | grep -qw dialout; then
    say "Füge $USER zur Gruppe 'dialout' hinzu (Zugriff auf /dev/ttyUSB*)"
    sudo usermod -aG dialout "$USER"
    echo "!! Danach einmal ab- und wieder anmelden, sonst fehlt die Berechtigung."
fi
# brltty (Braille-Treiber) schnappt sich unter Ubuntu gern CH340-Nano-Klone
if dpkg -s brltty >/dev/null 2>&1; then
    say "brltty ist installiert – blockiert oft Arduino-Nano-Klone (CH340)"
    read -r -p "brltty entfernen? (nur wenn du keine Braillezeile nutzt) [j/N] " a
    if [[ "$a" =~ ^[jJyY]$ ]]; then sudo apt-get remove -y brltty; fi
fi

# 4) Konfiguration & Highscores ------------------------------------------------
[ -f config.toml ] || { cp config.example.toml config.toml; echo "config.toml angelegt"; }
mkdir -p data logs
if [ ! -f data/highscore.json ]; then
    for old in "$HOME/Dokumente/boxautomat_alt/highscore.json" \
               "$HOME/Dokumente/boxautomat/highscore.json"; do
        if [ -f "$old" ] && [ "$(realpath "$old")" != "$(realpath -m data/highscore.json)" ]; then
            cp "$old" data/highscore.json
            say "Alte Highscores übernommen aus $old"
            break
        fi
    done
fi

# 5) Startmenü / Desktop / Autostart --------------------------------------------
say "Lege Starter an"
chmod +x run_boxautomat.sh
DESKTOP_FILE="$(cat <<EOF
[Desktop Entry]
Name=Boxautomat
Comment=Startet den Boxautomaten
Type=Application
Exec=$DIR/run_boxautomat.sh
Path=$DIR
Icon=$DIR/assets/icon.png
Terminal=false
Categories=Game;
EOF
)"
mkdir -p "$HOME/.local/share/applications"
echo "$DESKTOP_FILE" > "$HOME/.local/share/applications/boxautomat.desktop"
DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Schreibtisch")"
if [ -d "$DESK" ]; then
    echo "$DESKTOP_FILE" > "$DESK/Boxautomat.desktop"
    chmod +x "$DESK/Boxautomat.desktop"
    gio set "$DESK/Boxautomat.desktop" metadata::trusted true 2>/dev/null || true
fi
if [ $AUTOSTART = 1 ]; then
    mkdir -p "$HOME/.config/autostart"
    echo "$DESKTOP_FILE" > "$HOME/.config/autostart/boxautomat.desktop"
    echo "Autostart aktiviert"
fi

say "Fertig!  Testen ohne Arduino:  ./run_boxautomat.sh --windowed --no-serial  (Leertaste = Schlag)"
