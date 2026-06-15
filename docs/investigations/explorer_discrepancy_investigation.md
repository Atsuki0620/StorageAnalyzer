# StorageAnalyzer と Explorer プロパティ差分調査

> 本ドキュメントは **読み取り専用の原因調査レポート**です。本実装の修正・UI改善・出力構成変更などは一切行っていません。
> 調査に使った一時スクリプトは `tools/investigation/` 配下にあり、実行コマンドと結果は本文に記録しています。

---

## 1. 調査目的

`C:\Users` をスキャンした際、**StorageAnalyzer の集計値**と **Windows エクスプローラーのプロパティ値**が大きく食い違った。
その原因を、コード・既存出力・実測データから調査し、初心者にも分かるように整理することが目的。

今回確認した差分値（タスク提示値）:

| 項目 | StorageAnalyzer | Windows Explorer プロパティ |
|---|---|---|
| 合計容量 | **70.52 GB** | **367 GB** |
| ファイル数 | **757,600** | **1,314,519** |
| フォルダ数 | **110,751** | **176,498** |

※ StorageAnalyzer 側の値は、出力ファイル `output/storage_scan_20260615_222804.csv`（757,600 行）および
   同時刻の HTML レポートと一致することを確認済み（[4章](#4-既存出力ファイルの分析)）。

---

## 2. 結論サマリ

調査の結果、**差分の主因はほぼ特定できた**。最有力原因は「**OneDrive フォルダ（クラウド reparse point）をスキャンしていないこと**」である。

| 確度 | 原因候補 | 根拠 | 状態 |
|---|---|---|---|
| **確定（高）** | **OneDrive のクラウド reparse point を辿らない設計** → `C:\Users\atsuk\OneDrive` 配下（約 **306.7 GB / 766k ファイル**）が丸ごと未集計 | reparse tag `0x9000701a`(=クラウド) を確認。スキャンCSVに OneDrive 配下が **0 件**。OneDrive 実測 306.7GB と「スキャン済み 70.52GB」の合計 **377GB ≒ Explorer 367GB**（誤差約3%） | ✅ 確認済み |
| 低 | legacy junction（`Application Data` 等）を辿らない設計 + Explorer 側の二重計上 | junction 10種を確認。ただし容量はほぼ完全に OneDrive だけで説明できるため、**今回の差分への寄与は小さい**と判明 | ✅ 確認済み（寄与は小） |
| 低 | PermissionError によるスキップ | エラーCSVは **3件のみ**（容量極小フォルダ）。差分の主因ではない | ✅ 確認済み |
| 低 | 表示単位（GB の base-1024）/ 丸め誤差 | 差が約5倍と桁違いに大きく、丸めでは説明不能 | ✅ 確認済み |
| 参考（未確認） | Size と Size on disk の差（NTFS圧縮/スパース/オンライン専用の実バイト） | 今回は「論理サイズ」同士の比較で差分が説明できたため、主因ではない。ただし厳密検証は未実施 | ⚠️ 未確認 |

**一言でいうと**: StorageAnalyzer は「OneDrive フォルダの中身を一切数えていない」。Explorer はそれを数えている。
これがおよそ 300GB・約76万ファイルの差をほぼすべて生んでいる。

---

## 3. 現在の StorageAnalyzer の集計仕様（コードを読んで確認）

### 3.1 合計容量の算出方法
- `ScanStats.total_bytes` に、各ファイルの `entry.stat(follow_symlinks=follow).st_size` を加算（`scanner.py` `_process_entry`、`self.stats.total_bytes += size`）。
- `Aggregator.add()` でも `total_bytes` を別途加算（`aggregator.py`）。両者は一致する。
- **`st_size` は「論理サイズ（ファイルの中身の見かけ上のバイト数）」**であり、`size on disk`（ディスク上の実割り当て）ではない。
- **ディレクトリ自体のサイズは加算していない**（ディレクトリは `is_dir` 分岐で `folder_count++` のみ、容量には足さない）。これは Explorer も同じ挙動なので差分要因ではない。

### 3.2 ファイル数 / フォルダ数の算出方法
- ファイル数 `file_count`: ファイルエントリを処理するたびに +1（`_process_entry` の末尾）。
- フォルダ数 `folder_count`: **降下すると決めたディレクトリ**でのみ +1（`if self._should_descend(...): self.stats.folder_count += 1`）。
  - つまり「除外されたフォルダ」「reparse point」は **フォルダ数にカウントされない**。

### 3.3 スキップ処理
- ディレクトリ単位・エントリ単位で `try/except` を張り、`PermissionError` / `FileNotFoundError` / `OSError` などが出ても **処理を止めずスキップ**し、`storage_errors_*.csv` に記録（`scanner.py` `_record_skip`）。

### 3.4 除外処理（`config.yaml`）
```yaml
exclude_paths:
  - "C:\\System Volume Information"
  - "C:\\$Recycle.Bin"
  - "C:\\Windows\\WinSxS"
exclude_dir_names:
  - ".git"
follow_symlinks: false
```
- `C:\Users` スキャンに効くのは実質 **`.git` ディレクトリ名除外のみ**（`exclude_paths` はすべて `C:\Users` 配下ではない）。
- **`node_modules` / `.venv` / `AppData` / `Downloads` は除外していない**（実際 AppData は 55GB スキャン済み。[4章](#4-既存出力ファイルの分析)）。
- `.git` 除外の影響は容量的には軽微（巨大なのは別の領域）。ただしフォルダ/ファイル件数には多少効く。

### 3.5 symlink / reparse point の扱い（差分の核心）
`scanner.py` `_should_descend()`：
```python
def _should_descend(self, entry, follow):
    if not follow:                       # follow_symlinks=false なので常にこの枝
        if entry.is_symlink():           # symlink は降りない
            return False
        st = entry.stat(follow_symlinks=False)
        if is_reparse_point(st, entry):  # reparse point は降りない ★
            return False
    ...
```
`utils.py` `is_reparse_point()`：
```python
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
attrs = getattr(st, "st_file_attributes", None)
return bool(attrs & _FILE_ATTRIBUTE_REPARSE_POINT)   # REPARSE ビットで判定
```
- **ディレクトリが reparse point なら、その配下には一切降りない**（中身を数えない）。
- ここでいう reparse point には **junction（マウントポイント）/ symbolic link / OneDrive のクラウドプレースホルダ** がすべて含まれる。
- 一方 **ファイルの reparse 状態は見ていない**：ファイルは `_process_entry` のファイル枝で常に `st_size` を計上する（symlink ファイルはリンク自身のサイズ）。
  - → オンライン専用「ファイル」は、もし親フォルダを降りてさえいれば論理サイズで数えられるはずだった。しかし実際は **親フォルダ（OneDrive ルート＝クラウド reparse）で止まる**ため、配下のオンライン専用ファイルもまとめて数えられていない。

---

## 4. 既存出力ファイルの分析

`output/` に既存の `C:\Users` スキャン結果が存在した（**再スキャンは不要**だった）。

| ファイル | 行数 | 備考 |
|---|---|---|
| `storage_scan_20260615_222804.csv` | 757,600 データ行 | 提示値 757,600 と一致 → **これが当該スキャン** |
| `storage_errors_20260615_222804.csv` | **3 件** | エラーは極少 |
| `storage_scan_20260615_221803.csv` | 757,562 行 | 直前の別スキャン（誤差は時間差） |

### 4.1 エラーCSVの集計（`storage_errors_20260615_222804.csv`）
- 件数: **3 件**、すべて `PermissionError`。
- パス:
  - `C:\Users\CodexSandboxOnline`
  - `C:\Users\CodexSandboxOffline`
  - `C:\Users\atsuk\AppData\Local\ElevatedDiagnostics`
- → いずれも容量の小さい特殊フォルダ。**約 300GB の差分を説明できる規模ではない**。PermissionError は主因ではないと確定。

### 4.2 スキャンCSVの集計（`aggregate_scan_csv.ps1` の結果）

**合計**: 757,600 ファイル / **70.517 GB**（提示値と一致）。

**`C:\Users\<トップ>` 別**:
| トップ | 容量 | ファイル数 |
|---|---|---|
| atsuk | 70.229 GB | 757,503 |
| Public | 0.286 GB | 33 |
| Default | 0.003 GB | 62 |

→ 容量はほぼ `atsuk` のみ。**`OneDrive` というトップ項目は存在しない**（後述の核心）。

**`C:\Users\atsuk\<2階層>` 別**（上位）:
| フォルダ | 容量 | ファイル数 |
|---|---|---|
| AppData | 55.042 GB | 373,448 |
| anaconda3 | 10.736 GB | 355,736 |
| .vscode | 2.056 GB | 15,933 |
| Music | 0.801 GB | 806 |
| .cache | 0.465 GB | 17 |
| …（OneDrive は出現しない） | – | – |

**カテゴリ別**（上位）: Other 29.3GB / Development 12.2GB / Application 12.0GB / Cache 6.1GB / Archive 3.9GB …
**拡張子別**（上位）: (none) 15.7GB / .vhdx 9.6GB / .exe 8.0GB / .dll 6.1GB / .zst 3.7GB …
**上位巨大ファイル**: `AppData\Roaming\Claude\vm_bundles\...\rootfs.vhdx`(8.8GB) など。OneDrive の "中身" は一切現れず、出てくる OneDrive 関連は `AppData\Local\Microsoft\OneDrive\...`（= OneDrive アプリのローカル DB）のみ。

> 重要: スキャン済みデータには **OneDrive の同期コンテンツが 1 件も含まれていない**。含まれるのは OneDrive アプリの内部 DB だけ。

---

## 5. Explorer との差分整理

| 項目 | StorageAnalyzer | Explorer | 差分(Explorer−SA) | 差分率(対 Explorer) |
|---|---|---|---|---|
| 合計容量 | 70.52 GB | 367 GB | **+296.48 GB** | **約 80.8%** が未集計 |
| ファイル数 | 757,600 | 1,314,519 | **+556,919** | 約 42.4% が未集計 |
| フォルダ数 | 110,751 | 176,498 | **+65,747** | 約 37.3% が未集計 |

### 5.1 差分量・差分率から見える仮説
- **容量差(約296GB)はファイル数差(約56万件)に比べて桁違いに大きい** → 「少数の巨大ファイル」か「巨大なサブツリーの丸ごと欠落」が疑わしい。
- フォルダもファイルも約4割欠落 → 単一の大きなサブツリーが丸ごと抜けている可能性が高い。
- → **OneDrive サブツリーの丸ごと欠落**という仮説に一致（次章で実測により確定）。

---

## 6. 原因候補の検証

### 原因候補 A: OneDrive のクラウド reparse point を辿らない設計 ★最有力（確定）

- **内容**: `C:\Users\atsuk\OneDrive` 自体が「クラウド reparse point（OneDrive Files On-Demand のプレースホルダ）」。
  StorageAnalyzer は reparse point ディレクトリには降りない設計（[3.5](#35-symlink--reparse-point-の扱い差分の核心)）のため、**OneDrive 配下を丸ごと未集計**。
- **根拠（確認済み）**:
  1. `C:\Users\atsuk\OneDrive` の属性に `ReparsePoint` が立っており、`fsutil reparsepoint query` の **Reparse Tag = `0x9000701a`**（`0x9000xxxx` 帯 = `IO_REPARSE_TAG_CLOUD`、OneDrive クラウド）。
  2. スキャンCSV内で `\Users\atsuk\OneDrive` 配下のファイルは **0 件**（このリポジトリ自身が OneDrive 配下にあるのに未集計）。
  3. OneDrive 配下の実測（`measure_onedrive.ps1`）= **306.709 GB / 765,984 ファイル / 91,925 フォルダ**。
     - OneDrive 直下では `ドキュメント` が **306.327 GB** とほぼ全部。
     - なお OneDrive の各トップフォルダ自体もクラウド reparse（ネストした reparse を 63,838 個降下して測定）。
  4. **容量の突き合わせ**: スキャン済み 70.52 GB + OneDrive 実測 306.71 GB = **377.2 GB** ≒ Explorer **367 GB**（誤差約3%。OneDrive が活発に変化する領域である点・測定時刻差・丸めで説明可能）。
- **今回の差分への影響度**: **最大（容量差のほぼ全部）**。
- **状態**: ✅ 確認済み。
- **追加検証方法**:
  - `tools/investigation/measure_onedrive.ps1 -Root "C:\Users\atsuk\OneDrive"` を再実行して件数・容量を再確認。
  - 将来的に `follow_symlinks: true`（または「クラウド reparse のみ降りる」オプション）で再スキャンし、合計が Explorer に近づくか確認（※今回は実装しない）。

### 原因候補 B: legacy junction / reparse point を辿らない設計（寄与は小）

- **内容**: `C:\Users\atsuk` 直下に Windows 互換用の **junction が多数**（`Application Data`→Roaming、`Local Settings`→Local、`My Documents`→Documents、`My Music/Pictures/Videos` 等）。
  これらは reparse point なので StorageAnalyzer は降りない。これらは元々プロファイル内の実体へ**ループバック**するため、辿らないのは二重計上防止として正しい。
- **根拠（確認済み）**:
  - `detect_reparse_points.ps1` で `C:\Users` 全体に reparse point **120 個**（ディレクトリ 43 / ファイル 77）を検出。
  - 代表 reparse tag: junction = `0xa0000003`（MOUNT_POINT）、`All Users` = `0xa000000c`（SYMLINK → `C:\ProgramData`）。
  - 容量はほぼ OneDrive だけで Explorer と一致するため、**junction 由来の差分は容量的にはほぼゼロ**。
- **Explorer 側の二重計上について（推測）**: Explorer がプロパティ集計で junction を辿ると AppData/Documents を二重計上し得る、という一般論がある。**しかし今回は逆に「SA+OneDrive の件数(約152万) > Explorer 件数(約131万)」**であり、Explorer が大きく二重計上している兆候はない。→ この仮説は**今回の差分の主因ではない**と判断。
- **今回の差分への影響度**: **小**。
- **状態**: ✅ 確認済み（設計・件数）。Explorer の内部集計仕様そのものは ⚠️ 未確認。
- **追加検証方法**: `Application Data` など個別 junction のターゲットと、Explorer プロパティでの増減を手動比較。

### 原因候補 C: OneDrive の特殊ファイル（オンライン専用ファイル）

- **内容**: OneDrive のオンライン専用ファイルは「論理サイズ＝フルサイズ / ディスク上＝ほぼ0」。
  仮に StorageAnalyzer が OneDrive 配下を降りていたとしても、**ファイルの reparse は無視して `st_size`（論理サイズ）を計上**するため（[3.5](#35-symlink--reparse-point-の扱い差分の核心)）、本来は論理サイズで数えられる想定だった。
- **根拠**: 今回はそもそも OneDrive ルート（クラウド reparse ディレクトリ）で止まるため、**配下のオンライン専用ファイルもまとめて未集計**になっている。つまり「特殊ファイルのサイズ定義の差」ではなく「**親フォルダごと降りていない**」ことが効いている。
- **今回の差分への影響度**: 単独要因としては不明だが、**原因 A に内包**される（OneDrive を降りれば解消する話）。
- **状態**: ⚠️ 一部未確認（降下した場合に論理/実バイトどちらで一致するかは未検証）。
- **追加検証方法**: OneDrive を降りる設定で再スキャンし、論理サイズ合計と Explorer "サイズ" が一致するか確認。

### 原因候補 D: Explorer と Python のサイズ定義差（Size vs Size on disk・単位）

- **内容**: ① Explorer "サイズ" は論理サイズ、"ディスク上のサイズ" は実割り当て。② 単位は両者とも base-1024 だが表記は "GB"。③ NTFS 圧縮 / スパースファイルでは論理と実バイトがずれる。
- **根拠（確認済み）**: 今回の差は約5倍と桁違いで、単位・丸め・圧縮では説明不能。容量は「論理サイズ同士」で 377GB≒367GB と一致したため、**サイズ定義差は主因ではない**。
- **今回の差分への影響度**: **低**。
- **状態**: ⚠️ 厳密には未確認（"ディスク上のサイズ" との比較は未実施）。
- **追加検証方法**: 代表ファイルで `st_size` と `fsutil file queryallocated`（実割り当て）を比較。

---

## 7. 追加検証の提案

差分原因を「確定」に近づける／再発を切り分けるための具体策。**いずれも今回は実装・実行していない提案**（OneDrive 実測のみ実施済み）。

1. **通常実行 と 管理者実行 の比較**（管理者権限が差分要因か切り分け）
   1. 通常ユーザーで `python -m storage_analyzer --target "C:\Users"` を実行 → `storage_scan_*.csv` / `storage_errors_*.csv` を取得。
   2. 「管理者として実行」で同じ `C:\Users` を実行。
   3. 両者の **合計容量・ファイル数・フォルダ数・エラー件数・error_type 別件数** を比較。
   - 今回の結果（PermissionError 3件のみ）から、**管理者権限は今回の差分の主因ではない見込み**だが、`CodexSandbox*` 等が管理者で読めるようになるかは確認価値あり。
2. **reparse point 検出ログの追加**: スキャン時に「降りなかった reparse point の一覧（パス・種別・tag）」をCSV出力できるようにする。
3. **スキップ理由別の集計**: `error_type` 別・トップ階層別の件数を集計し、HTMLに表示。
4. **`C:\Users` 直下フォルダ別比較**: SA の CSV 集計値と、Explorer 各フォルダ プロパティ値を 1:1 で突き合わせるメモを作る。
5. **Explorer 比較用メモテンプレート**（下記）を残し、毎回同じ観点で記録する。

```text
[Explorer vs StorageAnalyzer 比較メモ]
対象フォルダ:
取得日時(Explorer):              取得日時(SA scan):
Explorer  合計容量 / ファイル数 / フォルダ数:
SA        合計容量 / ファイル数 / フォルダ数:
差分(容量/件数):
このフォルダは reparse/junction/OneDrive か: (はい/いいえ/種別)
SAのエラーCSV該当件数:
メモ:
```

---

## 8. 今後の改修候補（※今回は実装しない）

差分を「ツール側で説明・可視化」するための将来機能の候補。実装は別タスク。

- **スキャンカバレッジ・セクション**: 「降りなかった reparse/junction/OneDrive フォルダ件数」「スキップ件数」をレポート冒頭に明示し、"Explorer と一致しない理由" を自動表示。
- **reparse / junction / symlink 検出一覧**: 種別（cloud / mount point / symlink）と tag 付きで一覧化。
- **クラウド(OneDrive)領域の扱いオプション**: 「降りない（既定）/ 論理サイズで降りる / 別集計する」を選べるようにする。
- **管理者実行フラグの記録**: 実行時に管理者かどうかを KPI に記録（差分調査の再現性向上）。
- **通常実行 と 管理者実行 の比較レポート**: 2 つの出力CSVを読み込んで差分表を生成。
- **Explorer 差分説明パネル**: 「Explorer の値が大きい主因は OneDrive クラウド領域」等を定型文で表示。

---

## 9. 調査で実行したコマンド

すべて**読み取り専用**（属性・サイズの読み取り、CSV の読み取りのみ）。`<repo>` は本リポジトリのルート。

### 既存出力の確認・エラー/件数集計
```powershell
# 出力ファイル一覧とサイズ
Get-ChildItem "<repo>\output" -File | Select-Object Name,Length,LastWriteTime

# 各CSVの行数（どれが C:\Users スキャンかの特定）
Get-Content "<repo>\output\storage_scan_20260615_222804.csv" | Measure-Object -Line   # 757,601 行(=757,600+ヘッダ)
Get-Content "<repo>\output\storage_errors_20260615_222804.csv" | Measure-Object -Line # 4 行(=3件+ヘッダ)
```

### スキャンCSVの集計（一時スクリプト）
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<repo>\tools\investigation\aggregate_scan_csv.ps1" `
  -CsvPath "<repo>\output\storage_scan_20260615_222804.csv"
# 結果: ROWS=757600 TOTAL_GB=70.517 / atsuk 70.229GB / AppData 55.04GB / OneDrive 配下=0件
```

### OneDrive がクラウド reparse point である確認
```powershell
Get-Item "C:\Users\atsuk\OneDrive" -Force | Select-Object Name,Attributes   # → ... ReparsePoint
cmd /c 'fsutil reparsepoint query "C:\Users\atsuk\OneDrive"'                 # → Reparse Tag Value : 0x9000701a (cloud)
# 主要 reparse の tag 確認
#   Application Data/Local Settings/My Documents/My Music/Pictures/Videos → 0xa0000003 (junction)
#   All Users → 0xa000000c (symlink → C:\ProgramData)
```

### C:\Users 全体の reparse / junction / symlink 列挙（一時スクリプト）
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<repo>\tools\investigation\detect_reparse_points.ps1" -Root "C:\Users"
# 結果: 通常dir=111,089 / 通常file=757,874 / reparse=120 (dir43 + file77) / enumErrors=0
#       トップ別 reparse: atsuk=97, Default=18, Public=3, All Users=1, Default User=1
```

### OneDrive サブツリーの論理サイズ実測（一時スクリプト）
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<repo>\tools\investigation\measure_onedrive.ps1" -Root "C:\Users\atsuk\OneDrive"
# 結果: 論理サイズ=306.709 GB / files=765,984 / folders=91,925 / 降下したreparseフォルダ=63,838
#       直下: ドキュメント 306.327GB / デスクトップ 0.382GB
```

> 補足（スクリプト実行上の注意・調査中に判明）:
> - Windows PowerShell 5.1 は **BOM 無し UTF-8 の .ps1 を cp932 と誤認**してパースエラーになるため、スクリプトは **UTF-8 (BOM 付き)** で保存している。
> - PowerShell の変数名は**大文字小文字を区別しない**ため、定数 `$DIR` がループ変数 `$dir` と衝突する不具合があり、`$FA_DIR` 等にリネーム済み。
> - 走査は **reparse point の中には降りない**（`detect_reparse_points.ps1`）か、**深さ上限付きで降りる**（`measure_onedrive.ps1`、クラウド配下計測のため）方式で、ループ暴走を防いでいる。

---

## 10. 未解決事項

- **Explorer プロパティの正確な内部集計仕様は未確認**（junction を辿るか、オンライン専用を論理で数えるか、`C:\Users\All Users`→ProgramData を含めるか等）。今回は容量の突き合わせで主因が説明できたため深追いしていない。
- **件数の過剰一致**: SA(757,600) + OneDrive(765,984) = 1,523,584 件は Explorer(1,314,519) を約 21 万件**上回る**。考えられる理由（いずれも**推測**）:
  - Explorer プロパティ取得時刻と本調査の実測時刻の**差**（OneDrive・特に `ドキュメント` 配下は Claude セッション等で活発に増減）。
  - `measure_onedrive.ps1` は `.git` を除外していない（SA は `.git` 除外）。Explorer の数え方との差。
  - → 容量は約3%で一致しているため、**主因の結論は変わらない**が、件数の厳密一致には時刻を揃えた再計測が必要。
- **管理者実行との差**は未測定（[7章](#7-追加検証の提案)の手順で別途検証可能）。今回のエラーは3件のみで主因ではない見込み。
- **Size on disk（実割り当て）/ NTFS圧縮 / スパース**の厳密比較は未実施（[原因候補D](#原因候補-d-explorer-と-python-のサイズ定義差size-vs-size-on-disk単位)）。論理サイズ比較で差分が説明できたため優先度低。

---

### 付録: 一時調査スクリプト（`tools/investigation/`、すべて読み取り専用）
| スクリプト | 役割 |
|---|---|
| `aggregate_scan_csv.ps1` | `storage_scan_*.csv` をストリーミング集計（トップ別/ユーザー別/拡張子別/カテゴリ別/巨大ファイル） |
| `detect_reparse_points.ps1` | `C:\Users` 全体の reparse/junction/symlink を列挙（中には降りずループ回避） |
| `measure_onedrive.ps1` | OneDrive サブツリーの論理サイズ・件数を実測（クラウド配下を深さ上限付きで降下、本体DLなし） |
