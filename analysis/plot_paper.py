#!/usr/bin/env python3
"""Figures and macros of the paper's Performance Evaluation section.

Every input is an output of the RB-budget sweep (scripts/sweep_experiment.sh);
nothing here runs a scheduler or changes a result.

    analysis/csv/output_sweep.csv          verdicts, all budgets (sweep --collect)
    analysis/csv/derived_baseline_configs.csv   attempts each baseline committed to
    analysis/csv/derived_ilp_timing_rb135.csv   ILP per-flowset solving time

The two derived tables are extracted once from output/sweep/ (`--derive`) and
committed, so the figures regenerate without the ~8 GB report tree:

  * baseline configs: the baselines select their configuration from the
    unscreened set (baseline_algorithms/common: enforceLimits=false), bounded
    only by B <= B^max, and within A^max whenever some configuration within
    it fits the cell. Where every configuration with A <= A^max is wider
    than the cell, they fall back to more attempts. The paper defines A^max as
    a layer-wide limit, so such a run is outside the model: its verdict is
    reinterpreted here as `ko_amax` (not schedulable). The raw CSV is
    untouched. On the current catalogue and MCS table it never happens: every
    application has a configuration within A^max that fits even 51 RBs.
  * ILP timing: logs/ilp/<flowset>.log lists every candidate tuple in sweep
    order with its solve time; a candidate cut at the per-tuple cap is logged
    without a time and counted here at the cap. `first_table_s` is the
    sequential time until the first schedulable tuple, `decide_all_s` the
    time to decide every tuple (both lower bounds for capped candidates).
    Both are the solver's solve times: building each model is not included.
    `host` is the machine that ran the flowset (the log's "Host:" line), so
    times measured on different hardware can be told apart.

`--derive` reads whatever output/sweep/ holds and updates the derived CSVs
for that alone: a method or budget whose reports are absent keeps its rows,
and the ILP timing is kept when there are no ILP logs. So after running one
method on its own (the 5gtps_harq variant, scripts/sweep_experiment.sh) it
adds that method's rows without the other methods' report trees.

`--partial` draws the figures the paper includes (fig_rq2_verdicts,
fig_rq2_scaling, fig_rq3_residual_gain, fig_rq4_hyperperiod), and
fig_rq3_rb_usage, while the sweep
still runs, from the finished pairs output/sweep/ holds here, into analysis/partial/:
csv/ (the verdict CSVs, the baselines' configurations, the RQ4 results if
any), figures/ (under the paper's names) and README.md (what each figure is
drawn from, what is pending). It never reads or writes analysis/csv/ or
analysis/figures/paper/: the verdicts and configurations are derived again
from output/sweep/, the RQ4 results from output/rq4/.

  * A budget is drawn once --min-flowsets of its flowsets (100) are finished.
    fig_rq2_scaling and the two RQ3 figures compare budgets, so they use
    only the flowsets finished at every budget drawn; fig_rq2_verdicts is the
    main budget's (rb135) and waits for it.
  * Once every budget is drawn, it also writes numbers_results.tex, the
    paper's macros, on the flowsets finished at every budget. With
    --verdicts, the first-table times and the tuple counts are the verdict
    run's (it ran one pair per core), on the same flowsets.
  * --common-size N keeps only the N lowest-numbered of the flowsets
    finished at every budget, for figures, table and macros alike.
  * --flowsets FILE draws exactly the flowsets FILE lists instead, each
    finished at every budget.
  * The pairs that finish first are the quick ones, so partial shares are not
    estimates of the final ones: the README compares the ILP's schedulable
    share on all finished flowsets of each budget with its share on the
    flowsets the figures use. Once the sweep is complete, draw the real
    figures (the two commands above) and delete analysis/partial/.

Usage:
    python3 analysis/plot_paper.py --derive      # once, needs output/sweep/
    python3 analysis/plot_paper.py               # figures + numbers_results.tex
    python3 analysis/plot_paper.py --partial     # while the sweep runs (--copy:
                                                 # copy the newly finished pairs first)
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import io
import itertools
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SWEEP = ROOT / "output" / "sweep"
BUDGETS = [51, 79, 107, 135]
MAIN_BUDGET = 135
# The baselines the paper evaluates; 5G-TPS is text only. EDF is still
# simulated (its rows stay in the CSVs, under the A^max rule below), but the
# paper does not discuss it: it has no figure and no macro.
BASELINES = ["must", "qospf", "rr", "pf"]
# The baselines the text gives as one range ("the PF family a--b").
PF_FAMILY = ["qospf", "rr", "pf"]
# 5G-TPS with one HARQ process per TB job (the 5gtps_harq method variant,
# scripts/parameters.sh), the assumption of its own paper. Text only, like
# plain 5gtps (M = 3): its macros are written when its rows are in the CSV.
TPS = "5gtps_harq"
# Methods that pick their configuration without Section V's screen, so the
# A^max reinterpretation below applies to them, EDF included.
AMAX_RULE = ["must", "edf", "qospf", "rr", "pf", TPS]
# Its direct label in the scaling panel, if PLOTTED includes it (the bar
# chart uses pe.LABEL).
TPS_DIRECT_LABEL = "5G-TPS,\n$M\\geq$#TBs"
A_MAX = 4
CAP_S = 60.0                      # the sweep's ILP_CANDIDATE_TIMEOUT, s

CSV_DIR = HERE / "csv"            # every committed result table
CONFIGS_CSV = CSV_DIR / "derived_baseline_configs.csv"
TIMING_CSV = CSV_DIR / f"derived_ilp_timing_rb{MAIN_BUDGET}.csv"
# RQ4: one ILP model per (flowset, stretched hyperperiod), from
# analysis/rq4_hyperperiod.py collect. Optional: without it, no RQ4
# hyperperiod figure or macros are written. The control (rq4_hyperperiod.py
# compare) adds two macros when it exists.
RQ4_CSV = CSV_DIR / "rq4_hyperperiod.csv"
RQ4_CONTROL_CSV = CSV_DIR / "rq4_control.csv"


# ---------------------------------------------------------------- derive ----

def derive_baseline_configs(budgets: list | None = None, sweep: Path | None = None) -> pd.DataFrame:
    """Rows for every (budget, method) whose reports are in output/sweep/
    (BUDGETS and SWEEP unless given)."""
    budgets = BUDGETS if budgets is None else budgets
    sweep = SWEEP if sweep is None else sweep
    rows = []
    for rb in budgets:
        for m in AMAX_RULE:
            base = sweep / f"rb{rb}" / "reports" / m
            if not base.is_dir():
                print(f"  rb{rb} {m}: no reports under {base}, rows kept")
                continue
            for fs_dir in sorted(base.iterdir()):
                r = json.loads((fs_dir / "combo_0000.json").read_text())
                flows = r.get("flows", [])
                # C_i + t_up > D_i: the configuration misses its deadline
                # even alone (t_up_tti absent from older reports, whose C
                # included it).
                t_up = r.get("config", {}).get("t_up_tti", 0) or 0
                rows.append({
                    "rb": rb, "method": m, "flowset": fs_dir.name,
                    "max_attempts": max((f["a"] for f in flows), default=np.nan),
                    "max_c_tti": max((f["c_tti"] for f in flows), default=np.nan),
                    "c_gt_d": any(f["c_tti"] + t_up > f["deadline_tti"] for f in flows),
                })
    return pd.DataFrame(rows)


def merge_configs(new: pd.DataFrame) -> None:
    """Write CONFIGS_CSV with `new` in place of the rows of the same (budget,
    method) pairs. Every other row is kept verbatim, and the order is the one
    a full derive gives: budget, then method as in AMAX_RULE, then flowset."""
    import csv
    import io
    if new.empty:
        print(f"no baseline reports under {SWEEP}: {CONFIGS_CSV.name} kept")
        return
    fresh = list(csv.reader(io.StringIO(new.to_csv(index=False))))
    # Without a CSV yet (a run from empty: scripts/run_paper_experiment.sh),
    # the derived rows are all there is.
    old = [fresh[0]]
    if CONFIGS_CSV.exists():
        with open(CONFIGS_CSV, newline="") as f:
            old = list(csv.reader(f))
    header, old = old[0], old[1:]
    if fresh and fresh[0] != header:
        sys.exit(f"{CONFIGS_CSV.name}: columns differ from the derived ones; cannot merge")
    fresh = fresh[1:]
    replaced = {(r[0], r[1]) for r in fresh}
    rows = [r for r in old if (r[0], r[1]) not in replaced] + fresh
    order = {m: i for i, m in enumerate(AMAX_RULE)}
    rows.sort(key=lambda r: (int(r[0]), order.get(r[1], len(order)), r[1], r[2]))
    with open(CONFIGS_CSV, "w", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows([header] + rows)
    print(f"wrote {CONFIGS_CSV.name}: {len(rows)} rows ({len(fresh)} derived now)")


LINE = re.compile(r"^\[(\d+)/(\d+)\] .* -> (SCHEDULABLE|not schedulable|timed out|solver error)"
                  r"(?: \(([\d.]+(?:e[+-]?\d+)?) ms\))?")   # from 1e6 ms on, "1.2e+06 ms"
HOST = re.compile(r"^Host: (\S+)")


def derive_ilp_timing(rb: int = MAIN_BUDGET, sweep: Path | None = None) -> pd.DataFrame:
    """One row per ILP log of budget rb under sweep (SWEEP unless given)."""
    rows = []
    for log in sorted(((SWEEP if sweep is None else sweep) / f"rb{rb}" / "logs" / "ilp").glob("flowset_*.log")):
        times, first, n_cap, n_err, n, host = [], None, 0, 0, 0, ""
        for line in log.read_text().splitlines():
            hm = HOST.match(line)
            if hm:
                host = hm.group(1)
                continue
            mm = LINE.match(line)
            if not mm:
                continue
            n += 1
            if mm.group(3) == "timed out":
                t = CAP_S
                n_cap += 1
            else:
                t = float(mm.group(4)) / 1000.0
                n_err += mm.group(3) == "solver error"
            times.append(t)
            if mm.group(3) == "SCHEDULABLE" and first is None:
                first = sum(times)
        rows.append({"flowset": log.stem, "host": host, "n_candidates": n, "n_capped": n_cap,
                     "n_errored": n_err,
                     "decide_all_s": sum(times),
                     "first_table_s": first if first is not None else np.nan,
                     "median_candidate_s": float(np.median(times)) if times else np.nan})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ load ----

def load(csv_dir: Path | None = None, configs_csv: Path | None = None) -> pd.DataFrame:
    """The verdicts (CSV_DIR unless given) under the A^max rule."""
    csv_dir = CSV_DIR if csv_dir is None else csv_dir
    configs_csv = CONFIGS_CSV if configs_csv is None else configs_csv
    d = pd.read_csv(csv_dir / "output_sweep.csv")
    cfg = pd.read_csv(configs_csv)
    missing = set(d.method) & set(AMAX_RULE) - set(cfg.method)
    if missing:
        print(f"warning: no {configs_csv.name} rows for {sorted(missing)}, so the "
              "A^max rule cannot apply to them; run --derive after their sweep",
              file=sys.stderr)
    d = d.merge(cfg, on=["rb", "method", "flowset"], how="left")
    outside = d.method.isin(AMAX_RULE) & (d.max_attempts > A_MAX)
    d["verdict_model"] = d.verdict.where(~outside, "ko_amax")
    return d


# --------------------------------------------------------------- figures ----
# Visual identity (colours, dashes, markers, fonts) is plot_evaluation.py's, so
# these panels sit beside the earlier figures without a second palette.
sys.path.insert(0, str(HERE))
import matplotlib as mpl  # noqa: E402
mpl.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import plot_evaluation as pe  # noqa: E402

# The paper's name for the proposed method, which the framework calls `ilp`;
# only the paper's figures use it (plot_evaluation.py keeps "ILP").
NAME = "SIREN"
LABEL = {**pe.LABEL, "ilp": f"{NAME} (proposed)"}

# The methods the figures draw, in plotting order (top to bottom / legend).
# EDF and 5G-TPS (either HARQ count) are left out of every figure; the text
# still quotes 5G-TPS's numbers, so its macros are written all the same.
PLOTTED = ["ilp", "must", "qospf", "rr", "pf"]


def reported(d: pd.DataFrame) -> list:
    """The methods with RQ2 macros: the ILP and the baselines, then 5G-TPS
    (enough HARQs) if it was run."""
    return ["ilp"] + BASELINES + ([TPS] if TPS in set(d.method) else [])
MIN_N = 10                             # fewer paired flowsets: no median drawn
# fig_rq3_rb_usage draws every budget at which all plotted methods schedule
# some flowset, however few (the author's choice, 26 October 2026: the whole
# sweep in the figure; 6 flowsets at 51 RBs then). The text gives the n.
USAGE_MIN_N = 1
VERDICTS = ["ok", "timeout", "ko_unschedulable", "ko_deadline_miss", "ko_amax"]
VLABEL = {**pe.VERDICT_LABEL, "timeout": "undecided (tuple cap)",
          "ko_amax": "needs $A>A^{\\max}$"}
VCOLOR = {**pe.VERDICT_COLOR, "ko_amax": "#d03b3b"}
VHATCH = {"ko_amax": "////"}


def save(fig, out: Path, name: str, formats) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for ext in formats:
        p = out / f"{name}.{ext}"
        fig.savefig(p, dpi=400 if ext == "png" else None, bbox_inches="tight",
                    pad_inches=0.02)
        print(f"  wrote {p}")
    plt.close(fig)


def fig_verdicts(d: pd.DataFrame, a) -> None:
    """RQ2 (a): verdict mix per method at the main budget."""
    x = d[d.rb == MAIN_BUDGET]
    methods = PLOTTED
    tab = (pd.crosstab(x.method, x.verdict_model, normalize="index")
           .reindex(index=methods).reindex(columns=VERDICTS, fill_value=0.0).fillna(0.0))
    fig, ax = plt.subplots(figsize=(a.width, a.height))
    y = np.arange(len(methods))[::-1]
    left = np.zeros(len(methods))
    for v in VERDICTS:
        w = tab[v].to_numpy()
        if not w.any():
            continue
        ax.barh(y, w, left=left, height=0.56, color=VCOLOR[v], hatch=VHATCH.get(v),
                edgecolor=pe.SURFACE, linewidth=0.7, zorder=3)
        for yi, wi, li in zip(y, w, left):
            if wi >= 0.09 and v != "ok":
                ax.text(li + wi / 2, yi, f"{100 * wi:.0f}", ha="center", va="center",
                        color=pe.SURFACE, fontsize=a.fontsize - 1.2, zorder=4)
        left += w
    for yi, m in zip(y, methods):
        ax.text(1.015, yi, f"{100 * tab.loc[m, 'ok']:.1f}%", va="center", ha="left",
                color=pe.INK, fontsize=a.fontsize - 0.5)
    ax.set_yticks(y, [LABEL[m] for m in methods])
    ax.get_yticklabels()[0].set_fontweight("bold")
    ax.set_xlim(0, 1)
    ax.set_ylim(-0.6, len(methods) - 0.4)
    ax.set_xticks(np.arange(0, 1.01, 0.25), ["0", "25", "50", "75", "100"])
    ax.set_xlabel("flowsets (%)")
    ax.yaxis.grid(False)
    ax.tick_params(axis="y", length=0)
    save(fig, a.outdir, "fig_rq2_verdicts", a.formats)


def fig_scaling(d: pd.DataFrame, a, budgets: list | None = None) -> None:
    """RQ2 (b): schedulable share against the RB budget (BUDGETS unless given)."""
    budgets = BUDGETS if budgets is None else budgets
    fig, ax = plt.subplots(figsize=(a.width, a.height))
    share = (d.assign(ok=d.verdict_model == "ok", nu=d.verdict_model != "ko_unschedulable")
             .groupby(["method", "rb"])[["ok", "nu"]].mean() * 100)
    il = share.loc["ilp"]
    ax.fill_between(budgets, il.ok, il.nu, color=pe.COLOR["ilp"], alpha=0.14, lw=0,
                    zorder=1, label=f"{NAME} undecided")
    methods = PLOTTED
    for m in methods:
        s = share.loc[m]
        ax.plot(budgets, s.ok, color=pe.COLOR[m], ls=pe.DASH[m], marker=pe.MARKER[m],
                ms=2.4, zorder=3)
    # Direct labels: the PF family ends within a point (as MUST and EDF do
    # when both are drawn), so a label at each line's own end would overlap,
    # and a legend row per method does not fit a 1.7 in panel. Each method of
    # a group gets its own label, in its line's colour, stacked in the order
    # the lines end (highest on top) and centred on the group's mean.
    end = share.xs(budgets[-1], level="rb").ok
    step = (a.fontsize - 1.3) * 1.15          # points between stacked labels
    # The band's label stays above the band, inside the plotted range.
    last = budgets[-1]
    # (text, x, y of the anchor, offset in points, alignment, colour)
    labels = [
            (f"{NAME}: not proved\nunschedulable", last, share.loc["ilp"].nu.iloc[-1], 0, 3, "right",
             "bottom", pe.COLOR["ilp"]),
            (NAME, last, end["ilp"], 3, 0, "left", "center", pe.COLOR["ilp"])]
    for group in (["must", "edf"], ["qospf", "rr", "pf"]):
        g = sorted((m for m in group if m in methods), key=lambda m: -end[m])
        for k, m in enumerate(g):
            labels.append((pe.LABEL[m], last, end[g].mean(), 3, ((len(g) - 1) / 2 - k) * step,
                           "left", "center", pe.COLOR[m]))
    if TPS in methods:
        # 5G-TPS climbs through the band label's spot above the band, so that
        # label moves inside the band, midway between the second and third
        # budgets, where no line runs; the 5G-TPS label hangs from its end.
        mid = (budgets[1] + budgets[2]) / 2
        band_mid = (il.ok.loc[budgets[1]:budgets[2]].mean() + il.nu.loc[budgets[1]:budgets[2]].mean()) / 2
        labels[0] = (f"{NAME}: not proved\nunschedulable", mid, band_mid, 0, 0, "center", "center",
                     pe.COLOR["ilp"])
        labels.append((TPS_DIRECT_LABEL, last, end[TPS], 3, 3, "left", "top", pe.COLOR[TPS]))
    for text, x, y, dx, dy, ha, va, col in labels:
        ax.annotate(text, (x, y), xytext=(dx, dy), textcoords="offset points",
                    ha=ha, va=va, fontsize=a.fontsize - 1.3, color=col,
                    linespacing=0.95, annotation_clip=False)
    ax.set_xlim(budgets[0] - 6, budgets[-1] + 6)
    ax.set_xticks(budgets)
    ax.set_xlabel("RB budget $B^{\\max}$")
    ax.set_ylabel("schedulable flowsets (%)")
    ax.set_ylim(0, 100)
    save(fig, a.outdir, "fig_rq2_scaling", a.formats)


def paired_gain(d: pd.DataFrame, col: str, budgets: list | None = None) -> pd.DataFrame:
    """ILP minus baseline residual, on the flowsets both schedule (BUDGETS
    unless given)."""
    x = d[d.verdict_model == "ok"].assign(res=lambda t: t.rb_capacity - t[col])
    rows = []
    for rb in (BUDGETS if budgets is None else budgets):
        il = x[(x.rb == rb) & (x.method == "ilp")].set_index("flowset").res
        for m in BASELINES:
            b = x[(x.rb == rb) & (x.method == m)].set_index("flowset").res
            c = il.index.intersection(b.index)
            g = il.loc[c] - b.loc[c]
            rows.append({"rb": rb, "method": m, "n": len(c), "med": g.median(),
                         "q1": g.quantile(0.25), "q3": g.quantile(0.75),
                         "ilp_med": il.loc[c].median(), "base_med": b.loc[c].median(),
                         "worse": int((g < 0).sum())})
    return pd.DataFrame(rows)


# RQ2's table (the paper's Table of verdict shares, which replaced Fig. 2):
# per budget, the share of the flowsets each method schedules, leaves
# undecided, proves unschedulable, or misses a deadline on. Only the tabular
# is written; its caption and label are the paper's (results.tex).
TABLE_COLS = [("ok", "sched."), ("timeout", "undec."), ("ko_unschedulable", "unsch."),
              ("ko_deadline_miss", "missed")]


def table_rq2(d: pd.DataFrame, out: Path, budgets: list | None = None, note: str = "") -> None:
    """table_rq2.tex: the verdict shares (%) per budget and method. In bold:
    each budget's highest schedulable share, and SIREN's missed share (none:
    its tables are proved). Undecided and proved unschedulable apply to SIREN
    only; a baseline's are '--'. note, if any, closes the table."""
    budgets = budgets or BUDGETS
    shares = {(rb, m): d[(d.rb == rb) & (d.method == m)].verdict_model.value_counts(normalize=True) * 100
              for rb in budgets for m in PLOTTED}
    for (rb, m), s in shares.items():
        other = s.drop([c for c, _ in TABLE_COLS], errors="ignore")
        if other.sum() > 0:
            print(f"warning: table_rq2: rb{rb} {m} has verdicts outside its columns: "
                  f"{other.round(1).to_dict()}", file=sys.stderr)
    best = {rb: max(shares[(rb, m)].get("ok", 0.0) for m in PLOTTED) for rb in budgets}

    def cell(rb, m, c):
        if m != "ilp" and c in ("timeout", "ko_unschedulable"):
            return "--"
        v = shares[(rb, m)].get(c, 0.0)
        bold = (c == "ok" and v == best[rb]) or (m == "ilp" and c == "ko_deadline_miss")
        return rf"\textbf{{{v:.1f}}}" if bold else f"{v:.1f}"

    n = len(budgets) * len(TABLE_COLS)
    lines = [
        "% Generated by analysis/plot_paper.py (table_rq2): RQ2's verdict shares (%). Do not edit.",
        r"\footnotesize",
        r"\setlength{\tabcolsep}{3pt}",
        r"\begin{tabular}{@{}l" + "r" * n + r"@{}}",
        r"\toprule",
        " & " + " & ".join(rf"\multicolumn{{{len(TABLE_COLS)}}}{{c}}{{$B^{{\max}}={rb}$ RBs}}"
                           for rb in budgets) + r" \\",
        "".join(rf"\cmidrule(lr){{{2 + len(TABLE_COLS) * i}-{1 + len(TABLE_COLS) * (i + 1)}}}"
                for i in range(len(budgets))),
        "Flowsets (\\%) & " + " & ".join(h for _ in budgets for _, h in TABLE_COLS) + r" \\",
        r"\midrule",
    ]
    for m in PLOTTED:
        name_ = r"\siren" if m == "ilp" else LABEL[m]
        lines.append(name_ + " & " + " & ".join(cell(rb, m, c) for rb in budgets for c, _ in TABLE_COLS)
                     + r" \\")
    lines.append(r"\bottomrule")
    if note:
        lines.append(rf"\multicolumn{{{n + 1}}}{{@{{}}l}}{{\scriptsize {note}}} \\")
    lines.append(r"\end{tabular}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "table_rq2.tex").write_text("\n".join(lines) + "\n")
    print(f"  wrote {out / 'table_rq2.tex'}")


def fig_residual(d: pd.DataFrame, a, budgets: list | None = None) -> pd.DataFrame:
    """RQ3: guaranteed residual the ILP leaves beyond each baseline, paired."""
    g = paired_gain(d, "rb_peak", budgets)
    pairs = [(-1.5, "must"), (1.5, "qospf")]
    fig, ax = plt.subplots(figsize=(a.width, a.height))
    for off, m in pairs:
        s = g[(g.method == m) & (g.n >= MIN_N)]
        ax.errorbar(s.rb + off, s.med, yerr=[s.med - s.q1, s.q3 - s.med],
                    color=pe.COLOR[m], ls=pe.DASH[m], marker=pe.MARKER[m], ms=2.4,
                    elinewidth=0.6, capsize=1.2, zorder=3, label=f"vs {pe.LABEL[m]}")
    ax.legend(loc="upper left", fontsize=a.fontsize - 1.3, handlelength=2.2,
              borderaxespad=0.3)
    ax.axhline(0, color=pe.INK_MUTED, lw=0.6, zorder=2)
    # Only the budgets with a median to draw: an empty column beside lines
    # falling towards zero reads as a negative gain (51 RBs, as the text says).
    budgets = sorted(g[g.method.isin([m for _, m in pairs]) & (g.n >= MIN_N)].rb.unique())
    ax.set_xticks(budgets)
    ax.set_xlim(budgets[0] - 8, budgets[-1] + 8)
    ax.set_xlabel("RB budget $B^{\\max}$")
    ax.set_ylabel("extra RBs free in every TTI")
    save(fig, a.outdir, "fig_rq3_residual_gain", a.formats)
    return g


def usage(d: pd.DataFrame, budgets: list | None = None) -> pd.DataFrame:
    """Peak RB usage as a share of B^max (%), per plotted method and budget, on
    the flowsets every plotted method schedules at that budget, so that each
    method is measured on the same flowsets (n of them per budget)."""
    x = d[d.method.isin(PLOTTED) & (d.verdict_model == "ok")]
    rows = []
    for rb in (BUDGETS if budgets is None else budgets):
        y = x[x.rb == rb]
        common = set.intersection(*(set(y[y.method == m].flowset) for m in PLOTTED))
        for m in PLOTTED:
            u = 100 * y[(y.method == m) & y.flowset.isin(common)].rb_peak / rb
            rows.append({"rb": rb, "method": m, "n": len(common), "med": u.median()})
    return pd.DataFrame(rows)


def fig_usage(d: pd.DataFrame, a, budgets: list | None = None) -> pd.DataFrame:
    """RQ3, as a share of the cell: each method's median peak RB usage, on the
    flowsets all of them schedule, with the value at every point."""
    u = usage(d, budgets)
    u = u[u.n >= USAGE_MIN_N]
    fig, ax = plt.subplots(figsize=(a.width, a.height))
    ax.set_ylim(0, 112)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_xlabel("RB budget $B^{\\max}$")
    ax.set_ylabel("peak RB usage (% of $B^{\\max}$)")
    if u.empty:
        save(fig, a.outdir, "fig_rq3_rb_usage", a.formats)
        return u
    drawn = sorted(u.rb.unique())
    ax.set_xticks(drawn)
    ax.set_xlim(drawn[0] - 8, drawn[-1] + 8)
    # The y range follows the data (medians between about 40% and 100%),
    # with room below the lowest point for the value written under it and
    # above the highest for the one written over it.
    lo = max(0, 10 * np.floor((u.med.min() - 10) / 10))
    hi = min(112, u.med.max() + 12)
    step = 20 if min(hi, 100) - lo > 50 else 10
    ax.set_ylim(lo, hi)
    ax.set_yticks(np.arange(np.ceil(lo / step) * step, min(hi, 100) + 1, step))
    # Data units per point, now that the limits are set: offsets in points
    # look the same whatever the number of budgets drawn.
    box = ax.get_window_extent()
    px = (ax.get_xlim()[1] - ax.get_xlim()[0]) / (box.width * 72 / fig.dpi)
    py = (ax.get_ylim()[1] - ax.get_ylim()[0]) / (box.height * 72 / fig.dpi)
    # Points that would overlap (values closer than a marker, 3.5 pt) are
    # spread sideways around their budget, 3.2 pt apart, in the methods'
    # usual order; a point alone stays on its budget's line. The baselines'
    # peaks coincide on most flowsets (their configuration, the same for all,
    # sets them), and SIREN's meets them where every table needs the whole
    # budget. The value of points that coincide is written once.
    xs = {}
    for rb, s in u.groupby("rb"):
        groups = []
        for _, r in s.sort_values("med").iterrows():
            if groups and r.med - groups[-1][-1].med < 3.5 * py:
                groups[-1].append(r)
            else:
                groups.append([r])
        for g in groups:
            ms = sorted((r.method for r in g), key=PLOTTED.index)
            for k, m in enumerate(ms):
                xs[(m, rb)] = rb + (k - (len(ms) - 1) / 2) * 3.2 * px
    for m in PLOTTED:
        s = u[u.method == m]
        ax.plot([xs[(m, rb)] for rb in s.rb], s.med, color=pe.COLOR[m], ls=pe.DASH[m],
                marker=pe.MARKER[m], ms=2.4, zorder=3)
    num = dict(textcoords="offset points", ha="center", fontsize=a.fontsize - 1.8,
               annotation_clip=False)
    # The highest baseline value per budget, to tell a dip: where the
    # baselines are higher at both neighbouring budgets, their lines come
    # down into the points from above.
    btop = u[u.method != "ilp"].groupby("rb").med.max()
    for rb, s in u.groupby("rb"):
        il = s[s.method == "ilp"].med.iloc[0]
        ax.annotate(f"{il:.0f}", (xs[("ilp", rb)], il), xytext=(0, -4), va="top",
                    color=pe.COLOR["ilp"], **num)
        # The highest baseline value above its points, any lower one below
        # them, so that no number sits on the lines above it; at a dip, the
        # highest one goes below too, clear of the lines coming down to it.
        base = s[s.method != "ilp"].assign(v=lambda t: t.med.round())
        top = base.v.max()
        k = drawn.index(rb)
        nb = [btop[r] for r in drawn[max(k - 1, 0):k + 2] if r != rb]
        dip = bool(nb) and all(b > btop[rb] for b in nb)
        for v, grp in base.groupby("v"):
            x = np.mean([xs[(m, rb)] for m in grp.method])
            col = pe.COLOR[grp.method.iloc[0]] if len(grp) == 1 else pe.INK
            above = v == top and not dip
            ax.annotate(f"{v:.0f}", (x, v), xytext=(0, 3.5) if above else (0, -4),
                        va="bottom" if above else "top", color=col, **num)
    # Names at the line ends, as in fig_rq2_scaling: the baselines stacked in
    # the order they end, centred on their mean, and SIREN's name moved clear
    # of that stack when the two would touch.
    last = drawn[-1]
    end = u[u.rb == last].set_index("method").med
    step = (a.fontsize - 1.3) * 1.15 * py          # one label's height, data units
    order = sorted(BASELINES, key=lambda m: -end[m])
    mid = end[BASELINES].mean()
    ys = {m: mid + ((len(order) - 1) / 2 - k) * step for k, m in enumerate(order)}
    il = end["ilp"]
    if il <= mid:
        ys["ilp"] = min(il, min(ys[m] for m in order) - step)
    else:
        ys["ilp"] = max(il, max(ys[m] for m in order) + step)
    x = max(xs[(m, last)] for m in PLOTTED)
    for m in ["ilp"] + order:
        ax.annotate(NAME if m == "ilp" else pe.LABEL[m], (x, ys[m]), xytext=(5, 0),
                    textcoords="offset points", ha="left", va="center",
                    fontsize=a.fontsize - 1.3, color=pe.COLOR[m], annotation_clip=False)
    save(fig, a.outdir, "fig_rq3_rb_usage", a.formats)
    return u


def fig_time(t: pd.DataFrame, v: pd.DataFrame, a) -> None:
    """RQ4: time to a first table, and to decide every tuple (sequential)."""
    m = t.merge(v[["flowset", "verdict"]], on="flowset")
    fig, ax = plt.subplots(figsize=(a.width, a.height))
    series = [
        (m[m.verdict == "ok"].first_table_s, "first table", pe.COLOR["ilp"], (0, ())),
        (m[m.verdict == "ok"].decide_all_s, "all tuples, schedulable", "#184f95", (0, (4, 1.4))),
        (m[m.verdict == "ko_unschedulable"].decide_all_s, "all tuples, unsched.",
         pe.VERDICT_COLOR["ko_unschedulable"], (0, (1.6, 1.4))),
    ]
    # Text spots (x, y, ha) in data units, picked in the empty regions of
    # this run's curves: upper left, and right of the schedulable all-tuples
    # curve, which the x range extends to make room for.
    spots = {"first table": (11, 47, "right"), "all tuples, schedulable": (1.3e4, 11, "left"),
             "all tuples, unsched.": (45, 86, "right")}
    for vals, lab, col, ls in series:
        xs, ys = pe.ecdf(vals.dropna().to_numpy())
        ax.step(xs, 100 * ys, where="post", color=col, ls=ls)
        x, y, ha = spots[lab]
        ax.text(x, y, lab.replace(", ", ",\n"), color=col, ha=ha, va="center",
                fontsize=a.fontsize - 1.4, linespacing=0.95)
    # "1 h" goes mid-height: at the top it would sit on the other curves.
    for x, y, lab in [(60, 98, "1 min"), (3600, 50, "1 h")]:
        ax.axvline(x, color=pe.INK_MUTED, lw=0.5, zorder=1)
        ax.text(x * 1.15, y, lab, color=pe.INK_SOFT, va="top", fontsize=a.fontsize - 1.5)
    ax.set_xscale("log")
    ax.set_xlim(right=2e6)
    ax.set_xticks([1e1, 1e3, 1e5])
    ax.set_xlabel("sequential solving time $x$ (s)")
    ax.set_ylabel("flowsets with time $\\leq x$ (%)")
    ax.set_ylim(0, 100)
    save(fig, a.outdir, "fig_rq4_time", a.formats)


RQ4_STATUS = [("schedulable", "table found", pe.VERDICT_COLOR["ok"], "o"),
              ("infeasible", "proved infeasible", pe.VERDICT_COLOR["ko_unschedulable"], "s"),
              ("timeout", "time limit", pe.VERDICT_COLOR["timeout"], "^")]


def fig_rq4_hyperperiod(r: pd.DataFrame, a) -> None:
    """RQ4: time to decide one ILP model against its hyperperiod (log-log)."""
    fig, ax = plt.subplots(figsize=(a.width, a.height))
    rng = np.random.default_rng(0)
    for status, label, col, mk in RQ4_STATUS:
        s = r[r.status == status]
        # Multiplicative jitter, so the points of one hyperperiod stay apart
        # on the log axis without leaving their column.
        x = s.h_ms / 1000 * np.exp(rng.uniform(-0.12, 0.12, len(s)))
        ax.scatter(x, s.solve_s, s=4, marker=mk, color=col, alpha=0.55, lw=0, zorder=3,
                   label=label)
    med = r.groupby("target_h_ms").solve_s.median()
    ax.plot(med.index / 1000, med.values, color=pe.INK, lw=1.0, zorder=4, label="median")
    cap = r.cap_s.max()
    ax.axhline(cap, color=pe.INK_MUTED, lw=0.5, ls=(0, (3, 2)), zorder=1)
    ax.text(r.h_ms.min() / 1000, cap * 0.75, "time limit", color=pe.INK_SOFT,
            fontsize=a.fontsize - 1.5, va="top")
    ax.set_xscale("log")
    ax.set_yscale("log")
    # Ticks at the target hyperperiods, plain numbers on both axes.
    plain = mpl.ticker.FuncFormatter(lambda v, _: f"{v:g}")
    targets = sorted(r.target_h_ms.unique() / 1000)
    ax.set_xticks(targets, [f"{x:g}" for x in targets])
    ax.xaxis.set_minor_locator(mpl.ticker.NullLocator())
    ax.yaxis.set_major_formatter(plain)
    ax.set_xlabel("hyperperiod $H$ (s)")
    ax.set_ylabel("solving time (s)")
    # Above the axes: the points fill every corner inside.
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=2, frameon=False,
              columnspacing=0.8, fontsize=a.fontsize - 1.6, handletextpad=0.2,
              borderaxespad=0.2, markerscale=1.6)
    save(fig, a.outdir, "fig_rq4_hyperperiod", a.formats)


