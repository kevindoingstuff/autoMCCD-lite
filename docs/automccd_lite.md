---
module: automccd_lite
generated_from: df3b4722789f32f68be49485dea6282a496e39ae
generated_at: 2026-10-27
---

# automccd_lite User Guide

## Overview

![automccd_lite in the system](_static/diagrams/automccd_lite/system.drawio.png)

The package turns one clinical record into a WHO Medical Certificate of Cause of Death coded in ICD-11, and tells you whether a human must look at it before it counts. A language model writes the draft; the code checks it against ten rules and the WHO's own coding tools, gives the model one chance to fix what failed, and then decides the route from what it could verify. The api calls it once per request, the evaluation harness calls it once per case, and the notebooks call its functions one at a time.

The pipeline has four nodes, each a section of this guide:

1. **Draft the MCCD**: renders your record as text, pairs it with the fixed system prompt, and makes one model call with strict JSON-schema decoding.
2. **Verify**: checks the schema and the rules V1 to V10 against your record, then sends the failures back to the model once and adopts the answer only if it still answers the same question.
3. **Code**: codes every term with autocode, selects the underlying cause with DORIS, runs CoDEdit twice, and asks the model once for the standard name of a term autocode could not match.
4. **Generate the MCCD**: turns every finding into a reason code, decides `auto` or `review`, builds the certificate, and in the api writes the blob and the provenance row.

You provide a record with its notes and the demographics; the package produces a certificate with a route, its reasons, the codes and the versions that produced it.

## Inputs and Outputs

### Inputs

1. The request body of `POST /certify`, one record (JSON)

| Field | Type | Description | Source |
|---|---|---|---|
| `record_id` | `str` | Your identifier for the record; it comes back on the certificate. | `src/automccd_lite/models.py:45` |
| `sex` | `"male" \| "female" \| null` | Needed by DORIS; a missing value routes to review. | `src/automccd_lite/models.py:46` |
| `age`, `age_unit` | `float`, `minutes` to `years` | Given together or not at all. | `src/automccd_lite/models.py:47` |
| `context` | `str \| null` | Registry metadata the model may read but not cite. | `src/automccd_lite/models.py:49` |
| `notes` | `list[{name, text}]` | The free text, one entry per note; names must be unique. | `src/automccd_lite/models.py:50` |

2. `config/default.yaml` (the tuning by stage, see Quickstart)
3. `prompts/mccd/v4.7.md` and `prompts/schemas/mccd.schema.json` (the system prompt and the strict output schema, read once at import) (`src/automccd_lite/prompt.py:48`)
4. `data/code_cache.json` (the autocode cache, read at startup and rewritten on every new term) (`src/automccd_lite/code.py:207`)
5. The evaluation data under `data/`: `scenarios.json`, `demographics.json`, `eval_sets.json`, `gold/*.json`, `robust_sample.json` (`src/automccd_lite/eval.py:42`)

### Outputs

1. The response body of `POST /certify` (JSON)

| Field | Type | Description | Source |
|---|---|---|---|
| `record_id` | `str` | Echoed from the request. | `src/automccd_lite/models.py:184` |
| `route` | `"auto" \| "review"` | The decision. | `src/automccd_lite/models.py:185` |
| `reasons` | `list[str]` | Every reason code that kept the certificate from `auto`. | `src/automccd_lite/route.py:14` |
| `certificate` | `dict \| null` | The MCCD the model wrote, in the schema's shape. | `src/automccd_lite/models.py:187` |
| `coding` | `CodingSummary \| null` | `ucod_stem`, `ucod_full`, `codes`, `code_scores`, `codedit_issues`, `doris_warning`. | `src/automccd_lite/models.py:163` |
| `versions` | `Versions` | `prompt_version`, `prompt_sha1`, `schema_version`, `model`, `icd_release`, `automccd_version`. | `src/automccd_lite/models.py:172` |
| `usage` | `Usage` | Prompt, completion and reasoning tokens, and the number of model calls. | `src/automccd_lite/models.py:79` |
| `timings_ms` | `dict[str, int]` | Milliseconds per stage: `draft`, `validate`, `revise`, `code`, `route`. | `src/automccd_lite/pipeline.py:39` |

2. One row in the Postgres table `certificates` (the response plus the provenance the response leaves out)

