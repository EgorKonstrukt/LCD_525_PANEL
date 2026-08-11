#!/usr/bin/env bash
# ============================================================
#  LCD525 Panel - автоматическая сборка и заливка прошивки
#  под Linux (аналог flash.ps1 / flash.bat для Windows).
#
#  По умолчанию: Arduino Uno (atmega328p, загрузчик arduino).
#  Тип платы и программатор выбираются параметрами.
#
#  Примеры:
#    ./flash.sh                          # Uno, автопорт
#    ./flash.sh -Board nano
#    ./flash.sh -Board lgt8f -Port /dev/ttyUSB0
#    ./flash.sh -Board mega -Programmer usbasp
#    ./flash.sh -Board uno -BuildOnly    # только собрать
#    ./flash.sh -Board nano -BurnBootloader -Port /dev/ttyACM0
#
#  Инструменты ищутся в:
#    1) тулчейнах Arduino IDE (~/.arduino15/packages/...)
#    2) системных пакетах в PATH (gcc-avr, avrdude)
#  Ядра:
#    стандартное Arduino  - ~/.arduino15/packages/arduino/hardware/avr/*/ 
#                           или /usr/share/arduino/hardware/arduino/avr
#    LGT8F (WAVGAT)       - ~/.arduino15/packages/wavgat/hardware/avr/*/
# ============================================================
set -u
[ "${BASH_VERSINFO:-0}" -ge 4 ] || { echo "FATAL: нужен bash 4+" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG=""
WORKDIR=""
INO="$SCRIPT_DIR/AVR/AVR.ino"
BUILD_ONLY=0
BURN_BOOTLOADER=0
RESTART_APP=0
VERBOSE=0

usage() {
    awk 'BEGIN{c=0} /^# ====/{c++; next} c==1{print substr($0,3)} c==2{exit}' "$0"
    echo "Параметры:"
    echo "  -Board uno|nano|nano-old|pro-mini|pro-mini-8|mega|leonardo|micro|lgt8f"
    echo "  -Port /dev/ttyX            (иначе - автопоиск)"
    echo "  -Programmer arduino|usbasp|usbtiny|avrisp|stk500|stk500v1|avrispmkii"
    echo "  -BuildOnly -BurnBootloader -RestartApp -Verbose -WorkDir DIR -Ino FILE"
}

# ---------- Параметры ----------
BOARD="uno"
PORT=""
PROGRAMMER="arduino"
while [[ $# -gt 0 ]]; do
    case "$1" in
        -Board|--board)            BOARD="$2"; shift 2 ;;
        -Port|--port)              PORT="$2"; shift 2 ;;
        -Programmer|--programmer)  PROGRAMMER="$2"; shift 2 ;;
        -BuildOnly|--build-only)   BUILD_ONLY=1; shift ;;
        -BurnBootloader|--burn-bootloader) BURN_BOOTLOADER=1; shift ;;
        -RestartApp|--restart-app) RESTART_APP=1; shift ;;
        -Verbose|--verbose)        VERBOSE=1; shift ;;
        -WorkDir|--work-dir)       WORKDIR="$2"; shift 2 ;;
        -Ino|--ino)                INO="$2"; shift 2 ;;
        -h|-help|--help)           usage; exit 0 ;;
        *) echo "Неизвестный аргумент: $1" >&2; usage; exit 1 ;;
    esac
done