def spell(n: int) -> str:
    """A number as a macro-safe word: 25200 -> TwoFiveTwoZeroZero."""
    words = ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"]
    return "".join(words[int(c)] for c in str(n))


def rq4_numbers(r: pd.DataFrame, cmd, fmt) -> None:
    """Macros of the RQ4 hyperperiod experiment, per target hyperperiod."""
    cmd("RqFourModels", len(r))
    cmd("RqFourFlowsets", r.flowset.nunique())
    cmd("RqFourCapSec", fmt(r.cap_s.max()))
    for h, s in r.groupby("target_h_ms"):
        k = spell(int(h))
        cmd(f"RqFourMedianSec{k}", f"{s.solve_s.median():.1f}")
        cmd(f"RqFourTimeoutShare{k}", f"{100 * (s.status == 'timeout').mean():.0f}\\%")
        cmd(f"RqFourMedianJobs{k}", fmt(s.n_jobs.median()))
        cmd(f"RqFourMedianBinaries{k}", f"{s.n_binaries.median():,.0f}".replace(",", "\\,"))
    # The answer to RQ4: the largest target at which most models are decided
    # within the limit (fewer than half time out, so its median is a solving
    # time, not the limit), and how much the jobs and that median grow from
    # the smallest target to it.
    by = r.groupby("target_h_ms")
    decided = [h for h, s in by if (s.status == "timeout").mean() < 0.5]
    if decided:
        lo, hi = by.get_group(min(r.target_h_ms)), by.get_group(max(decided))
        cmd("RqFourDecidedUpToSec", f"{max(decided) / 1000:g}")
        cmd("RqFourJobsGrowth", f"{hi.n_jobs.median() / lo.n_jobs.median():.0f}")
        cmd("RqFourTimeGrowth", f"{hi.solve_s.median() / lo.solve_s.median():.0f}")
    # The control (rq4_hyperperiod.py compare): RQ4's time over the time alone.
    if RQ4_CONTROL_CSV.exists():
        c = pd.read_csv(RQ4_CONTROL_CSV)
        cmd("RqFourControlModels", len(c))
        cmd("RqFourControlRatio", f"{c.ratio.median():.2f}")


