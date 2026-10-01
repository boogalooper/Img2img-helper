@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

set "INSTALLER_VERSION=4"
set "RUNTIME=%LOCALAPPDATA%\JazzyScripts\SharedRuntime"
set "UV_DIR=%RUNTIME%\uv"
set "UV_STAGE=%RUNTIME%\uv.new"
set "UV=%UV_DIR%\uv.exe"
set "PYTHON_DIR=%RUNTIME%\python"
set "VENV=%RUNTIME%\venv"
set "CACHE=%RUNTIME%\cache"
set "PY=%VENV%\Scripts\python.exe"

set "PY_VERSION=3.11.16"
set "UV_VERSION=0.12.19"
set "PIP_INSECURE=0"

set "UV_SHA256=6dbb02d79e419522f1c500f0adb1cddcff0cda7d59b0d66ea7f5e3b4a1b2f5f0"

set "UV_URL=https://releases.astral.sh/github/uv/releases/download/%UV_VERSION%/uv-x86_64-pc-windows-msvc.zip"
set "UV_URL2=https://github.com/astral-sh/uv/releases/download/%UV_VERSION%/uv-x86_64-pc-windows-msvc.zip"

set "DOWNLOAD_DIR=%RUNTIME%\downloads"
set "UV_ZIP_TMP=%DOWNLOAD_DIR%\uv-x86_64-pc-windows-msvc.zip"
set "LAUNCHER_TMP=%RUNTIME%\launcher.vbs.new"
set "VERSION_TMP=%RUNTIME%\runtime_version.txt.new"

set "CURL=%SystemRoot%\System32\curl.exe"
if not exist "%CURL%" set "CURL=curl.exe"
set "TAR=%SystemRoot%\System32\tar.exe"
if not exist "%TAR%" set "TAR=tar.exe"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "UV_PYTHON_INSTALL_DIR=%PYTHON_DIR%"
set "UV_CACHE_DIR=%CACHE%"
set "UV_MANAGED_PYTHON=1"
set "UV_NO_MODIFY_PATH=1"
set "UV_PYTHON_INSTALL_BIN=0"

echo ==============================================
echo JazzyScripts - shared Python runtime
echo Installer revision: %INSTALLER_VERSION%
echo Runtime: %RUNTIME%
echo CPython: %PY_VERSION% x64 via uv
echo ==============================================
echo.
set "PYTHONNOUSERSITE=1"
set "PYTHONHOME="
set "PYTHONPATH="
set "LOCK=%RUNTIME%\install.lock"
set "LOCK_HELD=0"

call :preflight
if errorlevel 1 goto :failed

echo Connection mode for Python packages:
echo   [1] Normal secure mode ^(recommended^)
echo   [2] Kaspersky compatibility for official PyPI hosts
echo   [3] Cancel
echo.
choice /C 123 /N /M "Choose 1, 2 or 3: "
if errorlevel 3 goto :cancelled
if errorlevel 2 set "PIP_INSECURE=1"

if not exist "%RUNTIME%" mkdir "%RUNTIME%" >nul 2>&1
if not exist "%RUNTIME%" goto :failed
mkdir "%LOCK%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Another installer may be running.
    echo If a previous installer was forcibly closed, remove this empty folder:
    echo "%LOCK%"
    goto :failed
)
set "LOCK_HELD=1"
if not exist "%PYTHON_DIR%" mkdir "%PYTHON_DIR%" >nul 2>&1
if not exist "%DOWNLOAD_DIR%" mkdir "%DOWNLOAD_DIR%" >nul 2>&1

call :ensure_uv
if errorlevel 1 goto :failed
call :ensure_python
if errorlevel 1 goto :failed
call :ensure_packages
if errorlevel 1 goto :failed
call :create_launcher
if errorlevel 1 goto :failed
call :self_test
if errorlevel 1 goto :failed

> "%VERSION_TMP%" echo %INSTALLER_VERSION%
move /y "%VERSION_TMP%" "%RUNTIME%\runtime_version.txt" >nul 2>&1
if errorlevel 1 goto :failed
call :cleanup

echo.
echo ==============================================
echo Installation complete.
echo ==============================================
echo Runtime: %RUNTIME%
echo.
echo Shared runtime is ready. Install the Photoshop files from each script package.
echo Runtime for img2img helper, Remote API img2img helper and Spell checker.
echo.
pause
exit /b 0

