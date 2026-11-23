"""Statistical procedures as reproducible, standalone Python scripts.

Security model: the AI never writes executable code. Each procedure is a
fixed, reviewed template; researcher parameters are validated against the
dataset schema and embedded as a JSON literal (no string interpolation of
code). Scripts run in a separate interpreter with CPU/memory/time limits.
"""
from __future__ import annotations

import json
import textwrap

HEADER = r'''
# ResearchLab reproducible analysis script
# Procedure: {procedure}
# Usage: python this_script.py <dataset_path> <output_dir>
# Requires: pandas, numpy, scipy, statsmodels, matplotlib (+ openpyxl/pyreadstat for xlsx/sav)
import json, sys, os, warnings
import numpy as np
import pandas as pd
from scipy import stats
warnings.filterwarnings("ignore")
PARAMS = json.loads({params_json!r})
DATA_PATH, OUT_DIR = sys.argv[1], sys.argv[2]
FORMAT = {fmt!r}
np.random.seed(int(PARAMS.get("seed", 12345)))

def load():
    if FORMAT == "csv": return pd.read_csv(DATA_PATH)
    if FORMAT == "tsv": return pd.read_csv(DATA_PATH, sep="\t")
    if FORMAT == "xlsx": return pd.read_excel(DATA_PATH, sheet_name=PARAMS.get("sheet", 0))
    if FORMAT == "json": return pd.read_json(DATA_PATH)
    if FORMAT == "sav":
        import pyreadstat
        df, _ = pyreadstat.read_sav(DATA_PATH)
        return df
    raise SystemExit("unsupported format")

def clean(o):
    if isinstance(o, dict): return {{str(k): clean(v) for k, v in o.items()}}
    if isinstance(o, (list, tuple)): return [clean(v) for v in o]
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.floating, float)):
        return None if (o != o or o in (float("inf"), float("-inf"))) else round(float(o), 6)
    if isinstance(o, np.bool_): return bool(o)
    return o

def emit(result, tables=None, figures=None, assumptions=None, warnings_=None):
    print("@@RESULT@@" + json.dumps(clean({{"result": result, "tables": tables or [], "figures": figures or [],
        "assumptions": assumptions or [], "warnings": warnings_ or []}})))

df = load()
'''

BODIES: dict[str, str] = {}

BODIES["describe"] = r'''
cols = PARAMS.get("columns") or list(df.columns)
num = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
cat = [c for c in cols if c not in num]
rows = []
for c in num:
    s = df[c].dropna()
    rows.append({"variable": c, "n": int(s.count()), "missing": int(df[c].isna().sum()), "mean": s.mean(), "sd": s.std(),
                 "min": s.min(), "q1": s.quantile(.25), "median": s.median(), "q3": s.quantile(.75), "max": s.max(),
                 "skewness": stats.skew(s) if len(s) > 2 else None, "kurtosis": stats.kurtosis(s) if len(s) > 3 else None})
cats = []
for c in cat:
    vc = df[c].value_counts(dropna=False).head(15)
    cats.append({"variable": c, "n_unique": int(df[c].nunique()), "top": [{"value": str(k), "n": int(v), "pct": 100 * v / len(df)} for k, v in vc.items()]})
emit({"n_rows": len(df), "numeric": rows, "categorical": cats},
     tables=[{"title": "Descriptive statistics", "columns": ["variable","n","missing","mean","sd","min","median","max","skewness","kurtosis"], "rows": rows}])
'''

BODIES["missing"] = r'''
rows = [{"variable": c, "missing": int(df[c].isna().sum()), "pct": 100 * df[c].isna().mean()} for c in df.columns]
complete = int(df.dropna().shape[0])
emit({"n_rows": len(df), "complete_cases": complete, "complete_pct": 100 * complete / max(1, len(df)), "by_variable": rows},
     tables=[{"title": "Missing values", "columns": ["variable", "missing", "pct"], "rows": rows}])
'''

BODIES["frequencies"] = r'''
c = PARAMS["column"]
vc = df[c].value_counts(dropna=not PARAMS.get("include_missing", True))
rows = [{"value": str(k), "n": int(v), "pct": 100 * v / vc.sum(), "cum_pct": 100 * vc.loc[:k].sum() / vc.sum()} for k, v in vc.items()]
emit({"variable": c, "n": int(vc.sum()), "levels": len(rows)}, tables=[{"title": f"Frequencies: {c}", "columns": ["value","n","pct","cum_pct"], "rows": rows}])
'''

