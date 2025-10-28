#!/bin/bash
cd "$(dirname "$0")"
./run.py apply
read -n 1 -s -r -p "Gotowe. Naciśnij klawisz, aby zamknąć…"
