# qube-server

A labRAD server for QuBE environments

## Getting Started

### Clone this repository:

Clone this repository to your local machine:

```bash
git clone https://github.com/qipe-nlab/qube-server
```

### Prepare virtual environment

Use `uv` to create a virtual environment and install all the necessary Python dependencies from the lockfile:

```bash
uv sync
```

For developers,

```bash
uv sync --dev
```

Make sure you have uv installed.
If you don't have it yet, please follow the [official installation guide](https://docs.astral.sh/uv/getting-started/installation/).


### execute the server

Finally, you can start the server with the following command:

```bash
uv run qube_server
```

## For Developers

To configure your development environment, create a `.env` file.
An example, `.env.example`, is included in the project.
As the first step, run `cp .env.example .env` and then modify the new `.env` file.

To run linter and formatter, execute `make` command as following:

```
make lint
make format
```

To run unittest,

```
make unittest
```
