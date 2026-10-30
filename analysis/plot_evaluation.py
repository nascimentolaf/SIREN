#!/usr/bin/env python3
"""Publication figures for the performance-evaluation section.

Reads the two CSVs this folder already produces (in csv/) --

    output.csv                    one row per (flow set, method): the verdict
                                  table described in README.md
    results_per_configuration.csv one row per (flow set, candidate
                                  configuration, method)

-- and writes vector PDFs (plus PNG previews) ready to \\includegraphics.

    python3 plot_evaluation.py                  # every figure into ./figures
    python3 plot_evaluation.py --list           # what it can draw
    python3 plot_evaluation.py --only headroom  # just one
    python3 plot_evaluation.py --width double   # 7.16in, two-column span
    python3 plot_evaluation.py --font sans      # Helvetica-alike instead of Times

Every figure is standalone: no shared axis, no panel that only makes sense
next to another one, so they can be dropped into the paper in any order and
any subset. `--only numbers` skips the drawing and just writes numbers.tex,
the \\newcommand macros for the numbers quoted in the prose, so the text and
the figures can never drift apart.

Design notes (why it looks the way it does)
-------------------------------------------
* Method colors are fixed per method and never reassigned by rank, so "the
  blue one" means the ILP in every figure. The four hues were checked for
  colorblind separation over *all* pairs, not just adjacent ones (worst pair
  dE 9.2 deutan / 9.6 tritan, normal-vision 16.3), because in the ECDFs every
  series is on screen at once. 5G-TPS is deliberately not one of the hues: it
  is flat zero everywhere, so it is drawn as a labelled gray reference rather
  than competing for a color.
* Line style and marker repeat the identity that color carries, so the
  figures survive grayscale printing and photocopying.
* Verdicts use status colors, not the method colors: green = ok, red = KO,
  amber = undecided, gray = proved impossible. "Declined to place" is the
  same red as "missed a deadline" with a 45 hatch, because it is the same
  outcome by a different route -- and because amber next to orange is the one
  pair a reader with normal vision struggles with (dE 13.6).
* Serif by default to match an IEEE/ACM body font; `--font sans` if the
  paper is sans.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

HERE = Path(__file__).resolve().parent

# --------------------------------------------------------------------------
# Identity: order, labels, colors, and the secondary encodings that repeat
# them for grayscale. METHODS is also the plotting order everywhere.
# --------------------------------------------------------------------------
# The proposed method first, then the simulated policies, then 5G-TPS, which is
# a reference line rather than a series.
METHODS = ["ilp", "qospf", "pf", "rr", "edf", "must", "5gtps"]
LABEL = {
    "ilp": "ILP (proposed)",
    "qospf": "QoS-PF",
    "pf": "PF",
    "rr": "RR",
    "edf": "EDF",
    "must": "MUST",
    "5gtps": "5G-TPS",
    # The 5gtps_harq variant: 5G-TPS with a HARQ process per TB job. Styled
    # here for plot_paper.py, which draws it only if its PLOTTED lists it;
    # not in METHODS, so these figures leave it out.
    "5gtps_harq": "5G-TPS ($M\\geq$#TBs)",
}
# One row of the paper's catalogue table per reliability target. Keyed by the
# E_i of input/app_database.json; every application sharing a target belongs to
# the same class regardless of its name.
RELIABILITY_CLASS = {
    1e-3: "Monitoring and tracking",
    1e-4: "Discrete automation",
    1e-5: "Motion and process control",
    1e-6: "Precision and safety",
    1e-8: "High reliability",
}

COLOR = {
    "ilp": "#2a78d6",     # blue
    "qospf": "#eb6834",   # orange
    "pf": "#4a3aa7",      # violet
    "rr": "#1baf7a",      # aqua
    "edf": "#b5379b",     # magenta
    "must": "#7f9c1a",    # olive
    "5gtps": "#6b6a66",   # gray: a reference line, not a series
    "5gtps_harq": "#8f4c0e",  # brown: closest to orange, CVD dE 14.6, normal 19.6
}
DASH = {
    "ilp": (0, ()),
    "qospf": (0, (5, 1.4)),
    "pf": (0, (1.6, 1.4)),
    "rr": (0, (5, 1.3, 1.3, 1.3)),
    "edf": (0, (4, 1.2, 1.2, 1.2, 1.2, 1.2)),
    "must": (0, (1.2, 1.2, 4, 1.2)),
    "5gtps": (0, (3, 2)),
    "5gtps_harq": (0, (2.4, 1.2, 2.4, 3)),
}
MARKER = {"ilp": "o", "qospf": "s", "pf": "^", "rr": "D",
          "edf": "v", "must": "P", "5gtps": None, "5gtps_harq": "X"}

# Verdict encoding: state, not identity.
VERDICTS = ["ok", "timeout", "ko_unschedulable", "ko_deadline_miss", "ko_unplaced"]
VERDICT_LABEL = {
    "ok": "schedulable",
    "timeout": "undecided (budget)",
    "ko_unschedulable": "proved unschedulable",
    "ko_deadline_miss": "deadline missed",
    "ko_unplaced": "flow not placed",
}
VERDICT_COLOR = {
    "ok": "#0ca30c",
    "timeout": "#fab219",
    "ko_unschedulable": "#6b6a66",
    "ko_deadline_miss": "#d03b3b",
    "ko_unplaced": "#d03b3b",
}
VERDICT_HATCH = {"ko_unplaced": "////"}

# Chrome. One shade off the surface, solid hairlines, never dashed.
INK = "#0b0b0b"
INK_SOFT = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
SURFACE = "#ffffff"

# Ordered magnitude (0..3 late packets) -- one hue, light to dark.
BLUE_RAMP = ["#86b6ef", "#5598e7", "#2a78d6", "#184f95"]

# Packet error rate, loosest to tightest. Ordered data, so one hue light to
# dark rather than five unrelated colors: the ramp is the reading order.
PER_RAMP = ["#aed0f7", "#78acee", "#3d86dc", "#22619f", "#0d2f57"]

TTI_MS = 0.25  # scripts/parameters.sh: TTI = 0.00025 s. Override with --tti-ms.

WIDTHS = {"single": 3.5, "double": 7.16}  # IEEE column and full-width, inches


def style(font: str, base: float) -> None:
    serif = font == "serif"
    family = "serif" if serif else "sans-serif"
    faces = (
        ["Nimbus Roman", "Liberation Serif", "Times New Roman", "DejaVu Serif"]
        if serif
        else ["Nimbus Sans", "Liberation Sans", "Helvetica", "DejaVu Sans"]
    )
    mpl.rcParams.update(
        {
            "font.family": family,
            f"font.{family}": faces,
            "mathtext.fontset": "stix" if serif else "dejavusans",
            "font.size": base,
            "axes.titlesize": base + 0.5,
            "axes.labelsize": base,
            "xtick.labelsize": base - 0.5,
            "ytick.labelsize": base - 0.5,
            "legend.fontsize": base - 0.5,
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.edgecolor": "#c3c2b7",
            "axes.linewidth": 0.6,
            "axes.labelcolor": INK,
            "axes.titlecolor": INK,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "axes.axisbelow": True,
            "grid.color": GRID,
            "grid.linewidth": 0.5,
            "grid.linestyle": "-",
            "xtick.color": INK_MUTED,
            "ytick.color": INK_MUTED,
            "xtick.labelcolor": INK_SOFT,
            "ytick.labelcolor": INK_SOFT,
            "xtick.direction": "out",
            "ytick.direction": "out",
            "xtick.major.size": 2.5,
            "ytick.major.size": 2.5,
            "xtick.major.width": 0.6,
            "ytick.major.width": 0.6,
            "legend.frameon": False,
            "legend.handlelength": 1.9,
            "legend.handletextpad": 0.6,
            "legend.columnspacing": 1.2,
            "legend.labelspacing": 0.35,
            "lines.linewidth": 1.3,
            "lines.markersize": 3.2,
            "patch.linewidth": 0.8,
            "pdf.fonttype": 42,  # embed real glyphs, not Type-3
            "ps.fonttype": 42,
            "figure.constrained_layout.use": True,
            "figure.constrained_layout.h_pad": 0.02,
            "figure.constrained_layout.w_pad": 0.02,
        }
    )


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
def load_verdicts(path: Path) -> pd.DataFrame:
    d = pd.read_csv(path)
    missing = set(METHODS) - set(d.method.unique())
    if missing:
        print(f"note: {path.name} has no rows for {sorted(missing)}", file=sys.stderr)
    return d


def load_coverage(path: Path, cache_dir: Path, refresh: bool) -> pd.DataFrame:
    """Per (flow set, method): how many candidate configurations work.

    The per-configuration CSV is ~120 MB, so the 5x1000 summary it boils down
    to is cached next to the figures; delete it or pass --refresh to redo it.
    """
    cache = cache_dir / "coverage.csv"
    if cache.exists() and not refresh:
        return pd.read_csv(cache)
    if not path.exists():
        raise SystemExit(f"{path} not found (needed for configuration coverage)")
    print(f"reading {path.name} (~120 MB, once -- result cached)", file=sys.stderr)
    chunks = []
    for chunk in pd.read_csv(
        path, usecols=["flowset", "method", "verdict"], chunksize=400_000
    ):
        chunk["ok"] = chunk.verdict == "ok"
        chunks.append(chunk.groupby(["flowset", "method"], observed=True).ok.agg(["sum", "count"]))
    cov = (
        pd.concat(chunks)
        .groupby(level=[0, 1])
        .sum()
        .rename(columns={"sum": "n_ok", "count": "n_tested"})
        .reset_index()
    )
    cov["coverage"] = cov.n_ok / cov.n_tested
    cache.parent.mkdir(parents=True, exist_ok=True)
    cov.to_csv(cache, index=False)
    return cov


def load_candidates(reports: Path) -> pd.DataFrame:
    """One row per candidate configuration the planner emits, per flow.

    Read from the planner reports rather than the per-configuration CSV: that
    CSV records a combo index and a verdict, not what the configuration *is*,
    and the point here is what the choices look like, not how they scored. The
    whole tree is ~20k rows and parses in a fraction of a second, so unlike the
    coverage summary it is not worth caching.
    """
    ilp = reports / "ilp"
    if not ilp.is_dir():
        raise SystemExit(f"{ilp} not found (needed for the configuration space)")
    rows = []
    for path in sorted(ilp.glob("*/planner_report.json")):
        flowset = path.parent.name
        for app in json.loads(path.read_text()).get("applications", []):
            # The catalogue groups applications by reliability target, not by
            # name: every application carrying the same E_i is one row of the
            # paper's catalogue table. Deriving the class from the target keeps
            # that grouping stable when applications are renamed, which the
            # old "leading letters of MT4" rule did not.
            cls = RELIABILITY_CLASS.get(app.get("error"), "other")
            for seg in app.get("segmentation_options", []):
                for mbc in seg.get("mbc_combinations", []):
                    rows.append({
                        "flowset": flowset, "app": app["app"], "cls": cls,
                        "per": app.get("error"), "deadline_ms": app.get("deadline"),
                        "segments": seg["num_segments"], "tbs": seg["tbs"],
                        "m": mbc["m"], "attempts": mbc["a"], "rbs": mbc["b"],
                        "wctt_ms": mbc["c"],
                        # RB-TTIs one period of this flow costs the grid.
                        "cost": seg["num_segments"] * mbc["b"] * mbc["a"],
                    })
    if not rows:
        raise SystemExit(f"No planner reports under {ilp}")
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def ecdf(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    x = np.sort(np.asarray(values, dtype=float))
    y = np.arange(1, x.size + 1) / x.size
    return x, y


def survival_steps(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """P(value > x), as step coordinates starting at 1.0."""
    x, y = ecdf(values)
    return np.concatenate([[0.0], x]), np.concatenate([[1.0], 1.0 - y])


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def thou(x: float) -> str:
    """12345 -> 12\\,345, the thin space LaTeX groups digits with."""
    return f"{x:,.0f}".replace(",", "\\,")


def sci(x: float) -> str:
    """1e-06 -> $10^{-6}$, so the reliability tiers read as magnitudes."""
    return f"$10^{{{int(round(np.log10(x)))}}}$"


def annotate(ax, text: str, loc="lower right", pad=0.5) -> None:
    """A quiet note in the corner -- the caption's job, done in the figure."""
    at = ax.text(
        0.985 if "right" in loc else 0.015,
        0.03 if "lower" in loc else 0.97,
        text,
        transform=ax.transAxes,
        ha="right" if "right" in loc else "left",
        va="bottom" if "lower" in loc else "top",
        fontsize=mpl.rcParams["font.size"] - 1.2,
        color=INK_SOFT,
        linespacing=1.35,
    )
    at.set_bbox(dict(facecolor=SURFACE, edgecolor="none", alpha=0.85, pad=pad))


