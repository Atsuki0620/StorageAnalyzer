"""出力: CSV シンク（ストリーミング書き込み）・manifest.json・Jinja2 による HTML レポート."""
from __future__ import annotations

import csv
import json
from datetime import datetime
from typing import Any, Optional, Sequence

from jinja2 import Environment, FileSystemLoader, select_autoescape

from storage_analyzer import __version__
from storage_analyzer.aggregator import AggregateResult
from storage_analyzer.config import Config
from storage_analyzer.scanner import FileRecord, ScanStats, SkipRecord
from storage_analyzer.utils import display_path, human_size, resource_path, safe_timestamp

_RECORD_COLUMNS = [
    "path", "name", "size_bytes", "size_mb", "extension",
    "parent", "depth", "modified_at", "created_at", "category",
]
_ERROR_COLUMNS = ["path", "error_type", "error_message"]


class RecordSink:
    """FileRecord を 1 行ずつ CSV へストリーミング書き込みする（utf-8-sig）."""

    def __init__(self, path: str) -> None:
        # utf-8-sig = BOM 付き。Windows の Excel で文字化けしないようにする。
        self._fh = open(path, "w", newline="", encoding="utf-8-sig")
        self._writer = csv.writer(self._fh)
        self._writer.writerow(_RECORD_COLUMNS)

    def write(self, rec: FileRecord) -> None:
        self._writer.writerow([
            rec.path, rec.name, rec.size_bytes, rec.size_mb, rec.extension,
            rec.parent, rec.depth, rec.modified_at or "", rec.created_at or "", rec.category,
        ])

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "RecordSink":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class SkipSink:
    """SkipRecord を 1 行ずつ CSV へ書き込む（utf-8-sig）."""

    def __init__(self, path: str) -> None:
        self._fh = open(path, "w", newline="", encoding="utf-8-sig")
        self._writer = csv.writer(self._fh)
        self._writer.writerow(_ERROR_COLUMNS)

    def write(self, skip: SkipRecord) -> None:
        self._writer.writerow([skip.path, skip.error_type, skip.error_message])

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "SkipSink":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def write_records_csv(path: str) -> RecordSink:
    return RecordSink(path)


def write_errors_csv(path: str) -> SkipSink:
    return SkipSink(path)


def write_manifest(
    path: str,
    *,
    stats: ScanStats,
    cfg: Config,
    target_original: str,
    target_normalized: str,
    target_label: str,
    output_dir: str,
    report_path: str,
    scan_csv_path: str,
    errors_csv_path: str,
) -> None:
    """実行情報を manifest.json として書き出す（読み取り専用ツールの実行メタ）."""
    manifest = {
        "app_name": "StorageAnalyzer",
        "version": __version__,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "started_at": safe_timestamp(stats.started_at) if stats.started_at else None,
        "finished_at": safe_timestamp(stats.finished_at) if stats.finished_at else None,
        "elapsed_seconds": round(stats.elapsed_s, 3),
        "target_original": target_original,
        "target_normalized": target_normalized,
        "target_label": target_label,
        "output_dir": output_dir,
        "report_path": report_path,
        "scan_csv_path": scan_csv_path,
        "errors_csv_path": errors_csv_path,
        "total_bytes": stats.total_bytes,
        "total_size_human": human_size(stats.total_bytes),
        "file_count": stats.file_count,
        "folder_count": stats.folder_count,
        "skip_count": stats.skip_count,
        "traverse_onedrive_cloud_reparse": cfg.traverse_onedrive_cloud_reparse,
        "reparse_summary": stats.reparse_summary(),
        "onedrive_cloud_reparse_detected": stats.onedrive_cloud_reparse_detected,
        "onedrive_cloud_reparse_descended": stats.onedrive_cloud_reparse_descended,
        "onedrive_cloud_reparse_skipped": stats.onedrive_cloud_reparse_skipped,
        "symlink_skipped": stats.symlink_skipped,
        "junction_skipped": stats.junction_skipped,
        "mount_point_skipped": stats.mount_point_skipped,
        "other_reparse_skipped": stats.other_reparse_skipped,
        "config_summary": {
            "top_n_folders": cfg.top_n_folders,
            "top_n_files": cfg.top_n_files,
            "top_n_extensions": cfg.top_n_extensions,
            "top_n_old_large": cfg.top_n_old_large,
            "top_n_recent_large": cfg.top_n_recent_large,
            "old_threshold_days": cfg.old_threshold_days,
            "recent_threshold_days": cfg.recent_threshold_days,
            "deep_dive_top_n": cfg.deep_dive_top_n,
            "deep_dive_base_depth": cfg.deep_dive_base_depth,
            "deep_dive_extra_depth": cfg.deep_dive_extra_depth,
            "deep_dive_top_files": cfg.deep_dive_top_files,
            "deep_dive_top_extensions": cfg.deep_dive_top_extensions,
            "deep_dive_top_folders": cfg.deep_dive_top_folders,
            "follow_symlinks": cfg.follow_symlinks,
            "follow_junctions": cfg.follow_junctions,
            "follow_mount_points": cfg.follow_mount_points,
            "traverse_onedrive_cloud_reparse": cfg.traverse_onedrive_cloud_reparse,
            "record_reparse_points": cfg.record_reparse_points,
            "max_reparse_records_in_report": cfg.max_reparse_records_in_report,
            "use_long_path_prefix": cfg.use_long_path_prefix,
        },
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)


def build_kpi(stats: ScanStats, scan_target: str) -> dict[str, str]:
    return {
        "scan_target": scan_target,
        "total_size": human_size(stats.total_bytes),
        "total_bytes": f"{stats.total_bytes:,}",
        "file_count": f"{stats.file_count:,}",
        "folder_count": f"{stats.folder_count:,}",
        "skip_count": f"{stats.skip_count:,}",
        "elapsed": f"{stats.elapsed_s:.1f} 秒",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _records(df: Any) -> list[dict[str, Any]]:
    """DataFrame -> レコードの list。空でも安全."""
    if df is None or getattr(df, "empty", True):
        return []
    return df.to_dict("records")


def render_report(
    html_path: str,
    *,
    stats: ScanStats,
    agg: AggregateResult,
    figures: dict[str, str],
    cfg: Config,
    scan_target: str,
    deep_dives: Optional[Sequence[Any]] = None,
) -> None:
    """Jinja2 テンプレートをレンダリングして HTML を書き出す."""
    template_path = resource_path("templates/report.html.j2")
    template_dir = template_path.rsplit("report.html.j2", 1)[0] or "."
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "xml"]),
    )
    # 長パスプレフィックスを隠す表示用フィルタ
    env.filters["cleanpath"] = display_path
    template = env.get_template("report.html.j2")
    kpi = build_kpi(stats, display_path(scan_target))
    html = template.render(
        kpi=kpi,
        app_version=__version__,
        charts=figures,
        categories=_records(agg.categories),
        top_files=_records(agg.top_files),
        old_large=_records(agg.old_large),
        recent_large=_records(agg.recent_large),
        skips=_records(agg.skips),
        skip_total=stats.skip_count,
        reparse_summary=stats.reparse_summary(),
        traverse_onedrive_cloud_reparse=cfg.traverse_onedrive_cloud_reparse,
        follow_symlinks=cfg.follow_symlinks,
        follow_junctions=cfg.follow_junctions,
        follow_mount_points=cfg.follow_mount_points,
        old_threshold_days=cfg.old_threshold_days,
        recent_threshold_days=cfg.recent_threshold_days,
        top_n_files=cfg.top_n_files,
        deep_dives=list(deep_dives or []),
    )
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)