# ---------------------------------------------------------------- macros ----

def name(x) -> str:
    words = {51: "FiftyOne", 79: "SeventyNine", 107: "HundredSeven", 135: "HundredThirtyFive",
             "ilp": "Ilp", "must": "Must", "edf": "Edf", "qospf": "Qospf", "rr": "Rr",
             "pf": "Pf", "5gtps": "Fivegtps", TPS: "FivegtpsHarq"}
    return words[x]


def harmonic_share(flowsets) -> float:
    """Share (%) of the flowsets' pairs of distinct periods in which one period
    divides the other (the paper's Flowsets paragraph), from output/flowsets/."""
    pairs = harmonic = 0
    for fs in flowsets:
        periods = [a["period"] for a in json.loads((FLOWSETS / f"{fs}.json").read_text())]
        for p, q in itertools.combinations(periods, 2):
            if p != q:
                pairs += 1
                harmonic += max(p, q) % min(p, q) == 0
    return 100 * harmonic / pairs


def app_appearances(flowsets) -> pd.Series:
    """How many of the flowsets hold each application of input/app_database.json."""
    apps = json.loads((ROOT / "input" / "app_database.json").read_text())
    n = pd.Series(0, index=sorted(apps))
    for fs in flowsets:
        for a in json.loads((FLOWSETS / f"{fs}.json").read_text()):
            n[a["app"]] += 1
    return n