# A figure that explains itself inside its own frame spends ink on prose rather
# than on data, and that prose is redundant the moment the figure lands in a
# document that captions it. So the note travels beside the figure as
# <name>.note.txt instead, and --embed-notes puts it back under the axes when
# the figure has to stand alone. Figures are drawn one at a time, so a single
# slot is enough; save() consumes it.
PENDING: dict[str, str] = {}


def footnote(fig, text: str) -> None:
    """The note this figure would have carried under its axes.

    Held rather than drawn -- save() writes it to <name>.note.txt, or draws it
    below the figure under --embed-notes.
    """
    PENDING["note"] = text


def simulated(d: pd.DataFrame, cols: tuple[str, ...] = ("worst_overrun_tti",)) -> list[str]:
    """Methods whose verdict comes from running a dispatch table, in METHODS order.

    Derived from the data rather than listed: ilp decides by proof and 5G-TPS
    declines flows outright, so neither leaves a run to measure, while any
    baseline added to the sweep shows up here without an edit. `cols` is what
    the caller is about to plot -- a method that has no values in it is not in
    the group for that figure.
    """
    have = set(d.method.unique())
    return [m for m in METHODS
            if m in have and all(c in d.columns and d.loc[d.method == m, c].notna().any()
                                 for c in cols)]