| Field | Type | Description | Source |
|---|---|---|---|
| `record_id`, `route`, `reasons`, `draft_status` | `str`, `str`, `jsonb`, `str` | The decision and the model status. | `src/automccd_lite/db.py:34` |
| `versions`, `usage`, `timings_ms` | `jsonb` | As in the response. | `src/automccd_lite/db.py:41` |
| `certificate` | `jsonb` | The MCCD; it carries patient text. | `src/automccd_lite/db.py:42` |
| `validation` | `jsonb` | The gate's verdict, both revisions, the model's chain; it carries patient text. | `src/automccd_lite/db.py:43` |
| `coding` | `jsonb` | The raw autocode, DORIS and CoDEdit replies. | `src/automccd_lite/db.py:44` |
| `icd_error` | `str \| null` | `icd_unavailable` or `icd_error` when the WHO container failed. | `src/automccd_lite/db.py:45` |
| `blob_path` | `str \| null` | Where the JSON copy went. | `src/automccd_lite/db.py:48` |

3. One blob per certificate, `certificates/{yyyy-mm-dd}/{hhmmss}Z_{record_id}.json` (the response body, pretty-printed) (`src/automccd_lite/storage.py:22`)
4. `runs/<set>_<hhmmss>Z/results.json` and `summary.md` from the evaluation harness (`src/automccd_lite/eval.py:462`)
5. JSON log lines on stdout, one per certified record, restricted to an allowlist of fields that never carry patient text (`src/automccd_lite/log.py:16`)

## Folder Structure

```
src/automccd_lite/
├── __init__.py       # the version string
├── pipeline.py       # certify_record: the four nodes in order; Deps
│
│   # Draft the MCCD
├── prompt.py         # build_prompt: the system prompt, its sha1, the rendered record
├── draft.py          # draft_certificate: the one function that calls the model
│   # Verify
├── validate.py       # validate_draft: rules V1 to V10, the schema check, the triage route
├── revise.py         # revise_draft (rules) and revise_terms (terminology)
│   # Code
├── code.py           # code_draft: the WHO icd-api client, the autocode cache
│   # Generate the MCCD
├── route.py          # route_draft: the reason codes and the two routes
├── certificate.py    # generate_certificate: the Certificate, the blob, the row, the response body
│
│   # Supporting modules
├── models.py         # Record, Draft, DraftValidation, CodedDraft, Route, Decision, Certificate
├── config.py         # settings(): config/default.yaml by stage plus the environment
├── log.py            # JSON logs with the PHI allowlist
├── storage.py        # BlobStorage: Azurite or Azure Blob through a connection string
├── db.py             # the certificates table, create_all, the session, the store
├── api.py            # FastAPI: POST /certify, /healthz, /readyz; Runtime built at startup
└── eval.py           # the mini harness: headline and robust sets, scoring, runs/ output
```

## Quickstart

:::{note}
The api needs the WHO icd-api container, Postgres and Azurite beside it; `make up` starts all four. The icd-api downloads its release on the first start and takes about a minute to turn healthy, and `/readyz` reports it as not ready until then.
:::

### 1. Give it a model endpoint

- Copy `.env.example` to `.env` and set `OPENAI_BASE_URL` (e.g. `https://api.openai.com/v1`) and `OPENAI_API_KEY`. Any OpenAI-compatible endpoint works; the model name comes from the config file, and `LLM_MODEL` in `.env` overrides it (e.g. `gpt-5-nano-2025-08-07`).

### 2. Tune the stages, if you want to

- Edit `config/default.yaml`: `draft.model`, `draft.reasoning_effort` (e.g. `minimal`, the setting the baseline numbers were measured with), `revise.max_attempts` (set `0` to switch the rule revision off), `code.min_match_score` (e.g. `0.5`: an autocode match below it triggers the terminology revision and the `autocode_low_score` reason).

### 3. Run it

```
make up          # Makefile:19
make smoke       # Makefile:31
```

`make smoke` ends with `SMOKE OK` after it has certified `data/records/S01.json` and found the row and the blob. Then send your own record:

```
curl -s -X POST localhost:8000/certify -H 'content-type: application/json' --data-binary @data/records/S02.json
```

### 4. Measure it

```
make eval                     # Makefile:37, the 17 headline cases
EVAL_SET=robust make eval     # the 34 stress cases, scored on behaviour
```

The run folder is printed last; read its `summary.md`. The harness runs in-process and reaches the icd-api on the host port, so the stack must be up.

