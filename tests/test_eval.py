"""OWNER: C. Tests for the evaluation code: the experiment plan and file merging in eval/run_experiments.py,
the statistics in eval/analyze.py, the reaction times in eval/extra_metrics.py and the parsers in tests/demo_auto.py.

No Mininet, no Ryu, no root: everything here works on small files made in a temp folder, so it also runs in GitHub Actions.
Run:  PYTHONPATH=. python3 -m pytest tests/test_eval.py -q
"""
import csv
import json
import math
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import analyze as A            # noqa: E402
import run_experiments as R    # noqa: E402

HEADER = ["t_send", "t_recv", "sender", "receiver", "kind", "room", "bytes"]


def write_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)


def write_jsonl(path, events):
    with open(path, "w") as f:
        for t, kind, detail in events:
            f.write(json.dumps({"t": t, "kind": kind, "detail": detail}) + "\n")


class TmpDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


class PlanTest(unittest.TestCase):
    def test_split_adds_up_and_is_even(self):
        for n in (1, 5, 10, 25, 50, 100):
            parts = R.split(n)
            self.assertEqual(sum(parts.values()), n)
            self.assertLessEqual(max(parts.values()) - min(parts.values()), 1)
        self.assertEqual(R.split(10), {"h2": 4, "h3": 3, "h4": 3})

    def test_idle_has_no_background_load(self):
        p = R.make_plan("idle", users=10)
        self.assertFalse(p["iperf"])
        self.assertFalse(p["linkfail"])
        self.assertEqual(sum(n for n, _ in p["bots"].values()), 10)
        self.assertEqual(p["rate"], 2.0)

    def test_congested_runs_iperf_and_linkfail_cuts_the_link(self):
        self.assertTrue(R.make_plan("congested")["iperf"])
        self.assertFalse(R.make_plan("congested")["linkfail"])
        self.assertTrue(R.make_plan("linkfail")["linkfail"])
        self.assertFalse(R.make_plan("linkfail")["iperf"])

    def test_flood_puts_the_spammer_alone_on_h4(self):
        p = R.make_plan("flood", users=10)
        self.assertEqual(p["bots"]["h4"], (0, True))
        self.assertEqual(p["bots"]["h2"], (5, False))
        self.assertEqual(p["bots"]["h3"], (5, False))
        self.assertEqual([h for h, (_, spam) in p["bots"].items() if spam], ["h4"])

    def test_scale_runs_use_n_bots_and_lower_rates_for_big_runs(self):
        for n in (5, 10, 25, 50, 100):
            p = R.make_plan("scale%d" % n)
            self.assertEqual(sum(k for k, _ in p["bots"].values()), n)
            self.assertFalse(p["iperf"] or p["linkfail"])
        self.assertEqual(R.make_plan("scale25")["rate"], 2.0)
        self.assertEqual(R.make_plan("scale50")["rate"], 1.0)
        self.assertEqual(R.make_plan("scale100")["rate"], 0.5)

    def test_explicit_rate_wins(self):
        self.assertEqual(R.make_plan("scale100", rate=3.0)["rate"], 3.0)

    def test_unknown_scenario_is_refused(self):
        with self.assertRaises(ValueError):
            R.make_plan("nonsense")


