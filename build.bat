@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo  SC Translator 打包（PyInstaller onedir）
echo ============================================
echo.

set PY=.venv\Scripts\python.exe
if not exist "%PY%" (
  echo [错误] 未找到虚拟环境 %PY%
  echo        请先双击 run.bat 完成一次环境安装。
  pause
  exit /b 1
)

"%PY%" -c "import PyInstaller" 2>nul
if errorlevel 1 (
  echo [1/2] 安装 PyInstaller ...
  "%PY%" -m pip install --upgrade pyinstaller || goto :fail
) else (
  echo [1/2] PyInstaller 已安装，跳过。
)

echo [2/2] 开始打包 ...
"%PY%" -m PyInstaller --noconfirm --clean SCTranslator.spec || goto :fail

rem 校验产物：PyInstaller 退出码 0 不代表真的产出 exe，缺模型/被中断都可能空手而归。
set "EXE=dist\SCTranslator\SCTranslator.exe"
if not exist "%EXE%" (
  echo [错误] 打包命令结束但未找到产物 %EXE%，视为构建失败。
  goto :fail
)

rem 版本号从 sc_translator\__init__.py 现读，和 publish.bat 保持一致
set "VER="
rem 与 publish.bat 一致：for /f 里 exe 路径不加引号（加引号会被 cmd 引号剥离规则弄坏）
for /f "delims=" %%v in ('%PY% -c "import sc_translator; print(sc_translator.__version__)"') do set "VER=%%v"
if not defined VER (
  echo [错误] 无法读取版本号，跳过 ZIP 生成。
  goto :fail
)
set "ZIP=dist\SCTranslator-v%VER%-win64.zip"
echo [3/3] 生成发行包 %ZIP% ...
if exist "%ZIP%" del /q "%ZIP%"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\SCTranslator\*' -DestinationPath '%ZIP%' -Force"
if errorlevel 1 goto :fail
if not exist "%ZIP%" (
  echo [错误] 压缩完成但未找到 %ZIP%。
  goto :fail
)

echo.
echo 打包完成：%EXE%
echo 发行包：%ZIP%
echo 自检命令：%EXE% --doctor
echo 在线自检：%EXE% --doctor --online
pause
exit /b 0

:fail
echo.
echo [失败] 打包未完成，请查看上方错误信息。
pause
exit /b 1
