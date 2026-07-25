@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
echo Checking required packages...

REM Check if all packages from requirements.txt are installed
set "missing_packages="

REM Check Pillow
echo Checking: Pillow (version 10.2.0)
python -c "import PIL" 2>nul
if errorlevel 1 (
    echo   - Pillow NOT INSTALLED
    set "missing_packages=Pillow==10.2.0"
) else (
    echo   - Pillow INSTALLED
)

REM Check pywin32
echo Checking: pywin32 (version 306)
python -c "import win32api" 2>nul
if errorlevel 1 (
    echo   - pywin32 NOT INSTALLED
    if "!missing_packages!"=="" (
        set "missing_packages=pywin32==306"
    ) else (
        set "missing_packages=!missing_packages! pywin32==306"
    )
) else (
    echo   - pywin32 INSTALLED
)

REM Check pystray
echo Checking: pystray (version 0.19.5)
python -c "import pystray" 2>nul
if errorlevel 1 (
    echo   - pystray NOT INSTALLED
    if "!missing_packages!"=="" (
        set "missing_packages=pystray==0.19.5"
    ) else (
        set "missing_packages=!missing_packages! pystray==0.19.5"
    )
) else (
    echo   - pystray INSTALLED
)

REM Check pygame
echo Checking: pygame (version 2.6.1)
python -c "import pygame" 2>nul
if errorlevel 1 (
    echo   - pygame NOT INSTALLED
    if "!missing_packages!"=="" (
        set "missing_packages=pygame==2.6.1"
    ) else (
        set "missing_packages=!missing_packages! pygame==2.6.1"
    )
) else (
    echo   - pygame INSTALLED
)

echo.
echo Check result: !missing_packages!




REM TODO: IT's better to check interception driver




REM If there are missing packages, ask user if they want to install them
if not "!missing_packages!"=="" (
    echo.
    echo Missing packages: !missing_packages!
    echo These packages are required to run cursor.py
    echo.
    set /p "install_choice=Install missing packages? (Y/N): "
    if /i "!install_choice!"=="Y" (
        echo Installing missing packages...
        for %%p in (!missing_packages!) do (
            echo Installing: %%p
            pip install %%p
            if errorlevel 1 (
                echo Failed to install: %%p
                echo Please check your internet connection or try manually.
            ) else (
                echo Successfully installed: %%p
            )
        )
        echo.
        echo Package installation completed!
        echo.
        echo Starting Multi-Mouse Monitor...
        start /b pythonw.exe cursor.py
    ) else (
        echo Installation cancelled. Program cannot run without required packages.
        echo Please ensure all required packages are installed before running this program.
        pause
        exit /b 1
    )
) else (
    echo All required packages are installed!
    echo.
    echo Starting Multi-Mouse Monitor...
    start /b pythonw.exe cursor.py
)