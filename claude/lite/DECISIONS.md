# autoMCCD-lite: decisions

Decided by the user in an MCQ quiz on 2026-10-26, before any code. The user
decides every structure and naming question; names minted while coding go
into a "Names introduced" table here. Source project: `autoMCCD`
(`C:\Users\kevsa\Desktop\VitalStrategies\autoMCCD`, commits up to `229b480`),
whose own record is `...\aeras\training_data\images\VitalStrategies\outputs\automccd\{DECISIONS,PLAN}.md`.

## What lite is

| # | Question | Answer |
|---|----------|--------|
| L1 | Audience | AIAP apprentices, ~3 months, Python-fluent, little clinical or ICD background: lite must carry the domain explanation |
| L2 | Role | An **answer key**: if they mess up or get lost they reference it as the guiding answer. Complete and runnable; they build their own PoC and compare |
| L23 | Gaps | **No gaps**: everything works. No TODOs, no hidden traps |
| L19 | Derivation | **Copy and prune from autoMCCD, keep names**: delete queue/worker/batch/Alembic/managed-identity paths, flatten the tree, keep function and field names identical so the two repos map onto each other |
| L30 | Location | New repo `C:\Users\kevsa\Desktop\VitalStrategies\autoMCCD-lite`, own git history, import name **`automccd_lite`**, no dependency on the full repo |
| L36 | This record | `claude/lite/DECISIONS.md` + `claude/lite/PLAN.md` **inside the lite repo** (apprentices may read them) |
| N7 | Build | In the same Claude session, after a context compaction; this record is what survives the compaction |

## Pipeline scope

| # | Question | Answer |
|---|----------|--------|
| L3 | Stages kept | **build_prompt + draft (strict JSON), validate (rule gate), revise, code (WHO container)**: all of them |
| L25 | Revisions | **Both as in full**: rule revision after the gate (schema or V-rule failure) and terminology revision inside coding (autocode score < `code.min_match_score`), one attempt each, adopt/reject rules and the per-term score guard (FM-24) kept; never auto afterwards |
| L26 | ICD stage | **autocode + DORIS + CoDEdit ×2** (whole certificate, and the UCOD alone for the ill-defined metric) |
| L11 | Validators | **All ten, V1–V10**; V2 on every Part I line (as full after D3), bare-head check on 1a only |
| L12 | Schema | **jsonschema check on the reply + strict `json_schema` decoding**; a mismatch routes to review |
| L4, L33 | Routing | **Two routes: `auto` and `review`.** A declined record is `route: review` with `reasons: ["assessment:insufficient"]` or `["assessment:out_of_scope"]`; every other D9 reason code unchanged (`validator:<rule>`, `revised`, `terms_revised`, `low_confidence`, `schema_invalid`, `model_refused`, `model_truncated`, `model_invalid`, `not_coded`, `part1_line_uncoded`, `autocode_low_score`, `no_ucod`, `codedit:<id>`, `icd_unavailable`, `icd_error`, `demographics_missing`). The repair loop runs the same way as in full |
| L15 | Prompt | **v4.7 unchanged + `mccd.schema.json`**, same sha1 pinning, so results are comparable with full |
| L16 | Model | **Same OpenAI-compatible endpoint**: `OPENAI_BASE_URL`, `OPENAI_API_KEY` in `.env`; model name in `config/default.yaml` (`gpt-5-nano-2025-08-07`, `reasoning_effort: minimal`, temperature not sent, `max_completion_tokens: 8000`), `LLM_MODEL` override |
| L7 | Autocode cache | **JSON file cache like the PoC**: `data/code_cache.json`, persisted between runs, entries carry `adjudicated: false` until a human reviews them |
| L27 | Config | **`config/default.yaml` nested by stage** (`prompt`, `draft`, `revise`, `code`); endpoints and secrets from the environment under vendor-native names |
| L29 | Logging | **JSON logs with the PHI allowlist** (`log.py` as in full: `ALLOWLIST`, `NOISY_LOGGERS` incl. `httpx2`); record text, spans, terms and notes never reach a log |

## Runtime and persistence

