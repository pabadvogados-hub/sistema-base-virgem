@echo off
REM ==========================================================================
REM  Sistema Escritorio Modelo Advogados - rodar LOCAL no Windows
REM  Duplo clique. Na primeira vez cria o ambiente Python e instala tudo.
REM  A tela abre em http://localhost:8000 (o backend serve a tela neste modo).
REM  Rotinas automaticas NAO rodam aqui: elas ficam na VPS.
REM ==========================================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0..\.."
set "RAIZ=%CD%"

where python >nul 2>nul || (echo Instale o Python 3.11+ em python.org e marque "Add to PATH". & pause & exit /b 1)

if not exist "%RAIZ%\config\.env" (
  echo Falta config\.env - copiando o modelo. Preencha, salve e rode de novo.
  copy "%RAIZ%\config\.env.example" "%RAIZ%\config\.env" >nul
  notepad "%RAIZ%\config\.env"
  exit /b 0
)

if not exist "%RAIZ%\.venv\Scripts\python.exe" (
  echo Criando ambiente Python, pode levar alguns minutos...
  python -m venv "%RAIZ%\.venv" || (pause & exit /b 1)
  "%RAIZ%\.venv\Scripts\python.exe" -m pip install --upgrade pip
  "%RAIZ%\.venv\Scripts\python.exe" -m pip install -r "%RAIZ%\SISTEMA\backend\requirements.txt" || (pause & exit /b 1)
)

set "ESC_CONFIG_ENV=%RAIZ%\config\.env"
set "SISTEMA_DB=%RAIZ%\SISTEMA\backend\sistema_esc.db"
set "ESC_MEDIA_DIR=%RAIZ%\SISTEMA\backend\media"
set "SERVIR_FRONTEND=1"
set "DATABASE_URL=sqlite+aiosqlite:///%RAIZ:\=/%/SISTEMA/backend/sistema_esc.db"

cd /d "%RAIZ%\SISTEMA\backend"
echo.
echo Primeiro acesso? Crie o administrador em outra janela:
echo   cd "%RAIZ%\SISTEMA\backend" ^&^& "%RAIZ%\.venv\Scripts\python.exe" seed_admin.py "Seu Nome" seu@email
echo.
start "" http://localhost:8000
"%RAIZ%\.venv\Scripts\python.exe" -m uvicorn app.main_vps:app --host 127.0.0.1 --port 8000
pause
