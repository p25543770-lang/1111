@echo off
setlocal
cd /d "%~dp0"
echo Password demo - server settings
echo 1. This computer only (127.0.0.1)
echo 2. All network interfaces (for a trusted network)
echo 3. Specific IPv4 address
set "choice=1"
set /p "choice=Choose 1, 2 or 3 [1]: "
set "host=127.0.0.1"
if "%choice%"=="2" set "host=0.0.0.0"
if "%choice%"=="3" (
  set "host="
  set /p "host=IPv4 address assigned to this computer: "
)
if not "%choice%"=="1" if not "%choice%"=="2" if not "%choice%"=="3" (
  echo Invalid choice.
  pause
  exit /b 1
)
set "port=8000"
set /p "port=Port [8000]: "
echo.
if "%choice%"=="2" (
  echo Find your IPv4 address below. Share http://YOUR-IP:%port%/ with trusted devices.
  ipconfig | findstr /i "IPv4"
  echo.
)
"%~dp0password_demo.exe" --host "%host%" --port "%port%"
echo.
echo Server stopped.
pause
