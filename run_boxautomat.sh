#!/usr/bin/env bash
# Startet den Boxautomaten. Zusätzliche Argumente werden durchgereicht,
# z.B.  ./run_boxautomat.sh --windowed --no-serial
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# Bildschirmschoner / Energiesparen aus, solange der Automat läuft
if command -v xset >/dev/null 2>&1 && [ -n "${DISPLAY:-}" ]; then
    xset s off -dpms s noblank 2>/dev/null || true
fi

if [ ! -x "$DIR/.venv/bin/python" ]; then
    echo "Keine .venv gefunden – bitte zuerst ./install.sh ausführen." >&2
    exit 1
fi

exec "$DIR/.venv/bin/python" -m boxautomat "$@"
