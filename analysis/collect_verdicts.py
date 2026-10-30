#!/usr/bin/env python3
"""Collect one verdict row per (method, flowset) into a tidy CSV.

This is the backbone of the cross-method comparison: every experiment in the
evaluation reduces to counting or plotting rows of this table.

The distinction the table exists to make is between how the two kinds of
method arrive at "this flow set is fine":

  baselines  simulate one hyperperiod at worst-case transmission time and
             report whether anything missed a deadline in that one run.
  ILP        proves that a schedule meeting every deadline exists, or that
             none does, or runs out of time (or ends in a solver error)
             saying which.

A packet meets its deadline when its last transport block's end_tti plus the
upper-layer delay (config.t_up_tti; 0 in reports that predate it, whose
end_tti included it) is at most deadline_tti: end_tti is when a TB releases
its HARQ process and RBs, and t_up holds neither.

A simulated OK is therefore weaker than a proved OK, and the columns after
`verdict` are the independent checks that can turn a simulated OK into a
demonstrated false positive:

  n_dropped_packets  packets a scheduler discarded instead of dispatching late
                  (MUST); each one is counted in n_deadline_misses
  rb_violation    more resource blocks in use at some TTI than the cell has
  harq_overlap    two transport blocks on one HARQ process at once
  reliability_ok  every flow's chosen configuration actually meets its E_i

Two further columns measure what a table leaves the cell for non-real-time
traffic (RQ3): `rb_peak`, already above, is the most RBs any TTI uses, so
B^max - rb_peak RBs are free in every TTI; `rb_window_mean_peak` is the
highest mean usage over any window of length t_min_tti (the shortest period in
the flow set, cyclically over the hyperperiod), so B^max - rb_window_mean_peak
is the average capacity an NRT flow of that period is guaranteed. Under the
ILP's rb_peak objective the row reports the schedulable candidate with the
smallest rb_peak_used, not the first one found. With the bounded search
(--rb-peak-search bounded) the driver also reports how the peak minimisation
went: `n_rb_peak_solved`, `n_rb_peak_pruned` (candidates the mean-usage bound
made unnecessary), `n_rb_peak_timed_out`, and `rb_peak_optimal_over_decided`,
true when no candidate cheaper than the best was left undecided.

Caveat on 5G-TPS: it sizes its own RB allocation from a rate target, so its
dispatch `b` is the RBs it holds over many TTIs, not the RBs of one transport
block as for the other five methods, and its reliability column reflects the
configuration (TB size, MCS, attempts) its rate is derived from. Read its rb_*
and reliability columns with that in mind.

One row per (method, flow set) is the unit throughout: the CSV, and the
verdict summary printed after it. `--per-report` adds a second table counting
*dispatch tables* instead, which differs only for a method that emits more than
one schedule per flow set -- today, the ILP. `--per-config` adds a third, in
the same columns and rows as the verdict summary, counting *candidate
configurations*: what every method says about each configuration the ILP
tested, from the CSV sweep_configurations.py writes. Each table keeps to one
unit rather than mixing them, and neither flag changes the CSV.

Usage:
    python3 collect_verdicts.py --reports output/reports \\
                                --flowsets output/flowsets \\
                                --mcs input/mcs_configurations.json \\
                                --out results/verdicts.csv [--per-report]

`--merge` updates an existing CSV instead of replacing it: the rows of the
methods found under --reports are rewritten, every other row is kept as it
is. That is how one method added later (say, the 5gtps_harq variant run on
its own) joins the table of a sweep whose other reports are no longer here.

`--per-config` reads a CSV rather than producing one, because producing it
means re-running every baseline once per candidate configuration -- 311744
runs for the sweep in output/reports, some minutes of CPU. This script is
otherwise a seconds-long read over files that already exist, so the expensive
half lives in its own script and is run when the reports change, not on every
table you print:

    python3 sweep_configurations.py --reports output/reports \\
                                    --flowsets output/flowsets \\
                                    --mcs input/mcs_configurations.json \\
                                    --out output/per_config.csv --jobs 100

    python3 collect_verdicts.py ... --per-config output/per_config.csv
"""

import argparse
import csv
import json
import os
import statistics
import sys
from collections import defaultdict


