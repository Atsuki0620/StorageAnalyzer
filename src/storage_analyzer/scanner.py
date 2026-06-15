"""ファイルシステム走査.

設計上の要点:
- ``os.scandir`` ベースの **反復（明示スタック）DFS**。再帰を使わないので深い階層でも
  RecursionError にならない。
- ディレクトリ単位・エントリ単位で try/except を張り、PermissionError / FileNotFoundError /
  OSError / 長すぎるパス / 走査中の消失などが起きても **処理全体を止めない**。捕捉した
  エラーは SkipRecord として skip コールバックに渡す。
- ``count_files()`` は進捗バー用の **軽量な事前カウント**（stat を呼ばずファイル数だけ数える）。
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
    get_created_at,
    is_reparse_point,
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

    @property
    def elapsed_s(self) -> float:
        end = self.finished_at or time.time()
        return max(0.0, end - self.started_at)


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

    def _should_descend(self, entry: "os.DirEntry[str]", follow: bool) -> bool:
        """ディレクトリエントリに降りるべきか（symlink/junction/除外を考慮）."""
        if not follow:
            try:
                if entry.is_symlink():
                    return False
            except OSError:
                return False
            try:
                st = entry.stat(follow_symlinks=False)
                if is_reparse_point(st, entry):
                    return False
            except OSError:
                # stat できないディレクトリは安全側で降りない
                return False
        if self._is_excluded_dir(entry.path, entry.name):
            return False
        return True

    # ------------------------------------------------------------------ #
    # 事前カウント（軽量・stat を呼ばない）
    # ------------------------------------------------------------------ #
    def count_files(self, root: str, progress: Optional[Callable[[], None]] = None) -> int:
        """進捗バー用にファイル数を概算する（キャッシュ済みの安価な判定のみ）.

        reparse の厳密判定（stat 必須）は省くため実スキャン数とわずかにドリフトし得るが、
        tqdm は total の過不足を許容するので問題ない。
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
                                if not follow and entry.is_symlink():
                                    continue
                                if self._is_excluded_dir(entry.path, entry.name):
                                    continue
                                stack.append(entry.path)
                            else:
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

        # ファイル（symlink ファイルは follow=False の場合リンク自身を stat する）
        st = entry.stat(follow_symlinks=follow)
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
