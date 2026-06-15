"""config.yaml の読み込みと型付き Config への正規化."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Optional

import yaml

from storage_analyzer.utils import resource_path

DEFAULT_CONFIG_FILENAME = "config.yaml"


@dataclass(frozen=True)
class Config:
    """スキャン挙動・集計件数・グラフのプルーニング設定をまとめた不変オブジェクト."""

    # 除外設定
    exclude_paths: tuple[str, ...] = ()          # normcase 済みのフルパス前方一致で除外
    exclude_dir_names: tuple[str, ...] = ()       # 小文字化したディレクトリ名で除外
    follow_symlinks: bool = False                 # 既定では symlink/junction を辿らない

    # 上位 N 件
    top_n_folders: int = 30
    top_n_files: int = 100
    top_n_extensions: int = 30
    top_n_old_large: int = 50
    top_n_recent_large: int = 50

    # 「古い」「最近」のしきい値（日数）
    old_threshold_days: int = 365
    recent_threshold_days: int = 90

    # Sankey / Treemap のプルーニング
    sankey_top_l1: int = 12
    sankey_top_l2_per_l1: int = 6
    treemap_max_depth: int = 4
    treemap_top_folders: int = 200

    # Windows 長パス prefix（\\?\）。Windows 以外では無視される。
    use_long_path_prefix: bool = True

    # 表に出す最小サイズ（ノイズ除去用、既定 0 = 無効）
    min_size_bytes_for_tables: int = 0

    # 内部メタ（どの設定ファイルを読んだか。デバッグ表示用）
    source_path: Optional[str] = field(default=None, compare=False)


def _as_str_tuple(raw: dict, key: str) -> tuple[str, ...]:
    val = raw.get(key, [])
    if not isinstance(val, (list, tuple)):
        return ()
    return tuple(str(x) for x in val if x is not None)


def _as_int(raw: dict, key: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(raw.get(key, default)))
    except (TypeError, ValueError):
        return default


def _coerce(raw: dict, source_path: Optional[str]) -> Config:
    """YAML 由来の dict を検証・正規化して Config を組み立てる。未知キーは無視."""
    exclude_paths = tuple(
        os.path.normcase(p) for p in _as_str_tuple(raw, "exclude_paths")
    )
    exclude_dir_names = tuple(
        s.lower() for s in _as_str_tuple(raw, "exclude_dir_names")
    )
    return Config(
        exclude_paths=exclude_paths,
        exclude_dir_names=exclude_dir_names,
        follow_symlinks=bool(raw.get("follow_symlinks", False)),
        top_n_folders=_as_int(raw, "top_n_folders", 30),
        top_n_files=_as_int(raw, "top_n_files", 100),
        top_n_extensions=_as_int(raw, "top_n_extensions", 30),
        top_n_old_large=_as_int(raw, "top_n_old_large", 50),
        top_n_recent_large=_as_int(raw, "top_n_recent_large", 50),
        old_threshold_days=_as_int(raw, "old_threshold_days", 365, minimum=1),
        recent_threshold_days=_as_int(raw, "recent_threshold_days", 90, minimum=1),
        sankey_top_l1=_as_int(raw, "sankey_top_l1", 12, minimum=1),
        sankey_top_l2_per_l1=_as_int(raw, "sankey_top_l2_per_l1", 6, minimum=1),
        treemap_max_depth=_as_int(raw, "treemap_max_depth", 4, minimum=1),
        treemap_top_folders=_as_int(raw, "treemap_top_folders", 200, minimum=1),
        use_long_path_prefix=bool(raw.get("use_long_path_prefix", True)),
        min_size_bytes_for_tables=_as_int(raw, "min_size_bytes_for_tables", 0),
        source_path=source_path,
    )


def load_config(path: Optional[str] = None) -> Config:
    """config.yaml を読み込んで Config を返す.

    ``path`` が None の場合は同梱の config.yaml（resource_path 経由）を探す。
    ファイルが無い/壊れている場合でも **既定値で続行**（処理を止めない）。
    """
    cfg_path = path or resource_path(DEFAULT_CONFIG_FILENAME)
    raw: dict = {}
    used_path: Optional[str] = None
    try:
        with open(cfg_path, "r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh)
        if isinstance(loaded, dict):
            raw = loaded
            used_path = cfg_path
    except FileNotFoundError:
        print(f"[warn] config が見つかりません（既定値を使用）: {cfg_path}", file=sys.stderr)
    except Exception as exc:  # YAML 構文エラーなども握って既定値で続行
        print(f"[warn] config 読み込みに失敗（既定値を使用）: {exc}", file=sys.stderr)
    return _coerce(raw, used_path)
