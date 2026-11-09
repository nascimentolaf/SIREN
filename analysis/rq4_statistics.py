#!/usr/bin/env python3
"""RQ4's statistics: the time to decide one ILP model against the hyperperiod.

Reads analysis/csv/rq4_hyperperiod.csv (rq4_hyperperiod.py collect), one row
per model, and writes:

    analysis/csv/rq4_statistics.csv       one row per target hyperperiod
    analysis/csv/rq4_regression.json      the regressions and their checks
    analysis/figures/paper/table_rq4.tex  the paper's table

plot_paper.py calls write_results() and macros(), so that its normal run also
writes the table and puts the numbers the text quotes in numbers_results.tex.
Running this script alone does the same without the macros, and prints a
summary.

The design decides the method:

  * Flowsets each giving one model per target hyperperiod (fewer if it has no
    periods for a target), each model solved once. Within a target the models
    are distinct flowsets, so a mean's interval is Student's t on them; the
    solver's run-to-run variation is not told from the differences among
    flowsets. BCa bootstrap intervals are written beside it, as a check on the
    t interval under the skewed times.
  * Across targets the models of one flowset are not independent: a flowset slow
    at one hyperperiod is slow at all of them. So every regression's standard
    errors are clustered by flowset (CR1, t with G - 1 degrees of freedom), the
    flowset being the independent unit. The naive ones are in the JSON for
    comparison only. Two checks on the slope's interval: the t interval of the
    per-flowset slopes, and a bootstrap over flowsets.
  * Exact linearity is tested against the saturated model of one mean per
    target (clustered Wald F). A model whose status is not a decision (a
    timeout) has only a lower bound for its time: means and regressions are then
    not computed. A proof of infeasibility is a decision, so its time counts.
  * Each flowset's models are shared between the servers, and a server is not a
    flowset: the intervals above cover the flowsets, not the machine. So the JSON
    also fits each server alone (named slower and faster: no host name is
    written), and gives their ratio per target with the flowset held fixed.
  * The mode is not reported: the times are continuous, and it would be a
    rounding artefact. The CSV says how many distinct values each target has.

Usage (from the repository's root):
    python3 analysis/rq4_statistics.py [--csv FILE] [--csv-dir DIR] [--table-dir DIR]

Needs numpy, pandas and scipy.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
CSV_IN = HERE / "csv" / "rq4_hyperperiod.csv"
DECIDED = ("schedulable", "infeasible")
CONFIDENCE = 0.95
N_BOOTSTRAP = 10000
SEED = 20261105


def load(path: Path = CSV_IN) -> pd.DataFrame:
    """The models, refusing what would silently bias the statistics."""
    return validate(pd.read_csv(path))


def validate(r: pd.DataFrame) -> pd.DataFrame:
    if r.duplicated(["flowset", "target_h_ms"]).any():
        raise ValueError("rq4 results: a (flowset, target) pair appears twice")
    if r.solve_s.isna().any() or (r.solve_s <= 0).any():
        raise ValueError("rq4 results: a model has no positive solving time")
    if (r.h_ms != r.target_h_ms).any():
        raise ValueError("rq4 results: a model's hyperperiod is not its target")
    return r.assign(h_s=r.target_h_ms / 1000)


def censored(r: pd.DataFrame) -> int:
    return int((~r.status.isin(DECIDED)).sum())


def per_hyperperiod(r: pd.DataFrame) -> pd.DataFrame:
    """The descriptive statistics of the solving time, per target hyperperiod."""
    rows = []
    for h, s in r.groupby("h_s"):
        v = s.solve_s.to_numpy()
        n, mean, sd = len(v), v.mean(), v.std(ddof=1)
        half = stats.t.ppf((1 + CONFIDENCE) / 2, n - 1) * sd / math.sqrt(n)
        boot = stats.bootstrap((v,), np.mean, n_resamples=N_BOOTSTRAP, method="BCa",
                               confidence_level=CONFIDENCE,
                               random_state=np.random.default_rng(SEED)).confidence_interval
        q1, med, q3 = np.quantile(v, [0.25, 0.5, 0.75])
        rows.append({
            "h_s": h, "n": n, "n_flowsets": s.flowset.nunique(), "n_undecided": censored(s),
            "n_infeasible": int((s.status == "infeasible").sum()),
            "mean_s": mean, "median_s": med, "sd_s": sd, "variance_s2": sd ** 2,
            "min_s": v.min(), "max_s": v.max(), "q1_s": q1, "q3_s": q3, "iqr_s": q3 - q1,
            "ci_low_s": mean - half, "ci_high_s": mean + half,
            "bca_low_s": boot.low, "bca_high_s": boot.high,
            "ci_half_share_of_mean": half / mean, "cv": sd / mean, "skewness": stats.skew(v),
            "mode": f"not meaningful: {len(np.unique(v))} distinct values of {n}",
            "tukey_high_outliers": int((v > q3 + 1.5 * (q3 - q1)).sum()),
            "tukey_low_outliers": int((v < q1 - 1.5 * (q3 - q1)).sum()),
            "mean_s_per_s_of_h": mean / h,
            "median_ms_per_job": (1000 * s.solve_s / s.n_jobs).median(),
            "median_jobs": s.n_jobs.median(), "median_binaries": s.n_binaries.median(),
        })
    return pd.DataFrame(rows)


def _t_crit(g: int) -> float:
    return stats.t.ppf((1 + CONFIDENCE) / 2, g - 1)


def _clustered(x: np.ndarray, e: np.ndarray, cluster: np.ndarray) -> np.ndarray:
    """CR1 covariance of OLS coefficients: x'x^-1 (sum_g x_g'e_g e_g'x_g) x'x^-1."""
    n, k = x.shape
    labels = np.unique(cluster)
    g = len(labels)
    bread = np.linalg.inv(x.T @ x)
    meat = np.zeros((k, k))
    for c in labels:
        score = x[cluster == c].T @ e[cluster == c]
        meat += np.outer(score, score)
    return bread @ meat @ bread * (g / (g - 1)) * ((n - 1) / (n - k))


def _r_squared(y: np.ndarray, e: np.ndarray) -> float:
    return 1 - float(e @ e) / float(((y - y.mean()) ** 2).sum())


def _breusch_pagan(x: np.ndarray, e: np.ndarray) -> dict:
    """Koenker's studentized form: n R^2 of the squared residuals on the regressor."""
    lm = len(e) * stats.linregress(x, e ** 2).rvalue ** 2
    return {"lm": lm, "df": 1, "p": float(stats.chi2.sf(lm, 1))}


