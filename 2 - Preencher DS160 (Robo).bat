@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Robo DS-160

echo ================================================
echo   Robo de preenchimento do DS-160
echo ================================================
echo.
echo Isso so funciona depois de rodar o passo
echo "1 - Extrair PDF do Cliente" primeiro.
echo.
python robo.py

echo.
echo ================================================
pause
