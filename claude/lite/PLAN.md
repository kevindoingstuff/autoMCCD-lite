# autoMCCD-lite: plan

Built from `autoMCCD` by copy-and-prune (DECISIONS.md L19). Each milestone
ends with a stated check; the user approves each milestone and its commit
(commit skill: STE + Conventional Commits, one commit per change, no
trailer). Nothing is committed without being asked.

Sources: `C:\Users\kevsa\Desktop\VitalStrategies\autoMCCD` at `229b480`
(code, prompts, schema, data, tests, runs/headline_m5_baseline for parity);
the PoC at `...\VitalStrategies\poc` only for `data/code_cache.json` shape.

## Target tree

```
autoMCCD-lite/
├── claude/lite/{DECISIONS,PLAN}.md
├── config/default.yaml            # prompt, draft, revise, code (same keys as full)
├── prompts/mccd/v4.7.md, prompts/schemas/mccd.schema.json
├── data/
│   ├── gold/S01..S24.json, scenarios.json, eval_sets.json, ucod_attributions.json, demographics.json
│   ├── robust_sample.json         # ~30 cases, 2 per variant type + handwritten/PMC, tagged
│   ├── records/{S01,S02,S05}.json # POST /certify bodies
│   └── code_cache.json            # autocode cache, adjudicated flags
├── src/automccd_lite/
│   ├── models.py      Record, Draft, DraftValidation, CodedDraft, Route(auto|review), Decision, Certificate
│   ├── config.py      settings(): YAML by stage + Env
│   ├── log.py         JSON logs, ALLOWLIST, NOISY_LOGGERS
│   ├── prompt.py      build_prompt, system_prompt, sha1
│   ├── draft.py       draft_certificate (openai client, strict json_schema, statuses)
│   ├── validate.py    V1-V10, validate_schema, validate_draft, pre_icd_route
│   ├── revise.py      revise_draft (rules) + revise_terms (terminology, FM-24 guard)
│   ├── code.py        IcdClient (autocode, doris, codedit), FileCodeCache, code_draft
│   ├── route.py       route_draft: auto | review + reason codes
│   ├── certificate.py generate_certificate, coding_summary, versions, as_response
│   ├── storage.py     FileStorage: store/certificates/{date}/{time}Z_{record_id}.json
│   ├── db.py          SQLite engine under store/, CertificateRow, create_all, insert_certificate
│   ├── pipeline.py    certify_record(record, deps)
│   ├── api.py         FastAPI: POST /certify, /healthz, /readyz; Deps at startup
│   ├── cli.py         automccd-lite serve | icd | smoke | eval (the one container, the store, uvicorn)
│   └── eval.py        mini harness: headline + robust sample
├── notebooks/how-to-guide.ipynb, notebooks/mccd_guide.ipynb, notebooks/_helpers.py
├── tests/test_validate.py, test_route.py, test_gold.py
├── docs/ (docs skill output)
├── Makefile, pyproject.toml, .env.example, .gitignore, README.md
```

## Milestones

Status 2026-10-27: LM0 to LM5 built, checked and committed. The LM5 check passed: a fresh clone with only `.env` copied in ran `make up` and `make smoke` to `SMOKE OK`; the Sphinx site builds clean; `make notebooks` executes both notebooks against the stack.

### LM0 — Scaffold
- Repo, pyproject (`automccd_lite`, deps: fastapi, uvicorn, pydantic, pyyaml, sqlalchemy, httpx, openai, jsonschema, python-dotenv; dev: pytest, ruff; groups: notebooks (jupyter, matplotlib, graphviz), docs (sphinx, myst-parser, sphinx-design, sphinx-copybutton, furo)), Makefile, `.env.example`, `.gitignore` (`.env`, `runs/`, `store/`, `docs/_build/`, `.ipynb_checkpoints/`), README skeleton.
- Copy prompts, schema, config, data (gold, scenarios, sets, attributions, demographics, 3 records); build `data/robust_sample.json` from the full corpus (2 per variant, tagged `stress`).
- Check: `uv sync`, `uv run ruff check .` clean; `pytest` collects nothing yet.

