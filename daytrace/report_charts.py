"""Chart rendering for offline reports — matplotlib PNGs that mirror
the dashboard's '直方图' (stacked bar) and '分布' (donut + legend) views.

Two charts per report:
  • 直方图 — per-bucket stacked bar (daily: 24 hours; weekly: 7 days);
            bars stacked by **task** (work_items.title), unlinked events
            downgrade to project_guess so the legend is meaningful.
  • 分布   — donut over the same period total, with a side legend listing
            each task + hours + percentage; total hours go in the donut's
            center hole.

We reuse the dashboard's 5-minute-slot proportional split for hours so
the totals match what the live page shows. Palette is `TIMELINE_PALETTE`
from dashboard/server.py so colors line up too.

Returns raw PNG bytes per chart — the caller writes them to disk, embeds
in the email, or uploads to Feishu.
"""
from __future__ import annotations

import io
import sqlite3
from collections import Counter, defaultdict
from typing import Any

# Headless backend — important when running under launchd (no DISPLAY)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager, rcParams


# ───── Font setup (Chinese-capable) ──────────────────────────────────────

def _setup_fonts() -> None:
    """Pick a Chinese-capable font that's likely installed on macOS.
    matplotlib's default DejaVu Sans renders Chinese as tofu boxes."""
    candidates = [
        "PingFang SC",
        "PingFang HK",
        "STHeiti",
        "Heiti TC",
        "Arial Unicode MS",
        "Noto Sans CJK SC",
        "Hiragino Sans GB",
        "Microsoft YaHei",
    ]
    available = {f.name for f in font_manager.fontManager.ttflist}
    chosen = next((c for c in candidates if c in available), None)
    if chosen:
        rcParams["font.sans-serif"] = [chosen, "DejaVu Sans"]
        rcParams["axes.unicode_minus"] = False


_setup_fonts()


# ───── Palette (matches dashboard/server.py:TIMELINE_PALETTE) ────────────

_PALETTE = [
    "#2f6fed",  # blue
    "#f59e0b",  # amber
    "#16a34a",  # green
    "#ef4444",  # red
    "#7b61ff",  # purple
    "#14b8a6",  # teal
    "#d946ef",  # magenta
    "#0ea5e9",  # sky
    "#84cc16",  # lime
    "#f43f5e",  # rose
]
_OTHER_COLOR = "#cbd5e1"


# ───── Data loading ──────────────────────────────────────────────────────

