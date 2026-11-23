"""Writing-style profiling.

Deterministic stylometrics are computed locally from the researcher's own
writing samples. An optional AI-generated qualitative description is stored
separately and labelled as an AI interpretation. Researcher overrides always
take precedence. Style describes *how* the researcher writes — it says nothing
about their intellectual positions.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'“])")
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")

BRITISH = {
    "analyse", "analysed", "analysing", "organisation", "organisational", "organise", "behaviour", "colour", "centre",
    "favour", "labour", "programme", "modelling", "travelled", "realise", "recognise", "emphasise", "utilise",
    "characterise", "prioritise", "minimise", "maximise", "optimise", "conceptualise", "operationalise", "defence",
    "licence", "whilst", "amongst", "judgement", "theorise", "globalisation", "categorise", "summarise", "honour",
}
AMERICAN = {
    "analyze", "analyzed", "analyzing", "organization", "organizational", "organize", "behavior", "color", "center",
    "favor", "labor", "program", "modeling", "traveled", "realize", "recognize", "emphasize", "utilize",
    "characterize", "prioritize", "minimize", "maximize", "optimize", "conceptualize", "operationalize", "defense",
    "license", "judgment", "theorize", "globalization", "categorize", "summarize", "honor",
}
HEDGES = {"may", "might", "could", "suggests", "suggest", "appears", "appear", "likely", "possibly", "perhaps",
          "arguably", "tends", "tend", "potentially", "seemingly", "plausibly", "indicates", "indicate"}
BOOSTERS = {"clearly", "certainly", "undoubtedly", "demonstrates", "demonstrate", "evidently", "definitely",
            "indeed", "obviously", "must", "always", "never", "establishes"}
TRANSITIONS = ["however", "moreover", "furthermore", "consequently", "therefore", "nevertheless", "in addition",
               "in contrast", "similarly", "accordingly", "thus", "hence", "notably", "conversely", "nonetheless",
               "by contrast", "in particular", "to this end", "in this regard", "against this backdrop", "taken together"]
FIRST_SING = {"i", "me", "my", "mine"}
FIRST_PLUR = {"we", "us", "our", "ours"}
CITE_PAREN = re.compile(r"\(([A-Z][A-Za-z\-]+(?: et al\.)?(?:,| and| &)[^()]{0,80}?\d{4}[a-z]?)\)")
CITE_NARR = re.compile(r"\b([A-Z][A-Za-z\-]+(?: et al\.)?) \((\d{4}[a-z]?)\)")
CITE_NUM = re.compile(r"\[\d+(?:[,–-]\s*\d+)*\]")
PASSIVE = re.compile(r"\b(is|are|was|were|been|being|be)\s+(\w+ly\s+)?(\w+ed|\w+en)\b", re.I)


def compute_metrics(texts: list[str]) -> dict:
    text = "\n\n".join(t for t in texts if t)
    paras = [p for p in re.split(r"\n\s*\n", text) if len(p.split()) >= 25]
    sents = [s for p in paras for s in _SENT.split(p) if len(s.split()) >= 3]
    words = _WORD.findall(text)
    lw = [w.lower() for w in words]
    n = max(1, len(lw))
    sl = [len(s.split()) for s in sents] or [0]
    pl = [len(_SENT.split(p)) for p in paras] or [0]
    br = sum(1 for w in lw if w in BRITISH)
    am = sum(1 for w in lw if w in AMERICAN)
    variant = "en-GB" if br > am * 1.5 else ("en-US" if am > br * 1.5 else "undetermined")
    wc = Counter(lw)
    trans = {t: len(re.findall(r"\b" + re.escape(t) + r"\b", text, re.I)) for t in TRANSITIONS}
    trans = {k: v for k, v in sorted(trans.items(), key=lambda kv: -kv[1]) if v}
    starts = Counter(" ".join(s.split()[:2]).lower().strip(",") for s in sents)
    content_words = [w for w in lw if len(w) > 6]
    paren, narr, num = len(CITE_PAREN.findall(text)), len(CITE_NARR.findall(text)), len(CITE_NUM.findall(text))
    total_cites = paren + narr + num
    return {
        "sample_words": len(words),
        "sample_sentences": len(sents),
        "sample_paragraphs": len(paras),
        "mean_sentence_length": round(statistics.mean(sl), 1),
        "median_sentence_length": statistics.median(sl),
        "sentence_length_sd": round(statistics.pstdev(sl), 1) if len(sl) > 1 else 0,
        "long_sentence_share": round(sum(1 for x in sl if x > 35) / max(1, len(sl)), 3),
        "mean_paragraph_sentences": round(statistics.mean(pl), 1),
        "type_token_ratio": round(len(set(lw[:5000])) / max(1, len(lw[:5000])), 3),
        "long_word_share": round(len(content_words) / n, 3),
        "hedges_per_1000": round(1000 * sum(wc[h] for h in HEDGES) / n, 2),
        "boosters_per_1000": round(1000 * sum(wc[b] for b in BOOSTERS) / n, 2),
        "first_person_singular_per_1000": round(1000 * sum(wc[w] for w in FIRST_SING) / n, 2),
        "first_person_plural_per_1000": round(1000 * sum(wc[w] for w in FIRST_PLUR) / n, 2),
        "passive_per_1000": round(1000 * len(PASSIVE.findall(text)) / n, 2),
        "transitions": dict(list(trans.items())[:12]),
        "frequent_sentence_openers": [k for k, v in starts.most_common(8) if v > 1],
        "frequent_academic_terms": [w for w, c in Counter(content_words).most_common(25) if c > 2],
        "spelling_variant": variant,
        "british_markers": br,
        "american_markers": am,
        "citations_per_1000": round(1000 * total_cites / n, 2),
        "citation_forms": {"parenthetical": paren, "narrative": narr, "numeric": num},
    }


def describe_metrics(m: dict) -> list[str]:
    """Plain-language, deterministic description of the metrics."""
    if not m or not m.get("sample_words"):
        return []
    out = []
    msl = m["mean_sentence_length"]
    out.append(f"Average sentence length is {msl} words ({'long' if msl > 28 else 'moderate' if msl > 18 else 'short'} for academic prose).")
    out.append(f"Paragraphs average {m['mean_paragraph_sentences']} sentences.")
    if m["hedges_per_1000"] > m["boosters_per_1000"] * 1.5:
        out.append("Claims are typically hedged rather than asserted.")
    elif m["boosters_per_1000"] > m["hedges_per_1000"]:
        out.append("Claims are often stated assertively; boosters outnumber hedges.")
    if m["first_person_plural_per_1000"] > 3:
        out.append("Uses the authorial 'we'.")
    elif m["first_person_singular_per_1000"] > 2:
        out.append("Uses first-person singular.")
    else:
        out.append("Largely impersonal voice (little first-person usage).")
    if m["transitions"]:
        out.append("Preferred connectives: " + ", ".join(list(m["transitions"])[:6]) + ".")
    if m["spelling_variant"] != "undetermined":
        out.append(f"Spelling conventions indicate {'British' if m['spelling_variant'] == 'en-GB' else 'American'} English.")
    forms = m.get("citation_forms", {})
    if sum(forms.values()):
        dom = max(forms, key=forms.get)
        out.append(f"Citation density ~{m['citations_per_1000']} per 1,000 words; predominantly {dom} citations.")
    return out


def effective_profile(metrics: dict, qualitative: dict, overrides: dict) -> dict:
    """Merge for prompting: researcher overrides > AI description > metrics."""
    return {
        "metrics_summary": describe_metrics(metrics),
        "ai_description": qualitative.get("summary", "") if qualitative else "",
        "ai_traits": qualitative.get("traits", []) if qualitative else [],
        "researcher_specified": overrides or {},
    }


def style_prompt_block(profile: dict | None) -> str:
    if not profile:
        return "No writing-style profile is available; use clear, formal academic prose."
    lines = ["Emulate the researcher's writing characteristics (style only — not their opinions):"]
    for k, v in (profile.get("researcher_specified") or {}).items():
        if v:
            lines.append(f"- [researcher-specified] {k}: {v}")
    for s in profile.get("metrics_summary", []):
        lines.append(f"- {s}")
    for t in profile.get("ai_traits", [])[:8]:
        lines.append(f"- {t}")
    lines.append("Academic rigour takes precedence over stylistic imitation.")
    return "\n".join(lines)
