# StorageAnalyzer

Windows ローカル PC で、**どのフォルダ・ファイル・拡張子・カテゴリがストレージ容量を圧迫しているか**を
診断し、ダッシュボード風の **HTML レポート**を出力する CLI ツールです。

- Plotly 製のインタラクティブなグラフ（棒・円・ツリーマップ・サンキー）
- カテゴリや階層を識別しやすいカラフルな定性配色
- フォルダ別 / 拡張子別 / カテゴリ別 の容量内訳
- 第2階層 Top5 をタブで深掘り（配下のフォルダ・拡張子・カテゴリ・巨大ファイル）
- 巨大ファイル・古い巨大ファイル・最近更新された巨大ファイルの一覧
- **完全に読み取り専用**（削除・移動・変更は一切しません）
- 大量ファイルでも落ちにくいストリーミング設計＋リアルタイム進捗表示

---

## ⚠️ 注意事項（必ずお読みください）

- **読み取り専用です。** ファイルの削除・移動・変更・クリーンアップ機能は**ありません**。
- 出力されるのは「診断レポート（HTML）」と「スキャン結果（CSV）」だけです。
- **Windows のシステムフォルダ**（`C:\Windows` など）の結果は慎重に扱ってください。
  システムファイルは見た目が大きくても削除してはいけないものが多数あります。
- 既定では `node_modules` / `.venv` / `AppData` / `Downloads` は**容量分析の対象**に含めます
  （これらが容量を食う主要因のため）。除外したい場合は `config.yaml` で設定できます。
- シンボリックリンク / junction / mount point は既定では**辿りません**（循環・二重計上の防止）。
- OneDrive の Cloud 系 reparse point は、安全に識別できる場合のみメタデータ走査として辿ります。
  ファイル本文は開かず、クラウドファイルの強制ダウンロードは行いません。設定で無効化できます。
- Explorer のプロパティ値との完全一致は保証しません。ただし、OneDrive 配下の過小集計は改善する可能性があります。

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

実行ごとに `output/` の下へ **専用フォルダ** が作られ、その中に成果物がまとまります。
フォルダ名は `YYYY-MM-DD_HH-mm_<対象を安全な名前に変換>`（例: `C:\Users\atsuk` → `C_Users_atsuk`）。

```
output/
  2026-06-15_21-30_C_Users_atsuk/
    report.html      # 可視化レポート（ブラウザ単独で閲覧可）
    scan.csv         # 全ファイルの一覧（path, size, category 等）
    errors.csv       # スキップ/エラーログ（path, error_type, error_message）
    manifest.json    # 実行情報（対象・合計容量・件数・主要設定など）
```

- ファイル名は固定（`report.html` / `scan.csv` / `errors.csv` / `manifest.json`）なので、
  フォルダを開けば中身がすぐ分かります。
- `--output-dir` は「実行フォルダを作る親フォルダ」として扱われます
  （例: `--output-dir C:\Reports` → `C:\Reports\2026-06-15_21-30_C_Users_atsuk\`）。
- `manifest.json` には `app_name` / `version` / `generated_at` / `target_*` / `total_bytes` /
  `file_count` / `folder_count` / `skip_count` / `config_summary` / `reparse_summary` などが入ります。

HTML 生成後、可能なら既定ブラウザで自動的に開きます。

---

## HTML レポートの見方

レポートは 1 ページのダッシュボードで、上部の固定ナビ（概要・構造・深掘り・巨大ファイル・ログ）で
各セクションへ移動できます。

- **KPI カード**: 合計容量・ファイル数・フォルダ数・スキップ件数・実行時間。
- **概要**: フォルダ別容量 / 拡張子別容量 / カテゴリ別容量（円）/ 更新年月別容量 と、カテゴリ別内訳テーブル。
  - バーやセクターにカーソルを合わせるとフルパスや容量が表示されます。
  - グラフは青系だけに偏らない定性配色を使い、カテゴリ・拡張子・上位フォルダを識別しやすくしています。
- **構造**: フォルダ階層ツリーマップ（面積で容量を表現）、フォルダ階層アイシクル
  （左→右に階層が伸びる・サンキーより入れ子が読み取りやすい）、容量フローのサンキー図
  `対象 → 第1階層 → 第2階層 → カテゴリ`。ツリーマップ／アイシクルはクリックで深い階層まで
  ドリルダウンできます。色は容量の大小だけでなく、第1階層フォルダやカテゴリの識別にも使います。
- **深掘り（Deep Dive）**: スキャン対象から見た **第2階層までの容量 Top5** をタブで切り替え。
  各タブで対象フォルダのフルパス・合計容量・全体に対する割合・ファイル数・フォルダ数に加え、
  配下のツリーマップ、フォルダ別容量（さらに数階層）、拡張子別、カテゴリ別、巨大ファイルを確認できます。
- **巨大ファイル**: 巨大ファイル Top / 古い巨大ファイル / 最近更新された巨大ファイル。
- **ログ**: アクセスできずスキップした項目（全件は `errors.csv` を参照）。

> ヒント: グラフはインタラクティブです。ズーム・パン・凡例クリックでの絞り込みができます。
> `--cdn` を付けずに生成すれば、インターネットなしでもそのまま開けます。

---

## OneDrive / reparse point の扱い

StorageAnalyzer は読み取り専用の診断ツールです。OneDrive Files On-Demand で使われる Cloud 系
reparse point は、Windows の reparse tag と OneDrive 関連パス情報から安全に識別できる場合のみ、
`os.scandir` と `stat` 中心のメタデータ走査として辿ります。ファイル本文を開いて内容確認することはなく、
オンライン専用ファイルの強制ダウンロードも行いません。ファイル自体に Cloud 系 reparse 属性がある場合も、
本文は読まず、サイズや更新日時など取得できるメタデータの範囲で扱います。

一方で、シンボリックリンク / junction / mount point は循環や二重計上を避けるため既定では辿りません。
OneDrive cloud reparse point の走査も `config.yaml` の `traverse_onedrive_cloud_reparse: false` で無効化できます。

Explorer のプロパティ値は OneDrive の同期状態、測定タイミング、オンライン専用ファイル、サイズ定義の違いに
左右されるため、完全一致は保証しません。それでも、従来丸ごと未集計になり得た OneDrive 配下の過小集計は
改善する可能性があります。検出・降下・スキップ件数は `manifest.json` と HTML レポートのログ欄に出力します。


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
dist\StorageAnalyzer\StorageAnalyzer.exe   # 実体（フォルダ配布）
dist\StorageAnalyzer_<日時>.zip            # 同僚へ渡す配布用 zip
```

