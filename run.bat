@echo off
setlocal
cd /d "%~dp0"

echo.
echo  CinderClip - local launcher
echo.

python -c "import imageio_ffmpeg" >nul 2>&1
if errorlevel 1 (
    echo  Installing the local FFmpeg runtime...
    python -m pip install --disable-pip-version-check -q -r requirements.txt
    if errorlevel 1 (
        echo.
        echo  Could not install the FFmpeg runtime automatically.
        echo  Run: python -m pip install imageio-ffmpeg
        pause
        exit /b 1
    )
)

python server.py
pause
