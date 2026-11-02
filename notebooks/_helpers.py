"""Display helpers for the two teaching notebooks.

Everything here only shows things. The pipeline lives in `automccd_lite`;
these functions turn its objects into an HTML form, a highlighted record, a
flow diagram and a few small charts, so a notebook cell stays one line.
"""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from typing import Any

import matplotlib.pyplot as plt
from IPython.display import HTML, Image, display

from automccd_lite.config import DATA_DIR, settings
from automccd_lite.models import Result, Record
from automccd_lite.rules import _source_key, _span_key

settings()  # loads .env, so the store and the clients see STORE_DIR and the keys

# The reference data-viz palette: blue for a single measure, blue/orange as the
# fixed order for auto/review, grey for anything that is not a series.
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e6e5e1"
ROUTE_COLOR = {"auto": BLUE, "review": ORANGE}

plt.rcParams.update(
    {
        "figure.dpi": 110,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.edgecolor": GRID,
        "axes.grid": True,
        "axes.grid.axis": "x",
        "grid.color": GRID,
        "axes.axisbelow": True,
        "text.color": INK,
        "axes.labelcolor": MUTED,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "font.size": 10,
    }
)


# ---------------------------------------------------------------------------
# data


def load_record(record_id: str) -> Record:
    """One of the shipped example records, data/examples/<id>.json."""
    return Record.model_validate(
        json.loads((DATA_DIR / "examples" / f"{record_id}.json").read_text(encoding="utf-8"))
    )


def load_reference(parent: str) -> dict:
    """The reference certificate (gold) of a provided scenario."""
    from automccd_lite.eval import load_reference as _load

    return _load(parent)


def show_json(obj: Any, keys: list[str] | None = None, width: int = 110) -> None:
    """Print a model or dict as indented JSON, optionally only some keys."""
    data = obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj
    if keys:
        data = {k: data.get(k) for k in keys}
    text = json.dumps(data, indent=2, ensure_ascii=False)
    print("\n".join(line if len(line) <= width else line[: width - 1] + "…" for line in text.split("\n")))


# ---------------------------------------------------------------------------
# the WHO form


def _interval(iv: dict | None) -> str:
    if not iv or iv.get("value") is None:
        return "unknown" if iv and iv.get("unit") == "unknown" else ""
    unit = iv.get("unit", "")
    val = iv["value"]
    val = int(val) if float(val).is_integer() else val
    approx = "≈ " if iv.get("certainty") == "estimated" else ""
    return f"{approx}{val} {unit}"