### Configuration

| Key | Type | Default | Description | Source |
|---|---|---|---|---|
| `prompt.version` | `str` | `"4.7"` | Which `prompts/mccd/v<version>.md` to load. | `config/default.yaml:5` |
| `prompt.schema` | `str` | `"mccd"` | Which `prompts/schemas/<schema>.schema.json` to load. | `config/default.yaml:6` |
| `draft.model` | `str` | `"gpt-5-nano-2025-08-07"` | The model name; `LLM_MODEL` overrides it. | `config/default.yaml:9` |
| `draft.temperature` | `float \| null` | `null` | Not sent when null; reasoning models reject it. | `config/default.yaml:10` |
| `draft.seed` | `int \| null` | `20251023` | Best effort; this model is not deterministic with it. | `config/default.yaml:11` |
| `draft.reasoning_effort` | `str \| null` | `minimal` | Not sent when null; `minimal` costs no reasoning tokens. | `config/default.yaml:12` |
| `draft.max_completion_tokens` | `int` | `8000` | Must cover hidden reasoning; 2000 returned nothing. | `config/default.yaml:13` |
| `draft.timeout_s`, `draft.max_retries` | `float`, `int` | `120`, `4` | Per model call; the SDK retries 429 and 5xx. | `config/default.yaml:14` |
| `revise.max_attempts` | `int` | `1` | Rule revisions per record; `0` switches them off. | `config/default.yaml:18` |
| `revise.term_max_attempts` | `int` | `1` | Terminology revisions per record. | `config/default.yaml:19` |
| `code.icd_release` | `str` | `"2026-01"` | Must match the release the icd-api image serves. | `config/default.yaml:22` |
| `code.min_match_score` | `float` | `0.5` | The autocode score under which a term counts as unmatched. | `config/default.yaml:24` |
| `code.timeout_s`, `code.max_retries` | `float`, `int` | `30`, `2` | Per icd-api call. | `config/default.yaml:25` |
| `OPENAI_BASE_URL`, `OPENAI_API_KEY` | env | none | The model endpoint; never logged. | `src/automccd_lite/config.py:85` |
| `LLM_MODEL` | env | unset | Overrides `draft.model`. | `src/automccd_lite/config.py:82` |
| `ICD_API_URL` | env | `http://localhost:8082` | The icd-api as seen from the process; compose sets `http://icd-api:80` inside the container. | `src/automccd_lite/config.py:87` |
| `AZURE_STORAGE_CONNECTION_STRING`, `AZURE_STORAGE_CONTAINER` | env | none, `certificates` | The blob account and container. | `src/automccd_lite/config.py:88` |
| `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` | env | from `.env.example` | Read by psycopg; the URL names no host. | `src/automccd_lite/db.py:23` |
| `API_HOST`, `API_PORT` | env | `0.0.0.0`, `8000` | Where uvicorn listens. | `src/automccd_lite/api.py:147` |

## Pipeline

The four nodes have [a page of their own](pipeline.md): what each one does to your record, its inputs and outputs, its settings and its diagrams. The table says where each node lives.

| Node | Made of | Description |
|---|---|---|
| Draft the MCCD | `src/automccd_lite/prompt.py:89`, `src/automccd_lite/draft.py:125` | The record becomes a prompt; the model returns a draft under the strict schema. |
| Verify | `src/automccd_lite/validate.py:569`, `src/automccd_lite/revise.py:74` | The ten rules, and one bounded revision when they fail. |
| Code | `src/automccd_lite/code.py:257`, `src/automccd_lite/revise.py:171` | The WHO tools, with one bounded terminology revision inside. |
| Generate the MCCD | `src/automccd_lite/route.py:14`, `src/automccd_lite/certificate.py:66` | The reason codes, the route, the certificate and where it goes. |

## Supporting Modules

| Module | Lives in | Description |
|---|---|---|
| models | `src/automccd_lite/models.py:41` | The types that flow between the nodes. |
| config | `src/automccd_lite/config.py:79` | The settings, from the YAML file and the environment. |
| log | `src/automccd_lite/log.py:64` | JSON log lines filtered through an allowlist. |
| storage | `src/automccd_lite/storage.py:27` | The blob adapter. |
| db | `src/automccd_lite/db.py:30` | The certificates table and its store. |
| api | `src/automccd_lite/api.py:125` | The three routes and the runtime built at startup. |
| eval | `src/automccd_lite/eval.py:462` | The mini harness. |

