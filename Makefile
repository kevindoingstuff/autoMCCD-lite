# Thin aliases over the `automccd-lite` command (src/automccd_lite/cli.py).
# The WHO icd-api is the only container; `serve` starts it through Docker.

.PHONY: install lint test run smoke eval notebooks docs

install:
	uv sync --extra dev

lint:
	uv run ruff check .
	uv run ruff format --check .

test:
	uv run pytest

# Starts the icd-api container if needed, creates the store, runs the api on 127.0.0.1:8000.
run:
	uv run automccd-lite serve

# Against a running `make run`: readyz -> /certify S01 -> the row -> the file.
smoke:
	uv run automccd-lite smoke

# The mini harness runs in-process: it needs .env and the icd-api container
# (`uv run automccd-lite icd start`). EVAL_SET=robust make eval
EVAL_SET ?= headline
eval:
	uv run automccd-lite eval --set $(EVAL_SET) $(EVAL_ARGS)

# Executes both notebooks against a running `make run` and saves their outputs.
# --inexact keeps the other group installed, so docs and notebooks do not evict each other.
notebooks:
	uv sync --inexact --extra dev --group notebooks
	uv run jupyter nbconvert --execute --inplace --ExecutePreprocessor.timeout=1800 --ExecutePreprocessor.kernel_name=python3 notebooks/how-to-guide.ipynb notebooks/mccd_guide.ipynb

docs:
	uv sync --inexact --extra dev --group docs
	uv run sphinx-build -b html docs docs/_build