# ---------- Таблица плат ----------
# формат: mcu|f_cpu|variant|speed|protocol|core|bootloader|lfuse|hfuse|efuse|lock
declare -A BOARDS
BOARDS[uno]="atmega328p|16000000L|standard|115200|arduino|std|optiboot/optiboot_atmega328.hex|0xFF|0xDE|0xFD|0x0F"
BOARDS[nano]="atmega328p|16000000L|eightanaloginputs|115200|arduino|std|optiboot/optiboot_atmega328.hex|0xFF|0xDE|0xFD|0x0F"
BOARDS[nano-old]="atmega168|16000000L|eightanaloginputs|19200|arduino|std|atmega/ATmegaBOOT_168_diecimila.hex|0xFF|0xDD|0xF8|0x0F"
BOARDS[pro-mini]="atmega328p|16000000L|eightanaloginputs|57600|arduino|std|atmega/ATmegaBOOT_168_atmega328.hex|0xFF|0xDA|0xFD|0x0F"
BOARDS[pro-mini-8]="atmega328p|8000000L|eightanaloginputs|57600|arduino|std|atmega/ATmegaBOOT_168_atmega328_pro_8MHz.hex|0xFF|0xDA|0xFD|0x0F"
BOARDS[mega]="atmega2560|16000000L|mega|115200|arduino|std|stk500v2/stk500boot_v2_mega2560.hex|0xFF|0xD8|0xFD|0x0F"
BOARDS[leonardo]="atmega32u4|16000000L|leonardo|57600|avr109|std|caterina/Caterina-Leonardo.hex|0xFF|0xD8|0xCB|0x0F"
BOARDS[micro]="atmega32u4|16000000L|micro|57600|avr109|std|caterina/Caterina-Micro.hex|0xFF|0xD8|0xCB|0x0F"
BOARDS[lgt8f]="atmega328p|16000000L|lgt8fx8p48|57600|arduino|lgt|lgt8fx8p/optiboot_lgt8f328p.hex|0xFF|0xFF|0x07|0x3F"

# ---------- Таблица программаторов ----------
# формат: id|port_needed|baud (baud=0 -> использовать скорость платы)
declare -A PROGS
PROGS[arduino]="arduino|1|0"
PROGS[usbasp]="usbasp|0|0"
PROGS[usbtiny]="usbtiny|0|0"
PROGS[avrisp]="avrisp|1|19200"
PROGS[stk500]="stk500|1|19200"
PROGS[stk500v1]="stk500v1|1|19200"
PROGS[avrispmkii]="avrispmkii|0|0"

[[ -n "${BOARDS[$BOARD]:-}" ]] || { echo "FATAL: неизвестная плата: $BOARD" >&2; exit 1; }
[[ -n "${PROGS[$PROGRAMMER]:-}" ]] || { echo "FATAL: неизвестный программатор: $PROGRAMMER" >&2; exit 1; }

IFS='|' read -r MCU F_CPU VARIANT SPEED PROTOCOL CORE_KIND BOOT_HEX LFUSE HFUSE EFUSE LOCK <<< "${BOARDS[$BOARD]}"
IFS='|' read -r PROG_ID PROG_PORT PROG_BAUD <<< "${PROGS[$PROGRAMMER]}"

# ---------- Вспомогательные функции ----------
step() { echo -e "\033[36m==> $*\033[0m"; }
ok()   { echo -e "\033[32m$*\033[0m"; }
err()  { echo -e "\033[31mFATAL: $*\033[0m" >&2; exit 1; }

# ---------- Поиск инструментов и ядер ----------
GCC=""; GXX=""; OBJCOPY=""; SIZE=""; AVRDUDE=""; AVRDUDE_CONF=""
STD_CORE=""; LGT_CORE=""; LCI2C=""

