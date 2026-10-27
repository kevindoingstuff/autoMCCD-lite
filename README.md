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

You need Docker, `uv` and `make`.

1. `cp .env.example .env`, then fill `OPENAI_BASE_URL` and `OPENAI_API_KEY` for any
   OpenAI-compatible endpoint. The model name lives in `config/default.yaml`; `LLM_MODEL` in `.env`
   overrides it.
2. `make up` builds the api image and starts four containers: the api on `127.0.0.1:8000`, the WHO
   icd-api on `8082`, Postgres on `5433` and the Azurite blob emulator on `10000`. The icd-api
   downloads its release on the first start and takes about a minute to become healthy.
3. `make smoke` waits for `/readyz`, certifies `data/records/S01.json` through `POST /certify`, and
   checks the stored row and its blob path. It ends with `SMOKE OK`.

Then, from the host:

| command | what it does |
|---|---|
| `make test` | the unit tests: validators V1 to V10, the routing table, the gold checks |
| `make eval` | the mini harness on the 17 headline cases; `EVAL_SET=robust make eval` for the 34 stress cases; writes `runs/<set>_<time>Z/summary.md` |
| `make notebooks` | executes the two teaching notebooks against the running stack and saves their outputs |
| `make docs` | builds the Sphinx site into `docs/_build` |
| `make down` | stops the stack; the Postgres and Azurite volumes survive |

Send a record yourself:

```bash
curl -s -X POST localhost:8000/certify -H 'content-type: application/json' --data-binary @data/records/S02.json
```

## Where to start reading

- `notebooks/mccd_guide.ipynb` teaches what a death certificate is, how ICD-11 and DORIS select
  the underlying cause, and then runs every stage on record S01 with the outcome of each situation
  stated before the cell. Start here.
- `notebooks/how-to-guide.ipynb` sends one record to the api and shows the result four ways: the
  WHO form, the record with its evidence spans highlighted, the flow diagram with live values, and
  the charts over an evaluation run.
- `docs/` is the Sphinx site: one user guide per module with diagrams, and the API reference.
- `claude/lite/DECISIONS.md` records every design decision and why; `claude/lite/PLAN.md` the
  milestones and the parity report against the full autoMCCD.

## Layout

```
config/default.yaml        tuning by stage: prompt, draft, revise, code
prompts/                   the prompt (v4.7) and the strict output schema
data/                      gold certificates, scenarios, sets, 3 example records,
                           robust_sample.json (34 stress cases), code_cache.json (autocode cache)
src/automccd_lite/         models config log prompt draft validate revise code route
                           certificate storage db pipeline api eval
tests/                     test_validate test_route test_gold
notebooks/                 the two guides and _helpers.py
docker/                    compose.yml (api, icd-api, postgres, azurite) and the api Dockerfile
scripts/smoke_test.sh      the end-to-end check
docs/                      the Sphinx site
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
7. **generate_certificate** builds the response, writes the JSON to Blob under
   `certificates/{date}/{time}Z_{record_id}.json`, and inserts one provenance row in Postgres, in
   the same transaction as the request.

The response body is `record_id, route, reasons, certificate, coding, versions, usage, timings_ms`.
The validator's findings, both revisions and the raw WHO replies stay in the row.

## Relation to autoMCCD

This repository is autoMCCD with the production parts removed: no batch endpoint, no queue or
worker, no migrations, no managed identity, no Postgres cache. Function and field names are the
same, so the two map onto each other file by file. The gate, the revisions, the coder and the router
behave identically; `claude/lite/PLAN.md` shows the case-by-case comparison on the headline set.
