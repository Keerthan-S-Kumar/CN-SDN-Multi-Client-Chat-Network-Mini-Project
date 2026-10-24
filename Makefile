export PYTHONPATH := .
MODE ?= sdn
TOPO ?= single

controller:
	CONTROLLER_MODE=$(MODE) ryu-manager controller/app.py

topo:
	sudo env PYTHONPATH=. python3 topology/topo.py --topo $(TOPO) --cli

server:
	python3 chat/server.py --host 0.0.0.0

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
	python3 -m unittest discover -s tests -v

.PHONY: controller topo server mock flows meters queues metrics experiments report test
