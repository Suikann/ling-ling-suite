@echo off
echo Installing build dependencies...
pip install pyinstaller

echo Running tests...
python -m pytest tests/ -v --tb=short
if errorlevel 1 (
    echo Tests failed. Aborting build.
    exit /b 1
)

echo Building portable EXE...
pyinstaller --noconfirm --onedir --windowed ^
    --name "LingLingSuite" ^
    --add-data "src\assets;assets" ^
    --hidden-import PySide6.QtSvg ^
    --icon NONE ^
    src\main.py

echo.
echo Build complete: dist\LingLingSuite\
pause