:preflight
if not defined LOCALAPPDATA (
    echo [ERROR] LOCALAPPDATA is not defined.
    exit /b 1
)
if not defined TEMP (
    echo [ERROR] TEMP is not defined.
    exit /b 1
)
if /I "%PROCESSOR_ARCHITECTURE%"=="AMD64" goto :preflight_arch_ok
if /I "%PROCESSOR_ARCHITEW6432%"=="AMD64" goto :preflight_arch_ok
echo [ERROR] This installer requires 64-bit x86 Windows.
exit /b 1
:preflight_arch_ok
where certutil.exe >nul 2>&1
if errorlevel 1 (
    echo [ERROR] certutil.exe is required for SHA-256 verification.
    exit /b 1
)
"%TAR%" --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] tar.exe was not found. Modern Windows 10/11 includes it.
    exit /b 1
)
exit /b 0

:ensure_uv
set "UV_OK=0"
if exist "%UV%" (
    "%UV%" --version 2>nul | findstr /I /C:"uv %UV_VERSION%" >nul
    if not errorlevel 1 set "UV_OK=1"
)
if "%UV_OK%"=="1" (
    echo Existing uv %UV_VERSION% OK.
    exit /b 0
)

echo.
echo Installing verified uv %UV_VERSION%...
if exist "%UV_STAGE%" rmdir /s /q "%UV_STAGE%" >nul 2>&1
mkdir "%UV_STAGE%" >nul 2>&1
if not exist "%UV_STAGE%" (
    echo [ERROR] Cannot create temporary uv folder.
    exit /b 1
)

call :download_verified "%UV_URL%" "%UV_URL2%" "%UV_ZIP_TMP%" "%UV_SHA256%" "uv"
if errorlevel 1 exit /b 1

"%TAR%" -xf "%UV_ZIP_TMP%" -C "%UV_STAGE%"
if errorlevel 1 (
    echo [ERROR] Failed to extract uv archive.
    exit /b 1
)
if not exist "%UV_STAGE%\uv.exe" (
    echo [ERROR] uv.exe was not found after extraction.
    exit /b 1
)
"%UV_STAGE%\uv.exe" --version 2>nul | findstr /I /C:"uv %UV_VERSION%" >nul
if errorlevel 1 (
    echo [ERROR] Extracted uv has an unexpected version.
    exit /b 1
)
if exist "%UV_DIR%" rmdir /s /q "%UV_DIR%" >nul 2>&1
if exist "%UV_DIR%" (
    echo [ERROR] Cannot replace the old uv folder.
    exit /b 1
)
move "%UV_STAGE%" "%UV_DIR%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Cannot activate the new uv folder.
    exit /b 1
)
if not exist "%UV%" (
    echo [ERROR] uv.exe is missing after installation.
    exit /b 1
)
exit /b 0

:ensure_python
set "RECREATE_VENV=0"
if exist "%PY%" (
    "%PY%" -c "import sys,struct; raise SystemExit(0 if sys.version_info[:3]==(3,11,16) and struct.calcsize('P')==8 else 1)"
    if errorlevel 1 set "RECREATE_VENV=1"
)
if "%RECREATE_VENV%"=="1" (
    echo [ERROR] Existing venv is incompatible. Stop shared Python servers,
    echo rename SharedRuntime\venv, then run this installer again.
    exit /b 1
)
if not exist "%PY%" if exist "%VENV%" (
    echo [ERROR] An incomplete venv exists. Rename SharedRuntime\venv and retry.
    exit /b 1
)

if not exist "%PY%" (
    echo.
    echo Installing private CPython %PY_VERSION%...
    "%UV%" python install %PY_VERSION% --managed-python --no-bin
    if errorlevel 1 (
        echo [ERROR] uv could not install CPython %PY_VERSION%.
        exit /b 1
    )

    echo.
    echo Creating isolated virtual environment...
    "%UV%" venv "%VENV%" --python %PY_VERSION% --managed-python
    if errorlevel 1 (
        echo [ERROR] Failed to create the private virtual environment.
        exit /b 1
    )
)

if not exist "%PY%" (
    echo [ERROR] Private Python was not created: "%PY%".
    exit /b 1
)
"%PY%" -c "import sys,struct; raise SystemExit(0 if sys.version_info[:3]==(3,11,16) and struct.calcsize('P')==8 else 1)"
if errorlevel 1 (
    echo [ERROR] Private Python validation failed.
    exit /b 1
)
exit /b 0

:check_server_not_running
for %%P in (6380 6390 6410) do (
    "%PY%" -c "import socket; s=socket.socket(); s.settimeout(0.25); r=s.connect_ex(('127.0.0.1',%%P)); s.close(); raise SystemExit(0 if r==0 else 1)" >nul 2>&1
    if not errorlevel 1 (
        echo [ERROR] TCP port %%P is in use. A script server may still be running.
        echo Wait for the server to stop, then run this installer again.
        exit /b 1
    )
)
exit /b 0