def line(x: np.ndarray, y: np.ndarray, cluster: np.ndarray) -> dict:
    """y = a + b x by OLS, with standard errors clustered by flowset."""
    xm = np.column_stack([np.ones(len(x)), x])
    coef = np.linalg.lstsq(xm, y, rcond=None)[0]
    e = y - xm @ coef
    se = np.sqrt(np.diag(_clustered(xm, e, cluster)))
    g = len(np.unique(cluster))
    half = _t_crit(g) * se
    return {
        "intercept": coef[0], "intercept_low": coef[0] - half[0], "intercept_high": coef[0] + half[0],
        "intercept_se": se[0], "intercept_p": float(2 * stats.t.sf(abs(coef[0] / se[0]), g - 1)),
        "slope": coef[1], "slope_low": coef[1] - half[1], "slope_high": coef[1] + half[1],
        "slope_se": se[1], "slope_t": coef[1] / se[1],
        "slope_p": float(2 * stats.t.sf(abs(coef[1] / se[1]), g - 1)),
        "r_squared": _r_squared(y, e), "n": len(y), "clusters": g, "df": g - 1,
    }


def _lack_of_fit(x: np.ndarray, y: np.ndarray, cluster: np.ndarray) -> dict:
    """Clustered Wald test of one mean per target against the line."""
    levels = np.unique(x)
    q = len(levels) - 2
    # The line, plus an indicator per interior target: together, saturated.
    xm = np.column_stack([np.ones(len(x)), x] + [(x == h).astype(float) for h in levels[1:-1]])
    coef = np.linalg.lstsq(xm, y, rcond=None)[0]
    cov = _clustered(xm, y - xm @ coef, cluster)
    dev = coef[2:]
    f = float(dev @ np.linalg.inv(cov[2:, 2:]) @ dev) / q
    df2 = len(np.unique(cluster)) - 1
    return {"f": f, "df1": q, "df2": df2, "p": float(stats.f.sf(f, q, df2))}


