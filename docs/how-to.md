# How-To

This page takes you from a fresh clone to one certified record: you set up the environment, send one example record through the running api, and read what came back and what was saved.

## Set up your environment

1. Install Docker. The WHO ships its coding service (the WHO ICD-API container) only as a Docker image, so Docker is the one prerequisite beside `uv` and a model key.
2. Install `uv`. It creates the virtual environment and runs every command on this page.
3. Run the server once. The first run copies `.env.example` to `.env` and stops, because the model key is still empty.

   ```
   uv run automccd-lite serve        # src/automccd_lite/cli.py:55
   ```

4. Open `.env` and fill in the two model settings:
   - `OPENAI_BASE_URL`: the address of your OpenAI-compatible endpoint (e.g. `https://api.openai.com/v1`, the value in `.env.example`).
   - `OPENAI_API_KEY`: your key for that endpoint. The server refuses to start while it is empty (`src/automccd_lite/cli.py:57`).

   The other settings have working defaults; the full list is on the [Supporting Modules](supporting-modules.md#configuration) page.
5. Run the server again. It starts the WHO ICD-API container on port 8082, waits until the container has loaded its ICD-11 release, creates the `store/` folder, and runs the api on `127.0.0.1:8000`.

   ```
   uv run automccd-lite serve        # src/automccd_lite/cli.py:136
   ```

   The container pulls its image on the first start and then takes about a minute to load its release; the server waits up to 240 seconds for it (`src/automccd_lite/cli.py:34`). The container stays running between serves, so later starts are fast.

## Run one example case

1. Leave the server running and open a second terminal in the repository folder.
2. Send the example record S01 through the api. The command waits for the api to report ready, posts `data/examples/S01.json` to `POST /certify`, prints the certificate as the WHO form, and confirms that the row and the file were saved.

   ```
   uv run automccd-lite example S01  # src/automccd_lite/cli.py:207
   ```

   The other examples are `S02` and `S05` (`src/automccd_lite/cli.py:271`).

### Example input

The example record is one death record with a single note, the case narrative, as the api receives it on `POST /certify` (`data/examples/S01.json`):

```json
{
  "record_id": "S01",
  "sex": "male",
  "age": 49,
  "age_unit": "years",
  "registry_info": null,
  "notes": [
    {
      "name": "case_narrative",
      "text": "A male aged 49 years was admitted to hospital with a history of fever, disorientation and drowsiness for the last 2 days. Focal neurological findings were identified on physical examination and a provisional diagnosis of meningitis was made. A diagnostic lumber puncture was performed for CSF examination. Staphylococcus aureus organism was isolated from CSF and the provisional diagnosis was confirmed as Staphylococcus aureus meningitis. Despite IV antibiotic administration his condition worsened and on 3rd of admission blood cultures were also positive for Staphylococcus aureus and the patient expired on 4th day of admission due to septic shock. He was a diagnosed case of type II diabetes for the last 10 years and has had a renal transplant 6 years back."
    }
  ]
}
```

### Expected output

You should see the certificate, its route and reasons, and the two places it was saved, ending with `EXAMPLE OK`:

```
Record S01    route: review
Reasons: fix:rules, model:low_confidence, quality:BER-CE-1
      Part I: the chain, immediate cause first      interval        ICD-11
  1a  Septic shock                                  unknown         1G41
      Part II: other significant conditions
      Staphylococcus aureus meningitis              unknown         1B54
      ...
Underlying cause (UCOD): 1G41
Saved: row 1 in store/certificates.sqlite
       store/certificates/2026-11-02/084703Z_S01.json
EXAMPLE OK
```

The model does not return the same draft twice: the seed in `config/default.yaml` is best effort and the model is not deterministic (`config/default.yaml:9`). Your route, reasons and codes can differ from this output, and from one run to the next.

The api returns the same result as a JSON response body, and the store keeps a copy of it. This is the body of the run above, with the evidence spans of Part II, the review note and the timings shortened to `...`:

```javascript
{
  "record_id": "S01",
  "route": "review",
  "reasons": ["fix:rules", "model:low_confidence", "quality:BER-CE-1"],
  "certificate": {
    "record_id": "S01",
    "assessment": "certifiable",
    "decline_reason": null,
    "part1": [
      {
        "certified_term": "Septic shock",
        "evidence_field": "case_narrative",
        "evidence_span": "expired on 4th day of admission due to septic shock",
        "basis": "explicit_diagnosis",
        "interval": {"value": null, "unit": "unknown", "certainty": "unknown"}
      }
    ],
    "part2": [
      {"certified_term": "Staphylococcus aureus meningitis", ...},
      {"certified_term": "Diabetes mellitus type 2", ...},
      {"certified_term": "Renal transplant", ...}
    ],
    "review": {"confidence": "moderate", "flags": [...], "note": "..."}
  },
  "coding": {
    "underlying_cause": "1G41",
    "codes": {"part1": ["1G41"], "part2": ["1B54", "5A11", "NE84"]},
    "match_scores": [1.0],
    "quality_issues": ["BER-CE-1"],
    ...
  },
  "versions": {"prompt_version": "4.7", "schema_version": "v1.1", "icd_release": "2026-01", ...},
  ...
}
```

How to read the output:

- **route**: `auto` when no reason applies, `review` when any does (`src/automccd_lite/decide.py:127`).
- **Reasons**: each code says why a human must look at the certificate. In this run the model fixed a broken rule (`fix:rules`), rated its own confidence below high (`model:low_confidence`), and the WHO quality checks (CoDEdit) returned the issue `BER-CE-1` (`quality:BER-CE-1`). [Step 3. Decide](decide.md#reason-codes) lists every reason code.
- **Part I and Part II**: each condition with its interval and its ICD-11 code (`src/automccd_lite/cli.py:174`).
- **Underlying cause (UCOD)**: the stem code the WHO cause selector (DORIS) picked (`src/automccd_lite/cli.py:204`).
- **Saved**: the row number in the SQLite file and the path of the JSON file (`src/automccd_lite/cli.py:233`).

## View the result

The api saves every certified record twice: a JSON file with the response body for a downstream system, and a SQLite row with everything the response leaves out, for a reviewer or an audit.

1. Open the JSON file printed after `Saved:`, for example `store/certificates/2026-11-02/084703Z_S01.json`. It holds exactly what the api returned: the record id, the route, the reasons, the certificate, the coding summary, the versions, the token usage and the timings (`src/automccd_lite/store.py:132`).
2. Read the SQLite row for the same record. `latest_row` returns the newest row for a record id:

   ```
   uv run python -c "from automccd_lite.store import latest_row; r = latest_row('S01'); print(r.route, r.file_path); print(r.rule_check); print(r.who_replies)"   # src/automccd_lite/store.py:155
   ```

   Three columns are worth a look:
   - `rule_check`: what the rules found, which rules broke, and what each fix did (`src/automccd_lite/store.py:79`).
   - `who_replies`: the raw replies of the term coder (autocode), the cause selector (DORIS) and the quality checks (CoDEdit) (`src/automccd_lite/store.py:80`).
   - `file_path`: the JSON file of this row, relative to the `store/` folder (`src/automccd_lite/store.py:84`).

   Every column is listed on the [Supporting Modules](supporting-modules.md#the-stored-row) page.
3. Open the how-to notebook, `notebooks/how-to-guide.ipynb`. It sends one record through the running api and shows the response, the certificate as the WHO form, the record with its evidence highlighted, the saved row and file, and the flow diagram with that record's live values. Install its dependencies once, then run both notebooks against the running api:

   ```
   uv sync --group notebooks         # src/automccd_lite/cli.py:243
   uv run automccd-lite notebooks    # src/automccd_lite/cli.py:247
   ```

To run the unit tests, which need neither the container nor a model key:

```
uv run --extra dev pytest            # pyproject.toml:23
```