def write_numbers(d: pd.DataFrame, g: pd.DataFrame, gw: pd.DataFrame,
                  t: pd.DataFrame, out: Path, rq4: pd.DataFrame | None = None,
                  first: pd.DataFrame | None = None, sources: str = "",
                  tuples_all: pd.Series | None = None) -> None:
    """numbers_results.tex. first: the ILP timing (derive_ilp_timing) with the
    ILP's verdict and n_candidates_total per flowset, that the first-table and
    tuple-count macros read instead of t and d (--partial: the verdict run's).
    tuples_all: n_candidates_total of every generated flowset, for
    MedianTuplesAll (default: those of first, or of d)."""
    L = ["% Generated by analysis/plot_paper.py -- do not edit.",
         "% sources: " + (sources or "output_sweep.csv, derived_baseline_configs.csv, "
                          f"{TIMING_CSV.name}")]
    cmd = lambda k, v: L.append(f"\\newcommand{{\\{k}}}{{{v}}}")
    fmt = lambda x: f"{x:.0f}" if pd.notna(x) else "--"
    # A gain may be negative: a minus sign, not a hyphen, in the text.
    signed = lambda x: ("--" if pd.isna(x) else f"{int(round(x))}" if round(x) >= 0
                        else f"$-${-int(round(x))}")
    main = d[d.rb == MAIN_BUDGET]
    cmd("NFlowsets", main.flowset.nunique())
    cmd("NGenerated", len(list(FLOWSETS.glob("flowset_*.json"))))
    seen = app_appearances(main.flowset.unique())
    cmd("AppAppearancesMin", int(seen.min()))
    cmd("AppAppearancesMax", int(seen.max()))
    cmd("HarmonicPairShare", f"{harmonic_share(main.flowset.unique()):.0f}\\%")
    # RQ2: verdict counts, every method and budget
    for rb in BUDGETS:
        x = d[d.rb == rb]
        for m in reported(d) + ["5gtps"]:
            c = x[x.method == m].verdict_model.value_counts()
            cmd(f"Ok{name(m)}{name(rb)}", f"{100 * c.get('ok', 0) / x.flowset.nunique():.1f}\\%")
            cmd(f"N{name(m)}{name(rb)}", c.get("ok", 0))
        # The PF family's lowest and highest share: its range in the text holds
        # whichever of them leads (RR overtook QoS-PF on some flowset sets)
        fam = [100 * (x[x.method == m].verdict_model == "ok").sum() / x.flowset.nunique()
               for m in PF_FAMILY]
        cmd(f"OkPfFamilyMin{name(rb)}", f"{min(fam):.1f}\\%")
        cmd(f"OkPfFamilyMax{name(rb)}", f"{max(fam):.1f}\\%")
        il = x[x.method == "ilp"].verdict_model.value_counts()
        cmd(f"IlpUnsched{name(rb)}", il.get("ko_unschedulable", 0))
        cmd(f"IlpUndecided{name(rb)}", il.get("timeout", 0))
        cmd(f"Amax{name(rb)}", int((x[x.method == "must"].verdict_model == "ko_amax").sum()))
        if TPS in reported(d):
            cmd(f"Amax{name(TPS)}{name(rb)}",
                int((x[x.method == TPS].verdict_model == "ko_amax").sum()))
    # RQ2: the ILP's lead over MUST in schedulable share (percentage points),
    # its smallest and largest over the budgets; from the shares as the
    # table rounds them, so that the two agree
    share = lambda m: (d[d.method == m].groupby("rb").verdict_model
                       .apply(lambda v: 100 * (v == "ok").mean()).round(1))
    lead = share("ilp") - share("must")
    cmd("LeadMustMin", f"{lead.min():.1f}")
    cmd("LeadMustMax", f"{lead.max():.1f}")
    # RQ2: agreement with the ILP's verdict at the main budget
    v = main.pivot(index="flowset", columns="method", values="verdict_model")
    for m in BASELINES + [m for m in reported(d) if m == TPS]:
        cmd(f"MissOnProved{name(m)}", int(((v.ilp == "ok") & (v[m] != "ok")).sum()))
        cmd(f"OkOnUnsched{name(m)}", int(((v.ilp == "ko_unschedulable") & (v[m] == "ok")).sum()))
        cmd(f"OkOnUndecided{name(m)}", int(((v.ilp == "timeout") & (v[m] == "ok")).sum()))
    miss = [int(((v.ilp == "ok") & (v[m] != "ok")).sum()) for m in PF_FAMILY]
    cmd("MissOnProvedPfFamilyMin", min(miss))
    cmd("MissOnProvedPfFamilyMax", max(miss))
    sim = d[d.method.isin(BASELINES)]
    cmd("NBaselineRuns", f"{len(sim):,}".replace(",", "\\,"))
    cmd("NRbViolations", int(sim.rb_violation.astype(str).eq("True").sum()))
    cmd("NHarqOverlaps", int(sim.harq_overlap.astype(str).eq("True").sum()))
    if TPS in reported(d):
        tps = d[d.method == TPS]
        cmd(f"NRbViolations{name(TPS)}", int(tps.rb_violation.astype(str).eq("True").sum()))
        cmd(f"NHarqOverlaps{name(TPS)}", int(tps.harq_overlap.astype(str).eq("True").sum()))
    worst = main[main.method.isin(BASELINES)].worst_overrun_tti.max() * pe.TTI_MS
    cmd("WorstLatenessMs", fmt(worst))
    # RQ3: paired gains (guaranteed, window mean) and how the ILP peak was found
    for tab, tag in [(g, "Gain"), (gw, "WinGain")]:
        for _, r in tab.iterrows():
            k = f"{tag}{name(r.method)}{name(r.rb)}"
            cmd(k, signed(r.med) if r.n >= MIN_N else "--")
            cmd(f"{k}Qone", signed(r.q1) if r.n >= MIN_N else "--")
            cmd(f"{k}Qthree", signed(r.q3) if r.n >= MIN_N else "--")
            if tag == "Gain":
                cmd(f"Paired{name(r.method)}{name(r.rb)}", r.n)
                cmd(f"Worse{name(r.method)}{name(r.rb)}", r.worse)
    # RQ3: peak RB usage (% of B^max) on the flowsets all plotted methods
    # schedule (fig_rq3_rb_usage), and how many there are, per budget
    for rb, s in usage(d).groupby("rb"):
        n = s.n.iloc[0]
        cmd(f"UsageFlowsets{name(rb)}", n)
        for _, r in s.iterrows():
            cmd(f"Usage{name(r.method)}{name(rb)}",
                f"{r.med:.0f}\\%" if n >= USAGE_MIN_N and pd.notna(r.med) else "--")
    ilp_ok = main[(main.method == "ilp") & (main.verdict == "ok")]
    cmd("PeakOptimal", int(ilp_ok.rb_peak_optimal_over_decided.astype(str).eq("True").sum()))
    # RQ4: solving time and model size at the main budget
    tm = t.merge(main[main.method == "ilp"][["flowset", "verdict", "n_candidates_total"]],
                 on="flowset")
    ok = tm[tm.verdict == "ok"]
    ft = tm if first is None else first
    ft_ok = ft[ft.verdict == "ok"].first_table_s
    cmd("MedianCandidateSec", f"{tm.median_candidate_s.median():.1f}")
    cmd("FirstTableMedianSec", f"{ft_ok.median():.1f}" if ft_ok.median() < 10 else fmt(ft_ok.median()))
    for s, k in [(60, "Minute"), (600, "TenMinutes"), (3600, "Hour")]:
        cmd(f"FirstTableWithin{k}", f"{100 * (ft_ok <= s).mean():.0f}\\%")
    cmd("DecideAllMedianHours", f"{ok.decide_all_s.median() / 3600:.1f}")
    un = tm[tm.verdict == "ko_unschedulable"]
    cmd("DecideUnschedMedianMin", fmt(un.decide_all_s.median() / 60))
    cmd("CappedTupleShare", f"{100 * tm.n_capped.sum() / tm.n_candidates.sum():.1f}\\%")
    # The tuples per flowset (RQ1): the reports' count, which depends on the
    # inputs only (the logs list as many in a full sweep).
    cmd("MedianTuples", f"{ft.n_candidates_total.median():,.0f}".replace(",", "\\,"))
    cmd("MaxTuples", f"{ft.n_candidates_total.max():,.0f}".replace(",", "\\,"))
    every = (ft.n_candidates_total if tuples_all is None else tuples_all).dropna()
    cmd("MedianTuplesAll", f"{every.median():,.0f}".replace(",", "\\,") if len(every) else "--")
    for c, k in [("n_jobs", "Jobs"), ("n_variables", "Binaries"), ("n_constraints", "Constraints")]:
        s = ilp_ok[c].dropna()
        cmd(f"Median{k}", f"{s.median():,.0f}".replace(",", "\\,"))
        cmd(f"Max{k}", f"{s.max():,.0f}".replace(",", "\\,"))
    if rq4 is not None:
        rq4_numbers(rq4, cmd, fmt)
    out.mkdir(parents=True, exist_ok=True)
    (out / "numbers_results.tex").write_text("\n".join(L) + "\n")
    print(f"  wrote {out / 'numbers_results.tex'} ({len(L) - 2} macros)")