def _load_events_for_range(con: sqlite3.Connection,
                           date_from: str, date_to: str) -> list[dict]:
    """Return events with start in [date_from, date_to]. Includes id +
    start + project_guess. We need start for hour/day bucketing."""
    rows = con.execute(
        """
        SELECT id, start, project_guess
          FROM events
         WHERE substr(start, 1, 10) BETWEEN ? AND ?
         ORDER BY start
        """,
        (date_from, date_to),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_task_map(con: sqlite3.Connection, event_ids: list[str]) -> dict[str, str]:
    """For events linked to a work_item, return {event_id: task_title}.
    Mirrors dashboard's _enrich_events_with_tasks but **with the user's
    requested downgrade** — unlinked events are NOT bucketed under
    "未对应任务"; the caller falls back to project_guess instead.

    Also respects work_items.yaml `collapse_in_dim` so e.g. all 33
    review rows fold into a single "审稿" label."""
    if not event_ids:
        return {}
    collapse_map: dict[str, str] = {}
    try:
        from daytrace.work_items import load_config
        cfg = load_config()
        for t in (cfg or {}).get("tables", []):
            if t.get("collapse_in_dim"):
                collapse_map[t["key"]] = t.get("collapsed_label") or t.get("name") or t["key"]
    except Exception:
        pass

    out: dict[str, str] = {}
    chunk = 900
    for i in range(0, len(event_ids), chunk):
        sub = event_ids[i:i+chunk]
        ph = ",".join("?" * len(sub))
        for r in con.execute(
            f"""
            SELECT l.event_id, w.title, w.table_key
              FROM event_work_item_links l
              JOIN work_items w ON w.record_id = l.record_id
             WHERE l.event_id IN ({ph})
            """, sub
        ).fetchall():
            tk = r["table_key"] or "tasks"
            if tk in collapse_map:
                out[r["event_id"]] = collapse_map[tk]
            elif r["title"]:
                out[r["event_id"]] = r["title"]
    return out


def _label_for_event(ev: dict, task_map: dict[str, str]) -> str:
    """Task title if linked; otherwise project_guess (downgrade)."""
    t = task_map.get(ev["id"])
    if t:
        return t
    proj = (ev.get("project_guess") or "").strip()
    return proj or "misc"


# ───── Hours-by-bucket aggregation (5-min slot proportional) ─────────────

_SLOT_MIN = 5


def _safe_minute(start: str | None) -> int | None:
    """Parse 'YYYY-MM-DDTHH:MM:SS' → minutes since 00:00. None on bad input."""
    if not start or len(start) < 16:
        return None
    try:
        h = int(start[11:13]); m = int(start[14:16])
        return h * 60 + m
    except Exception:
        return None


def _shifted_day_of(start: str, boundary_hour: int = 4) -> str | None:
    """Returns YYYY-MM-DD of the *work day* the event belongs to (events
    between 00:00 and boundary_hour belong to the prior calendar day)."""
    if not start or len(start) < 10:
        return None
    try:
        date_part = start[:10]
        h = int(start[11:13])
        if h < boundary_hour:
            from datetime import date as _date, timedelta
            d = _date.fromisoformat(date_part) - timedelta(days=1)
            return d.isoformat()
        return date_part
    except Exception:
        return None


def _per_bucket_hours(events: list[dict], task_map: dict[str, str],
                     *, bucket_of) -> tuple[dict[Any, dict[str, float]], list[str], dict[str, float]]:
    """Distribute 5-min slots' airtime across the labels present in each
    slot, proportionally by event count. Returns:
      • per_bucket[bucket] = {label: hours}
      • labels_sorted_by_total_desc (the legend order)
      • totals_by_label = aggregate across all buckets
    """
    # First collect per-(bucket, slot_idx) the label counts
    per_slot: dict[tuple[Any, int], Counter] = defaultdict(Counter)
    for ev in events:
        m = _safe_minute(ev.get("start"))
        if m is None:
            continue
        b = bucket_of(ev)
        if b is None:
            continue
        slot = m // _SLOT_MIN
        label = _label_for_event(ev, task_map)
        per_slot[(b, slot)][label] += 1

    per_bucket: dict[Any, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    totals: dict[str, float] = defaultdict(float)
    for (b, _slot), counts in per_slot.items():
        s = sum(counts.values())
        if s <= 0:
            continue
        for label, c in counts.items():
            mins = _SLOT_MIN * (c / s)
            per_bucket[b][label] += mins / 60.0
            totals[label] += mins / 60.0

    labels_ordered = [k for k, _ in sorted(totals.items(), key=lambda kv: -kv[1])]
    return ({b: dict(d) for b, d in per_bucket.items()},
            labels_ordered,
            dict(totals))


def _palette_for(labels: list[str]) -> dict[str, str]:
    pal: dict[str, str] = {}
    for i, lab in enumerate(labels):
        pal[lab] = _PALETTE[i] if i < len(_PALETTE) else _OTHER_COLOR
    return pal


# ───── Rendering ─────────────────────────────────────────────────────────

def _render_stacked_bar_png(
    *, bucket_keys: list, bucket_xticks: list[str], bucket_secondary: list[str] | None,
    per_bucket: dict, labels: list[str], palette: dict[str, str],
    title: str, ylabel: str = "小时",
) -> bytes:
    """One stacked-bar PNG: x is buckets (days or hours), bars stack
    `labels` in descending-total order so the biggest contributor anchors
    the base of each bar."""
    fig, ax = plt.subplots(figsize=(11.5, 4.8), dpi=150)
    bottoms = [0.0] * len(bucket_keys)
    x = list(range(len(bucket_keys)))
    # Stack: smallest at top → biggest at bottom. So iterate reverse of
    # `labels` (which is desc-by-total) and put biggest on top of stack.
    # Actually the convention in the dashboard is biggest at the *bottom*;
    # do the same — iterate labels in order, stacking from bottom up.
    for lab in labels:
        heights = [per_bucket.get(b, {}).get(lab, 0.0) for b in bucket_keys]
        ax.bar(x, heights, bottom=bottoms, label=lab,
               color=palette.get(lab, _OTHER_COLOR), width=0.78,
               edgecolor="white", linewidth=0.6)
        bottoms = [bo + h for bo, h in zip(bottoms, heights)]

    # Per-bucket total label on top of each bar
    for i, total in enumerate(bottoms):
        if total > 0:
            ax.text(i, total + max(bottoms) * 0.015, f"{total:.1f}h",
                    ha="center", va="bottom", fontsize=10, fontweight="bold",
                    color="#2b2722")

    ax.set_xticks(x)
    if bucket_secondary:
        labels_two_line = [f"{a}\n{b}" for a, b in zip(bucket_xticks, bucket_secondary)]
        ax.set_xticklabels(labels_two_line, fontsize=10)
    else:
        ax.set_xticklabels(bucket_xticks, fontsize=10)
    ax.set_ylabel(ylabel, fontsize=10, color="#6b6052")
    ax.set_title(title, fontsize=13, fontweight="bold", color="#1a1814",
                 loc="left", pad=12)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#e0d7c5")
    ax.spines["bottom"].set_color("#e0d7c5")
    ax.tick_params(colors="#6b6052")
    ax.grid(axis="y", linestyle="--", alpha=0.4, color="#e0d7c5")
    ax.set_axisbelow(True)

    # Legend below the chart so wide labels don't squeeze the plot area
    if labels:
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18),
                  ncol=min(len(labels), 4), frameon=False, fontsize=9.5,
                  handlelength=1.2, handleheight=1.0, columnspacing=1.4)

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white",
                edgecolor="none")
    plt.close(fig)
    return buf.getvalue()


