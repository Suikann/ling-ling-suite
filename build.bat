@echo off
cd /d "%~dp0"
echo Installing build dependencies...
pip install pyinstaller

echo Running tests...
python -m pytest tests/ -v --tb=short
if errorlevel 1 (
    echo Tests failed. Aborting build.
    exit /b 1
)

echo Building portable EXE...
pyinstaller --noconfirm LingLingSuite.spec
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo.
echo Build complete: dist\LingLingSuite\
pause