### models

The pydantic types every node reads and writes, so a node's contract is visible in one file. The MCCD itself stays a dict: it is the JSON the model returned under the strict schema. The request body is validated into `Record`; note names must be unique and age needs its unit (`src/automccd_lite/models.py:53`).

### config

Reads the tuning by stage from the YAML file and the endpoints and secrets from the environment, once, and caches the result as one `Settings` object (`src/automccd_lite/config.py:79`). It loads `.env` from the repository root first, so the api, the harness and the notebooks all see the same values.

### log

Writes one JSON line per event and drops every field that is not on the allowlist, so a call site can only log less, never leak more (`src/automccd_lite/log.py:64`). The HTTP client loggers stay at WARNING because the icd-api URLs carry certified terms, and the uvicorn access log is disabled because request URLs can carry identifiers; the api logs its own `certified` event instead (`src/automccd_lite/log.py:59`).

### storage

Writes the response body as JSON to a blob container through a connection string, creating the container on first use (`src/automccd_lite/storage.py:33`). The blob name `certificates/{yyyy-mm-dd}/{hhmmss}Z_{record_id}.json` carries the time, so a re-run of the same record never collides with the first (`src/automccd_lite/storage.py:22`).

### db

The one table, `certificates`, its engine and session, `create_all`, and the store that inserts a row from a `Certificate` (`src/automccd_lite/db.py:83`). There are no migrations: the api creates the table at startup. The connection URL is `postgresql+psycopg://` with no host, so psycopg reads `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER` and `PGPASSWORD` from the environment and no secret is ever in a string this package logs (`src/automccd_lite/db.py:23`).

### api

The FastAPI app: `POST /certify` runs one record in the request, in one transaction (`src/automccd_lite/api.py:125`); `/healthz` answers at once; `/readyz` probes Postgres, the icd-api, the blob container and the model configuration and returns `503` until every component is true (`src/automccd_lite/api.py:136`). A `ModelUnavailable` becomes `503 {"detail": "model unavailable; retry later"}` with nothing stored (`src/automccd_lite/api.py:119`). The `Runtime` built at startup holds the model client, the icd client, the cache and the storage (`src/automccd_lite/api.py:54`). `API_HOST` and `API_PORT` say where uvicorn listens (`src/automccd_lite/api.py:147`).

### eval

The mini harness. It builds the 17 headline cases from the scenarios, the demographics and the gold, or loads the 34 stress cases, runs `certify_record` in-process with no storage, scores each case, and writes `runs/<set>_<hhmmss>Z/results.json` and `summary.md` (`src/automccd_lite/eval.py:462`). The gold is coded through the same `code_draft`, so the UCOD comparison is code against code (`src/automccd_lite/eval.py:258`). The headline case ids are `<parent>-clean`, the ids the full autoMCCD harness uses, so the two runs compare case by case (`src/automccd_lite/eval.py:118`).

![eval](_static/diagrams/automccd_lite/eval.drawio.png)

## Data Flow

![POST /certify](_static/diagrams/automccd_lite/sequence.drawio.png)

1. The api validates the body into a `Record` and opens one database transaction (`src/automccd_lite/api.py:72`).
2. **Draft the MCCD**: `build_prompt` renders the record and `draft_certificate` makes one model call under the strict schema (`src/automccd_lite/pipeline.py:49`).
3. **Verify**: `validate_draft` checks the schema and the rules V1 to V10 against the notes (`src/automccd_lite/pipeline.py:57`); when a rule failed, `revise_draft` sends the failures back once and adopts the answer only if it keeps the assessment (`src/automccd_lite/pipeline.py:61`).
4. **Code**: for a certifiable draft, `revise_terms` restates weak terms once, then `code_draft` runs autocode, DORIS and CoDEdit twice; a container failure becomes `icd_unavailable` or `icd_error` instead of an exception (`src/automccd_lite/pipeline.py:65`).
5. **Generate the MCCD**: `route_draft` turns every finding into a reason code and picks `auto` or `review` (`src/automccd_lite/pipeline.py:81`); `generate_certificate` writes the blob, inserts the row, and returns the `Certificate`; the api commits the transaction and answers with `as_response` (`src/automccd_lite/certificate.py:94`).