| # | Question | Answer |
|---|----------|--------|
| L5 | Runtime | **Small FastAPI with `POST /certify` only** (plus `/healthz`, `/readyz`); synchronous; no batch, no jobs, no worker |
| L31 | Response | **Same body as full**: `record_id, route, reasons, certificate, coding, versions, usage, timings_ms`; provenance (`validation`, `coded`, `icd_error`, `draft_status`) stored in the row, not returned |
| L6 | Persistence | **A SQLite file and a local folder under `store/`** (git-ignored): `store/certificates.sqlite` for the provenance rows, `store/certificates/` for the certificate JSON; `STORE_DIR` in `.env` overrides the folder. No Postgres, no Azurite, no cloud code |
| L9 | Table | **`certificates` table only** (one provenance row per certified record, SQLAlchemy over SQLite, JSON columns); no `jobs`, `job_records`, `icd_cache` |
| L10 | Schema creation | **SQLAlchemy `create_all` at startup**; no Alembic |
| L34 | File name | **`store/certificates/{yyyy-mm-dd}/{hhmmss}Z_{record_id}.json`**: a time prefix so a re-run of the same record never collides |
| L8, L28 | Docker | **Only the WHO icd-api runs in Docker**, because that is the only form the WHO ships it in. `automccd-lite serve` starts the pinned image as a named container (`automccd-lite-icd`, port `8082`, `acceptLicense`, `enableDoris`), reuses it when it exists, waits for its health and leaves it running afterwards so the release stays loaded; `automccd-lite icd stop` removes it. The api runs on the host under uv; no Dockerfile, no compose |
| L24 | Tooling | **uv + ruff + pytest + one console script**, `automccd-lite` (`cli.py`: `serve`, `icd start|stop|status`, `smoke`, `eval`), with the Makefile as thin aliases: `make run`, `make smoke`, `make test`, `make eval`, `make notebooks`, `make docs`; no pre-commit, gitleaks or sonar. `serve` copies `.env.example` to `.env` when it is missing and stops with one line asking for the model key |

## Data and evaluation

| # | Question | Answer |
|---|----------|--------|
| L14 | Data shipped | **The 24 scenarios + gold** (client deck), **the 3 example records** S01/S02/S05 in the request shape, `eval_sets.json`, `ucod_attributions.json`, `demographics.json`, and **a sample of the robust corpus** |
| L17 | Robust sample | **~30 cases: 2 per variant type** (15 types) plus a few handwritten and PMC records, each tagged with what it stresses; scored on behaviour only |
| L35 | Data note | **No `data/README.md`**; apprentices are briefed verbally |
| L13 | Evaluation | **Mini harness on the 17 headline cases** (`python -m automccd_lite.eval` / `make eval`): validator-clean (first pass and final), auto share, UCOD exact vs gold, behaviour on the robust sample. No robust corpus run, no Wilson intervals, no compare |
| L32 | Acceptance | **Parity with full**: lite's eval on the 17 headline cases vs `autoMCCD/runs/headline_m5_baseline`, case by case on route, reasons and UCOD codes; same prompt sha1, same model; the gate and coding results must match (model noise aside) |
| LM0-1 | Robust sample rule (2026-10-26) | **Keep it simple**: 2 per variant type from parents S01 and S02, plus H01, H02 and the first 2 PMC records (34 cases); tags gain `stress`; scored on behaviour only. Eval is a tuning aid for apprentices, not a deliverable |
| LM0-2 | Cache location | **`data/code_cache.json`, tracked**, so a fresh clone codes the gold chains without icd-api and adjudicated flags survive |
| LM0-3 | Host ports | **api 8000, icd-api 8082**, the same icd-api port as the full autoMCCD dev stack, so stop one before starting the other |
| L20 | Tests | **`tests/test_validate.py`** (ported V1–V10 cases), **`test_route.py`**, **`test_gold.py`** (G1–G4); no API/DB/integration tests; `automccd-lite smoke` covers the running api |

## Code shape and docs