def load_json(path):
    with open(path) as f:
        return json.load(f)


def flow_targets(flowsets_dir):
    """app -> target packet error, per flowset."""
    targets = {}
    for name in os.listdir(flowsets_dir):
        if not (name.startswith("flowset_") and name.endswith(".json")):
            continue
        stem = os.path.splitext(name)[0]
        targets[stem] = {f["app"]: f for f in load_json(os.path.join(flowsets_dir, name))}
    return targets


def mcs_bler(mcs_path):
    """(tbs, mcs index) -> per-attempt block error rate, from the tables at
    SINR_DB (default 10 dB, as in the schedulers): the rows they schedule with."""
    sinr_db = float(os.environ.get("SINR_DB") or 10)
    table = {}
    for entry in load_json(mcs_path):
        if abs(entry["sinr"] - sinr_db) > 1e-9:
            continue
        for row in entry["mcs"]:
            table[(entry["tbs"], row["m"])] = row["bler"]
    if not table:
        sys.exit(f"{mcs_path}: no MCS configurations at SINR_DB={sinr_db:g} dB")
    return table


def check_reliability(flows, targets, bler):
    """Worst achieved/target packet error ratio over the flows. <= 1 is safe."""
    worst = 0.0
    for fl in flows:
        spec = targets.get(fl["app"])
        if spec is None:
            continue
        key = (fl.get("tbs"), fl.get("m"))
        if key not in bler or "a" not in fl:
            return None  # report predates the per-flow attempt count
        per_tb = bler[key] ** fl["a"]
        achieved = 1 - (1 - per_tb) ** fl["num_segments"]
        worst = max(worst, achieved / spec["error"])
    return worst


def t_min_of(flows):
    """Shortest period, in TTIs, among the flows a report lists."""
    periods = [f["period_tti"] for f in flows if f.get("period_tti")]
    return min(periods) if periods else None


def t_up_of(report):
    """t_up in TTIs, from a report's config. A table's end_tti is when a TB
    releases its HARQ process and RBs; the packet is delivered t_up later.
    Reports written before t_up left the TB's occupancy have no such field
    and already included it in end_tti, hence 0."""
    return report.get("config", {}).get("t_up_tti", 0) or 0


def check_dispatch(dispatch, n_rb, m_harq, hyperperiod=None, t_min=None, t_up=0):
    """Deadline misses, peak RB usage, and HARQ overlap in a dispatch table.

    A packet is delivered t_up TTIs (the upper-layer delay) after its last
    transport block ends, and that delivery is what meets or misses the
    deadline. The RB and HARQ checks use [start, end) only: t_up holds
    neither.

    With `hyperperiod` and `t_min` (both in TTIs) it also returns
    `rb_window_mean_peak`: the highest mean RB usage over any window of t_min
    consecutive TTIs, the table being read cyclically over the hyperperiod.
    """
    if not dispatch:
        return {"misses": 0, "worst_overrun": 0, "rb_peak": 0, "harq_overlap": False,
                "rb_window_mean_peak": 0}

    # A packet is late when the last of its transport blocks is late, so group
    # the fork/join set by (app, release) before comparing to the deadline.
    jobs = defaultdict(lambda: {"end": 0, "deadline": 0})
    for e in dispatch:
        key = (e["app"], e["release_tti"])
        jobs[key]["end"] = max(jobs[key]["end"], e["end_tti"])
        jobs[key]["deadline"] = e["deadline_tti"]

    overruns = [j["end"] + t_up - j["deadline"] for j in jobs.values()
                if j["end"] + t_up > j["deadline"]]

    horizon = max(e["end_tti"] for e in dispatch)
    usage = [0] * (horizon + 1)
    for e in dispatch:
        for t in range(e["start_tti"], e["end_tti"]):
            usage[t] += e["b"]

    window_peak = None
    if hyperperiod and t_min and 0 < t_min <= hyperperiod:
        # Cyclic usage over [0, H): a job that runs past H (a baseline's late
        # job) wraps, as its replay would; the ILP's jobs never do.
        cyc = [0] * hyperperiod
        for t, u in enumerate(usage):
            cyc[t % hyperperiod] += u
        window = sum(cyc[:t_min])
        best = window
        for start in range(1, hyperperiod):
            window += cyc[(start + t_min - 1) % hyperperiod] - cyc[start - 1]
            best = max(best, window)
        window_peak = best / t_min

    overlap = False
    by_machine = defaultdict(list)
    for e in dispatch:
        by_machine[e["machine"]].append(e)
    for entries in by_machine.values():
        entries.sort(key=lambda x: x["start_tti"])
        for a, b in zip(entries, entries[1:]):
            if a["end_tti"] > b["start_tti"]:
                overlap = True

    return {
        "misses": len(overruns),
        "worst_overrun": max(overruns) if overruns else 0,
        "rb_peak": max(usage) if usage else 0,
        "harq_overlap": overlap,
        "rb_window_mean_peak": window_peak,
    }


