# LCD525 Panel

> [Русская версия](README.md)

PC status monitor based on an Arduino (WAVGAT UNO clone, LGT8F) and a 1602
LCD display (16x2, I2C). The Windows application (Python, built with Nuitka)
polls the system, displays the data on the LCD, controls the buzzer and LED,
and alerts when USB devices are connected/disconnected or when error windows
appear.
![img.jpg](img.jpg)
## Features

- **16x2 LCD readout:**
  - CPU load, CPU frequency;
  - used RAM;
  - GPU load (nvidia-smi), temperature, frequency;
  - CPU temperature (LibreHardwareMonitor / OpenHardwareMonitor).
- **Boot animation** — the title "LCD525 PANEL" types out character by
  character, a progress bar, then transition to the normal mode.
- **Spinner** — bitmap wheel animation (8 custom CGRAM characters) in the
  bottom-right corner, updated every 350 ms.
- **Sound alerts:**
  - errors/warnings detected from Windows window titles;
  - USB device connect/disconnect;
  - PC link up/link down;
  - 9 patterns: `short`, `long`, `double`, `triple`, `rapid`, `chime_up`,
    `chime_down`, `siren`, `wake`.
- **LED** (pin 13): PWM "load" proportional to CPU load and blink on alert.
- **Windows autostart**, tray icon, GUI settings.

## Hardware

| Component | Pin/address |
|---|---|
| Arduino (UNO clone, LGT8F) | — |
| LCD 1602 I2C | address `0x27`, SDA = A4, SCL = A5 |
| Buzzer | pin **3** (default, configurable) |
| LED | pin **13** (built-in) |
| COM port | 9600 baud, newline-terminated commands |

## Protocol

The buzzer can be active or passive. For a passive buzzer the tone is generated
in a TIMER1 interrupt (32 kHz soft-PWM); frequency and volume are configurable.
Settings are stored in the board EEPROM.

Commands from the PC to the board:

| Command | Purpose |
|---|---|
| `D:line1\|line2` | update display text (16 and 15 characters) |
| `A:pattern` | pattern + 2 s LED blink |
| `B:pattern` | pattern only |
| `X:0-100` | LED duty (PWM, 500 ms cycle) |
| `P:2-13` | buzzer pin (saved to EEPROM) |
| `V:0-100` | volume |
| `F:100-10000` | passive buzzer frequency, Hz |
| `S:25-400` | pattern speed, % |
| `T:passive\|active` | buzzer type |
| `L:on\|off` | LED enable/disable |
| `Q:` | reply `S:pin,passive,volume,freq,speed,beeping` |

## Installation

### Windows application

The ready `LCD525Panel.exe` is built by `build.py` and installed to
`%LOCALAPPDATA%\LCD525Panel\`:

```
python build.py
```

Build requirements: Python 3.11+, Nuitka, a C compiler (MSVC).

Dependencies: `psutil`, `pystray`, `PIL` (Pillow), `pyserial`.

Settings are stored in `%APPDATA%\LCD525Panel\config.json` (logs in `app.log`).

### Firmware

The sketch is `AVR/AVR.ino` (Arduino IDE). For building outside the IDE a
`build.bat` is used from the build directory (see
`C:\Users\<user>\AppData\Local\Temp\opencode\avrbuild`), compiling with
avr-gcc/avr-g++ and flashing via avrdude:

```
avrdude -patmega328p -carduino -PCOM10 -b57600 -D -Uflash:w:sketch.hex:i -V
```

Stop the application before flashing (otherwise the port is busy). Flashing
reboots the board — the `wake` pattern plays and the boot animation runs.

## Settings

The settings window opens from the tray icon menu.

- **Connection** — COM port, update interval.
- **Notifications** — error/warning window watching, buzzer (pin, type,
  volume, frequency, speed), patterns for errors/warnings, test buttons.
- **Display** — which parameters are shown on the LCD.
- **USB** — sound on USB device connect/disconnect.

The tray menu also provides: status, reconnect, autostart, settings folder.

## Project structure

```
AVR/AVR.ino      Arduino firmware
main.py          Windows application (system polling, LCD, sound)
build.py         .exe build (Nuitka onefile)
3D_models/       case STL models
```