BODIES["correlation"] = r'''
cols, method = PARAMS["columns"], PARAMS.get("method", "pearson")
d = df[cols].apply(pd.to_numeric, errors="coerce")
fn = {"pearson": stats.pearsonr, "spearman": stats.spearmanr, "kendall": stats.kendalltau}[method]
r = {a: {} for a in cols}; p = {a: {} for a in cols}; n = {a: {} for a in cols}
for a in cols:
    for b in cols:
        pair = d[[a, b]].dropna()
        if a == b: r[a][b], p[a][b], n[a][b] = 1.0, 0.0, len(pair); continue
        res = fn(pair[a], pair[b]) if len(pair) > 2 else (np.nan, np.nan)
        r[a][b], p[a][b], n[a][b] = float(res[0]), float(res[1]), len(pair)
rows = [{"variable": a, **{b: r[a][b] for b in cols}} for a in cols]
emit({"method": method, "r": r, "p": p, "n": n},
     tables=[{"title": f"{method.title()} correlations", "columns": ["variable"] + cols, "rows": rows}],
     assumptions=["Pearson assumes linearity and approximately bivariate-normal data; Spearman/Kendall are rank-based."] )
'''

BODIES["ttest_ind"] = r'''
dv, grp = PARAMS["dv"], PARAMS["group"]
d = df[[dv, grp]].dropna()
levels = PARAMS.get("levels") or sorted(d[grp].unique().tolist(), key=str)[:2]
if len(levels) != 2: raise SystemExit("group variable needs exactly two levels")
a = pd.to_numeric(d[d[grp] == levels[0]][dv], errors="coerce").dropna()
b = pd.to_numeric(d[d[grp] == levels[1]][dv], errors="coerce").dropna()
equal = bool(PARAMS.get("equal_var", False))
t = stats.ttest_ind(a, b, equal_var=equal)
sp = np.sqrt(((len(a)-1)*a.var() + (len(b)-1)*b.var()) / (len(a)+len(b)-2))
dcoh = (a.mean() - b.mean()) / sp if sp else None
lev = stats.levene(a, b)
sh = [stats.shapiro(x) if 3 <= len(x) <= 5000 else None for x in (a, b)]
df_ = (len(a) + len(b) - 2) if equal else ((a.var()/len(a) + b.var()/len(b))**2 / ((a.var()/len(a))**2/(len(a)-1) + (b.var()/len(b))**2/(len(b)-1)))
emit({"test": "Student t" if equal else "Welch t", "t": t.statistic, "df": df_, "p": t.pvalue, "cohens_d": dcoh,
      "groups": [{"level": str(levels[0]), "n": len(a), "mean": a.mean(), "sd": a.std()}, {"level": str(levels[1]), "n": len(b), "mean": b.mean(), "sd": b.std()}],
      "levene": {"W": lev.statistic, "p": lev.pvalue}, "shapiro": [{"W": s.statistic, "p": s.pvalue} if s else None for s in sh]},
     assumptions=["Independent observations", "Approximately normal DV within groups (Shapiro-Wilk reported)",
                  "Welch's test does not assume equal variances" if not equal else "Equal variances assumed (Levene reported)"])
'''

BODIES["ttest_paired"] = r'''
x, y = PARAMS["a"], PARAMS["b"]
d = df[[x, y]].apply(pd.to_numeric, errors="coerce").dropna()
t = stats.ttest_rel(d[x], d[y])
diff = d[x] - d[y]
emit({"t": t.statistic, "df": len(d) - 1, "p": t.pvalue, "n": len(d), "mean_difference": diff.mean(), "sd_difference": diff.std(),
      "cohens_dz": diff.mean() / diff.std() if diff.std() else None,
      "shapiro_differences": ({"W": stats.shapiro(diff).statistic, "p": stats.shapiro(diff).pvalue} if 3 <= len(diff) <= 5000 else None)},
     assumptions=["Paired observations", "Differences approximately normal"])
'''