def require(d: pd.DataFrame, cols: list[str], what: str) -> bool:
    """A figure with no data to draw says so and stands aside; one missing
    column must not take the other five figures down with it."""
    missing = [c for c in cols if c not in d.columns or d[c].isna().all()]
    if missing:
        print(f"  skipped: {what} needs {', '.join(missing)}, "
              "absent or empty in this data set", file=sys.stderr)
        return False
    return True


def panel_tag(ax, tag: str) -> None:
    ax.annotate(tag, xy=(0, 1), xycoords="axes fraction", xytext=(-2, 6),
                textcoords="offset points", ha="right", va="bottom", color=INK,
                fontsize=mpl.rcParams["font.size"], fontweight="bold",
                annotation_clip=False)


def rel(path: Path) -> Path:
    return path.relative_to(HERE) if path.is_relative_to(HERE) else path


def save(fig, name: str, args) -> bool:
    note = PENDING.pop("note", "")
    caption = PENDING.pop("caption", "")
    if note and args.embed_notes:
        fig.supxlabel(note, fontsize=mpl.rcParams["font.size"] - 1.2, color=INK_SOFT,
                      ha="left", x=0.0, linespacing=1.3)
    args.outdir.mkdir(parents=True, exist_ok=True)
    for ext in args.formats:
        out = args.outdir / f"{name}.{ext}"
        fig.savefig(out, dpi=args.dpi if ext == "png" else None, bbox_inches="tight",
                    pad_inches=0.02)
        print(f"  wrote {rel(out)}")
    plt.close(fig)

    # Caption first, then the note: that is the order they are read in, and it
    # lets the file be pasted into a document as-is. The line breaks in a note
    # are there to fit a 3.5 in column, so they are undone here -- a text file
    # has no column to fit, and the document it is pasted into rewraps anyway.
    if caption or note:
        out = args.outdir / f"{name}.note.txt"
        paras = [" ".join(t.split()) for t in (caption, note) if t.strip()]
        out.write_text("\n\n".join(paras) + "\n")
        print(f"  wrote {rel(out)}")
    return True


# --------------------------------------------------------------------------
# Figures
# --------------------------------------------------------------------------
def fig_schedulability(d: pd.DataFrame, args, ctx) -> None:
    """Headline: what fraction of flow sets each method delivers."""
    methods = ctx["methods"]
    if not require(d, ["method", "verdict"], "the verdict mix"):
        return
    tab = (
        pd.crosstab(d.method, d.verdict, normalize="index")
        .reindex(index=methods)
        .reindex(columns=VERDICTS, fill_value=0.0)
        .fillna(0.0)
    )
    narrow = args.width < 2.5  # one panel of a two-up figure
    row = 0.21 if narrow else 0.38
    pad = 0.55 if args.no_legend else (1.0 if narrow else 1.05)  # axis labels (+ legend)
    fig, ax = plt.subplots(figsize=(args.width, row * len(methods) + pad))
    y = np.arange(len(methods))[::-1]
    left = np.zeros(len(methods))
    for v in VERDICTS:
        w = tab[v].to_numpy()
        if not w.any():
            continue
        ax.barh(y, w, left=left, height=0.52, color=VERDICT_COLOR[v],
                hatch=VERDICT_HATCH.get(v), edgecolor=SURFACE, linewidth=0.9,
                label=VERDICT_LABEL[v], zorder=3)
        for yi, (wi, li) in zip(y, zip(w, left)):
            # The ok share is spelled out at the bar end; labelling it here too
            # would print the same number twice. Hatched fills take dark text.
            if wi >= 0.07 and v != "ok":
                ax.text(li + wi / 2, yi, f"{100 * wi:.0f}", ha="center", va="center",
                        color=INK if v in VERDICT_HATCH else SURFACE,
                        fontsize=mpl.rcParams["font.size"] - 1.2, zorder=4)
        left += w
    # The number that matters, once, at the end of each bar.
    for yi, m in zip(y, methods):
        ax.text(1.012, yi, pct(tab.loc[m, "ok"]), va="center", ha="left",
                color=INK, fontsize=mpl.rcParams["font.size"] - 0.5)
    ax.set_yticks(y, [LABEL[m] for m in methods])
    ax.get_yticklabels()[0].set_color(INK)
    ax.get_yticklabels()[0].set_fontweight("bold")
    ax.set_xlim(0, 1)
    ax.set_ylim(-0.62, len(methods) - 0.38)
    ax.set_xticks(np.arange(0, 1.01, 0.25), ["0", "25", "50", "75", "100"])
    ax.set_xlabel("flow sets (%)")
    ax.xaxis.grid(True)
    ax.yaxis.grid(False)
    ax.tick_params(axis="y", length=0)
    if not args.no_legend:  # else the caption names the colours
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.22 if not narrow else -0.3),
                  ncol=2 if narrow else 3, borderpad=0, columnspacing=0.8, handlelength=1.2)
    footnote(fig, f"n = {ctx['n_flowsets']} flow sets, the same set for every method.")
    return save(fig, "fig1_schedulability", args)


def fig_coverage(cov: pd.DataFrame, args, ctx) -> None:
    """Not "can it be scheduled" but "how many ways can it be scheduled"."""
    fig, ax = plt.subplots(figsize=(args.width, args.width * 0.68))
    piv = cov.pivot(index="flowset", columns="method", values="coverage")
    for m in METHODS:  # fixed order; absent methods are skipped below
        if m not in piv:
            continue
        x, y = survival_steps(piv[m].to_numpy())
        if piv[m].max() == 0:
            # 5G-TPS never schedules anything, so its curve is the x-axis. It
            # goes in the legend rather than as floating text, which would land
            # on whichever baseline curve is lowest.
            ax.plot([0, 1], [0, 0], color=COLOR[m], lw=1.1, ls=DASH[m], zorder=2,
                    label=f"{LABEL[m]} (none, in any flow set)")
            continue
        ax.step(x, y, where="post", color=COLOR[m], ls=DASH[m], label=LABEL[m], zorder=3)
        med = float(piv[m].median())
        if med > 0:  # one marker per series: the median flow set
            ax.plot([med], [0.5], marker=MARKER[m], color=COLOR[m], ms=4,
                    mec=SURFACE, mew=0.8, ls="none", zorder=4)
    ax.axhline(0.5, color=INK_MUTED, lw=0.5, zorder=1)
    ax.text(0.995, 0.505, "median flow set", ha="right", va="bottom",
            color=INK_MUTED, fontsize=mpl.rcParams["font.size"] - 1.4)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.02)
    ax.set_xticks(np.arange(0, 1.01, 0.25), ["0", "25", "50", "75", "100"])
    ax.set_yticks(np.arange(0, 1.01, 0.25), ["0", "25", "50", "75", "100"])
    ax.set_xlabel("configurations that schedule, per flow set (%)")
    ax.set_ylabel("flow sets above that share (%)")
    ax.legend(loc="upper right", ncol=1)
    footnote(fig, f"{ctx['n_configs']:,} candidate configurations over "
                  f"{ctx['n_flowsets']} flow sets.")
    return save(fig, "fig2_configuration_coverage", args)