def render_mccd(mccd: dict | Result, codes: dict | None = None, title: str = "") -> HTML:
    """The certificate drawn as the WHO international form: Part I lines
    1a to 1d (each line is caused by the one below it), Part II beside it,
    and the ICD-11 code per line when coding has happened."""
    if isinstance(mccd, Result):
        codes = codes or (mccd.coding.codes if mccd.coding else None)
        title = title or f"{mccd.record_id}: route {mccd.route.value}"
        mccd = mccd.certificate or {}
    p1 = mccd.get("part1") or []
    p2 = mccd.get("part2") or []
    c1 = (codes or {}).get("part1") or []
    c2 = (codes or {}).get("part2") or []
    rows = []
    for i in range(max(4, len(p1))):
        label = "1" + "abcde"[i] if i < 5 else f"1[{i}]"
        if i < len(p1):
            term = html.escape(p1[i].get("certified_term") or "")
            iv = _interval(p1[i].get("interval"))
            code = html.escape(c1[i] or "") if i < len(c1) and c1[i] else ""
        else:
            term = iv = code = ""
        cause = "disease or condition directly leading to death" if i == 0 else "due to (or as a consequence of)"
        rows.append(
            f"<tr><td class='lbl'>{label}</td><td class='hint'>{cause}</td>"
            f"<td class='term'>{term}</td><td class='iv'>{iv}</td><td class='code'>{code}</td></tr>"
        )
    p2_rows = "".join(
        f"<tr><td class='term'>{html.escape(c.get('certified_term') or '')}</td>"
        f"<td class='iv'>{_interval(c.get('interval'))}</td>"
        f"<td class='code'>{html.escape(c2[i] or '') if i < len(c2) and c2[i] else ''}</td></tr>"
        for i, c in enumerate(p2)
    ) or "<tr><td class='term' colspan='3'>&nbsp;</td></tr>"
    assessment = mccd.get("assessment", "")
    decline = mccd.get("decline_reason")
    review = mccd.get("review") or {}
    head = f"<div class='t'>{html.escape(title)}</div>" if title else ""
    banner = (
        f"<div class='decline'>Not certified: <b>{html.escape(str(assessment))}</b>"
        f" ({html.escape(str(decline))})</div>"
        if assessment and assessment != "certifiable"
        else ""
    )
    return HTML(
        f"""<style>
.mccd {{ font-family: Georgia, serif; border: 2px solid #333; padding: 12px 16px; max-width: 820px; background: #fffdf7; color: #111; }}
.mccd .t {{ font: 600 13px/1.4 system-ui, sans-serif; color: #444; margin-bottom: 8px; }}
.mccd h4 {{ margin: 8px 0 4px; font: 600 12px/1.4 system-ui, sans-serif; letter-spacing: .04em; text-transform: uppercase; color: #333; }}
.mccd table {{ border-collapse: collapse; width: 100%; }}
.mccd td {{ border-bottom: 1px solid #999; padding: 5px 6px; vertical-align: bottom; font-size: 14px; }}
.mccd td.lbl {{ width: 28px; font-weight: 700; color: #333; }}
.mccd td.hint {{ width: 230px; font: italic 11px/1.3 system-ui, sans-serif; color: #666; }}
.mccd td.term {{ font-weight: 600; }}
.mccd td.iv {{ width: 110px; color: #333; font-size: 13px; }}
.mccd td.code {{ width: 80px; font: 12px/1.3 ui-monospace, monospace; color: #1a4d8f; text-align: right; }}
.mccd .note {{ font: 12px/1.45 system-ui, sans-serif; color: #444; margin-top: 10px; }}
.mccd .decline {{ background: #fde8d7; border: 1px solid #e0a070; padding: 6px 8px; font: 13px system-ui, sans-serif; margin: 6px 0; }}
</style>
<div class='mccd'>{head}{banner}
<h4>Part I</h4><table>{''.join(rows)}</table>
<h4>Part II &nbsp;<span style='font-weight:400;text-transform:none;letter-spacing:0'>other significant conditions contributing to death but not related to the disease causing it</span></h4>
<table>{p2_rows}</table>
<div class='note'>reviewer confidence: <b>{html.escape(str(review.get('confidence', '')))}</b>
&nbsp;·&nbsp; flags: {html.escape(', '.join(review.get('flags') or []) or 'none')}<br>
{html.escape(review.get('note') or '')}</div></div>"""
    )


# ---------------------------------------------------------------------------
# the record with its evidence spans


def _find_span(span: str, text: str) -> tuple[int, int] | None:
    """Where `span` sits in `text`, tolerating the same layout differences
    V1 tolerates: whitespace, case, typographic quotes and dashes."""
    key = _span_key(span)
    if not key:
        return None
    # Walk the source char by char, building the folded key alongside a map
    # back to the original offsets.
    folded, offsets = [], []
    for i, ch in enumerate(text):
        k = _source_key(ch)
        if k:
            folded.append(k)
            offsets.append(i)
        elif folded and folded[-1] != " ":
            folded.append(" ")
            offsets.append(i)
    hay = "".join(folded)
    j = hay.find(key)
    if j < 0:
        return None
    start = offsets[j]
    end = offsets[min(j + len(key), len(offsets)) - 1] + 1
    return start, end


def highlight_spans(record: Record, mccd: dict | Result) -> HTML:
    """The record text with every evidence span the model cited highlighted
    and labelled with the line it supports. This is V1 made visible: a span
    the highlighter cannot find is a span the validator rejects."""
    if isinstance(mccd, Result):
        mccd = mccd.certificate or {}
    cites = []
    for i, c in enumerate(mccd.get("part1") or []):
        cites.append(("1" + "abcde"[i], c))
    for i, c in enumerate(mccd.get("part2") or []):
        cites.append((f"II.{i + 1}", c))
    palette = [BLUE, ORANGE, AQUA, YELLOW, "#e87ba4", "#6250d6", "#008300", "#e34948"]
    blocks, legend, missing = [], [], []
    for name, text in record.fields.items():
        marks = []
        for n, (label, c) in enumerate(cites):
            if c.get("evidence_field") != name or not (c.get("evidence_span") or "").strip():
                continue
            pos = _find_span(c["evidence_span"], text)
            color = palette[n % len(palette)]
            legend.append((label, c.get("certified_term", ""), color))
            if pos:
                marks.append((pos[0], pos[1], label, color))
            else:
                missing.append((label, c["evidence_span"]))
        marks.sort()
        out, cur = [], 0
        for s, e, label, color in marks:
            if s < cur:
                continue  # overlapping spans: keep the first
            out.append(html.escape(text[cur:s]))
            out.append(
                f"<mark style='background:{color}26;border-bottom:2px solid {color};padding:1px 0'>"
                f"{html.escape(text[s:e])}<sup style='color:{color};font:700 10px system-ui'> {label}</sup></mark>"
            )
            cur = e
        out.append(html.escape(text[cur:]))
        blocks.append(f"<h4>field: {html.escape(name)}</h4><p>{''.join(out)}</p>")
    legend_html = "".join(
        f"<span style='display:inline-block;margin:2px 10px 2px 0'><b style='color:{c}'>■</b> {l} {html.escape(t)}</span>"
        for l, t, c in dict.fromkeys(legend)
    )
    miss_html = "".join(
        f"<div style='color:#b3261e'>✗ {l}: span not found verbatim: <i>{html.escape(s)}</i> (V1 fires)</div>"
        for l, s in missing
    )
    return HTML(
        "<div style='font:14px/1.6 Georgia,serif;max-width:820px;white-space:pre-wrap'>"
        f"<div style='font:12px system-ui;color:#444'>{legend_html}</div>"
        f"{''.join(blocks)}<div style='font:12px system-ui'>{miss_html}</div></div>"
    )


