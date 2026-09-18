@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title AgentZh 一键汉化安装器

set "AGENTZH_REPO=652036/agent-zh-cn"
set "AGENTZH_HOME=%LOCALAPPDATA%\AgentZh"
set "AGENTZH_CURRENT=%LOCALAPPDATA%\AgentZh\current"
set "AGENTZH_WORK=%TEMP%\AgentZh-Setup-%RANDOM%-%RANDOM%"

echo ============================================================
echo   AgentZh - Devin / Cursor / Windsurf / VS Code 简体中文
echo ============================================================
echo.
echo [1/4] 正在准备安装文件...
mkdir "%AGENTZH_WORK%" >nul 2>nul
if errorlevel 1 goto :fail

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$ErrorActionPreference='Stop';" ^
 "$repo=$env:AGENTZH_REPO; $work=$env:AGENTZH_WORK; $home=$env:AGENTZH_HOME; $current=$env:AGENTZH_CURRENT;" ^
 "$zip=Join-Path $work 'AgentZh.zip';" ^
 "Write-Host '[2/4] 正在下载最新版...';" ^
 "try {" ^
 "  $headers=@{'User-Agent'='AgentZh-Installer'};" ^
 "  $release=Invoke-RestMethod -Headers $headers -Uri ('https://api.github.com/repos/'+$repo+'/releases/latest');" ^
 "  $asset=$release.assets | Where-Object { $_.name -eq 'AgentZh-Windows.zip' } | Select-Object -First 1;" ^
 "  if (-not $asset) { throw '最新 Release 中没有 AgentZh-Windows.zip' }" ^
 "  Write-Host ('版本：'+$release.tag_name);" ^
 "  Invoke-WebRequest -Headers $headers -UseBasicParsing -Uri $asset.browser_download_url -OutFile $zip;" ^
 "} catch {" ^
 "  Write-Host '未找到正式 Windows 包，改用 main 分支最新版。';" ^
 "  Invoke-WebRequest -UseBasicParsing -Uri ('https://github.com/'+$repo+'/archive/refs/heads/main.zip') -OutFile $zip;" ^
 "}" ^
 "Write-Host '[3/4] 正在解压...';" ^
 "$extract=Join-Path $work 'extract'; Expand-Archive -LiteralPath $zip -DestinationPath $extract -Force;" ^
 "$entry=Get-ChildItem -LiteralPath $extract -Filter 'AgentZh.ps1' -File -Recurse | Select-Object -First 1;" ^
 "if (-not $entry) { throw '安装包结构无效：没有找到 AgentZh.ps1' }" ^
 "$source=$entry.Directory.FullName; New-Item -ItemType Directory -Force -Path $home | Out-Null;" ^
 "$stage=Join-Path $home ('stage-'+[Guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Force -Path $stage | Out-Null;" ^
 "Get-ChildItem -LiteralPath $source -Force | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $stage -Recurse -Force };" ^
 "if (Test-Path -LiteralPath $current) { Remove-Item -LiteralPath $current -Recurse -Force };" ^
 "Move-Item -LiteralPath $stage -Destination $current;" ^
 "Write-Host '[4/4] 正在应用汉化...';" ^
 "& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $current 'AgentZh.ps1') -Cmd apply -Kill -Restart;" ^
 "if ($LASTEXITCODE -ne 0) { throw ('AgentZh 执行失败，退出码：'+$LASTEXITCODE) }"
if errorlevel 1 goto :fail

echo.
echo 汉化完成。
rmdir /s /q "%AGENTZH_WORK%" >nul 2>nul
pause
exit /b 0

:fail
echo.
echo [错误] AgentZh 安装/汉化未完成，请保留上方错误信息。
echo https://github.com/652036/agent-zh-cn/issues
rmdir /s /q "%AGENTZH_WORK%" >nul 2>nul
pause
exit /b 1