discover_tools() {
    local base cand
    for base in \
        "$HOME/.arduino15/packages/arduino/tools/avr-gcc" \
        "$HOME/.arduino15/packages/wavgat/tools/avr-gcc"; do
        cand=$(ls -d "$base"/*/bin 2>/dev/null | sort -V | tail -n 1)
        if [[ -n "$cand" && -x "$cand/avr-gcc" && -x "$cand/avr-objcopy" ]]; then
            GCC="$cand/avr-gcc"; GXX="$cand/avr-g++"; OBJCOPY="$cand/avr-objcopy"
            [[ -x "$cand/avr-size" ]] && SIZE="$cand/avr-size"
            break
        fi
    done
    if [[ -z "$GCC" ]]; then
        command -v avr-gcc >/dev/null 2>&1 || err "avr-gcc не найден. Установите gcc-avr или Arduino IDE."
        command -v avr-objcopy >/dev/null 2>&1 || err "avr-objcopy не найден (gcc-avr)"
        GCC="avr-gcc"; GXX="avr-g++"; OBJCOPY="avr-objcopy"
        [[ -z "$SIZE" ]] && command -v avr-size >/dev/null 2>&1 && SIZE="avr-size"
    fi
    for base in \
        "$HOME/.arduino15/packages/arduino/tools/avrdude" \
        "$HOME/.arduino15/packages/wavgat/tools/avrdude"; do
        cand=$(ls -d "$base"/*/bin/avrdude 2>/dev/null | sort -V | tail -n 1)
        if [[ -n "$cand" && -x "$cand" ]]; then
            AVRDUDE="$cand"
            AVRDUDE_CONF="$(dirname "$cand")/../etc/avrdude.conf"
            break
        fi
    done
    if [[ -z "$AVRDUDE" ]]; then
        command -v avrdude >/dev/null 2>&1 || err "avrdude не найден. Установите avrdude или Arduino IDE."
        AVRDUDE="avrdude"
        if [[ -f "/etc/avrdude.conf" ]]; then AVRDUDE_CONF="/etc/avrdude.conf"; fi
    fi
    if [[ -n "$AVRDUDE_CONF" && ! -f "$AVRDUDE_CONF" ]]; then
        err "конфиг avrdude не найден: $AVRDUDE_CONF"
    fi
}

find_cores() {
    local cand
    cand=$(ls -d "$HOME/.arduino15/packages/arduino/hardware/avr"/*/ 2>/dev/null | sort -V | tail -n 1)
    if [[ -n "$cand" && -d "${cand%/}/cores/arduino" ]]; then
        STD_CORE="${cand%/}"
    elif [[ -d "/usr/share/arduino/hardware/arduino/avr/cores/arduino" ]]; then
        STD_CORE="/usr/share/arduino/hardware/arduino/avr"
    fi
    cand=$(ls -d "$HOME/.arduino15/packages/wavgat/hardware/avr"/*/ 2>/dev/null | sort -V | tail -n 1)
    if [[ -n "$cand" && -d "${cand%/}/cores/lgt8f" ]]; then
        LGT_CORE="${cand%/}"
    fi
}

find_lcd_lib() {
    local d
    for d in \
        "$HOME/Arduino/libraries/LiquidCrystal_I2C" \
        "$HOME/Documents/Arduino/libraries/LiquidCrystal_I2C" \
        "$HOME/sketchbook/libraries/LiquidCrystal_I2C" \
        "/usr/share/arduino/libraries/LiquidCrystal_I2C"; do
        if [[ -f "$d/LiquidCrystal_I2C.cpp" ]]; then LCI2C="$d"; return; fi
    done
}

find_port() {
    local d
    for d in /dev/ttyACM* /dev/ttyUSB*; do
        [[ -e "$d" ]] && { echo "$d"; return 0; }
    done
    return 1
}

touch_1200() {
    # 1200bps touch: перезапуск в bootloader для 32u4 (leonardo/micro)
    if command -v stty >/dev/null 2>&1; then
        stty -F "$1" 1200 2>/dev/null
        sleep 0.3
        stty -F "$1" 9600 2>/dev/null || true
        sleep 0.5
        echo "    1200bps touch on $1"
    fi
}

# ---------- Генерация sketch.cpp (прототипы функций из .ino) ----------
gen_sketch_cpp() {
    local ino="$1" out="$2" line sig
    local -a protos=()
    while IFS= read -r line; do
        if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*[[:space:]]+[A-Za-z_][A-Za-z0-9_]*[[:space:]]*\([^;{}]*\)[[:space:]]*)\{$ ]]; then
            sig="${BASH_REMATCH[1]}"
            sig="$(printf '%s' "$sig" | sed 's/  */ /g; s/^ *//; s/ *$//')"
            if [[ "$sig" != ISR\(* ]]; then
                protos+=("$sig;")
            fi
        fi
    done < "$ino"
    {
        echo "#include <Arduino.h>"
        printf '%s\n' "${protos[@]}"
        echo ""
        cat "$ino"
    } > "$out"
    echo "    protos: ${#protos[@]}"
}

