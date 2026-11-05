@echo off
chcp 936 >nul
setlocal
title 安装音频分析环境
set "ROOT=%~dp0"
set "AUDIO=%LOCALAPPDATA%\qqmusic-audio"
set "VENV=%AUDIO%\venv"
set "MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"

echo ==========================================
echo   一起听：安装音频分析环境
echo   会占用约 5~6 GB 磁盘，装在：
echo   %AUDIO%
echo ==========================================
echo.

where py >nul 2>&1
if errorlevel 1 (
  echo 找不到 py 启动器，请确认装了 Python 3.13
  pause
  exit /b 1
)

if not exist "%VENV%\Scripts\python.exe" (
  echo [1/4] 创建独立环境...
  py -3.13 -m venv "%VENV%" || (echo 创建失败 & pause & exit /b 1)
) else (
  echo [1/4] 环境已存在，跳过创建
)

echo.
echo [2/4] 安装录音与分析库（约 300MB，几分钟）...
"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -i %MIRROR% -r "%ROOT%local\audio\requirements.txt"
if errorlevel 1 (echo 安装失败，把上面的报错发给 DS & pause & exit /b 1)

echo.
echo [3/4] 安装 PyTorch（CUDA 版，约 2.6GB，最慢的一步）...
"%VENV%\Scripts\python.exe" -c "import torch" >nul 2>&1
if errorlevel 1 (
  "%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check --index-url https://download.pytorch.org/whl/cu126 torch torchaudio
  if errorlevel 1 (echo PyTorch 安装失败，把报错发给 DS & pause & exit /b 1)
) else (
  echo PyTorch 已装，跳过
)

echo.
echo [4/4] 下载乐器识别的标签文件...
if not exist "%AUDIO%\panns_data" mkdir "%AUDIO%\panns_data"
if not exist "%AUDIO%\panns_data\class_labels_indices.csv" (
  curl -L -o "%AUDIO%\panns_data\class_labels_indices.csv" https://raw.githubusercontent.com/qiuqiangkong/audioset_tagging_cnn/master/metadata/class_labels_indices.csv
)

echo.
echo 自检...
"%VENV%\Scripts\python.exe" -c "import torch,torchaudio,librosa,soundfile,soundcard,demucs,panns_inference;print('依赖 OK；CUDA 可用:',torch.cuda.is_available())"
echo.
echo ==========================================
echo   装好了。以后双击“启动听歌采集.bat”时，
echo   会顺带开启录音和分析，不用再跑这个。
echo ==========================================
pause