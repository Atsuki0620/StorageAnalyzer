"""出力: CSV シンク（ストリーミング書き込み）と Jinja2 による HTML レポート."""
from __future__ import annotations

import csv
from datetime import datetime
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from storage_analyzer.aggregator import AggregateResult
from storage_analyzer.config import Config
from storage_analyzer.scanner import FileRecord, ScanStats, SkipRecord
from storage_analyzer.utils import human_size, resource_path

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
) -> None:
    """Jinja2 テンプレートをレンダリングして HTML を書き出す."""
    template_path = resource_path("templates/report.html.j2")
    template_dir = template_path.rsplit("report.html.j2", 1)[0] or "."
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("report.html.j2")
    html = template.render(
        kpi=build_kpi(stats, scan_target),
        charts=figures,
        categories=_records(agg.categories),
        top_files=_records(agg.top_files),
        old_large=_records(agg.old_large),
        recent_large=_records(agg.recent_large),
        skips=_records(agg.skips),
        skip_total=stats.skip_count,
        old_threshold_days=cfg.old_threshold_days,
        recent_threshold_days=cfg.recent_threshold_days,
        top_n_files=cfg.top_n_files,
    )
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)
