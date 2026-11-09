#!/usr/bin/env python3
"""Each baseline with a search over SIREN's tuples (the paper's RQ2 and RQ3).

A baseline is given one configuration per flow by the shared selection rule
(doc/baselines/index.md). This experiment asks what it would reach if it could
search instead, as SIREN does: `sweep_configurations.py` runs it once on every
tuple of prod_i P_i that the ILP decided, with that tuple imposed
(FLOW_CONFIG), and writes one row per (tuple, method) in
output/per_config/rb<B>.csv. Such a search needs Section V's configurations
and an offline simulation of every tuple, so "baseline + search" is an
ablation of SIREN, not a scheduler a gNB runs.

  derive   reduce the per-tuple CSVs (about 9 million rows per budget) to
           analysis/csv/baseline_search.csv, one row per (budget, baseline,
           flowset): how many tuples were run, how many meet every deadline,
           and the lowest peak among those
  summary  print, per budget and baseline, what plot_paper.py turns into
           macros

A flowset is schedulable for "baseline + search" if one tuple meets every
deadline; its residual capacity is that of its lowest-peak such tuple. The
gains compare with SIREN's minimum-peak table, as paired_gain() in
plot_paper.py does for the baselines' own configuration: B^max minus the peak
of SIREN, minus B^max minus the peak of the baseline, on the flowsets both
schedule.

Usage:
    python3 analysis/baseline_search.py derive [--per-config output/per_config]
    python3 analysis/baseline_search.py summary
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PER_CONFIG = ROOT / "output" / "per_config"
SEARCH_CSV = HERE / "csv" / "baseline_search.csv"
COLUMNS = ["rb", "method", "flowset", "n_tuples", "n_ok", "best_peak"]


def derive(per_config: Path = PER_CONFIG, budgets: list | None = None) -> pd.DataFrame:
    """One row per (budget, baseline, flowset) from per_config/rb<B>.csv. The
    ILP's own rows (method ilp) are left out: its verdict per flowset is in
    output_sweep.csv already."""
    files = sorted(per_config.glob("rb*.csv"), key=lambda p: int(p.stem[2:]))
    if budgets is not None:
        files = [p for p in files if int(p.stem[2:]) in budgets]
    rows = []
    for path in files:
        rb = int(path.stem[2:])
        parts = []
        for chunk in pd.read_csv(path, usecols=["flowset", "method", "verdict", "rb_peak"],
                                 chunksize=2_000_000, low_memory=False):
            chunk = chunk[chunk.method != "ilp"]
            ok = chunk.verdict == "ok"
            parts.append(chunk.assign(ok=ok, peak=chunk.rb_peak.where(ok))
                         .groupby(["method", "flowset"])
                         .agg(n_tuples=("verdict", "size"), n_ok=("ok", "sum"),
                              best_peak=("peak", "min")))
        # A flowset's tuples may straddle two chunks: combine the partial counts.
        part = pd.concat(parts).groupby(level=[0, 1]).agg(
            n_tuples=("n_tuples", "sum"), n_ok=("n_ok", "sum"), best_peak=("best_peak", "min"))
        rows.append(part.reset_index().assign(rb=rb))
        print(f"  {path.name}: {int(part.n_tuples.sum()):,} baseline runs, "
              f"{part.index.get_level_values(1).nunique()} flowsets")
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    out = pd.concat(rows)[COLUMNS]
    out["n_ok"] = out.n_ok.astype(int)
    return out.sort_values(["rb", "method", "flowset"]).reset_index(drop=True)


def compare(search: pd.DataFrame, d: pd.DataFrame) -> pd.DataFrame:
    """Per (budget, baseline): flowsets schedulable with the search, those of
    them SIREN does not schedule, and SIREN's gain in guaranteed residual (RBs)
    over the baseline's lowest-peak tuple, on the flowsets both schedule. d is
    plot_paper.load()'s frame (verdict_model, rb_peak, rb_capacity)."""
    ilp = d[(d.method == "ilp") & (d.verdict_model == "ok")]
    rows = []
    for (rb, m), s in search.groupby(["rb", "method"]):
        il = ilp[ilp.rb == rb].set_index("flowset")
        ok = s[s.n_ok > 0].set_index("flowset")
        both = il.index.intersection(ok.index)
        g = (ok.loc[both, "best_peak"] - il.loc[both, "rb_peak"]).astype(float)
        rows.append({"rb": rb, "method": m, "n_flowsets": s.flowset.nunique(),
                     "n_runs": int(s.n_tuples.sum()), "n_ok": len(ok),
                     "only_search": len(ok.index.difference(il.index)), "n": len(both),
                     "med": g.median(), "q1": g.quantile(0.25), "q3": g.quantile(0.75),
                     "worse": int((g < 0).sum())})
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("derive", help="per-tuple CSVs -> analysis/csv/baseline_search.csv")
    p.add_argument("--per-config", type=Path, default=PER_CONFIG,
                   help="folder of sweep_configurations.py's rb<B>.csv (default output/per_config)")
    p.add_argument("--out", type=Path, default=SEARCH_CSV)
    sub.add_parser("summary", help="print what plot_paper.py turns into macros")
    a = ap.parse_args()

    if a.cmd == "derive":
        s = derive(a.per_config)
        if s.empty:
            print(f"no rb<B>.csv under {a.per_config}: {a.out.name} kept")
            return 1
        a.out.parent.mkdir(parents=True, exist_ok=True)
        s.to_csv(a.out, index=False)
        print(f"wrote {a.out}: {len(s)} rows")
        return 0

    sys.path.insert(0, str(HERE))
    import plot_paper
    c = compare(pd.read_csv(SEARCH_CSV), plot_paper.load())
    with pd.option_context("display.width", 160):
        print(c.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
