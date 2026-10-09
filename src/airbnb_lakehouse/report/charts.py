"""Tiny dependency-free SVG line chart for the results site.

Follows the dataviz rules used across the site: one y-axis, recessive grid, 2px lines,
markers >= 8px hit targets, categorical colours by fixed slot (never by rank), direct labels
at the line ends plus a legend, and a hover tooltip (data-* attributes read by site JS).
Colours are CSS custom properties so light/dark mode swap in one place.
"""

from __future__ import annotations

from datetime import date
from html import escape


def _nice_max(v: float) -> float:
    if v <= 0:
        return 1
    import math

    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * exp:
            return m * exp
    return 10 * exp


def line_chart(series: list[dict], *, y_label: str, width: int = 760, height: int = 280) -> str:
    """series: [{"name": str, "slot": "--series-1", "points": [(date, value), ...]}]"""
    pts_all = [p for s in series for p in s["points"]]
    if not pts_all:
        return '<p class="muted">No data.</p>'
    left, right, top, bottom = 56, 110, 16, 32
    pw, ph = width - left - right, height - top - bottom
    xs = sorted({p[0] for p in pts_all})
    x0, x1 = xs[0].toordinal(), xs[-1].toordinal()
    ymax = _nice_max(max(p[1] for p in pts_all))

    def sx(d: date) -> float:
        return left + (0 if x1 == x0 else (d.toordinal() - x0) / (x1 - x0) * pw)

    def sy(v: float) -> float:
        return top + ph - v / ymax * ph

    out = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(y_label)}">'
    ]
    for i in range(5):
        v = ymax * i / 4
        y = sy(v)
        out.append(f'<line class="grid" x1="{left}" x2="{left + pw}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(
            f'<text class="tick" x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">{v:,.0f}</text>'
        )
    for d in xs[:: max(1, len(xs) // 6)]:
        out.append(
            f'<text class="tick" x="{sx(d):.1f}" y="{height - 10}" text-anchor="middle">{d:%b %Y}</text>'
        )
    label_ys = []
    for s in series:
        if not s["points"]:
            continue
        path = " ".join(
            f"{'M' if i == 0 else 'L'}{sx(d):.1f},{sy(v):.1f}"
            for i, (d, v) in enumerate(s["points"])
        )
        color = f"var({s['slot']})"
        out.append(
            f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>'
        )
        for d, v in s["points"]:
            out.append(
                f'<circle class="pt" cx="{sx(d):.1f}" cy="{sy(v):.1f}" r="3" fill="{color}" '
                f'data-tip="{escape(s["name"])} - {d:%b %Y}: {v:,} reviews"><title>{escape(s["name"])} {d:%b %Y}: {v:,}</title></circle>'
            )
        ld, lv = s["points"][-1]
        ly = sy(lv)
        while any(abs(ly - o) < 14 for o in label_ys):
            ly += 14
        label_ys.append(ly)
        out.append(
            f'<text class="direct" x="{sx(ld) + 8:.1f}" y="{ly + 4:.1f}">{escape(s["name"])}</text>'
        )
    out.append(f'<text class="tick" x="{left}" y="{top - 4}">{escape(y_label)}</text>')
    out.append("</svg>")
    legend = "".join(
        f'<span class="key"><i style="background:var({s["slot"]})"></i>{escape(s["name"])}</span>'
        for s in series
    )
    return f'<div class="legend">{legend}</div>' + "".join(out)
