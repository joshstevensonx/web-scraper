.PHONY: install ui test lint clean

install:
	./install.sh --yes

ui:
	.venv/bin/webscraper ui

test:
	.venv/bin/python -m pytest -q

clean:
	rm -rf build dist *.egg-info .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
