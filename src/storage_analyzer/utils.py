"""クロスプラットフォーム補助関数.

- リソースパス解決（PyInstaller frozen 対応）
- サイズ/時刻の整形
- Windows reparse point / 長パス の安全な扱い（Linux ではフォールバック）

これらの関数は **決して例外で処理を止めない** ことを重視している。
"""
from __future__ import annotations

import os
import re
import stat
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

# Windows のファイル属性 / reparse tag 定数（Linux の stat には存在しないため定数で持つ）
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
IO_REPARSE_TAG_SYMLINK = 0xA000000C
# OneDrive Files On-Demand などで使われる Cloud 系タグ。実測値 0x9000701a も範囲判定で捕捉する。
IO_REPARSE_TAG_CLOUD = 0x9000001A
IO_REPARSE_TAG_CLOUD_1 = 0x9000101A
IO_REPARSE_TAG_CLOUD_2 = 0x9000201A


@dataclass(frozen=True)
class ReparseInfo:
    """reparse point の安全な分類結果。

    ``kind`` は ``not_reparse`` / ``symlink`` / ``junction`` / ``onedrive_cloud`` /
    ``other_reparse`` / ``unknown_reparse`` のいずれかを想定する。
    """

    kind: str
    is_reparse: bool
    tag: Optional[int] = None
    tag_hex: Optional[str] = None
    is_cloud_tag: bool = False
    is_onedrive_path: bool = False


def is_cloud_reparse_tag(tag: Optional[int]) -> bool:
    """Cloud 系 reparse tag かどうかを判定する。

    OneDrive の実測で見られた ``0x9000701a`` を含め、上位 nibble が ``0x9``、
    下位 byte が ``0x1a`` のタグを Cloud 系として扱う。
    """
    if tag is None:
        return False
    return (tag & 0xF0000000) == 0x90000000 and (tag & 0x000000FF) == 0x1A


def _onedrive_roots() -> tuple[str, ...]:
    roots: list[str] = []
    for name in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        value = os.environ.get(name)
        if value:
            roots.append(os.path.normcase(os.path.abspath(value)))
    return tuple(dict.fromkeys(roots))


def is_under_onedrive_root(path: str) -> bool:
    """OneDrive 関連環境変数の配下かどうかを安全に判定する。"""
    if not path:
        return False
    npath = os.path.normcase(os.path.abspath(path))
    for root in _onedrive_roots():
        try:
            if npath == root or os.path.commonpath([npath, root]) == root:
                return True
        except ValueError:
            continue
    return False


def has_onedrive_path_component(path: str) -> bool:
    """パス要素に OneDrive が含まれるかを補助的に判定する。

    これ単独では降下許可に使わず、Cloud 系 reparse tag と組み合わせる。
    """
    parts = re.split(r"[\\/]+", strip_long_path_prefix(str(path)))
    return any(part.lower().startswith("onedrive") for part in parts if part)


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
    """エントリが reparse point（ジャンクション/シンボリックリンク等）かどうか。"""
    return classify_reparse_point(st, entry).is_reparse


