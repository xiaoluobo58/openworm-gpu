@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "MODE=legacy"
set "DURATION=15"

:options
if "%~1"=="" goto start
if /i "%~1"=="-d" goto duration
if /i "%~1"=="-gpu" (
  set "MODE=gpu"
  shift
  goto options
)
if /i "%~1"=="-cpu" (
  set "MODE=cpu"
  shift
  goto options
)
if /i "%~1"=="-check-gpu" (
  set "MODE=check-gpu"
  shift
  goto options
)
goto usage

:duration
if "%~2"=="" goto usage
set "DURATION=%~2"
shift
shift
goto options

:start
pushd "%~dp0" || exit /b 1
set "OW_OUT_DIR=/home/ow/shared"
set "HOST_OUT_DIR=%CD%"
set "VERSION="
set /p "VERSION="<VERSION
if not defined VERSION goto missing_version

where docker >nul 2>nul
if errorlevel 1 goto missing_docker

if /i "%MODE%"=="legacy" goto legacy
set "DOCKER_OS="
for /f "delims=" %%i in ('docker info --format "{{.OSType}}"') do set "DOCKER_OS=%%i"
if /i not "%DOCKER_OS%"=="linux" goto wrong_engine
set "TARGET=%MODE%"
if /i "%MODE%"=="check-gpu" set "TARGET=gpu"
set "IMAGE=openworm-%TARGET%:%VERSION%"
docker image inspect "%IMAGE%" >nul 2>nul
if not errorlevel 1 goto run_local
docker build --platform linux/amd64 --target "%TARGET%" --tag "%IMAGE%" .
if errorlevel 1 goto finish

:run_local
if /i "%MODE%"=="check-gpu" goto check_gpu
if /i "%TARGET%"=="gpu" goto gpu
docker run --rm --init --env OW_OUT_DIR --env DURATION --env OW_DEVICE=CPU ^
  --mount "type=bind,source=%HOST_OUT_DIR%,target=%OW_OUT_DIR%" ^
  "%IMAGE%"
goto finish

:gpu
docker run --rm --init --gpus all --env OW_OUT_DIR --env DURATION ^
  --mount "type=bind,source=%HOST_OUT_DIR%,target=%OW_OUT_DIR%" ^
  --mount "type=volume,source=openworm-pocl-cuda-7.1,target=/var/cache/openworm-pocl" ^
  "%IMAGE%"
goto finish

:check_gpu
docker run --rm --gpus all "%IMAGE%" python3 openworm_gpu.py
goto finish

:legacy
docker run -d --name openworm --env OW_OUT_DIR --env DURATION --privileged ^
  --volume "%HOST_OUT_DIR%:%OW_OUT_DIR%:rw" ^
  "openworm/openworm:%VERSION%" python3 master_openworm.py
if errorlevel 1 goto finish
docker logs -f openworm
goto finish

:usage
echo Usage: %~nx0 [-gpu ^| -cpu ^| -check-gpu] [-d duration_in_ms] >&2
exit /b 2

:missing_version
echo OpenWorm could not read VERSION. >&2
goto failed

:missing_docker
echo Docker was not found. Install or start Docker Desktop and reopen this terminal. >&2
goto failed

:wrong_engine
echo OpenWorm requires a running Docker Linux engine. For GPU mode, enable the WSL 2 backend in Docker Desktop. >&2
goto failed

:failed
popd
exit /b 1

:finish
set "RUN_STATUS=%ERRORLEVEL%"
popd
exit /b %RUN_STATUS%

@echo off
endlocal