# ---------- Компиляция ----------
compile_core() {
    local core_dir="$1" outdir="$2" src name out
    local -a CSRC=(wiring.c wiring_digital.c wiring_analog.c wiring_shift.c
                   wiring_pulse.c WInterrupts.c hooks.c wiring_pulse.S)
    local -a CPPSRC=(abi.cpp CDC.cpp HardwareSerial.cpp HardwareSerial0.cpp
                     HardwareSerial1.cpp HardwareSerial2.cpp HardwareSerial3.cpp
                     IPAddress.cpp main.cpp new.cpp PluggableUSB.cpp Print.cpp
                     Stream.cpp Tone.cpp USBCore.cpp WMath.cpp WString.cpp)
    declare -A OBJ_USED
    for src in "${CSRC[@]}"; do
        [[ -f "$core_dir/$src" ]] || continue
        name="${src%.*}"
        out="$outdir/$name.o"
        if [[ -n "${OBJ_USED[$out]:-}" ]]; then out="$outdir/${name}_asm.o"; fi
        OBJ_USED["$out"]=1
        [[ "$VERBOSE" -eq 1 ]] && echo "    gcc $src"
        "$GCC" "${CFLAGS[@]}" "${DEFS[@]}" "${INC[@]}" "$core_dir/$src" -o "$out" 2>>"$LOG" \
            || err "не удалось собрать $src (см. $LOG)"
        OBJS+=("$out")
    done
    for src in "${CPPSRC[@]}"; do
        [[ -f "$core_dir/$src" ]] || continue
        name="${src%.*}"
        out="$outdir/$name.o"
        if [[ -n "${OBJ_USED[$out]:-}" ]]; then out="$outdir/${name}_c.o"; fi
        OBJ_USED["$out"]=1
        [[ "$VERBOSE" -eq 1 ]] && echo "    g++ $src"
        "$GXX" "${CPPFLAGS[@]}" "${DEFS[@]}" "${INC[@]}" "$core_dir/$src" -o "$out" 2>>"$LOG" \
            || err "не удалось собрать $src (см. $LOG)"
        OBJS+=("$out")
    done
}

# ============================================================
#  Основной ход
# ============================================================
step "Поиск инструментов и ядер"
discover_tools
find_cores
find_lcd_lib

case "$CORE_KIND" in
    std)  CORE="$STD_CORE" ;;
    lgt)  CORE="$LGT_CORE" ;;
esac
[[ -n "$CORE" ]] || err "ядро не найдено. Установите ядро Arduino (или WAVGAT для lgt8f)."
CORE_DIR="$CORE/cores/$([ "$CORE_KIND" = "lgt" ] && echo lgt8f || echo arduino)"
VARIANT_DIR="$CORE/variants/$VARIANT"
[[ -d "$VARIANT_DIR" ]] || err "вариант платы не найден: $VARIANT_DIR"
[[ -f "$INO" ]] || err "скетч не найден: $INO"
[[ -n "$LCI2C" ]] || err "библиотека LiquidCrystal_I2C не найдена. Установите её в ~/Arduino/libraries."

# Библиотеки Wire/EEPROM в зависимости от ядра
if [[ "$CORE_KIND" = "lgt" ]]; then
    WIRE_DIR="$CORE/libraries/Wire"
    WIRE_CPP="$WIRE_DIR/Wire.cpp"
    WIRE_C="$WIRE_DIR/utility/twi.c"
    EEPROM_INC="$CORE/libraries/E2PROM"
    EEPROM_SRC="$CORE/libraries/E2PROM/EEPROM.cpp"
else
    WIRE_DIR="$CORE/libraries/Wire/src"
    WIRE_CPP="$WIRE_DIR/Wire.cpp"
    WIRE_C="$WIRE_DIR/utility/twi.c"
    EEPROM_INC="$CORE/libraries/EEPROM/src"
    EEPROM_SRC=""
fi
[[ -f "$WIRE_CPP" ]] || err "Wire.cpp не найден: $WIRE_CPP"
[[ -f "$WIRE_C" ]] || err "twi.c не найден: $WIRE_C"

