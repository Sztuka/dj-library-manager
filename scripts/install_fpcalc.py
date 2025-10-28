#!/usr/bin/env python3
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = ROOT / "bin" / "mac"
DEST = BIN_DIR / "fpcalc"

CANDIDATE_URLS = [
    # Chromaprint 1.5.1 universal (sprawdzone, działa na Intel & Apple Silicon)
    "https://github.com/acoustid/chromaprint/releases/download/v1.5.1/chromaprint-fpcalc-1.5.1-macos-universal.tar.gz",
    # Starszy fallback (gdyby powyższy był niedostępny)
    "https://github.com/acoustid/chromaprint/releases/download/v1.5.0/chromaprint-fpcalc-1.5.0-macos-universal.tar.gz"
]

def have_brew() -> bool:
    try:
        subprocess.run(["brew", "--version"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False

def brew_install():
    print("🔧 Próba instalacji przez Homebrew: brew install chromaprint ...")
    r = subprocess.run(["brew", "install", "chromaprint"])
    if r.returncode != 0:
        raise RuntimeError("Nie udało się zainstalować chromaprint przez Homebrew.")
    print("✅ Zainstalowano przez Homebrew.")

def download_vendor():
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    for url in CANDIDATE_URLS:
        try:
            print(f"⬇️  Pobieram: {url}")
            with urlopen(url) as resp, tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
                tmp.write(resp.read())
                tgz_path = Path(tmp.name)

            with tarfile.open(tgz_path, "r:gz") as tf:
                member = next((m for m in tf.getmembers() if m.name.endswith("/fpcalc")), None)
                if not member:
                    raise RuntimeError("W archiwum nie znaleziono fpcalc.")
                tf.extract(member, BIN_DIR)
                # po ekstrakcji ścieżka to BIN_DIR/<folder>/fpcalc
                folder = BIN_DIR / Path(member.name).parts[0]
                src = folder / "fpcalc"
                shutil.move(str(src), str(DEST))
                shutil.rmtree(folder, ignore_errors=True)
            tgz_path.unlink(missing_ok=True)

            DEST.chmod(0o755)
            print(f"✅ Zainstalowano fpcalc do: {DEST}")
            return
        except Exception as e:
            print(f"⚠️  Próba nieudana ({e}). Próbuję kolejny url...")
    raise RuntimeError("Nie udało się pobrać fpcalc z żadnego źródła.")

def main():
    if platform.system().lower() != "darwin":
        raise SystemExit("Ten installer jest przeznaczony dla macOS.")

    # 1) jeśli już jest w PATH – nic nie rób
    if shutil.which("fpcalc"):
        print("✅ fpcalc już jest w systemie (PATH).")
        return

    # 2) jeśli mamy już vendora – nic nie rób
    if DEST.exists():
        DEST.chmod(0o755)
        print(f"✅ fpcalc już dostępny: {DEST}")
        return

    # 3) brew albo download
    if have_brew():
        try:
            brew_install()
            return
        except Exception as e:
            print(f"⚠️  Homebrew nie zadziałał ({e}). Próbuję pobrać binarkę...")

    download_vendor()

if __name__ == "__main__":
    main()