def ilp_row(report, flowset, targets, bler, report_dir=None):
    combos = report.get("schedulable", [])
    infeasible = report.get("infeasible_flows", [])

    # A candidate abandoned at the per-candidate timeout leaves the flow set
    # undecided just as much as the global budget running out does, so a sweep
    # that finished every candidate but skipped some is a timeout, not a proof
    # of unschedulability. So does a candidate the solver ended in an error:
    # nothing was proved about it either.
    n_timed_out = report.get("num_combinations_timed_out", 0)
    n_errored = report.get("num_combinations_errored", 0)

    if infeasible:
        verdict = "ko_infeasible"
    elif combos:
        verdict = "ok"
    elif report.get("timed_out") or n_timed_out or n_errored:
        verdict = "timeout"
    else:
        verdict = "ko_unschedulable"

    solve_times = [c["solve_time_ms"] for c in combos if "solve_time_ms" in c]
    row = {
        "flowset": flowset,
        "method": "ilp",
        "verdict": verdict,
        "evidence": "proof",
        "n_candidates_total": report.get("num_combinations_total", 0),
        "n_candidates_tested": report.get("num_combinations_tested", 0),
        "n_schedulable": report.get("num_schedulable", 0),
        "n_candidates_timed_out": n_timed_out,
        "n_candidates_errored": n_errored,
        "timed_out": report.get("timed_out", False),
        "infeasible_flows": ";".join(infeasible),
        "solve_time_ms_median": round(statistics.median(solve_times), 3) if solve_times else "",
        "solve_time_ms_max": round(max(solve_times), 3) if solve_times else "",
    }
    search = report.get("rb_peak_search")
    if search:
        row.update({
            "n_rb_peak_solved": search.get("num_solved", ""),
            "n_rb_peak_pruned": search.get("num_pruned", ""),
            "n_rb_peak_timed_out": search.get("num_timed_out", ""),
            "rb_peak_optimal_over_decided": search.get("optimal_over_decided", ""),
        })

    if combos:
        # Under the rb_peak objective every schedulable candidate carries the
        # peak its optimal table reaches; the flow set's residual capacity is
        # the best of them. Under the other objectives the first found stands.
        best = combos[0]
        if report.get("config", {}).get("objective") == "rb_peak":
            with_peak = [c for c in combos if c.get("rb_peak_used") is not None]
            if with_peak:
                best = min(with_peak, key=lambda c: c["rb_peak_used"])
        row.update({
            "n_flows": len(best.get("flows", [])),
            "hyperperiod_tti": best.get("hyperperiod_tti", ""),
            "n_jobs": best.get("num_jobs", ""),
            "n_variables": best.get("num_variables", ""),
            "n_constraints": best.get("num_constraints", ""),
            "worst_reliability_ratio": check_reliability(best.get("flows", []), targets, bler),
        })
        # The proved table is a dispatch table like any baseline's, so measure
        # it the same way: its RB peak is what the ILP leaves the cell, and
        # rb_violation / harq_overlap are an independent check on the proof.
        # worst_overrun_tti is deliberately not set: a proof has no late job,
        # and the miss-severity figures group methods by that column.
        table = os.path.join(report_dir or "", os.path.basename(best.get("schedule_file", "")))
        if best.get("schedule_file") and os.path.exists(table):
            cfg = report.get("config", {})
            doc = load_json(table)
            checks = check_dispatch(doc.get("dispatch_table", []),
                                    cfg.get("n_rb", 0), cfg.get("m_harq", 0),
                                    best.get("hyperperiod_tti"), t_min_of(best.get("flows", [])),
                                    t_up_of(doc))
            row.update({
                "rb_peak": checks["rb_peak"],
                "rb_capacity": cfg.get("n_rb", ""),
                "rb_violation": checks["rb_peak"] > cfg.get("n_rb", 10**9),
                "harq_overlap": checks["harq_overlap"],
                "t_min_tti": t_min_of(best.get("flows", [])),
                "rb_window_mean_peak": checks["rb_window_mean_peak"],
                "rb_peak_optimal": best.get("rb_peak_used", ""),
            })
    # Verdict mode (the ILP's --stop-at-first): the table is the first found,
    # not the minimum-peak one, so the row gives the verdict only; its peak is
    # no result.
    if report.get("config", {}).get("stop_at_first"):
        row.update({"verdict_only": True, "rb_peak": "", "rb_peak_optimal": ""})
    return row


