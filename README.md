# StorageAnalyzer

Windows ローカル PC で、**どのフォルダ・ファイル・拡張子・カテゴリがストレージ容量を圧迫しているか**を
診断し、グラフィックリッチな **HTML レポート**を出力する CLI ツールです。

- 📊 Plotly 製のインタラクティブなグラフ（棒・円・ツリーマップ・サンキー）
- 🗂️ フォルダ別 / 拡張子別 / カテゴリ別 の容量内訳
- 🧾 巨大ファイル・古い巨大ファイル・最近更新された巨大ファイルの一覧
- 🛡️ **完全に読み取り専用**（削除・移動・変更は一切しません）
- 🚀 大量ファイルでも落ちにくいストリーミング設計＋リアルタイム進捗表示

---

## ⚠️ 注意事項（必ずお読みください）

- **読み取り専用です。** ファイルの削除・移動・変更・クリーンアップ機能は**ありません**。
- 出力されるのは「診断レポート（HTML）」と「スキャン結果（CSV）」だけです。
- **Windows のシステムフォルダ**（`C:\Windows` など）の結果は慎重に扱ってください。
  システムファイルは見た目が大きくても削除してはいけないものが多数あります。
- 既定では `node_modules` / `.venv` / `AppData` / `Downloads` は**容量分析の対象**に含めます
  （これらが容量を食う主要因のため）。除外したい場合は `config.yaml` で設定できます。
- ジャンクション / シンボリックリンク / reparse point は既定では**辿りません**（循環・二重計上の防止）。

---

## セットアップ

### 前提
- Windows（開発・テストは Linux/Python 3.11 でも動作確認済み。コードはクロスプラットフォーム）
- Python 3.11 以上

### インストール
```powershell
# 仮想環境（任意）
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 依存パッケージ
python -m pip install -r requirements.txt

# パッケージとして入れておくと `python -m storage_analyzer` がどこからでも動く
python -m pip install -e .
```

---

## 実行方法（Python）

```powershell
# 対象フォルダを指定して実行
python -m storage_analyzer --target "C:\Users\you"

# 引数なしで起動すると、対象フォルダを対話入力できます
python -m storage_analyzer
```

`pip install -e .` をしていない場合は、`src` を import パスに追加して実行します。

```powershell
# PowerShell
$env:PYTHONPATH = "src"; python -m storage_analyzer --target "C:\Users\you"
```
```bash
# macOS / Linux
PYTHONPATH=src python -m storage_analyzer --target "."
```

### 主なオプション
| オプション | 説明 |
|---|---|
| `--target <path>` | スキャン対象のドライブ/フォルダ。未指定なら対話入力。 |
| `--config <path>` | 使用する `config.yaml` のパス。 |
| `--output-dir <dir>` | 出力先ディレクトリ（既定 `output`）。exe を書き込み不可の場所に置いた場合に有効。 |
| `--no-open` | 生成した HTML を自動で開かない。 |
| `--no-precount` | 事前カウントを省略。`%` 表示はなくなりますが総走査は最短になります。 |
| `--cdn` | plotly.js を CDN 参照にして HTML を軽量化（**インターネット接続が必要**）。 |

### 進捗表示について
スキャンは対象が大きいと長時間になります。本ツールは**どのステップを実行中か**を常に表示し、
スキャン中は**進捗バー（残り時間つき）**を表示するので、「固まった/エラー」と「正常稼働」を区別できます。

```
[1/6] 設定読み込み・対象検証 ...
[2/6] 対象を数えています ...
数えています: 234,567 files [00:12, 19,500 files/s]
[3/6] スキャン中 ...
スキャン中: 45%|██████████        | 105,000/234,567 [01:20<01:35, 1,350 files/s]
[4/6] 集計中 ...
[5/6] グラフ生成中 ...
[6/6] レポート出力中 ...
```

---

## 出力

実行すると `output/` 配下に 3 ファイルが生成されます（`YYYYMMDD_HHMMSS` は実行時刻）。

```
output/
  storage_scan_YYYYMMDD_HHMMSS.csv     # 全ファイルの一覧（path, size, category 等）
  storage_errors_YYYYMMDD_HHMMSS.csv   # スキップ/エラーログ（path, error_type, error_message）
  storage_report_YYYYMMDD_HHMMSS.html  # 可視化レポート（ブラウザ単独で閲覧可）
```

HTML 生成後、可能なら既定ブラウザで自動的に開きます。

---

## HTML レポートの見方