# --------------------------------------------------------------- partial ----
# --partial: the figures the paper includes, from a sweep still running. It
# reads nothing committed and writes only to PARTIAL (see the module's
# docstring); the figures are drawn by the functions above, on the budgets and
# flowsets finished so far.

PARTIAL = HERE / "partial"
PARTIAL_FIGURES = ["table_rq2", "fig_rq2_verdicts", "fig_rq2_scaling", "fig_rq3_residual_gain",
                   "fig_rq3_rb_usage", "fig_rq4_hyperperiod", "numbers_results"]
RQ4_OUT = ROOT / "output" / "rq4"
FLOWSETS = ROOT / "output" / "flowsets"
MCS = ROOT / "input" / "mcs_configurations.json"


def partial_finished(rb: int, sweep: Path = SWEEP) -> set:
    """The flowsets of budget rb finished and complete here. A status file
    arrives before its pair's files, so it is not enough: copy lists a pair in
    .parallel/copied once all its files are here; without that list (pull),
    status ok and an ILP report."""
    par = sweep / f"rb{rb}" / ".parallel"
    if not (par / "status").is_dir():
        return set()
    ok = {f.name for f in (par / "status").glob("flowset_*") if f.read_text().strip() == "ok"}
    if (par / "copied").is_file():
        return ok & set((par / "copied").read_text().split())
    return {f for f in ok
            if (sweep / f"rb{rb}" / "reports" / "ilp" / f / "offline_phase_report.json").exists()}


