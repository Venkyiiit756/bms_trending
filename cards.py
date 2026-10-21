"""Table and chart images in one consistent style: navy header, thin borders, green/red changes."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

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


def _square(title, subtitle, footer):
    """A 1200x1200 canvas with a title block and footer; returns (fig, content box [x, y, w, h])."""
    fig = plt.figure(figsize=(6, 6), dpi=200)
    fig.patch.set_facecolor('white')
    fig.text(0.5, 0.935, title, ha='center', va='center', fontsize=17, fontweight='bold', color=NAVY)
    fig.text(0.5, 0.885, subtitle, ha='center', va='center', fontsize=11.5, color=MUTE)
    fig.text(0.5, 0.045, footer, ha='center', va='center', fontsize=9, color=MUTE)
    return fig, [0.07, 0.10, 0.86, 0.74]


def square_table(path, title, subtitle, footer, header, rows, colors=None, widths=None):
    fig, (x, y, w, h) = _square(title, subtitle, footer)
    n = len(rows) + 1
    row_h = min(h / n, 0.085)
    th = row_h * n
    ax = fig.add_axes([x, y + (h - th) / 2, w, th])
    ax.axis('off')
    widths = widths or [.46] + [.54 / (len(header) - 1)] * (len(header) - 1)
    tb = ax.table(cellText=rows, colLabels=header, cellLoc='center', bbox=[0, 0, 1, 1], colWidths=widths)
    tb.auto_set_font_size(False)
    tb.set_fontsize(13 if len(header) <= 3 else 11.5)
    _style(tb, colors)
    fig.savefig(path, facecolor='white')
    plt.close(fig)


def square_lines(path, title, subtitle, footer, series, ylabel='Tickets booked in the last hour (thousands)'):
    """series: [(label, [(hour_float, value_k)], color, width)] plotted on a 24-hour axis."""
    fig, (x, y, w, h) = _square(title, subtitle, footer)
    ax = fig.add_axes([x + 0.06, y + 0.05, w - 0.06, h - 0.08])
    for label, pts, color, lw in series:
        if pts:
            ax.plot([p[0] for p in pts], [p[1] for p in pts], color=color, linewidth=lw, label=label)
    ax.set_xlim(0, 24)
    ax.set_xticks(range(0, 25, 3))
    ax.set_xticklabels(['12a', '3a', '6a', '9a', '12p', '3p', '6p', '9p', '12a'])
    ax.set_ylim(bottom=0)
    ax.set_ylabel(ylabel, color=MUTE, fontsize=9)
    ax.grid(axis='y', color='#e3e8ef', linewidth=0.8)
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    for s in ('left', 'bottom'):
        ax.spines[s].set_color('#b8c2cf')
    ax.tick_params(colors=MUTE, labelsize=9)
    ax.legend(frameon=False, fontsize=10, loc='upper left')
    fig.savefig(path, facecolor='white')
    plt.close(fig)