# ---------- Рабочий каталог ----------
[[ -n "$WORKDIR" ]] || WORKDIR="${TMPDIR:-/tmp}/lcd525-avrbuild/$BOARD"
mkdir -p "$WORKDIR" || err "не удалось создать каталог: $WORKDIR"
LOG="$WORKDIR/build.log"
: > "$LOG"

DEFS=("-mmcu=$MCU" "-DF_CPU=$F_CPU" "-DARDUINO=10819" "-DARDUINO_ARCH_AVR")
if [[ "$BOARD" = "leonardo" || "$BOARD" = "micro" ]]; then
    PID_USB=$([ "$BOARD" = "micro" ] && echo "0x8037" || echo "0x8036")
    DEFS+=("-DUSBCON" "-DUSB_VID=0x2341" "-DUSB_PID=$PID_USB")
fi
CFLAGS=(-c -g -Os -w -std=gnu11 -ffunction-sections -fdata-sections -flto -fno-fat-lto-objects)
CPPFLAGS=(-c -g -Os -w -std=gnu++11 -fpermissive -ffunction-sections -fdata-sections -flto -fno-fat-lto-objects)
INC=("-I$CORE_DIR" "-I$VARIANT_DIR" "-I$LCI2C" "-I$WIRE_DIR" "-I$WIRE_DIR/utility" "-I$EEPROM_INC")

step "Плата: $BOARD ($MCU, $F_CPU) | Программатор: $PROGRAMMER"
step "Генерация sketch.cpp"
gen_sketch_cpp "$INO" "$WORKDIR/sketch.cpp"

declare -a OBJS
step "Компиляция ядра ($CORE_DIR)"
compile_core "$CORE_DIR" "$WORKDIR"

step "Компиляция библиотек"
[[ "$VERBOSE" -eq 1 ]] && echo "    g++ Wire.cpp"
"$GXX" "${CPPFLAGS[@]}" "${DEFS[@]}" "${INC[@]}" "$WIRE_CPP" -o "$WORKDIR/Wire.o" 2>>"$LOG" \
    || err "Wire.cpp (см. $LOG)"
OBJS+=("$WORKDIR/Wire.o")
[[ "$VERBOSE" -eq 1 ]] && echo "    gcc twi.c"
"$GCC" "${CFLAGS[@]}" "${DEFS[@]}" "${INC[@]}" "$WIRE_C" -o "$WORKDIR/twi.o" 2>>"$LOG" \
    || err "twi.c (см. $LOG)"
OBJS+=("$WORKDIR/twi.o")
[[ "$VERBOSE" -eq 1 ]] && echo "    g++ LiquidCrystal_I2C.cpp"
"$GXX" "${CPPFLAGS[@]}" "${DEFS[@]}" "${INC[@]}" "$LCI2C/LiquidCrystal_I2C.cpp" -o "$WORKDIR/LiquidCrystal_I2C.o" 2>>"$LOG" \
    || err "LiquidCrystal_I2C.cpp (см. $LOG)"
OBJS+=("$WORKDIR/LiquidCrystal_I2C.o")
if [[ -n "$EEPROM_SRC" ]]; then
    [[ "$VERBOSE" -eq 1 ]] && echo "    g++ EEPROM.cpp"
    "$GXX" "${CPPFLAGS[@]}" "${DEFS[@]}" "${INC[@]}" "$EEPROM_SRC" -o "$WORKDIR/EEPROM.o" 2>>"$LOG" \
        || err "EEPROM.cpp (см. $LOG)"
    OBJS+=("$WORKDIR/EEPROM.o")
fi

step "Компиляция скетча"
"$GXX" "${CPPFLAGS[@]}" "${DEFS[@]}" "${INC[@]}" "$WORKDIR/sketch.cpp" -o "$WORKDIR/sketch.o" 2>>"$LOG" \
    || err "sketch.cpp (см. $LOG)"
OBJS+=("$WORKDIR/sketch.o")