BODIES["anova_oneway"] = r'''
import statsmodels.api as sm
from statsmodels.formula.api import ols
from statsmodels.stats.multicomp import pairwise_tukeyhsd
dv, grp = PARAMS["dv"], PARAMS["group"]
d = df[[dv, grp]].dropna().rename(columns={dv: "y", grp: "g"})
d["y"] = pd.to_numeric(d["y"], errors="coerce"); d = d.dropna(); d["g"] = d["g"].astype(str)
model = ols("y ~ C(g)", data=d).fit()
tab = sm.stats.anova_lm(model, typ=2)
ss_b, ss_w = tab["sum_sq"].iloc[0], tab["sum_sq"].iloc[1]
groups = [grp_df["y"] for _, grp_df in d.groupby("g")]
lev = stats.levene(*groups)
tk = pairwise_tukeyhsd(d["y"], d["g"])
tk_rows = [{"group1": str(r[0]), "group2": str(r[1]), "meandiff": float(r[2]), "p_adj": float(r[3]), "lower": float(r[4]), "upper": float(r[5]), "reject": bool(r[6])} for r in tk._results_table.data[1:]]
desc = [{"group": k, "n": len(v), "mean": v["y"].mean(), "sd": v["y"].std()} for k, v in d.groupby("g")]
emit({"F": tab["F"].iloc[0], "df_between": tab["df"].iloc[0], "df_within": tab["df"].iloc[1], "p": tab["PR(>F)"].iloc[0],
      "eta_squared": ss_b / (ss_b + ss_w), "levene": {"W": lev.statistic, "p": lev.pvalue}, "groups": desc},
     tables=[{"title": "Group descriptives", "columns": ["group","n","mean","sd"], "rows": desc},
             {"title": "Tukey HSD post-hoc comparisons", "columns": ["group1","group2","meandiff","p_adj","lower","upper","reject"], "rows": tk_rows}],
     assumptions=["Independent observations", "Normal residuals", "Homogeneity of variance (Levene reported)"])
'''

BODIES["chi_square"] = r'''
r_, c_ = PARAMS["row"], PARAMS["col"]
ct = pd.crosstab(df[r_], df[c_])
chi2, p, dof, exp = stats.chi2_contingency(ct, correction=bool(PARAMS.get("yates", False)))
n = ct.values.sum(); k = min(ct.shape) - 1
low = float((exp < 5).mean())
rows = [{"row": str(i), **{str(c): int(ct.loc[i, c]) for c in ct.columns}} for i in ct.index]
emit({"chi2": chi2, "dof": dof, "p": p, "n": int(n), "cramers_v": np.sqrt(chi2 / (n * k)) if k else None, "share_expected_below_5": low},
     tables=[{"title": f"Crosstab {r_} x {c_}", "columns": ["row"] + [str(c) for c in ct.columns], "rows": rows}],
     warnings_=(["More than 20% of expected counts are below 5; consider Fisher's exact test or collapsing categories."] if low > .2 else []),
     assumptions=["Independent observations", "Expected counts mostly >= 5"])
'''

BODIES["mann_whitney"] = r'''
dv, grp = PARAMS["dv"], PARAMS["group"]
d = df[[dv, grp]].dropna()
levels = PARAMS.get("levels") or sorted(d[grp].unique().tolist(), key=str)[:2]
a = pd.to_numeric(d[d[grp] == levels[0]][dv], errors="coerce").dropna(); b = pd.to_numeric(d[d[grp] == levels[1]][dv], errors="coerce").dropna()
u = stats.mannwhitneyu(a, b, alternative="two-sided")
emit({"U": u.statistic, "p": u.pvalue, "rank_biserial": 1 - 2 * u.statistic / (len(a) * len(b)),
      "groups": [{"level": str(levels[0]), "n": len(a), "median": a.median()}, {"level": str(levels[1]), "n": len(b), "median": b.median()}]},
     assumptions=["Independent observations", "Ordinal or continuous DV"])
'''

BODIES["kruskal"] = r'''
dv, grp = PARAMS["dv"], PARAMS["group"]
d = df[[dv, grp]].dropna()
groups = [pd.to_numeric(g[dv], errors="coerce").dropna() for _, g in d.groupby(grp)]
h = stats.kruskal(*groups)
n = sum(len(g) for g in groups); k = len(groups)
emit({"H": h.statistic, "df": k - 1, "p": h.pvalue, "epsilon_squared": (h.statistic - k + 1) / (n - k) if n > k else None, "n": n})
'''