:ensure_packages
"%PY%" -c "import importlib.metadata as m; expected={'Pillow': '11.1.0', 'requests': '2.32.3', 'websocket-client': '1.8.0', 'pymorphy3': '2.0.3'}; assert all(m.version(k)==v for k,v in expected.items()); import PIL.Image,requests,websocket,pymorphy3; assert callable(websocket.create_connection); assert pymorphy3.MorphAnalyzer().parse('test')" >nul 2>&1
if not errorlevel 1 (
    echo Existing Python packages OK.
    "%UV%" pip check --python "%PY%"
    if not errorlevel 1 goto :ensure_pip
    echo Existing environment has dependency problems; repairing it...
)

:install_packages
call :check_server_not_running
if errorlevel 1 exit /b 1
echo.
echo Installing pinned binary packages into the private venv...
if "%PIP_INSECURE%"=="1" (
    "%UV%" pip install --python "%PY%" --upgrade --no-build "Pillow==11.1.0" "requests==2.32.3" "websocket-client==1.8.0" "pymorphy3==2.0.3" --allow-insecure-host pypi.org --allow-insecure-host files.pythonhosted.org
) else (
    "%UV%" pip install --python "%PY%" --upgrade --no-build "Pillow==11.1.0" "requests==2.32.3" "websocket-client==1.8.0" "pymorphy3==2.0.3"
)
if errorlevel 1 (
    if "%PIP_INSECURE%"=="1" (
        echo [ERROR] Python package installation failed.
        exit /b 1
    )
    echo.
    echo Package installation failed. If Kaspersky intercepts HTTPS, retry PyPI only without certificate verification.
    echo   [1] Retry in Kaspersky compatibility mode
    echo   [2] Cancel
    choice /C 12 /N /M "Choose 1 or 2: "
    if errorlevel 2 exit /b 1
    set "PIP_INSECURE=1"
    goto :install_packages
)

"%UV%" pip check --python "%PY%"
if errorlevel 1 (
    echo [ERROR] Installed Python packages have dependency conflicts.
    exit /b 1
)
"%PY%" -c "import importlib.metadata as m; expected={'Pillow': '11.1.0', 'requests': '2.32.3', 'websocket-client': '1.8.0', 'pymorphy3': '2.0.3'}; assert all(m.version(k)==v for k,v in expected.items()); import PIL.Image,requests,websocket,pymorphy3; assert callable(websocket.create_connection); assert pymorphy3.MorphAnalyzer().parse('test')"
if errorlevel 1 (
    echo [ERROR] Python package validation failed.
    exit /b 1
)
:ensure_pip
"%PY%" -m pip --version >nul 2>&1
if not errorlevel 1 exit /b 0
call :check_server_not_running
if errorlevel 1 exit /b 1
"%PY%" -m ensurepip --upgrade
if errorlevel 1 exit /b 1
exit /b 0

:create_launcher
echo.
echo Creating shared runtime launcher...
> "%LAUNCHER_TMP%" echo Option Explicit
>>"%LAUNCHER_TMP%" echo Dim sh, fso, root, py, server, cmd
>>"%LAUNCHER_TMP%" echo Set sh = CreateObject^("WScript.Shell"^)
>>"%LAUNCHER_TMP%" echo Set fso = CreateObject^("Scripting.FileSystemObject"^)
>>"%LAUNCHER_TMP%" echo root = sh.ExpandEnvironmentStrings^("%%LOCALAPPDATA%%"^) ^& "\JazzyScripts\SharedRuntime"
>>"%LAUNCHER_TMP%" echo py = root ^& "\venv\Scripts\pythonw.exe"
>>"%LAUNCHER_TMP%" echo server = sh.Environment^("PROCESS"^)^("JAZZYSCRIPTS_SERVER"^)
>>"%LAUNCHER_TMP%" echo If Len^(server^) = 0 Then WScript.Quit 2
>>"%LAUNCHER_TMP%" echo If Not fso.FileExists^(py^) Then WScript.Quit 3
>>"%LAUNCHER_TMP%" echo If Not fso.FileExists^(server^) Then WScript.Quit 4
>>"%LAUNCHER_TMP%" echo sh.CurrentDirectory = fso.GetParentFolderName^(server^)
>>"%LAUNCHER_TMP%" echo sh.Environment^("PROCESS"^)^("PYTHONUTF8"^) = "1"
>>"%LAUNCHER_TMP%" echo sh.Environment^("PROCESS"^)^("PYTHONIOENCODING"^) = "utf-8"
>>"%LAUNCHER_TMP%" echo sh.Environment^("PROCESS"^)^("PYTHONNOUSERSITE"^) = "1"
>>"%LAUNCHER_TMP%" echo sh.Environment^("PROCESS"^).Remove "PYTHONHOME"
>>"%LAUNCHER_TMP%" echo sh.Environment^("PROCESS"^).Remove "PYTHONPATH"
>>"%LAUNCHER_TMP%" echo cmd = Chr^(34^) ^& py ^& Chr^(34^) ^& " " ^& Chr^(34^) ^& server ^& Chr^(34^)
>>"%LAUNCHER_TMP%" echo On Error Resume Next
>>"%LAUNCHER_TMP%" echo sh.Run cmd, 0, False
>>"%LAUNCHER_TMP%" echo If Err.Number ^<^> 0 Then WScript.Quit 5
>>"%LAUNCHER_TMP%" echo WScript.Quit 0
if not exist "%LAUNCHER_TMP%" exit /b 1
move /y "%LAUNCHER_TMP%" "%RUNTIME%\launcher.vbs" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Cannot create the shared launcher.
    exit /b 1
)
exit /b 0