def fig_agreement(d: pd.DataFrame, args, ctx) -> None:
    """The ILP as the oracle: what the baselines do where a schedule exists."""
    p = d.pivot(index="flowset", columns="method", values="verdict")
    if "ilp" not in p:
        print("  skipped: this figure compares the baselines against the ILP's verdicts,\n"
              "           and the data set has no ilp rows", file=sys.stderr)
        return
    base = [m for m in ctx["methods"] if m != "ilp"]
    proved = p[p.ilp == "ok"]
    undec = p[p.ilp == "timeout"]
    rec = [(proved[m] == "ok").mean() for m in base]
    und = [(undec[m] == "ok").mean() for m in base]

    fig, ax = plt.subplots(figsize=(args.width, args.width * 0.62))
    y = np.arange(len(base))[::-1]
    h = 0.34
    ax.barh(y + h / 2 + 0.02, rec, height=h, color=COLOR["ilp"], zorder=3,
            label=f"ILP proved a schedule exists (n={len(proved)})")
    ax.barh(y - h / 2 - 0.02, und, height=h, color=COLOR["pf"], zorder=3,
            label=f"ILP undecided at the budget (n={len(undec)})")
    for yi, v in zip(y + h / 2 + 0.02, rec):
        ax.text(v + 0.008, yi, pct(v), va="center", ha="left", color=INK,
                fontsize=mpl.rcParams["font.size"] - 1)
    for yi, v in zip(y - h / 2 - 0.02, und):
        ax.text(v + 0.008, yi, pct(v), va="center", ha="left", color=INK_SOFT,
                fontsize=mpl.rcParams["font.size"] - 1)
    ax.set_yticks(y, [LABEL[m] for m in base])
    ax.set_xlim(0, max(max(rec), max(und)) * 1.28 + 0.02)
    ax.xaxis.set_major_formatter(lambda v, _: f"{100 * v:.0f}")
    ax.set_xlabel("flow sets the baseline also schedules (%)")
    ax.xaxis.grid(True)
    ax.yaxis.grid(False)
    ax.tick_params(axis="y", length=0)
    ax.legend(loc="lower right", bbox_to_anchor=(1.0, -0.02))
    # Never assert soundness -- measure it, so a data set that does contradict
    # the proof says so instead of inheriting last run's happy sentence.
    impossible = p[p.ilp == "ko_unschedulable"]
    clashes = int(sum((impossible[m] == "ok").sum() for m in base if m in impossible))
    if not len(impossible):
        note = "No flow set was proved unschedulable in this data set."
    elif clashes == 0:
        note = (f"On the {len(impossible)} flow sets the ILP proved unschedulable,\n"
                "no baseline ever reported a schedule.")
    else:
        note = (f"On the {len(impossible)} flow sets the ILP proved unschedulable, a baseline\n"
                f"still reported a schedule for {clashes} of them: proof and simulation disagree.")
    footnote(fig, note)
    return save(fig, "fig3_ilp_agreement", args)


def fig_severity(d: pd.DataFrame, args, ctx) -> None:
    """A miss is not a coin flip: how late, and how many flows."""
    if not require(d, ["worst_overrun_tti", "n_deadline_misses"], "miss severity"):
        return
    sim = simulated(d, ("worst_overrun_tti", "n_deadline_misses"))
    if not sim:
        print("  skipped: no method with a simulated run in this data set", file=sys.stderr)
        return
    fig, axes = plt.subplots(2, 1, figsize=(args.width, args.width * 1.15),
                             height_ratios=[1.25, 1])
    ax = axes[0]
    for m in sim:
        v = d[d.method == m].worst_overrun_tti.dropna().to_numpy() * args.tti_ms
        x, y = survival_steps(v)
        ax.step(x, y, where="post", color=COLOR[m], ls=DASH[m], label=LABEL[m], zorder=3)
    ax.set_xlim(left=0)
    ax.set_ylim(0, 1.02)
    ax.set_yticks(np.arange(0, 1.01, 0.25), ["0", "25", "50", "75", "100"])
    panel_tag(ax, "(a)")
    ax.set_xlabel("worst lateness of a packet (ms)")
    ax.set_ylabel("flow sets later than that (%)")
    ax.legend(loc="upper right")

    ax = axes[1]
    counts = (
        pd.crosstab(d[d.method.isin(sim)].method, d.n_deadline_misses, normalize="index")
        .reindex(index=sim)
    )
    y = np.arange(len(sim))[::-1]
    left = np.zeros(len(sim))
    for j, col in enumerate(sorted(counts.columns)):
        w = counts[col].to_numpy()
        ax.barh(y, w, left=left, height=0.5, color=BLUE_RAMP[min(j, len(BLUE_RAMP) - 1)],
                edgecolor=SURFACE, linewidth=0.9, zorder=3, label=f"{int(col)}")
        for yi, (wi, li) in zip(y, zip(w, left)):
            if wi >= 0.08:
                ax.text(li + wi / 2, yi, f"{100 * wi:.0f}", ha="center", va="center",
                        color=SURFACE if j >= 2 else INK,
                        fontsize=mpl.rcParams["font.size"] - 1.2, zorder=4)
        left += w
    panel_tag(ax, "(b)")
    ax.set_yticks(y, [LABEL[m] for m in sim])
    ax.set_xlim(0, 1)
    ax.set_xticks(np.arange(0, 1.01, 0.25), ["0", "25", "50", "75", "100"])
    ax.set_xlabel("flow sets (%)")
    ax.xaxis.grid(True)
    ax.yaxis.grid(False)
    ax.tick_params(axis="y", length=0)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.32), ncol=4, borderpad=0,
              title=f"late flows, of {ctx['n_flows']} in the set", title_fontproperties={"size": mpl.rcParams["font.size"] - 0.5})
    footnote(fig, f"n = {ctx['n_flowsets']} flow sets. The ILP is absent: it proves a schedule\n"
                  "rather than running one, so it has no lateness to report.")
    return save(fig, "fig4_miss_severity", args)


