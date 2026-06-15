"""ストリーミング集計.

毎ファイル呼ばれる ``add()`` では、軽量な dict 合計と **上限付き min-heap**（上位 N 件）だけを
更新する。レコードの全保持はしない（メモリ O(distinct_folders + distinct_ext + N)）。
pandas は ``finalize()`` で **集計後の小さなデータ** を整形するときだけ使う。
"""
from __future__ import annotations

import heapq
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from storage_analyzer.config import Config
from storage_analyzer.scanner import FileRecord, SkipRecord
from storage_analyzer.utils import human_size, path_segments, to_mb

_SECONDS_PER_DAY = 86400
_SKIP_TABLE_CAP = 1000  # レポートの skip テーブルに載せる上限（CSV には全件出る）


@dataclass
class AggregateResult:
    """finalize() の出力。DataFrame は全て小さい（高々 数百行）."""

    total_bytes: int
    folders: pd.DataFrame
    extensions: pd.DataFrame
    categories: pd.DataFrame
    months: pd.DataFrame
    top_files: pd.DataFrame
    old_large: pd.DataFrame
    recent_large: pd.DataFrame
    skips: pd.DataFrame
    folder_direct: dict[str, int]      # treemap 用（フォルダ直下サイズ。未ロールアップ）
    sankey_agg: dict[tuple[str, str, str], int]
    root: str


class Aggregator:
    def __init__(self, config: Config, root: str) -> None:
        self.cfg = config
        self.root = root
        self.total_bytes = 0

        self.folder_direct: dict[str, int] = defaultdict(int)
        self.ext_size: dict[str, int] = defaultdict(int)
        self.category_size: dict[str, int] = defaultdict(int)
        self.month_size: dict[str, int] = defaultdict(int)
        self.sankey_agg: dict[tuple[str, str, str], int] = defaultdict(int)

        self._files_heap: list[tuple[int, int, dict[str, Any]]] = []
        self._old_heap: list[tuple[int, int, dict[str, Any]]] = []
        self._recent_heap: list[tuple[int, int, dict[str, Any]]] = []
        self._counter = 0

        now = time.time()
        self._old_cutoff = now - config.old_threshold_days * _SECONDS_PER_DAY
        self._recent_cutoff = now - config.recent_threshold_days * _SECONDS_PER_DAY

        self._skips: list[SkipRecord] = []

    # ------------------------------------------------------------------ #
    def add(self, rec: FileRecord) -> None:
        b = rec.size_bytes
        self.total_bytes += b

        self.folder_direct[rec.parent] += b
        self.ext_size[rec.extension or "(none)"] += b
        self.category_size[rec.category] += b
        if rec.modified_at:
            self.month_size[rec.modified_at[:7]] += b  # "YYYY-MM"

        segs = self._rel_segments(rec.parent)
        l1 = segs[0] if segs else "(root files)"
        l2 = segs[1] if len(segs) > 1 else "(direct)"
        self.sankey_agg[(l1, l2, rec.category)] += b

        payload = {
            "path": rec.path,
            "name": rec.name,
            "size_bytes": b,
            "size_mb": rec.size_mb,
            "size_human": human_size(b),
            "category": rec.category,
            "extension": rec.extension or "(none)",
            "modified_at": rec.modified_at or "",
        }
        self._push(self._files_heap, self.cfg.top_n_files, b, payload)

        mtime = rec.mtime_epoch
        if mtime is not None:
            if mtime < self._old_cutoff:
                self._push(self._old_heap, self.cfg.top_n_old_large, b, payload)
            elif mtime >= self._recent_cutoff:
                self._push(self._recent_heap, self.cfg.top_n_recent_large, b, payload)

    def add_skip(self, skip: SkipRecord) -> None:
        if len(self._skips) < _SKIP_TABLE_CAP:
            self._skips.append(skip)

    # ------------------------------------------------------------------ #
    def _push(self, heap: list, n: int, key: int, payload: dict) -> None:
        if n <= 0:
            return
        self._counter += 1
        item = (key, self._counter, payload)  # counter で同サイズ時の payload 比較を回避
        if len(heap) < n:
            heapq.heappush(heap, item)
        else:
            heapq.heappushpop(heap, item)

    def _rel_segments(self, path: str) -> list[str]:
        try:
            rel = os.path.relpath(path, self.root)
        except ValueError:
            return []
        return path_segments(rel)

    def _pct(self, value: int) -> float:
        return (value / self.total_bytes * 100.0) if self.total_bytes else 0.0

    # ------------------------------------------------------------------ #
    def finalize(self) -> AggregateResult:
        folders = self._folders_df()
        extensions = self._dict_to_df(self.ext_size, "extension", self.cfg.top_n_extensions)
        categories = self._dict_to_df(self.category_size, "category", top_n=None)
        months = self._months_df()
        cols = ["name", "size_human", "size_bytes", "size_mb", "category", "extension", "modified_at", "path"]
        top_files = self._heap_to_df(self._files_heap, cols)
        old_large = self._heap_to_df(self._old_heap, cols)
        recent_large = self._heap_to_df(self._recent_heap, cols)
        skips = pd.DataFrame(
            [{"path": s.path, "error_type": s.error_type, "error_message": s.error_message} for s in self._skips],
            columns=["path", "error_type", "error_message"],
        )
        return AggregateResult(
            total_bytes=self.total_bytes,
            folders=folders,
            extensions=extensions,
            categories=categories,
            months=months,
            top_files=top_files,
            old_large=old_large,
            recent_large=recent_large,
            skips=skips,
            folder_direct=dict(self.folder_direct),
            sankey_agg=dict(self.sankey_agg),
            root=self.root,
        )

    # ------------------------------------------------------------------ #
    def _dict_to_df(self, data: dict[str, int], key_name: str, top_n: Optional[int]) -> pd.DataFrame:
        items = sorted(data.items(), key=lambda kv: kv[1], reverse=True)
        if top_n and top_n > 0:
            items = items[:top_n]
        rows = [
            {
                key_name: key,
                "size_bytes": val,
                "size_mb": to_mb(val),
                "size_human": human_size(val),
                "pct": round(self._pct(val), 2),
            }
            for key, val in items
        ]
        return pd.DataFrame(rows, columns=[key_name, "size_bytes", "size_mb", "size_human", "pct"])

    def _folders_df(self) -> pd.DataFrame:
        """フォルダ別容量（子孫を含むロールアップ）の Top N。ルート自身は除外."""
        rolled: dict[str, int] = defaultdict(int)
        for folder, size in self.folder_direct.items():
            segs = self._rel_segments(folder)
            acc = self.root
            rolled[acc] += size
            for seg in segs:
                acc = os.path.join(acc, seg)
                rolled[acc] += size
        rolled.pop(self.root, None)  # ルート=総容量なので棒グラフからは除外
        return self._dict_to_df(rolled, "parent", self.cfg.top_n_folders)

    def _months_df(self) -> pd.DataFrame:
        items = sorted(self.month_size.items(), key=lambda kv: kv[0])  # 月の昇順
        rows = [
            {"month": m, "size_bytes": v, "size_mb": to_mb(v), "size_human": human_size(v)}
            for m, v in items
        ]
        return pd.DataFrame(rows, columns=["month", "size_bytes", "size_mb", "size_human"])

    @staticmethod
    def _heap_to_df(heap: list, columns: list[str]) -> pd.DataFrame:
        ordered = sorted(heap, key=lambda x: (x[0], x[1]), reverse=True)  # サイズ降順
        rows = [payload for (_key, _cnt, payload) in ordered]
        return pd.DataFrame(rows, columns=columns)
