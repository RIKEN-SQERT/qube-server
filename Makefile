lint:
	uvx ruff check .

format:
	uvx ruff check --select I --fix
	uvx ruff format
