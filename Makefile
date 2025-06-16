lint:
	uv run ruff check .

format:
	uv run ruff check --select I --fix
	uv run ruff format

format-check:
	uv run ruff check --select I .
	uv run ruff format --check .

unittest:
	uv run pytest
