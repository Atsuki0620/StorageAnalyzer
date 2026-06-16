"""ファイルシステム走査.

設計上の要点:
- ``os.scandir`` ベースの **反復（明示スタック）DFS**。再帰を使わないので深い階層でも
  RecursionError にならない。
- ディレクトリ単位・エントリ単位で try/except を張り、PermissionError / FileNotFoundError /
  OSError / 長すぎるパス / 走査中の消失などが起きても **処理全体を止めない**。捕捉した
  エラーは SkipRecord として skip コールバックに渡す。
- ``count_files()`` は進捗バー用の **軽量な事前カウント**。ディレクトリだけは本スキャンと
  reparse 降下ポリシーを揃えるため、分類用のメタデータ stat を行う。
- ``iter_records()`` は FileRecord を 1 件ずつ **yield** するジェネレータ。CSV 書き込みや集計、
  tqdm によるラップは呼び出し側（main.py）が行い、メモリにレコードを溜め込まない。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

from storage_analyzer.classifier import classify
from storage_analyzer.config import Config
from storage_analyzer.utils import (
    ReparseInfo,
    classify_reparse_point,
    get_created_at,
    safe_timestamp,
    to_mb,
)

SkipCallback = Callable[["SkipRecord"], None]


@dataclass(slots=True)
class FileRecord:
    """1 ファイル分の情報（CSV の 1 行に対応）.

    ``mtime_epoch`` は old/recent 判定の都合で保持するが、CSV には書き出さない補助フィールド。
    """

    path: str
    name: str
    size_bytes: int
    size_mb: float
    extension: str
    parent: str
    depth: int
    modified_at: Optional[str]
    created_at: Optional[str]
    category: str
    mtime_epoch: Optional[float] = None


@dataclass(slots=True)
class SkipRecord:
    """スキップ/エラー 1 件分の情報."""

    path: str
    error_type: str
    error_message: str


@dataclass
class ScanStats:
    """スキャン全体の集計（KPI 用）."""

    total_bytes: int = 0
    file_count: int = 0
    folder_count: int = 0
    skip_count: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    onedrive_cloud_reparse_detected: int = 0
    onedrive_cloud_reparse_descended: int = 0
    onedrive_cloud_reparse_skipped: int = 0
    symlink_skipped: int = 0
    junction_skipped: int = 0
    mount_point_skipped: int = 0
    other_reparse_skipped: int = 0
    unknown_reparse_skipped: int = 0
    onedrive_cloud_file_detected: int = 0
    other_reparse_file_detected: int = 0
    reparse_records: list[dict[str, object]] | None = None

    @property
    def elapsed_s(self) -> float:
        end = self.finished_at or time.time()
        return max(0.0, end - self.started_at)

    def add_reparse_record(self, record: dict[str, object], limit: int) -> None:
        """manifest / HTML に出す reparse point 代表パスを上限付きで記録する。"""
        if limit <= 0:
            return
        if self.reparse_records is None:
            self.reparse_records = []
        if len(self.reparse_records) < limit:
            self.reparse_records.append(record)

    def reparse_summary(self) -> dict[str, object]:
        """reparse point 対応状況を出力用 dict にする。"""
        return {
            "onedrive_cloud_reparse_detected": self.onedrive_cloud_reparse_detected,
            "onedrive_cloud_reparse_descended": self.onedrive_cloud_reparse_descended,
            "onedrive_cloud_reparse_skipped": self.onedrive_cloud_reparse_skipped,
            "symlink_skipped": self.symlink_skipped,
            "junction_skipped": self.junction_skipped,
            "mount_point_skipped": self.mount_point_skipped,
            "other_reparse_skipped": self.other_reparse_skipped,
            "unknown_reparse_skipped": self.unknown_reparse_skipped,
            "onedrive_cloud_file_detected": self.onedrive_cloud_file_detected,
            "other_reparse_file_detected": self.other_reparse_file_detected,
            "records": list(self.reparse_records or []),
        }


def _make_skip(path: str, exc: BaseException) -> SkipRecord:
    return SkipRecord(
        path=path,
        error_type=type(exc).__name__,
        error_message=str(exc)[:500],
    )


class Scanner:
    """設定に従ってフォルダを走査する."""

    def __init__(self, config: Config, skip_callback: SkipCallback, stats: ScanStats) -> None:
        self.config = config
        self._skip = skip_callback
        self.stats = stats
        self._exclude_paths = config.exclude_paths           # normcase 済み
        self._exclude_dir_names = set(config.exclude_dir_names)  # 小文字
        self._visited_reparse_dirs: set[tuple[int, int] | str] = set()

    # ------------------------------------------------------------------ #
    # 除外判定
    # ------------------------------------------------------------------ #
    def _is_excluded_dir(self, path: str, name: str) -> bool:
        if name.lower() in self._exclude_dir_names:
            return True
        if self._exclude_paths:
            npath = os.path.normcase(path)
            for ex in self._exclude_paths:
                if npath == ex or npath.startswith(ex + os.sep):
                    return True
        return False

    def _should_descend(self, entry: "os.DirEntry[str]", follow: bool, *, record: bool = True) -> bool:
        """ディレクトリエントリに降りるべきかを安全に判定する。

        任意の reparse point は辿らず、OneDrive cloud reparse point と安全に識別できた
        ディレクトリだけを設定有効時にメタデータ走査として降りる。
        """
        if self._is_excluded_dir(entry.path, entry.name):
            return False

        try:
            st = entry.stat(follow_symlinks=False)
        except OSError as exc:
            if record:
                self._record_skip(entry.path, exc)
            return False

        info = classify_reparse_point(st, entry)
        if not info.is_reparse:
            return True

        if info.kind == "onedrive_cloud":
            if record:
                self.stats.onedrive_cloud_reparse_detected += 1
            if not self.config.traverse_onedrive_cloud_reparse:
                if record:
                    self.stats.onedrive_cloud_reparse_skipped += 1
                    self._record_reparse_decision(entry.path, info, "skipped", "設定で OneDrive cloud reparse point 走査が無効")
                return False
            if not record:
                return True
            if self._seen_reparse_dir(st, entry.path):
                if record:
                    self.stats.onedrive_cloud_reparse_skipped += 1
                    self._record_reparse_decision(entry.path, info, "skipped", "循環・二重計上防止のため既訪問")
                return False
            self.stats.onedrive_cloud_reparse_descended += 1
            self._record_reparse_decision(entry.path, info, "descended", "安全に識別した OneDrive cloud reparse point")
            return True

        if info.kind == "symlink":
            allowed = follow
            counter = "symlink_skipped"
            reason = "symlink は既定では辿らない"
        elif info.kind == "junction":
            allowed = self.config.follow_junctions
            counter = "junction_skipped"
            reason = "junction は循環・二重計上防止のため既定では辿らない"
        elif info.kind == "mount_point":
            allowed = self.config.follow_mount_points
            counter = "mount_point_skipped"
            reason = "mount point は既定では辿らない"
        elif info.kind == "unknown_reparse":
            allowed = False
            counter = "unknown_reparse_skipped"
            reason = "種別不明の reparse point は安全側で辿らない"
        else:
            allowed = False
            counter = "other_reparse_skipped"
            reason = "OneDrive 以外の reparse point は安全側で辿らない"

        if not allowed:
            if record:
                setattr(self.stats, counter, getattr(self.stats, counter) + 1)
                self._record_reparse_decision(entry.path, info, "skipped", reason)
            return False
        return True

    def _seen_reparse_dir(self, st: os.stat_result, path: str) -> bool:
        """reparse directory の循環・二重計上を避けるため既訪問なら True。"""
        ino = getattr(st, "st_ino", None)
        dev = getattr(st, "st_dev", None)
        key: tuple[int, int] | str
        if ino is not None and dev is not None:
            key = (int(dev), int(ino))
        else:
            key = os.path.normcase(os.path.abspath(path))
        if key in self._visited_reparse_dirs:
            return True
        self._visited_reparse_dirs.add(key)
        return False

    def _record_reparse_file(self, path: str, info: ReparseInfo) -> None:
        """クラウド/その他 reparse file を本文を読まずにメタデータ検出として記録する。"""
        if info.kind == "onedrive_cloud":
            self.stats.onedrive_cloud_file_detected += 1
        elif info.is_reparse:
            self.stats.other_reparse_file_detected += 1
        else:
            return
        self._record_reparse_decision(path, info, "file_metadata", "ファイル本文を開かずメタデータのみ計上")

    def _record_reparse_decision(self, path: str, info: ReparseInfo, action: str, reason: str) -> None:
        if not self.config.record_reparse_points:
            return
        self.stats.add_reparse_record(
            {
                "path": path,
                "kind": info.kind,
                "tag": info.tag_hex,
                "action": action,
                "reason": reason,
            },
            self.config.max_reparse_records_in_report,
        )

    def _stat_countable_file(
        self, entry: "os.DirEntry[str]", follow: bool, *, record: bool
    ) -> Optional[os.stat_result]:
        """通常ファイルとして集計してよい場合だけ stat 結果を返す。

        symlink / junction / 種別不明 reparse file は容量集計に混ぜない。
        OneDrive cloud file は本文を開かず stat のメタデータだけでサイズを計上する。
        """
        st = entry.stat(follow_symlinks=follow)
        info = classify_reparse_point(st, entry)
        if not info.is_reparse:
            return st

        if record:
            self._record_reparse_file(entry.path, info)

        if info.kind == "onedrive_cloud":
            return st
        return None

    # ------------------------------------------------------------------ #
    # 事前カウント（軽量・stat を呼ばない）
    # ------------------------------------------------------------------ #
    def count_files(self, root: str, progress: Optional[Callable[[], None]] = None) -> int:
        """進捗バー用にファイル数を概算する。

        OneDrive cloud reparse point の降下可否だけは実スキャンと揃えるため、ディレクトリでは
        reparse 分類用の軽い stat を行う。統計カウンタは更新しない。
        """
        count = 0
        follow = self.config.follow_symlinks
        stack: list[str] = [root]
        while stack:
            current = stack.pop()
            try:
                entries = os.scandir(current)
            except OSError:
                continue
            try:
                with entries:
                    for entry in entries:
                        try:
                            if entry.is_dir(follow_symlinks=follow):
                                if self._should_descend(entry, follow, record=False):
                                    stack.append(entry.path)
                            else:
                                if self._stat_countable_file(entry, follow, record=False) is not None:
                                    count += 1
                                    if progress is not None:
                                        progress()
                        except OSError:
                            continue
            except OSError:
                continue
        return count

    # ------------------------------------------------------------------ #
    # 本スキャン（FileRecord を yield）
    # ------------------------------------------------------------------ #
    def iter_records(self, root: str) -> Iterator[FileRecord]:
        follow = self.config.follow_symlinks
        stack: list[tuple[str, int]] = [(root, 0)]
        while stack:
            current, depth = stack.pop()
            try:
                entries = os.scandir(current)
            except OSError as exc:
                self._record_skip(current, exc)
                continue
            try:
                with entries:
                    for entry in entries:
                        try:
                            rec = self._process_entry(entry, current, depth + 1, stack, follow)
                        except OSError as exc:
                            # stat 失敗（権限・消失・長すぎ等）。1 件スキップして継続。
                            self._record_skip(_entry_path(entry, current), exc)
                            continue
                        if rec is not None:
                            yield rec
            except OSError as exc:
                # ディレクトリ反復中の異常。残りは諦めて次へ。
                self._record_skip(current, exc)

    def _process_entry(
        self,
        entry: "os.DirEntry[str]",
        parent: str,
        depth: int,
        stack: list[tuple[str, int]],
        follow: bool,
    ) -> Optional[FileRecord]:
        # ディレクトリ判定（OSError は呼び出し側で捕捉）
        if entry.is_dir(follow_symlinks=follow):
            if self._should_descend(entry, follow):
                self.stats.folder_count += 1
                stack.append((entry.path, depth))
            return None

        # ファイル（follow=False の reparse file / symlink file は通常ファイルとして数えない）
        st = self._stat_countable_file(entry, follow, record=True)
        if st is None:
            return None
        size = int(st.st_size)
        name = entry.name
        ext = os.path.splitext(name)[1].lower()
        mtime = getattr(st, "st_mtime", None)
        category = classify(ext, parent.lower())

        self.stats.total_bytes += size
        self.stats.file_count += 1
        return FileRecord(
            path=entry.path,
            name=name,
            size_bytes=size,
            size_mb=to_mb(size),
            extension=ext,
            parent=parent,
            depth=depth,
            modified_at=safe_timestamp(mtime),
            created_at=safe_timestamp(get_created_at(st)),
            category=category,
            mtime_epoch=float(mtime) if mtime is not None else None,
        )

    def _record_skip(self, path: str, exc: BaseException) -> None:
        self.stats.skip_count += 1
        self._skip(_make_skip(path, exc))


def _entry_path(entry: "os.DirEntry[str]", parent: str) -> str:
    """entry.path がアクセスできない場合のフォールバックを含むパス取得."""
    try:
        return entry.path
    except Exception:
        try:
            return os.path.join(parent, entry.name)
        except Exception:
            return parent
