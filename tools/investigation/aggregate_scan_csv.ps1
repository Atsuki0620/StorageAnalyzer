<#
.SYNOPSIS
  StorageAnalyzer の storage_scan_*.csv を読み取り専用でストリーミング集計する調査スクリプト。

.DESCRIPTION
  目的: Explorer プロパティ値との差分調査のため、スキャン済みデータが
        「どのフォルダ / 拡張子 / カテゴリ / 巨大ファイル」に偏っているかを把握する。
  本スクリプトは CSV を読むだけで、ファイルの作成・変更・削除・移動は一切行わない。

.PARAMETER CsvPath
  集計対象の storage_scan_*.csv のフルパス。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\investigation\aggregate_scan_csv.ps1 `
    -CsvPath "output\storage_scan_20260615_222804.csv"

.NOTES
  CSV は utf-8-sig。path にカンマが含まれるケースは稀なため簡易 split を使う（注意点はレポートに記載）。
#>
param(
  [Parameter(Mandatory = $true)][string]$CsvPath,
  [int]$TopN = 25
)

if (-not (Test-Path -LiteralPath $CsvPath)) {
  Write-Error "CSV が見つかりません: $CsvPath"; exit 1
}

$sr = New-Object System.IO.StreamReader($CsvPath, [System.Text.Encoding]::UTF8)
$null = $sr.ReadLine()  # header: path,name,size_bytes,size_mb,extension,parent,depth,modified_at,created_at,category

$total = [int64]0
$rows = 0
$byTop   = @{}; $byTopCnt   = @{}   # C:\Users\<seg1>
$byUser  = @{}; $byUserCnt  = @{}   # C:\Users\atsuk\<seg2>
$byExt   = @{}; $byExtCnt   = @{}   # extension (col 4)
$byCat   = @{}; $byCatCnt   = @{}   # category  (col 9)
$topFiles = New-Object System.Collections.Generic.List[object]
$minTop = [int64]0   # 上位巨大ファイル保持用の足切り

while (($line = $sr.ReadLine()) -ne $null) {
  $rows++
  $parts = $line.Split(',')
  $p = $parts[0]
  $size = [int64]0; [int64]::TryParse($parts[2], [ref]$size) | Out-Null
  $ext = if ($parts.Length -gt 4) { $parts[4] } else { '' }
  $cat = if ($parts.Length -gt 9) { $parts[$parts.Length - 1] } else { '' }
  $total += $size

  # top-level: C:\Users\<seg1>
  $i1 = $p.IndexOf('\Users\', [System.StringComparison]::OrdinalIgnoreCase)
  $seg1 = if ($i1 -ge 0) { $p.Substring($i1 + 7).Split('\')[0] } else { '(other)' }
  $byTop[$seg1] = [int64]($byTop[$seg1]) + $size; $byTopCnt[$seg1] = [int]($byTopCnt[$seg1]) + 1

  # second-level under atsuk
  $i2 = $p.IndexOf('\Users\atsuk\', [System.StringComparison]::OrdinalIgnoreCase)
  if ($i2 -ge 0) {
    $seg2 = $p.Substring($i2 + 13).Split('\')[0]
    $byUser[$seg2] = [int64]($byUser[$seg2]) + $size; $byUserCnt[$seg2] = [int]($byUserCnt[$seg2]) + 1
  }

  if (-not $ext) { $ext = '(none)' }
  $byExt[$ext] = [int64]($byExt[$ext]) + $size; $byExtCnt[$ext] = [int]($byExtCnt[$ext]) + 1
  if ($cat) { $byCat[$cat] = [int64]($byCat[$cat]) + $size; $byCatCnt[$cat] = [int]($byCatCnt[$cat]) + 1 }

  # 上位巨大ファイル（簡易: しきい値超のみ保持して後でソート）
  if ($size -gt $minTop -or $topFiles.Count -lt 30) {
    $rec = [pscustomobject]@{ Path = $p; Bytes = $size }
    [void]$topFiles.Add($rec)
    if ($topFiles.Count -gt 200) {
      $kept = $topFiles | Sort-Object Bytes -Descending | Select-Object -First 30
      $topFiles = New-Object System.Collections.Generic.List[object]
      foreach ($k in $kept) { [void]$topFiles.Add($k) }
      $minTop = ($kept | Select-Object -Last 1).Bytes
    }
  }
}
$sr.Close()

function Show-Group($title, $sizes, $counts, $n) {
  Write-Output "---- $title ----"
  $sizes.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First $n | ForEach-Object {
    "{0,-30} {1,10} GB  files={2}" -f $_.Key, [math]::Round($_.Value / 1GB, 3), $counts[$_.Key]
  }
}

Write-Output "ROWS=$rows  TOTAL_BYTES=$total  TOTAL_GB=$([math]::Round($total/1GB,3))"
Show-Group "by C:\Users\<seg1>"          $byTop  $byTopCnt  $TopN
Show-Group "by C:\Users\atsuk\<seg2>"    $byUser $byUserCnt $TopN
Show-Group "by extension"                 $byExt  $byExtCnt  $TopN
Show-Group "by category"                  $byCat  $byCatCnt  50
Write-Output "---- top large files ----"
$topFiles | Sort-Object Bytes -Descending | Select-Object -First 20 | ForEach-Object {
  "{0,10} GB  {1}" -f [math]::Round($_.Bytes / 1GB, 3), $_.Path
}
