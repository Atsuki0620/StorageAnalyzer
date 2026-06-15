"""レポート全体で使う色管理。

カテゴリ・拡張子・フォルダ階層を、青系だけに偏らない定性配色で安定して識別するための
ヘルパをまとめる。色は SaaS 風の淡い背景でも見やすく、赤と緑だけに意味が依存しないよう
Plotly / Tableau / Okabe-Ito 系の考え方を混ぜた落ち着いたパレットにしている。
"""
from __future__ import annotations

import hashlib

# 色覚多様性に配慮した、業務レポート向けの定性パレット。
QUALITATIVE_PALETTE: tuple[str, ...] = (
    "#4E79A7",  # blue
    "#F28E2B",  # orange
    "#E15759",  # red
    "#76B7B2",  # teal
    "#59A14F",  # green
    "#EDC948",  # yellow
    "#B07AA1",  # purple
    "#FF9DA7",  # pink
    "#9C755F",  # brown
    "#BAB0AC",  # gray
    "#6B5B95",  # indigo
    "#00A6A6",  # cyan
    "#D37295",  # rose
    "#8CD17D",  # light green
    "#B6992D",  # ochre
    "#499894",  # dark teal
    "#A0CBE8",  # light blue
    "#FFBE7D",  # light orange
)

CATEGORY_COLORS: dict[str, str] = {
    "Video": "#7B61FF",
    "Image": "#D37295",
    "Archive": "#F28E2B",
    "Document": "#4E79A7",
    "Spreadsheet": "#59A14F",
    "Code": "#6B5B95",
    "Development": "#00A6A6",
    "Cache": "#8A8F98",
    "Temp": "#EDC948",
    "Application": "#E15759",
    "Database": "#9C755F",
    "Audio": "#B07AA1",
    "Font": "#499894",
    "Other": "#6B7280",
}

NEUTRAL_COLOR = "#94A3B8"
ROOT_COLOR = "#64748B"
OTHER_COLOR = "#9CA3AF"
MONTH_BAR_COLOR = "#B6992D"


def get_palette_color(index: int) -> str:
    """インデックスから循環パレット色を返す。"""
    return QUALITATIVE_PALETTE[index % len(QUALITATIVE_PALETTE)]


def _stable_index(label: str) -> int:
    digest = hashlib.blake2b(label.encode("utf-8", errors="ignore"), digest_size=2).digest()
    return int.from_bytes(digest, "big")


def get_category_color(category: str) -> str:
    """カテゴリ名に対する固定色を返す。未知カテゴリは安定ハッシュで割り当てる。"""
    label = str(category or "Other")
    return CATEGORY_COLORS.get(label, get_palette_color(_stable_index(label)))


def get_label_color(label: str) -> str:
    """フォルダ名・拡張子など任意ラベルに対して安定した色を返す。"""
    text = str(label or "(empty)")
    if text in {"(other)", "Other", "その他"}:
        return OTHER_COLOR
    return get_palette_color(_stable_index(text))


def make_color_sequence(labels: list[str] | tuple[str, ...]) -> list[str]:
    """ラベル配列と同じ長さの安定色配列を作る。"""
    return [get_label_color(str(label)) for label in labels]


def hex_to_rgba(hex_color: str, alpha: float) -> str:
    """#RRGGBB を Plotly 向け rgba() 文字列へ変換する。"""
    value = hex_color.strip().lstrip("#")
    if len(value) != 6:
        return f"rgba(148, 163, 184, {alpha:.3f})"
    try:
        r = int(value[0:2], 16)
        g = int(value[2:4], 16)
        b = int(value[4:6], 16)
    except ValueError:
        return f"rgba(148, 163, 184, {alpha:.3f})"
    a = min(1.0, max(0.0, float(alpha)))
    return f"rgba({r}, {g}, {b}, {a:.3f})"
