"""Controlled execution of analysis scripts, dataset loading and recorded transformations."""
from __future__ import annotations

import hashlib
import io
import json
import os
import resource
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from app.config import get_settings

FORMATS = {".csv": "csv", ".tsv": "tsv", ".xlsx": "xlsx", ".xls": "xlsx", ".json": "json", ".sav": "sav", ".txt": "txt"}


class AnalysisError(RuntimeError):
    pass


def detect_format(filename: str) -> str:
    ext = os.path.splitext(filename.lower())[1]
    if ext not in FORMATS:
        raise AnalysisError(f"Unsupported dataset format '{ext}'. Supported: CSV, TSV, XLSX, JSON, SAV, TXT")
    return FORMATS[ext]


def load_dataframe(data: bytes, fmt: str) -> pd.DataFrame:
    try:
        if fmt == "csv":
            return pd.read_csv(io.BytesIO(data))
        if fmt == "tsv":
            return pd.read_csv(io.BytesIO(data), sep="\t")
        if fmt == "xlsx":
            return pd.read_excel(io.BytesIO(data))
        if fmt == "json":
            return pd.read_json(io.BytesIO(data))
        if fmt == "sav":
            import pyreadstat

            with tempfile.NamedTemporaryFile(suffix=".sav") as f:
                f.write(data)
                f.flush()
                df, _ = pyreadstat.read_sav(f.name)
            return df
    except Exception as e:  # noqa: BLE001
        raise AnalysisError(f"Could not parse dataset: {e}") from e
    raise AnalysisError(f"Format {fmt} is not tabular")


def infer_schema(df: pd.DataFrame) -> list[dict]:
    out = []
    for c in df.columns:
        s = df[c]
        kind = "numeric" if pd.api.types.is_numeric_dtype(s) else ("datetime" if pd.api.types.is_datetime64_any_dtype(s) else "categorical")
        if kind == "numeric" and s.nunique(dropna=True) <= 7 and s.dropna().apply(float.is_integer if s.dtype.kind == "f" else lambda _: True).all():
            kind = "numeric (possibly ordinal/categorical)"
        out.append({"name": str(c), "dtype": str(s.dtype), "kind": kind, "n_missing": int(s.isna().sum()),
                    "n_unique": int(s.nunique(dropna=True)),
                    "example_values": [str(v) for v in s.dropna().unique()[:5]]})
    return out


def _limits() -> None:  # runs in the child before exec
    s = get_settings()
    mem = s.analysis_memory_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    resource.setrlimit(resource.RLIMIT_CPU, (s.analysis_timeout_seconds, s.analysis_timeout_seconds + 5))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    os.setsid()


def run_script(code: str, data: bytes, fmt: str) -> dict:
    """Execute a template-generated script in an isolated interpreter."""
    s = get_settings()
    with tempfile.TemporaryDirectory(prefix="rl-analysis-") as tmp:
        t = Path(tmp)
        (t / "out").mkdir()
        script = t / "analysis.py"
        script.write_text(code)
        data_path = t / f"data.{fmt}"
        data_path.write_bytes(data)
        env = {"PATH": "/usr/bin:/bin", "MPLBACKEND": "Agg", "HOME": tmp, "OPENBLAS_NUM_THREADS": "1", "MPLCONFIGDIR": tmp}
        try:
            p = subprocess.run([sys.executable, "-I", str(script), str(data_path), str(t / "out")], capture_output=True, text=True,
                               timeout=s.analysis_timeout_seconds, cwd=tmp, env=env, preexec_fn=_limits)
        except subprocess.TimeoutExpired:
            raise AnalysisError(f"Analysis exceeded the {s.analysis_timeout_seconds}s time limit") from None
        marker = [ln for ln in p.stdout.splitlines() if ln.startswith("@@RESULT@@")]
        if p.returncode != 0 or not marker:
            err = (p.stderr or p.stdout).strip().splitlines()
            raise AnalysisError("Analysis failed: " + (err[-1] if err else f"exit code {p.returncode}"))
        res = json.loads(marker[-1][len("@@RESULT@@"):])
        figs = []
        for f in res.get("figures", []):
            fp = t / "out" / f["file"]
            if fp.exists():
                figs.append({**f, "bytes": fp.read_bytes()})
        res["figures"] = figs
        return res


