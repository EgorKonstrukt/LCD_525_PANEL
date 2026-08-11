<#
.SYNOPSIS
  Автоматическая сборка и заливка прошивки LCD525 Panel (AVR/AVR.ino).

.DESCRIPTION
  Скрипт полностью автоматически:
    1. генерирует sketch.cpp (с прототипами функций из .ino),
    2. компилирует ядро AVR + библиотеки + скетч (avr-gcc/avr-g++),
    3. линкует и конвертирует в HEX (avr-objcopy),
    4. заливает на плату через avrdude.

  По умолчанию сборка идёт под обычную плату Arduino (Uno, atmega328p,
  загрузчик arduino). Тип платы и программатор выбираются параметрами.

.EXAMPLE
  .\flash.ps1                         # сборка+заливка на Arduino Uno
  .\flash.ps1 -Board nano             # Arduino Nano (новый загрузчик)
  .\flash.ps1 -Board lgt8f -Port COM13
  .\flash.ps1 -Board uno -Programmer usbasp
  .\flash.ps1 -Board mega -BuildOnly  # только собрать, без заливки
  .\flash.ps1 -Board nano -BurnBootloader -Port COM5
#>
param(
    [ValidateSet("uno", "nano", "nano-old", "pro-mini", "pro-mini-8",
                 "mega", "leonardo", "micro", "lgt8f")]
    [string]$Board = "uno",
    [string]$Port = "",
    [ValidateSet("arduino", "usbasp", "usbtiny", "avrisp",
                 "stk500", "stk500v1", "avrispmkii")]
    [string]$Programmer = "arduino",
    [switch]$BuildOnly,
    [switch]$BurnBootloader,
    [switch]$RestartApp,
    [switch]$Verbose,
    [string]$Ino = "",
    [string]$WorkDir = ""
)

$ErrorActionPreference = "Continue"

# ---------- Пути к инструментам и ядрам ----------
$TOOL    = "C:\Program Files (x86)\Arduino\hardware\tools\avr"
$GCC     = "$TOOL\bin\avr-gcc.exe"
$GXX     = "$TOOL\bin\avr-g++.exe"
$OBJCOPY = "$TOOL\bin\avr-objcopy.exe"
$AVRDUDE = "$TOOL\bin\avrdude.exe"
$AVRCONF = "$TOOL\etc\avrdude.conf"
$LCI2C   = "C:\Users\Zarrakun\Documents\Arduino\libraries\LiquidCrystal_I2C"
$STD_CORE = "C:\Program Files (x86)\Arduino\hardware\arduino\avr"
$LGT_CORE = "$env:LOCALAPPDATA\Arduino15\packages\wavgat\hardware\avr\0.0.1"

