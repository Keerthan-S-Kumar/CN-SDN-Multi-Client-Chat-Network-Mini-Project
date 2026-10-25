"""OWNER: A. summary.csv -> latency_bar.png, scalability.png, linkfail_timeline.png, congestion_cdf.png
(docs/eval-plan.md). sdn vs baseline on every plot; axes labelled with units."""
import argparse
import csv
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import analyze  # noqa: E402
from common.constants import MODES, SCENARIOS, RESULTS_DIR  # noqa: E402

NAN = float("nan")
COLORS = {"sdn": "tab:blue", "baseline": "tab:orange"}


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return NAN


def err(x):
    v = num(x)
    return 0.0 if v != v else v


def load_summary(results):
    data = {}
    with open(os.path.join(results, "summary.csv"), newline="") as f:
        for r in csv.DictReader(f):
            data[(r["mode"], r["scenario"])] = r
    return data


def latency_bar(summary, out):
    scenarios = [s for s in ("idle", "congested", "flood", "linkfail")
                 if any((m, s) in summary for m in MODES)]
    if not scenarios:
        return False
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    w = 0.38
    for ax, p in zip(axes, ("p50", "p95", "p99")):
        for i, mode in enumerate(MODES):
            xs = [j + (i - 0.5) * w for j in range(len(scenarios))]
            ys = [num(summary.get((mode, s), {}).get(p + "_ms_mean")) for s in scenarios]
            es = [err(summary.get((mode, s), {}).get(p + "_ms_std")) for s in scenarios]
            ax.bar(xs, ys, w, yerr=es, capsize=3, label=mode, color=COLORS[mode])
        ax.set_xticks(range(len(scenarios)))
        ax.set_xticklabels(scenarios)
        ax.set_ylabel("message latency (ms)")
        ax.set_title(p.upper() + " latency")
    axes[0].legend()
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    return True


def scalability(summary, out):
    ns = sorted((int(s[5:]), s) for s in SCENARIOS if s.startswith("scale"))
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4))
    drawn = False
    for mode in MODES:
        pts = [(n, summary[(mode, s)]) for n, s in ns if (mode, s) in summary]
        if not pts:
            continue
        drawn = True
        xs = [n for n, _ in pts]
        for p, ls in (("p50", "-"), ("p95", "--")):
            a1.errorbar(xs, [num(d[p + "_ms_mean"]) for _, d in pts],
                        yerr=[err(d[p + "_ms_std"]) for _, d in pts], marker="o", ls=ls,
                        color=COLORS[mode], capsize=3, label=f"{mode} {p}")
        a2.errorbar(xs, [num(d["throughput_msgs_s_mean"]) for _, d in pts],
                    yerr=[err(d["throughput_msgs_s_std"]) for _, d in pts], marker="o",
                    color=COLORS[mode], capsize=3, label=mode)
    if not drawn:
        return False
    a1.set_xlabel("number of bot clients")
    a1.set_ylabel("message latency (ms)")
    a1.set_title("Latency vs number of clients")
    a1.legend()
    a2.set_xlabel("number of bot clients")
    a2.set_ylabel("delivered messages per second")
    a2.set_title("Throughput vs number of clients")
    a2.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    return True


def linkfail_timeline(results, out):
    fig, axes = plt.subplots(len(MODES), 1, figsize=(10, 6), sharex=True)
    drawn = False
    for ax, mode in zip(axes, MODES):
        files = analyze.run_files(results, mode, "linkfail")
        rows = analyze.read_run(files[0]) if files else []
        ax.set_title(mode + (" (run 1)" if rows else ": no data"))
        ax.set_ylabel("message latency (ms)")
        if not rows:
            continue
        drawn = True
        t0 = rows[0][0]
        ok = [(s - t0, (r - s) * 1000) for s, r, _, _ in rows if r is not None]
        lost = [s - t0 for s, r, _, _ in rows if r is None]
        top = max([y for _, y in ok], default=1.0)
        ax.scatter([x for x, _ in ok], [y for _, y in ok], s=6, color=COLORS[mode],
                   label="delivered")
        if lost:
            ax.scatter(lost, [top * 1.05] * len(lost), s=12, marker="x", color="red",
                       label="lost")
        for e in analyze.read_controller_log(files[0]):
            if e.get("kind") in ("link_down", "link_up"):
                x = e["t"] - t0
                ax.axvline(x, ls="--", color="gray")
                ax.text(x, top * 1.12, e["kind"], rotation=90, va="top", fontsize=8)
        ax.legend(loc="upper left")
    if not drawn:
        return False
    axes[-1].set_xlabel("time since first message (s)")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    return True


def congestion_cdf(results, out):
    fig, ax = plt.subplots(figsize=(7, 4.5))
    drawn = False
    for mode in MODES:
        lat = []
        for p in analyze.run_files(results, mode, "congested"):
            lat += [(r - s) * 1000 for s, r, _, _ in analyze.read_run(p)
                    if r is not None and r > s]
        if not lat:
            continue
        drawn = True
        lat.sort()
        ax.plot(lat, [(i + 1) / len(lat) for i in range(len(lat))],
                label=mode, color=COLORS[mode])
    if not drawn:
        return False
    ax.set_xscale("log")
    ax.set_xlabel("message latency (ms, log scale)")
    ax.set_ylabel("fraction of messages with latency <= x")
    ax.set_title("Latency CDF under congestion")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=os.path.join(os.path.dirname(HERE), RESULTS_DIR))
    a = ap.parse_args()
    summary = load_summary(a.results)
    jobs = [
        ("latency_bar.png", lambda p: latency_bar(summary, p)),
        ("scalability.png", lambda p: scalability(summary, p)),
        ("linkfail_timeline.png", lambda p: linkfail_timeline(a.results, p)),
        ("congestion_cdf.png", lambda p: congestion_cdf(a.results, p)),
    ]
    for name, fn in jobs:
        path = os.path.join(a.results, name)
        print(("wrote " + path) if fn(path) else f"skipped {name}: no data yet")
    plt.close("all")


if __name__ == "__main__":
    main()