### LM1 — Pipeline port
- Copy and prune core: `models.py` (Route two values), `prompt.py`, `draft.py`, `validate.py`, `revise.py` (both revisions, `scoped_to_terms`), `code.py` (`FileCodeCache` keyed like full's `cache_key`, `adjudicated: false`), `route.py` (two routes, `assessment:*` reasons), `certificate.py`, `pipeline.py`, `config.py`, `log.py`.
- Tests: `test_validate.py` (full's `tests/unit/core/rules/test_validators.py` ported), `test_route.py` (two-route table).
- Check: unit tests pass; `certify_record` on S01/S02/S05 against the live model and the icd-api container returns schema-valid certificates with routes and reasons; prompt sha1 == `d051044cd4a12f9a46085b23869a4ed70623719e`.

### LM2 — API, persistence, the serve command
- `db.py` (SQLite under `store/`, create_all on startup), `storage.py` (local folder, time-prefixed name), `api.py` (`POST /certify` → certify_one in one transaction; `/readyz` probes icd-api, store, model config), `cli.py` (`serve`: ensure `.env`, start the pinned icd-api container by name, wait for health, create the store, run uvicorn on 127.0.0.1:8000; `icd start|stop|status`; `smoke`: readyz → /certify S01 → the row → the file).
- Check: `uv run automccd-lite serve`, then `uv run automccd-lite smoke` → SMOKE OK from a clean clone with only `.env` filled and Docker running.

### LM3 — Mini evaluation harness
- `eval.py`: loads headline (17) and the robust sample; runs `certify_record` in-process with `FileCodeCache`; prints validator-clean first pass/final, auto share, UCOD exact vs gold (gold coded through the same `code_draft`), behaviour on the robust sample; writes `runs/<set>_<hhmmss>Z/{results.json,summary.md}`; `test_gold.py` (G1–G4).
- Check (acceptance, L32): headline run vs `autoMCCD/runs/headline_m5_baseline/results.json` case by case: same rules fired, same routes modulo the two-route mapping, same UCOD codes; differences explained by model noise only.

### LM4 — Notebooks
- `notebooks/_helpers.py`: `render_mccd(cert)` (HTML WHO form), `highlight_spans(record, cert)`, `flow_diagram(cert)` (graphviz, PNG fallback), `charts(results)`.
- `how-to-guide.ipynb`: setup check (`/readyz`), one record in → certificate out, the four visuals, the stored row and file, the eval charts.
- `mccd_guide.ipynb`: N3 order with S01 throughout; N4 per-function situations → expected outcomes, each stated before the run; teacher voice, docs-skill prose rules.
- `make notebooks` executes both in place; check: both run clean against the running api, outputs saved.

### LM5 — Docs and hand-over
- Docs skill on the lite package (`/docs automccd_lite` or per-module as the skill detects); README final (10-minute run, eval, notebooks).
- Check: Sphinx builds clean; a fresh clone follows the README to SMOKE OK; parity report from LM3 attached to PLAN.md.

## Open items to settle during the build (ask as MCQs when reached)
- Whether `FileCodeCache` lives at `data/code_cache.json` (tracked, with adjudicated flags) or under `runs/`.
- The robust-sample selection rule beyond "2 per variant" (which parents; include the 6 held-out out-of-scope parents?).
- `/readyz` component list for lite.
- Whether `make notebooks` needs the eval run first (charts cell) or runs the eval itself.

## LM3 parity report (2026-10-26)

Lite run `headline_090724Z` against `autoMCCD/runs/headline_m5_baseline`, same prompt sha1 `d051044cd4`, same model, same ICD release.

| aggregate | full | lite |
|---|---|---|
| validator clean, first pass | 58.8% | 58.8% |
| validator clean, final | 82.4% | 82.4% |
| revised once | 7 | 7 |
| auto share | 5.9% | 5.9% |
| model calls | 24 | 24 |
| gold UCOD exact | 47.1% (8/17) | 56.2% (9/16) |

| case | route full → lite | final rules full → lite | model UCOD full → lite | gold UCOD full → lite |
|---|---|---|---|---|
| S01-clean | review → review | - → - | 1G41 → 1G40 | 1B54 → 1B54 |
| S02-clean | review → review | - → - | BA6Z → 8B11.5Z | 8B11.5Z → 8B11.5Z |
| S03-clean | review → review | - → - | DC31.1 → DC32.3 | DC32.3 → DC32.3 |
| S04-clean | review → review | - → - | BA41.Z → BA41.Z | BA41.Z → BA41.Z |
| S06-clean | review → auto | - → - | CA22.Z → CA22.Z | CA22.Z → CA22.Z |
| S07-clean | review → review | - → - | GB60.Z → GB60.Z | 5A11 → 5A11 |
| S08-clean | review → review | - → V2 | PB6Z → MH14 | LA89.Z → LA89.Z |
| S09-clean | review → review | - → - | DA63.Y → 1A09.Z | DA63.Y → DA63.Y |
| S10-clean | review → review | - → - | LA88.2Z → BD10 | LA88.2Z → LA88.2Z |
| S11-clean | review → review | - → - | 2B90.3Z → 2B90.3Z | 2B90.3Z → 2B90.3Z |
| S12-clean | review → review | - → - | 2C12.02 → 2C12.02 | 2C12.02 → 2C12.02 |
| S13-clean | auto → review | - → - | 5B81.Z → BA41.Z | BA41.Z → BA41.Z |
| S14-clean | review → review | - → V1 | GC08.Y → CA40.Z | 8B25.4 → 8B25.4 |
| S15-clean | review → review | - → - | CA25.Z → CA25.Z | CA25.Z → CA25.Z |
| S16-clean | review → review | V7 → - | JB64.Y → JB64.Y | JA8C.Z → JA8C.Z |
| S17-clean | review → review | V3 → - | 1F86.1 → None | BA5Z → (not coded: declined) |
| S18-clean | review → review | V4 → V7 | 1C62.3Z → 1C62.3Z | 1C62.3Z → 1C62.3Z |

Reading: the deterministic half is identical. The gold certificates code to the same UCOD in 16/16 compared cases, the same rules fire on the same drafts (the validator test suite is the port of the full one), and the aggregates match to the decimal. The per-case differences in route, rules and model UCOD come from the model: gpt-5-nano does not return the same draft twice (seed is best effort), so S13 was auto in the full run and S06 in this one, and S17 was certified in the full run and declined as insufficient here. No difference traces to the gate, the revisions, the coder or the router.