- **KPI カード**: スキャン対象・合計容量・ファイル数・フォルダ数・スキップ件数・実行時間・作成日時。
- **フォルダ別容量 Top N（横棒）**: 子孫を含めて容量の大きいフォルダ。バーにカーソルを合わせるとフルパスと容量。
- **拡張子別容量 Top N（横棒）**: どの拡張子が容量を食っているか。
- **カテゴリ別容量（円）**: Video / Image / Archive / Development / Cache … などの内訳。
- **更新年月別容量（棒）**: いつ頃のファイルが容量を占めているか。
- **フォルダ階層ツリーマップ**: フォルダの大きさを面積で表現（子孫含む）。クリックでドリルダウン。
- **サンキー図**: 容量フロー `対象 → 第1階層 → 第2階層 → カテゴリ`。上位に絞って表示。
- **テーブル**: 巨大ファイル Top 100 / 古い巨大ファイル / 最近更新された巨大ファイル / スキップログ。

> ヒント: グラフはインタラクティブです。ズーム・パン・凡例クリックでの絞り込みができます。

---

## exe 化（PyInstaller）

安定性重視で `--onedir`（フォルダ配布）を使います。

```powershell
# Windows / PowerShell
.\build_exe.ps1
# 実行ポリシーで弾かれる場合:
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

生成物:
```
dist\StorageAnalyzer\StorageAnalyzer.exe
```

実行例:
```powershell
.\dist\StorageAnalyzer\StorageAnalyzer.exe --target "C:\Users\you"
```

`build_exe.ps1` は次の点に注意しています。
- `--add-data` の区切りは Windows では `;`（`"templates;templates"`、`"config.yaml;."`）。
- `--paths src` で `src` レイアウトのパッケージを解決。
- `--collect-data plotly` で **インライン埋め込み用の plotly.js** を確実に同梱。

---

## よくあるエラー

### PermissionError（アクセス拒否）
権限のないフォルダ/ファイルは**スキップして処理を継続**します。止まりません。
スキップ内容は `storage_errors_*.csv` と HTML の「スキップ / エラーログ」に記録されます。
`C:` 直下など保護領域を含めると大量に出ることがあります（正常動作です）。

### 文字化け（CSV を Excel で開くと文字化けする）
CSV は **UTF-8 (BOM 付き / utf-8-sig)** で出力しているため、通常は Excel でも正しく表示されます。
それでも化ける場合は、Excel の「データ → テキスト/CSV から」で UTF-8 を指定して取り込んでください。

### スキャンが遅い
- 対象を絞る（ドライブ全体ではなく `C:\Users\you` など）。
- `config.yaml` の `exclude_dir_names` / `exclude_paths` で不要領域を除外する。
- `--no-precount` で事前カウントを省略すると総時間は短くなります（ただし `%` 表示はなくなります）。
- ネットワークドライブや外付け HDD は I/O が遅く時間がかかります。

### PyInstaller でテンプレート/設定が見つからない（FileNotFoundError: report.html.j2 等）
`--add-data` の同梱漏れか、区切り文字の誤りが原因です。Windows では区切りは `;` です。
本リポジトリの `build_exe.ps1` を使えば `templates` と `config.yaml` を正しく同梱します。
凍結時はリソースを `sys._MEIPASS` から解決する実装（`utils.resource_path`）になっています。

### グラフが表示されない / 真っ白
`--cdn` を付けて生成した HTML は**インターネット接続が必要**です。オフラインで見るなら
`--cdn` を外して（既定の完全インライン埋め込みで）生成してください。

---

## 仕組み（アーキテクチャ）

| モジュール | 役割 |
|---|---|
| `scanner.py` | `os.scandir` による反復走査・事前カウント・エラー処理（処理を止めない） |
| `aggregator.py` | ストリーミング集計（dict 合計 + 上限付き min-heap）。pandas は最後の整形のみ |
| `classifier.py` | 拡張子 + パスからカテゴリ判定 |
| `charts.py` | Plotly 図（棒/円/ツリーマップ/サンキー）の生成 |
| `report.py` | CSV ストリーミング書き込み + Jinja2 で HTML レンダリング |
| `config.py` / `utils.py` | 設定の読み込み・パス/サイズ/時刻の補助 |
| `main.py` | CLI とステージ進捗のオーケストレーション |

メモリ安全のため、全ファイルのレコードをメモリに溜め込まず、CSV へ逐次書き出しつつ集計します。
数百万ファイル規模でも完走することを目標にしています。

---

## ライセンス / 免責
個人利用を想定したツールです。表示される容量や分類はあくまで診断の目安です。
**ファイル操作は行いません**が、結果（特にシステム領域）に基づく操作はご自身の責任で慎重に行ってください。
