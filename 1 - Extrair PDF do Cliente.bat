@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Extrair PDF do Cliente

echo ================================================
echo   Extrair dados do PDF do cliente
echo ================================================
echo.
echo Coloque o PDF do cliente na pasta "ds160_preencher"
echo antes de rodar isso, se ainda nao colocou.
echo.
python leitor_pdf.py

echo.
echo ================================================
pause