# ---------- Таблица плат ----------
$BOARDS = @{
    uno = @{
        mcu="atmega328p"; f_cpu="16000000L"; variant="standard"
        speed=115200; protocol="arduino"; core=$STD_CORE; usb=$false
        bootloader="optiboot/optiboot_atmega328.hex"
        lfuse=0xFF; hfuse=0xDE; efuse=0xFD; lock=0x0F
    }
    nano = @{
        mcu="atmega328p"; f_cpu="16000000L"; variant="eightanaloginputs"
        speed=115200; protocol="arduino"; core=$STD_CORE; usb=$false
        bootloader="optiboot/optiboot_atmega328.hex"
        lfuse=0xFF; hfuse=0xDE; efuse=0xFD; lock=0x0F
    }
    "nano-old" = @{
        mcu="atmega168"; f_cpu="16000000L"; variant="eightanaloginputs"
        speed=19200; protocol="arduino"; core=$STD_CORE; usb=$false
        bootloader="atmega/ATmegaBOOT_168_diecimila.hex"
        lfuse=0xFF; hfuse=0xDD; efuse=0xF8; lock=0x0F
    }
    "pro-mini" = @{
        mcu="atmega328p"; f_cpu="16000000L"; variant="eightanaloginputs"
        speed=57600; protocol="arduino"; core=$STD_CORE; usb=$false
        bootloader="atmega/ATmegaBOOT_168_atmega328.hex"
        lfuse=0xFF; hfuse=0xDA; efuse=0xFD; lock=0x0F
    }
    "pro-mini-8" = @{
        mcu="atmega328p"; f_cpu="8000000L"; variant="eightanaloginputs"
        speed=57600; protocol="arduino"; core=$STD_CORE; usb=$false
        bootloader="atmega/ATmegaBOOT_168_atmega328_pro_8MHz.hex"
        lfuse=0xFF; hfuse=0xDA; efuse=0xFD; lock=0x0F
    }
    mega = @{
        mcu="atmega2560"; f_cpu="16000000L"; variant="mega"
        speed=115200; protocol="arduino"; core=$STD_CORE; usb=$false
        bootloader="stk500v2/stk500boot_v2_mega2560.hex"
        lfuse=0xFF; hfuse=0xD8; efuse=0xFD; lock=0x0F
    }
    leonardo = @{
        mcu="atmega32u4"; f_cpu="16000000L"; variant="leonardo"
        speed=57600; protocol="avr109"; core=$STD_CORE; usb=$true
        bootloader="caterina/Caterina-Leonardo.hex"
        lfuse=0xFF; hfuse=0xD8; efuse=0xCB; lock=0x0F
    }
    micro = @{
        mcu="atmega32u4"; f_cpu="16000000L"; variant="micro"
        speed=57600; protocol="avr109"; core=$STD_CORE; usb=$true
        bootloader="caterina/Caterina-Micro.hex"
        lfuse=0xFF; hfuse=0xD8; efuse=0xCB; lock=0x0F
    }
    lgt8f = @{
        mcu="atmega328p"; f_cpu="16000000L"; variant="lgt8fx8p48"
        speed=57600; protocol="arduino"; core=$LGT_CORE; usb=$false
        bootloader="lgt8fx8p/optiboot_lgt8f328p.hex"
        lfuse=0xFF; hfuse=0xFF; efuse=0x07; lock=0x3F
    }
}

# ---------- Таблица программаторов ----------
$PROG = @{
    arduino    = @{ id="arduino";    port=$true;  baud=0 }
    usbasp     = @{ id="usbasp";     port=$false; baud=0 }
    usbtiny    = @{ id="usbtiny";    port=$false; baud=0 }
    avrisp     = @{ id="avrisp";     port=$true;  baud=19200 }
    stk500     = @{ id="stk500";     port=$true;  baud=19200 }
    stk500v1   = @{ id="stk500v1";   port=$true;  baud=19200 }
    avrispmkii = @{ id="avrispmkii"; port=$false; baud=0 }
}