| # | Question | Answer |
|---|----------|--------|
| L18 | Layout | **Flat package, one file per stage**: `src/automccd_lite/{models,config,log,prompt,draft,validate,revise,code,route,certificate,storage,db,api,cli,eval}.py` + `prompts/ config/ data/ tests/ notebooks/ docs/`. Same stage names as full, no subpackages |
| L22 | Comments | **Same sparse style as full**; the notebooks and the docs carry the explanation |
| L21 | Docs | **Sphinx site via the docs skill** (module guides + diagrams), as for full. Plus a README to run it |

## Notebooks (added 2026-10-26)

| # | Question | Answer |
|---|----------|--------|
| — | Which | **`notebooks/how-to-guide.ipynb`** (input → output, visually, what can be read from it) and **`notebooks/mccd_guide.ipynb`** (how an MCCD works and how the pipeline produces one; runnable outputs of the important functions). Voice: a nurturing teacher; the docs skill's prose rules (second person, purpose before names, the reason beside every surprising choice, an example beside every variable, no dashes) |
| N1 | Outputs | **Live against the running api** (model endpoint, icd-api, the local store); executed outputs saved in the `.ipynb` so it reads before anything runs |
| N2 | Visuals in how-to-guide | **All four**: the certificate rendered as the WHO form (Part I 1a–1d with intervals, Part II); the record with each evidence span highlighted and labelled (V1 made visible); a stage-by-stage flow diagram carrying the live route, reasons and timings; small charts over the mini-harness results (routes, rules fired, UCOD agreement) |
| N3 | mccd_guide order | **WHO MCCD → ICD-11 coding → our pipeline, one record (S01) throughout**: 1 what a death certificate is and why Part I order matters, 2 the underlying cause and how DORIS selects it, 3 why free text fails (ill-defined causes, fabrication), 4 our stages one by one, each function run on S01 and its output read aloud, 5 the routes and what a reviewer does, 6 how we measure |
| N4 | "Various options" | **Per important function, the outcomes to expect from given situations**: e.g. `validate` on a fabricated span → V1, on a mode of dying → V2, on a decreasing interval → V6; `route` with `low` confidence → review, with a CoDEdit id → review; `code` with a weak term → terminology revision, then the score guard reverting it; `draft` on an out-of-scope record → `assessment: out_of_scope`. Each shown as a runnable cell with the expected outcome stated before the run |
| N5 | Tooling | **`notebooks` dependency group**: jupyter, matplotlib, graphviz; **`make notebooks`** executes both with `jupyter nbconvert --execute --inplace` against the running api and fails if a cell errors, so saved outputs stay current. Graphviz needs the `dot` binary; fall back to the docs skill's PNGs when absent |
| N6 | In the Sphinx site | **No**; the notebooks live under `notebooks/` only |

## Mapping from autoMCCD (what is dropped)

| autoMCCD | lite |
|----------|------|
| `api/` with `/batchcertify`, `/jobs/{id}`, `BatchRequest`, `JobStatus`, `CertifyService` | `api.py`: `POST /certify`, `/healthz`, `/readyz`; `certify_one` only |
| `worker/`, `core/io/queue.py`, `jobs`/`job_records` tables, `JobRef`, `blob_prefix` | dropped; the Blob name carries a time prefix instead |
| `core/db/alembic/`, `icd_cache` table, `PostgresCodeCache` | `create_all`; `data/code_cache.json` (`FileCodeCache`, PoC `CodeCache` interface) |
| `core/io/storage.py`, `azure-storage-blob`, `azure-identity` | `storage.py`: a local folder under `store/` |
| `core/db/{session,store}.py`, `psycopg`, the `PG*` variables | `db.py`: SQLite under `store/` through SQLAlchemy |
| `core/runtime.py`, `Deps` | kept small: one `Deps` built in `api.py` at startup |
| `evaluation/` run/rescore/compare, robust set, Wilson, attributions table | `eval.py`: headline + robust-sample run, four numbers, per-case table |
| `docker/`, `compose.dev.yml`, `scripts/{bootstrap_vm,provision_azure}.sh`, `docs/deployment.md` | dropped; `cli.py` starts the one container the project needs |
| `Route` enum of four | `Route` of two; `assessment:*` reasons |

## Names introduced while building (change on request)