_REG_COMMON = r'''
import statsmodels.formula.api as smf
from statsmodels.stats.outliers_influence import variance_inflation_factor
def q(c): return 'Q("%s")' % c.replace('"', '')
def coef_table(res):
    ci = res.conf_int()
    return [{"term": t, "b": res.params[t], "se": res.bse[t], "stat": res.tvalues[t], "p": res.pvalues[t], "ci_low": ci.loc[t, 0], "ci_high": ci.loc[t, 1]} for t in res.params.index]
'''

BODIES["ols"] = _REG_COMMON + r'''
from statsmodels.stats.diagnostic import het_breuschpagan
from statsmodels.stats.stattools import durbin_watson
dv, ivs, cats = PARAMS["dv"], PARAMS["ivs"], set(PARAMS.get("categorical") or [])
d = df[[dv] + ivs].dropna()
for c in [dv] + [i for i in ivs if i not in cats]: d[c] = pd.to_numeric(d[c], errors="coerce")
d = d.dropna()
formula = q(dv) + " ~ " + " + ".join(("C(%s)" % q(i)) if i in cats else q(i) for i in ivs)
res = smf.ols(formula, data=d).fit(cov_type=PARAMS.get("cov_type", "nonrobust"))
X = res.model.exog
vif = [{"term": res.model.exog_names[i], "vif": variance_inflation_factor(X, i)} for i in range(1, X.shape[1])] if X.shape[1] > 2 else []
bp = het_breuschpagan(res.resid, res.model.exog)
emit({"formula": formula, "n": int(res.nobs), "r2": res.rsquared, "adj_r2": res.rsquared_adj, "F": res.fvalue, "F_p": res.f_pvalue,
      "df_model": res.df_model, "df_resid": res.df_resid, "aic": res.aic, "bic": res.bic, "cov_type": PARAMS.get("cov_type", "nonrobust"),
      "breusch_pagan": {"lm": bp[0], "p": bp[1]}, "durbin_watson": durbin_watson(res.resid), "vif": vif,
      "residual_normality_jarque_bera_p": stats.jarque_bera(res.resid).pvalue},
     tables=[{"title": "OLS coefficients", "columns": ["term","b","se","stat","p","ci_low","ci_high"], "rows": coef_table(res)}],
     assumptions=["Linearity", "Independent errors (Durbin-Watson reported)", "Homoscedasticity (Breusch-Pagan reported; use HC3 if violated)",
                  "No severe multicollinearity (VIF reported)", "Approximately normal residuals"])
'''

BODIES["logit"] = _REG_COMMON + r'''
dv, ivs, cats = PARAMS["dv"], PARAMS["ivs"], set(PARAMS.get("categorical") or [])
d = df[[dv] + ivs].dropna()
for c in [i for i in ivs if i not in cats]: d[c] = pd.to_numeric(d[c], errors="coerce")
d = d.dropna()
vals = sorted(d[dv].unique().tolist(), key=str)
if len(vals) != 2: raise SystemExit("logistic regression needs a binary outcome")
d["_y"] = (d[dv] == (PARAMS.get("positive") if PARAMS.get("positive") is not None else vals[1])).astype(int)
formula = "_y ~ " + " + ".join(("C(%s)" % q(i)) if i in cats else q(i) for i in ivs)
res = smf.logit(formula, data=d).fit(disp=0)
rows = coef_table(res)
for r in rows: r["odds_ratio"] = float(np.exp(r["b"]))
emit({"formula": formula, "n": int(res.nobs), "pseudo_r2_mcfadden": res.prsquared, "llr_p": res.llr_pvalue, "aic": res.aic,
      "positive_class": str(PARAMS.get("positive") if PARAMS.get("positive") is not None else vals[1])},
     tables=[{"title": "Logistic regression coefficients", "columns": ["term","b","se","stat","p","odds_ratio","ci_low","ci_high"], "rows": rows}],
     assumptions=["Binary outcome", "Independent observations", "Linearity of logit for continuous predictors", "No severe multicollinearity"])
'''

