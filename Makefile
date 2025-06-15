lint:
	ruff check .

format:
	ruff check --select I --fix
	ruff format

unittest:
	pytest
