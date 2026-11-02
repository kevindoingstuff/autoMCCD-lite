# autoMCCD-lite

autoMCCD-lite turns one clinical death record into a WHO Medical Certificate of Cause of Death
(MCCD), coded in ICD-11. A language model writes the certificate, and the code checks it against
ten rules and the WHO's own coding tools. Each certificate comes back with a route, `auto` or
`review`, and the reason codes that say why a human must look at it.

**Draft** (the model writes the certificate) → **Check** (rules, WHO coding, one fix each) →
**Decide** (reasons and route)

## Run it

You need Docker (for the WHO ICD-API container), `uv`, and a key for an OpenAI-compatible model
endpoint.

```bash
uv run automccd-lite serve
```

The first run creates `.env` from `.env.example` and stops. Fill in `OPENAI_BASE_URL` and
`OPENAI_API_KEY`, then run it again. When the api says it is running, open a second terminal:

```bash
uv run automccd-lite example S01
```

You see the certificate as the WHO form, its route and reasons, and where it was saved. The run
ends with `EXAMPLE OK`. To run the unit tests:

```bash
uv run pytest
```

## Read next

- `notebooks/how-to-guide.ipynb`: one record through the api, shown four ways.
- `notebooks/mccd_guide.ipynb`: what a death certificate is, then each step run on one record.
- `docs/`: the Sphinx site. Build it with `uv run automccd-lite docs`.
