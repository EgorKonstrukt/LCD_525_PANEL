@echo off
rem ============================================================
rem  LCD525 Panel - автоматическая сборка и заливка прошивки
rem  По умолчанию: Arduino Uno (atmega328p, загрузчик arduino)
rem
rem  Примеры:
rem    flash.bat                          -> Uno, автопорт
rem    flash.bat -Board nano
rem    flash.bat -Board lgt8f -Port COM13
rem    flash.bat -Board mega -Programmer usbasp
rem    flash.bat -Board uno -BuildOnly    -> только собрать
rem ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0flash.ps1" %*