def baseline_row(report, method, flowset, targets, bler):
    if report.get("schedulable") is False and report.get("infeasible_flows"):
        return {
            "flowset": flowset,
            "method": method,
            "verdict": "ko_infeasible",
            "evidence": "configuration selection",
            "infeasible_flows": ";".join(report["infeasible_flows"]),
            "n_flows": 0,
        }

    # 5G-TPS places each flow in a fixed (RB set, offset) slot and declines
    # the ones it cannot fit, naming them in `unscheduled` rather than leaving
    # a late job in the table. A declined flow is a KO, not an absence of
    # misses -- without this the method scores a perfect run by dropping work.
    unscheduled = report.get("unscheduled", [])
    if unscheduled:
        # The table it did build is still measured, so the RB peak of what it
        # placed can sit next to the other methods' -- read with n_flows_placed
        # in mind, since a flow it dropped costs no resource block. No
        # worst_overrun_tti: a declined flow is not a late one.
        cfg = report.get("config", {})
        checks = check_dispatch(report.get("dispatch_table", []),
                                cfg.get("n_rb", 0), cfg.get("m_harq", 0),
                                report.get("hyperperiod_tti"), t_min_of(report.get("flows", [])),
                                t_up_of(report))
        return {
            "flowset": flowset,
            "method": method,
            "verdict": "ko_unplaced",
            "evidence": "simulation",
            "n_flows": len(report.get("flows", [])),
            "n_flows_placed": len(report.get("flows", [])) - len(unscheduled),
            "infeasible_flows": ";".join(unscheduled),
            "rb_peak": checks["rb_peak"],
            "rb_capacity": cfg.get("n_rb", ""),
            "rb_violation": checks["rb_peak"] > cfg.get("n_rb", 10**9),
            "harq_overlap": checks["harq_overlap"],
            "harq_used": report.get("harq_used", ""),
            "t_min_tti": t_min_of(report.get("flows", [])),
            "rb_window_mean_peak": checks["rb_window_mean_peak"],
        }

    flows = report.get("flows", [])
    cfg = report.get("config", {})
    checks = check_dispatch(report.get("dispatch_table", []),
                            cfg.get("n_rb", 0), cfg.get("m_harq", 0),
                            report.get("hyperperiod_tti"), t_min_of(flows),
                            t_up_of(report))
    ratio = check_reliability(flows, targets, bler)

    # MUST discards a packet it cannot deliver by its deadline instead of
    # dispatching it late, and names every discard in `dropped_packets`. A
    # discarded packet is a missed deadline: counting it here is what keeps a
    # scheduler from scoring a clean run by shedding work.
    n_dropped = len(report.get("dropped_packets", []))
    misses = checks["misses"] + n_dropped

    return {
        "flowset": flowset,
        "method": method,
        "verdict": "ko_deadline_miss" if misses else "ok",
        "evidence": "simulation",
        "n_flows": len(flows),
        "hyperperiod_tti": report.get("hyperperiod_tti", ""),
        "n_deadline_misses": misses,
        "n_dropped_packets": n_dropped,
        "worst_overrun_tti": checks["worst_overrun"],
        "rb_peak": checks["rb_peak"],
        "rb_capacity": cfg.get("n_rb", ""),
        "rb_violation": checks["rb_peak"] > cfg.get("n_rb", 10**9),
        "harq_overlap": checks["harq_overlap"],
        "harq_used": report.get("harq_used", ""),
        "worst_reliability_ratio": ratio,
        "reliability_ok": (ratio is not None and ratio <= 1 + 1e-9),
        "t_min_tti": t_min_of(flows),
        "rb_window_mean_peak": checks["rb_window_mean_peak"],
    }


