<#
.SYNOPSIS
  OneDrive サブツリーの論理サイズ・ファイル数・フォルダ数を読み取り専用で実測する調査スクリプト。

.DESCRIPTION
  目的: StorageAnalyzer がスキャンしなかった OneDrive 配下が、Explorer との
        容量差分(約296GB)にどれだけ寄与するかを定量化する。
  - 起点(OneDrive root)は reparse point だが、ここだけは明示的に降下して内部を測る。
  - 内部のネストした reparse point には降りない（ループ防止・二重計上防止）。
  - FileInfo.Length はディレクトリエントリ由来の「論理サイズ」。オンライン専用ファイルでも
    ダウンロードは発生しない（メタデータ列挙のみ）。Explorer の "サイズ" 相当。

.PARAMETER Root
  測定起点。既定 C:\Users\atsuk\OneDrive。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\investigation\measure_onedrive.ps1 -Root "C:\Users\atsuk\OneDrive"
#>
param(
  [string]$Root = "C:\Users\atsuk\OneDrive",
  [int]$MaxDepth = 64   # ループ暴走防止のための深さ上限
)

if (-not (Test-Path -LiteralPath $Root)) { Write-Error "対象が見つかりません: $Root"; exit 1 }

# 注意: PowerShell の変数名は大文字小文字を区別しないため、ループ変数 $dir と衝突しないよう
#       FileAttributes 定数には $FA_ プレフィックスを付ける。
$FA_RP  = [System.IO.FileAttributes]::ReparsePoint
$FA_DIR = [System.IO.FileAttributes]::Directory

# OneDrive Files On-Demand では各フォルダ自体がクラウド reparse point（プレースホルダ）に
# なっているため、reparse でも降下しないと中身を測れない。クラウド配下にループする junction は
# 無いので降下は安全だが、万一に備え深さ上限 $MaxDepth で暴走を防ぐ。
# 列挙はメタデータのみ（.Length は論理サイズ）でファイル本体のダウンロードは発生しない。
$bytes = [int64]0; $files = 0; $folders = 0; $reparseDirsDescended = 0; $enumErrors = 0; $depthCapped = 0
$byChild = @{}   # OneDrive 直下の子フォルダ別 論理バイト

$stack = New-Object System.Collections.Stack
$stack.Push(@($Root, 0))
$rootLen = $Root.Length

while ($stack.Count -gt 0) {
  $frame = $stack.Pop()
  $dir = $frame[0]; $depth = [int]$frame[1]
  # 直下子フォルダ名（集計キー）
  $childKey = $null
  if ($dir.Length -gt $rootLen) {
    $rest = $dir.Substring($rootLen).TrimStart('\')
    $childKey = $rest.Split('\')[0]
  }
  $children = Get-ChildItem -LiteralPath $dir -Force -ErrorAction SilentlyContinue
  if (-not $children) { continue }
  foreach ($info in $children) {
    try {
      $attr = $info.Attributes
      $isDir = ($attr -band $FA_DIR) -ne 0
      $isRep = ($attr -band $FA_RP) -ne 0
      if ($isDir) {
        if ($depth + 1 -gt $MaxDepth) { $depthCapped++; continue }
        if ($isRep) { $reparseDirsDescended++ }
        $folders++; $stack.Push(@($info.FullName, $depth + 1))
      } else {
        $len = [int64]$info.Length
        $files++; $bytes += $len
        $k = if ($childKey) { $childKey } else { '(direct)' }
        $byChild[$k] = [int64]($byChild[$k]) + $len
      }
    } catch { $enumErrors++ }
  }
}

Write-Output "ROOT=$Root"
Write-Output "OneDrive 論理サイズ = $([math]::Round($bytes/1GB,3)) GB  ($bytes bytes)"
Write-Output "ファイル数 = $files   フォルダ数 = $folders   降下した reparse フォルダ = $reparseDirsDescended   深さ上限到達 = $depthCapped   列挙エラー = $enumErrors"
Write-Output "---- OneDrive 直下の子フォルダ別 論理サイズ ----"
$byChild.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 25 | ForEach-Object {
  "{0,-30} {1,10} GB" -f $_.Key, [math]::Round($_.Value / 1GB, 3)
}
