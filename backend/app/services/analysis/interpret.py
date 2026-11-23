"""Deterministic, plain-language explanations of computed results.

These explanations are generated from the numbers themselves (no AI), and
always separate statistical significance from substantive importance.
"""
from __future__ import annotations


def _p(p) -> str:
    if p is None:
        return "p = n/a"
    return "p < .001" if p < 0.001 else f"p = {p:.3f}".replace("0.", ".")


def _sig(p, alpha=0.05) -> str:
    if p is None:
        return "could not be evaluated"
    return "statistically significant" if p < alpha else "not statistically significant"


def _d_size(d) -> str:
    if d is None:
        return "unknown"
    a = abs(d)
    return "negligible" if a < 0.2 else "small" if a < 0.5 else "medium" if a < 0.8 else "large"


def _r_size(r) -> str:
    a = abs(r or 0)
    return "negligible" if a < 0.1 else "small" if a < 0.3 else "moderate" if a < 0.5 else "strong"


def _eta_size(e) -> str:
    a = e or 0
    return "small" if a < 0.06 else "medium" if a < 0.14 else "large"


SIG_NOTE = ("Statistical significance indicates the result is unlikely under the null hypothesis given the sample; "
            "it does not by itself establish practical or theoretical importance, which depends on the effect size and context.")


def interpret(procedure: str, out: dict) -> str:
    r = out.get("result", {})
    try:
        if procedure == "ttest_ind":
            g = r["groups"]
            return (f"{r['test']} test comparing {g[0]['level']} (M = {g[0]['mean']:.2f}, SD = {g[0]['sd']:.2f}, n = {g[0]['n']}) with "
                    f"{g[1]['level']} (M = {g[1]['mean']:.2f}, SD = {g[1]['sd']:.2f}, n = {g[1]['n']}): t({r['df']:.1f}) = {r['t']:.2f}, {_p(r['p'])}; "
                    f"the difference is {_sig(r['p'])}. Cohen's d = {r['cohens_d']:.2f} ({_d_size(r['cohens_d'])} effect). " + SIG_NOTE)
        if procedure == "ttest_paired":
            return (f"Paired t-test: t({r['df']}) = {r['t']:.2f}, {_p(r['p'])} ({_sig(r['p'])}); mean difference = {r['mean_difference']:.2f}; "
                    f"d_z = {r['cohens_dz']:.2f} ({_d_size(r['cohens_dz'])}). " + SIG_NOTE)
        if procedure == "anova_oneway":
            return (f"One-way ANOVA: F({r['df_between']:.0f}, {r['df_within']:.0f}) = {r['F']:.2f}, {_p(r['p'])} ({_sig(r['p'])}); "
                    f"η² = {r['eta_squared']:.3f} ({_eta_size(r['eta_squared'])} effect). Levene {_p(r['levene']['p'])}"
                    f"{' — variances may be unequal; consider Welch ANOVA' if r['levene']['p'] is not None and r['levene']['p'] < .05 else ''}. "
                    "See Tukey HSD table for pairwise differences. " + SIG_NOTE)
        if procedure == "chi_square":
            return (f"Chi-square test: χ²({r['dof']}) = {r['chi2']:.2f}, {_p(r['p'])} ({_sig(r['p'])}), N = {r['n']}; Cramér's V = "
                    f"{r['cramers_v']:.3f}. " + SIG_NOTE)
        if procedure == "correlation":
            pairs = []
            cols = list(r["r"].keys())
            for i, a in enumerate(cols):
                for b in cols[i + 1:]:
                    rv, pv = r["r"][a][b], r["p"][a][b]
                    if rv is not None:
                        pairs.append(f"{a}–{b}: r = {rv:.2f} ({_r_size(rv)}, {_p(pv)})")
            return f"{r['method'].title()} correlations — " + "; ".join(pairs[:15]) + ". Correlation does not establish causation. " + SIG_NOTE
        if procedure == "ols":
            return (f"OLS regression ({r['formula']}), n = {r['n']}: R² = {r['r2']:.3f} (adjusted {r['adj_r2']:.3f}), F = {r['F']:.2f}, "
                    f"{_p(r['F_p'])}. Breusch-Pagan {_p(r['breusch_pagan']['p'])}"
                    f"{' (heteroscedasticity detected — rerun with HC3 robust errors)' if r['breusch_pagan']['p'] < .05 and r['cov_type'] == 'nonrobust' else ''}; "
                    f"Durbin-Watson = {r['durbin_watson']:.2f}. Inspect coefficients with their confidence intervals; " + SIG_NOTE)
        if procedure == "logit":
            return (f"Logistic regression, n = {r['n']}: McFadden pseudo-R² = {r['pseudo_r2_mcfadden']:.3f}, likelihood-ratio {_p(r['llr_p'])}. "
                    "Odds ratios > 1 indicate higher odds of the positive class. " + SIG_NOTE)
        if procedure == "cronbach_alpha":
            a = r["alpha"]
            q = "excellent" if a >= .9 else "good" if a >= .8 else "acceptable" if a >= .7 else "questionable" if a >= .6 else "poor"
            return (f"Cronbach's α = {a:.3f} ({q} by conventional thresholds) for {r['k']} items, n = {r['n']}. Very high α (> .95) may "
                    "indicate item redundancy. Review 'alpha if deleted' and item-total correlations.")
        if procedure == "efa":
            return (f"EFA with {r['n_factors']} factor(s), {r['rotation']} rotation, n = {r['n']}. KMO = {r['kmo']:.3f}; Bartlett χ²({r['bartlett']['df']:.0f}) "
                    f"= {r['bartlett']['chi2']:.1f}, {_p(r['bartlett']['p'])}. Kaiser criterion suggests {r['kaiser_criterion_factors']} factor(s); "
                    "confirm with a scree plot or parallel analysis and theory.")
        if procedure == "moderation":
            return (f"Moderation: interaction b = {r['interaction_b']:.3f}, {_p(r['interaction_p'])} ({_sig(r['interaction_p'])}); model R² = {r['r2']:.3f}. "
                    "Simple slopes are reported at −1 SD, mean and +1 SD of the moderator. " + SIG_NOTE)
        if procedure == "mediation":
            lo, hi = r["indirect_ci95"]
            return (f"Mediation (X = {r['variables']['X']}, M = {r['variables']['M']}, Y = {r['variables']['Y']}): indirect effect a×b = {r['indirect']:.3f}, "
                    f"95% bootstrap CI [{lo:.3f}, {hi:.3f}] ({'excludes' if r['indirect_significant'] else 'includes'} zero). Direct effect c′ = "
                    f"{r['c_direct']:.3f} ({_p(r['c_direct_p'])}); total effect c = {r['c_total']:.3f}. Statistical mediation does not prove causal mediation.")
        if procedure in ("mann_whitney", "kruskal"):
            stat = "U" if procedure == "mann_whitney" else "H"
            return f"{stat} = {r[stat]:.2f}, {_p(r['p'])} ({_sig(r['p'])}). " + SIG_NOTE
        if procedure == "describe":
            return f"Descriptive statistics for {len(r['numeric'])} numeric and {len(r['categorical'])} categorical variables (N = {r['n_rows']})."
        if procedure == "missing":
            return f"{r['complete_cases']} of {r['n_rows']} rows ({r['complete_pct']:.1f}%) are complete cases."
    except (KeyError, TypeError, ValueError):
        pass
    return "Results computed; see tables."
