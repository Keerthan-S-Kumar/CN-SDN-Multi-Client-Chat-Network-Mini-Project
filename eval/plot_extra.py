"""OWNER: C. Extra charts for docs/results.md. eval/plot.py (A) keeps the original four; these are the ones it does not draw:

  p95_all_scenarios.png  p95 latency of every scenario (including floodbig) on a log axis, with the spread over the runs.
                         The linear bars in latency_bar.png hide everything next to the 11 s baseline link-failure tail.
  latency_over_time.png  p95 latency per second for the congested, flood and floodbig runs, with the moments the
                         controller reacted (congestion_on, throttle_on) marked.
  reaction_times.png     how fast the controller reacts (link failure and congestion), from reaction_times.csv.

Reads eval/results/summary.csv, eval/results/reaction_times.csv (make report / eval/extra_metrics.py) and the run files
under eval/results/<mode>/<scenario>/. Run:  python3 eval/plot_extra.py
Modes keep the colours of eval/plot.py (blue = sdn, orange = baseline). The orange has low contrast on white, so every
chart carries value labels or a legend and never relies on colour alone.
"""
import argparse
import csv
import math
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import analyze  # noqa: E402
from common.constants import MODES, RESULTS_DIR  # noqa: E402

COLORS = {"sdn": "#1f77b4", "baseline": "#ff7f0e"}
INK, MUTED, GRID = "#222222", "#666666", "#dddddd"
EVENTS = {"congestion_on": "congestion response on", "throttle_on": "spammer throttled", "link_down": "link down"}
MIN_SAMPLES = 5          # a second with fewer deliveries than this is not plotted


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def style(ax, grid_axis="y"):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=INK)
    ax.grid(True, axis=grid_axis, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def load_summary(results):
    path = os.path.join(results, "summary.csv")
    if not os.path.exists(path):
        return {}
    with open(path, newline="") as f:
        return {(r["mode"], r["scenario"]): r for r in csv.DictReader(f)}


def fmt_ms(v):
    return "%.0f" % v if v >= 100 else "%.1f" % v


# ------------------------------------------------------------------ chart 1 ------------------------
def p95_all_scenarios(summary, out):
    scenarios = [s for s in ("idle", "congested", "flood", "floodbig", "linkfail")
                 if any((m, s) in summary for m in MODES)]
    if not scenarios:
        return False
    fig, ax = plt.subplots(figsize=(9, 4.6))
    w = 0.38
    for i, mode in enumerate(MODES):
        xs, ys, lo, hi = [], [], [], []
        for j, s in enumerate(scenarios):
            r = summary.get((mode, s))
            if not r or math.isnan(num(r.get("p95_ms_mean"))):
                continue
            m, sd = num(r["p95_ms_mean"]), num(r.get("p95_ms_std"))
            sd = 0.0 if math.isnan(sd) else sd
            xs.append(j + (i - 0.5) * w)
            ys.append(m)
            lo.append(min(sd, 0.9 * m))          # keep the error bar on the log axis
            hi.append(sd)
        ax.bar(xs, ys, w, yerr=[lo, hi], capsize=3, color=COLORS[mode], label=mode,
               error_kw={"ecolor": INK, "elinewidth": 1})
        for x, y, h in zip(xs, ys, hi):
            ax.text(x, (y + h) * 1.12, fmt_ms(y), ha="center", va="bottom", fontsize=8, color=INK)
    ax.set_yscale("log")
    ax.set_xticks(range(len(scenarios)))
    ax.set_xticklabels(scenarios)
    ax.set_ylabel("p95 message latency (ms, log scale)")
    ax.set_title("p95 latency by scenario (mean of the runs, bars show ± std)", color=INK, fontsize=11)
    ax.set_ylim(top=ax.get_ylim()[1] * 2)
    style(ax)
    ax.legend(loc="upper left", frameon=False)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return True


# ------------------------------------------------------------------ chart 2 ------------------------
def per_second_p95(rows):
    """[(second, p95 ms)] from analyze.read_run rows, seconds counted from the first message."""
    if not rows:
        return [], 0.0
    t0 = rows[0][0]
    bins = {}
    for s, r, _, _ in rows:
        if r is not None and r >= s:
            bins.setdefault(int(s - t0), []).append((r - s) * 1000.0)
    pts = []
    for sec in sorted(bins):
        v = sorted(bins[sec])
        if len(v) >= MIN_SAMPLES:
            pts.append((sec, analyze.percentile(v, 95)))
    return pts, t0


def latency_over_time(results, out):
    scenarios = [s for s in ("congested", "flood", "floodbig")
                 if any(analyze.run_files(results, m, s) for m in MODES)]
    if not scenarios:
        return False
    fig, axes = plt.subplots(len(scenarios), 1, figsize=(10, 3.1 * len(scenarios)), squeeze=False)
    for ax, sc in zip(axes[:, 0], scenarios):
        top = 0.0
        for mode in MODES:
            files = analyze.run_files(results, mode, sc)
            if not files:
                continue
            pts, t0 = per_second_p95(analyze.read_run(files[0]))
            if not pts:
                continue
            ax.plot([p[0] for p in pts], [p[1] for p in pts], color=COLORS[mode], linewidth=1.6, label=mode)
            top = max(top, max(p[1] for p in pts))
            if mode == "sdn":
                seen = set()
                for e in analyze.read_controller_log(files[0]):
                    kind, x = e.get("kind"), e.get("t", 0) - t0
                    if kind in EVENTS and kind not in seen and x >= 0:
                        seen.add(kind)
                        ax.axvline(x, color=MUTED, linestyle="--", linewidth=1)
                        ax.text(x + 0.3, 0.97, EVENTS[kind], transform=ax.get_xaxis_transform(),
                                va="top", ha="left", fontsize=8, color=INK,
                                bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.5})
        ax.set_title("%s (run 1)" % sc, loc="left", color=INK, fontsize=10)
        ax.set_ylabel("p95 latency (ms)")
        ax.set_ylim(0, top * 1.15 if top else 1)
        style(ax)
        ax.legend(loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=2, frameon=False)
    axes[-1, 0].set_xlabel("time since the first message (s)")
    fig.suptitle("p95 message latency per second, with the controller's reactions marked", color=INK, fontsize=11)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return True


