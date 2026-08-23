@echo off
chcp 65001 >nul
title Instalador - Automacao DS-160
cd /d "%~dp0"

echo ================================================
echo   Instalador - Automacao de Vistos DS-160
echo ================================================
echo.
echo Este computador precisa de internet para instalar
echo tudo. Isso pode demorar alguns minutos.
echo.

where python >nul 2>&1
if %errorlevel% equ 0 goto python_ok

echo Python nao encontrado neste computador.
echo Baixando o instalador oficial do Python...
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile '%TEMP%\python-installer.exe'"

if not exist "%TEMP%\python-installer.exe" (
    echo.
    echo ERRO: nao consegui baixar o Python. Verifique a internet
    echo ou instale manualmente em python.org/downloads marcando
    echo a opcao "Add python.exe to PATH", depois rode este
    echo instalador de novo.
    pause
    exit /b
)

echo Instalando o Python, aguarde...
"%TEMP%\python-installer.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0

echo.
echo ================================================
echo   Python foi instalado agora.
echo   FECHE esta janela e clique de novo em INSTALAR.bat
echo   para concluir a instalacao.
echo ================================================
pause
exit /b

:python_ok
echo Python encontrado. Instalando as bibliotecas necessarias...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo ERRO ao instalar as bibliotecas. Verifique sua internet
    echo e rode este instalador de novo.
    pause
    exit /b
)

echo.
echo Instalando o navegador usado pelo robo, aguarde...
python -m playwright install chromium

echo.
echo Criando atalhos na Area de Trabalho...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0criar_atalhos.ps1"

echo.
echo ================================================
echo   Instalacao concluida!
echo.
echo   Use os atalhos criados na Area de Trabalho:
echo     1 - Extrair PDF do Cliente
echo     2 - Preencher DS160 (Robo)
echo ================================================
pause