BODIES["cronbach_alpha"] = r'''
items = PARAMS["items"]
d = df[items].apply(pd.to_numeric, errors="coerce").dropna()
def alpha(x):
    k = x.shape[1]; return (k / (k - 1)) * (1 - x.var(ddof=1).sum() / x.sum(axis=1).var(ddof=1)) if k > 1 else None
tot = d.sum(axis=1)
rows = [{"item": c, "mean": d[c].mean(), "sd": d[c].std(), "corrected_item_total_r": stats.pearsonr(d[c], tot - d[c])[0],
         "alpha_if_deleted": alpha(d.drop(columns=[c]))} for c in items]
emit({"alpha": alpha(d), "n": len(d), "k": len(items)},
     tables=[{"title": "Item statistics", "columns": ["item","mean","sd","corrected_item_total_r","alpha_if_deleted"], "rows": rows}],
     assumptions=["Items measure a single construct (tau-equivalence); consider omega for congeneric items"])
'''

BODIES["efa"] = r'''
items, nf, rot = PARAMS["items"], int(PARAMS.get("n_factors", 1)), PARAMS.get("rotation", "varimax")
d = df[items].apply(pd.to_numeric, errors="coerce").dropna()
R = np.corrcoef(d.values, rowvar=False); p = len(items); n = len(d)
eig = np.sort(np.linalg.eigvalsh(R))[::-1]
chi = -(n - 1 - (2 * p + 5) / 6) * np.log(np.linalg.det(R)); bart_df = p * (p - 1) / 2
inv = np.linalg.inv(R); partial = -inv / np.sqrt(np.outer(np.diag(inv), np.diag(inv)))
np.fill_diagonal(partial, 0); r2 = R.copy(); np.fill_diagonal(r2, 0)
kmo = (r2 ** 2).sum() / ((r2 ** 2).sum() + (partial ** 2).sum())
from statsmodels.multivariate.factor import Factor
fa = Factor(d.values, n_factor=nf, method="pa").fit()
if nf > 1 and rot in ("varimax", "promax", "oblimin", "quartimax"): fa.rotate(rot)
L = fa.loadings
rows = [{"item": items[i], **{f"F{j+1}": L[i, j] for j in range(nf)}, "communality": float((L[i] ** 2).sum())} for i in range(p)]
emit({"n": n, "n_factors": nf, "rotation": rot, "eigenvalues": eig.tolist(), "kmo": kmo,
      "bartlett": {"chi2": chi, "df": bart_df, "p": stats.chi2.sf(chi, bart_df)}, "kaiser_criterion_factors": int((eig > 1).sum())},
     tables=[{"title": "Factor loadings (principal axis)", "columns": ["item"] + [f"F{j+1}" for j in range(nf)] + ["communality"], "rows": rows}],
     assumptions=["KMO >= .60 and significant Bartlett test indicate factorability", "Adequate sample size (n >= 5-10 per item)"])
'''

BODIES["moderation"] = _REG_COMMON + r'''
y, x, w, ctrl = PARAMS["dv"], PARAMS["iv"], PARAMS["moderator"], PARAMS.get("controls") or []
d = df[[y, x, w] + ctrl].apply(pd.to_numeric, errors="coerce").dropna().rename(columns={y: "Y", x: "X", w: "W"})
if PARAMS.get("center", True): d["X"] -= d["X"].mean(); d["W"] -= d["W"].mean()
formula = "Y ~ X * W" + "".join(" + " + q(c) for c in ctrl)
res = smf.ols(formula, data=d).fit(cov_type=PARAMS.get("cov_type", "HC3"))
sd = d["W"].std(); slopes = []
for lab, val in (("-1 SD", -sd), ("mean", 0.0), ("+1 SD", sd)):
    b = res.params["X"] + res.params["X:W"] * val
    cov = res.cov_params(); se = np.sqrt(cov.loc["X", "X"] + val ** 2 * cov.loc["X:W", "X:W"] + 2 * val * cov.loc["X", "X:W"])
    slopes.append({"moderator_level": lab, "simple_slope": b, "se": se, "p": 2 * stats.norm.sf(abs(b / se))})
emit({"formula": formula, "n": int(res.nobs), "r2": res.rsquared, "interaction_b": res.params["X:W"], "interaction_p": res.pvalues["X:W"],
      "centered": bool(PARAMS.get("center", True)), "variables": {"Y": y, "X": x, "W": w}},
     tables=[{"title": "Moderation model", "columns": ["term","b","se","stat","p","ci_low","ci_high"], "rows": coef_table(res)},
             {"title": "Simple slopes", "columns": ["moderator_level","simple_slope","se","p"], "rows": slopes}],
     assumptions=["OLS assumptions", "Predictors mean-centred for interpretability", "HC3 robust standard errors by default"])
'''

