export PYTHONPATH := .
MODE ?= sdn
RYU ?= $(shell command -v ryu-manager 2>/dev/null || echo $(HOME)/ryuenv/bin/ryu-manager)
PY ?= $(dir $(RYU))python3
TOPO ?= single

controller:
	CONTROLLER_MODE=$(MODE) $(RYU) controller/app.py

topo:
	sudo env PYTHONPATH=. python3 topology/topo.py --topo $(TOPO) --cli

server:
	python3 chat/server.py --host 0.0.0.0

web-chat:
	python3 chat/web_gateway.py --server $(or $(SERVER),10.0.0.1) --port 8000

mock:
	python3 tools/mock_controller.py

flows:
	sudo ovs-ofctl -O OpenFlow13 dump-flows s1

meters:
	sudo ovs-ofctl -O OpenFlow13 dump-meters s1

queues:
	sudo ovs-ofctl -O OpenFlow13 queue-stats s1

metrics:
	curl -s http://127.0.0.1:8080/metrics

experiments:
	sudo env PYTHONPATH=. python3 eval/run_experiments.py

report:
	python3 eval/analyze.py && python3 eval/plot.py

test:
	$(PY) -m unittest controller.test_logic -v
	@if ls tests/test_*.py >/dev/null 2>&1; then python3 -m unittest discover -s tests -v; fi

.PHONY: web-chat controller topo server mock flows meters queues metrics experiments report test