def fig_ilp_cost(d: pd.DataFrame, args, ctx) -> None:
    """What the proof costs, and where the budget stops the search."""
    i = d[d.method == "ilp"].copy()
    if not require(d, ["n_candidates_total", "n_candidates_tested",
                       "solve_time_ms_median", "solve_time_ms_max"], "ILP cost"):
        return
    if i.empty or i.n_candidates_total.isna().all():
        print("  skipped: no ilp rows with solver columns in this data set", file=sys.stderr)
        return
    i["coverage"] = i.n_candidates_tested / i.n_candidates_total
    # --ilp-cost-panels time: solve-time CDF only, in the landscape aspect of
    # the other figures, for a side-by-side strip. Coverage is then stated in
    # the prose (it is 100% everywhere once every candidate is closed).
    time_only = args.ilp_cost_panels == "time"
    narrow = args.width < 2.5  # one panel of a two-up figure
    if time_only:
        fig, ax = plt.subplots(figsize=(args.width, args.width * (0.95 if narrow else 0.9)))
        axes = [ax]
    else:
        fig, axes = plt.subplots(2, 1, figsize=(args.width, args.width * 1.25))

    # A CDF over flow sets: for each flow set the ILP proved, the median and
    # the slowest solve time among its schedulable candidates; a point (x, y)
    # reads "y% of the flow sets have that time <= x s". Everything the
    # reader needs to decode it is on the axes, not in the caption.
    ax = axes[0]
    for key, color, lab in (("solve_time_ms_median", COLOR["ilp"], "median candidate"),
                            ("solve_time_ms_max", COLOR["qospf"], "slowest candidate")):
        v = i[key].dropna().to_numpy() / 1000.0
        x, y = ecdf(v)
        ax.step(x, y, where="post", color=color, label=lab,
                ls=(0, ()) if "median" in key else (0, (5, 1.4)), zorder=3)
    cap = ctx.get("candidate_cap_s")
    ax.set_xscale("log")
    ticks = [t for t in (1, 3, 10, 30, 100, 300) if not cap or t < cap]
    if cap:
        ticks.append(cap)
        ax.axvline(cap, color=INK_MUTED, lw=0.7, zorder=2)
        ax.text(cap, 1.03, "cap", ha="center", va="bottom", color=INK_SOFT,
                fontsize=mpl.rcParams["font.size"] - 1.2, clip_on=False)
        ax.set_xlim(right=cap * 1.15)
    ax.set_xticks(ticks, [f"{t:g}" for t in ticks])
    ax.xaxis.set_minor_locator(mpl.ticker.NullLocator())
    ax.set_ylim(0, 1.02)
    ax.set_yticks(np.arange(0, 1.01, 0.25), ["0", "25", "50", "75", "100"])
    if not time_only:
        panel_tag(ax, "(a)")
    ax.set_xlabel("ILP solve time $x$ (s)")
    ax.set_ylabel("flow sets with time $\\leq x$ (%)")
    ax.legend(loc="upper left", handlelength=1.4, borderaxespad=0.3,
              title="per flow set, over its\nschedulable candidates",
              title_fontproperties={"size": mpl.rcParams["font.size"] - 1.2},
              fontsize=mpl.rcParams["font.size"] - (1.0 if narrow else 0))
    if time_only:
        return save(fig, "fig5_ilp_cost", args)

    ax = axes[1]
    for v in ("ok", "ko_unschedulable", "timeout"):
        s = i[i.verdict == v]
        if s.empty:
            continue
        ax.scatter(s.n_candidates_total, s.coverage, s=4.5, lw=0.25,
                   facecolor=VERDICT_COLOR[v], edgecolor=SURFACE, alpha=0.7,
                   label=VERDICT_LABEL[v], zorder=3)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_ylim(top=1.6)
    panel_tag(ax, "(b)")
    ax.set_xlabel("candidate configurations the planner emits")
    ax.set_ylabel("share explored (%)")
    ax.yaxis.set_major_formatter(lambda v, _: f"{100 * v:g}")
    ax.legend(loc="lower left", ncol=1)
    footnote(fig, "A proof of unschedulability needs the whole space; a schedule is\n"
                  "usually found in the first few percent of it.")
    return save(fig, "fig5_ilp_cost", args)


def fig_headroom(d: pd.DataFrame, args, ctx) -> None:
    """Why the baselines fail: not capacity -- ordering.

    Every method with a measured table is drawn: the baselines' simulated
    runs, the ILP's proved table (its peak is what the schedule leaves the
    cell), and 5G-TPS's partial table, whose peak covers only the flows it
    placed and is labelled as such.
    """
    if not require(d, ["rb_peak", "rb_capacity"], "resource headroom"):
        return
    sim = simulated(d, ("rb_peak", "rb_capacity"))
    if not sim:
        print("  skipped: no method with a measured table in this data set", file=sys.stderr)
        return
    # A method that dropped flows in some run has a peak that undercounts
    # its load; the label says so rather than letting the bar pass as a run.
    partial = {m for m in sim if "n_flows_placed" in d
               and (d.loc[d.method == m, "n_flows_placed"] < d.loc[d.method == m, "n_flows"]).any()}
    label = {m: LABEL[m] + (" (placed flows)" if m in partial else "") for m in sim}
    fig, ax = plt.subplots(figsize=(args.width, 0.34 * len(sim) + 1.15))
    data = [(d[d.method == m].rb_peak / d[d.method == m].rb_capacity).dropna().to_numpy()
            for m in sim]
    y = np.arange(len(sim))[::-1]
    bp = ax.boxplot(data, positions=y, vert=False, widths=0.5, whis=(5, 95),
                    showfliers=False, patch_artist=True, zorder=3)
    for patch, m in zip(bp["boxes"], sim):
        patch.set(facecolor=COLOR[m], alpha=0.28, edgecolor=COLOR[m], linewidth=0.9)
    for key in ("whiskers", "caps"):
        for art in bp[key]:
            art.set(color=INK_MUTED, linewidth=0.7)
    for med, m in zip(bp["medians"], sim):
        med.set(color=COLOR[m], linewidth=1.6)
    for yi, m, v in zip(y, sim, data):
        ax.text(1.012, yi, f"med {100 * np.median(v):.0f}%", va="center", ha="left",
                color=INK_SOFT, fontsize=mpl.rcParams["font.size"] - 1)
    ax.axvline(1.0, color="#d03b3b", lw=0.9, zorder=2)
    ax.annotate("RB budget $B^{\\max}$", xy=(1.0, 1.0), xycoords=("data", "axes fraction"),
                xytext=(0, 3), textcoords="offset points", ha="center", va="bottom",
                color="#d03b3b", fontsize=mpl.rcParams["font.size"] - 1.2, annotation_clip=False)
    ax.set_yticks(y, [label[m] for m in sim])
    ax.set_ylim(-0.6, len(sim) - 0.4)
    ax.set_xlim(0, 1.16)
    ax.set_xticks(np.arange(0, 1.01, 0.25), ["0", "25", "50", "75", "100"])
    ax.set_xlabel("peak resource blocks in use, against the budget (%)")
    ax.xaxis.grid(True)
    ax.yaxis.grid(False)
    ax.tick_params(axis="y", length=0)
    # Same rule: the three "never happened" claims are counted, not asserted.
    runs = d[d.method.isin(sim) & d.rb_peak.notna()]  # rows that have a table
    bad_rb = int(runs.rb_violation.fillna(False).astype(bool).sum()) if "rb_violation" in runs else 0
    bad_harq = int(runs.harq_overlap.fillna(False).astype(bool).sum()) if "harq_overlap" in runs else 0
    bad_rel = (int((~runs.reliability_ok.fillna(True).astype(bool)).sum())
               if "reliability_ok" in runs else 0)
    n_proof = int((runs.evidence == "proof").sum()) if "evidence" in runs else 0
    what = f"{len(runs):,} tables" + (f" ({n_proof:,} of them ILP proofs)" if n_proof else "")
    if not (bad_rb or bad_harq or bad_rel):
        note = (f"Across all {what}: none exceeded the RB budget, none overlapped a "
                "HARQ\nprocess, and every flow met its reliability target. "
                "The failures are ordering, not capacity.")
    else:
        note = (f"Across all {what}: {bad_rb} exceeded the RB budget, {bad_harq} "
                f"overlapped a HARQ\nprocess, {bad_rel} missed a reliability target.")
    # 5G-TPS sizes its own RB allocation from a rate target instead of taking
    # the Section V configuration (see collect_verdicts.py), so its peak is on
    # a different footing from the other five methods'; say so on the figure.
    caveat = {m: [] for m in sim}
    for m in partial:
        caveat[m].append("peak over the flows it placed")
    if "5gtps" in sim:
        caveat["5gtps"].append("RBs sized from its own rate target, not the Section V configuration")
    for m, why in caveat.items():
        if why:
            note += f"\n{LABEL[m]}: " + "; ".join(why) + "."
    footnote(fig, note)
    return save(fig, "fig6_resource_headroom", args)