def _cluster_bootstrap_slope(x: np.ndarray, y: np.ndarray, cluster: np.ndarray) -> dict:
    """Percentile interval of the slope, resampling flowsets with replacement."""
    t = pd.DataFrame({"c": cluster, "n": 1.0, "x": x, "y": y, "xx": x * x, "xy": x * y})
    s = t.groupby("c").sum().to_numpy()
    rng = np.random.default_rng(SEED)
    draw = s[rng.integers(0, len(s), (N_BOOTSTRAP, len(s)))].sum(axis=1)
    n, sx, sy, sxx, sxy = draw.T
    slopes = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    lo, hi = np.quantile(slopes, [(1 - CONFIDENCE) / 2, (1 + CONFIDENCE) / 2])
    return {"low": lo, "high": hi, "resamples": N_BOOTSTRAP, "seed": SEED}


def _power_law(x: np.ndarray, y: np.ndarray, cluster: np.ndarray) -> dict:
    """log y = log c + beta log x: beta = 1 is proportional growth."""
    lx, ly = np.log(x), np.log(y)
    fit = line(lx, ly, cluster)
    e = ly - (fit["intercept"] + fit["slope"] * lx)
    return {"exponent": fit["slope"], "exponent_low": fit["slope_low"],
            "exponent_high": fit["slope_high"], "r_squared": fit["r_squared"],
            "residual_sd_log": float(e.std(ddof=2)), "breusch_pagan": _breusch_pagan(lx, e)}


def servers(r: pd.DataFrame) -> dict | None:
    """The two servers apart, the slower being the one of the steeper line; None
    unless there are exactly two. The ratio per target is from log(time) on one
    level per flowset, one per target and one per target on the slower server,
    which holds the flowset fixed: which flowsets a server drew does not enter it."""
    if "host" not in r or r.host.nunique() != 2:
        return None
    fits = {}
    for host, s in r.groupby("host"):
        x, y, c = s.h_s.to_numpy(), s.solve_s.to_numpy(), s.flowset.to_numpy()
        means = s.groupby("h_s").solve_s.mean()
        fits[host] = {"models": len(s), "line": line(x, y, c), "power_law": _power_law(x, y, c),
                      "lack_of_fit": _lack_of_fit(x, y, c),
                      "mean_s": {str(h): v for h, v in means.items()},
                      "mean_s_per_s_of_h": {str(h): v / h for h, v in means.items()}}
    slower, faster = sorted(fits, key=lambda h: -fits[h]["line"]["slope"])
    levels = np.unique(r.h_s)
    on_slower = np.column_stack([(r.h_s == h) & (r.host == slower) for h in levels]).astype(float)
    targets = np.column_stack([r.h_s == h for h in levels[1:]]).astype(float)
    xm = np.column_stack([on_slower, targets, pd.get_dummies(r.flowset).to_numpy(dtype=float)])
    ly = np.log(r.solve_s.to_numpy())
    coef = np.linalg.lstsq(xm, ly, rcond=None)[0]
    cov = _clustered(xm, ly - xm @ coef, r.flowset.to_numpy())
    half = _t_crit(r.flowset.nunique()) * np.sqrt(np.diag(cov))
    return {
        "slower": fits[slower], "faster": fits[faster],
        "slower_over_faster_same_flowset": {
            str(h): {"ratio": math.exp(coef[i]), "low": math.exp(coef[i] - half[i]),
                     "high": math.exp(coef[i] + half[i])} for i, h in enumerate(levels)},
    }


