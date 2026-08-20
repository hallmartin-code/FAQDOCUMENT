.PHONY: install install-dev test cov lint check clean

PY ?= python

install:
	$(PY) -m pip install -e .

install-dev:
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest

cov:
	$(PY) -m pytest --cov=deckpager --cov-report=term-missing

lint:
	$(PY) -m mypy

check:
	$(PY) -m deckpager check

clean:
	rm -rf .pytest_cache .mypy_cache htmlcov .coverage build dist