def partial_collect(budgets: list, csv_dir: Path, sweep: Path = SWEEP) -> None:
    """collect_verdicts.py per budget into csv_dir, stacked into
    output_sweep.csv with an rb column, as sweep_experiment.sh --collect does."""
    tables = []
    for rb in budgets:
        out = csv_dir / f"output_rb{rb}.csv"
        subprocess.run([sys.executable, str(HERE / "collect_verdicts.py"),
                        "--reports", str(sweep / f"rb{rb}" / "reports"),
                        "--flowsets", str(FLOWSETS), "--mcs", str(MCS), "--out", str(out)],
                       check=True, stdout=subprocess.DEVNULL)
        tables.append(pd.read_csv(out).assign(rb=rb))
    d = pd.concat(tables, ignore_index=True)
    d[["rb"] + [c for c in d.columns if c != "rb"]].to_csv(csv_dir / "output_sweep.csv", index=False)


def partial_rq4(csv_dir: Path) -> pd.DataFrame | None:
    """rq4_hyperperiod.py collect into csv_dir, if output/rq4/ holds results."""
    if not any(RQ4_OUT.glob("flowset_*/H*.result.json")):
        return None
    csv = csv_dir / "rq4_hyperperiod.csv"
    subprocess.run([sys.executable, str(HERE / "rq4_hyperperiod.py"), "--out", str(RQ4_OUT),
                    "collect", "--csv", str(csv)], check=True, stdout=subprocess.DEVNULL)
    return pd.read_csv(csv)


