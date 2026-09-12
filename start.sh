#!/bin/bash

# 1. Запуск виртуального экрана
Xvfb :99 -screen 0 1024x768x16 &
export DISPLAY=:99
sleep 2

# 2. Запуск MetaTrader 5 в фоне через Wine
MT5_PATH=$(find /root/.wine/drive_c -name "terminal64.exe" | head -n 1)
if [ -n "$MT5_PATH" ]; then
    echo "Запуск MT5: $MT5_PATH"
    wine "$MT5_PATH" &
else
    echo "Ошибка: terminal64.exe не найден"
fi
sleep 5

# 3. Запуск сервера-моста mt5server
echo "Запуск mt5server.exe..."
wine /app/mt5server.exe &
sleep 3

# 4. Запуск Python приложения
echo "Запуск основного сервиса..."
python3 /app/main.py
