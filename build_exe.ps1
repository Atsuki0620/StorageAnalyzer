# StorageAnalyzer を PyInstaller で exe 化する（Windows / PowerShell 用）
#
# 使い方:
#   1) PowerShell をこのフォルダで開く
#   2) （初回のみ）実行ポリシーで弾かれる場合:
#        powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
#   3) 生成物: dist\StorageAnalyzer\StorageAnalyzer.exe
#
# まずは安定性重視で --onedir（フォルダ配布）を使います。

$ErrorActionPreference = "Stop"

Write-Host "==> 依存パッケージをインストール" -ForegroundColor Cyan
python -m pip install -r requirements.txt
python -m pip install pyinstaller

Write-Host "==> PyInstaller でビルド (--onedir)" -ForegroundColor Cyan

# 注意点:
#  --paths src        : src レイアウトの storage_analyzer パッケージを解決する
#  --add-data "A;B"   : Windows の区切りは ';'（templates と config.yaml を同梱）
#  --collect-data plotly : plotly.js（インライン埋め込み用）の同梱を確実にする
pyinstaller `
  --onedir `
  --noconfirm `
  --clean `
  --name StorageAnalyzer `
  --paths src `
  --collect-data plotly `
  --add-data "templates;templates" `
  --add-data "config.yaml;." `
  src/storage_analyzer/main.py

Write-Host ""
Write-Host "==> 完了: dist\StorageAnalyzer\StorageAnalyzer.exe" -ForegroundColor Green
Write-Host "    実行例: .\dist\StorageAnalyzer\StorageAnalyzer.exe --target `"C:\Users\you`"" -ForegroundColor Green
