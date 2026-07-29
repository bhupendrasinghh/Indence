@echo off
title Indence Oncology Evidence Engine Launcher
color 0A

echo ======================================================================
echo           STARTING INDENCE ONCOLOGY EVIDENCE ENGINE
echo ======================================================================
echo.

echo [STEP 1] Cleaning up stale processes on ports 8000, 3000, and 6333...

for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo   Killing PID %%a on port 8000...
    taskkill /f /pid %%a >nul 2>&1
)

for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":3000 " ^| findstr "LISTENING"') do (
    echo   Killing PID %%a on port 3000...
    taskkill /f /pid %%a >nul 2>&1
)

for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":6333 " ^| findstr "LISTENING"') do (
    echo   Killing PID %%a on port 6333...
    taskkill /f /pid %%a >nul 2>&1
)
taskkill /f /im qdrant.exe >nul 2>&1

timeout /t 2 /nobreak >nul
echo   Ports cleared successfully.
echo.

echo [STEP 2] Launching FastAPI Backend Server (Port 8000) on GPU...
start "Indence Backend Server (Port 8000)" /D "c:\Users\ASHIRWAD PRATAPSINGH\Desktop\Indence\backend\src" cmd /k "..\venv\Scripts\python.exe -m uvicorn evidence_platform.app:app --host 0.0.0.0 --port 8000"

echo [STEP 3] Launching Next.js Production Frontend (Port 3000)...
start "Indence Frontend UI (Port 3000)" /D "c:\Users\ASHIRWAD PRATAPSINGH\Desktop\Indence\frontend" cmd /k "npx next start -p 3000"

echo.
echo [STEP 4] Waiting 12 seconds for backend AI models to load...
timeout /t 12 /nobreak >nul

echo [STEP 5] Opening web browser...
start http://localhost:3000

echo.
echo ======================================================================
echo   INDENCE PLATFORM STARTED!
echo   Backend API:  http://localhost:8000
echo   Frontend UI:  http://localhost:3000
echo ======================================================================
timeout /t 3 >nul
