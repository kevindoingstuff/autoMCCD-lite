"""Step 2. Check, part one: the ten rules, and one fix when they fail.

check_rules runs the deterministic gate: the schema again, then the rules
V1 to V10 against the text of the notes. The model is asked to self-check
too, but a check by the thing being checked fails in exactly the cases that
matter, so every rule lives here, in code that cannot be talked out of its
answer.

V1   exact quote: evidence_span is verbatim in the note named by evidence_field
V2   mode of dying: no Part I line is one
V3   one cause per line
V4   no abbreviations
V5   chain shape: Part I has 1-4 lines, none empty or repeated
V6   interval order: intervals do not decrease from 1a down
V7   Part II overlap: Part II shares nothing with Part I, at most 4 entries
V8   decline consistency: a declined record has no chain and states a reason
V9   no codes in terms: no ICD-10 or ICD-11 code in a certified term
V10  no identifiers: no phone, NIC, email or street address in a term, span,
     review.note or decline_reason (names have no pattern and are not checked)

Severity: an error blocks auto and triggers the fix; a warning is recorded
for the reviewer and does not block.

fix_rules gives the model one chance to fix what failed. The fix replaces
the first draft only if it still answers the same question: it parses, and
it keeps the assessment unless the first draft broke V8 itself. Adopted or
rejected, the router adds fix:rules, so the draft never goes auto.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

import jsonschema

from automccd_lite.draft import Ask, render, system_prompt
from automccd_lite.models import Draft, Record, RuleCheck

# --------------------------------------------------------------------------
# Rule tables
# --------------------------------------------------------------------------

# V2. Modes of dying: the manner in which the heart or lungs stopped, which
# every death shares and which therefore carries no information. The AICOD
# avoid-list names five; WHO's guidance is broader, so this is broader.
#
# NOTE the qualifier logic. "Heart failure" unqualified is barred; "congestive
# heart failure" is NOT -- the avoid-list explicitly asks for the type to be
# specified, and S17's gold certificate puts congestive heart failure on line
# 1a. A validator that flags it is over-matching, and S17 exists to catch that.
MODES_OF_DYING = [
    r"cardiac arrest",
    r"cardiopulmonary arrest",
    r"cardiorespiratory arrest",
    r"asystole",
    r"ventricular fibrillation",
    r"respiratory arrest",
    r"respiratory failure",
    r"cardiorespiratory failure",
    r"multi[- ]?organ failure",
    r"multiple organ (?:dysfunction|failure)",
    r"brain death",
    r"cerebral anoxia",
    r"senility",
    r"old age",
    r"general debility",
    r"failure to thrive",
]
# Barred only when they stand alone, i.e. with no qualifying adjective. A
# qualifier names the type ("congestive", "septic") or the disease that is
# the type ("hypertensive heart failure", "spinal shock").
BARE_ONLY = {
    "heart failure": r"^(?:congestive|acute|chronic|left[- ]sided|right[- ]sided|"
    r"biventricular|systolic|diastolic|decompensated|"
    r"hypertensive|ischaemic|ischemic|rheumatic|high[- ]output)\b",
    "shock": r"^(?:septic|haemorrhagic|hemorrhagic|cardiogenic|hypovolaemic|"
    r"hypovolemic|anaphylactic|neurogenic|obstructive|toxic|"
    r"distributive|spinal|endotoxic|endotoxin|burn|traumatic)\b",
}
# Named entities in which the head is a modifier, not the condition: "Dengue
# shock syndrome" is a syndrome, "shock lung" is ARDS. Also the
# type written after the head ("heart failure with reduced ejection fraction").
# Anything else after the head -- "unspecified", "NOS", "due to X" -- leaves it
# bare.
BARE_ONLY_EXCEPTIONS = [
    r"\bshock syndrome\b",
    r"\bshock (?:lung|liver)\b",
    r"\bheart failure with (?:reduced|preserved|mildly reduced|mid[- ]range) ejection fraction\b",
]

# V4. Abbreviations that must be expanded on a certificate. Matched as whole
# tokens so that "CAD" is caught but "Cadaveric" is not.
ABBREVIATIONS = {
    "MI", "AMI", "CVA", "COPD", "CKD", "CRF", "IHD", "CAD", "DM", "T2DM",
    "NIDDM", "IDDM", "HTN", "CA", "CCF", "CHF", "PE", "DVT", "UTI", "ARF",
    "AKI", "TB", "HIV", "AIDS", "RTA", "SOB", "LVF", "RVF", "ARDS", "GI",
    "CVS", "CNS", "PCP", "ESRD", "ESRF", "MODS", "SIRS", "DIC",
}  # fmt: skip
# HIV and AIDS are conventional on certificates and appear expanded in the
# gold for S18; keep them out of the error path.
ABBREVIATION_ALLOWED = {"HIV", "AIDS", "TB"}

# V3. Conjunctions that indicate two diagnoses crammed onto one line.
MULTI_CAUSE = re.compile(r"\b(?:and|with|plus)\b|[,;/]|\+", re.I)
# ... except where the conjunction is part of a single accepted diagnosis name.
MULTI_CAUSE_EXCEPTIONS = [
    r"chronic obstructive pulmonary disease",
    r"signs? and symptoms?",
    r"head and neck",
    r"failure to thrive",
    r"non[- ]insulin[- ]dependent",
    r"heart failure with (?:reduced|preserved|mildly reduced|mid[- ]range) ejection fraction",
]

# V9. ICD-10 (A00.0) and ICD-11 (2D42, CB41.0Z) code shapes.
# An ICD-11 stem is chapter, letter, digit, alphanumeric. Demanding the letter
# in second place keeps four-letter words ("AIDS") and ISO durations ("P49Y")
# out.
ICD_CODE = re.compile(r"\b(?:[A-Z]\d{2}(?:\.\d+)?|[0-9A-Z][A-Z]\d[0-9A-Z](?:\.[0-9A-Z]+)?)\b")
# Terms that look like codes but are ordinary clinical text.
ICD_FALSE_POSITIVES = re.compile(
    r"\b(?:COVID|SARS|CD4|CD8|T2DM|HbA1c|FEV1|FVC|B12)\b"
    r"|\bP(?=T?\d)(?:\d+[YMWD])*(?:T(?:\d+[HMS])+)?\b",
    re.I,
)

# V10. Personal identifiers. Sri Lankan NIC is 9 digits + V/X, or 12 digits.
# A phone number is ten digits from a leading 0, or nine after +94, with at
# most one separator between digits. Dates ("2003-04-01", "08 10 2020") have
# eight digits or fewer, so they do not match.
PII_PATTERNS = {
    "phone": re.compile(r"(?<![\w-])(?:\+94[\s-]?|0)\d(?:[\s-]?\d){8}(?!\d)"),
    "nic": re.compile(r"\b\d{9}[VvXx]\b|\b\d{12}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    "address": re.compile(
        r"\b\d+[/,]?\d*\s+(?:[A-Z][a-z]+\s+){0,3}"
        r"(?:Road|Rd|Street|St|Lane|Ln|Avenue|Ave|Mawatha|Place)\b"
    ),
}

UNIT_HOURS = {
    "minutes": 1 / 60,
    "hours": 1.0,
    "days": 24.0,
    "weeks": 168.0,
    "months": 730.0,
    "years": 8766.0,
}

NOTE_MAX_CHARS = 300

# The short name that goes with each rule id in messages and docs.
RULE_NAMES = {
    "V1": "exact quote",
    "V2": "mode of dying",
    "V3": "one cause per line",
    "V4": "no abbreviations",
    "V5": "chain shape",
    "V6": "interval order",
    "V7": "Part II overlap",
    "V8": "decline consistency",
    "V9": "no codes in terms",
    "V10": "no identifiers",
}


# --------------------------------------------------------------------------


@dataclass
class Finding:
    rule: str
    severity: str  # "error" | "warning"
    message: str
    location: str = ""

    def __str__(self) -> str:
        """For people: 'V2 mode of dying (error, 1a): ...'."""
        where = f", {self.location}" if self.location else ""
        return f"{self.rule} {RULE_NAMES[self.rule]} ({self.severity}{where}): {self.message}"

    def brief(self) -> str:
        """For the model, in the fix request: 'V2 [1a]: ...'."""
        return f"{self.rule} [{self.location}]: {self.message}"


@dataclass
class Findings:
    record_id: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def passed(self) -> bool:
        return not self.errors

    def add(self, rule, severity, message, location=""):
        self.findings.append(Finding(rule, severity, message, location))


def _norm(s: str) -> str:
    """Fold for verbatim comparison: unicode-normalise, collapse whitespace,
    casefold. Deliberately does NOT strip punctuation -- a span that differs
    from the source by punctuation has been edited, not quoted."""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-")
    # End-of-line hyphenation is page layout, not content: OCR of a scanned
    # record splits "anticoagu-\nlants" and a reader quotes "anticoagulants".
    # Only a hyphen immediately followed by a line break is joined; an
    # in-line hyphen ("left-sided") is content and is kept.
    s = re.sub(r"(?<=\w)-[ \t]*\r?\n\s*(?=\w)", "", s)
    return re.sub(r"\s+", " ", s).strip().casefold()


def _drop_word_hyphens(s: str) -> str:
    """Hyphens between two letters are ignored on BOTH sides of V1: joining a
    line-end hyphen cannot tell 'anticoagu-/lants' (a split word) from
    'non-/insulin' (a real compound hyphen that landed on a line break), so a
    faithful quote of either form must pass. A hyphen next to
    a digit is content -- '1-2 hours' is not '12 hours' -- and spaces are kept,
    so 'left-sided' and 'left sided' still differ."""
    return re.sub(r"(?<=[^\W\d_])-(?=[^\W\d_])", "", s)


def _span_key(span: str) -> str:
    """The span side of V1. A span copied exactly up to an end-of-line hyphen
    ('congestive h-') is verbatim; _norm joins the source's line-end hyphen,
    so the span's trailing layout hyphen is dropped to match.
    Only a hyphen directly after a letter at the very end is dropped."""
    s = _norm(span)
    s = s[:-1] if re.search(r"[^\W\d_]-$", s) else s
    return _drop_word_hyphens(s)


def _source_key(text: str) -> str:
    """The source side of V1."""
    return _drop_word_hyphens(_norm(text))


def verbatim_in(span: str, text: str) -> bool:
    """V1's test: `span` is quoted from `text`, up to layout -- whitespace,
    case, typographic quotes and dashes, and hyphens a line break made."""
    return _span_key(span) in _source_key(text)


def line_label(i: int) -> str:
    """Part I line i as printed on the certificate: 1a ... 1e."""
    return "1" + "abcde"[i] if i < 5 else f"1[{i}]"


def _pii(text: str) -> str | None:
    """The first kind of personal identifier in `text`, if any."""
    return next((kind for kind, pat in PII_PATTERNS.items() if pat.search(text)), None)


def _term_words(term: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z0-9'-]*", term)


def _is_bare(term: str, head: str, qualifier_re: str) -> bool:
    """True when `head` appears in `term` with no qualifying adjective."""
    t = _norm(term)
    if head not in t:
        return False
    if any(re.search(x, t) for x in BARE_ONLY_EXCEPTIONS):
        return False
    prefix = t.split(head)[0].strip()
    if not prefix:
        return True
    # A qualifier anywhere before the head counts: "Staphylococcus aureus
    # septic shock" is septic shock with its organism in front, not bare
    # shock. The tables anchor on ^ for readability; search
    # word-by-word instead.
    words = prefix.split()
    return not any(re.match(qualifier_re, " ".join(words[i:])) for i in range(len(words)))


def apply_rules(cert: dict, source_fields: dict[str, str]) -> Findings:  # noqa: C901
    """Validate one certificate against its source record.

    source_fields maps evidence_field name -> the text of that field.
    """
    res = Findings(record_id=cert.get("record_id", "?"))
    part1 = cert.get("part1") or []
    part2 = cert.get("part2") or []
    assessment = cert.get("assessment")

    # ---- V8: decline consistency (checked first; it gates the rest) --------
    if assessment != "certifiable":
        if part1:
            res.add(
                "V8",
                "error",
                f"assessment is {assessment!r} but Part I has {len(part1)} line(s); "
                "a declined case must not also emit a chain",
                "part1",
            )
        if not cert.get("decline_reason"):
            res.add(
                "V8",
                "error",
                f"assessment is {assessment!r} but decline_reason is null",
                "decline_reason",
            )
    else:
        if cert.get("decline_reason"):
            res.add(
                "V8",
                "error",
                "assessment is 'certifiable' but decline_reason is set",
                "decline_reason",
            )
        if not part1:
            res.add("V8", "error", "certifiable but Part I is empty", "part1")

    # ---- V5: structure ----------------------------------------------------
    # The WHO international form has four Part I lines. WHO's own DORIS and
    # CoDEdit endpoints accept five (causeOfDeathCodeA..E), and S14's gold
    # certificate uses all five, so five is a warning rather than an error.
    # Six is beyond anything the API can take.
    if len(part1) > 5:
        res.add(
            "V5",
            "error",
            f"Part I has {len(part1)} lines; DORIS accepts at most five (A-E)",
            "part1",
        )
    elif len(part1) == 5:
        res.add(
            "V5",
            "warning",
            "Part I has five lines; the WHO international form has four (1a-1d), "
            "though DORIS accepts five",
            "part1",
        )
    seen: dict[str, int] = {}
    for i, c in enumerate(part1):
        line = line_label(i)
        term = (c.get("certified_term") or "").strip()
        if not term:
            res.add("V5", "error", "empty certified_term", line)
            continue
        key = _norm(term)
        if key in seen:
            res.add(
                "V5", "error", f"{term!r} already appears on line {line_label(seen[key])}", line
            )
        seen[key] = i

    # ---- per-condition checks ---------------------------------------------
    for section, conds in (("part1", part1), ("part2", part2)):
        for i, c in enumerate(conds):
            line = line_label(i) if section == "part1" else f"part2[{i}]"
            term = (c.get("certified_term") or "").strip()
            basis = c.get("basis")
            ev_field = c.get("evidence_field")
            ev_span = (c.get("evidence_span") or "").strip()

            # ---- V1: span is verbatim in the named field ------------------
            inferred = basis in ("clinical_inference", "absent")
            if inferred:
                if ev_field != "none":
                    res.add(
                        "V1",
                        "warning",
                        f"basis is {basis!r} so evidence_field should be 'none', got {ev_field!r}",
                        line,
                    )
            elif not ev_span:
                res.add("V1", "error", f"basis is {basis!r} but evidence_span is empty", line)
            elif ev_field not in source_fields:
                res.add(
                    "V1",
                    "error",
                    f"evidence_field {ev_field!r} is not present in this record "
                    f"(available: {', '.join(sorted(source_fields))})",
                    line,
                )
            elif not verbatim_in(ev_span, source_fields[ev_field]):
                res.add(
                    "V1",
                    "error",
                    f"evidence_span is not verbatim in {ev_field}: {ev_span!r}",
                    line,
                )

            if inferred and not (cert.get("review", {}).get("note") or "").strip():
                res.add(
                    "V1",
                    "warning",
                    f"basis is {basis!r} but review.note is empty; an inferred "
                    "condition needs an explanation for the reviewer",
                    line,
                )

            if not term:
                continue

            # ---- V2: modes of dying on every Part I line ------------------
            if section == "part1":
                t = _norm(term)
                for pat in MODES_OF_DYING:
                    if re.search(pat, t):
                        res.add(
                            "V2",
                            "error",
                            f"line {line} is a mode of dying ({term!r}); state the "
                            "disease that caused it",
                            line,
                        )
                        break
                else:
                    if i == 0:
                        for head, qual in BARE_ONLY.items():
                            if _is_bare(term, head, qual):
                                res.add(
                                    "V2",
                                    "error",
                                    f"line {line} is unqualified {head!r} ({term!r}); "
                                    "specify the type and, where known, the cause",
                                    line,
                                )
                                break

            # ---- V3: one cause per line -----------------------------------
            t = _norm(term)
            if not any(re.search(x, t) for x in MULTI_CAUSE_EXCEPTIONS) and MULTI_CAUSE.search(
                term
            ):
                sev = "error" if section == "part1" else "warning"
                res.add(
                    "V3",
                    sev,
                    f"{term!r} appears to carry more than one condition; "
                    "WHO allows one cause per line",
                    line,
                )

            # ---- V4: abbreviations ----------------------------------------
            for w in _term_words(term):
                if (
                    w.upper() in ABBREVIATIONS
                    and w.upper() not in ABBREVIATION_ALLOWED
                    and w.isupper()
                ):
                    res.add("V4", "error", f"{w!r} is an abbreviation; write it out in full", line)

            # ---- V9: ICD codes --------------------------------------------
            m = ICD_CODE.search(ICD_FALSE_POSITIVES.sub("", term))
            if m:
                res.add(
                    "V9",
                    "error",
                    f"certified_term contains what looks like an ICD code ({m.group(0)!r}); "
                    "coding is done downstream by WHO tooling, not here",
                    line,
                )

            # ---- V10: identifiers -----------------------------------------
            for what, blob in (("certified_term", term), ("evidence_span", ev_span)):
                kind = _pii(blob)
                if kind:
                    res.add(
                        "V10",
                        "error",
                        f"{what} contains a possible {kind}; cite the clinical "
                        "fragment, not the record header",
                        line,
                    )

    # ---- V6: interval monotonicity ----------------------------------------
    prev_hours, prev_line = None, None
    n_unknown = 0
    for i, c in enumerate(part1[:5]):
        line = line_label(i)
        interval = c.get("interval") or {}
        val, unit = interval.get("value"), interval.get("unit")
        cert_ = interval.get("certainty")
        if val is None or unit == "unknown" or cert_ == "unknown":
            # An unknown cannot be ordered. Skipped in the comparison and
            # counted, not treated as a violation -- S22's own gold gives
            # "Unknown" for both Part I intervals.
            n_unknown += 1
            continue
        try:
            hours = float(val) * UNIT_HOURS[unit]
        except (TypeError, KeyError):
            res.add("V6", "error", f"uninterpretable interval {interval!r}", line)
            continue
        if prev_hours is not None and hours < prev_hours:
            res.add(
                "V6",
                "error",
                f"onset-to-death decreases down the chain: {prev_line} is "
                f"{prev_hours:g}h but {line} is {hours:g}h. The originating "
                "condition must not be more recent than the immediate cause.",
                line,
            )
        prev_hours, prev_line = hours, line
    if n_unknown and "interval_undocumented" not in (cert.get("review", {}).get("flags") or []):
        res.add(
            "V6",
            "warning",
            f"{n_unknown} Part I interval(s) are unknown but the "
            "'interval_undocumented' review flag was not set",
            "review.flags",
        )

    # ---- V7: Part II disjoint from Part I ---------------------------------
    p1_terms = {_norm(c.get("certified_term", "")) for c in part1}
    for i, c in enumerate(part2):
        if _norm(c.get("certified_term", "")) in p1_terms:
            res.add(
                "V7",
                "error",
                f"{c.get('certified_term')!r} appears in both Part I and Part II",
                f"part2[{i}]",
            )
    if len(part2) > 4:
        res.add("V7", "warning", f"Part II has {len(part2)} entries", "part2")

    # ---- V10 on the free-text fields ---------------------------------------
    # The reviewer note and the decline reason go to a human verbatim, so they
    # are the likeliest place for a record header to leak.
    note = (cert.get("review", {}) or {}).get("note") or ""
    for what, blob in (("review.note", note), ("decline_reason", cert.get("decline_reason") or "")):
        kind = _pii(blob)
        if kind:
            res.add(
                "V10",
                "error",
                f"{what} contains a possible {kind}; describe the clinical reason, not the person",
                what,
            )

    # ---- note length (constrained decoding cannot express this) ------------
    if len(note) > NOTE_MAX_CHARS:
        res.add(
            "V5",
            "warning",
            f"review.note is {len(note)} chars; cap is {NOTE_MAX_CHARS}",
            "review.note",
        )

    return res


@lru_cache(maxsize=1)
def _schema_validator() -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(system_prompt().schema)


def validate_schema(cert: dict) -> list[str]:
    """Schema conformance, reported separately from the rules.

    Under strict structured outputs this should always pass; it stays as the
    cheap defence, and a failure feeds the fix like any other.
    """
    return [
        f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
        for e in sorted(_schema_validator().iter_errors(cert), key=lambda e: list(e.path))
    ]


def check_rules(cert: dict, fields: dict[str, str]) -> RuleCheck:
    """Schema conformance and the rules V1-V10 against the notes."""
    schema_errors = validate_schema(cert)
    res = apply_rules(cert, fields)
    review = cert.get("review") or {}
    assessment = cert.get("assessment")
    confidence = review.get("confidence")
    return RuleCheck(
        schema_ok=not schema_errors,
        rules_passed=res.passed,
        messages=[f"schema (error): {e}" for e in schema_errors[:5]]
        + [str(f) for f in res.findings],
        broken_rules=sorted({f.rule for f in res.errors}),
        assessment=assessment,
        decline_reason=cert.get("decline_reason"),
        confidence=confidence,
        flags=list(review.get("flags") or []),
        model_chain=[c.get("certified_term", "") for c in (cert.get("part1") or [])],
    )


# --------------------------------------------------------------------------
# fix_rules
# --------------------------------------------------------------------------

PREVIOUS_MAX_CHARS = 6000  # the budget the fix rates were measured with

REVISE_TEMPLATE = """{record}

