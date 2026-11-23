"""Editable manuscript exports: DOCX (python-docx), LaTeX and Markdown."""
from __future__ import annotations

import io
import re

from docx import Document as Docx
from docx.enum.text import WD_COLOR_INDEX
from docx.shared import Cm, Pt

INLINE = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*|\[(?:DATA REQUIRED|CITATION NEEDED|EVIDENCE REQUIRED)[^\]]*\]|UNVERIFIED CITATION: [\w:\-]+)")


def _add_runs(par, text: str) -> None:
    for part in INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            par.add_run(part[2:-2]).bold = True
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            par.add_run(part[1:-1]).italic = True
        elif part.startswith("[") or part.startswith("UNVERIFIED"):
            r = par.add_run(part)
            r.font.highlight_color = WD_COLOR_INDEX.YELLOW
            r.bold = True
        else:
            par.add_run(part)


def to_docx(doc: dict, author: str = "", affiliation: str = "") -> bytes:
    d = Docx()
    st = d.styles["Normal"]
    st.font.name = "Times New Roman"
    st.font.size = Pt(12)
    st.paragraph_format.line_spacing = 1.5
    for sec in d.sections:
        sec.left_margin = sec.right_margin = Cm(2.54)
    d.core_properties.title = doc["title"]
    if author:
        d.core_properties.author = author
    d.add_heading(doc["title"], level=0)
    if author:
        p = d.add_paragraph(author + (f", {affiliation}" if affiliation else ""))
        p.runs[0].italic = True
    for s in doc["sections"]:
        d.add_heading(s["heading"], level=1)
        for block in re.split(r"\n\s*\n", s["content"] or ""):
            b = block.strip()
            if not b:
                continue
            m = re.match(r"^(#{2,4})\s+(.*)$", b)
            if m and "\n" not in b:
                d.add_heading(m.group(2), level=min(len(m.group(1)), 3))
                continue
            if m:  # heading followed directly by text
                first, rest = b.split("\n", 1)
                d.add_heading(first.lstrip("# ").strip(), level=min(len(m.group(1)), 3))
                b = rest
            if b.lstrip().startswith(("- ", "* ")):
                for line in b.splitlines():
                    _add_runs(d.add_paragraph(style="List Bullet"), line.lstrip("-* ").strip())
                continue
            if b.lstrip().startswith("|"):
                rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in b.splitlines() if not re.match(r"^\s*\|?\s*:?-{2,}", ln)]
                if rows:
                    t = d.add_table(rows=len(rows), cols=max(len(r) for r in rows))
                    t.style = "Table Grid"
                    for i, row in enumerate(rows):
                        for j, cell in enumerate(row):
                            t.cell(i, j).text = cell
                continue
            _add_runs(d.add_paragraph(), " ".join(b.split()))
    if doc.get("bibliography"):
        d.add_heading("References", level=1)
        for ref in doc["bibliography"]:
            p = d.add_paragraph()
            p.paragraph_format.left_indent = Cm(1.27)
            p.paragraph_format.first_line_indent = Cm(-1.27)
            _add_runs(p, ref)
    if doc.get("unresolved"):
        d.add_heading("Unresolved citations (must be fixed before submission)", level=2)
        for k in doc["unresolved"]:
            _add_runs(d.add_paragraph(style="List Bullet"), f"UNVERIFIED CITATION: {k}")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


_LATEX_ESC = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\^{}"}


def _tex(s: str) -> str:
    s = "".join(_LATEX_ESC.get(c, c) for c in s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"\\textbf{\1}", s)
    s = re.sub(r"\*([^*]+)\*", r"\\emph{\1}", s)
    return s


def to_latex(doc: dict, author: str = "") -> str:
    out = [r"\documentclass[12pt]{article}", r"\usepackage[utf8]{inputenc}", r"\usepackage[margin=2.54cm]{geometry}",
           r"\usepackage{setspace}\onehalfspacing", f"\\title{{{_tex(doc['title'])}}}", f"\\author{{{_tex(author)}}}",
           r"\begin{document}", r"\maketitle"]
    for s in doc["sections"]:
        if s["key"] == "abstract":
            out += [r"\begin{abstract}", _tex(s["content"]), r"\end{abstract}"]
            continue
        out.append(f"\\section{{{_tex(s['heading'])}}}")
        for block in re.split(r"\n\s*\n", s["content"] or ""):
            m = re.match(r"^(#{2,4})\s+(.*)$", block.strip())
            if m and "\n" not in block.strip():
                out.append(("\\subsection" if len(m.group(1)) == 2 else "\\subsubsection") + f"{{{_tex(m.group(2))}}}")
            elif block.strip():
                out.append(_tex(" ".join(block.split())) + "\n")
    if doc.get("bibliography"):
        out += [r"\section*{References}", r"\begin{list}{}{\leftmargin=1.27cm \itemindent=-1.27cm}"]
        out += [f"\\item {_tex(r)}" for r in doc["bibliography"]]
        out.append(r"\end{list}")
    out.append(r"\end{document}")
    return "\n".join(out)


def to_markdown(doc: dict) -> str:
    out = [f"# {doc['title']}\n"]
    for s in doc["sections"]:
        out.append(f"## {s['heading']}\n\n{(s['content'] or '').replace(chr(10) + '## ', chr(10) + '### ')}\n")
    if doc.get("bibliography"):
        out.append("## References\n\n" + "\n\n".join(doc["bibliography"]))
    return "\n".join(out)