FIELDS = [
    "flowset", "method", "verdict", "evidence", "n_flows", "n_flows_placed", "hyperperiod_tti",
    "n_deadline_misses", "n_dropped_packets", "worst_overrun_tti",
    "rb_peak", "rb_capacity", "rb_violation", "harq_overlap", "harq_used",
    "t_min_tti", "rb_window_mean_peak", "rb_peak_optimal",
    "n_rb_peak_solved", "n_rb_peak_pruned", "n_rb_peak_timed_out", "rb_peak_optimal_over_decided",
    "worst_reliability_ratio", "reliability_ok",
    "n_candidates_total", "n_candidates_tested", "n_schedulable",
    "n_candidates_timed_out", "n_candidates_errored", "timed_out",
    "solve_time_ms_median", "solve_time_ms_max",
    "n_jobs", "n_variables", "n_constraints", "infeasible_flows", "verdict_only",
]


def report_stats(reports_dir):
    """Per method: how many dispatch tables, spread over how many flow sets.

    Counts `combo_*.json` files and deliberately does not open them -- the
    per-flowset rows above already carry everything a dispatch table holds, and
    on a large sweep opening every one of them dominates the runtime.

    `offline_phase_report.json`, where a method writes one, also tells us how
    much of the candidate space the search actually got through. That context
    belongs next to the report count: a method that stops at a time budget
    produces a number of reports that says as much about the budget as about
    the flow set.
    """
    stats = {}
    for method in sorted(os.listdir(reports_dir)):
        method_dir = os.path.join(reports_dir, method)
        if not os.path.isdir(method_dir):
            continue

        s = {"reports": 0, "flowsets": 0, "with_reports": 0,
             "per_flowset": [], "searches": 0, "timed_out": 0, "explored": []}

        for flowset in sorted(os.listdir(method_dir)):
            flowset_dir = os.path.join(method_dir, flowset)
            if not os.path.isdir(flowset_dir):
                continue
            names = os.listdir(flowset_dir)

            n = sum(1 for x in names
                    if x.startswith("combo_") and x.endswith(".json"))
            s["flowsets"] += 1
            s["reports"] += n
            if n:
                s["with_reports"] += 1
                s["per_flowset"].append(n)

            if "offline_phase_report.json" in names:
                r = load_json(os.path.join(flowset_dir, "offline_phase_report.json"))
                total = r.get("num_combinations_total", 0)
                if total:  # zero means configuration selection produced nothing
                    s["searches"] += 1
                    s["explored"].append(r.get("num_combinations_tested", 0) / total)
                    if r.get("timed_out"):
                        s["timed_out"] += 1

        stats[method] = s
    return stats


def print_report_table(stats):
    """The per-report view: one row per method, counting reports not flow sets.

    Kept as a separate table from the verdict summary rather than folded into
    it, so neither table mixes the two units.
    """
    rows = [(m, s) for m, s in sorted(stats.items()) if s["flowsets"]]
    if not rows:
        return

    width = max(max(len(m) for m, _ in rows), len("method"))
    print("\nPer-report summary (one dispatch table = one report)\n")
    header = (f"{'method':{width}s} {'reports':>9s} {'flow sets':>10s} "
              f"{'with report':>12s} {'med/set':>8s} {'max/set':>8s}")
    print(header)
    print("-" * len(header))
    for m, s in rows:
        med = int(statistics.median(s["per_flowset"])) if s["per_flowset"] else 0
        mx = max(s["per_flowset"]) if s["per_flowset"] else 0
        print(f"{m:{width}s} {s['reports']:>9d} {s['flowsets']:>10d} "
              f"{s['with_reports']:>12d} {med:>8d} {mx:>8d}")

    for m, s in rows:
        if not s["searches"] or not s["timed_out"]:
            continue
        pct = statistics.median(s["explored"]) * 100
        print(f"\n  {m}: {s['timed_out']} of {s['searches']} searches hit the time budget, "
              f"exploring a median\n"
              f"  {'':{len(m)}s}  {pct:.1f}% of their candidates. These counts therefore measure how far\n"
              f"  {'':{len(m)}s}  the search got, not how many schedules exist.")