class MergeTest(TmpDirCase):
    def test_merge_run_sorts_by_send_time_and_counts_rows(self):
        parts = os.path.join(self.tmp, "parts")
        os.makedirs(parts)
        write_csv(os.path.join(parts, "h2.csv"), [["3.0", "3.1", "a", "b", "room", "g", "64"],
                                                  ["1.0", "1.1", "a", "b", "room", "g", "64"]])
        write_csv(os.path.join(parts, "h3.csv"), [["2.0", "", "c", "b", "room", "g", "64"]])
        stem = os.path.join(self.tmp, "run1")
        self.assertEqual(R.merge_run(stem, parts), 3)
        with open(stem + ".csv", newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], HEADER)
        self.assertEqual([r[0] for r in rows[1:]], ["1.0", "2.0", "3.0"])

    def test_write_events_merges_and_sorts_and_skips_bad_lines(self):
        log = os.path.join(self.tmp, "controller.jsonl")
        with open(log, "w") as f:
            f.write(json.dumps({"t": 5.0, "kind": "flow_install", "detail": {"ms": 10}}) + "\n")
            f.write("not json\n")
            f.write(json.dumps({"t": 1.0, "kind": "link_down", "detail": {}}) + "\n")
        stem = os.path.join(self.tmp, "run1")
        n = R.write_events(stem, log, [(3.0, "iperf_start", {"mbps": 12})])
        self.assertEqual(n, 3)
        with open(stem + ".jsonl") as f:
            events = [json.loads(line) for line in f]
        self.assertEqual([e["kind"] for e in events], ["link_down", "iperf_start", "flow_install"])

    def test_write_events_without_a_controller_log(self):
        stem = os.path.join(self.tmp, "run1")
        self.assertEqual(R.write_events(stem, os.path.join(self.tmp, "missing.jsonl"), [(1.0, "x", {})]), 1)


class AnalyzeTest(TmpDirCase):
    def test_percentile(self):
        vals = sorted(range(1, 101))
        self.assertEqual(A.percentile(vals, 50), 50)
        self.assertEqual(A.percentile(vals, 95), 95)
        self.assertEqual(A.percentile(vals, 99), 99)
        self.assertEqual(A.percentile([7.0], 99), 7.0)
        self.assertTrue(math.isnan(A.percentile([], 50)))

    def test_mean_std_ignores_nan_and_handles_one_value(self):
        m, s = A.mean_std([2.0, 4.0, float("nan")])
        self.assertAlmostEqual(m, 3.0)
        self.assertAlmostEqual(s, math.sqrt(2.0))
        self.assertEqual(A.mean_std([5.0]), (5.0, 0.0))
        m, s = A.mean_std([float("nan")])
        self.assertTrue(math.isnan(m) and math.isnan(s))

    def test_read_run_merges_duplicates_and_a_delivered_row_wins(self):
        p = os.path.join(self.tmp, "run1.csv")
        write_csv(p, [["1.0", "", "a", "b", "room", "g", "64"],        # lost copy
                      ["1.0", "1.5", "a", "b", "room", "g", "64"],      # same message, delivered
                      ["2.0", "", "a", "c", "room", "g", "64"]])
        rows = A.read_run(p)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][1], 1.5)
        self.assertIsNone(rows[1][1])

    def test_run_metrics_latency_loss_and_controller_times(self):
        p = os.path.join(self.tmp, "run1.csv")
        write_csv(p, [["10.00", "10.01", "a", "b", "room", "g", "64"],     # 10 ms
                      ["10.50", "10.52", "a", "b", "room", "g", "64"],     # 20 ms
                      ["11.00", "11.03", "a", "b", "room", "g", "64"],     # 30 ms
                      ["12.00", "", "a", "b", "room", "g", "64"]])         # lost
        write_jsonl(os.path.join(self.tmp, "run1.jsonl"),
                    [(1.0, "flow_install", {"ms": 10}), (2.0, "flow_install", {"ms": 30}),
                     (3.0, "reroute_done", {"ms": 50})])
        m = A.run_metrics(p)
        self.assertAlmostEqual(m["p50_ms"], 20.0, places=3)
        self.assertAlmostEqual(m["loss_pct"], 25.0)
        self.assertAlmostEqual(m["flow_install_ms"], 20.0)
        self.assertAlmostEqual(m["reroute_ms"], 50.0)

    def test_run_metrics_without_controller_events_gives_nan(self):
        p = os.path.join(self.tmp, "run1.csv")
        write_csv(p, [["1.0", "1.01", "a", "b", "room", "g", "64"]])
        m = A.run_metrics(p)
        self.assertTrue(math.isnan(m["flow_install_ms"]))
        self.assertTrue(math.isnan(m["reroute_ms"]))