step "Линковка"
"$GCC" -w -Os -g -flto -fuse-linker-plugin "-Wl,--gc-sections" "-mmcu=$MCU" \
    -o "$WORKDIR/sketch.elf" "${OBJS[@]}" -L"$WORKDIR" -lm 2>>"$LOG" \
    || err "линковка не удалась (см. $LOG)"

step "Конвертация в HEX"
"$OBJCOPY" -O ihex -R .eeprom "$WORKDIR/sketch.elf" "$WORKDIR/sketch.hex" \
    || err "objcopy (см. $LOG)"

if [[ -n "$SIZE" ]]; then
    "$SIZE" "$WORKDIR/sketch.elf"
fi

HEX="$WORKDIR/sketch.hex"
echo ""
echo "HEX: $HEX"

if [[ "$BUILD_ONLY" -eq 1 ]]; then
    ok "BUILD OK (заливка пропущена: -BuildOnly)"
    exit 0
fi

# ---------- Порт ----------
if [[ -z "$PORT" ]]; then
    PORT="$(find_port)" || err "порт не найден. Укажите -Port /dev/ttyX"
    step "Порт не указан, автоматически выбран: $PORT"
fi
[[ -e "$PORT" ]] || err "порт не найден: $PORT"

# ---------- Останавливаем приложение ----------
APP_WAS_RUNNING=0
if pgrep -x LCD525Panel >/dev/null 2>&1; then
    APP_WAS_RUNNING=1
    step "Останавливаю LCD525Panel (порт занят приложением)"
    pkill -x LCD525Panel 2>/dev/null
    sleep 0.8
fi

# ---------- Аргументы avrdude ----------
AVR_ARGS=("-p$MCU" "-c$PROG_ID")
[[ -n "$AVRDUDE_CONF" ]] && AVR_ARGS=("-C$AVRDUDE_CONF" "${AVR_ARGS[@]}")
if [[ "$PROG_PORT" = "1" ]]; then
    AVR_ARGS+=("-P$PORT")
    if [[ "$PROGRAMMER" = "arduino" ]]; then
        if [[ "$PROTOCOL" = "avr109" ]]; then
            touch_1200 "$PORT"
        fi
        AVR_ARGS+=("-b$SPEED")
    else
        AVR_ARGS+=("-b$PROG_BAUD")
    fi
fi

# ---------- Заливка / загрузчик ----------
if [[ "$BURN_BOOTLOADER" -eq 1 ]]; then
    step "Прожиг загрузчика (fuses + bootloader)"
    echo -e "\033[33m    ВНИМАНИЕ: перезапись фьюзов может вывести плату из строя!\033[0m"
    BOOT_HEX="$CORE/bootloaders/$BOOT_HEX"
    [[ -f "$BOOT_HEX" ]] || err "bootloader не найден: $BOOT_HEX"
    "$AVRDUDE" "${AVR_ARGS[@]}" -e \
        "-Ulock:w:0x3F:m" \
        "-Uefuse:w:$EFUSE:m" \
        "-Uhfuse:w:$HFUSE:m" \
        "-Ulfuse:w:$LFUSE:m" \
        "-Uflash:w:$BOOT_HEX:i" \
        "-Ulock:w:$LOCK:m" || err "прожиг загрузчика не удался"
    ok "BOOTLOADER OK"
else
    step "Заливка прошивки ($BOARD, $PORT)"
    "$AVRDUDE" "${AVR_ARGS[@]}" -D "-Uflash:w:$HEX:i" || err "заливка не удалась"
    ok "UPLOAD OK"
fi

# ---------- Перезапуск приложения ----------
if [[ "$APP_WAS_RUNNING" -eq 1 && "$RESTART_APP" -eq 1 ]]; then
    step "Запускаю LCD525Panel"
    nohup "$HOME/.local/bin/LCD525Panel" >/dev/null 2>&1 &
    disown
elif [[ "$APP_WAS_RUNNING" -eq 1 ]]; then
    echo -e "\033[33mПриложение остановлено. Для запуска: ~/.local/bin/LCD525Panel\033[0m"
fi
