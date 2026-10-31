"""Table and chart images.

compact_table: the small bordered snapshot tables (navy header, thin borders).
square_table / square_lines: the 1200x1200 images posted to X, in the website's modern look: gradient
background, white card with a soft shadow, rounded gradient header, rounded change pills, KPI tiles,
Inter font (bundled in fonts/).
"""
import os
import re

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager as fm  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402

NAVY, BAND, ZEBRA = '#1f3a5f', '#dfe8f5', '#f4f7fb'
UP, DOWN, INK, MUTE = '#0a7d33', '#c0392b', '#222222', '#6b7684'


def change_color(cur, prev):
    if cur is None or prev is None or cur == prev:
        return INK
    return UP if cur > prev else DOWN


def _style(tb, colors, first_col_left=False):
    for (r, c), x in tb.get_celld().items():
        x.set_edgecolor(NAVY)
        x.set_linewidth(0.7)
        t = x.get_text()
        t.set_fontweight('bold')
        if r == 0:
            x.set_facecolor(NAVY)
            t.set_color('white')
            continue
        x.set_facecolor(ZEBRA if r % 2 else 'white')
        given = colors[r - 1][c] if colors else None
        t.set_color(given or (NAVY if c == 0 or (colors and c == 1 and colors[r - 1][0] == MUTE) else INK))


def compact_table(path, title, header, rows, colors=None, widths=None):
    """Small table with a title band on top (snapshots, quick shares)."""
    n = len(rows) + 2
    total_w = 2.9 if len(header) == 2 else 3.9
    widths = widths or ([.64, .36] if len(header) == 2 else [.34] + [.66 / (len(header) - 1)] * (len(header) - 1))
    fig, ax = plt.subplots(figsize=(total_w, 0.27 * n + 0.05), dpi=300)
    ax.axis('off')
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    tb = ax.table(cellText=rows, colLabels=header, cellLoc='center', bbox=[0, 0, 1, (n - 1) / n], colWidths=widths)
    tb.auto_set_font_size(False)
    tb.set_fontsize(8)
    _style(tb, colors)
    ax.add_patch(Rectangle((0, (n - 1) / n), 1, 1 / n, facecolor=BAND, edgecolor=NAVY, linewidth=0.7,
                           transform=ax.transAxes, clip_on=False))
    ax.text(0.5, (n - 0.5) / n, title, ha='center', va='center', fontsize=7.5, fontweight='bold', color=NAVY)
    fig.savefig(path, bbox_inches='tight', pad_inches=0.02)
    plt.close(fig)


HERE = os.path.dirname(os.path.abspath(__file__))
for _f in ('Regular', 'SemiBold', 'Bold', 'ExtraBold'):
    _p = os.path.join(HERE, 'fonts', f'Inter-{_f}.otf')
    if os.path.exists(_p):
        fm.fontManager.addfont(_p)
FONT_RC = {'font.family': ['Inter', 'DejaVu Sans']}

BG_A, BG_B, PANEL, HAIR, BAND_ROW = '#f7faff', '#e8eff9', '#ffffff', '#e2e9f3', '#f4f8fd'
HEAD_A, HEAD_B, ACC_A, ACC_B = '#1f3a5f', '#33619c', '#1f3a5f', '#5b8fd0'
PILL_UP, PILL_DOWN = '#dcefe2', '#f6dedb'
NUMERIC = re.compile(r'^[\s\d.,KMk≈+▲▼%—–\-]*$')
PX = 72 / 200            # 1 px of the 1200 px canvas in points


def _canvas():
    """A 1200x1200 figure with one pixel-space axes (y grows downwards) and the gradient background."""
    fig = plt.figure(figsize=(6, 6), dpi=200)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1200)
    ax.set_ylim(1200, 0)
    ax.axis('off')
    cmap = LinearSegmentedColormap.from_list('bg', [BG_A, BG_B])
    yy, xx = np.mgrid[0:1:200j, 0:1:200j]
    ax.imshow((yy * 0.8 + xx * 0.2), extent=[0, 1200, 1200, 0], cmap=cmap, aspect='auto', zorder=0)
    ax.set_xlim(0, 1200)
    ax.set_ylim(1200, 0)
    return fig, ax


def _rr(ax, x, y, w, h, r, fc, z=2, alpha=1.0, ec='none', lw=0):
    p = FancyBboxPatch((x, y), w, h, boxstyle=f'round,pad=0,rounding_size={min(r, w / 2, h / 2)}',
                       facecolor=fc, edgecolor=ec, linewidth=lw, alpha=alpha, zorder=z)
    ax.add_patch(p)
    return p