def regression(r: pd.DataFrame) -> dict:
    """Time against hyperperiod: the line, how far to trust it, the power law."""
    x, y, c = r.h_s.to_numpy(), r.solve_s.to_numpy(), r.flowset.to_numpy()
    fit = line(x, y, c)
    e = y - (fit["intercept"] + fit["slope"] * x)
    naive = stats.linregress(x, y)
    means = r.groupby("h_s").solve_s.mean()
    on_means = stats.linregress(means.index.to_numpy(), means.to_numpy())
    largest = x == x.max()
    per_flowset = np.array([np.polyfit(s.h_s, s.solve_s, 1)[0] for _, s in r.groupby("flowset")])
    half = _t_crit(len(per_flowset)) * per_flowset.std(ddof=1) / math.sqrt(len(per_flowset))
    xm = np.column_stack([np.ones(len(x)), x])
    leverage = np.sum(xm * (xm @ np.linalg.inv(xm.T @ xm)), axis=1)
    wide = r.pivot(index="flowset", columns="h_s", values="solve_s")
    rho = wide.corr(method="spearman").to_numpy()
    off = rho[~np.eye(len(rho), dtype=bool)]
    jobs = r.n_jobs.to_numpy(dtype=float)
    binaries = r.n_binaries.to_numpy(dtype=float)
    power = _power_law(x, y, c)
    span = float(x.max() / x.min())
    return {
        "source": "analysis/csv/rq4_hyperperiod.csv",
        "unit": "dependent: solving time (s); independent: target hyperperiod (s)",
        "models": len(r), "flowsets": r.flowset.nunique(), "undecided": censored(r),
        "distinct_times": int(r.solve_s.nunique()),
        "line": fit,
        "line_naive_for_comparison": {
            "slope": naive.slope, "slope_se": naive.stderr, "intercept": naive.intercept,
            "intercept_se": naive.intercept_stderr, "r_squared": naive.rvalue ** 2, "p": naive.pvalue,
            "note": "treats every model as independent: not valid here, see the module's docstring"},
        "line_on_the_means": {"slope": on_means.slope, "intercept": on_means.intercept,
                              "r_squared": on_means.rvalue ** 2, "points": len(means)},
        "line_without_largest_hyperperiod": line(x[~largest], y[~largest], c[~largest]),
        "slope_check_per_flowset": {"mean": per_flowset.mean(), "low": per_flowset.mean() - half,
                                    "high": per_flowset.mean() + half, "min": per_flowset.min(),
                                    "max": per_flowset.max(), "flowsets": len(per_flowset)},
        "slope_check_cluster_bootstrap": _cluster_bootstrap_slope(x, y, c),
        "lack_of_fit": _lack_of_fit(x, y, c),
        "breusch_pagan": _breusch_pagan(x, e),
        "residuals": {"shapiro_p": float(stats.shapiro(e).pvalue), "skewness": float(stats.skew(e)),
                      "excess_kurtosis": float(stats.kurtosis(e))},
        "mean_minus_line_s": {str(h): v - (fit["intercept"] + fit["slope"] * h)
                              for h, v in means.items()},
        "leverage_share": {str(h): float(leverage[x == h].sum() / 2) for h in np.unique(x)},
        "spearman_between_targets": {"min": float(off.min()), "max": float(off.max())},
        "power_law_on_hyperperiod": power,
        "power_law_excess_over_proportional": span ** (power["exponent"] - 1) - 1,
        "power_law_without_largest_hyperperiod": _power_law(x[~largest], y[~largest], c[~largest]),
        "power_law_on_jobs": _power_law(jobs, y, c),
        "power_law_on_binaries": _power_law(binaries, y, c),
        "servers": servers(r),
    }


def _sig(v: float, digits: int = 3) -> str:
    """Three significant digits, never in exponent form."""
    decimals = max(0, digits - 1 - math.floor(math.log10(abs(v))))
    return f"{v:.{decimals}f}"


def _ms(seconds: float, digits: int = 3) -> str:
    """A time given in seconds, in milliseconds to three significant digits,
    so that a large value shows no digits beyond those of its statistics."""
    v = seconds * 1000
    decimals = digits - 1 - math.floor(math.log10(abs(v)))
    return f"{round(v, decimals):.{max(0, decimals)}f}"


def _p(p: float) -> str:
    return r"$p<0.001$" if p < 0.001 else f"$p={p:.2f}$"


def table_rq4(s: pd.DataFrame, out: Path) -> None:
    """table_rq4.tex, one column wide, under the caption in results.tex. The N
    column is commented out, not dropped: the text gives the models per
    hyperperiod, and the file keeps each N beside its row."""
    lines = [
        "% Generated by analysis/rq4_statistics.py (table_rq4): RQ4's solving times. Do not edit.",
        r"\scriptsize",
        r"\setlength{\tabcolsep}{3pt}",
        r"\begin{tabular}{@{}r%r",
        r"rcrrrrrr@{}}",
        r"\toprule",
        r"$H$ % & $N$",  # the paper's header (the author's layout)
        r"& Mean & $95\%$ CI & SD & Min & $Q_1$ & Median & $Q_3$ & Max \\",
        r"\midrule",
    ]
    for _, t in s.iterrows():
        # The interval is rounded at the same digit as the mean it surrounds.
        decimals = 2 - math.floor(math.log10(t.mean_s * 1000))
        low, high = (round(t[c] * 1000, decimals) for c in ("ci_low_s", "ci_high_s"))
        lines.append(
            f"{t.h_s * 1000:g} % & {int(t.n)}\n& {_ms(t.mean_s)} & "
            f"[{low:.{max(0, decimals)}f}, {high:.{max(0, decimals)}f}] & " +
            " & ".join(_ms(t[c]) for c in ("sd_s", "min_s", "q1_s", "median_s", "q3_s", "max_s")) +
            r" \\")
    lines += [r"\bottomrule", r"\end{tabular}", ""]
    out.mkdir(parents=True, exist_ok=True)
    (out / "table_rq4.tex").write_text("\n".join(lines))


