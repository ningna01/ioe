@echo off
setlocal

set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"
cd /d "%ROOT_DIR%" || (
  echo [BROWSER-MODE] cannot enter project directory: %ROOT_DIR%
  pause
  exit /b 1
)

set "PYTHON_EXE=C:\Users\wen\AppData\Local\Programs\Python\Python312\python.exe"
set "HOST=127.0.0.1"
set "PORT=8000"
set "URL=http://%HOST%:%PORT%/"

if not exist "%PYTHON_EXE%" (
  echo [BROWSER-MODE] Python not found: %PYTHON_EXE%
  pause
  exit /b 1
)

for %%D in (db media logs backups temp) do (
  if not exist "%%D" mkdir "%%D"
)

set "IOE_DB_PATH=%ROOT_DIR%\db\db.sqlite3"
set "IOE_MEDIA_ROOT=%ROOT_DIR%\media"
set "IOE_LOG_DIR=%ROOT_DIR%\logs"
set "IOE_BACKUP_ROOT=%ROOT_DIR%\backups"
set "IOE_TEMP_DIR=%ROOT_DIR%\temp"

echo [BROWSER-MODE] %PYTHON_EXE% manage.py migrate --noinput
"%PYTHON_EXE%" manage.py migrate --noinput
if errorlevel 1 (
  echo [BROWSER-MODE] migrate failed.
  pause
  exit /b 1
)

echo [BROWSER-MODE] preparing browser opener for %URL%
start "" powershell -NoProfile -WindowStyle Hidden -Command "$u='%URL%'; $h='%HOST%'; $p=%PORT%; $deadline=(Get-Date).AddSeconds(30); while((Get-Date)-lt $deadline){ try{ $c=New-Object Net.Sockets.TcpClient($h,$p); $c.Close(); Start-Process $u; exit 0 } catch { Start-Sleep -Milliseconds 300 } }; Start-Process $u"

echo [BROWSER-MODE] starting Django at %HOST%:%PORT%
echo [BROWSER-MODE] keep this window open while using the system
"%PYTHON_EXE%" manage.py runserver %HOST%:%PORT% --noreload
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo [BROWSER-MODE] Django stopped with exit code %EXIT_CODE%
pause
exit /b %EXIT_CODE%