# ---------- Вспомогательные функции ----------
function Write-Step([string]$msg) {
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Find-Port {
    try {
        $pnp = Get-CimInstance Win32_PnPEntity -ErrorAction Stop |
               Where-Object { $_.Name -match '\(COM\d+\)' }
        foreach ($d in $pnp) {
            if ($d.DeviceID -match 'VID_(2341|2A03|1A86|10C4|0403)') {
                if ($d.Name -match '\((COM\d+)\)') { return $Matches[1] }
            }
        }
    } catch { }
    $ports = [System.IO.Ports.SerialPort]::GetPortNames()
    if ($ports.Count -gt 0) { return $ports[0] }
    return $null
}

function Invoke-Touch([string]$port) {
    # 1200bps touch: перезапуск в bootloader для 32u4
    try {
        $sp = New-Object System.IO.Ports.SerialPort($port, 1200)
        $sp.Open()
        Start-Sleep -Milliseconds 300
        $sp.Close()
        Start-Sleep -Milliseconds 500
        Write-Host "    1200bps touch on $port"
    } catch {
        Write-Host "    touch failed: $_"
    }
}

function New-SketchCpp([string]$inoPath, [string]$outPath) {
    $content = Get-Content -Raw -Path $inoPath
    $lines = $content -split "`r?`n"
    $protos = @()
    foreach ($l in $lines) {
        if ($l -match '^([A-Za-z_][A-Za-z0-9_]*\s+[A-Za-z_][A-Za-z0-9_]*\s*\([^;{}]*\)\s*)\{$') {
            $sig = ($Matches[1] -replace '\s+', ' ').Trim()
            if ($sig -notmatch '^ISR\(') { $protos += $sig + ';' }
        }
    }
    $header = "#include <Arduino.h>`n" + ($protos -join "`n") + "`n"
    Set-Content -Path $outPath -Value ($header + $content) -NoNewline -Encoding ASCII
    Write-Host "    protos: $($protos.Count)"
}

# ---------- Определяем плату и пути ----------
$b = $BOARDS[$Board]
$p = $PROG[$Programmer]

if (-not $Ino) {
    $Ino = Join-Path $PSScriptRoot "AVR\AVR.ino"
}
if (-not (Test-Path $Ino)) {
    Write-Host "FATAL: скетч не найден: $Ino" -ForegroundColor Red
    exit 1
}

if (-not $WorkDir) {
    $WorkDir = Join-Path $env:TEMP "opencode\avrbuild\$Board"
}
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

$log = Join-Path $WorkDir "build.log"
Remove-Item $log -ErrorAction SilentlyContinue

$CoreDir    = Join-Path $b.core "cores\$(@{uno="arduino"; nano="arduino"; "nano-old"="arduino"; "pro-mini"="arduino"; "pro-mini-8"="arduino"; mega="arduino"; leonardo="arduino"; micro="arduino"; lgt8f="lgt8f"}[$Board])"
$VariantDir = Join-Path $b.core "variants\$($b.variant)"

$CSources = @("wiring.c", "wiring_digital.c", "wiring_analog.c",
              "wiring_shift.c", "wiring_pulse.c", "WInterrupts.c",
              "hooks.c", "wiring_pulse.S")
$CPPSources = @("abi.cpp", "CDC.cpp", "HardwareSerial.cpp",
                "HardwareSerial0.cpp", "HardwareSerial1.cpp",
                "HardwareSerial2.cpp", "HardwareSerial3.cpp",
                "IPAddress.cpp", "main.cpp", "new.cpp", "PluggableUSB.cpp",
                "Print.cpp", "Stream.cpp", "Tone.cpp", "USBCore.cpp",
                "WMath.cpp", "WString.cpp")

if (-not (Test-Path $CoreDir)) {
    Write-Host "FATAL: ядро не найдено: $CoreDir" -ForegroundColor Red
    exit 1
}

# ---------- Пути библиотек ----------
if ($Board -eq "lgt8f") {
    $WireDir     = Join-Path $b.core "libraries\Wire"
    $WireUtil    = Join-Path $WireDir "utility"
    $EEPROM_SRC  = Join-Path $b.core "libraries\E2PROM\EEPROM.cpp"
    $EEPROM_INC  = Join-Path $b.core "libraries\E2PROM"
    $BootRoot    = Join-Path $b.core "bootloaders"
} else {
    $WireDir     = Join-Path $b.core "libraries\Wire\src"
    $WireUtil    = Join-Path $WireDir "utility"
    $EEPROM_SRC  = ""   # в стандартном ядре EEPROM — header-only
    $EEPROM_INC  = Join-Path $b.core "libraries\EEPROM\src"
    $BootRoot    = Join-Path $b.core "bootloaders"
}

$DEFS = @("-mmcu=$($b.mcu)", "-DF_CPU=$($b.f_cpu)", "-DARDUINO=10819", "-DARDUINO_ARCH_AVR")
if ($b.usb) {
    $pid = if ($Board -eq "micro") { "0x8037" } else { "0x8036" }
    $DEFS += @("-DUSBCON", "-DUSB_VID=0x2341", "-DUSB_PID=$pid")
}
$CFLAGS   = @("-c", "-g", "-Os", "-w", "-std=gnu11", "-ffunction-sections",
              "-fdata-sections", "-flto", "-fno-fat-lto-objects")
$CPPFLAGS = @("-c", "-g", "-Os", "-w", "-std=gnu++11", "-fpermissive",
              "-fno-exceptions", "-ffunction-sections", "-fdata-sections",
              "-fno-threadsafe-statics", "-flto")
$INC = @("-I$CoreDir", "-I$VariantDir", "-I$LCI2C", "-I$WireDir", "-I$WireUtil", "-I$EEPROM_INC")

# ---------- Порт ----------
if (-not $BuildOnly -and $p.port) {
    if (-not $Port) {
        $Port = Find-Port
        Write-Step "Порт не указан, автоматически выбран: $Port"
    }
    if (-not $Port -or -not ([System.IO.Ports.SerialPort]::GetPortNames() -contains $Port)) {
        Write-Host "FATAL: порт $Port не найден. Укажите -Port COMxx" -ForegroundColor Red
        exit 1
    }
}

# ---------- Останавливаем приложение ----------
$appWasRunning = $false
$appExe = "$env:LOCALAPPDATA\LCD525Panel\LCD525Panel.exe"
if (-not $BuildOnly) {
    $proc = Get-Process LCD525Panel -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Step "Останавливаю LCD525Panel (порт занят приложением)"
        $proc | Stop-Process -Force
        $appWasRunning = $true
        Start-Sleep -Milliseconds 800
    }
}

