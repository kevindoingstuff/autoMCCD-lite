# autoMCCD-lite

The answer key for the MCCD PoC: clinical record text goes in, a WHO Medical
Certificate of Cause of Death (MCCD) coded in ICD-11 comes out, with a route
(`auto` or `review`) and the reasons for it.

Pipeline: `build_prompt -> draft_certificate -> validate_draft -> revise_draft
-> code_draft (-> revise_terms) -> route_draft -> generate_certificate`.

## Run it

1. `cp .env.example .env` and fill `OPENAI_BASE_URL` and `OPENAI_API_KEY`.
2. `make up` starts the api, the WHO icd-api, Postgres and Azurite.
3. `make smoke` certifies `data/records/S01.json` through `POST /certify`.

`make test` runs the unit tests, `make eval` the mini harness,
`make notebooks` the two teaching notebooks, `make docs` the Sphinx site.

## Layout

See `claude/lite/PLAN.md` for the tree and `claude/lite/DECISIONS.md` for why
each part is the way it is.