# --------------------------------------------------------------------------
# numbers.tex -- the prose and the figures read from the same source
# --------------------------------------------------------------------------
def write_numbers(d: pd.DataFrame, cov: pd.DataFrame | None,
                  cand: pd.DataFrame | None, args, ctx) -> None:
    p = d.pivot(index="flowset", columns="method", values="verdict")
    i = d[d.method == "ilp"].copy()
    proved = p[p.ilp == "ok"] if "ilp" in p else p.iloc[:0]
    undec = p[p.ilp == "timeout"] if "ilp" in p else p.iloc[:0]

    def cmd(name: str, value: str) -> str:
        return f"\\newcommand{{\\{name}}}{{{value}}}\n"

    out = ["% Generated by plot_evaluation.py -- do not edit.\n",
           f"% source: {args.verdicts.name}\n"]
    out.append(cmd("NFlowsets", f"{ctx['n_flowsets']}"))
    out.append(cmd("NFlowsPerSet", f"{ctx['n_flows']}"))
    for m in ctx["methods"]:
        tag = {"5gtps": "Fivegtps"}.get(m, m.capitalize())
        out.append(cmd(f"Ok{tag}", f"{100 * (p[m] == 'ok').mean():.1f}\\%"))
    if "ilp" not in p:
        args.outdir.mkdir(parents=True, exist_ok=True)
        (args.outdir / "numbers.tex").write_text("".join(out))
        print(f"  wrote {args.outdir / 'numbers.tex'} (no ilp rows: ILP macros omitted)")
        return
    out.append(cmd("IlpUnschedulable", f"{(p.ilp == 'ko_unschedulable').sum()}"))
    out.append(cmd("IlpUndecided", f"{(p.ilp == 'timeout').sum()}"))
    # The solver columns are ILP-only and may be absent from a hand-made CSV;
    # each macro is written only if the data set can back it up.
    if "solve_time_ms_median" in i and i.solve_time_ms_median.notna().any():
        out.append(cmd("IlpMedianSolveSec", f"{i.solve_time_ms_median.median() / 1000:.1f}"))
    if "solve_time_ms_max" in i and i.solve_time_ms_max.notna().any():
        out.append(cmd("IlpMaxSolveSec", f"{i.solve_time_ms_max.max() / 1000:.0f}"))
    if {"n_candidates_tested", "n_candidates_total"} <= set(i.columns):
        out.append(cmd("IlpMedianExplored",
                       f"{100 * (i.n_candidates_tested / i.n_candidates_total).median():.1f}\\%"))
        out.append(cmd("IlpMedianCandidates", f"{i.n_candidates_total.median():.0f}"))
    for m in [x for x in ctx["methods"] if x != "ilp"]:
        tag = {"5gtps": "Fivegtps"}.get(m, m.capitalize())
        out.append(cmd(f"Recover{tag}", f"{100 * (proved[m] == 'ok').mean():.1f}\\%"))
    if len(undec):
        out.append(cmd("RecoverBestUndecided",
                       f"{100 * max((undec[m] == 'ok').mean() for m in ctx['methods'] if m != 'ilp'):.1f}\\%"))
    sim = d[d.method.isin(simulated(d))]
    if "worst_overrun_tti" in sim and sim.worst_overrun_tti.notna().any():
        out.append(cmd("WorstLatenessMs", f"{sim.worst_overrun_tti.max() * args.tti_ms:.0f}"))
    if {"rb_peak", "rb_capacity"} <= set(sim.columns):
        out.append(cmd("MedianRbPeak", f"{100 * (sim.rb_peak / sim.rb_capacity).median():.0f}\\%"))
    if "rb_violation" in sim:
        out.append(cmd("NRbViolations", str(int(sim.rb_violation.fillna(False).astype(bool).sum()))))
    if cand is not None and not cand.empty:
        k = cand.groupby(["flowset", "app"], observed=True).size()
        space = cand.groupby(["flowset", "app"], observed=True).size().groupby("flowset").prod()
        out.append(cmd("MeanConfigsPerFlow", f"{k.mean():.1f}"))
        out.append(cmd("MinConfigsPerFlow", f"{k.min():.0f}"))
        out.append(cmd("MaxConfigsPerFlow", f"{k.max():.0f}"))
        out.append(cmd("MedianConfigSpace", f"{space.median():,.0f}".replace(",", "\\,")))
        out.append(cmd("MaxConfigSpace", f"{space.max():,.0f}".replace(",", "\\,")))

    if cov is not None:
        piv = cov.pivot(index="flowset", columns="method", values="coverage")
        out.append(cmd("NConfigs", f"{ctx['n_configs']:,}".replace(",", "\\,")))
        for m in METHODS:
            if m in piv:
                tag = {"5gtps": "Fivegtps"}.get(m, m.capitalize())
                out.append(cmd(f"MeanCoverage{tag}", f"{100 * piv[m].mean():.1f}\\%"))
    args.outdir.mkdir(parents=True, exist_ok=True)
    path = args.outdir / "numbers.tex"
    path.write_text("".join(out))
    print(f"  wrote {path.relative_to(HERE) if path.is_relative_to(HERE) else path}")
    print("".join("    " + line for line in out[2:]), end="")