| Name | Where | What |
|------|-------|------|
| `automccd-lite` | `pyproject.toml` project name | distribution name; the import name stays `automccd_lite` |
| `data/robust_sample.json` | data | `{corpus_version, rule, n, cases}`; `rule` states the selection in words |
| `stress` | tag on every sampled robust case | marks a case as a stress sample, not a headline case |
| `FileCodeCache`, `CACHE_PATH` | `code.py` | the JSON autocode cache at `data/code_cache.json`; `_save` merges what another writer added, then rewrites the file whole |
| `DATA_DIR` | `config.py` | `ROOT / "data"`, next to `PROMPTS_DIR` |
| `Runtime` | `api.py` | the collaborators built once at startup (`from_env`, `certify_one`, `readiness`); full's `core/runtime.py` + `api/service.py` folded into one class |
| `Readiness` | `api.py` | the `/readyz` body: `ready` and the three components icd_api, store, model_configured |
| `create_all`, `SqliteCertificateStore`, `session_scope`, `STORE_DIR` | `db.py`, `config.py` | one module for the engine, the row and the store; the SQLite file lives at `STORE_DIR/certificates.sqlite` |
| `FileStorage`, `certificate_path(record_id, now)` | `storage.py` | writes the response body to `STORE_DIR/certificates/{yyyy-mm-dd}/{hhmmss}Z_{record_id}.json` and returns the path relative to `STORE_DIR` |
| `cli.py`: `serve`, `icd`, `smoke`, `eval`; `ICD_CONTAINER = "automccd-lite-icd"`, `ICD_IMAGE` (the pinned digest) | `cli.py` | the `automccd-lite` console script; `serve` = ensure `.env`, start the container, wait for `/icd/release/11/<release>/mms`, create the store, run uvicorn |
| `headline_cases`, `robust_cases`, `load_cases` | `eval.py` | the 17 `<parent>-clean` records built from scenarios + demographics, and the 34 stress cases |
| `Reference.ucod`, `ucod_match` | `eval.py` | the gold coded through `code_draft`; three match kinds (exact, same_stem, different) instead of full's six |
| `score`, `summarize`, `report`, `run`, `default_deps`, `must_not_violations`, `norm` | `eval.py` | the harness in one file; `run()` is what the notebook calls in-process |
| `icd_client()` | `api.py` | the `IcdClient` built from settings, shared by the api and the cli |
| `db_path()`, `DB_NAME` | `db.py` | `STORE_DIR/certificates.sqlite` |
| `file_path` (column) | `db.py` | the certificate JSON path relative to `STORE_DIR`, in the `certificates` row |
| `CERTIFICATES_DIR` | `storage.py` | the `certificates/` folder under `STORE_DIR` |
| `ICD_WAIT_S` | `cli.py` | how long `serve` waits for the container to answer (240 s) |
| `make run` | Makefile | alias of `uv run automccd-lite serve` |
| `make notebooks`, `make docs` use `uv sync --inexact` | Makefile | the two groups do not evict each other |
| `notebooks/_helpers.py`: `load_record`, `load_gold`, `show_json`, `render_mccd`, `highlight_spans`, `flow_diagram`, `charts`, `timings_chart`, `reason_table` | notebooks | display only; `render_mccd` draws the WHO form with the ICD code per line, `highlight_spans` uses V1's own folding so what it highlights is what V1 accepted, `flow_diagram` needs `dot` and falls back to text |
| `LM4-1` notebook expectations | notebooks | every "expected outcome" in mccd_guide was checked against a live run on 2026-10-26 and rewritten where the WHO tools behaved differently (DORIS rescues the reversed S01 chain; CoDEdit raises BER-CE-1 on the S01 gold because "Renal transplant" codes to an injury code) |
| Pipeline nodes: Draft the MCCD, Verify, Code, Generate the MCCD | `docs/automccd_lite.md` | the four sections of the module guide (asked 2026-10-27): build_prompt + draft_certificate; validate_draft + revise_draft; code_draft + revise_terms; route_draft + generate_certificate. The other modules sit under Supporting Modules |
| `make docs`, `make notebooks` | Makefile | `docs` syncs the `docs` group and runs sphinx-build; `notebooks` syncs the `notebooks` group and executes both notebooks in place |