---

Your previous answer for this record was:

{previous}

It failed these checks:

{failures}

Return the corrected JSON object in full. Change only what these failures require; keep
everything else as it was. If a span cannot be copied verbatim from the field you named,
shorten it to a few words that appear exactly in that field, or change the field."""


def needs_fix(draft: Draft, validation: RuleCheck | None) -> bool:
    if draft.status == "unparsable":
        return True
    return validation is not None and validation.needs_fix


def previous_text(draft: Draft) -> str:
    if draft.certificate is not None:
        return json.dumps(draft.certificate, ensure_ascii=False)[:PREVIOUS_MAX_CHARS]
    return ""


def fix_message(record: Record, draft: Draft, validation: RuleCheck | None) -> str:
    if validation is None or draft.certificate is None:
        fails = [f"The reply is not valid JSON ({draft.reason})."]
    else:
        # The failures in the wording the fix rates were measured with.
        cert = draft.certificate
        fails = [f"schema: {e}" for e in validate_schema(cert)[:5]]
        fails += [f.brief() for f in apply_rules(cert, record.fields).errors]
    return REVISE_TEMPLATE.format(
        record=render(record),
        previous=previous_text(draft),
        failures="\n".join(f"- {f}" for f in fails),
    )


def fix_rules(
    record: Record, draft: Draft, validation: RuleCheck | None, ask: Ask
) -> tuple[Draft, RuleCheck | None]:
    """One fix call. Returns the (draft, check) the pipeline goes on with:
    the fix when adopted, the first draft when rejected. Either way the
    returned draft's usage includes the fix call."""
    again = ask(fix_message(record, draft, validation))
    new_validation = (
        check_rules(again.certificate, record.fields) if again.certificate is not None else None
    )
    keeps_question = (
        validation is None
        or (new_validation is not None and new_validation.assessment == validation.assessment)
        or "V8" in validation.broken_rules
    )
    if new_validation is not None and keeps_question:
        new_validation.rules_fixed = True
        new_validation.before_fix = (
            {
                "schema_ok": validation.schema_ok,
                "rules_passed": validation.rules_passed,
                "broken_rules": validation.broken_rules,
            }
            if validation is not None
            else {"status": draft.status, "reason": draft.reason}
        )
        again.usage = draft.usage + again.usage
        return again, new_validation
    why = again.reason if again.certificate is None else "changed the assessment"
    if validation is not None:
        validation.fix_rejected = f"rejected: {why}"
    draft.usage = draft.usage + again.usage
    return draft, validation