def tally(rows):
    """method -> verdict -> count."""
    counts = defaultdict(lambda: defaultdict(int))
    for r in rows:
        counts[r["method"]][r["verdict"]] += 1
    return counts


def print_verdict_table(counts, verdicts, methods, title=None):
    """The verdict table. Columns and rows are passed in rather than derived
    from the counts, so every table printed in one run lines up column for
    column even where a verdict never occurs in one of them."""
    width = max([len(m) for m in methods] + [len("method")])
    if title:
        print(f"\n{title}")
    print(f"\n{'method':{width}s} " + " ".join(f"{v:>17s}" for v in verdicts))
    for m in methods:
        print(f"{m:{width}s} " + " ".join(f"{counts[m][v]:>17d}" for v in verdicts))


def read_per_config(path):
    """Rows written by sweep_configurations.py: one per (configuration, method)."""
    if not os.path.exists(path):
        raise SystemExit(
            f"--per-config: no such file: {path}\n"
            f"It is written by sweep_configurations.py, which decides every "
            f"configuration\nthe ILP tested with every method:\n\n"
            f"    python3 {os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sweep_configurations.py')} \\\n"
            f"        --reports <reports> --flowsets <flowsets> --mcs <mcs> \\\n"
            f"        --out {path} --jobs $(nproc)\n")
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def print_config_caveats(reports_dir, per_config):
    """What the per-configuration counts do and do not cover.

    Everything here bounds the denominator, and all of it is a property of the
    ILP run the configurations were reconstructed from rather than of the
    methods being compared."""
    ilp_dir = os.path.join(reports_dir, "ilp")
    if not os.path.isdir(ilp_dir):
        return

    total = tested = infeasible = timed_out = skipped = 0
    for flowset in sorted(os.listdir(ilp_dir)):
        path = os.path.join(ilp_dir, flowset, "offline_phase_report.json")
        if not os.path.exists(path):
            continue
        r = load_json(path)
        if r.get("infeasible_flows"):
            infeasible += 1
            continue
        total += r.get("num_combinations_total", 0)
        tested += r.get("num_combinations_tested", 0)
        skipped += r.get("num_combinations_timed_out", 0)
        if r.get("timed_out"):
            timed_out += 1

    # One row per configuration per method, so any one method's row length is
    # the number of configurations the table actually covers.
    counted = max((sum(v.values()) for v in per_config.values()), default=0)
    pct = 100.0 * tested / total if total else 0.0

    if counted == tested:
        print(f"\n  Counts are over {counted} configuration(s): the ones the ILP decided,")
        print(f"  {pct:.1f}% of the {total} the planner generated. The rest were never reached")
        print(f"  before the time budget. {infeasible} flow set(s) contribute nothing, having no")
        print(f"  configuration at all (empty Pareto set, Eq. (6)).")
    else:
        print(f"\n  Counts are over {counted} configuration(s), a subset of the {tested} this")
        print(f"  reports tree holds. Those {tested} are {pct:.1f}% of the {total} the planner")
        print(f"  generated; the rest were never reached before the time budget. {infeasible} flow")
        print(f"  set(s) contribute nothing, having no configuration at all (Eq. (6)).")

    if skipped:
        print(f"\n  {skipped} of those configuration(s) carry the ilp verdict 'timeout': their own")
        print(f"  solve hit the per-candidate cap, so nothing was proved about them either way.")
        print(f"  They are excluded from the ilp proof counts, not counted as unschedulable.")

    if timed_out:
        print(f"\n  {timed_out} flow set(s) ran out the global budget mid-candidate, so the rest of")
        print(f"  their candidate space was never reached at all.")

    # ko_infeasible means something else here than it does per flow set, and
    # the ilp row does not use it at all -- worth saying, since the whole point
    # of the shared columns is that a reader compares them straight down.
    baseline_infeasible = sum(v["ko_infeasible"] for m, v in per_config.items() if m != "ilp")
    if baseline_infeasible:
        print(f"\n  ko_infeasible is per configuration here, not per flow set: the imposed")
        print(f"  candidate needs more resource blocks than the cell has, so no policy can")
        print(f"  transmit it. The ILP has no separate configuration-selection step and")
        print(f"  counts those same candidates under ko_unschedulable, so read that pair of")
        print(f"  columns together rather than down the ilp row alone.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reports", required=True, help="output/reports")
    ap.add_argument("--flowsets", required=True, help="output/flowsets")
    ap.add_argument("--mcs", required=True, help="input/mcs_configurations.json")
    ap.add_argument("--out", required=True, help="CSV to write")
    ap.add_argument("--per-config", metavar="CSV",
                    help="also print a per-configuration summary, in the same "
                         "columns and rows as the verdict table above: the CSV "
                         "written by sweep_configurations.py, which decides "
                         "every configuration the ILP tested with every method. "
                         "Does not change the CSV written here.")
    ap.add_argument("--per-report", action="store_true",
                    help="also print a per-report summary: how many dispatch "
                         "tables each method produced, rather than how many "
                         "flow sets it was given. Does not change the CSV.")
    ap.add_argument("--merge", action="store_true",
                    help="update --out instead of replacing it: rewrite the rows "
                         "of the methods found under --reports, keep the others")
    args = ap.parse_args()

    targets_by_flowset = flow_targets(args.flowsets)
    bler = mcs_bler(args.mcs)

    rows = []
    for method in sorted(os.listdir(args.reports)):
        method_dir = os.path.join(args.reports, method)
        if not os.path.isdir(method_dir):
            continue
        for flowset in sorted(os.listdir(method_dir)):
            flowset_dir = os.path.join(method_dir, flowset)
            if not os.path.isdir(flowset_dir):
                continue
            targets = targets_by_flowset.get(flowset, {})

            if method == "ilp":
                path = os.path.join(flowset_dir, "offline_phase_report.json")
                if os.path.exists(path):
                    rows.append(ilp_row(load_json(path), flowset, targets, bler, flowset_dir))
                continue

            for name in sorted(os.listdir(flowset_dir)):
                if name.startswith("combo_") and name.endswith(".json"):
                    rows.append(baseline_row(load_json(os.path.join(flowset_dir, name)),
                                             method, flowset, targets, bler))
                    break

    if args.merge and os.path.exists(args.out):
        # The kept rows are written back verbatim (as read), in the order a
        # full collection would give: methods sorted, each in its own order.
        with open(args.out, newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != FIELDS:
                sys.exit(f"{args.out}: columns differ from this script's; "
                         "cannot merge (collect without --merge)")
            collected = {r["method"] for r in rows}
            kept = [r for r in reader if r["method"] not in collected]
        print(f"Merging: {len(rows)} new row(s) for {sorted(collected)}, "
              f"{len(kept)} kept from {args.out}")
        rows = sorted(kept + rows, key=lambda r: r["method"])

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"Wrote {len(rows)} rows to {args.out}")

    # Solver errors are scored undecided ("timeout"), like a candidate cut at
    # the cap; say so, so that they do not pass unseen inside that verdict.
    errored = [r for r in rows if int(r.get("n_candidates_errored") or 0) > 0]
    if errored:
        print(f"WARNING: {len(errored)} ILP flow set(s) with candidates that ended in a "
              f"solver error (counted undecided): "
              + ", ".join(sorted(r["flowset"] for r in errored)[:20])
              + (" ..." if len(errored) > 20 else ""))

    counts = tally(rows)
    per_config = tally(read_per_config(args.per_config)) if args.per_config else None

    # Both tables carry the same columns and the same rows, so a cell can be
    # read straight across from one to the other. A verdict or a method that
    # only occurs in one of them shows up as a zero in the other rather than
    # shifting the columns.
    tables = [counts] + ([per_config] if per_config else [])
    verdicts = sorted({v for t in tables for m in t.values() for v in m})
    methods = sorted({m for t in tables for m in t})

    print_verdict_table(counts, verdicts, methods)

    if args.per_report:
        print_report_table(report_stats(args.reports))

    if per_config:
        print_verdict_table(
            per_config, verdicts, methods,
            title="Per-configuration summary (one candidate configuration = one count)")
        print_config_caveats(args.reports, per_config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