def result_hash(res: dict) -> str:
    stable = {k: v for k, v in res.items() if k != "figures"}
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Recorded, whitelisted transformations (originals are never modified)
# ---------------------------------------------------------------------------
TRANSFORMS = {"drop_missing", "fill_missing", "recode", "filter", "reverse_score", "compute_mean", "compute_sum", "rename",
              "drop_columns", "standardize", "to_numeric"}


def apply_transformations(df: pd.DataFrame, steps: list[dict]) -> tuple[pd.DataFrame, list[dict]]:
    log = []
    d = df.copy()
    for st in steps:
        op = st.get("op")
        if op not in TRANSFORMS:
            raise AnalysisError(f"Unsupported transformation '{op}'")
        before = len(d)
        cols = st.get("columns") or ([st["column"]] if st.get("column") else [])
        missing = [c for c in cols if c not in d.columns]
        if missing:
            raise AnalysisError(f"Unknown column(s): {missing}")
        if op == "drop_missing":
            d = d.dropna(subset=cols or None)
        elif op == "fill_missing":
            c, strat = st["column"], st.get("strategy", "mean")
            val = {"mean": lambda: d[c].mean(), "median": lambda: d[c].median(), "mode": lambda: d[c].mode().iloc[0],
                   "value": lambda: st.get("value")}[strat]()
            d[c] = d[c].fillna(val)
        elif op == "recode":
            mapping = {str(k): v for k, v in (st.get("mapping") or {}).items()}
            target = st.get("new_column") or st["column"]
            d[target] = d[st["column"]].map(lambda v: mapping.get(str(v), v))
        elif op == "filter":
            c, o, v = st["column"], st.get("operator", "=="), st.get("value")
            ser = d[c]
            if o in (">", ">=", "<", "<="):
                ser = pd.to_numeric(ser, errors="coerce")
                v = float(v)
            mask = {"==": ser.astype(str) == str(v), "!=": ser.astype(str) != str(v), ">": ser > v, ">=": ser >= v, "<": ser < v,
                    "<=": ser <= v, "in": ser.astype(str).isin([str(x) for x in (v or [])])}[o]
            d = d[mask]
        elif op == "reverse_score":
            lo, hi = float(st["min"]), float(st["max"])
            for c in cols:
                d[c] = lo + hi - pd.to_numeric(d[c], errors="coerce")
        elif op in ("compute_mean", "compute_sum"):
            vals = d[cols].apply(pd.to_numeric, errors="coerce")
            d[st["new_column"]] = vals.mean(axis=1) if op == "compute_mean" else vals.sum(axis=1, min_count=1)
        elif op == "rename":
            d = d.rename(columns=st.get("mapping") or {})
        elif op == "drop_columns":
            d = d.drop(columns=cols)
        elif op == "standardize":
            for c in cols:
                x = pd.to_numeric(d[c], errors="coerce")
                d[st.get("prefix", "z_") + c] = (x - x.mean()) / x.std()
        elif op == "to_numeric":
            for c in cols:
                d[c] = pd.to_numeric(d[c], errors="coerce")
        log.append({**st, "rows_before": before, "rows_after": len(d)})
    return d.reset_index(drop=True), log


def df_preview(df: pd.DataFrame, n: int = 20) -> dict:
    head = df.head(n).replace({np.nan: None})
    return {"columns": [str(c) for c in df.columns], "rows": json.loads(head.to_json(orient="records", date_format="iso")),
            "n_rows": len(df), "n_cols": df.shape[1]}
