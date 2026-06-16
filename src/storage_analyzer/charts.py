"""Plotly 図の生成と、単独 HTML 用フラグメントへの変換.

- 棒グラフ（フォルダ別/拡張子別）、カテゴリ円グラフ、更新年月別棒グラフ
- フォルダ階層ツリーマップ（branchvalues="total" + 子孫含むロールアップ値）
- 容量フローのサンキー図（root → L1 → L2 → カテゴリ、上位に絞る）

各図は ``fig.to_html(full_html=False, ...)`` で断片化し、**先頭図にだけ plotly.js を 1 回**同梱する
（インライン=完全オフライン、または CDN）。
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Optional

import pandas as pd
import plotly.graph_objects as go

from storage_analyzer.aggregator import AggregateResult
from storage_analyzer.config import Config
from storage_analyzer.palette import (
    MONTH_BAR_COLOR,
    NEUTRAL_COLOR,
    ROOT_COLOR,
    get_category_color,
    get_label_color,
    hex_to_rgba,
    make_color_sequence,
)
from storage_analyzer.utils import display_path, human_size, path_segments

_GIB = 1024 ** 3

# 落ち着いた SaaS ダッシュボード向けのベース色。実データの識別色は palette.py で管理する。
_INK = "#1f2933"
_MUTED = "#64748b"
_GRID = "#eef1f5"
_FONT = '"Segoe UI", "Yu Gothic UI", "Hiragino Sans", Meiryo, system-ui, sans-serif'
_TREE_LINE = dict(color="#ffffff", width=1.5)

_PLOTLY_CONFIG = {"responsive": True, "displaylogo": False}


# --------------------------------------------------------------------------- #
# 共通ヘルパ
# --------------------------------------------------------------------------- #
def _style(fig: go.Figure, *, title: Optional[str] = None, height: Optional[int] = None,
           has_axes: bool = True) -> go.Figure:
    """全図に共通のレイアウト（フォント・余白・透明背景・控えめなグリッド）を適用する."""
    fig.update_layout(
        template="plotly_white",
        font=dict(family=_FONT, size=12, color=_INK),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=12, r=14, t=46 if title else 16, b=40),
        hoverlabel=dict(font=dict(family=_FONT, size=12), bgcolor="#ffffff"),
        title=dict(text=title, font=dict(size=14, color=_INK), x=0.01, xanchor="left", y=0.97)
        if title else None,
    )
    if height is not None:
        fig.update_layout(height=height)
    if has_axes:
        fig.update_xaxes(gridcolor=_GRID, zeroline=False, linecolor=_GRID, ticks="outside",
                         tickcolor=_GRID, tickfont=dict(color=_MUTED))
        fig.update_yaxes(gridcolor=_GRID, zeroline=False, linecolor=_GRID,
                         tickfont=dict(color=_MUTED))
    return fig


def _empty_fig(text: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=text, showarrow=False, font=dict(size=14, color=_MUTED))
    _style(fig, height=260, has_axes=False)
    fig.update_layout(xaxis=dict(visible=False), yaxis=dict(visible=False))
    return fig


def _short_label(path: str, keep: int = 2) -> str:
    parts = path.replace("\\", "/").rstrip("/").split("/")
    parts = [p for p in parts if p]
    if len(parts) <= keep:
        return path
    return ".../" + "/".join(parts[-keep:])


def _hbar(full_labels: list[str], short_labels: list[str], sizes: list[int],
          title: str, colors: list[str]) -> go.Figure:
    """横棒グラフ。入力は **小さい順**（=描画時に大きいものが上に来る）."""
    if not sizes:
        return _empty_fig("データなし")
    ypos = list(range(len(sizes)))
    gb = [s / _GIB for s in sizes]
    customdata = [[full, human_size(s)] for full, s in zip(full_labels, sizes)]
    fig = go.Figure(
        go.Bar(
            x=gb,
            y=ypos,
            orientation="h",
            marker_color=colors,
            customdata=customdata,
            hovertemplate="%{customdata[0]}<br><b>%{customdata[1]}</b><extra></extra>",
        )
    )
    _style(fig, title=title, height=max(340, 24 * len(sizes) + 120))
    fig.update_yaxes(tickmode="array", tickvals=ypos, ticktext=short_labels, automargin=True)
    fig.update_xaxes(title=dict(text="サイズ (GB)", font=dict(color=_MUTED, size=11)))
    return fig


def _rel_segments(path: str, root: str) -> list[str]:
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        return []
    return path_segments(rel)


# --------------------------------------------------------------------------- #
# 各図
# --------------------------------------------------------------------------- #
def fig_folder_bar(df: pd.DataFrame, cfg: Config) -> go.Figure:
    if df.empty:
        return _empty_fig("フォルダデータなし")
    d = df.iloc[::-1]
    full = [str(x) for x in d["parent"]]
    short = [_short_label(p) for p in full]
    sizes = [int(x) for x in d["size_bytes"]]
    return _hbar(full, short, sizes, "", make_color_sequence(short))


def fig_extension_bar(df: pd.DataFrame, cfg: Config) -> go.Figure:
    if df.empty:
        return _empty_fig("拡張子データなし")
    d = df.iloc[::-1]
    labels = [str(x) for x in d["extension"]]
    sizes = [int(x) for x in d["size_bytes"]]
    return _hbar(labels, labels, sizes, "", make_color_sequence(labels))


def fig_category(df: pd.DataFrame) -> go.Figure:
    if df.empty or int(df["size_bytes"].sum()) == 0:
        return _empty_fig("カテゴリデータなし")
    fig = go.Figure(
        go.Pie(
            labels=[str(x) for x in df["category"]],
            values=[int(x) for x in df["size_bytes"]],
            customdata=[str(x) for x in df["size_human"]],
            textinfo="label+percent",
            hovertemplate="<b>%{label}</b><br>%{customdata}<br>%{percent}<extra></extra>",
            hole=0.55,
            sort=True,
            marker=dict(colors=[get_category_color(str(x)) for x in df["category"]], line=dict(color="#ffffff", width=1)),
        )
    )
    _style(fig, height=420, has_axes=False)
    fig.update_layout(legend=dict(font=dict(size=11, color=_MUTED)))
    return fig


def fig_month_bar(df: pd.DataFrame) -> go.Figure:
    if df.empty:
        return _empty_fig("更新年月データなし")
    gb = [int(s) / _GIB for s in df["size_bytes"]]
    fig = go.Figure(
        go.Bar(
            x=[str(m) for m in df["month"]],
            y=gb,
            marker_color=MONTH_BAR_COLOR,
            customdata=[str(x) for x in df["size_human"]],
            hovertemplate="%{x}<br><b>%{customdata}</b><extra></extra>",
        )
    )
    _style(fig, height=380)
    fig.update_xaxes(title=dict(text="更新年月", font=dict(color=_MUTED, size=11)))
    fig.update_yaxes(title=dict(text="サイズ (GB)", font=dict(color=_MUTED, size=11)))
    return fig


def _build_hierarchy(
    folder_direct: dict[str, int], root: str, cfg: Config
) -> Optional[tuple[list[str], list[str], list[str], list[int], list[str], list[str]]]:
    """フォルダ階層を (ids, labels, parents, values, hover) に組み立てる.

    深さ ``treemap_max_depth`` を超えるフォルダはその深さの祖先に畳み込む。各ノードの値は
    **子孫を含むロールアップ合計**にし、``branchvalues="total"`` 用に親 >= 子合計を保つ。
    treemap と icicle で共有する。
    """
    if not folder_direct:
        return None

    max_depth = cfg.treemap_max_depth
    total: dict[str, int] = defaultdict(int)
    for folder, size in folder_direct.items():
        segs = _rel_segments(folder, root)
        if len(segs) > max_depth:
            segs = segs[:max_depth]
        acc = root
        total[acc] += size
        for seg in segs:
            acc = os.path.join(acc, seg)
            total[acc] += size

    # 上位フォルダに絞る（祖先は必ず残してツリーの整合を保つ）
    cap = cfg.treemap_top_folders
    if len(total) > cap:
        kept = dict(sorted(total.items(), key=lambda kv: kv[1], reverse=True)[:cap])
        closed: dict[str, int] = {}
        for path in kept:
            cur = path
            guard = 0
            while guard < 1024:
                guard += 1
                closed[cur] = total[cur]
                if cur == root:
                    break
                parent = os.path.dirname(cur)
                if parent == cur or parent not in total:
                    break
                cur = parent
        total = closed

    ids: list[str] = []
    labels: list[str] = []
    parents: list[str] = []
    values: list[int] = []
    hover: list[str] = []
    colors: list[str] = []
    for path, val in total.items():
        ids.append(path)
        values.append(val)
        hover.append(human_size(val))
        if path == root:
            clean = display_path(root).rstrip("\\/")
            labels.append(os.path.basename(clean) or clean or display_path(root))
            parents.append("")
            colors.append(ROOT_COLOR)
        else:
            labels.append(os.path.basename(path) or path)
            parents.append(os.path.dirname(path))
            segs = _rel_segments(path, root)
            colors.append(get_label_color(segs[0] if segs else os.path.basename(path) or path))
    return ids, labels, parents, values, hover, colors


def fig_treemap(folder_direct: dict[str, int], root: str, cfg: Config) -> go.Figure:
    """フォルダ階層ツリーマップ（面積で容量・クリックでドリルダウン）."""
    built = _build_hierarchy(folder_direct, root, cfg)
    if built is None:
        return _empty_fig("フォルダデータなし")
    ids, labels, parents, values, hover, colors = built

    fig = go.Figure(
        go.Treemap(
            ids=ids,
            labels=labels,
            parents=parents,
            values=values,
            branchvalues="total",
            customdata=hover,
            hovertemplate="<b>%{label}</b><br>合計: %{customdata}<br>全体比: %{percentRoot}<extra></extra>",
            maxdepth=4,
            tiling=dict(packing="squarify", pad=3),
            marker=dict(colors=colors, line=_TREE_LINE),
        )
    )
    _style(fig, height=560, has_axes=False)
    fig.update_layout(margin=dict(l=8, r=8, t=10, b=8))
    return fig


def fig_icicle(folder_direct: dict[str, int], root: str, cfg: Config) -> go.Figure:
    """フォルダ階層アイシクル（左→右に階層が伸びる・クリックでドリルダウン）.

    サンキー図より階層の入れ子が読み取りやすい。treemap と同じロールアップ階層を使う。
    """
    built = _build_hierarchy(folder_direct, root, cfg)
    if built is None:
        return _empty_fig("フォルダデータなし")
    ids, labels, parents, values, hover, colors = built

    fig = go.Figure(
        go.Icicle(
            ids=ids,
            labels=labels,
            parents=parents,
            values=values,
            branchvalues="total",
            customdata=hover,
            hovertemplate="<b>%{label}</b><br>合計: %{customdata}<br>全体比: %{percentRoot}<extra></extra>",
            maxdepth=6,
            tiling=dict(orientation="h", pad=2),
            marker=dict(colors=colors, line=_TREE_LINE),
        )
    )
    _style(fig, height=600, has_axes=False)
    fig.update_layout(margin=dict(l=8, r=8, t=10, b=8))
    return fig


def fig_sankey(sankey_agg: dict[tuple[str, str, str], int], root: str, cfg: Config) -> go.Figure:
    """容量フローのサンキー図: root → 第1階層 → 第2階層 → カテゴリ."""
    if not sankey_agg:
        return _empty_fig("サンキー用データなし")

    # L1 合計 → 上位を残し、残りは "(other)" へ
    l1_total: dict[str, int] = defaultdict(int)
    for (l1, _l2, _cat), v in sankey_agg.items():
        l1_total[l1] += v
    top_l1 = {k for k, _ in sorted(l1_total.items(), key=lambda kv: kv[1], reverse=True)[: cfg.sankey_top_l1]}

    def map_l1(l1: str) -> str:
        return l1 if l1 in top_l1 else "(other)"

    # L2 合計（mapped L1 単位）→ L1 ごとに上位を残す
    l2_total: dict[tuple[str, str], int] = defaultdict(int)
    for (l1, l2, _cat), v in sankey_agg.items():
        l2_total[(map_l1(l1), l2)] += v
    keep_l2: dict[str, set[str]] = defaultdict(set)
    by_l1: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for (l1m, l2), v in l2_total.items():
        by_l1[l1m].append((l2, v))
    for l1m, lst in by_l1.items():
        for l2, _ in sorted(lst, key=lambda x: x[1], reverse=True)[: cfg.sankey_top_l2_per_l1]:
            keep_l2[l1m].add(l2)

    def map_l2(l1m: str, l2: str) -> str:
        return l2 if l2 in keep_l2[l1m] else "(other)"

    root_l1: dict[str, int] = defaultdict(int)
    l1_l2: dict[tuple[str, str], int] = defaultdict(int)
    l2_cat: dict[tuple[str, str, str], int] = defaultdict(int)
    for (l1, l2, cat), v in sankey_agg.items():
        l1m = map_l1(l1)
        l2m = map_l2(l1m, l2)
        root_l1[l1m] += v
        l1_l2[(l1m, l2m)] += v
        l2_cat[(l1m, l2m, cat)] += v

    node_labels: list[str] = []
    node_colors: list[str] = []
    node_index: dict[tuple, int] = {}

    def color_for_node(key: tuple, display: str) -> str:
        if key[0] == "ROOT":
            return ROOT_COLOR
        if key[0] == "L1":
            return get_label_color(str(key[1]))
        if key[0] == "L2":
            return get_label_color(str(key[1]))
        if key[0] == "CAT":
            return get_category_color(display)
        return NEUTRAL_COLOR

    def node(key: tuple, display: str) -> int:
        if key not in node_index:
            node_index[key] = len(node_labels)
            node_labels.append(display)
            node_colors.append(color_for_node(key, display))
        return node_index[key]

    root_display = os.path.basename(root.rstrip("/\\")) or root
    root_id = node(("ROOT",), root_display)

    src: list[int] = []
    tgt: list[int] = []
    val: list[int] = []
    for l1m, v in root_l1.items():
        if v <= 0:
            continue
        src.append(root_id)
        tgt.append(node(("L1", l1m), l1m))
        val.append(v)
    for (l1m, l2m), v in l1_l2.items():
        if v <= 0:
            continue
        src.append(node(("L1", l1m), l1m))
        tgt.append(node(("L2", l1m, l2m), l2m))
        val.append(v)
    for (l1m, l2m, cat), v in l2_cat.items():
        if v <= 0:
            continue
        src.append(node(("L2", l1m, l2m), l2m))
        tgt.append(node(("CAT", cat), cat))
        val.append(v)

    customdata = [human_size(v) for v in val]
    link_colors = [hex_to_rgba(node_colors[source], 0.24) for source in src]
    fig = go.Figure(
        go.Sankey(
            node=dict(label=node_labels, pad=14, thickness=14, color=node_colors,
                      line=dict(color="#ffffff", width=0.5)),
            link=dict(
                source=src,
                target=tgt,
                value=val,
                color=link_colors,
                customdata=customdata,
                hovertemplate="%{source.label} → %{target.label}<br><b>%{customdata}</b><extra></extra>",
            ),
        )
    )
    _style(fig, height=560, has_axes=False)
    fig.update_layout(margin=dict(l=10, r=10, t=12, b=10), font=dict(family=_FONT, size=11, color=_INK))
    return fig


# --------------------------------------------------------------------------- #
# フラグメント化
# --------------------------------------------------------------------------- #
def build_all_figures(
    agg: AggregateResult,
    root: str,
    cfg: Config,
    deep_dives: Optional[list] = None,
    use_cdn: bool = False,
) -> dict[str, str]:
    """全図を生成し、{名前: HTML 断片} を返す。先頭図にだけ plotly.js を同梱.

    深掘り（Top フォルダ）の treemap は ``deep_treemap_{i}`` キーで返す。全フラグメントを
    通して plotly.js は **最初の 1 つだけ** に同梱する（インライン or CDN）。
    """
    figs: list[tuple[str, go.Figure]] = [
        ("folder_bar", fig_folder_bar(agg.folders, cfg)),
        ("extension_bar", fig_extension_bar(agg.extensions, cfg)),
        ("category", fig_category(agg.categories)),
        ("treemap", fig_treemap(agg.folder_direct, root, cfg)),
        ("icicle", fig_icicle(agg.folder_direct, root, cfg)),
        ("sankey", fig_sankey(agg.sankey_agg, root, cfg)),
        ("month_bar", fig_month_bar(agg.months)),
    ]
    for i, dd in enumerate(deep_dives or []):
        figs.append((f"deep_treemap_{i}", fig_treemap(dd.folder_direct_sub, dd.raw_path, cfg)))

    fragments: dict[str, str] = {}
    first = True
    for name, fig in figs:
        if first:
            include: object = "cdn" if use_cdn else "inline"
        else:
            include = False
        fragments[name] = fig.to_html(
            full_html=False,
            include_plotlyjs=include,
            div_id=f"chart_{name}",
            config=_PLOTLY_CONFIG,
        )
        first = False
    return fragments
