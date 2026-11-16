"""Prepare the project environment and launch Yomiquill."""

import argparse
import importlib.util
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from struct import calcsize


ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PYTHON = VENV / "Scripts" / "python.exe"
REQUIREMENTS = ROOT / "requirements.txt"
MODULES = ("PySide6", "pypdf", "pypdfium2", "openvino_genai", "huggingface_hub")


def run_python(*args):
    subprocess.run([str(PYTHON), *map(str, args)], cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description="Yomiquill のセットアップと起動")
    parser.add_argument("--setup-only", action="store_true", help="準備だけ実行します")
    args = parser.parse_args()
    if sys.platform != "win32" or sys.version_info[:2] != (3, 12) or calcsize("P") != 8:
        raise SystemExit("64 ビット版 Windows と Python 3.12 が必要です。README を確認してください。")

    if Path(sys.executable).resolve() != PYTHON.resolve():
        if not PYTHON.is_file():
            print("[1/3] Yomiquill 専用の Python 環境を作成しています…", flush=True)
            subprocess.run([sys.executable, "-m", "venv", str(VENV)], cwd=ROOT, check=True)
        run_python(__file__, *sys.argv[1:])
        return

    signature = sha256(REQUIREMENTS.read_bytes()).hexdigest()
    stamp = VENV / ".yomiquill-requirements"
    installed = stamp.is_file() and stamp.read_text(encoding="ascii").strip() == signature
    if not installed or any(importlib.util.find_spec(module) is None for module in MODULES):
        if importlib.util.find_spec("pip") is None:
            print("[1/3] 仮想環境の pip を復旧しています…", flush=True)
            run_python("-m", "ensurepip")
        print("[1/3] 必要なライブラリをインストールしています…", flush=True)
        run_python("-m", "pip", "--disable-pip-version-check", "install", "-r", REQUIREMENTS)
        stamp.write_text(signature + "\n", encoding="ascii")

    from setup_models import prepare_models

    prepare_models()
    if args.setup_only:
        print("Yomiquill の準備が完了しました。", flush=True)
    else:
        print("[3/3] Yomiquill を起動しています…", flush=True)
        run_python(ROOT / "desktop.py")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from None
