.PHONY: help install-dev format lint check test run dossier holdings web

PYTHON ?= python

help:
	@echo "Targets:"
	@echo "  install-dev            Install dev dependencies (ruff, pytest)"
	@echo "  format                 Format code with ruff"
	@echo "  lint                   Lint code with ruff (auto-fix)"
	@echo "  check                  Run format check + lint + tests"
	@echo "  test                   Run pytest"
	@echo "  run TICKER=            Report for one asset: ticker or fund CNPJ (default PETR4)"
	@echo "  dossier TICKER=        Show everything the agents will read (no LLM)"
	@echo "  holdings               List your stocks and funds (no LLM)"
	@echo "  web                    Wallet page on http://127.0.0.1:8000 to type in your holdings"

install-dev:
	$(PYTHON) -m pip install -e ".[dev]"

format:
	$(PYTHON) -m ruff format .

lint:
	$(PYTHON) -m ruff check --fix .

check:
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m ruff check .
	$(PYTHON) -m pytest

test:
	$(PYTHON) -m pytest

run:
	$(PYTHON) -m crewai_invest_reporter.main asset $(or $(TICKER),PETR4)

dossier:
	$(PYTHON) -m crewai_invest_reporter.main dossier $(or $(TICKER),PETR4)

holdings:
	$(PYTHON) -m crewai_invest_reporter.main holdings

web:
	$(PYTHON) -m crewai_invest_reporter.main web
