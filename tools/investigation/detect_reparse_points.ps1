<#
.SYNOPSIS
  C:\Users 配下の reparse point / junction / symbolic link を読み取り専用で列挙する調査スクリプト。

.DESCRIPTION
  目的: StorageAnalyzer が follow_symlinks=false で「降りない」reparse point が
        どこに何件あるかを把握し、Explorer との差分原因を特定する。
  ループ防止のため reparse point の中には降りない（StorageAnalyzer と同じ方針）。
  ファイル操作は一切行わない（属性の読み取りのみ）。

.PARAMETER Root
  走査の起点。既定 C:\Users。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\investigation\detect_reparse_points.ps1 -Root "C:\Users"

.NOTES
  EnumerateFileSystemInfos は列挙時に Attributes を取得済みのため追加 stat が不要で高速。
  reparse の分類(LinkType/Target)は Get-Item で取得（発見した reparse point のみ）。
#>
param(
  [string]$Root = "C:\Users",
  [int]$SampleN = 40
)

# 注意: PowerShell の変数名は大文字小文字を区別しないため、ループ変数 $dir と衝突しないよう
#       FileAttributes 定数には $FA_ プレフィックスを付ける。
$FA_RP  = [System.IO.FileAttributes]::ReparsePoint
$FA_DIR = [System.IO.FileAttributes]::Directory

$reparse = New-Object System.Collections.Generic.List[object]
$dirCount = 0; $fileCount = 0; $enumErrors = 0

$stack = New-Object System.Collections.Stack
$stack.Push($Root)

while ($stack.Count -gt 0) {
  $dir = $stack.Pop()
  # Get-ChildItem は列挙中の権限エラーを SilentlyContinue で個別にスキップでき、
  # .NET の EnumerateFileSystemInfos のように1件の失敗で全体が止まらないため堅牢。
  $children = Get-ChildItem -LiteralPath $dir -Force -ErrorAction SilentlyContinue
  if (-not $children) { continue }
  foreach ($info in $children) {
    try {
      $attr = $info.Attributes
      $isRep = ($attr -band $FA_RP) -ne 0
      $isDir = ($attr -band $FA_DIR) -ne 0
      if ($isRep) {
        $full = $info.FullName
        $i = $full.IndexOf('\Users\', [System.StringComparison]::OrdinalIgnoreCase)
        $topSeg = if ($i -ge 0) { $full.Substring($i + 7).Split('\')[0] } else { '(other)' }
        $depth = ($full -split '\\').Count
        $reparse.Add([pscustomobject]@{ Path = $full; IsDir = $isDir; Depth = $depth; Top = $topSeg }) | Out-Null
        # reparse point の中には降りない
      } elseif ($isDir) {
        $dirCount++
        $stack.Push($info.FullName)
      } else {
        $fileCount++
      }
    } catch { $enumErrors++ }
  }
}

Write-Output "ROOT=$Root"
Write-Output "通常ディレクトリ数(降下した)=$dirCount  通常ファイル数=$fileCount  列挙エラー=$enumErrors"
Write-Output "reparse point 合計=$($reparse.Count)  (うちディレクトリ=$(($reparse | Where-Object IsDir).Count) / ファイル=$(($reparse | Where-Object { -not $_.IsDir }).Count))"

Write-Output "---- reparse point: 階層深さ別 ----"
$reparse | Group-Object Depth | Sort-Object { [int]$_.Name } | ForEach-Object { "depth={0,-3} count={1}" -f $_.Name, $_.Count }

Write-Output "---- reparse point: C:\Users\<top> 別 ----"
$reparse | Group-Object Top | Sort-Object Count -Descending | Select-Object -First 30 | ForEach-Object { "{0,-30} {1}" -f $_.Name, $_.Count }

Write-Output "---- reparse point: LinkType 分類（Get-Item / 最大 $SampleN 件まで詳細表示） ----"
$detail = $reparse | Select-Object -First $SampleN | ForEach-Object {
  $lt = $null; $tg = $null
  try { $it = Get-Item -LiteralPath $_.Path -Force -ErrorAction Stop; $lt = $it.LinkType; $tg = ($it.Target -join '; ') } catch {}
  [pscustomobject]@{ Path = $_.Path; IsDir = $_.IsDir; LinkType = $lt; Target = $tg }
}
$detail | Format-Table -AutoSize -Wrap