def write_results(r: pd.DataFrame, table_dir: Path, csv_dir: Path = HERE / "csv") -> tuple:
    """rq4_statistics.csv, rq4_regression.json and table_rq4.tex; (per target, regression)."""
    r = validate(r)
    if censored(r):
        print(f"warning: rq4_statistics: {censored(r)} models have no decision, so only a lower "
              "bound for their time: no table, no regression", file=sys.stderr)
        return None, None
    s, reg = per_hyperperiod(r), regression(r)
    csv_dir.mkdir(parents=True, exist_ok=True)
    s.to_csv(csv_dir / "rq4_statistics.csv", index=False, float_format="%.6g")
    (csv_dir / "rq4_regression.json").write_text(json.dumps(reg, indent=2, default=float) + "\n")
    table_rq4(s, table_dir)
    if reg["distinct_times"] < 0.99 * reg["models"]:
        print("warning: rq4_statistics: many models have the same time, but the paper's caption "
              "says almost none do", file=sys.stderr)
    if s.mean_s_per_s_of_h.iloc[-1] <= s.mean_s_per_s_of_h.iloc[0]:
        print("warning: rq4_statistics: the mean time per second of hyperperiod does not rise "
              "from the first target to the last, as the paper's text says", file=sys.stderr)
    return s, reg


