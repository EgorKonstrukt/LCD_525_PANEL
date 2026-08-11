import os
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw

APP_NAME = "LCD525Panel"
ROOT = os.path.dirname(os.path.abspath(__file__))
BUILD_DIR = os.path.join(ROOT, "build")
ICON_PATH = os.path.join(ROOT, "app.ico")
OUT_EXE = os.path.join(BUILD_DIR, APP_NAME + ".exe")


def make_icon():
    img = Image.new("RGB", (64, 64), (20, 22, 26))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 6, 60, 58), radius=8, outline=(70, 200, 150), width=3)
    for x, y, h in ((8, 50, 12), (16, 50, 24), (24, 50, 18),
                    (32, 50, 30), (40, 50, 22), (48, 50, 38)):
        d.rounded_rectangle((x, y - h, x + 6, y), radius=2, fill=(110, 230, 130))
    img.save(ICON_PATH, sizes=[(256, 256), (128, 128), (64, 64),
                               (48, 48), (32, 32), (16, 16)])


def build():
    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--onefile",
        "--enable-plugin=tk-inter",
        "--include-package=psutil",
        "--include-package=pystray",
        "--include-package=PIL",
        "--include-package=serial",
        "--windows-console-mode=disable",
        "--windows-icon-from-ico=" + ICON_PATH,
        "--output-filename=" + APP_NAME + ".exe",
        "--output-dir=" + BUILD_DIR,
        "--assume-yes-for-downloads",
        "--product-name=" + APP_NAME,
        "--file-description=LCD525 Panel monitor",
        "--company-name=" + APP_NAME,
        "--file-version=1.0.0",
        "main.py",
    ]
    print("BUILD:", " ".join(cmd))
    subprocess.check_call(cmd)
    if not os.path.exists(OUT_EXE):
        raise RuntimeError("EXE not found: " + OUT_EXE)


def install():
    target_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_NAME)
    os.makedirs(target_dir, exist_ok=True)
    target_exe = os.path.join(target_dir, APP_NAME + ".exe")
    shutil.copy2(OUT_EXE, target_exe)
    import winreg
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0, winreg.KEY_SET_VALUE)
    try:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, '"%s"' % target_exe)
    finally:
        winreg.CloseKey(key)
    return target_exe


def main():
    make_icon()
    build()
    if "--no-install" not in sys.argv:
        target = install()
        print("INSTALLED:", target)
    print("DONE")


if __name__ == "__main__":
    main()