Write-Step "Плата: $Board ($($b.mcu), $($b.f_cpu)) | Программатор: $Programmer"
Write-Step "Генерация sketch.cpp"
New-SketchCpp $Ino (Join-Path $WorkDir "sketch.cpp")

# ---------- Компиляция ----------
Write-Step "Компиляция ядра ($CoreDir)"
$objs = @()
foreach ($f in $CSources) {
    $src = Join-Path $CoreDir $f
    if (Test-Path $src) {
        $name = [IO.Path]::GetFileNameWithoutExtension($f)
        $out  = Join-Path $WorkDir ($name + ".o")
        if ($objs -contains $out) { $out = Join-Path $WorkDir ($name + "_asm.o") }
        if ($Verbose) { Write-Host "    gcc $f" }
        & $GCC @CFLAGS @DEFS @INC $src -o $out 2>> $log
        if ($LASTEXITCODE -ne 0) { Write-Host "FATAL: не удалось собрать $f (см. $log)" -ForegroundColor Red; exit 1 }
        $objs += $out
    }
}
foreach ($f in $CPPSources) {
    $src = Join-Path $CoreDir $f
    if (Test-Path $src) {
        $name = [IO.Path]::GetFileNameWithoutExtension($f)
        $out  = Join-Path $WorkDir ($name + ".o")
        if ($objs -contains $out) { $out = Join-Path $WorkDir ($name + "_c.o") }
        if ($Verbose) { Write-Host "    g++ $f" }
        & $GXX @CPPFLAGS @DEFS @INC $src -o $out 2>> $log
        if ($LASTEXITCODE -ne 0) { Write-Host "FATAL: не удалось собрать $f (см. $log)" -ForegroundColor Red; exit 1 }
        $objs += $out
    }
}

Write-Step "Компиляция библиотек"
$wireCpp = Join-Path $WireDir "Wire.cpp"
$wireC   = Join-Path $WireUtil "twi.c"
& $GXX @CPPFLAGS @DEFS @INC $wireCpp -o (Join-Path $WorkDir "Wire.o") 2>> $log
if ($LASTEXITCODE -ne 0) { Write-Host "FATAL: Wire.cpp (см. $log)" -ForegroundColor Red; exit 1 }
$objs += Join-Path $WorkDir "Wire.o"
& $GCC @CFLAGS @DEFS @INC $wireC -o (Join-Path $WorkDir "twi.o") 2>> $log
if ($LASTEXITCODE -ne 0) { Write-Host "FATAL: twi.c (см. $log)" -ForegroundColor Red; exit 1 }
$objs += Join-Path $WorkDir "twi.o"

& $GXX @CPPFLAGS @DEFS @INC "$LCI2C\LiquidCrystal_I2C.cpp" -o (Join-Path $WorkDir "LiquidCrystal_I2C.o") 2>> $log
if ($LASTEXITCODE -ne 0) { Write-Host "FATAL: LiquidCrystal_I2C.cpp (см. $log)" -ForegroundColor Red; exit 1 }
$objs += Join-Path $WorkDir "LiquidCrystal_I2C.o"

if ($EEPROM_SRC) {
    & $GXX @CPPFLAGS @DEFS @INC $EEPROM_SRC -o (Join-Path $WorkDir "EEPROM.o") 2>> $log
    if ($LASTEXITCODE -ne 0) { Write-Host "FATAL: EEPROM.cpp (см. $log)" -ForegroundColor Red; exit 1 }
    $objs += Join-Path $WorkDir "EEPROM.o"
}

