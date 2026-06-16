import json
from pathlib import Path

import pandas as pd

from storage_analyzer.aggregator import AggregateResult
from storage_analyzer.config import Config
from storage_analyzer.report import render_report, write_manifest
from storage_analyzer.scanner import ScanStats


def test_manifest_contains_reparse_summary(tmp_path: Path) -> None:
    stats = ScanStats(started_at=1.0, finished_at=2.0)
    stats.onedrive_cloud_reparse_detected = 3
    stats.add_reparse_record({"path": "C:/Users/me/OneDrive", "kind": "onedrive_cloud"}, limit=10)
    manifest = tmp_path / "manifest.json"
    write_manifest(
        str(manifest),
        stats=stats,
        cfg=Config(),
        target_original=".",
        target_normalized=".",
        target_label="root",
        output_dir=str(tmp_path),
        report_path=str(tmp_path / "report.html"),
        scan_csv_path=str(tmp_path / "scan.csv"),
        errors_csv_path=str(tmp_path / "errors.csv"),
    )
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["reparse_summary"]["onedrive_cloud_reparse_detected"] == 3
    assert data["reparse_summary"]["records"][0]["path"] == "C:/Users/me/OneDrive"


def test_html_report_renders_with_empty_reparse_data(tmp_path: Path) -> None:
    empty = pd.DataFrame()
    agg = AggregateResult(
        total_bytes=0,
        folders=empty,
        extensions=empty,
        categories=empty,
        months=empty,
        top_files=empty,
        old_large=empty,
        recent_large=empty,
        skips=empty,
        folder_direct={},
        sankey_agg={},
        root=str(tmp_path),
    )
    charts = {
        "folder_bar": "",
        "extension_bar": "",
        "category": "",
        "treemap": "",
        "icicle": "",
        "sankey": "",
        "month_bar": "",
    }
    html_path = tmp_path / "report.html"
    render_report(str(html_path), stats=ScanStats(), agg=agg, figures=charts, cfg=Config(), scan_target=".")
    html = html_path.read_text(encoding="utf-8")
    assert "Scan Coverage / reparse point 対応" in html
    assert "reparse point の代表記録はありません" in html
