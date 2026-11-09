.PHONY: help install-dev format lint check test daily web run dossier

PYTHON ?= python

help:
	@echo "Targets:"
	@echo "  install-dev            Install dev dependencies (ruff, pytest)"
	@echo "  format                 Format code with ruff"
	@echo "  lint                   Lint code with ruff (auto-fix)"
	@echo "  check                  Run format check + lint + tests"
	@echo "  test                   Run pytest"
	@echo "  daily                  Today's digests + emails to subscribers (cron, trading days)"
	@echo "  web                    Sign-up page on http://127.0.0.1:8000"
	@echo "  run [TICKER=]          Full reports (rating saved) for the watchlist, or just TICKER"
	@echo "  dossier TICKER=        Show everything the agents will read (no LLM)"

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

daily:
	$(PYTHON) -m crewai_invest_reporter.main daily

web:
	$(PYTHON) -m crewai_invest_reporter.main web

run:
	$(PYTHON) -m crewai_invest_reporter.main report $(TICKER)

dossier:
	$(PYTHON) -m crewai_invest_reporter.main dossier $(or $(TICKER),PETR4)
