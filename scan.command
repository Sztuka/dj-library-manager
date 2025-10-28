#!/bin/bash
cd "$(dirname "$0")"
./run.py scan
read -n 1 -s -r -p "Gotowe. Naciśnij klawisz, aby zamknąć…"
