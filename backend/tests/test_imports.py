import json
import tempfile

import pandas as pd

BIB = r"""@article{webb2009,
  author = {Webb, Justin W. and Tihanyi, Laszlo and Ireland, R. Duane},
  title = {You say illegal, I say legitimate: Entrepreneurship in the informal economy},
  journal = {Academy of Management Review},
  year = {2009},
  volume = {34},
  number = {3},
  pages = {492--510},
  doi = {10.5465/amr.2009.40632826}
}
"""

RIS = """TY  - JOUR
AU  - North, Douglass C.
TI  - Institutions
JO  - Journal of Economic Perspectives
PY  - 1991
VL  - 5
IS  - 1
SP  - 97
EP  - 112
DO  - 10.1257/jep.5.1.97
ER  -
"""

CSL = [{"id": "khanna1997", "type": "article-journal", "title": "Why focused strategies may be wrong for emerging markets",
        "author": [{"family": "Khanna", "given": "Tarun"}, {"family": "Palepu", "given": "Krishna"}],
        "issued": {"date-parts": [[1997]]}, "container-title": "Harvard Business Review", "volume": 75, "issue": 4, "page": "41-51"}]


def test_reference_file_imports(auth_client):
    c = auth_client
    pid = c.post("/api/projects", json={"title": "Imports"}).json()["id"]
    r = c.post(f"/api/literature/references/import?project_id={pid}", files={"file": ("lib.bib", BIB.encode(), "text/plain")}).json()
    assert r["citekeys"] == ["webb2009"]
    r = c.post(f"/api/literature/references/import?project_id={pid}", files={"file": ("lib.ris", RIS.encode(), "text/plain")}).json()
    assert r["imported"] == 1
    r = c.post(f"/api/literature/references/import?project_id={pid}", files={"file": ("lib.json", json.dumps(CSL).encode(), "application/json")}).json()
    assert r["citekeys"] == ["khanna1997"]
    refs = {x["citekey"]: x for x in c.get(f"/api/literature/references?project_id={pid}").json()}
    webb = refs["webb2009"]["csl"]
    assert webb["authors"][1] == {"family": "Tihanyi", "given": "Laszlo"} and webb["pages"] == "492-510" and webb["doi"] == "10.5465/amr.2009.40632826"
    north = next(v for k, v in refs.items() if k.startswith("north1991"))
    assert north["csl"]["pages"] == "97-112" and north["csl"]["volume"] == "5"
    assert refs["khanna1997"]["formatted"].startswith("Khanna, T., & Palepu, K. (1997).")
    assert all(x["verification_status"] == "unverified" for x in refs.values())  # imports are never assumed verified


def test_spss_sav_dataset(auth_client):
    import pyreadstat

    df = pd.DataFrame({"score": [1.0, 2.0, 3.0, 4.0, 5.0], "group": [1.0, 1.0, 2.0, 2.0, 2.0]})
    with tempfile.NamedTemporaryFile(suffix=".sav") as f:
        pyreadstat.write_sav(df, f.name)
        data = open(f.name, "rb").read()
    d = auth_client.post("/api/analysis/datasets", files={"file": ("survey.sav", data, "application/octet-stream")}).json()
    assert d["file_format"] == "sav" and d["n_rows"] == 5
    r = auth_client.post("/api/analysis/run", json={"dataset_id": d["id"], "procedure": "describe", "params": {}}).json()
    assert r["status"] == "succeeded"
    assert next(o for o in r["outputs"] if o["kind"] == "json")["content"]["numeric"][0]["mean"] == 3.0