class ExtraMetricsTest(unittest.TestCase):
    def setUp(self):
        try:
            import extra_metrics
        except ImportError:
            self.skipTest("eval/extra_metrics.py is not in this checkout")
        self.X = extra_metrics

    def test_failover_chain(self):
        ev = [{"t": 100.0, "kind": "reroute_done", "detail": {"ms": 90}},          # earlier, must be ignored
              {"t": 110.0, "kind": "link_down_cmd", "detail": {}},
              {"t": 110.02, "kind": "link_down", "detail": {}},
              {"t": 110.08, "kind": "reroute_done", "detail": {"ms": 60}}]
        r = self.X.failover(ev)
        self.assertAlmostEqual(r["detect_ms"], 20.0, places=3)
        self.assertAlmostEqual(r["install_ms"], 60.0)
        self.assertAlmostEqual(r["total_ms"], 80.0, places=3)

    def test_failover_is_none_when_the_chain_is_incomplete(self):
        self.assertIsNone(self.X.failover([]))
        self.assertIsNone(self.X.failover([{"t": 1.0, "kind": "link_down_cmd", "detail": {}}]))
        self.assertIsNone(self.X.failover([{"t": 1.0, "kind": "link_down_cmd", "detail": {}},
                                           {"t": 1.1, "kind": "link_down", "detail": {}}]))

    def test_congestion_reaction(self):
        ev = [{"t": 49.0, "kind": "congestion_on", "detail": {}},                  # before iperf, must be ignored
              {"t": 50.0, "kind": "iperf_start", "detail": {}},
              {"t": 52.5, "kind": "congestion_on", "detail": {}}]
        self.assertAlmostEqual(self.X.congestion(ev), 2500.0, places=3)
        self.assertIsNone(self.X.congestion([{"t": 50.0, "kind": "iperf_start", "detail": {}}]))
        self.assertIsNone(self.X.congestion([]))


class DemoParsersTest(unittest.TestCase):
    def setUp(self):
        try:
            import demo_auto
        except ImportError:
            self.skipTest("tests/demo_auto.py is not in this checkout")
        self.D = demo_auto

    def test_port_to_uses_the_triangle_links(self):
        self.assertEqual(self.D.port_to(2, 3), 3)
        self.assertEqual(self.D.port_to(2, 1), 2)
        self.assertEqual(self.D.port_to(1, 2), 2)
        with self.assertRaises(ValueError):
            self.D.port_to(1, 1)

    def test_route_port(self):
        flows = (" cookie=0x1000, table=1, n_packets=35, priority=10,dl_dst=00:00:00:00:00:03 actions=output:3\n"
                 " cookie=0x0, table=1, priority=0 actions=CONTROLLER:65535\n")
        self.assertEqual(self.D.route_port(flows, "00:00:00:00:00:03"), 3)
        self.assertIsNone(self.D.route_port(flows, "00:00:00:00:00:09"))

    def test_queue_stats_ping_loss_and_block_rules(self):
        q = self.D.queue_stats('port "s1-eth1" queue 0: bytes=100, pkts=2, errors=0\n'
                               'port "s1-eth1" queue 1: bytes=5, pkts=1, errors=0\n')
        self.assertEqual(q, {0: (100, 2), 1: (5, 1)})
        self.assertEqual(self.D.ping_loss("100 packets transmitted, 100 received, 0% packet loss"), 0.0)
        self.assertEqual(self.D.ping_loss("3 packets transmitted, 2 received, 33.3% packet loss"), 33.3)
        self.assertIsNone(self.D.ping_loss("garbage"))
        rules = " cookie=0x8000, table=0, priority=400,tcp,nw_src=10.0.0.4,tp_dst=5000 actions=drop\n" * 4
        self.assertEqual(self.D.count_block_rules(rules, "10.0.0.4"), 4)
        self.assertEqual(self.D.count_block_rules(rules, "10.0.0.2"), 0)


if __name__ == "__main__":
    unittest.main()