def _render_donut_png(
    *, totals: dict[str, float], labels: list[str], palette: dict[str, str],
    title: str, total_label: str = "TASK",
) -> bytes:
    """Donut + legend with horizontal bars. Mirrors dashboard '分布' view."""
    grand_total = sum(totals.values())
    if grand_total <= 0:
        # Empty placeholder
        fig, ax = plt.subplots(figsize=(11.5, 4.8), dpi=150)
        ax.text(0.5, 0.5, "(本期无数据)", ha="center", va="center",
                fontsize=14, color="#9b8f7d")
        ax.axis("off")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
        plt.close(fig)
        return buf.getvalue()

    fig = plt.figure(figsize=(11.5, 5.0), dpi=150)
    # Left: donut. Right: legend with hbars.
    ax_pie = fig.add_axes([0.04, 0.08, 0.32, 0.84])
    ax_leg = fig.add_axes([0.42, 0.06, 0.55, 0.88])

    sizes = [totals[lab] for lab in labels]
    colors = [palette.get(lab, _OTHER_COLOR) for lab in labels]
    wedges, _ = ax_pie.pie(
        sizes, colors=colors, startangle=90, counterclock=False,
        wedgeprops=dict(width=0.42, edgecolor="white", linewidth=2.2),
    )
    # Center label
    ax_pie.text(0, 0.08, f"{grand_total:.1f}h", ha="center", va="center",
                fontsize=22, fontweight="bold", color="#1a1814")
    ax_pie.text(0, -0.20, total_label, ha="center", va="center",
                fontsize=11, color="#9b8f7d")
    ax_pie.set_title(title, fontsize=13, fontweight="bold", color="#1a1814",
                     loc="left", pad=4, x=-0.1)

    # Legend axis (no frame; manually drawn rows)
    ax_leg.set_xlim(0, 100)
    n = len(labels)
    ax_leg.set_ylim(-0.5, max(n - 0.5, 0.5))
    ax_leg.invert_yaxis()
    ax_leg.axis("off")
    max_hours = max(sizes) if sizes else 1.0
    for i, lab in enumerate(labels):
        v = totals[lab]
        pct = v / grand_total * 100
        color = palette.get(lab, _OTHER_COLOR)
        # Color square
        ax_leg.add_patch(plt.Rectangle((1, i - 0.18), 1.6, 0.36,
                                       facecolor=color, edgecolor="none",
                                       transform=ax_leg.transData))
        # Label
        ax_leg.text(4, i, lab, va="center", fontsize=10.5, color="#1a1814")
        # Bar background
        ax_leg.add_patch(plt.Rectangle((38, i - 0.16), 40, 0.32,
                                       facecolor="#f3ecd9", edgecolor="none",
                                       transform=ax_leg.transData))
        # Bar fill
        ax_leg.add_patch(plt.Rectangle((38, i - 0.16), 40 * v / max_hours, 0.32,
                                       facecolor=color, edgecolor="none",
                                       transform=ax_leg.transData))
        # Hours
        ax_leg.text(82, i, f"{v:.1f}h", va="center", fontsize=10.5,
                    color="#1a1814", fontweight="bold")
        # Percent
        ax_leg.text(94, i, f"{pct:.0f}%", va="center", fontsize=10.5,
                    color="#9b8f7d")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white",
                edgecolor="none")
    plt.close(fig)
    return buf.getvalue()


