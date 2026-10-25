"""OWNER: A. eval/results/<mode>/<scenario>/run*.csv (+ controller logs) -> eval/results/summary.csv
(mean/std of p50,p95,p99 latency, throughput, loss, flow_install_ms, reroute_ms per mode+scenario)."""
import argparse
import csv
import glob
import json
import math
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from common.constants import RESULTS_DIR, MODES, SCENARIOS  # noqa: E402

NAN = float("nan")
FIELDS = ["p50_ms", "p95_ms", "p99_ms", "throughput_msgs_s", "loss_pct",
          "flow_install_ms", "reroute_ms"]


def percentile(sorted_vals, p):
    if not sorted_vals:
        return NAN
    k = max(0, min(len(sorted_vals) - 1, math.ceil(p / 100 * len(sorted_vals)) - 1))
    return sorted_vals[k]


def read_run(path):
    """-> list of (t_send, t_recv or None, sender, receiver) sorted by t_send.
    Rows duplicated by several bot processes are merged; a delivered row wins over a lost one."""
    best = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            key = (r["sender"], r["receiver"], r["t_send"])
            t_recv = float(r["t_recv"]) if r["t_recv"] else None
            if key not in best or (best[key][1] is None and t_recv is not None):
                best[key] = (float(r["t_send"]), t_recv, r["sender"], r["receiver"])
    return sorted(best.values(), key=lambda x: x[0])


def read_controller_log(run_csv):
    """Controller events for this run: any run<k>*.jsonl next to run<k>.csv."""
    stem = os.path.splitext(run_csv)[0]
    events = []
    for p in sorted(glob.glob(stem + "*.jsonl")):
        with open(p) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        pass
    return events


def run_files(results, mode, scenario):
    return sorted(glob.glob(os.path.join(results, mode, scenario, "run*.csv")))


def avg(vals):
    return statistics.mean(vals) if vals else NAN


def run_metrics(path):
    rows = read_run(path)
    lat = sorted((r - s) * 1000 for s, r, _, _ in rows if r is not None)
    total = len(rows)
    span = max(1.0, rows[-1][0] - rows[0][0]) if rows else 1.0
    events = read_controller_log(path)

    def ms(kind):
        return [e["detail"]["ms"] for e in events
                if e.get("kind") == kind and "ms" in e.get("detail", {})]

    return {
        "p50_ms": percentile(lat, 50),
        "p95_ms": percentile(lat, 95),
        "p99_ms": percentile(lat, 99),
        "throughput_msgs_s": len(lat) / span,
        "loss_pct": 100.0 * (total - len(lat)) / total if total else NAN,
        "flow_install_ms": avg(ms("flow_install")),
        "reroute_ms": avg(ms("reroute_done")),
    }


def mean_std(vals):
    vals = [v for v in vals if not math.isnan(v)]
    if not vals:
        return NAN, NAN
    return statistics.mean(vals), (statistics.stdev(vals) if len(vals) > 1 else 0.0)


def fmt(x):
    return "" if math.isnan(x) else round(x, 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=os.path.join(ROOT, RESULTS_DIR))
    a = ap.parse_args()

    out_rows = []
    for mode in MODES:
        for sc in SCENARIOS:
            files = run_files(a.results, mode, sc)
            if not files:
                continue
            per_run = [run_metrics(p) for p in files]
            row = {"mode": mode, "scenario": sc, "runs": len(files)}
            for f in FIELDS:
                m, s = mean_std([x[f] for x in per_run])
                row[f + "_mean"] = fmt(m)
                row[f + "_std"] = fmt(s)
            out_rows.append(row)

    if not out_rows:
        sys.exit(f"no run*.csv files found under {a.results}/<mode>/<scenario>/")

    cols = ["mode", "scenario", "runs"] + [f + s for f in FIELDS for s in ("_mean", "_std")]
    path = os.path.join(a.results, "summary.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(out_rows)

    print(f"{'mode':9}{'scenario':11}{'runs':5}{'p50 ms':>9}{'p95 ms':>9}{'p99 ms':>9}{'loss %':>8}")
    for r in out_rows:
        print(f"{r['mode']:9}{r['scenario']:11}{r['runs']:<5}{r['p50_ms_mean']!s:>9}"
              f"{r['p95_ms_mean']!s:>9}{r['p99_ms_mean']!s:>9}{r['loss_pct_mean']!s:>8}")
    print("wrote", path)


if __name__ == "__main__":
    main()