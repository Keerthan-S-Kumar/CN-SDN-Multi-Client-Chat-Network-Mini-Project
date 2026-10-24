"""OWNER: C. Orchestrates docs/eval-plan.md: for mode in MODES, scenario in SCENARIOS, run 1..3:
restart controller (CONTROLLER_MODE=mode), build triangle topo + QoS, start chat server on h1,
run bots (chat/bot.py on h2/h3/h4), iperf / link events per scenario, collect CSV + controller log into
eval/results/<mode>/<scenario>/run<k>.csv. CLI: sudo python3 eval/run_experiments.py [--only SCENARIO] [--mode M]"""

if __name__ == "__main__":
    raise SystemExit("TODO (C): implement runner")