def classify_reparse_point(st: os.stat_result, entry: "os.DirEntry[str]") -> ReparseInfo:
    """Windows reparse point を安全に分類する。

    Windows では ``st_file_attributes`` と ``st_reparse_tag`` を使う。属性がない環境では
    symlink 判定だけにフォールバックし、Cloud / junction と誤判定しない。
    """
    path = getattr(entry, "path", "")
    raw_tag = getattr(st, "st_reparse_tag", None)
    tag = int(raw_tag or 0)
    tag_hex = f"0x{tag:08x}" if tag else None
    try:
        is_link = entry.is_symlink()
    except OSError:
        is_link = False

    raw_attrs = getattr(st, "st_file_attributes", None)
    attrs = int(raw_attrs or 0)
    attr_reparse = bool(attrs & _FILE_ATTRIBUTE_REPARSE_POINT)

    # Windows の通常ディレクトリにも st_reparse_tag=0 が現れることがある。
    # FILE_ATTRIBUTE_REPARSE_POINT が立っていない場合は、tag 属性の有無や tag=0 を
    # reparse point の根拠にせず、明確に通常エントリとして扱う。
    if not is_link and raw_attrs is not None and not attr_reparse:
        return ReparseInfo(kind="not_reparse", is_reparse=False)
    if not is_link and raw_attrs is None and tag == 0:
        return ReparseInfo(kind="not_reparse", is_reparse=False)

    cloud_tag = is_cloud_reparse_tag(tag)
    onedrive_path = is_under_onedrive_root(path) or has_onedrive_path_component(path)
    is_reparse = attr_reparse or is_link or tag != 0

    if is_link or tag == IO_REPARSE_TAG_SYMLINK:
        kind = "symlink"
    elif tag == IO_REPARSE_TAG_MOUNT_POINT:
        # Windows の junction と mount point は同じ reparse tag で表れる。安全側では同じ扱い。
        kind = "junction"
    elif cloud_tag and onedrive_path:
        kind = "onedrive_cloud"
    elif cloud_tag:
        kind = "other_reparse"
    elif is_reparse and tag == 0:
        kind = "unknown_reparse"
    elif is_reparse:
        kind = "other_reparse"
    else:
        kind = "not_reparse"

    return ReparseInfo(
        kind=kind,
        is_reparse=is_reparse,
        tag=tag if tag else None,
        tag_hex=tag_hex,
        is_cloud_tag=cloud_tag,
        is_onedrive_path=onedrive_path,
    )


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


def run_stamp() -> str:
    """実行フォルダ名用のタイムスタンプ ``YYYY-MM-DD_HH-mm``（分精度）を返す."""
    return datetime.now().strftime("%Y-%m-%d_%H-%M")


# Windows でファイル名・フォルダ名に使えない文字
_INVALID_NAME_CHARS = ':\\/*?"<>|'


def strip_long_path_prefix(path: str) -> str:
    """Windows の長パスプレフィックス（``\\\\?\\`` / ``\\\\?\\UNC\\``）を外す."""
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[len("\\\\?\\UNC\\"):]
    if path.startswith("\\\\?\\"):
        return path[len("\\\\?\\"):]
    return path


def display_path(path: Optional[str]) -> str:
    """表示用にパスを整える。長パスプレフィックスを外す。None/空は空文字へ."""
    if not path:
        return ""
    return strip_long_path_prefix(str(path))


def safe_target_name(target: str, max_len: int = 100) -> str:
    """対象パスを Windows で安全なフォルダ名に変換する.

    例: ``C:\\Users\\atsuk`` -> ``C_Users_atsuk`` / ``D:\\Video Library`` ->
    ``D_Video_Library`` / ``C:\\`` -> ``C_root``。禁止文字と空白を ``_`` に置換し、
    連続 ``_`` を畳んで端を整える。
    """
    p = strip_long_path_prefix(str(target)).strip()

    # ドライブルート（"C:\" / "C:" / "C:/"）は "<letter>_root"
    if len(p) >= 2 and p[1] == ":" and p[0].isalpha():
        rest = p[2:].replace("/", "\\").strip("\\")
        if not rest:
            return f"{p[0].upper()}_root"

    for ch in _INVALID_NAME_CHARS:
        p = p.replace(ch, "_")
    p = p.replace(" ", "_")
    while "__" in p:
        p = p.replace("__", "_")
    p = p.strip("_. ")  # 末尾のドット/空白は Windows で不可
    if not p:
        p = "root"
    if len(p) > max_len:
        p = p[:max_len].strip("_. ") or "root"
    return p


def unique_dir(path: str) -> str:
    """``path`` が既存なら ``_2``, ``_3`` … を付けて未使用のパスを返す."""
    if not os.path.exists(path):
        return path
    n = 2
    while os.path.exists(f"{path}_{n}"):
        n += 1
    return f"{path}_{n}"


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
