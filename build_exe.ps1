# StorageAnalyzer を PyInstaller で exe 化し、配布用 zip を作る（Windows / PowerShell 用）
#
# 使い方:
#   1) PowerShell をこのフォルダで開く
#   2) （初回のみ）実行ポリシーで弾かれる場合:
#        powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
#   3) 生成物:
#        dist\StorageAnalyzer\StorageAnalyzer.exe        … 実体（フォルダ配布）
#        dist\StorageAnalyzer_<日時>.zip                 … 同僚へ渡す zip
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

$distDir = Join-Path (Get-Location) "dist\StorageAnalyzer"
$exePath = Join-Path $distDir "StorageAnalyzer.exe"
if (-not (Test-Path $exePath)) {
    throw "ビルドに失敗しました（$exePath が見つかりません）。上のログを確認してください。"
}

# ---- 配布用 zip（StorageAnalyzer フォルダごと固める） ----
Write-Host ""
Write-Host "==> 配布用 zip を作成" -ForegroundColor Cyan
$stamp   = Get-Date -Format "yyyy-MM-dd_HH-mm"
$zipPath = Join-Path (Get-Location) ("dist\StorageAnalyzer_{0}.zip" -f $stamp)
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
# -Path にフォルダ自身を渡すと、zip 展開時に StorageAnalyzer\ フォルダごと得られる。
Compress-Archive -Path $distDir -DestinationPath $zipPath -Force

Write-Host ""
Write-Host "==> 完了" -ForegroundColor Green
Write-Host ("    exe : {0}" -f $exePath) -ForegroundColor Green
Write-Host ("    zip : {0}" -f $zipPath) -ForegroundColor Green
Write-Host ""
Write-Host "    同僚への渡し方:" -ForegroundColor Yellow
Write-Host "      1) 上記 zip をそのまま渡す（中身は StorageAnalyzer フォルダ一式）"
Write-Host "      2) 受け取った人は zip を展開し、StorageAnalyzer フォルダごと任意の場所へ置く"
Write-Host "      3) フォルダ内の StorageAnalyzer.exe をダブルクリックで起動"
Write-Host "      ※ exe 単体を取り出すと動きません（_internal などの同梱物が必要）"
Write-Host ""
Write-Host "    実行例（コマンドから対象を指定する場合）:" -ForegroundColor Yellow
Write-Host ('      .\dist\StorageAnalyzer\StorageAnalyzer.exe --target "C:\Users\you"')
