"""Analysis workspace tests. The CSV below is synthetic test input generated in the
test itself (seeded), used only to exercise the statistical procedures."""
import io

import numpy as np
import pandas as pd
import pytest

from tests.conftest import run_jobs


@pytest.fixture
def dataset(auth_client):
    rng = np.random.default_rng(7)
    n = 200
    x = rng.normal(0, 1, n)
    m = 0.6 * x + rng.normal(0, 1, n)
    y = 0.5 * m + 0.2 * x + rng.normal(0, 1, n)
    group = np.where(rng.random(n) > 0.5, "micro", "small")
    items = {f"t{i}": np.clip(np.round(3 + x + rng.normal(0, 0.6, n)), 1, 5) for i in range(1, 5)}
    df = pd.DataFrame({"capacity": x, "trust": m, "adoption": y, "size": group, "sector": rng.choice(["fintech", "retail", "agri"], n), **items})
    df.loc[[3, 9], "trust"] = np.nan
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    pid = auth_client.post("/api/projects", json={"title": "Analysis"}).json()["id"]
    r = auth_client.post("/api/analysis/datasets", files={"file": ("survey.csv", buf.getvalue(), "text/csv")}, data={"project_id": pid})
    assert r.status_code == 200, r.text
    return r.json()


def run(c, ds, proc, **params):
    r = c.post("/api/analysis/run", json={"dataset_id": ds["id"], "procedure": proc, "params": params})
    assert r.status_code == 200, r.text
    return r.json()


def test_dataset_inspection(auth_client, dataset):
    assert dataset["n_rows"] == 200 and dataset["n_cols"] == 9
    trust = next(c for c in dataset["schema"] if c["name"] == "trust")
    assert trust["n_missing"] == 2 and trust["kind"] == "numeric"
    d = auth_client.get(f"/api/analysis/datasets/{dataset['id']}").json()
    assert len(d["preview"]["rows"]) == 20


def test_statistical_procedures(auth_client, dataset):
    c = auth_client
    desc = run(c, dataset, "describe", columns=["capacity", "trust", "size"])
    assert desc["status"] == "succeeded" and "Descriptive" in desc["outputs"][1]["title"]
    miss = run(c, dataset, "missing")
    assert next(o for o in miss["outputs"] if o["kind"] == "json")["content"]["complete_cases"] == 198
    reg = run(c, dataset, "ols", dv="adoption", ivs=["capacity", "trust", "sector"], categorical=["sector"])
    res = next(o for o in reg["outputs"] if o["kind"] == "json")["content"]
    assert res["n"] == 198 and 0 < res["r2"] < 1
    coefs = {r["term"]: r for r in next(o for o in reg["outputs"] if o["title"] == "OLS coefficients")["content"]["rows"]}
    assert coefs['Q("trust")']["p"] < 0.001
    assert "does not by itself establish practical or theoretical importance" in reg["interpretation"]
    assert any("Homoscedasticity" in a for a in reg["assumptions"])
    t = run(c, dataset, "ttest_ind", dv="adoption", group="size")
    assert "Welch" in t["interpretation"] and "Cohen's d" in t["interpretation"]
    alpha = run(c, dataset, "cronbach_alpha", items=["t1", "t2", "t3", "t4"])
    assert next(o for o in alpha["outputs"] if o["kind"] == "json")["content"]["alpha"] > 0.7
    med = run(c, dataset, "mediation", iv="capacity", mediator="trust", dv="adoption", n_boot=300)
    mres = next(o for o in med["outputs"] if o["kind"] == "json")["content"]
    assert mres["indirect_significant"] is True and mres["indirect_ci95"][0] > 0
    mod = run(c, dataset, "moderation", dv="adoption", iv="capacity", moderator="trust")
    assert any(o["title"] == "Simple slopes" for o in mod["outputs"])
    efa = run(c, dataset, "efa", items=["t1", "t2", "t3", "t4"], n_factors=1)
    assert next(o for o in efa["outputs"] if o["kind"] == "json")["content"]["kmo"] > 0.6
    chi = run(c, dataset, "chi_square", row="size", col="sector")
    assert "Cramér's V" in chi["interpretation"]
    an = run(c, dataset, "anova_oneway", dv="adoption", group="sector")
    assert any("Tukey" in o["title"] for o in an["outputs"])
    corr = run(c, dataset, "correlation", columns=["capacity", "trust", "adoption"], method="spearman")
    assert "causation" in corr["interpretation"]


def test_visualisation_and_table_export(auth_client, dataset):
    c = auth_client
    p = run(c, dataset, "plot", kind="scatter", x="capacity", y="adoption", title="Capacity vs adoption")
    fig = next(o for o in p["outputs"] if o["kind"] == "figure")
    img = c.get(f"/api/analysis/outputs/{fig['id']}/figure")
    assert img.status_code == 200 and img.content[:8] == b"\x89PNG\r\n\x1a\n"
    d = run(c, dataset, "describe")
    tab = next(o for o in d["outputs"] if o["kind"] == "table")
    csv = c.get(f"/api/analysis/outputs/{tab['id']}/csv").text
    assert csv.startswith("variable,n,missing,mean")


