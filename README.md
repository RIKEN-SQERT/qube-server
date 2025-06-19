# qube-server

A labRAD server for QuBE environments

## Getting Started

### 1. Clone this repository:

Clone this repository to your local machine:

```bash
git clone https://github.com/qipe-nlab/qube-server
```

### 2. Download prebuilt dependencies

run the provided script to download and extract prebuilt packages required by the server:

```bash
./download_and_extract_prebuilt.sh
```

### 3. Prepare virtual environment

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


### 4. execute the server

Finally, you can start the server with the following command:

```bash
uv run qube_server
```

## For Developers

To run linter and formatter, execute `make` command as following:

```
make lint
make format
```

To run unittest,

```
make unittest
```
