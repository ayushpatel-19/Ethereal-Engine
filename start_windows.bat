@echo off
setlocal EnableDelayedExpansion
title Ethereal Engine - Windows Launcher

set "ROOT=%~dp0"
set "BACKEND_DIR=%ROOT%backend"
set "FRONTEND_DIR=%ROOT%frontend"
set "VENV_PYTHON=%BACKEND_DIR%\venv\Scripts\python.exe"
set "ACTIVATE_BAT=%BACKEND_DIR%\venv\Scripts\activate.bat"
set "BACKEND_PORT=8010"
set "FRONTEND_PORT=3010"
set "LLM_MODEL=phi3:mini"
set "EMBED_MODEL=nomic-embed-text"

echo.
echo  =========================================================
echo   Ethereal Engine - Local Windows Startup
echo  =========================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found. Install Python 3.11+ and add it to PATH.
    pause
    exit /b 1
)

node --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js was not found. Install Node.js 18+ and add it to PATH.
    pause
    exit /b 1
)

npm --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] npm was not found. Install Node.js 18+ and add it to PATH.
    pause
    exit /b 1
)

ollama --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Ollama was not found. Install it from https://ollama.com/download
    pause
    exit /b 1
)

echo [1/6] Preparing Python virtual environment...
if not exist "%VENV_PYTHON%" (
    cd /d "%BACKEND_DIR%"
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create backend virtual environment.
        pause
        exit /b 1
    )
    echo       Virtual environment created.
) else (
    echo       Virtual environment already exists.
)

echo [2/6] Starting Ollama...
tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I "ollama.exe" >NUL
if errorlevel 1 (
    start "Ollama" /min ollama serve
    timeout /t 3 /nobreak >nul
    echo       Ollama started.
) else (
    echo       Ollama already running.
)

echo [3/6] Ensuring Ollama models are available...
ollama pull %LLM_MODEL%
if errorlevel 1 (
    echo [ERROR] Failed to pull %LLM_MODEL%.
    pause
    exit /b 1
)
ollama pull %EMBED_MODEL%
if errorlevel 1 (
    echo [ERROR] Failed to pull %EMBED_MODEL%.
    pause
    exit /b 1
)
echo       Models ready.

echo [4/6] Installing backend dependencies...
cd /d "%BACKEND_DIR%"
call "%ACTIVATE_BAT%"
python -m pip install --upgrade pip --quiet >nul 2>&1
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] Backend dependency installation failed.
    pause
    exit /b 1
)
python -m playwright install chromium >nul 2>&1
echo       Backend ready.

echo [5/6] Installing frontend dependencies...
cd /d "%FRONTEND_DIR%"
if not exist node_modules (
    npm install
    if errorlevel 1 (
        echo [ERROR] Frontend dependency installation failed.
        pause
        exit /b 1
    )
    echo       Frontend dependencies installed.
) else (
    echo       Frontend dependencies already installed.
)

echo [6/6] Starting backend and React frontend...
start "Ethereal Backend" cmd /k "cd /d ""%BACKEND_DIR%"" && call venv\Scripts\activate.bat && python -m uvicorn main:app --host 0.0.0.0 --port %BACKEND_PORT% --reload"
timeout /t 4 /nobreak >nul
start "Ethereal Frontend" cmd /k "cd /d ""%FRONTEND_DIR%"" && set VITE_API_BASE=http://localhost:%BACKEND_PORT%/api && npm run dev -- --host 0.0.0.0 --port %FRONTEND_PORT%"
timeout /t 4 /nobreak >nul

echo.
echo  =========================================================
echo   All services started
echo.
echo   Frontend :  http://localhost:%FRONTEND_PORT%
echo   Backend  :  http://localhost:%BACKEND_PORT%
echo   API Docs :  http://localhost:%BACKEND_PORT%/docs
echo  =========================================================
echo.
echo  Opening browser...
start "" "http://localhost:%FRONTEND_PORT%"

echo.
echo  Press any key to stop the Ethereal Engine processes...
pause >nul

echo Stopping services...
taskkill /FI "WINDOWTITLE eq Ethereal Backend*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Ethereal Frontend*" /T /F >nul 2>&1
echo Done. Goodbye.
endlocal