def partial(a) -> int:
    """--partial: PARTIAL/{csv,figures,README.md} from the finished pairs."""
    csv_dir, a.outdir = PARTIAL / "csv", PARTIAL / "figures"
    if a.copy and subprocess.run([str(ROOT / "scripts" / "remote_e2e_xp.sh"), "copy"]).returncode:
        print("warning: the copy did not complete; drawing from what output/sweep/ holds",
              file=sys.stderr)

    done = {rb: partial_finished(rb) for rb in BUDGETS}
    present = [rb for rb in BUDGETS if done[rb]]
    drawn = [rb for rb in present if len(done[rb]) >= a.min_flowsets]
    total = len(list(FLOWSETS.glob("flowset_*.json")))
    if not present:
        sys.exit(f"no finished pair under {SWEEP}: run scripts/remote_e2e_xp.sh copy first")
    # Checked before anything is deleted, so that a refused run keeps the last one.
    if a.common_size is not None:
        if drawn != BUDGETS:
            sys.exit(f"--common-size: needs every budget drawn, has {', '.join(map(str, drawn))}")
        have = len(set.intersection(*(done[rb] for rb in drawn)))
        if have < a.common_size:
            sys.exit(f"--common-size {a.common_size}: only {have} flowsets are finished at every budget")
    if a.flowsets is not None:
        if drawn != BUDGETS:
            sys.exit(f"--flowsets: needs every budget drawn, has {', '.join(map(str, drawn))}")
        listed = {l.strip() for l in a.flowsets.read_text().splitlines() if l.strip()}
        missing = sorted(listed - set.intersection(*(done[rb] for rb in drawn)))
        if missing:
            sys.exit(f"--flowsets: {len(missing)} of the {len(listed)} listed are not finished at "
                     f"every budget here ({', '.join(missing[:5])}{', ...' if len(missing) > 5 else ''})")
    # Start from nothing, so that no figure or CSV outlives the data it came from.
    for folder in (csv_dir, a.outdir):
        folder.mkdir(parents=True, exist_ok=True)
        for f in folder.iterdir():
            shutil.rmtree(f) if f.is_dir() else f.unlink()

    partial_collect(present, csv_dir)
    # Its "rows kept" lines are about merging into the committed CSV, which
    # does not happen here (and 5gtps_harq is not part of this sweep).
    with contextlib.redirect_stdout(io.StringIO()):
        cfg = derive_baseline_configs(present)
    cfg.to_csv(csv_dir / "derived_baseline_configs.csv", index=False)
    d = load(csv_dir, csv_dir / "derived_baseline_configs.csv")
    # Only the pairs complete here (a pair being copied may have some files).
    d = d[[fs in done[rb] for rb, fs in zip(d.rb, d.flowset)]]
    pe.style("serif", a.fontsize)
    figures = {}

    # RQ2 (a): the main budget's verdicts, on all its finished flowsets; from
    # the verdict run (--verdicts) when given. Only this figure reads it: its
    # ILP rows are verdicts only (collect_verdicts.py: verdict_only, no peak).
    if a.verdicts:
        vdone = partial_finished(MAIN_BUDGET, a.verdicts)
        vdir = csv_dir / "verdicts"
        vdir.mkdir()
        partial_collect([MAIN_BUDGET], vdir, a.verdicts)
        with contextlib.redirect_stdout(io.StringIO()):
            derive_baseline_configs([MAIN_BUDGET], a.verdicts).to_csv(
                vdir / "derived_baseline_configs.csv", index=False)
        dv = load(vdir, vdir / "derived_baseline_configs.csv")
        dv = dv[dv.flowset.isin(vdone)]
        if len(vdone) >= a.min_flowsets:
            fig_verdicts(dv, a)
        figures["fig_rq2_verdicts"] = (
            f"rb{MAIN_BUDGET} from the verdict run ({a.verdicts}), {len(vdone)} of {total} flowsets: "
            "the ILP stops at its first table (the same verdict as a full sweep), every other "
            "method runs in full" + ("" if len(vdone) >= a.min_flowsets else
                                     f"; pending: fewer than {a.min_flowsets}"))
    elif MAIN_BUDGET in drawn:
        fig_verdicts(d, a)
        figures["fig_rq2_verdicts"] = f"rb{MAIN_BUDGET}, {len(done[MAIN_BUDGET])} flowsets"
    else:
        figures["fig_rq2_verdicts"] = (f"pending: rb{MAIN_BUDGET} has {len(done[MAIN_BUDGET])} "
                                       f"finished flowsets of the {a.min_flowsets} needed")

    # RQ2 (b) and RQ3 compare budgets: on the flowsets finished at all of them.
    common = set.intersection(*(done[rb] for rb in drawn)) if drawn else set()
    which = f"{len(common)} flowsets"
    if a.common_size is not None:
        # The lowest-numbered: a set the files alone determine (the author's
        # choice, 26 October 2026, for a paper whose count is a multiple of 100).
        which = f"{a.common_size} lowest-numbered of the {len(common)} flowsets"
        common = set(sorted(common)[:a.common_size])
    if a.flowsets is not None:
        # An explicit set, checked above.
        which = f"{len(listed)} flowsets of {a.flowsets.name}, of the {len(common)} flowsets"
        common = listed
    # The flowsets themselves, so that a set can be rebuilt or copied
    (csv_dir / "flowsets.txt").write_text("".join(f"{f}\n" for f in sorted(common)))
    x = d[d.rb.isin(drawn) & d.flowset.isin(common)]
    where = f"rb{', rb'.join(map(str, drawn))}, on the {which} finished at each"
    # fig_rq2_scaling counts verdicts only, so it may take the main budget from
    # the verdict run when the sweep has not drawn it; the RQ3 figures below,
    # which read peaks, never do.
    s_drawn, s_x, s_where = drawn, x, where
    if a.verdicts and MAIN_BUDGET not in drawn and len(vdone) >= a.min_flowsets:
        s_drawn = drawn + [MAIN_BUDGET]
        s_common = set.intersection(*(done[rb] for rb in drawn), vdone) if drawn else vdone
        s_x = pd.concat([d[d.rb.isin(drawn) & d.flowset.isin(s_common)],
                         dv[dv.flowset.isin(s_common)]], ignore_index=True)
        s_where = (f"rb{', rb'.join(map(str, drawn))} and rb{MAIN_BUDGET} (verdict run), on the "
                   f"{len(s_common)} flowsets finished at each")
    if len(s_drawn) >= 2:
        fig_scaling(s_x, a, s_drawn)
        figures["fig_rq2_scaling"] = s_where
        # No note under the table: the paper gives the flowset count in the text.
        table_rq2(s_x, a.outdir, s_drawn)
        figures["table_rq2"] = s_where
    else:
        figures["fig_rq2_scaling"] = figures["table_rq2"] = (
            f"pending: needs two budgets with {a.min_flowsets} finished flowsets, has {len(s_drawn)}")
    g = paired_gain(x, "rb_peak", drawn) if drawn else pd.DataFrame()
    if not g.empty and (g.method.isin(["must", "qospf"]) & (g.n >= MIN_N)).any():
        fig_residual(x, a, drawn)
        figures["fig_rq3_residual_gain"] = where
    else:
        figures["fig_rq3_residual_gain"] = (f"pending: no budget drawn yet has {MIN_N} flowsets "
                                            "that the ILP and MUST or QoS-PF both schedule")
    u = usage(x, drawn) if drawn else pd.DataFrame(columns=["rb", "n"])
    n = u.groupby("rb").n.first()
    if (n >= USAGE_MIN_N).any():
        fig_usage(x, a, drawn)
        figures["fig_rq3_rb_usage"] = (f"{where}; at each budget, the ones all five methods "
                                       "schedule: " + ", ".join(f"rb{rb} {k}" for rb, k in n.items())
                                       + " (a budget with none is left out)")
    else:
        figures["fig_rq3_rb_usage"] = ("pending: no budget drawn yet has a flowset that "
                                       "all five methods schedule ("
                                       + ", ".join(f"rb{rb} {k}" for rb, k in n.items()) + ")")

    # RQ4: the hyperperiod experiment, which runs after the sweep.
    r = partial_rq4(csv_dir)
    if r is not None:
        fig_rq4_hyperperiod(r, a)
        figures["fig_rq4_hyperperiod"] = f"{len(r)} models solved of the RQ4 experiment"
    else:
        figures["fig_rq4_hyperperiod"] = f"pending: no RQ4 result under {RQ4_OUT.relative_to(ROOT)}"

    # The macros, on the same flowsets as the figures comparing budgets (all
    # four needed: the text quotes every budget). The ILP timing is the main
    # budget's logs on those flowsets; with --verdicts, the first-table times
    # and the tuple counts are the verdict run's, on the same flowsets (it
    # stops at the first table, so it has no per-tuple times of a full sweep).
    if drawn == BUDGETS:
        t = derive_ilp_timing()
        t = t[t.flowset.isin(common)]
        first, src = None, f"--partial: the {which} finished at every budget"
        tuples_all = pd.Series(dtype=float)       # MedianTuplesAll "--" unless every flowset is known
        if a.verdicts:
            first = derive_ilp_timing(MAIN_BUDGET, a.verdicts).merge(
                dv[dv.method == "ilp"][["flowset", "verdict", "n_candidates_total"]], on="flowset")
            first = first[first.flowset.isin(vdone & common)]
            src += (f"; first tables and tuple counts from the verdict run ({a.verdicts}), "
                    f"{len(first)} flowsets")
            if len(vdone) == total:
                tuples_all = dv[dv.method == "ilp"].n_candidates_total
                src += f"; MedianTuplesAll from its {total}"
        write_numbers(x, paired_gain(x, "rb_peak", drawn), paired_gain(x, "rb_window_mean_peak", drawn),
                      t, a.outdir, r, first, src, tuples_all)
        figures["numbers_results"] = src[len("--partial: "):]
    else:
        figures["numbers_results"] = (f"pending: needs every budget with {a.min_flowsets} "
                                      "finished flowsets")

    newest = max((f.stat().st_mtime for rb in present
                  for f in (SWEEP / f"rb{rb}" / ".parallel" / "status").glob("flowset_*")), default=0)
    lines = [
        "# Partial results (generated by plot_paper.py --partial -- do not edit)",
        "",
        f"Generated {datetime.datetime.now():%Y-%m-%d %H:%M} from output/sweep/, whose newest "
        f"status file is from {datetime.datetime.fromtimestamp(newest):%Y-%m-%d %H:%M}. "
        "A preview of the paper's figures while the sweep runs, not its results: "
        "the pairs that finish first are the quick ones, so the shares below are "
        "not estimates of the final ones. When the sweep is complete, regenerate "
        "the figures with plot_paper.py (RUN_EXPERIMENTS.md, step 4) and delete this folder.",
        "",
        "The last two columns measure that bias for the figures comparing budgets: "
        f"{NAME}'s schedulable share on every finished flowset of the budget, and on "
        "the flowsets finished at every budget drawn, which those figures use. The "
        "further apart they are, the less those figures look like the final ones.",
        "",
        f"| Budget | Finished flowsets | Drawn | {NAME} schedulable, all finished | "
        f"{NAME} schedulable, common set |",
        "| :--- | ---: | :--- | ---: | ---: |",
    ]
    ok = d[d.method == "ilp"].assign(ok=lambda t: t.verdict_model == "ok")
    for rb in BUDGETS:
        mine = ok[ok.rb == rb]
        share = (lambda t: f"{100 * t.ok.mean():.0f}% of {len(t)}" if len(t) else "--")
        lines.append(f"| rb{rb} | {len(done[rb])} of {total} | "
                     f"{'yes' if rb in drawn else f'no (fewer than {a.min_flowsets})'} | "
                     f"{share(mine)} | "
                     f"{share(mine[mine.flowset.isin(common)]) if rb in drawn else '--'} |")
    lines += ["", "| Figure | Drawn from |", "| :--- | :--- |"]
    lines += [f"| `{f}` | {figures[f]} |" for f in PARTIAL_FIGURES]
    lines += ["", "Verdict CSVs, the baselines' configurations, any RQ4 results and the "
              "flowsets the figures comparing budgets and the macros use (flowsets.txt): `csv/`. "
              "Figures: `figures/`, under the names the paper uses."]
    (PARTIAL / "README.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[8:]))
    print(f"wrote {PARTIAL}/: README.md, csv/, figures/")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--derive", action="store_true",
                    help="extract the derived CSVs from output/sweep/ and exit")
    ap.add_argument("--partial", action="store_true",
                    help="the paper's figures from the pairs finished so far, into analysis/partial/")
    ap.add_argument("--copy", action="store_true",
                    help="with --partial: first copy the newly finished pairs here "
                         "(scripts/remote_e2e_xp.sh copy)")
    ap.add_argument("--min-flowsets", type=int,
                    help="with --partial: finished flowsets a budget needs to be drawn (default 100)")
    ap.add_argument("--verdicts", type=Path,
                    help="with --partial: a sweep root whose rb135 the ILP ran in verdict mode "
                         "(--stop-at-first), for fig_rq2_verdicts only")
    ap.add_argument("--common-size", type=int, metavar="N",
                    help="with --partial: draw only the N lowest-numbered of the flowsets "
                         "finished at every budget (all four must be drawn)")
    ap.add_argument("--flowsets", type=Path, metavar="FILE",
                    help="with --partial: draw exactly the flowsets FILE lists, one per line, each "
                         "finished at every budget (all four must be drawn); not with --common-size")
    ap.add_argument("--outdir", type=Path, help="figures and macros (default analysis/figures/paper/)")
    ap.add_argument("--width", type=float, default=1.7, help="panel width, inches")
    ap.add_argument("--height", type=float, default=1.45, help="panel height, inches")
    ap.add_argument("--fontsize", type=float, default=7.0)
    ap.add_argument("--formats", nargs="+", default=["pdf", "png"])
    args = ap.parse_args()

    if args.partial:
        if args.derive or args.outdir:
            ap.error("--partial derives what it needs itself and writes to analysis/partial/: "
                     "no --derive or --outdir with it")
        if args.common_size is not None and args.flowsets is not None:
            ap.error("--common-size or --flowsets, not both")
        args.min_flowsets = 100 if args.min_flowsets is None else args.min_flowsets
        return partial(args)
    if (args.copy or args.min_flowsets is not None or args.verdicts or args.common_size is not None
            or args.flowsets is not None):
        ap.error("--copy, --min-flowsets, --verdicts, --common-size and --flowsets go with --partial")
    args.outdir = args.outdir or HERE / "figures" / "paper"

    if args.derive:
        merge_configs(derive_baseline_configs())
        t = derive_ilp_timing()
        if t.empty:
            print(f"no ILP logs under {SWEEP / f'rb{MAIN_BUDGET}' / 'logs' / 'ilp'}: "
                  f"{TIMING_CSV.name} kept")
        else:
            t.to_csv(TIMING_CSV, index=False)
            print(f"wrote {TIMING_CSV.name}: {len(t)} rows")
        return 0

    d = load()
    t = pd.read_csv(TIMING_CSV)
    pe.style("serif", args.fontsize)
    fig_verdicts(d, args)
    fig_scaling(d, args)
    table_rq2(d, args.outdir)
    g = fig_residual(d, args)
    fig_usage(d, args)
    gw = paired_gain(d, "rb_window_mean_peak")
    fig_time(t, d[(d.rb == MAIN_BUDGET) & (d.method == "ilp")], args)
    rq4 = pd.read_csv(RQ4_CSV) if RQ4_CSV.exists() else None
    if rq4 is not None:
        fig_rq4_hyperperiod(rq4, args)
    write_numbers(d, g, gw, t, args.outdir, rq4)
    return 0


if __name__ == "__main__":
    sys.exit(main())
