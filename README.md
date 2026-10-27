# autoMCCD-lite

The answer key for the MCCD proof of concept. Clinical record text goes in; a WHO Medical
Certificate of Cause of Death (MCCD) coded in ICD-11 comes out, with a route (`auto` or `review`)
and the reason codes behind it.

```
build_prompt -> draft_certificate -> validate_draft -> revise_draft
             -> code_draft (with revise_terms inside) -> route_draft -> generate_certificate
```

One module per stage under `src/automccd_lite/`; `pipeline.py` calls them in order. The model
proposes a certificate, the code checks it against ten rules and the WHO's own coding tools, and
only a certificate that passes everything may skip the human.

## Run it in ten minutes

You need Docker (for the WHO icd-api container, the only thing that runs in one), `uv`, and a key
for any OpenAI-compatible model endpoint.

```bash
uv run automccd-lite serve
```

The first run creates `.env` from `.env.example` and stops; fill `OPENAI_BASE_URL` and
`OPENAI_API_KEY` and run it again. It then starts the icd-api container (the image is pulled once
and the release takes about a minute to load), creates the `store/` folder, and runs the api on
`127.0.0.1:8000`. In a second terminal:

```bash
uv run automccd-lite smoke
```

certifies `data/records/S01.json` through `POST /certify`, finds the row in `store/certificates.sqlite`
and the JSON file under `store/certificates/`, and ends with `SMOKE OK`. Send a record yourself:

```bash
curl -s -X POST localhost:8000/certify -H 'content-type: application/json' --data-binary @data/records/S02.json
```

| command | what it does |
|---|---|
| `uv run automccd-lite serve` (`make run`) | start the icd-api container if needed, create the store, run the api |
| `uv run automccd-lite icd start\|stop\|status` | the container alone, for the eval and the notebooks |
| `uv run automccd-lite smoke` (`make smoke`) | the end-to-end check against a running serve |
| `make test` | the unit tests: validators V1 to V10, the routing table, the gold checks |
| `make eval` | the mini harness on the 17 headline cases; `EVAL_SET=robust make eval` for the 34 stress cases; writes `runs/<set>_<time>Z/summary.md` |
| `make notebooks` | executes the two teaching notebooks against the running api and saves their outputs |
| `make docs` | builds the Sphinx site into `docs/_build` |

The container is named `automccd-lite-icd` and stays running between serves so the release stays
loaded; `uv run automccd-lite icd stop` removes it. The model name lives in `config/default.yaml`;
`LLM_MODEL` in `.env` overrides it.

## Where to start reading

- `notebooks/mccd_guide.ipynb` teaches what a death certificate is, how ICD-11 and DORIS select
  the underlying cause, and then runs every stage on record S01 with the outcome of each situation
  stated before the cell. Start here.
- `notebooks/how-to-guide.ipynb` sends one record to the api and shows the result four ways: the
  WHO form, the record with its evidence spans highlighted, the flow diagram with live values, and
  the charts over an evaluation run.
- `docs/` is the Sphinx site: the pipeline node by node, the module guide, and the API reference.
- `claude/lite/DECISIONS.md` records every design decision and why; `claude/lite/PLAN.md` the
  milestones and the parity report against the full autoMCCD.

## Layout

```
config/default.yaml        tuning by stage: prompt, draft, revise, code
prompts/                   the prompt (v4.7) and the strict output schema
data/                      gold certificates, scenarios, sets, 3 example records,
                           robust_sample.json (34 stress cases), code_cache.json (autocode cache)
src/automccd_lite/         models config log prompt draft validate revise code route
                           certificate storage db pipeline api cli eval
tests/                     test_validate test_route test_gold
notebooks/                 the two guides and _helpers.py
docs/                      the Sphinx site
store/                     what the api stores: certificates.sqlite and certificates/ (not tracked)
runs/                      evaluation output (not tracked)
```

## How a record travels

1. **build_prompt** renders the record as text and pairs it with the system prompt. The prompt's
   SHA-1 goes into every certificate, so results from different prompts never mix.
2. **draft_certificate** makes one model call with strict JSON-schema decoding. The reply is `ok`
   with a certificate, or `refused`, `truncated` or `invalid` without one.
3. **validate_draft** checks the schema and the ten rules: verbatim evidence spans (V1), no modes of
   dying (V2), one cause per line (V3), no abbreviations (V4), structure (V5), non-decreasing
   intervals (V6), Part II disjoint from Part I (V7), decline consistency (V8), no ICD codes in
   terms (V9), no personal identifiers (V10).
4. **revise_draft** gives the model one chance to fix what failed. The revision is adopted only if
   it keeps the same assessment; adopted or not, the draft never routes `auto` afterwards.
5. **code_draft** runs the WHO tools: autocode per term (cached in `data/code_cache.json`), DORIS for
   the underlying cause, CoDEdit on the whole certificate and again on the underlying cause alone.
   Inside it, **revise_terms** asks once for the standard ICD-11 name of any term autocode could not
   match with confidence, and keeps a restated term only if it codes better.
6. **route_draft** turns every finding into a reason code. No reasons means `auto`.
7. **generate_certificate** builds the response, writes the JSON to
   `store/certificates/{date}/{time}Z_{record_id}.json`, and inserts one provenance row in the
   SQLite table, in the same transaction as the request.

The response body is `record_id, route, reasons, certificate, coding, versions, usage, timings_ms`.
The validator's findings, both revisions and the raw WHO replies stay in the row.

## Relation to autoMCCD

This repository is autoMCCD with the production parts removed: no batch endpoint, no queue or
worker, no migrations, no Azure services (the full project writes to Azure Blob Storage and Azure
Postgres from a VM; lite writes to a folder and a SQLite file). Function and field names are the
same, so the two map onto each other file by file. The gate, the revisions, the coder and the router
behave identically; `claude/lite/PLAN.md` shows the case-by-case comparison on the headline set.
