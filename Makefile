# One stack: api + icd-api + postgres + azurite (docker/compose.yml).
# The full autoMCCD dev stack uses the same host ports; `make down` there first.
COMPOSE = docker compose -f docker/compose.yml --env-file .env
API_PORT ?= $(shell sed -n 's/^API_PORT=//p' .env 2>/dev/null)
API_URL ?= http://localhost:$(or $(API_PORT),8000)

.PHONY: install lint test up down logs ps smoke eval notebooks docs

install:
	uv sync --extra dev

lint:
	uv run ruff check .
	uv run ruff format --check .

test:
	uv run pytest

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100 api

ps:
	$(COMPOSE) ps

smoke:
	API_URL=$(API_URL) sh scripts/smoke_test.sh

# The mini harness runs in-process: it needs .env and icd-api published on the host.
# EVAL_SET=robust make eval
EVAL_SET ?= headline
eval:
	uv run python -m automccd_lite.eval --set $(EVAL_SET) $(EVAL_ARGS)

# Executes both notebooks against the running stack and saves their outputs.
# --inexact keeps the other group installed, so docs and notebooks do not evict each other.
notebooks:
	uv sync --inexact --extra dev --group notebooks
	uv run jupyter nbconvert --execute --inplace --ExecutePreprocessor.timeout=1800 --ExecutePreprocessor.kernel_name=python3 notebooks/how-to-guide.ipynb notebooks/mccd_guide.ipynb

docs:
	uv sync --inexact --extra dev --group docs
	uv run sphinx-build -b html docs docs/_build