def fig_configuration_space(cand: pd.DataFrame, args, ctx) -> None:
    """The flexibility claim, made concrete: how many ways each application can
    be served, and how differently."""
    if cand is None or cand.empty:
        print("  skipped: no planner reports to read the candidate space from",
              file=sys.stderr)
        return

    # An application class is not one reliability target: CC0/CC1, CP0/CP1 and
    # PC0/PC1 differ only in PER (doc/input.md), which is precisely the thing
    # this figure is about. Splitting them keeps each row a single target
    # instead of averaging two.
    cand = cand.assign(group=cand.cls + "|" + cand.per.map(lambda e: f"{e:g}"))

    # Configurations per flow: count them per (flow set, flow), because that
    # is the choice the planner offers for one flow. Aggregating the raw rows
    # instead would weigh a class by how often it is sampled.
    per_flow = (cand.groupby(["group", "flowset", "app"], observed=True)
                    .size().rename("k").reset_index())
    stat = (per_flow.groupby("group", observed=True).k
                    .agg(["mean", "min", "max"]).reset_index())
    cost = cand.groupby("group", observed=True).cost.apply(list)

    per_of = dict(zip(cand.group, cand.per))
    # Loosest target first: the figure's argument is that the space narrows as
    # the reliability target tightens, so the rows have to be in that order for
    # the trend to be visible rather than asserted.
    order = sorted(stat.group, key=lambda g: (-per_of[g], g))
    stat = stat.set_index("group").loc[order]
    tiers = sorted({p for p in per_of.values()}, reverse=True)
    color = {g: PER_RAMP[min(tiers.index(per_of[g]), len(PER_RAMP) - 1)] for g in order}

    y = np.arange(len(order))[::-1]
    fig, axes = plt.subplots(1, 2, figsize=(args.width, 0.215 * len(order) + 1.0),
                             sharey=True, gridspec_kw={"width_ratios": [1, 1.15]})

    ax = axes[0]
    ax.barh(y, stat["mean"], height=0.6, zorder=3,
            color=[color[g] for g in order], edgecolor="none")
    for yi, g in zip(y, order):
        lo, hi = stat.loc[g, "min"], stat.loc[g, "max"]
        if hi > lo:  # the spread across flow sets, drawn on top of the mean
            ax.plot([lo, hi], [yi, yi], color=INK_SOFT, lw=0.7, zorder=4,
                    solid_capstyle="butt")
        ax.text(hi + 0.35, yi, f"{stat.loc[g, 'mean']:.1f}", va="center",
                ha="left", color=INK_SOFT,
                fontsize=mpl.rcParams["font.size"] - 1.6)
    ax.set_xlim(0, stat["max"].max() * 1.30)
    panel_tag(ax, "(a)")
    ax.set_xlabel("configurations per flow")

    ax = axes[1]
    bp = ax.boxplot([cost[g] for g in order], positions=y, vert=False, widths=0.6,
                    whis=(5, 95), showfliers=False, patch_artist=True, zorder=3)
    for patch, g in zip(bp["boxes"], order):
        patch.set(facecolor=color[g], alpha=0.32, edgecolor=color[g], linewidth=0.9)
    for key in ("whiskers", "caps"):
        for art in bp[key]:
            art.set(color=INK_MUTED, linewidth=0.7)
    for art in bp["medians"]:
        art.set(color=INK, linewidth=1.1)
    ax.set_xscale("log")
    panel_tag(ax, "(b)")
    ax.set_xlabel("grid cost (RB$\\cdot$TTI)")

    # After the boxplot, not before: boxplot() installs its own tick locator on
    # the axis it draws in, and with sharey that replaces the labels set here.
    axes[0].set_yticks(y, [f"{g.split('|')[0]}  {sci(float(g.split('|')[1]))}"
                           for g in order])
    for a in axes:
        a.set_ylim(-0.8, len(order) - 0.2)

    # The flow-set totals: the product over the flows in the set, which is the
    # space the ILP actually walks.
    space = (per_flow.groupby(["flowset"], observed=True).k.prod())
    footnote(fig,
             f"Per flow set the choices multiply: a median of {space.median():,.0f} candidate\n"
             f"configurations over the {ctx.get('n_flows', '')} flows in a set, up to "
             f"{space.max():,.0f}.")
    return save(fig, "fig7_configuration_space", args)


def write_config_table(cand: pd.DataFrame, args, ctx) -> None:
    r"""The candidate space as a booktabs table, for the flexibility section.

    Same rows, same order and same split as fig_configuration_space, written in
    the paper's own notation -- N segments, A attempts, B resource blocks, as in
    the worked example of Table~\ref{tab:example_candidates} -- so the table
    generalises that example over the whole corpus instead of restating it.
    """
    if cand is None or cand.empty:
        print("  skipped: no planner reports to read the candidate space from",
              file=sys.stderr)
        return

    cand = cand.assign(group=cand.cls + "|" + cand.per.map(lambda e: f"{e:g}"))
    per_of = dict(zip(cand.group, cand.per))
    order = sorted(set(cand.group), key=lambda g: (-per_of[g], g))
    k = cand.groupby(["group", "flowset", "app"], observed=True).size()
    space = cand.groupby(["flowset", "app"], observed=True).size().groupby("flowset").prod()

    def span(v) -> str:
        lo, hi = int(v.min()), int(v.max())
        return f"{lo}" if lo == hi else f"{lo}--{hi}"

    rows = []
    for g in order:
        sub = cand[cand.group == g]
        kk = k.loc[g]
        rows.append(f"{g.split('|')[0]} & {sci(per_of[g])} & {kk.mean():.1f} & "
                    f"{span(sub.segments)} & {span(sub.attempts)} & {span(sub.rbs)} \\\\")

    tex = [
        "% generated by analysis/plot_evaluation.py -- do not edit by hand",
        "\\begin{table}[htbp]",
        "\\centering",
        "\\caption{Candidate configurations per flow, by application class and reliability "
        f"target $E_i$, over the {space.size} flow sets. $|\\mathcal{{P}}_i|$ is the mean size "
        "of the Pareto set~\\eqref{eq:pareto_set}; $N$, $A$ and $B$ span the configurations "
        "it contains.}",
        "\\label{tableresults1}",
        "\\begin{tabular}{lcrccc}",
        "\\toprule",
        "\\textbf{App} & $E_i$ & $|\\mathcal{P}_i|$ & $N$ & $A$ & $B$ \\\\",
        "\\midrule",
        *rows,
        "\\midrule",
        "\\multicolumn{2}{l}{Per flow set, $\\prod_i |\\mathcal{P}_i|$} & "
        + thou(space.median()) + " & \\multicolumn{3}{c}{median; up to "
        + thou(space.max()) + "} \\\\",
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table}",
    ]
    args.outdir.mkdir(parents=True, exist_ok=True)
    out = args.outdir / "table_configurations.tex"
    out.write_text("\n".join(tex) + "\n")
    print(f"  wrote {rel(out)}")
    print("".join(f"    {line}\n" for line in tex[1:]), end="")


