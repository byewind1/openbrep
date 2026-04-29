from __future__ import annotations

import math
import streamlit.components.v1 as components

from openbrep.gdl_previewer import Preview2DResult, Preview3DResult
from ui.three_preview import render_three_preview_html


def render_preview_2d(st, data: Preview2DResult, *, plotly_available: bool, go) -> None:
    if not data:
        st.info("暂无 2D 预览数据。")
        return

    count = len(data.lines) + len(data.polygons) + len(data.circles) + len(data.arcs)
    if count == 0:
        st.info("2D 预览为空（脚本无可渲染几何，或命令未覆盖）。")
        return

    if not plotly_available:
        st.info("未安装 plotly，无法显示 2D 图形。请安装 ui 依赖后重试。")
        st.caption(f"统计：线段 {len(data.lines)}，多边形 {len(data.polygons)}，圆 {len(data.circles)}，圆弧 {len(data.arcs)}")
        return

    fig = go.Figure()

    for p1, p2 in data.lines:
        fig.add_trace(go.Scatter(
            x=[p1[0], p2[0]],
            y=[p1[1], p2[1]],
            mode="lines",
            line={"width": 2},
            showlegend=False,
            hoverinfo="skip",
        ))

    for poly in data.polygons:
        if len(poly) < 2:
            continue
        xs = [p[0] for p in poly] + [poly[0][0]]
        ys = [p[1] for p in poly] + [poly[0][1]]
        fig.add_trace(go.Scatter(
            x=xs,
            y=ys,
            mode="lines",
            line={"width": 2},
            fill="toself",
            fillcolor="rgba(56,189,248,0.15)",
            showlegend=False,
            hoverinfo="skip",
        ))

    for cx, cy, r in data.circles:
        n = 64
        xs = [cx + r * math.cos(2.0 * math.pi * i / n) for i in range(n + 1)]
        ys = [cy + r * math.sin(2.0 * math.pi * i / n) for i in range(n + 1)]
        fig.add_trace(go.Scatter(
            x=xs,
            y=ys,
            mode="lines",
            line={"width": 2},
            showlegend=False,
            hoverinfo="skip",
        ))

    for cx, cy, r, a0, a1 in data.arcs:
        end = a1
        if end < a0:
            end += 360.0
        n = 48
        xs = [cx + r * math.cos(math.radians(a0 + (end - a0) * i / n)) for i in range(n + 1)]
        ys = [cy + r * math.sin(math.radians(a0 + (end - a0) * i / n)) for i in range(n + 1)]
        fig.add_trace(go.Scatter(
            x=xs,
            y=ys,
            mode="lines",
            line={"width": 2},
            showlegend=False,
            hoverinfo="skip",
        ))

    fig.update_layout(
        height=420,
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
        xaxis={"title": "X"},
        yaxis={"title": "Y", "scaleanchor": "x", "scaleratio": 1},
    )
    st.plotly_chart(fig, use_container_width=True)


def render_preview_3d(st, data: Preview3DResult, *, plotly_available: bool, go) -> None:
    if not data:
        st.info("暂无 3D 预览数据。")
        return

    if not data.meshes and not data.wires:
        st.info("3D 预览为空（脚本无可渲染几何，或命令未覆盖）。")
        return

    components.html(render_three_preview_html(data, height=500), height=510)

    if plotly_available:
        with st.expander("Plotly fallback", expanded=False):
            _render_preview_3d_plotly(st, data, go=go)
    else:
        st.caption(f"统计：网格 {len(data.meshes)}，线框 {len(data.wires)}")


def _render_preview_3d_plotly(st, data: Preview3DResult, *, go) -> None:
    fig = go.Figure()

    for i, mesh in enumerate(data.meshes):
        hue = (i * 53) % 360
        fig.add_trace(go.Mesh3d(
            x=mesh.x,
            y=mesh.y,
            z=mesh.z,
            i=mesh.i,
            j=mesh.j,
            k=mesh.k,
            opacity=0.45,
            color=f"hsl({hue},70%,55%)",
            showscale=False,
            name=f"{mesh.name} #{i + 1}",
        ))

    for wire in data.wires:
        if len(wire) < 2:
            continue
        fig.add_trace(go.Scatter3d(
            x=[p[0] for p in wire],
            y=[p[1] for p in wire],
            z=[p[2] for p in wire],
            mode="lines",
            line={"width": 4, "color": "rgba(15,23,42,0.85)"},
            showlegend=False,
            hoverinfo="skip",
        ))

    fig.update_layout(
        height=500,
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
        scene={
            "aspectmode": "data",
            "xaxis": {"title": "X"},
            "yaxis": {"title": "Y"},
            "zaxis": {"title": "Z"},
        },
    )
    st.plotly_chart(fig, use_container_width=True)