# ---------------------------------------------------------------------------
# the flow diagram


STEPS = [("Step 1. Draft", "draft"), ("Step 2. Check", "check"), ("Step 3. Decide", "decide")]


def flow_diagram(result: Result) -> Image | HTML:
    """The three steps left to right, each carrying what this record did
    there: the model status; the rules, the fixes, the codes and the
    underlying cause; the route and its reasons. Needs the Graphviz `dot`
    binary; without it, a text fallback."""
    v, who, ms = result.rule_check, result.who_replies, result.timings_ms
    if v:
        rules = f"rules broken: {', '.join(v.broken_rules) or 'none'}"
        fix = "rules fix: adopted" if v.rules_fixed else "rules fix: rejected" if v.fix_rejected else ""
        terms = f"terms fix: {v.terms_fixed}" if v.terms_fixed else ""
    else:
        rules, fix, terms = "no certificate to check", "", ""
    coding = (
        f"underlying cause {who.doris.get('ucod_stem_code') or '-'}\n"
        f"codes {', '.join(c or '-' for c in who.codes.get('part1') or [])}\n"
        f"CoDEdit {', '.join(who.codedit.get('issue_ids') or []) or 'clean'}"
        if who
        else (result.icd_error or "not coded")
    )
    check = "\n".join(x for x in (rules, fix, terms, coding, f"{ms.get('check', 0)} ms") if x)
    notes = {
        "draft": f"{result.draft_status} · {result.usage.calls} model call(s)\n"
        f"prompt sha1 {result.versions.prompt_sha1[:8]}\n{ms.get('draft', 0)} ms",
        "check": check,
        "decide": f"{result.route.value.upper()}\n" + ("\n".join(result.reasons) or "no reasons"),
    }
    clean = bool(v and v.rules_passed and v.schema_ok and not v.rules_fixed and who)
    fill = {
        "draft": "#e8f1fb" if result.draft_status == "ok" else "#fde8d7",
        "check": "#e8f1fb" if clean else "#fde8d7",
        "decide": "#e2f3ea" if result.route.value == "auto" else "#fde8d7",
    }
    try:
        import graphviz

        g = graphviz.Digraph(graph_attr={"rankdir": "LR", "nodesep": "0.25", "pad": "0.2"})
        g.attr("node", shape="box", style="rounded,filled", fontname="Helvetica", fontsize="10")
        for name, key in STEPS:
            g.node(key, f"{name}\n\n{notes[key]}", fillcolor=fill[key])
        for (_, a), (_, b) in zip(STEPS, STEPS[1:], strict=False):
            g.edge(a, b)
        return Image(g.pipe(format="png"))
    except Exception:  # noqa: BLE001 - no dot binary: fall back to text
        lines = [f"{name:<15} {notes[key].replace(chr(10), ' | ')}" for name, key in STEPS]
        return HTML("<pre>" + html.escape(chr(10).join(lines)) + "</pre>")


# ---------------------------------------------------------------------------
# charts over an eval run


def _bar(ax, labels, values, colors, title):
    y = range(len(labels))
    ax.barh(y, values, color=colors, height=0.55)
    ax.set_yticks(list(y), labels)
    ax.invert_yaxis()
    ax.set_title(title, loc="left", fontsize=11, color=INK)
    for i, val in enumerate(values):
        ax.text(val + max(values + [1]) * 0.02, i, f"{val}", va="center", color=INK, fontsize=10)
    ax.set_xlim(0, max(values + [1]) * 1.18)
    ax.tick_params(axis="y", length=0)