def macros(r: pd.DataFrame, cmd) -> None:
    """The numbers the text quotes, through plot_paper.py's numbers_results.tex."""
    r = validate(r)
    if censored(r) or r.h_s.nunique() < 3:
        print("warning: rq4_statistics: no regression macros (models without a decision, or "
              "fewer than three hyperperiods)", file=sys.stderr)
        return
    s, g = per_hyperperiod(r), regression(r)
    first, last = s.iloc[0], s.iloc[-1]
    fit, without, power, lof = g["line"], g["line_without_largest_hyperperiod"], g["power_law_on_hyperperiod"], g["lack_of_fit"]
    cmd("RqFourNMin", int(s.n.min()))
    cmd("RqFourNMax", int(s.n.max()))
    infeasible = r[r.status == "infeasible"]
    cmd("RqFourInfeasible", len(infeasible))
    cmd("RqFourInfeasibleFlowsets", infeasible.flowset.nunique())
    cmd("RqFourSlope", f"{fit['slope']:.2f}")
    cmd("RqFourSlopeLow", f"{fit['slope_low']:.2f}")
    cmd("RqFourSlopeHigh", f"{fit['slope_high']:.2f}")
    cmd("RqFourSlopeP", _p(fit["slope_p"]))
    cmd("RqFourIntercept", f"{fit['intercept']:.2f}")   # in math mode: a plain minus sign
    cmd("RqFourRsqModels", f"{fit['r_squared']:.2f}")
    rsq = g["line_on_the_means"]["r_squared"]
    cmd("RqFourRsqMeans", "$R^2>0.999$" if rsq > 0.999 else f"$R^2={rsq:.3f}$")
    cmd("RqFourSpearmanMin", f"{g['spearman_between_targets']['min']:.2f}")
    cmd("RqFourSdFirst", _sig(first.sd_s, 2))
    cmd("RqFourSdLast", _sig(last.sd_s))
    cmd("RqFourCvMin", f"{s.cv.min():.2f}")
    cmd("RqFourCvMax", f"{s.cv.max():.2f}")
    cmd("RqFourCiShare", f"{100 * s.ci_half_share_of_mean.max():.0f}\\%")
    cmd("RqFourLastMinSec", _sig(last.min_s, 2))
    cmd("RqFourLastMaxSec", _sig(last.max_s))
    cmd("RqFourMaxSec", _sig(s.max_s.max()))
    cmd("RqFourMaxShare", f"{100 * s.max_s.max() / r.cap_s.max():.1f}\\%")
    cmd("RqFourLofTest", f"$F({lof['df1']},{lof['df2']})={lof['f']:.1f}$, {_p(lof['p'])}")
    cmd("RqFourRateFirst", f"{first.mean_s_per_s_of_h:.2f}")
    cmd("RqFourRateLast", f"{last.mean_s_per_s_of_h:.2f}")
    cmd("RqFourLastLeverage", f"{100 * g['leverage_share'][str(last.h_s)]:.0f}\\%")
    cmd("RqFourSlopeNoLast", f"{without['slope']:.2f}")
    cmd("RqFourSlopeNoLastLow", f"{without['slope_low']:.2f}")
    cmd("RqFourSlopeNoLastHigh", f"{without['slope_high']:.2f}")
    cmd("RqFourBpP", _p(g["breusch_pagan"]["p"]))
    cmd("RqFourLogBpP", _p(power["breusch_pagan"]["p"]))
    cmd("RqFourExponent", f"{power['exponent']:.2f}")
    cmd("RqFourExponentLow", f"{power['exponent_low']:.2f}")
    cmd("RqFourExponentHigh", f"{power['exponent_high']:.2f}")
    cmd("RqFourExponentExcess", f"{100 * g['power_law_excess_over_proportional']:.0f}\\%")
    cmd("RqFourBinariesRsq", f"{g['power_law_on_binaries']['r_squared']:.2f}")
    cmd("RqFourHRsq", f"{power['r_squared']:.2f}")
    cmd("RqFourMsPerJobFirst", f"{first.median_ms_per_job:.1f}")
    cmd("RqFourMsPerJobLast", f"{last.median_ms_per_job:.1f}")
    two = g["servers"]
    if two is None:
        print("warning: rq4_statistics: no server macros (not exactly two hosts)", file=sys.stderr)
        return
    gap = two["slower_over_faster_same_flowset"]
    cmd("RqFourServerGapFirst", f"{100 * (gap[str(first.h_s)]['ratio'] - 1):.1f}\\%")
    cmd("RqFourServerGapLast", f"{100 * (gap[str(last.h_s)]['ratio'] - 1):.0f}\\%")
    cmd("RqFourSlopeSlowServer", f"{two['slower']['line']['slope']:.2f}")
    cmd("RqFourSlopeFastServer", f"{two['faster']['line']['slope']:.2f}")
    cmd("RqFourExponentSlowServer", f"{two['slower']['power_law']['exponent']:.2f}")
    cmd("RqFourExponentFastServer", f"{two['faster']['power_law']['exponent']:.2f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, default=CSV_IN, help="rq4_hyperperiod.py collect's CSV")
    ap.add_argument("--csv-dir", type=Path, default=HERE / "csv",
                    help="where rq4_statistics.csv and rq4_regression.json go")
    ap.add_argument("--table-dir", type=Path, default=HERE / "figures" / "paper",
                    help="where table_rq4.tex goes")
    args = ap.parse_args()
    s, g = write_results(load(args.csv), args.table_dir, args.csv_dir)
    if s is None:
        return 1
    fit, power = g["line"], g["power_law_on_hyperperiod"]
    print(s[["h_s", "n", "mean_s", "ci_low_s", "ci_high_s", "median_s", "sd_s", "cv",
             "min_s", "max_s"]].round(3).to_string(index=False))
    print(f"line: t = {fit['intercept']:.3f} + {fit['slope']:.3f} H, slope 95% CI "
          f"[{fit['slope_low']:.3f}, {fit['slope_high']:.3f}], p = {fit['slope_p']:.2e}, "
          f"R^2 = {fit['r_squared']:.3f} (on the means {g['line_on_the_means']['r_squared']:.4f})")
    print(f"lack of fit: F({g['lack_of_fit']['df1']},{g['lack_of_fit']['df2']}) = "
          f"{g['lack_of_fit']['f']:.2f}, p = {g['lack_of_fit']['p']:.2e}; Breusch-Pagan p = "
          f"{g['breusch_pagan']['p']:.2e}")
    print(f"power law: exponent {power['exponent']:.3f} "
          f"[{power['exponent_low']:.3f}, {power['exponent_high']:.3f}], "
          f"Breusch-Pagan p = {power['breusch_pagan']['p']:.2f}")
    if g["servers"]:
        two = g["servers"]
        print(f"per server: slope {two['slower']['line']['slope']:.3f} and "
              f"{two['faster']['line']['slope']:.3f}, exponent "
              f"{two['slower']['power_law']['exponent']:.3f} and "
              f"{two['faster']['power_law']['exponent']:.3f}")
    print(f"wrote {args.csv_dir}/rq4_statistics.csv, rq4_regression.json, "
          f"{args.table_dir}/table_rq4.tex")
    return 0


if __name__ == "__main__":
    sys.exit(main())