Write-Step "Компиляция скетча"
& $GXX @CPPFLAGS @DEFS @INC (Join-Path $WorkDir "sketch.cpp") -o (Join-Path $WorkDir "sketch.o") 2>> $log
if ($LASTEXITCODE -ne 0) { Write-Host "FATAL: sketch.cpp (см. $log)" -ForegroundColor Red; exit 1 }
$objs += Join-Path $WorkDir "sketch.o"

# ---------- Линковка ----------
Write-Step "Линковка"
& $GCC -w -Os -g -flto -fuse-linker-plugin "-Wl,--gc-sections" $DEFS[0] -o (Join-Path $WorkDir "sketch.elf") $objs -L$WorkDir -lm 2>> $log
if ($LASTEXITCODE -ne 0) { Write-Host "FATAL: линковка не удалась (см. $log)" -ForegroundColor Red; exit 1 }

Write-Step "Конвертация в HEX"
& $OBJCOPY -O ihex -R .eeprom (Join-Path $WorkDir "sketch.elf") (Join-Path $WorkDir "sketch.hex")
if ($LASTEXITCODE -ne 0) { Write-Host "FATAL: objcopy (см. $log)" -ForegroundColor Red; exit 1 }

& "$TOOL\bin\avr-size.exe" (Join-Path $WorkDir "sketch.elf")
$hex = Join-Path $WorkDir "sketch.hex"
Write-Host ""
Write-Host "HEX: $hex"

if ($BuildOnly) {
    Write-Host "BUILD OK (заливка пропущена: -BuildOnly)" -ForegroundColor Green
    exit 0
}

# ---------- Программатор / аргументы avrdude ----------
$avrArgs = @("-C$AVRCONF", "-p$($b.mcu)", "-c$($p.id)")
if ($p.port) {
    if ($p.id -eq "arduino") {
        if ($b.protocol -eq "avr109") {
            Invoke-Touch $Port
            $avrArgs += "-P$Port", "-b$($b.speed)"
        } else {
            $avrArgs += "-P$Port", "-b$($b.speed)"
        }
    } else {
        $avrArgs += "-P$Port", "-b$($p.baud)"
    }
}

# ---------- Заливка / загрузчик ----------
if ($BurnBootloader) {
    Write-Step "Прожиг загрузчика (fuses + bootloader)"
    Write-Host "    ВНИМАНИЕ: перезапись фьюзов может вывести плату из строя!" -ForegroundColor Yellow
    $bootHex = Join-Path $BootRoot $b.bootloader
    if (-not (Test-Path $bootHex)) {
        Write-Host "FATAL: bootloader не найден: $bootHex" -ForegroundColor Red
        exit 1
    }
    & $AVRDUDE @avrArgs "-e" `
        "-Ulock:w:0x3F:m" `
        "-Uefuse:w:0x$('{0:X2}' -f $b.efuse):m" `
        "-Uhfuse:w:0x$('{0:X2}' -f $b.hfuse):m" `
        "-Ulfuse:w:0x$('{0:X2}' -f $b.lfuse):m" `
        "-Uflash:w:`"$bootHex`":i" `
        "-Ulock:w:0x$('{0:X2}' -f $b.lock):m"
    if ($LASTEXITCODE -ne 0) { Write-Host "FATAL: прожиг загрузчика не удался" -ForegroundColor Red; exit 1 }
    Write-Host "BOOTLOADER OK" -ForegroundColor Green
} else {
    Write-Step "Заливка прошивки ($Board, $Port)"
    & $AVRDUDE @avrArgs "-D" "-Uflash:w:`"$hex`":i"
    if ($LASTEXITCODE -ne 0) { Write-Host "FATAL: заливка не удалась" -ForegroundColor Red; exit 1 }
    Write-Host "UPLOAD OK" -ForegroundColor Green
}

# ---------- Перезапуск приложения ----------
if ($appWasRunning -and $RestartApp) {
    if (Test-Path $appExe) {
        Write-Step "Запускаю LCD525Panel"
        Start-Process -FilePath $appExe
    }
} elseif ($appWasRunning -and -not $BuildOnly) {
    Write-Host "Приложение остановлено. Для запуска: $appExe" -ForegroundColor Yellow
}