# ------------------------------------------------------------------ chart 3 ------------------------
def reaction_times(results, out):
    path = os.path.join(results, "reaction_times.csv")
    if not os.path.exists(path):
        return False
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    rows = [r for r in rows if not math.isnan(num(r.get("mean_ms")))]
    if not rows:
        return False
    rows.reverse()                                   # first row at the top of a horizontal chart
    fig, ax = plt.subplots(figsize=(9, 0.9 + 0.75 * len(rows)))
    means = [num(r["mean_ms"]) for r in rows]
    stds = [0.0 if math.isnan(num(r["std_ms"])) else num(r["std_ms"]) for r in rows]
    lo = [min(s, 0.9 * m) for m, s in zip(means, stds)]
    ys = range(len(rows))
    ax.barh(list(ys), means, height=0.55, xerr=[lo, stds], color=COLORS["sdn"], capsize=3,
            error_kw={"ecolor": INK, "elinewidth": 1})
    for y, m, s in zip(ys, means, stds):
        ax.text((m + s) * 1.15, y, "%s ms" % fmt_ms(m), va="center", fontsize=9, color=INK)
    ax.set_xscale("log")
    ax.set_yticks(list(ys))
    ax.set_yticklabels(["%s: %s" % (r["scenario"], r["what"]) for r in rows], fontsize=9)
    ax.set_xlabel("time (ms, log scale)")
    ax.set_title("Controller reaction times (sdn, mean ± std)", color=INK, fontsize=11)
    ax.set_xlim(right=ax.get_xlim()[1] * 4)
    style(ax, grid_axis="x")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--results", default=os.path.join(os.path.dirname(HERE), RESULTS_DIR))
    a = ap.parse_args()
    summary = load_summary(a.results)
    jobs = [("p95_all_scenarios.png", lambda p: p95_all_scenarios(summary, p)),
            ("latency_over_time.png", lambda p: latency_over_time(a.results, p)),
            ("reaction_times.png", lambda p: reaction_times(a.results, p))]
    for name, fn in jobs:
        path = os.path.join(a.results, name)
        print(("wrote " + path) if fn(path) else "skipped %s: no data yet" % name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