CAPTION = {
    "schedulability": "Verdict for each of the {n_flowsets} flow sets, per method. The ILP "
                      "decides {ok_ilp} schedulable; the best baseline reaches {ok_qospf}.",
    "coverage": "How much of the planner's configuration space each method can actually "
                "run: not whether a flow set is schedulable, but how many ways it is.",
    "agreement": "Where the ILP proves a schedule exists, how often a baseline finds one. "
                 "The gap is scheduling policy, not infeasibility.",
    "severity": "(a) how late the worst packet is; (b) how many of the {n_flows} flows in "
                "the set are late. A miss is typically 2 of 5 flows, several ms late.",
    "ilp-cost": "(a) time per candidate configuration; (b) the share of the space explored "
                "before the budget ran out, by outcome.",
    "headroom": "Peak resource-block use against the budget. No run ever exceeded it, so "
                "the baselines' misses are an ordering failure, not a capacity one.",
    "config-space": "The configuration space the planner offers, by application class and "
                    "reliability target: (a) how many ways one flow can be served, "
                    "(b) what those ways cost the grid. The method chooses within this "
                    "space per flow; a baseline commits to a single point in it.",
}

# (function, one-line blurb, which data set it is drawn from). Three sources
# now, so the third field names one instead of being a "reads the big CSV" flag.
FIGURES = {
    "schedulability": (fig_schedulability, "verdict mix per method (headline)", "verdicts"),
    "coverage": (fig_coverage, "how many configurations each method can schedule", "coverage"),
    "agreement": (fig_agreement, "baselines vs the ILP's proof", "verdicts"),
    "severity": (fig_severity, "how late, and how many flows", "verdicts"),
    "ilp-cost": (fig_ilp_cost, "solve time and search coverage", "verdicts"),
    "headroom": (fig_headroom, "RB peak vs budget: failures are timing, not capacity", "verdicts"),
    "config-space": (fig_configuration_space,
                     "the candidate space per application: how many ways, at what cost",
                     "candidates"),
    "config-table": (write_config_table,
                     "the same space as a LaTeX table (tableresults1)", "candidates"),
    "numbers": (None, "numbers.tex macros only", "verdicts"),
}

SOURCE_NOTE = {"coverage": "   [reads the per-configuration CSV]",
               "candidates": "   [reads the planner reports]"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verdicts", type=Path, default=HERE / "csv" / "output.csv")
    ap.add_argument("--per-config", type=Path, default=HERE / "csv" / "results_per_configuration.csv")
    ap.add_argument("--reports", type=Path, default=HERE.parent / "output" / "reports",
                    help="output/reports, for the planner's candidate space")
    ap.add_argument("--outdir", type=Path, default=HERE / "figures")
    ap.add_argument("--only", nargs="+", metavar="FIG", help="subset of --list names")
    ap.add_argument("--list", action="store_true", help="show the figures and exit")
    ap.add_argument("--formats", nargs="+", default=["pdf", "png"], choices=["pdf", "png", "svg"])
    ap.add_argument("--dpi", type=int, default=400, help="raster preview resolution")
    ap.add_argument("--font", choices=["serif", "sans"], default="serif")
    ap.add_argument("--fontsize", type=float, default=8.0, help="base point size")
    ap.add_argument("--width", default="single",
                    help="'single' (3.5in), 'double' (7.16in), or inches")
    ap.add_argument("--tti-ms", type=float, default=TTI_MS,
                    help="TTI in ms, for turning overruns in TTIs into ms "
                         f"(default {TTI_MS}; the CSV does not carry it)")
    ap.add_argument("--candidate-cap", type=float, default=30.0,
                    help="per-candidate ILP budget to mark, in seconds (0 = don't)")
    ap.add_argument("--no-legend", action="store_true",
                    help="fig1: omit the verdict legend (the caption names the colours)")
    ap.add_argument("--ilp-cost-panels", choices=["both", "time"], default="both",
                    help="fig5: solve-time CDF and coverage (both), or the CDF alone (time)")
    ap.add_argument("--embed-notes", action="store_true",
                    help="draw each figure's note under its axes as well as "
                         "writing it to <name>.note.txt")
    ap.add_argument("--refresh", action="store_true", help="rebuild the coverage cache")
    args = ap.parse_args()

    if args.list:
        for name, (_, blurb, src) in FIGURES.items():
            print(f"  {name:<16} {blurb}{SOURCE_NOTE.get(src, '')}")
        return

    args.width = WIDTHS.get(args.width, None) or float(args.width)
    names = args.only or [n for n in FIGURES if n != "numbers"]
    unknown = set(names) - set(FIGURES)
    if unknown:
        raise SystemExit(f"unknown figure(s): {sorted(unknown)} (try --list)")

    style(args.font, args.fontsize)
    d = load_verdicts(args.verdicts)
    present = [m for m in METHODS if m in set(d.method)]
    extra = sorted(set(d.method) - set(METHODS))
    if extra:
        print(f"note: ignoring method(s) not in METHODS: {extra} -- add them to METHODS, "
              "LABEL, COLOR, DASH and MARKER to plot them", file=sys.stderr)
    ctx = {
        "methods": present,
        "n_flowsets": d.flowset.nunique(),
        "n_flows": int(d.n_flows.dropna().max()),
        "n_unschedulable": int((d[d.method == "ilp"].verdict == "ko_unschedulable").sum()),
        "n_sim_runs": int(d.method.isin(simulated(d)).sum()),
        "candidate_cap_s": args.candidate_cap or None,
    }

    wants = {FIGURES[n][2] for n in names}

    cov = None
    if "coverage" in wants or "numbers" in names:
        try:
            cov = load_coverage(args.per_config, args.outdir / ".cache", args.refresh)
            ctx["n_configs"] = int(cov.groupby("flowset").n_tested.max().sum())
        except SystemExit as e:
            if "coverage" in wants:
                raise
            print(f"note: {e}; numbers.tex will omit the coverage macros", file=sys.stderr)

    cand = None
    if "candidates" in wants or "numbers" in names:
        try:
            cand = load_candidates(args.reports)
        except SystemExit as e:
            if "candidates" in wants:
                raise
            print(f"note: {e}; numbers.tex will omit the configuration-space macros",
                  file=sys.stderr)

    for name in names:
        fn, blurb, src = FIGURES[name]
        print(f"{name}: {blurb}")
        if name == "numbers":
            write_numbers(d, cov, cand, args, ctx)
            continue
        if name in CAPTION:
            p_ = d.pivot(index="flowset", columns="method", values="verdict")
            fmt = collections.defaultdict(lambda: "n/a", ctx)
            fmt.update({f"ok_{m}": pct((p_[m] == "ok").mean()) for m in ctx["methods"]})
            # format_map over a defaultdict: a caption naming a method this data
            # set does not have degrades to "n/a" instead of raising.
            PENDING["caption"] = CAPTION[name].format_map(fmt)
        fn({"coverage": cov, "candidates": cand}.get(src, d), args, ctx)
        # A figure that stood aside never reached save(), so it leaves nothing
        # behind for the next one to pick up.
        PENDING.clear()
    if "numbers" not in names:
        print("numbers: numbers.tex macros")
        write_numbers(d, cov, cand, args, ctx)


if __name__ == "__main__":
    main()
