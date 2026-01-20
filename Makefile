.PHONY: help install-dev format lint check test run

PYTHON ?= python

help:
	@echo "Targets:"
	@echo "  install-dev  Install dev dependencies (ruff, pytest)"
	@echo "  format       Format code with ruff"
	@echo "  lint         Lint code with ruff"
	@echo "  check        Run format check + lint"
	@echo "  test         Run pytest"
	@echo "  run TICKER=  Run the crew for a ticker (default PETR4)"

install-dev:
	$(PYTHON) -m pip install -e ".[dev]"

format:
	$(PYTHON) -m ruff format .

lint:
	$(PYTHON) -m ruff check --fix .

check:
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m ruff check .

test:
	$(PYTHON) -m pytest -q

run:
	$(PYTHON) -m crewai_invest_reporter.main $(or $(TICKER),PETR4)
