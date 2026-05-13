@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

python "scripts\verify_dashboard_render.py"
set "EXITCODE=%ERRORLEVEL%"

echo.
if "%EXITCODE%"=="0" (
  echo 브라우저 렌더링 검증 성공
) else (
  echo 브라우저 렌더링 검증 실패
)
echo 스크린샷은 output\browser_checks 폴더에 저장됩니다.
pause
exit /b %EXITCODE%
