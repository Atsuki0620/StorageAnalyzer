"""拡張子とパスからカテゴリを判定する純粋関数群.

判定の優先順位（plan で確定）:
  1. パスシグナル（node_modules → Development、cache → Cache、temp/tmp → Temp など）が
     拡張子マップより **優先**。例: node_modules 配下の .js は Code ではなく Development。
  2. 拡張子マップ。
  3. どれにも当たらなければ "Other"。

パスシグナルは os.sep で分割したセグメント単位で完全一致を取り、
"contemporary" が "temp" に誤マッチするような部分一致を避ける。
"""
from __future__ import annotations

# 14 カテゴリ（仕様準拠）
CATEGORIES: tuple[str, ...] = (
    "Video",
    "Image",
    "Audio",
    "Archive",
    "Document",
    "Spreadsheet",
    "Presentation",
    "Code",
    "Development",
    "Cache",
    "Temp",
    "Application",
    "Database",
    "Other",
)

# 拡張子 → カテゴリ。キーは小文字・ドット付き。
EXT_MAP: dict[str, str] = {
    # Video
    ".mp4": "Video", ".mkv": "Video", ".mov": "Video", ".avi": "Video",
    ".wmv": "Video", ".flv": "Video", ".webm": "Video", ".m4v": "Video",
    ".mpg": "Video", ".mpeg": "Video", ".m2ts": "Video", ".ts": "Video",
    ".vob": "Video", ".3gp": "Video", ".ogv": "Video", ".mts": "Video",
    # Image
    ".jpg": "Image", ".jpeg": "Image", ".png": "Image", ".gif": "Image",
    ".bmp": "Image", ".tiff": "Image", ".tif": "Image", ".webp": "Image",
    ".svg": "Image", ".heic": "Image", ".heif": "Image", ".ico": "Image",
    ".psd": "Image", ".raw": "Image", ".cr2": "Image", ".nef": "Image",
    ".arw": "Image", ".dng": "Image", ".avif": "Image",
    # Audio
    ".mp3": "Audio", ".wav": "Audio", ".flac": "Audio", ".aac": "Audio",
    ".ogg": "Audio", ".m4a": "Audio", ".wma": "Audio", ".aiff": "Audio",
    ".alac": "Audio", ".opus": "Audio", ".mid": "Audio", ".midi": "Audio",
    # Archive
    ".zip": "Archive", ".7z": "Archive", ".rar": "Archive", ".tar": "Archive",
    ".gz": "Archive", ".bz2": "Archive", ".xz": "Archive", ".zst": "Archive",
    ".cab": "Archive", ".iso": "Archive", ".tgz": "Archive", ".lz": "Archive",
    ".lzma": "Archive", ".arj": "Archive", ".z": "Archive",
    # Document
    ".pdf": "Document", ".doc": "Document", ".docx": "Document", ".txt": "Document",
    ".rtf": "Document", ".odt": "Document", ".md": "Document", ".tex": "Document",
    ".epub": "Document", ".mobi": "Document", ".pages": "Document", ".wpd": "Document",
    ".log": "Document",
    # Spreadsheet
    ".xls": "Spreadsheet", ".xlsx": "Spreadsheet", ".xlsm": "Spreadsheet",
    ".csv": "Spreadsheet", ".tsv": "Spreadsheet", ".ods": "Spreadsheet",
    ".numbers": "Spreadsheet",
    # Presentation
    ".ppt": "Presentation", ".pptx": "Presentation", ".odp": "Presentation",
    ".key": "Presentation",
    # Code
    ".py": "Code", ".js": "Code", ".ts": "Code", ".tsx": "Code", ".jsx": "Code",
    ".java": "Code", ".c": "Code", ".cpp": "Code", ".cc": "Code", ".h": "Code",
    ".hpp": "Code", ".cs": "Code", ".go": "Code", ".rs": "Code", ".rb": "Code",
    ".php": "Code", ".swift": "Code", ".kt": "Code", ".scala": "Code", ".sh": "Code",
    ".ps1": "Code", ".bat": "Code", ".cmd": "Code", ".html": "Code", ".htm": "Code",
    ".css": "Code", ".scss": "Code", ".sass": "Code", ".less": "Code", ".json": "Code",
    ".xml": "Code", ".yaml": "Code", ".yml": "Code", ".toml": "Code", ".ini": "Code",
    ".ipynb": "Code", ".sql": "Code", ".r": "Code", ".lua": "Code", ".pl": "Code",
    ".vue": "Code", ".dart": "Code", ".vb": "Code", ".asm": "Code",
    # Development（ビルド生成物・成果物など）
    ".o": "Development", ".obj": "Development", ".lib": "Development", ".a": "Development",
    ".pdb": "Development", ".class": "Development", ".pyc": "Development", ".pyd": "Development",
    ".jar": "Development", ".war": "Development", ".whl": "Development", ".egg": "Development",
    ".node": "Development", ".wasm": "Development", ".map": "Development",
    # Temp
    ".tmp": "Temp", ".temp": "Temp", ".bak": "Temp", ".old": "Temp", ".swp": "Temp",
    ".part": "Temp", ".crdownload": "Temp",
    # Application（実行ファイル・ライブラリ・インストーラ）
    ".exe": "Application", ".msi": "Application", ".dll": "Application", ".sys": "Application",
    ".app": "Application", ".apk": "Application", ".deb": "Application", ".rpm": "Application",
    ".dmg": "Application", ".appimage": "Application", ".com": "Application", ".so": "Application",
    # Database
    ".db": "Database", ".sqlite": "Database", ".sqlite3": "Database", ".mdb": "Database",
    ".accdb": "Database", ".dbf": "Database", ".mdf": "Database", ".ldf": "Database",
    ".frm": "Database", ".ibd": "Database", ".myd": "Database",
}

# パスシグナル（セグメント完全一致）。先に評価されるものほど優先度が高い。
_DEV_SEGMENTS = frozenset({
    "node_modules", "site-packages", "__pycache__", ".gradle", ".m2",
    "bower_components", ".tox", ".nox", ".venv", "venv", "virtualenv",
    "vendor", ".egg-info",
})
_CACHE_SEGMENTS = frozenset({".cache", "cache", "caches"})
_TEMP_SEGMENTS = frozenset({"temp", "tmp"})


def classify(extension: str, parent_path_lower: str) -> str:
    """拡張子（小文字・ドット付き）と小文字化した親パスからカテゴリを返す.

    Parameters
    ----------
    extension:
        例 ".mp4"（無い場合は ""）。
    parent_path_lower:
        ファイルの親フォルダのパスを小文字化したもの。
    """
    segments = set(parent_path_lower.replace("\\", "/").split("/"))

    # 1. パスシグナル（優先）
    if segments & _DEV_SEGMENTS:
        return "Development"
    if segments & _CACHE_SEGMENTS:
        return "Cache"
    if segments & _TEMP_SEGMENTS:
        return "Temp"

    # 2. 拡張子
    if extension:
        category = EXT_MAP.get(extension)
        if category is not None:
            return category

    # 3. フォールバック
    return "Other"
