"""Top フォルダの深掘り集計（Phase 3）.

設計の要点:
- 深掘り対象（Top N）は ``Aggregator.finalize()`` 後の ``folder_direct``（全フォルダの
  直下サイズ）から **相対第 N 階層** のフォルダをロールアップ容量で選ぶ。全 FileRecord を
  メモリに溜め込む設計にはしない。
- 各 Top 配下の拡張子別/カテゴリ別/巨大ファイル/フォルダ別は、**生成済みの scan.csv を
  pandas の chunk 読み込みで 1 回読み直して** 集計する（数百万行でも破綻しにくい）。
- どこで失敗しても例外を握りつぶし、空の結果を返す（レポート全体は落とさない）。
"""
from __future__ import annotations

import heapq
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from storage_analyzer.config import Config
from storage_analyzer.utils import display_path, human_size, path_segments, to_mb

_CHUNK_ROWS = 100_000
_USECOLS = ["path", "name", "size_bytes", "parent", "extension", "category", "modified_at"]


# --------------------------------------------------------------------------- #
# 小さな補助
# --------------------------------------------------------------------------- #
def _rel_segments(path: str, root: str) -> list[str]:
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        return []
    return path_segments(rel)


def _depth(path: str, root: str) -> int:
    return len(_rel_segments(path, root))


def _normcase(s: str) -> str:
    return os.path.normcase(s)


def _normcase_series(s: "pd.Series") -> "pd.Series":
    # Windows の normcase は小文字化（区切りは scan.csv 側で既に os.sep に揃っている）。
    return s.str.lower() if os.name == "nt" else s


# --------------------------------------------------------------------------- #
# 結果データ
# --------------------------------------------------------------------------- #
@dataclass
class DeepDiveResult:
    label: str                       # フォルダ名（basename）
    full_path: str                   # 表示用フルパス（長パスプレフィックス除去済み）
    raw_path: str                    # 集計に使った生パス（treemap のルート一致用・prefix 込み）
    total_bytes: int
    total_human: str
    pct: float                       # 全体に対する割合（%）
    file_count: int
    folder_count: int
    folders: list[dict[str, Any]] = field(default_factory=list)
    extensions: list[dict[str, Any]] = field(default_factory=list)
    categories: list[dict[str, Any]] = field(default_factory=list)
    top_files: list[dict[str, Any]] = field(default_factory=list)
    folder_direct_sub: dict[str, int] = field(default_factory=dict)  # treemap 用


# --------------------------------------------------------------------------- #
# Top フォルダの選定（CSV 再読み込み不要・folder_direct から決める）
# --------------------------------------------------------------------------- #
def select_top_folders(folder_direct: dict[str, int], root: str, cfg: Config) -> list[tuple[str, int]]:
    """相対第 ``deep_dive_base_depth`` 階層のフォルダを、ロールアップ容量の降順で Top N 件返す.

    候補が無ければ階層を 1 つずつ浅くしてフォールバックする（浅い対象でも空にしない）。
    返り値の各フルパスは ``root`` と同じ表現（長パスプレフィックスを含み得る）。
    """
    if not folder_direct:
        return []

    rolled: dict[str, int] = defaultdict(int)
    for folder, size in folder_direct.items():
        acc = root
        for seg in _rel_segments(folder, root):
            acc = os.path.join(acc, seg)
            rolled[acc] += size
    if not rolled:
        return []

    depth = max(1, cfg.deep_dive_base_depth)
    while depth >= 1:
        candidates = [(p, v) for p, v in rolled.items() if _depth(p, root) == depth]
        if candidates:
            candidates.sort(key=lambda kv: kv[1], reverse=True)
            return candidates[: cfg.deep_dive_top_n]
        depth -= 1
    return []


