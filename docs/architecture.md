# Pipeline Architecture

This page describes how autoMCCD-lite is put together and what it needs to run. Each part comes
with the reason it is built that way, so you can judge what to keep when you build your own.

## Overall architecture

![system](_static/diagrams/system.drawio.png)

The system has four parts, and only one of them runs in a container:

| Part | What it is | Where it runs |
|---|---|---|
| The api | A FastAPI service with one working endpoint, `POST /certify`, plus `/healthz` and `/readyz` | Your machine, on `127.0.0.1:8000` |
| The model endpoint | Any OpenAI-compatible chat completions service | Wherever `OPENAI_BASE_URL` points |
| The WHO ICD-API | The WHO's own coding tools: the term coder (autocode), the cause selector (DORIS) and the quality checks (CoDEdit) | A Docker container named `automccd-lite-icd`, on port 8082 |
| The store | One SQLite file and one folder of JSON files | `store/` in the repo, or `STORE_DIR` |

One request goes through three steps inside the api, then the api saves the result:

```
POST /certify
  -> Step 1. Draft    the model writes a certificate
  -> Step 2. Check    ten rules and the WHO tools check it; one fix for rules, one for terms
  -> Step 3. Decide   every problem becomes a reason code; no reasons means auto
  -> save             the JSON file first, then the row that points at it
```

### The model proposes, the code checks

The language model is the only part that understands a clinical narrative, and it is also the only
part that can be wrong without any sign of it. So the model does one job, writing the certificate,
and everything after it is ordinary code that gives the same answer every time. A certificate goes
`auto` only when every check that code can make has passed.

### Strict JSON output

The model call uses strict JSON schema decoding, so the reply has the certificate's shape by
construction. A free-text reply would need a parser that guesses at structure, and every guess is
a place for a silent error. Step 2 still checks the schema again: it costs microseconds and catches
an endpoint that does not enforce strict mode.

### Evidence spans make the medicine checkable

Every certified line carries the exact words of the record that support it and the name of the note
they came from. A quote is checkable by string comparison even when the diagnosis is not, so an
invented diagnosis needs an invented quote, and rule V1 (exact quote) catches that quote.

### The WHO tools do the coding

ICD-11 codes and the underlying cause of death (UCOD) come from the WHO ICD-API, not from the model.
The cause selector (DORIS) applies the WHO's own selection rules, which are the rules a national
statistics office applies, and it returns the same answer for the same certificate. A model asked
for codes would give plausible codes with no guarantee they follow those rules.

The container image is pinned by its digest, so the ICD release and the selection rules cannot
change under you between runs. It stays running between serves because loading the release takes
about a minute.

### One bounded fix per problem

When a rule breaks, the model gets its draft back with the list of failures and one chance to fix
it. When a term codes weakly, the model gets one chance to restate it as the standard ICD-11 name.
One attempt each keeps the cost of a record predictable: at most three model calls, and in practice
one or two. A loop until the checks pass would have no upper bound on calls or time.

Each fix has a guard. A rules fix is kept only if it keeps the same assessment, so a model cannot
fix a quote by declaring the record out of scope. A restated term is kept only if it codes with a
higher match score than the original, so a fix cannot make coding worse. A certificate that needed
a fix never routes `auto`, because a human should see what the model changed.

### Reasons instead of a score

Step 3 has no confidence score and no threshold. Every problem becomes a reason code in the form
`source:what`, such as `rule:V2` or `quality:FER-CE-931`, and any reason means `review`. A reviewer
reads the list as a worklist, and an auditor can trust that `auto` means every check passed. A
score would need a threshold that someone tunes, and a tuned threshold hides which check failed.

### A pure pipeline and a separate save

`certify_record` runs the three steps and returns a result; it writes nothing. The api saves that
result in a separate call. The same function therefore runs in the api, in the evaluation harness
(which must not fill the store) and in the tests, where fakes replace the model and the WHO client.

### A file and a row, written in that order

The response body goes to a JSON file under `store/certificates/{date}/`, and one row goes to the
`certificates` table in `store/certificates.sqlite` with everything the body leaves out: the rule
check, both fixes and the raw WHO replies. The file is written first and the row records its path,
so a row never points at a missing file. SQLite and a folder need no database server and no cloud
account, so the whole system runs on one laptop.

### The autocode cache

Every term the term coder (autocode) has seen is kept in `data/code_cache.json`, which is tracked in
git. A rerun of the evaluation makes no container call for a known term, and two people who run the
same cases get the same codes.

### Versions in every result

Every result carries the prompt version and its SHA-1 hash, the schema version, the model and the
ICD release. Two results are comparable only when these match, and the hash changes with any edit
to the prompt, so results from different prompts never mix by accident.

### Records stay private

The api listens on the loopback address only, and the logger writes only an allowlist of fields:
the record id, the route, the reasons, the draft status and the timings. No note text and no
certificate text reaches a log. When the model endpoint is unreachable, the api answers 503 and
saves nothing, so a retry starts clean. When the WHO container is down, the record still gets a
result, routed `review` with the reason `who:unavailable`.

## System requirements

| Requirement | Version or value | Why |
|---|---|---|
| Python | 3.12 or later | The version `pyproject.toml` requires; uv installs it for you when it is missing |
| uv | any recent version | Installs the locked dependencies and runs every command |
| Docker | Docker Desktop or Docker Engine | Runs the WHO ICD-API container, the only way the WHO ships those tools |
| Free ports | 8000 for the api, 8082 for the container | Change them with `API_PORT` and `ICD_PORT` in `.env` |
| Model endpoint | An OpenAI-compatible URL and key | Set `OPENAI_BASE_URL` and `OPENAI_API_KEY` in `.env`; the model name is in `config/default.yaml` |
| Network | Internet access on the first run | Pulls the container image once, and reaches the model endpoint on every record |

The optional dependency groups add tools you only need for some commands: `notebooks` (Jupyter,
Matplotlib and Graphviz, for `automccd-lite notebooks`) and `docs` (Sphinx, for
`automccd-lite docs`). Install one with `uv sync --group notebooks`.

A record takes a few seconds, nearly all of it the model call; the WHO calls take milliseconds.