:self_test
echo.
echo Running runtime self-test...
"%PY%" -c "import sys,struct; assert sys.version_info[:3]==(3,11,16) and struct.calcsize('P')==8; import importlib.metadata as m; expected={'Pillow': '11.1.0', 'requests': '2.32.3', 'websocket-client': '1.8.0', 'pymorphy3': '2.0.3'}; assert all(m.version(k)==v for k,v in expected.items()); import PIL.Image,requests,websocket,pymorphy3; assert callable(websocket.create_connection); assert pymorphy3.MorphAnalyzer().parse('test'); print('OK - Python and all shared dependencies')"
if errorlevel 1 (
    echo [ERROR] Runtime self-test failed.
    exit /b 1
)
exit /b 0

:download_verified
set "DV_URL1=%~1"
set "DV_URL2=%~2"
set "DV_OUT=%~3"
set "DV_HASH=%~4"
set "DV_NAME=%~5"
del /f /q "%DV_OUT%" >nul 2>&1
call :download_one "%DV_URL1%" "%DV_OUT%"
if not errorlevel 1 (
    call :verify_hash "%DV_OUT%" "%DV_HASH%"
    if not errorlevel 1 exit /b 0
    echo [WARN] %DV_NAME% downloaded from primary source but failed SHA-256 verification.
)
del /f /q "%DV_OUT%" >nul 2>&1
if not "%DV_URL2%"=="" (
    echo Trying fallback source for %DV_NAME%...
    call :download_one "%DV_URL2%" "%DV_OUT%"
    if not errorlevel 1 (
        call :verify_hash "%DV_OUT%" "%DV_HASH%"
        if not errorlevel 1 exit /b 0
        echo [WARN] %DV_NAME% downloaded from fallback source but failed SHA-256 verification.
    )
)
del /f /q "%DV_OUT%" >nul 2>&1
echo [ERROR] Failed to download a verified copy of %DV_NAME%.
exit /b 1

:download_one
set "DO_URL=%~1"
set "DO_OUT=%~2"
"%CURL%" --version >nul 2>&1
if not errorlevel 1 (
    "%CURL%" -L --fail --silent --show-error --retry 4 --connect-timeout 20 --max-time 600 --output "%DO_OUT%" "%DO_URL%"
    if not errorlevel 1 exit /b 0
    del /f /q "%DO_OUT%" >nul 2>&1
)
rem certutil is a fallback for Windows installations without curl.
certutil -urlcache -split -f "%DO_URL%" "%DO_OUT%" >nul 2>&1
if errorlevel 1 exit /b 1
if not exist "%DO_OUT%" exit /b 1
exit /b 0

:verify_hash
if not exist "%~1" exit /b 1
setlocal EnableDelayedExpansion
set "VH_ACTUAL="
for /f "skip=1 tokens=* delims=" %%H in ('certutil -hashfile "%~1" SHA256 2^>nul') do if not defined VH_ACTUAL set "VH_ACTUAL=%%H"
set "VH_ACTUAL=!VH_ACTUAL: =!"
if /I "!VH_ACTUAL!"=="%~2" (
    endlocal & exit /b 0
)
endlocal & exit /b 1

:cleanup
if not "%LOCK_HELD%"=="1" exit /b 0
if exist "%CACHE%" rmdir /s /q "%CACHE%" >nul 2>&1
if exist "%UV_STAGE%" rmdir /s /q "%UV_STAGE%" >nul 2>&1
if exist "%UV_ZIP_TMP%" del /f /q "%UV_ZIP_TMP%" >nul 2>&1
if exist "%LAUNCHER_TMP%" del /f /q "%LAUNCHER_TMP%" >nul 2>&1
if exist "%VERSION_TMP%" del /f /q "%VERSION_TMP%" >nul 2>&1
if exist "%LOCK%" rmdir "%LOCK%" >nul 2>&1
set "LOCK_HELD=0"
exit /b 0

:failed
call :cleanup
echo.
echo ==============================================
echo Installation failed. See the message above.
echo ==============================================
pause
exit /b 1

:cancelled
echo Installation cancelled.
exit /b 1