# --------------------------------------------------------------------------- #
# Top 毎のアキュムレータ（chunk を足し込む）
# --------------------------------------------------------------------------- #
class _Accumulator:
    def __init__(self, top_path: str, cfg: Config) -> None:
        self.top = top_path
        self.cfg = cfg
        self.total_bytes = 0
        self.file_count = 0
        self.ext_size: dict[str, int] = defaultdict(int)
        self.category_size: dict[str, int] = defaultdict(int)
        self.folder_direct_sub: dict[str, int] = defaultdict(int)
        self._files_heap: list[tuple[int, int, dict[str, Any]]] = []
        self._counter = 0

    # -- chunk 取り込み（ベクトル化） -- #
    def add_chunk(self, sub: "pd.DataFrame") -> None:
        if sub.empty:
            return
        self.total_bytes += int(sub["size_bytes"].sum())
        self.file_count += int(len(sub))

        for ext, v in sub.groupby("extension")["size_bytes"].sum().items():
            self.ext_size[str(ext) or "(none)"] += int(v)
        for cat, v in sub.groupby("category")["size_bytes"].sum().items():
            self.category_size[str(cat) or "(other)"] += int(v)
        for par, v in sub.groupby("parent")["size_bytes"].sum().items():
            self.folder_direct_sub[str(par)] += int(v)

        n = self.cfg.deep_dive_top_files
        for r in sub.nlargest(n, "size_bytes").itertuples(index=False):
            payload = {
                "name": str(r.name),
                "size_bytes": int(r.size_bytes),
                "size_human": human_size(int(r.size_bytes)),
                "category": str(r.category),
                "extension": str(r.extension) or "(none)",
                "modified_at": str(r.modified_at),
                "path": display_path(str(r.path)),
            }
            self._counter += 1
            item = (int(r.size_bytes), self._counter, payload)
            if len(self._files_heap) < n:
                heapq.heappush(self._files_heap, item)
            else:
                heapq.heappushpop(self._files_heap, item)

    # -- 確定 -- #
    def finalize(self, root_total_bytes: int) -> DeepDiveResult:
        cfg = self.cfg
        top = self.top

        # 配下フォルダのロールアップ（相対深さ 1..extra_depth）と総フォルダ数
        rolled: dict[str, int] = defaultdict(int)
        all_folders: set[str] = set()
        extra = cfg.deep_dive_extra_depth
        for parent, size in self.folder_direct_sub.items():
            acc = top
            for i, seg in enumerate(_rel_segments(parent, top), start=1):
                acc = os.path.join(acc, seg)
                all_folders.add(acc)
                if i <= extra:
                    rolled[acc] += size

        folders_rows = self._rank_folders(rolled, top, root_total_bytes)
        ext_rows = self._rank_dict(self.ext_size, "extension", cfg.deep_dive_top_extensions, root_total_bytes)
        cat_rows = self._rank_dict(self.category_size, "category", None, root_total_bytes)
        top_files = [p for (_k, _c, p) in sorted(self._files_heap, key=lambda x: (x[0], x[1]), reverse=True)]

        pct = (self.total_bytes / root_total_bytes * 100.0) if root_total_bytes else 0.0
        return DeepDiveResult(
            label=os.path.basename(top.rstrip("\\/")) or display_path(top),
            full_path=display_path(top),
            raw_path=top,
            total_bytes=self.total_bytes,
            total_human=human_size(self.total_bytes),
            pct=round(pct, 2),
            file_count=self.file_count,
            folder_count=len(all_folders),
            folders=folders_rows,
            extensions=ext_rows,
            categories=cat_rows,
            top_files=top_files,
            folder_direct_sub=dict(self.folder_direct_sub),
        )

    def _rank_folders(self, rolled: dict[str, int], top: str, root_total: int) -> list[dict[str, Any]]:
        items = sorted(rolled.items(), key=lambda kv: kv[1], reverse=True)[: self.cfg.deep_dive_top_folders]
        rows = []
        for path, val in items:
            rel = os.path.relpath(path, top)
            rows.append({
                "folder": rel.replace("/", "\\"),
                "path": display_path(path),
                "size_bytes": val,
                "size_mb": to_mb(val),
                "size_human": human_size(val),
                "pct": round((val / self.total_bytes * 100.0) if self.total_bytes else 0.0, 2),
            })
        return rows

    @staticmethod
    def _rank_dict(data: dict[str, int], key_name: str, top_n: Optional[int], root_total: int) -> list[dict[str, Any]]:
        items = sorted(data.items(), key=lambda kv: kv[1], reverse=True)
        if top_n:
            items = items[:top_n]
        subtotal = sum(data.values())
        rows = []
        for key, val in items:
            rows.append({
                key_name: key,
                "size_bytes": val,
                "size_mb": to_mb(val),
                "size_human": human_size(val),
                "pct": round((val / subtotal * 100.0) if subtotal else 0.0, 2),
            })
        return rows


# --------------------------------------------------------------------------- #
# scan.csv を chunk で読み直して Top 毎に集計
# --------------------------------------------------------------------------- #
def compute_deep_dive(
    scan_csv: str,
    top_folders: list[tuple[str, int]],
    root: str,
    cfg: Config,
    total_bytes: int,
) -> list[DeepDiveResult]:
    if not top_folders:
        return []
    try:
        accums = [_Accumulator(path, cfg) for path, _ in top_folders]
        prefixes = [_normcase(path) + os.sep for path, _ in top_folders]
        dtypes = {c: "string" for c in _USECOLS if c != "size_bytes"}
        reader = pd.read_csv(
            scan_csv,
            encoding="utf-8-sig",
            usecols=_USECOLS,
            dtype=dtypes,
            keep_default_na=False,
            chunksize=_CHUNK_ROWS,
        )
        for chunk in reader:
            chunk["size_bytes"] = pd.to_numeric(chunk["size_bytes"], errors="coerce").fillna(0).astype("int64")
            pcol = _normcase_series(chunk["path"])
            for prefix, acc in zip(prefixes, accums):
                mask = pcol.str.startswith(prefix)
                if mask.any():
                    acc.add_chunk(chunk[mask])
        return [acc.finalize(total_bytes) for acc in accums]
    except Exception as exc:  # 深掘りはベストエフォート。失敗してもレポートは出す。
        print(f"[warn] 深掘り集計に失敗（スキップ）: {exc}", file=sys.stderr)
        return []
