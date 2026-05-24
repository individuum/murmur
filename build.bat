@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo No virtualenv yet. Run run.bat once first.
  exit /b 1
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pyinstaller -q || exit /b 1

if not exist "icon.ico" (
  echo Generating icon.ico...
  python -c "import icons; icons.idle().save('icon.ico', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])"
)

echo.
echo Building Murmur.exe ...
echo.
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
pyinstaller --clean --noconfirm murmur.spec || exit /b 1

echo.
echo ===== BUILD COMPLETE =====
echo dist\Murmur.exe
echo.
echo You can copy that single file anywhere and run it.
endlocal