def test_reproducibility_from_saved_script(auth_client, dataset, tmp_path):
    c = auth_client
    a = run(c, dataset, "ols", dv="adoption", ivs=["capacity", "trust"])
    r = c.post(f"/api/analysis/analyses/{a['id']}/reproduce").json()
    assert r["reproducible"] is True and r["original_hash"] == r["new_hash"]
    # the downloaded script runs standalone outside ResearchLab
    import json
    import subprocess
    import sys

    script = c.get(f"/api/analysis/analyses/{a['id']}/script").text
    (tmp_path / "a.py").write_text(script)
    (tmp_path / "out").mkdir()
    (tmp_path / "d.csv").write_bytes(c.get(f"/api/analysis/datasets/{dataset['id']}/file").content)
    out = subprocess.run([sys.executable, str(tmp_path / "a.py"), str(tmp_path / "d.csv"), str(tmp_path / "out")], capture_output=True, text=True, timeout=120)
    res = json.loads(out.stdout.split("@@RESULT@@")[1])
    assert abs(res["result"]["r2"] - next(o for o in a["outputs"] if o["kind"] == "json")["content"]["r2"]) < 1e-9


def test_parameters_are_validated_not_executed(auth_client, dataset):
    c = auth_client
    r = c.post("/api/analysis/run", json={"dataset_id": dataset["id"], "procedure": "ols",
                                          "params": {"dv": "adoption\"); import os; os.system('id'); (\"", "ivs": ["capacity"]}})
    assert r.status_code == 422
    r = c.post("/api/analysis/run", json={"dataset_id": dataset["id"], "procedure": "exec_python", "params": {}})
    assert r.status_code == 422


def test_transformations_require_approval_and_preserve_original(auth_client, dataset):
    c = auth_client
    steps = [{"op": "drop_missing", "columns": ["trust"]}, {"op": "reverse_score", "columns": ["t1"], "min": 1, "max": 5},
             {"op": "compute_mean", "columns": ["t1", "t2", "t3", "t4"], "new_column": "trust_scale"}]
    prev = c.post(f"/api/analysis/datasets/{dataset['id']}/transform", json={"steps": steps}).json()
    assert prev["requires_confirmation"] and prev["preview"]["n_rows"] == 198
    assert len(c.get("/api/analysis/datasets").json()) == 1  # nothing created without approval
    derived = c.post(f"/api/analysis/datasets/{dataset['id']}/transform", json={"steps": steps, "confirm": True}).json()
    assert derived["parent_dataset_id"] == dataset["id"] and derived["n_rows"] == 198
    assert [t["op"] for t in derived["transformations"]] == ["drop_missing", "reverse_score", "compute_mean"]
    orig = c.get(f"/api/analysis/datasets/{dataset['id']}").json()
    assert orig["n_rows"] == 200 and orig["content_hash"] == dataset["content_hash"]
    bad = c.post(f"/api/analysis/datasets/{dataset['id']}/transform", json={"steps": [{"op": "eval", "expr": "1"}]})
    assert bad.status_code == 422


def test_qualitative_coding_workflow(auth_client, llm):
    c = auth_client
    pid = c.post("/api/projects", json={"title": "Interviews"}).json()["id"]
    transcript = ("Interviewer: How do you decide whether to trust a digital payment platform?\n\n"
                  "Respondent: Mostly through other traders. If my neighbour uses it without problems, I trust it.\n\n"
                  "Respondent: The fees and the network outages make me hesitate.\n\n"
                  "Respondent: Regulation by the central bank gives me some confidence.")
    ds = c.post("/api/analysis/datasets", files={"file": ("interview1.txt", transcript.encode(), "text/plain")}, data={"project_id": pid}).json()
    assert ds["kind"] == "text"
    assert c.post("/api/analysis/qual/segments/from-source", json={"project_id": pid, "dataset_id": ds["id"]}).json()["segments_created"] == 4
    segs = c.get(f"/api/analysis/qual/segments?project_id={pid}").json()
    social = c.post("/api/analysis/qual/codes", json={"project_id": pid, "name": "Social proof", "theme": "Trust formation"}).json()
    inst = c.post("/api/analysis/qual/codes", json={"project_id": pid, "name": "Institutional assurance", "theme": "Trust formation"}).json()
    c.post("/api/analysis/qual/codings", json={"segment_id": segs[1]["id"], "code_id": social["id"]})
    c.post("/api/analysis/qual/codings", json={"segment_id": segs[3]["id"], "code_id": inst["id"]})
    c.post("/api/analysis/qual/codings", json={"segment_id": segs[3]["id"], "code_id": social["id"]})
    # AI suggestions arrive as proposals and do not count until approved
    c.post(f"/api/analysis/qual/suggest?project_id={pid}")
    run_jobs()
    codes = {x["name"]: x for x in c.get(f"/api/analysis/qual/codes?project_id={pid}").json()}
    assert codes["Trust barriers"]["status"] == "proposed" and codes["Trust barriers"]["origin"] == "ai"
    s = c.get(f"/api/analysis/qual/summary?project_id={pid}").json()
    assert s["code_frequencies"] == {"Social proof": 2, "Institutional assurance": 1}
    assert s["co_occurrence"] == [{"a": "Institutional assurance", "b": "Social proof", "n": 1}]
    assert s["themes"]["Trust formation"] == ["Institutional assurance", "Social proof"]
    c.put(f"/api/analysis/qual/codes/{codes['Trust barriers']['id']}", json={"status": "approved"})
    s = c.get(f"/api/analysis/qual/summary?project_id={pid}").json()
    assert "Trust barriers" in s["code_frequencies"]
