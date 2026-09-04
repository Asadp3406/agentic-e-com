.PHONY: setup data resolve graph detect gnn-eval investigate eval api web web-install demo

VENV := .venv
PYTHON := $(VENV)/bin/python

setup:
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q --upgrade pip
	$(VENV)/bin/pip install -q -r requirements.txt

data:
	$(PYTHON) data/generate.py

resolve:
	$(PYTHON) resolve/entity_resolution.py

graph:
	$(PYTHON) -m graph.build

detect:
	$(PYTHON) -m detect.community
	$(PYTHON) -m baseline.txn_classifier
	$(PYTHON) -m detect.scorer

gnn-eval:
	$(PYTHON) -m eval.gnn_eval

investigate:
	$(PYTHON) -m agent.policy

eval:
	$(PYTHON) -m eval.gnn_eval
	$(PYTHON) -m eval.run_eval

api:
	$(VENV)/bin/uvicorn api.main:app --reload --port 8000

web-install:
	cd web && npm install

web:
	cd web && npm run dev

demo:
	$(PYTHON) -m demo.run_demo