BODIES["mediation"] = r'''
import statsmodels.api as sm
x, m, y, ctrl = PARAMS["iv"], PARAMS["mediator"], PARAMS["dv"], PARAMS.get("controls") or []
B = int(PARAMS.get("n_boot", 2000))
d = df[[x, m, y] + ctrl].apply(pd.to_numeric, errors="coerce").dropna()
def paths(dd):
    C = dd[ctrl].values if ctrl else np.empty((len(dd), 0))
    Xa = sm.add_constant(np.column_stack([dd[x].values, C]))
    a = sm.OLS(dd[m].values, Xa).fit()
    Xb = sm.add_constant(np.column_stack([dd[x].values, dd[m].values, C]))
    b = sm.OLS(dd[y].values, Xb).fit()
    c = sm.OLS(dd[y].values, Xa).fit()
    return a, b, c
a, b, c = paths(d)
ind = a.params[1] * b.params[2]
rng = np.random.default_rng(int(PARAMS.get("seed", 12345))); boots = []
vals = d.reset_index(drop=True)
for _ in range(B):
    s = vals.iloc[rng.integers(0, len(vals), len(vals))]
    try:
        aa, bb, _c = paths(s); boots.append(aa.params[1] * bb.params[2])
    except Exception: pass
lo, hi = np.percentile(boots, [2.5, 97.5])
emit({"n": len(d), "a": a.params[1], "a_p": a.pvalues[1], "b": b.params[2], "b_p": b.pvalues[2], "c_total": c.params[1], "c_total_p": c.pvalues[1],
      "c_direct": b.params[1], "c_direct_p": b.pvalues[1], "indirect": ind, "indirect_ci95": [lo, hi], "bootstrap_samples": len(boots),
      "indirect_significant": bool(lo > 0 or hi < 0), "variables": {"X": x, "M": m, "Y": y, "controls": ctrl}},
     assumptions=["Causal ordering X -> M -> Y is a theoretical assumption the data cannot establish", "No unmeasured confounding of M-Y",
                  "Percentile bootstrap CI for the indirect effect"])
'''

BODIES["plot"] = r'''
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
kind = PARAMS["kind"]; fig, ax = plt.subplots(figsize=(7, 4.5), dpi=150)
if kind == "histogram":
    pd.to_numeric(df[PARAMS["x"]], errors="coerce").dropna().plot.hist(ax=ax, bins=int(PARAMS.get("bins", 20)), color="#3b5b7a", edgecolor="white"); ax.set_xlabel(PARAMS["x"])
elif kind == "scatter":
    dd = df[[PARAMS["x"], PARAMS["y"]]].apply(pd.to_numeric, errors="coerce").dropna()
    ax.scatter(dd[PARAMS["x"]], dd[PARAMS["y"]], s=14, alpha=.7, color="#3b5b7a")
    if PARAMS.get("fit_line", True) and len(dd) > 2:
        k, b0 = np.polyfit(dd[PARAMS["x"]], dd[PARAMS["y"]], 1); xs = np.linspace(dd[PARAMS["x"]].min(), dd[PARAMS["x"]].max(), 50); ax.plot(xs, k * xs + b0, color="#b5543c")
    ax.set_xlabel(PARAMS["x"]); ax.set_ylabel(PARAMS["y"])
elif kind == "boxplot":
    dd = df[[PARAMS["y"], PARAMS["group"]]].dropna(); groups = sorted(dd[PARAMS["group"]].unique(), key=str)
    ax.boxplot([pd.to_numeric(dd[dd[PARAMS["group"]] == g][PARAMS["y"]], errors="coerce").dropna() for g in groups]); ax.set_xticks(range(1, len(groups) + 1), [str(g) for g in groups]); ax.set_ylabel(PARAMS["y"])
elif kind == "bar":
    df[PARAMS["x"]].value_counts().head(25).plot.bar(ax=ax, color="#3b5b7a"); ax.set_ylabel("Count")
for s in ("top", "right"): ax.spines[s].set_visible(False)
ax.set_title(PARAMS.get("title", ""))
fig.tight_layout(); name = f"{kind}.png"; fig.savefig(os.path.join(OUT_DIR, name)); plt.close(fig)
emit({"kind": kind}, figures=[{"file": name, "title": PARAMS.get("title") or kind}])
'''