zip は `StorageAnalyzer` フォルダごと固めてあるので、展開すると次のようになります。
```
StorageAnalyzer\StorageAnalyzer.exe
StorageAnalyzer\_internal\...
StorageAnalyzer\config.yaml
...
```

実行例:
```powershell
.\dist\StorageAnalyzer\StorageAnalyzer.exe --target "C:\Users\you"
```

`build_exe.ps1` は次の点に注意しています。
- `--add-data` の区切りは Windows では `;`（`"templates;templates"`、`"config.yaml;."`）。
- `--paths src` で `src` レイアウトのパッケージを解決。
- `--collect-data plotly` で **インライン埋め込み用の plotly.js** を確実に同梱。
- ビルド後に `Compress-Archive` で日時付き zip を作成（既存があれば上書き）。

---

## 同僚に渡して使ってもらう（zip 配布）

GUI はありません。黒いコンソール画面で対象フォルダを入力するだけのシンプルなツールです。

**渡す側**: `build_exe.ps1` を実行し、できた `dist\StorageAnalyzer_<日時>.zip` をそのまま渡します。

**使う側の手順**:
1. 受け取った zip を展開する。
2. 中の `StorageAnalyzer` フォルダごと、任意の場所（デスクトップ等）に置く。
3. フォルダ内の `StorageAnalyzer.exe` をダブルクリックする。
4. 黒い画面が開いたら、調べたいフォルダのパスを貼り付ける（例: `C:\Users\自分の名前`）。
5. Enter を押す。
6. スキャンが終わると、HTML レポートが自動でブラウザに開きます。
7. レポートやログは `output` フォルダの中に、**実行日時ごとのフォルダ**として保存されます。

**注意事項**:
- zip の中の `StorageAnalyzer.exe` **単体だけを取り出さない**でください。`_internal` などの同梱物が
  必要なので、**フォルダごと**使います。
- 初回起動時に Windows Defender / SmartScreen の警告が出ることがあります（「詳細情報」→「実行」）。
- このツールは **読み取り専用** です。ファイルの削除・移動・変更は一切行いません。
- `C:\Users` などの大きいフォルダはスキャンに時間がかかります。
- 外付け HDD やネットワークドライブは I/O が遅く、さらに時間がかかります。
- 管理者権限がないと読めないフォルダはスキップされることがあります（レポートのログに記録）。

---

## よくあるエラー

### PermissionError（アクセス拒否）
権限のないフォルダ/ファイルは**スキップして処理を継続**します。止まりません。
スキップ内容は実行フォルダの `errors.csv` と HTML の「ログ」セクションに記録されます。
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
| `deepdive.py` | 第2階層 Top5 の選定と、`scan.csv` を chunk 読み込みして配下を深掘り集計 |
| `charts.py` / `palette.py` | Plotly 図（棒/円/ツリーマップ/サンキー）の生成と、カテゴリ・階層を識別しやすい共通配色 |
| `report.py` | CSV ストリーミング書き込み + `manifest.json` 出力 + Jinja2 で HTML レンダリング |
| `config.py` / `utils.py` | 設定の読み込み・パス/サイズ/時刻・安全なフォルダ名の補助 |
| `main.py` | CLI とステージ進捗のオーケストレーション（実行フォルダの作成を含む） |

メモリ安全のため、全ファイルのレコードをメモリに溜め込まず、CSV へ逐次書き出しつつ集計します。
数百万ファイル規模でも完走することを目標にしています。

---

## ライセンス / 免責
個人利用を想定したツールです。表示される容量や分類はあくまで診断の目安です。
**ファイル操作は行いません**が、結果（特にシステム領域）に基づく操作はご自身の責任で慎重に行ってください。
