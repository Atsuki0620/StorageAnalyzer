"""エントリポイント: CLI 解析・対話入力・ステージ進捗オーケストレーション.

各ステージを ``[n/6]`` のラベル付きで表示し、tqdm で「生きている」ことを常に示す:
  [1/6] 設定読み込み・対象検証
  [2/6] 対象を数えています（事前カウント。--no-precount で省略）
  [3/6] スキャン中（事前カウントがあれば真の % バー + ETA）
  [4/6] 集計
  [5/6] グラフ生成
  [6/6] レポート出力（CSV/HTML 書き込み → 既定ブラウザで自動オープン）
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional

from tqdm import tqdm

from storage_analyzer import __version__
from storage_analyzer.aggregator import Aggregator
from storage_analyzer.charts import build_all_figures
from storage_analyzer.config import load_config
from storage_analyzer.deepdive import compute_deep_dive, select_top_folders
from storage_analyzer.report import (
    render_report,
    write_errors_csv,
    write_manifest,
    write_records_csv,
)
from storage_analyzer.scanner import ScanStats, Scanner
from storage_analyzer.utils import (
    ensure_output_dir,
    human_size,
    normalize_long_path,
    run_stamp,
    safe_target_name,
    try_open_browser,
    unique_dir,
)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="storage_analyzer",
        description="Windows ローカルストレージ分析ツール（読み取り専用・削除機能なし）",
    )
    parser.add_argument("--target", help="スキャン対象のドライブ/フォルダ。未指定なら対話入力。")
    parser.add_argument("--config", help="config.yaml のパス（未指定なら同梱の既定を使用）")
    parser.add_argument("--output-dir", dest="output_dir", default="output",
                        help="出力先ディレクトリ（既定: output）")
    parser.add_argument("--no-open", dest="no_open", action="store_true",
                        help="生成した HTML を自動で開かない")
    parser.add_argument("--no-precount", dest="no_precount", action="store_true",
                        help="事前カウントを省略（% 表示なし・総走査時間は最短）")
    parser.add_argument("--cdn", action="store_true",
                        help="plotly.js を CDN 参照にする（HTML 軽量・要インターネット）")
    parser.add_argument("--version", action="version", version=f"StorageAnalyzer {__version__}")
    return parser.parse_args(argv)


def prompt_for_target() -> str:
    """対象未指定時に対話入力を求める."""
    print("スキャン対象フォルダが指定されていません。")
    while True:
        try:
            value = input("スキャン対象フォルダのパスを入力してください: ").strip().strip('"')
        except (EOFError, KeyboardInterrupt):
            print("\n入力がありませんでした。終了します。", file=sys.stderr)
            sys.exit(2)
        if value:
            return value
        print("空です。もう一度入力してください。")


def _run_precount(scanner: Scanner, root: str) -> int:
    with tqdm(total=None, unit=" files", desc="数えています", dynamic_ncols=True,
              mininterval=0.3, miniters=1) as bar:
        scanner.count_files(root, progress=lambda: bar.update(1))
        return int(bar.n)


def _run_scan(scanner: Scanner, root: str, estimate: Optional[int], rec_sink, agg: Aggregator) -> None:
    with tqdm(total=estimate, unit=" files", desc="スキャン中", dynamic_ncols=True,
              mininterval=0.3, miniters=1) as bar:
        for rec in scanner.iter_records(root):
            rec_sink.write(rec)
            agg.add(rec)
            bar.update(1)


def _print_summary(
    stats: ScanStats,
    run_dir: str,
    html_path: str,
    csv_path: str,
    err_path: str,
    manifest_path: str,
) -> None:
    print("\n========== 完了 ==========")
    print(f"  合計容量    : {human_size(stats.total_bytes)} ({stats.total_bytes:,} bytes)")
    print(f"  ファイル数  : {stats.file_count:,}")
    print(f"  フォルダ数  : {stats.folder_count:,}")
    print(f"  スキップ    : {stats.skip_count:,}")
    print(f"  実行時間    : {stats.elapsed_s:.1f} 秒")
    print(f"  実行フォルダ: {run_dir}")
    print(f"  HTML        : {html_path}")
    print(f"  CSV         : {csv_path}")
    print(f"  エラー CSV  : {err_path}")
    print(f"  manifest    : {manifest_path}")
    print("==========================")


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    # [1/6] 設定読み込み・対象検証
    print("[1/6] 設定読み込み・対象検証 ...")
    cfg = load_config(args.config)
    target_input = args.target or prompt_for_target()
    target = os.path.expanduser(target_input)
    if not os.path.exists(target):
        print(f"エラー: 対象が存在しません: {target}", file=sys.stderr)
        return 2
    if not os.path.isdir(target):
        print(f"エラー: 対象はフォルダを指定してください（ファイルは不可）: {target}", file=sys.stderr)
        return 2

    scan_root = normalize_long_path(target, cfg.use_long_path_prefix)

    # 実行ごとの専用フォルダ: output/<YYYY-MM-DD_HH-mm>_<safe_target>/
    parent_dir = ensure_output_dir(args.output_dir)
    target_label = safe_target_name(target)
    run_dir = unique_dir(os.path.join(parent_dir, f"{run_stamp()}_{target_label}"))
    os.makedirs(run_dir, exist_ok=True)
    html_path = os.path.join(run_dir, "report.html")
    csv_path = os.path.join(run_dir, "scan.csv")
    err_path = os.path.join(run_dir, "errors.csv")
    manifest_path = os.path.join(run_dir, "manifest.json")

    stats = ScanStats(started_at=time.time())
    agg = Aggregator(cfg, root=scan_root)

    with write_errors_csv(err_path) as skip_sink, write_records_csv(csv_path) as rec_sink:
        def on_skip(skip) -> None:
            skip_sink.write(skip)
            agg.add_skip(skip)

        scanner = Scanner(cfg, on_skip, stats)

        # [2/6] 事前カウント
        estimate: Optional[int] = None
        if args.no_precount:
            print("[2/6] 事前カウントをスキップ（--no-precount）")
        else:
            print("[2/6] 対象を数えています ...")
            estimate = _run_precount(scanner, scan_root)
            print(f"      概算 {estimate:,} ファイル")

        # [3/6] スキャン
        print("[3/6] スキャン中 ...")
        _run_scan(scanner, scan_root, estimate, rec_sink, agg)

    stats.finished_at = time.time()

    # [4/6] 集計（+ 深掘り集計: scan.csv を chunk で読み直す）
    print("[4/6] 集計中 ...")
    result = agg.finalize()
    print("      深掘り集計中（Top フォルダ）...")
    top_folders = select_top_folders(result.folder_direct, scan_root, cfg)
    deep_dives = compute_deep_dive(csv_path, top_folders, scan_root, cfg, result.total_bytes)

    # [5/6] グラフ生成
    print("[5/6] グラフ生成中 ...")
    figures = build_all_figures(result, scan_root, cfg, deep_dives=deep_dives, use_cdn=args.cdn)

    # [6/6] レポート出力（HTML + manifest.json）
    print("[6/6] レポート出力中 ...")
    render_report(
        html_path, stats=stats, agg=result, figures=figures, cfg=cfg,
        scan_target=target, deep_dives=deep_dives,
    )
    write_manifest(
        manifest_path, stats=stats, cfg=cfg,
        target_original=target, target_normalized=scan_root, target_label=target_label,
        output_dir=run_dir, report_path=html_path,
        scan_csv_path=csv_path, errors_csv_path=err_path,
    )

    _print_summary(stats, run_dir, html_path, csv_path, err_path, manifest_path)

    if not args.no_open:
        if try_open_browser(html_path):
            print("既定ブラウザでレポートを開きました。")
        else:
            print("ブラウザの自動オープンに失敗しました。上記 HTML を手動で開いてください。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