# ───── Public API ────────────────────────────────────────────────────────

def render_daily_charts(con: sqlite3.Connection, date: str) -> dict[str, bytes]:
    """Two PNGs for the daily report — histogram (24 hour buckets) + donut."""
    # Daily events span the *shifted* day, which can include the 04:00–03:59
    # window of the calendar day. Pull a 2-day window to cover events that
    # belong to `date` but landed at HH < 04:00 the next morning.
    from datetime import date as _date, timedelta
    d = _date.fromisoformat(date)
    events = _load_events_for_range(con, (d - timedelta(days=0)).isoformat(),
                                          (d + timedelta(days=1)).isoformat())
    # Keep only events whose shifted-day == date
    events = [e for e in events if _shifted_day_of(e["start"]) == date]
    task_map = _load_task_map(con, [e["id"] for e in events])

    # Bucket = hour-of-day in 04..03 shifted axis (04, 05, …, 23, 00, …, 03)
    hour_order = [(4 + i) % 24 for i in range(24)]

    def hour_bucket(ev: dict) -> int | None:
        m = _safe_minute(ev["start"])
        return None if m is None else (m // 60)

    per_bucket, labels, totals = _per_bucket_hours(events, task_map, bucket_of=hour_bucket)
    palette = _palette_for(labels)

    hist_png = _render_stacked_bar_png(
        bucket_keys=hour_order,
        bucket_xticks=[f"{h:02d}" for h in hour_order],
        bucket_secondary=None,
        per_bucket=per_bucket, labels=labels, palette=palette,
        title=f"每日 · 任务时间直方图 · {date}",
        ylabel="小时",
    )
    donut_png = _render_donut_png(
        totals=totals, labels=labels, palette=palette,
        title=f"每日 · 任务分布 · {date}",
        total_label="TASK",
    )
    return {"hist": hist_png, "donut": donut_png}


def render_weekly_charts(con: sqlite3.Connection, week: str) -> dict[str, bytes]:
    """Two PNGs for the weekly report — 7-day histogram + donut."""
    from daytrace.db import iso_week_to_date_range
    monday, sunday, days = iso_week_to_date_range(week)
    events = _load_events_for_range(con, monday, sunday)
    # Strip out events whose shifted-day falls outside the week (edge cases)
    events = [e for e in events if (_shifted_day_of(e["start"]) or "") in days]
    task_map = _load_task_map(con, [e["id"] for e in events])

    def day_bucket(ev: dict) -> str | None:
        return _shifted_day_of(ev["start"])

    per_bucket, labels, totals = _per_bucket_hours(events, task_map, bucket_of=day_bucket)
    palette = _palette_for(labels)

    weekday_labels_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    hist_png = _render_stacked_bar_png(
        bucket_keys=days,
        bucket_xticks=weekday_labels_cn,
        bucket_secondary=[d[5:] for d in days],  # MM-DD under the day name
        per_bucket=per_bucket, labels=labels, palette=palette,
        title=f"每周 · 任务时间直方图 · {week}",
        ylabel="小时",
    )
    donut_png = _render_donut_png(
        totals=totals, labels=labels, palette=palette,
        title=f"每周 · 任务分布 · {week}",
        total_label="TASK",
    )
    return {"hist": hist_png, "donut": donut_png}
