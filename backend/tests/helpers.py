from __future__ import annotations

import io

import pymupdf

INTRO_V1 = ("Digital trust infrastructure refers to the institutional and technical arrangements that allow parties to rely on digital "
            "transactions. In many African economies, institutional voids raise transaction costs and weaken enforcement. This paper "
            "examines how institutional capacity conditions the adoption of digital trust services by small enterprises.")
METHODS = ("We surveyed owner-managers of small and medium enterprises in Lagos and Nairobi using a stratified sampling design. "
           "Constructs were measured with established multi-item scales and analysed with partial least squares structural equation modelling.")
FINDINGS_V1 = ("Institutional capacity was positively associated with the adoption of electronic signatures and digital identity services. "
               "Firms reporting higher regulatory confidence were more likely to invest in innovation ecosystems.")
FINDINGS_V2 = ("In the revised analysis, institutional capacity remained positively associated with adoption, but the effect was moderated "
               "by firm size, with micro-enterprises benefiting least from improvements in regulatory quality.")


def make_pdf(version: int = 1, doi: str = "10.1234/rl.test.2024.001", title: str = "Institutional Capacity and Digital Trust Infrastructure in African Enterprises") -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72

    def write(text: str, size: float = 10.5, bold: bool = False, gap: float = 8):
        nonlocal y, page
        font = "hebo" if bold else "helv"
        rect = pymupdf.Rect(72, y, 523, y + 400)
        rc = page.insert_textbox(rect, text, fontsize=size, fontname=font)
        used = 400 - rc if rc >= 0 else 400
        y += used + gap
        if y > 700:
            page = doc.new_page()
            y = 72

    write(title, 17, True, 14)
    write("A. Researcher and B. Colleague", 11)
    write(f"https://doi.org/{doi}", 9)
    write("Abstract", 13, True)
    write("This study investigates the relationship between institutional capacity and digital trust adoption among African small "
          "enterprises. Using survey data, it finds that institutional capacity shapes adoption of digital identity services.")
    write("Keywords: digital trust; institutional voids; entrepreneurship; Africa", 10)
    write("1. Introduction", 13, True)
    write(INTRO_V1)
    write("2. Methodology", 13, True)
    write(METHODS)
    page = doc.new_page()
    y = 72
    write("3. Findings", 13, True)
    write(FINDINGS_V1 if version == 1 else FINDINGS_V2)
    write("References", 13, True)
    write("Khanna, T., & Palepu, K. (1997). Why focused strategies may be wrong for emerging markets. Harvard Business Review, 75(4), 41-51.")
    write("North, D. C. (1990). Institutions, institutional change and economic performance. Cambridge University Press.")
    buf = io.BytesIO(doc.tobytes())
    doc.close()
    return buf.getvalue()


def make_docx(paragraphs: list[str], title: str = "Writing sample") -> bytes:
    import docx

    d = docx.Document()
    d.add_heading(title, 0)
    for p in paragraphs:
        d.add_paragraph(p)
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()
