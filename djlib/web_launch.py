from __future__ import annotations
import threading, time, webbrowser
import uvicorn

def _open():
    # daj serwerowi chwilę na start
    time.sleep(1.2)
    # Otwórz Setup Wizard (krok 1)
    webbrowser.open("http://127.0.0.1:8000/wizard")

def main() -> None:
    threading.Thread(target=_open, daemon=True).start()
    uvicorn.run("djlib.webapp:app", host="127.0.0.1", port=8000, reload=False)

if __name__ == "__main__":
    main()
