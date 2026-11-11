#!/usr/bin/env python3
"""OWNER: C. Reaction times that eval/analyze.py does not compute, from the run*.jsonl files of the sdn runs.

  failover (linkfail)  : the runner cuts s1-s2 (link_down_cmd) -> the controller sees the port go down (link_down)
                         -> the new routes are installed and confirmed (reroute_done). Reported as detection, install
                         and total, all in ms.
  congestion (congested): the runner starts iperf (iperf_start) -> the controller turns the congestion response on
                         (congestion_on). Reported in ms.

Run:  python3 eval/extra_metrics.py          (reads eval/results/sdn/{linkfail,congested}/run*.jsonl)
Writes eval/results/reaction_times.csv and prints a markdown table for docs/results.md.
The baseline has no controller reactions, so only sdn runs are read.
"""
import argparse
import csv
import glob
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_events(path):
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return sorted(out, key=lambda e: e.get("t", 0))


def first_after(events, kind, t0):
    for e in events:
        if e.get("kind") == kind and e.get("t", 0) >= t0:
            return e
    return None


def failover(events):
    """-> dict(detect_ms, install_ms, total_ms) for the first link_down_cmd, or None if the chain is incomplete."""
    cmd = first_after(events, "link_down_cmd", 0)
    if cmd is None:
        return None
    seen = first_after(events, "link_down", cmd["t"])
    done = first_after(events, "reroute_done", seen["t"]) if seen else None
    if seen is None or done is None:
        return None
    return {"detect_ms": (seen["t"] - cmd["t"]) * 1000.0,
            "install_ms": float(done.get("detail", {}).get("ms", (done["t"] - seen["t"]) * 1000.0)),
            "total_ms": (done["t"] - cmd["t"]) * 1000.0}


def congestion(events):
    """-> reaction time in ms from iperf_start to the first congestion_on, or None."""
    start = first_after(events, "iperf_start", 0)
    if start is None:
        return None
    on = first_after(events, "congestion_on", start["t"])
    return None if on is None else (on["t"] - start["t"]) * 1000.0


def stat(vals):
    if not vals:
        return None, None
    return statistics.mean(vals), (statistics.stdev(vals) if len(vals) > 1 else 0.0)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--results", default=os.path.join(ROOT, "eval", "results"))
    a = ap.parse_args()

    fo, cg = [], []
    for p in sorted(glob.glob(os.path.join(a.results, "sdn", "linkfail", "run*.jsonl"))):
        r = failover(read_events(p))
        if r:
            fo.append(r)
    for p in sorted(glob.glob(os.path.join(a.results, "sdn", "congested", "run*.jsonl"))):
        r = congestion(read_events(p))
        if r is not None:
            cg.append(r)
    if not fo and not cg:
        sys.exit("no usable sdn linkfail/congested run*.jsonl files under %s" % a.results)

    rows = []
    for key, label in (("detect_ms", "link down seen by the controller"),
                       ("install_ms", "new routes installed and confirmed"),
                       ("total_ms", "failover, link cut to routes confirmed")):
        m, s = stat([r[key] for r in fo])
        if m is not None:
            rows.append(("linkfail", label, len(fo), m, s))
    m, s = stat(cg)
    if m is not None:
        rows.append(("congested", "iperf start to congestion response on", len(cg), m, s))

    path = os.path.join(a.results, "reaction_times.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scenario", "what", "runs", "mean_ms", "std_ms"])
        for sc, what, n, m, s in rows:
            w.writerow([sc, what, n, round(m, 1), round(s, 1)])

    print("| Scenario | What is timed | Runs | Mean ± std (ms) |")
    print("|---|---|---|---|")
    for sc, what, n, m, s in rows:
        print("| %s | %s | %d | %.1f ± %.1f |" % (sc, what, n, m, s))
    print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