def _grad(ax, x, y, w, h, r, c1, c2, z=3):
    """Left-to-right gradient inside a rounded rectangle."""
    cmap = LinearSegmentedColormap.from_list('g', [c1, c2])
    im = ax.imshow(np.linspace(0, 1, 128)[None, :], extent=[x, x + w, y + h, y], cmap=cmap, aspect='auto', zorder=z)
    clip = FancyBboxPatch((x, y), w, h, boxstyle=f'round,pad=0,rounding_size={min(r, w / 2, h / 2)}',
                          transform=ax.transData)
    im.set_clip_path(clip)
    ax.set_xlim(0, 1200)
    ax.set_ylim(1200, 0)


def _panel(ax, x, y, w, h, z=2):
    for i in range(12):                                   # soft shadow: stacked translucent layers
        g = i * 2.6
        _rr(ax, x - g, y - g + 12, w + 2 * g, h + 2 * g, 26 + g, '#1f3a5f', z=z - 0.5, alpha=0.011)
    _rr(ax, x, y, w, h, 26, PANEL, z=z, ec=HAIR, lw=0.8)


def _text(ax, x, y, s, px, color, weight=600, ha='left', z=6):
    return ax.text(x, y, s, fontsize=px * PX, fontweight=weight, color=color, ha=ha, va='center', zorder=z)


def _width(fig, t):
    return t.get_window_extent(fig.canvas.get_renderer()).width


def _fit(ax, fig, x, y, s, px, maxw, color, weight=600, ha='left', z=6, floor=0.55):
    t = _text(ax, x, y, s, px, color, weight, ha, z)
    while _width(fig, t) > maxw and t.get_fontsize() > px * PX * floor:
        t.set_fontsize(t.get_fontsize() * 0.94)
    return t


def _frame(ax, fig, title, subtitle, footer, tiles=None):
    """Accent bar, left-aligned title, optional KPI tiles and footer. Returns (top, bottom) of the free area."""
    mx = 72
    _grad(ax, mx, 48, 120, 9, 5, ACC_A, ACC_B)
    ty = 48 + 62
    _fit(ax, fig, mx, ty, title, 54, 1056, '#1f3a5f', 800)
    _fit(ax, fig, mx, ty + 50, subtitle, 27, 1056, '#6b7684', 400)
    top = ty + 50 + 36
    if tiles:
        n, gap, th = len(tiles), 18, 124
        tw = (1056 - gap * (n - 1)) / n
        for i, (label, value, sub, col) in enumerate(tiles):
            x = mx + i * (tw + gap)
            _panel(ax, x, top, tw, th, z=3)
            _fit(ax, fig, x + 22, top + 26, label.upper(), 19, tw - 40, '#6b7684', 700)
            _fit(ax, fig, x + 22, top + 66, value, 46, tw - 40, '#1f3a5f', 800)
            if sub:
                _fit(ax, fig, x + 22, top + 100, sub, 20, tw - 40, col or '#6b7684', 600)
        top += th + 26
    bottom = 1200 * 0.965
    if footer:
        left, _, right = footer.partition(' · ')
        ax.plot([mx, mx + 1056], [1200 - 82, 1200 - 82], color=HAIR, linewidth=0.8, zorder=3)
        _text(ax, mx, 1200 - 48, left, 21, '#6b7684', 400)
        if right:
            _text(ax, mx + 1056, 1200 - 48, right, 21, '#6b7684', 700, ha='right')
        bottom = 1200 - 102
    return top, bottom


