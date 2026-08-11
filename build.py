import os
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw

APP_NAME = "LCD525Panel"
ROOT = os.path.dirname(os.path.abspath(__file__))
BUILD_DIR = os.path.join(ROOT, "build")
IS_WINDOWS = sys.platform == "win32"
OUT_BIN = os.path.join(BUILD_DIR, APP_NAME + (".exe" if IS_WINDOWS else ""))


def make_icon():
    img = Image.new("RGB", (64, 64), (20, 22, 26))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 6, 60, 58), radius=8, outline=(70, 200, 150), width=3)
    for x, y, h in ((8, 50, 12), (16, 50, 24), (24, 50, 18),
                    (32, 50, 30), (40, 50, 22), (48, 50, 38)):
        d.rounded_rectangle((x, y - h, x + 6, y), radius=2, fill=(110, 230, 130))
    icon_ico = os.path.join(ROOT, "app.ico")
    icon_png = os.path.join(ROOT, "app.png")
    if IS_WINDOWS:
        img.save(icon_ico, sizes=[(256, 256), (128, 128), (64, 64),
                                  (48, 48), (32, 32), (16, 16)])
    img.save(icon_png)
    return icon_ico, icon_png


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
        "--output-dir=" + BUILD_DIR,
        "--assume-yes-for-downloads",
    ]
    if IS_WINDOWS:
        cmd += [
            "--windows-console-mode=disable",
            "--windows-icon-from-ico=" + os.path.join(ROOT, "app.ico"),
            "--output-filename=" + APP_NAME + ".exe",
            "--product-name=" + APP_NAME,
            "--file-description=LCD525 Panel monitor",
            "--company-name=" + APP_NAME,
            "--file-version=1.0.0",
        ]
    else:
        cmd += [
            "--output-filename=" + APP_NAME,
            "--include-package=Xlib",
            "--linux-onefile-icon=" + os.path.join(ROOT, "app.png"),
        ]
    cmd.append("main.py")
    print("BUILD:", " ".join(cmd))
    subprocess.check_call(cmd)
    if not os.path.exists(OUT_BIN):
        raise RuntimeError("Binary not found: " + OUT_BIN)


def _linux_install():
    bin_dir = os.path.join(os.path.expanduser("~"), ".local", "bin")
    os.makedirs(bin_dir, exist_ok=True)
    target = os.path.join(bin_dir, APP_NAME)
    shutil.copy2(OUT_BIN, target)
    os.chmod(target, 0o755)

    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    autostart_dir = os.path.join(base, "autostart")
    os.makedirs(autostart_dir, exist_ok=True)
    desktop = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=%s\n"
        "Comment=LCD525 Panel monitor\n"
        "Exec=%s\n"
        "Icon=%s\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "Hidden=false\n" % (APP_NAME, target, os.path.join(ROOT, "app.png"))
    )
    for d in (autostart_dir,
              os.path.join(os.path.expanduser("~"), ".local", "share", "applications")):
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, APP_NAME + ".desktop"), "w", encoding="utf-8") as f:
            f.write(desktop)
    return target


def install():
    if IS_WINDOWS:
        target_dir = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_NAME)
        os.makedirs(target_dir, exist_ok=True)
        target_exe = os.path.join(target_dir, APP_NAME + ".exe")
        shutil.copy2(OUT_BIN, target_exe)
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
    return _linux_install()


def main():
    make_icon()
    build()
    if "--no-install" not in sys.argv:
        target = install()
        print("INSTALLED:", target)
    print("DONE")


if __name__ == "__main__":
    main()