# name -> (label, required param specs). Spec: (param, type) where type in col, cols, num_col, int, str, bool, choice:...
PROCEDURES: dict[str, dict] = {
    "describe": {"label": "Descriptive statistics", "params": {"columns": "cols?"}},
    "missing": {"label": "Missing-value analysis", "params": {}},
    "frequencies": {"label": "Frequency table", "params": {"column": "col"}},
    "correlation": {"label": "Correlation matrix", "params": {"columns": "cols", "method": "choice:pearson,spearman,kendall?"}},
    "ttest_ind": {"label": "Independent-samples t-test", "params": {"dv": "col", "group": "col", "equal_var": "bool?"}},
    "ttest_paired": {"label": "Paired-samples t-test", "params": {"a": "col", "b": "col"}},
    "anova_oneway": {"label": "One-way ANOVA (+ Tukey HSD)", "params": {"dv": "col", "group": "col"}},
    "chi_square": {"label": "Chi-square test of independence", "params": {"row": "col", "col": "col", "yates": "bool?"}},
    "mann_whitney": {"label": "Mann-Whitney U test", "params": {"dv": "col", "group": "col"}},
    "kruskal": {"label": "Kruskal-Wallis H test", "params": {"dv": "col", "group": "col"}},
    "ols": {"label": "Linear regression (OLS)", "params": {"dv": "col", "ivs": "cols", "categorical": "cols?", "cov_type": "choice:nonrobust,HC3?"}},
    "logit": {"label": "Logistic regression", "params": {"dv": "col", "ivs": "cols", "categorical": "cols?", "positive": "str?"}},
    "cronbach_alpha": {"label": "Reliability (Cronbach's alpha)", "params": {"items": "cols"}},
    "efa": {"label": "Exploratory factor analysis", "params": {"items": "cols", "n_factors": "int?", "rotation": "choice:varimax,promax,oblimin,quartimax,none?"}},
    "moderation": {"label": "Moderation analysis", "params": {"dv": "col", "iv": "col", "moderator": "col", "controls": "cols?", "center": "bool?"}},
    "mediation": {"label": "Mediation analysis (bootstrap)", "params": {"dv": "col", "iv": "col", "mediator": "col", "controls": "cols?", "n_boot": "int?", "seed": "int?"}},
    "plot": {"label": "Visualisation", "params": {"kind": "choice:histogram,scatter,boxplot,bar", "x": "col?", "y": "col?", "group": "col?", "bins": "int?", "title": "str?"}},
}


class ParamError(ValueError):
    pass


def validate_params(procedure: str, params: dict, columns: list[str]) -> dict:
    if procedure not in PROCEDURES:
        raise ParamError(f"Unknown procedure '{procedure}'")
    spec = PROCEDURES[procedure]["params"]
    cols = set(columns)
    clean: dict = {}
    for name, typ in spec.items():
        optional = typ.endswith("?")
        typ = typ.rstrip("?")
        val = params.get(name)
        if val in (None, "", []):
            if not optional:
                raise ParamError(f"Parameter '{name}' is required")
            continue
        if typ == "col":
            if not isinstance(val, str) or val not in cols:
                raise ParamError(f"'{name}' must be a column in the dataset")
        elif typ == "cols":
            if not isinstance(val, list) or not val or any(v not in cols for v in val):
                raise ParamError(f"'{name}' must be a list of dataset columns")
        elif typ == "int":
            val = int(val)
            if not 0 < val <= 100000:
                raise ParamError(f"'{name}' out of range")
        elif typ == "bool":
            val = bool(val)
        elif typ.startswith("choice:"):
            if val not in typ.split(":", 1)[1].split(","):
                raise ParamError(f"'{name}' must be one of {typ.split(':', 1)[1]}")
        elif typ == "str":
            val = str(val)[:200]
        clean[name] = val
    if procedure == "plot":
        need = {"histogram": ["x"], "bar": ["x"], "scatter": ["x", "y"], "boxplot": ["y", "group"]}[clean["kind"]]
        for n in need:
            if n not in clean:
                raise ParamError(f"Plot '{clean['kind']}' requires '{n}'")
    return clean


def build_script(procedure: str, params: dict, fmt: str) -> str:
    header = HEADER.format(procedure=procedure, params_json=json.dumps(params, sort_keys=True), fmt=fmt)
    return textwrap.dedent(header).lstrip() + textwrap.dedent(BODIES[procedure])
