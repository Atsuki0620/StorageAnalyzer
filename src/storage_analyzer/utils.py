"""クロスプラットフォーム補助関数.

- リソースパス解決（PyInstaller frozen 対応）
- サイズ/時刻の整形
- Windows reparse point / 長パス の安全な扱い（Linux ではフォールバック）

これらの関数は **決して例外で処理を止めない** ことを重視している。
"""
from __future__ import annotations

import os
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Windows のファイル属性定数（Linux の stat には存在しないことがあるため定数で持つ）
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def resource_path(relative: str) -> str:
    """テンプレートや config.yaml などの同梱リソースの絶対パスを返す.

    PyInstaller でフリーズされている場合は展開先（``sys._MEIPASS``）を、
    通常実行時はリポジトリルート（``src/storage_analyzer/utils.py`` から 2 つ上）を基点にする。
    """
    if getattr(sys, "frozen", False):  # PyInstaller でバンドルされた状態
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        return os.path.join(base, relative)

    # 通常実行: 複数の候補から実在するものを優先（editable/通常 install・任意の CWD に強くする）
    repo_root = str(Path(__file__).resolve().parents[2])
    src_dir = str(Path(__file__).resolve().parents[1])
    for base in (repo_root, os.getcwd(), src_dir):
        candidate = os.path.join(base, relative)
        if os.path.exists(candidate):
            return candidate
    return os.path.join(repo_root, relative)


def to_mb(num_bytes: int) -> float:
    """バイトを MB（小数 3 桁）に変換する."""
    return round(num_bytes / (1024 * 1024), 3)


def human_size(num_bytes: float) -> str:
    """バイトを人間可読な文字列（base-1024）に整形する."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(size) < 1024.0:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} EB"


def is_reparse_point(st: os.stat_result, entry: "os.DirEntry[str]") -> bool:
    """エントリが reparse point（ジャンクション/シンボリックリンク等）かどうか.

    Windows では ``st_file_attributes`` の REPARSE_POINT ビットで判定（ジャンクションも捕捉）。
    Linux など ``st_file_attributes`` が無い環境では ``is_symlink()`` にフォールバックする。
    **例外は投げない。**
    """
    attrs = getattr(st, "st_file_attributes", None)
    if attrs is not None:
        return bool(attrs & _FILE_ATTRIBUTE_REPARSE_POINT)
    try:
        return entry.is_symlink()
    except OSError:
        return False


def get_created_at(st: os.stat_result) -> Optional[float]:
    """作成日時（epoch 秒）を返す.

    Windows は ``st_birthtime``（真の作成日時）。無い環境（多くの Linux）は ``st_ctime``
    （inode 変更時刻＝作成日時の近似）にフォールバックする。
    """
    birth = getattr(st, "st_birthtime", None)
    if birth:
        return float(birth)
    ctime = getattr(st, "st_ctime", None)
    return float(ctime) if ctime is not None else None


def safe_timestamp(ts: Optional[float]) -> Optional[str]:
    """epoch 秒を ISO8601（秒精度・ローカル時刻）に変換する。None/異常値は None."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def now_stamp() -> str:
    """出力ファイル名用のタイムスタンプ ``YYYYMMDD_HHMMSS`` を返す."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def normalize_long_path(root: str, enabled: bool) -> str:
    """スキャンルートを絶対パス化し、Windows では任意で長パス prefix（``\\\\?\\``）を付ける.

    - Windows 以外、または ``enabled`` が False の場合は単に絶対パス化のみ。
    - 既に prefix 済み、または相対は安全側に倒す。UNC パスは ``\\\\?\\UNC\\`` 形式。
    """
    abspath = os.path.abspath(root)
    if not enabled or os.name != "nt":
        return abspath
    if abspath.startswith("\\\\?\\"):
        return abspath
    if abspath.startswith("\\\\"):  # UNC パス
        return "\\\\?\\UNC\\" + abspath[2:]
    return "\\\\?\\" + abspath


def ensure_output_dir(path: str) -> str:
    """出力ディレクトリを作成（存在すれば何もしない）して、そのパスを返す."""
    os.makedirs(path, exist_ok=True)
    return path


def try_open_browser(path: str) -> bool:
    """生成した HTML を既定ブラウザで開く。失敗しても例外にしない（戻り値で表現）."""
    try:
        import webbrowser

        uri = Path(path).resolve().as_uri()
        return bool(webbrowser.open(uri))
    except Exception:
        return False


def path_segments(rel: str) -> list[str]:
    """相対パス文字列を区切り（``/``・``\\``）で分割し、空要素と ``.`` を除いたリストを返す."""
    if not rel or rel == ".":
        return []
    norm = rel.replace("\\", "/")
    return [seg for seg in norm.split("/") if seg and seg != "."]


# stat 定数を re-export（呼び出し側が import しやすいように）
FILE_ATTRIBUTE_REPARSE_POINT = getattr(
    stat, "FILE_ATTRIBUTE_REPARSE_POINT", _FILE_ATTRIBUTE_REPARSE_POINT
)