def square_table(path, title, subtitle, footer, header, rows, colors=None, widths=None, tiles=None):
    """Card table. colors[r][c] overrides a cell's text colour; cells starting with an arrow become pills."""
    with plt.rc_context(FONT_RC):
        fig, ax = _canvas()
        top, bottom = _frame(ax, fig, title, subtitle, footer, tiles)
        n = len(rows)
        pad, x0, w = 22, 72, 1056
        avail = bottom - top - 2 * pad
        rh = min(avail / (n + 0.86), 98)
        hh = rh * 0.86
        total = hh + rh * n
        y0 = top + pad + (avail - total) / 2
        _panel(ax, x0, y0 - pad, w, total + 2 * pad)
        ix, iw = x0 + pad, w - 2 * pad
        widths = widths or [.46] + [.54 / (len(header) - 1)] * (len(header) - 1)
        xs = [ix]
        for f in widths:
            xs.append(xs[-1] + f * iw)
        align = ['c' if h == '#' else 'r' if ci > 0 and rows and all(NUMERIC.match(str(r[ci])) for r in rows) else 'l'
                 for ci, h in enumerate(header)]
        name_col = 1 if header and header[0] == '#' else 0
        fs, px = min(rh * 0.40, 36), 16

        def cell(txt, ci, yc, size, color, weight):
            cw = xs[ci + 1] - xs[ci] - 2 * px
            if align[ci] == 'r':
                _fit(ax, fig, xs[ci + 1] - px, yc, txt, size, cw, color, weight, 'right')
            elif align[ci] == 'c':
                _fit(ax, fig, (xs[ci] + xs[ci + 1]) / 2, yc, txt, size, cw, color, weight, 'center')
            else:
                _fit(ax, fig, xs[ci] + px, yc, txt, size, cw, color, weight, 'left')

        _grad(ax, ix, y0, iw, hh, 16, HEAD_A, HEAD_B)
        for ci, h in enumerate(header):
            cell(str(h), ci, y0 + hh / 2, fs * 0.86, 'white', 700)
        for r, cells in enumerate(rows):
            y = y0 + hh + r * rh
            yc = y + rh / 2
            if r % 2 == 1:
                _rr(ax, ix, y + 2, iw, rh - 4, 12, BAND_ROW, z=3)
            for ci, c in enumerate(cells):
                txt = str(c)
                given = colors[r][ci] if colors and colors[r] and colors[r][ci] else None
                if txt[:1] in '▲▼':
                    up = txt[0] == '▲'
                    col = '#0a7d33' if up else '#c0392b'
                    label = f'{txt[0]} {txt[1:].strip()}'
                    t = _text(ax, 0, yc, label, fs * 0.92, col, 800, ha='center')
                    pw, ph = _width(fig, t) + 42, rh * 0.6
                    cx = xs[ci + 1] - px - pw / 2 if align[ci] == 'r' else (xs[ci] + xs[ci + 1]) / 2 if align[ci] == 'c' else xs[ci] + px + pw / 2
                    _rr(ax, cx - pw / 2, yc - ph / 2, pw, ph, ph / 2, PILL_UP if up else PILL_DOWN, z=4)
                    t.set_x(cx)
                elif txt in ('—', ''):
                    cell(txt, ci, yc, fs, '#6b7684', 600)
                else:
                    color = given or ('#1f3a5f' if ci == name_col else '#222222')
                    cell(txt, ci, yc, fs, color, 800 if ci == name_col or align[ci] == 'r' else 700)
        fig.savefig(path, dpi=200)
        plt.close(fig)


def square_lines(path, title, subtitle, footer, series, ylabel='Tickets booked in the last hour (thousands)', tiles=None):
    """series: [(label, [(hour_float, value_k)], color, width)] on a 24-hour axis; the last series is the main one."""
    with plt.rc_context(FONT_RC):
        fig, ax = _canvas()
        top, bottom = _frame(ax, fig, title, subtitle, footer, tiles)
        x0, w, pad = 72, 1056, 22
        _panel(ax, x0, top, w, bottom - top)
        plot = fig.add_axes([(x0 + 92) / 1200, 1 - (bottom - 80) / 1200, (w - 92 - 50) / 1200, (bottom - 80 - (top + 92)) / 1200])
        plot.set_facecolor('none')
        for i, (label, pts, color, lw) in enumerate(series):
            if not pts:
                continue
            xs_, ys_ = np.array([p[0] for p in pts], float), np.array([p[1] for p in pts], float)
            main = i == len(series) - 1
            plot.plot(xs_, ys_, color=color, linewidth=lw * (1.15 if main else 1), label=label, solid_capstyle='round', zorder=4 if main else 3)
            if main:
                plot.fill_between(xs_, ys_, 0, color=ACC_B, alpha=0.2, linewidth=0, zorder=2)
                ok = ~np.isnan(ys_)
                if ok.any():
                    lx, ly = xs_[ok][-1], ys_[ok][-1]
                    plot.scatter([lx], [ly], s=95, color='white', zorder=5)
                    plot.scatter([lx], [ly], s=42, color=color, zorder=6)
                    plot.annotate(f'{ly:.1f}K', (lx, ly), xytext=(0, 14), textcoords='offset points', ha='center',
                                  fontsize=11, fontweight=800, color=color)
        plot.set_xlim(0, 24)
        plot.set_xticks(range(0, 25, 3))
        plot.set_xticklabels(['12a', '3a', '6a', '9a', '12p', '3p', '6p', '9p', '12a'])
        plot.set_ylim(bottom=0)
        plot.yaxis.set_major_formatter(lambda v, _: f'{v:g}K')
        plot.grid(axis='y', color='#e3e8ef', linewidth=0.9)
        for sp in ('top', 'right', 'left'):
            plot.spines[sp].set_visible(False)
        plot.spines['bottom'].set_color('#b8c2cf')
        plot.tick_params(colors='#6b7684', labelsize=10, length=0, pad=8)
        leg = plot.legend(frameon=False, fontsize=11, loc='upper left', bbox_to_anchor=(0.0, 1.13), ncol=2)
        for t in leg.get_texts():
            t.set_color('#222222')
            t.set_fontweight(600)
        fig.savefig(path, dpi=200)
        plt.close(fig)