def charts(rows: list[dict], save_to: str | None = None):
    """Three small charts over a run's rows: the routes, the rules broken
    (first try against after the fix), and how the model's underlying cause
    compared with the reference's. Counts, not percentages: 17 cases are too
    few to pretend otherwise."""
    routes = Counter(r.get("route", "error") for r in rows)
    first = Counter(x for r in rows if r.get("parsed") for x in r["first_try"].get("broken_rules", []))
    final = Counter(x for r in rows if r.get("parsed") for x in r.get("broken_rules", []))
    rules = sorted(set(first) | set(final), key=lambda k: int(k[1:]))
    match = Counter(r.get("cause_match") or "not compared" for r in rows)
    order = ["exact", "same_family", "different", "not compared"]

    fig, axes = plt.subplots(1, 3, figsize=(13, 3.2), gridspec_kw={"width_ratios": [1, 1.4, 1.2]})
    _bar(
        axes[0],
        ["auto", "review"],
        [routes.get("auto", 0), routes.get("review", 0)],
        [ROUTE_COLOR["auto"], ROUTE_COLOR["review"]],
        "Routes",
    )
    ax = axes[1]
    if rules:
        y = range(len(rules))
        ax.barh([i - 0.18 for i in y], [first[k] for k in rules], height=0.34, color=ORANGE, label="first try")
        ax.barh([i + 0.18 for i in y], [final[k] for k in rules], height=0.34, color=BLUE, label="after the fix")
        ax.set_yticks(list(y), rules)
        ax.invert_yaxis()
        ax.legend(frameon=False, fontsize=9, loc="lower right")
        ax.set_xlim(0, max(list(first.values()) + list(final.values()) + [1]) * 1.18)
    else:
        ax.text(0.5, 0.5, "no rule broken", ha="center", va="center", color=MUTED)
    ax.set_title("Rules broken (cases)", loc="left", fontsize=11, color=INK)
    ax.tick_params(axis="y", length=0)
    _bar(
        axes[2],
        order,
        [match.get(k, 0) for k in order],
        [BLUE, BLUE, BLUE, GRID],
        "Underlying cause vs reference (cases)",
    )
    for ax in axes:
        ax.set_xlabel("")
    fig.tight_layout()
    if save_to:
        fig.savefig(save_to, bbox_inches="tight")
    return fig


def timings_chart(rows: list[dict], save_to: str | None = None):
    """Where the seconds go, per case: nearly all of it is the model."""
    ids = [r["id"] for r in rows]
    stages = ["draft", "check", "decide"]
    colors = {"draft": BLUE, "check": ORANGE, "decide": AQUA}
    fig, ax = plt.subplots(figsize=(10, 0.28 * len(ids) + 1))
    left = [0.0] * len(ids)
    for st in stages:
        vals = [(r.get("timings_ms") or {}).get(st, 0) / 1000 for r in rows]
        ax.barh(ids, vals, left=left, color=colors[st], height=0.6, label=st, edgecolor="white", linewidth=1)
        left = [a + b for a, b in zip(left, vals, strict=True)]
    ax.invert_yaxis()
    ax.set_xlabel("seconds")
    ax.set_title("Time per case by step", loc="left", fontsize=11, color=INK)
    ax.legend(frameon=False, fontsize=9, ncol=3, loc="lower right")
    ax.tick_params(axis="y", length=0)
    fig.tight_layout()
    if save_to:
        fig.savefig(save_to, bbox_inches="tight")
    return fig


def reason_table(rows: list[dict]) -> HTML:
    """One line per case: route, reasons, rules, underlying cause against the reference."""
    head = "<tr><th>case</th><th>route</th><th>reasons</th><th>rules first → final</th><th>cause model / reference</th><th>match</th></tr>"
    body = ""
    for r in rows:
        fp = ",".join((r.get("first_try") or {}).get("broken_rules") or []) or "–"
        fn = ",".join(r.get("broken_rules") or []) or "–"
        color = ROUTE_COLOR.get(r.get("route", ""), MUTED)
        body += (
            f"<tr><td>{r['id']}</td><td><b style='color:{color}'>{r.get('route', '')}</b></td>"
            f"<td>{', '.join(r.get('reasons') or [])}</td><td>{fp} → {fn}</td>"
            f"<td>{r.get('cause') or '–'} / {r.get('reference_cause_code') or '–'}</td>"
            f"<td>{r.get('cause_match') or ''}</td></tr>"
        )
    return HTML(
        "<style>.rt td,.rt th{font:12px/1.4 system-ui;padding:3px 8px;text-align:left;border-bottom:1px solid #e6e5e1}</style>"
        f"<table class='rt'>{head}{body}</table>"
    )


def strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


__all__ = [
    "charts",
    "display",
    "flow_diagram",
    "highlight_spans",
    "load_reference",
    "load_record",
    "reason_table",
    "render_mccd",
    "show_json",
    "timings_chart",
]
