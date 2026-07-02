@echo off
setlocal
cd /d C:\AIWork\opt\gemini-ui

echo === gemini-ui venv setup ===

if not exist venv (
    python -m venv venv
    if errorlevel 1 goto :error
    echo [OK] venv created
) else (
    echo [SKIP] venv already exists
)

call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 goto :error

echo [OK] pip install complete
echo Next: edit .env and run install_ollama_models.bat
goto :end

:error
echo [NG] setup failed
exit /b 1

:end
endlocal
