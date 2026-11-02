# autoMCCD-lite

autoMCCD-lite turns one clinical death record into a WHO Medical Certificate of Cause of Death
(MCCD), coded in ICD-11. A language model writes the certificate, and the code checks it against
ten rules and the WHO's own coding tools. Each certificate comes back with a route, `auto` or
`review`, and the reason codes that say why a human must look at it.

The pipeline runs in three steps:

1. **Draft**: the model reads your record and writes a certificate in one call.
2. **Check**: the code tests the certificate against ten rules, codes every condition with the WHO's tools, and gives the model one chance to fix a broken rule and one chance to restate a weakly coded term.
3. **Decide**: the code collects every reason that keeps the certificate from automatic acceptance and sets the route.

You send one record; you get back the certificate, its ICD-11 codes, its route and its reasons, and a copy is saved in the store.

## Sections

::::{grid} 1 1 2 2
:gutter: 2

:::{grid-item-card} How-To
:link: how-to
:link-type: doc

Set up your environment, run one example record, and read the result.
:::

:::{grid-item-card} Architecture
:link: architecture
:link-type: doc

How the parts of the system fit together.
:::

:::{grid-item-card} Step 1. Draft
:link: draft
:link-type: doc

The Draft step turns your death record into a first certificate, written by the model in one call.
:::

:::{grid-item-card} Step 2. Check
:link: check
:link-type: doc

The Check step tests the drafted certificate against ten rules and codes it with the WHO's own tools.
:::

:::{grid-item-card} Step 3. Decide
:link: decide
:link-type: doc

The Decide step turns everything the first two steps found into a route and the reasons behind it.
:::

:::{grid-item-card} Technical Documentation
:link: technical
:link-type: doc

The supporting modules, the request and response fields, the store, the configuration and the commands.
:::

:::{grid-item-card} API reference
:link: reference/api
:link-type: doc

The code reference, generated from the docstrings.
:::
::::

## Architecture

![system](_static/diagrams/system.drawio.png)

The api receives your record as a JSON request and hands it to the pipeline, which runs the three steps in order and writes nothing itself. The command line tool starts the api and the WHO coding container, and the evaluation harness runs the same pipeline in-process over a whole case set. For the design in depth, read the [architecture page](architecture.md).

The [Draft step](draft.md) builds the prompt from your record's notes and asks the model for one certificate under a strict JSON schema. It hands the Check step a draft with a status: `ok`, `refused`, `truncated` or `unparsable`.

The [Check step](check.md) tests the draft against the rules V1 to V10 and, when a rule breaks, asks the model once for a fix. It then codes each Part I and Part II term with the WHO term coder (autocode), asks the WHO cause selector (DORIS) for the underlying cause, and runs the WHO quality checks (CoDEdit). Each term's code is kept in a cache file, so a term is coded once and a rerun makes no container call for it. It hands the Decide step the rule check and the coding.

The [Decide step](decide.md) lists every reason the certificate needs a human, in a fixed order. No reasons means `auto`; any reason means `review`. The api then saves the result in the store as a JSON file and a SQLite row.

## Data

| Path | Description | Source |
|---|---|---|
| `store/certificates.sqlite`, table `certificates` | One row per certified record, with the rule check and the raw WHO replies the response leaves out. | `src/automccd_lite/store.py:33` |
| `store/certificates/{yyyy-mm-dd}/{hhmmss}Z_{record_id}.json` | The response body of each certified record. | `src/automccd_lite/store.py:49` |
| `data/code_cache.json` | The term cache, `{term_key: entry}`, one autocode entry per term, shared by the api, the eval and the notebooks. | `src/automccd_lite/coding.py:62` |
| `runs/{set}_{hhmmss}Z/` | `results.json` and `summary.md` of one evaluation run. | `src/automccd_lite/eval.py:423` |

`STORE_DIR` moves the `store/` folder (`src/automccd_lite/config.py:81`).

## Source code

```
autoMCCD-lite/
├── config/default.yaml      # the draft and check settings
├── data/                    # code_cache.json, examples/, provided/, dlc/
├── docs/                    # this site
├── notebooks/               # how-to-guide.ipynb, mccd_guide.ipynb, _helpers.py
├── prompts/                 # mccd/v4.7.md, schemas/mccd.schema.json
├── src/automccd_lite/       # the package: draft, rules, coding, decide, pipeline and the supporting modules
├── tests/                   # test_check.py, test_decide.py, test_reference.py
├── .env.example             # the model endpoint, the key, the ports
└── pyproject.toml           # the package, the automccd-lite command, the dependency groups
```

| Step | Purpose | Doc | Generated from |
|---|---|---|---|
| Draft | The Draft step turns your death record into a first certificate, written by the model in one call. | [draft.md](draft.md) | `84a0a34` |
| Check | The Check step tests the drafted certificate against ten rules and codes it with the WHO's own tools. | [check.md](check.md) | `84a0a34` |
| Decide | The Decide step turns everything the first two steps found into a route and the reasons behind it. | [decide.md](decide.md) | `84a0a34` |

## Glossary

| Term | Meaning | Source |
|---|---|---|
| Medical Certificate of Cause of Death (MCCD) | The WHO form a doctor fills in after a death: the chain of conditions that led to it, and the other conditions that contributed. | `README.md:3` |
| Part I | The chain of conditions on the certificate. Line 1a is the immediate cause; the last line used is the condition that started the chain. One condition per line. | `prompts/schemas/mccd.schema.json:26` |
| Part II | Other significant conditions that contributed to the death but are not part of the Part I chain. | `prompts/schemas/mccd.schema.json:31` |
| Interval | The approximate time from the onset of a condition to the death. It must not decrease going down Part I, because the immediate cause began most recently. | `prompts/schemas/mccd.schema.json:71` |
| Underlying cause of death (UCOD) | The condition that started the chain of events leading to death. The WHO cause selector picks it from the coded certificate. | `src/automccd_lite/coding.py:155` |
| ICD-11 | The WHO's International Classification of Diseases, 11th revision. Every condition on the certificate gets an ICD-11 code. | `README.md:4` |
| WHO ICD-API container | The WHO's coding service, shipped only as a Docker image. It runs on your machine and answers the term coder, the cause selector and the quality checks. | `src/automccd_lite/cli.py:10` |
| Term coder (autocode) | The WHO endpoint that turns one written condition into an ICD-11 code. | `src/automccd_lite/coding.py:140` |
| Match score | How closely the term coder's matched text fits the written condition. A score under `check.min_match_score` counts as weak. | `src/automccd_lite/coding.py:149` |
| Cause selector (DORIS) | The WHO endpoint that applies the WHO's selection rules to the coded certificate and returns the underlying cause. It takes codes, not free text. | `src/automccd_lite/coding.py:12` |
| Quality checks (CoDEdit) | The WHO endpoint that checks a coded certificate and returns issue ids, for example an ill-defined cause or a sex the condition does not fit. | `src/automccd_lite/coding.py:167` |
| Record | One death record as the api receives it: an id, optional sex and age, optional registry metadata, and one or more notes. | `src/automccd_lite/models.py:41` |
| Note | One named piece of record text, for example `case_narrative`. Note names are unique within a record. | `src/automccd_lite/models.py:36` |
| Evidence span | A fragment copied word for word from a note to support one condition. Rule V1 checks it against the note text. | `prompts/schemas/mccd.schema.json:58` |
| Reference certificate | The answer certificate for a provided case, transcribed from the client's teaching material. The eval codes it through the same WHO tools and compares underlying causes. | `src/automccd_lite/eval.py:58` |
| Route | Where the certificate goes next: `auto` (accepted without a human) or `review` (a human looks at it). | `src/automccd_lite/models.py:110` |
| Reason code | A stable code of the form `source:what`, for example `rule:V2`, that says why the certificate needs a human. | `src/automccd_lite/decide.py:5` |
| The provided set | 17 clean teaching cases, each with its reference certificate. | `src/automccd_lite/eval.py:6` |
| The dlc set | 40 harder cases: 34 stress cases (OCR noise, cut pages, a mixed-in patient and more) and 6 coroner cases the pipeline must decline, expected `out_of_scope`. | `src/automccd_lite/eval.py:8` |

```{toctree}
:hidden:

how-to
architecture
```

```{toctree}
:caption: Steps
:hidden:

draft
check
decide
```

```{toctree}
:caption: Reference
:hidden:

technical
reference/api
```
