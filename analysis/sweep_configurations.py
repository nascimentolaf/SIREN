#!/usr/bin/env python3
"""Decide every candidate configuration with every method, one row each.

`collect_verdicts.py` counts flow sets: one row per (method, flow set), which
is the unit a method is actually used in. That unit hides how much work the
ILP does. The planner emits a candidate set per flow set -- one configuration
K_i per flow, crossed over the flows -- and the ILP decides the candidates one
by one until its time budget runs out, so a single "ok" in that table can
stand behind hundreds of decided configurations, while a baseline's "ok"
stands behind exactly one: the configuration it picked for itself.

This script puts both on the same footing. For each configuration the ILP
actually tested it re-runs every baseline with that same configuration
imposed (see FLOW_CONFIG in baseline_algorithms/common/scheduler.cpp), so each
method answers for the same candidate, and writes one row per
(configuration, method).

Which configurations were tested is reconstructible rather than recorded. The
ILP walks the candidate space as a mixed-radix odometer over the planner's
per-flow choice lists, last flow advancing fastest, starting at all-zeros
(offline_phase/siren/src/main.cpp), so the tested set is exactly the
first `num_combinations_tested` candidates in that order. `combo_index` in
each emitted combo_*.json is that 1-based position, which is what makes the
reconstruction checkable: decoding a combo index has to reproduce the flows
the ILP recorded for it, and --self-check asserts precisely that.

Flow sets whose configuration selection failed (`infeasible_flows`, Eq. (6))
contribute nothing: the planner emitted no configuration for them, so there is
nothing to decide. They are counted as flow sets by collect_verdicts.py.

--methods defaults to every directory under --reports except ilp; --exclude
drops some of them, and --run-timeout bounds each baseline call:

Usage:
    python3 sweep_configurations.py --reports ../output/reports \\
        --flowsets ../output/flowsets --mcs ../input/mcs_configurations.json \\
        --out per_config.csv --jobs 100

    python3 collect_verdicts.py ... --per-config per_config.csv
"""

import argparse
import re
import time
import csv
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_verdicts import baseline_row, flow_targets, mcs_bler, load_json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def base_environment():
    """parameters.sh is the single source of truth for every other setting
    (kept here so that this script needs no other module)."""
    script = os.path.join(ROOT, "scripts", "parameters.sh")
    out = subprocess.run(["bash", "-c", f"source {script} >/dev/null 2>&1 && env -0"],
                         capture_output=True, text=True, check=True).stdout
    return dict(line.split("=", 1) for line in out.split("\0") if "=" in line)

FIELDS = [
    "flowset", "combo_index", "method", "verdict", "evidence",
    "n_deadline_misses", "worst_overrun_tti", "rb_peak", "rb_violation",
    "harq_overlap", "worst_reliability_ratio", "reliability_ok",
]


def flow_choices(planner_report):
    """Per flow, the flattened choice list the ILP odometer walks.

    One entry is a whole urllc::Candidate -- the planner and the baselines
    share common/urllc_config.hpp, so it can be handed to a scheduler verbatim
    instead of being re-derived from the flow spec.
    """
    per_flow = []
    for app in planner_report["applications"]:
        choices = []
        for seg in app["segmentation_options"]:
            for mbc in seg["mbc_combinations"]:
                choices.append({
                    "app": app["app"],
                    "tbs": seg["tbs"],
                    "segments": seg["num_segments"],
                    "etb_target": seg["tb_error"],
                    "m": mbc["m"],
                    "attempts": mbc["a"],
                    "rbs": mbc["b"],
                    "bler": mbc["bler"],
                    "wctt_ms": mbc["c"],
                })
        per_flow.append(choices)
    return per_flow


def decode(combo_index, per_flow):
    """1-based combo index -> one choice per flow, as FLOW_CONFIG wants it.

    The odometer in the ILP increments from the last flow, so that is the
    fastest-moving digit here too.
    """
    rest = combo_index - 1
    picks = [None] * len(per_flow)
    for i in range(len(per_flow) - 1, -1, -1):
        picks[i] = per_flow[i][rest % len(per_flow[i])]
        rest //= len(per_flow[i])
    return {p["app"]: {k: v for k, v in p.items() if k != "app"} for p in picks}


def schedulable_indices(offline_report):
    """The combo indices the ILP proved schedulable, from the file names it wrote."""
    found = set()
    for entry in offline_report.get("schedulable", []):
        path = entry.get("schedule_file", "")
        stem = os.path.splitext(os.path.basename(path))[0]
        if stem.startswith("combo_"):
            found.add(int(stem[len("combo_"):]))
    return found


def radio_env(env, report_config, where):
    """Environment for a run of the cell the report was produced on."""
    out = dict(env)
    out["RBs"] = str(int(report_config["n_rb"]))
    out["HARQs"] = str(int(report_config["m_harq"]))

    # TTI is not overridden but verified: parameters.sh derives FEEDBACK_PER_RET
    # (k1) from it, so silently substituting one would leave that derived value
    # describing a different grid.
    tti_ms = float(report_config.get("tti_ms", 0.0))
    env_tti_ms = float(env["TTI"]) * 1000.0
    if abs(tti_ms - env_tti_ms) > 1e-9:
        raise SystemExit(
            f"{where}: report was produced at TTI={tti_ms} ms but parameters.sh "
            f"gives {env_tti_ms} ms. Set TTI={tti_ms / 1000.0} and re-run, so the "
            f"WCTTs and k1 describe the same grid the report was solved on.")
    return out


def report_radio(reports_dir, flowsets):
    """The cell every report in the tree was produced on, or an error.

    A tree mixing two radio configurations cannot be reduced to one table:
    the counts would be over candidates decided under different capacities.
    """
    seen = {}
    for name in flowsets:
        path = os.path.join(reports_dir, "ilp", name, "offline_phase_report.json")
        if not os.path.exists(path):
            continue
        c = load_json(path)["config"]
        seen.setdefault((int(c["n_rb"]), int(c["m_harq"]), float(c["tti_ms"])), []).append(name)

    if not seen:
        raise SystemExit(f"No ILP reports under {reports_dir}/ilp")
    if len(seen) > 1:
        detail = "; ".join(f"n_rb={k[0]} m_harq={k[1]} in {len(v)} flow set(s)"
                           for k, v in sorted(seen.items()))
        raise SystemExit(f"Reports were produced on more than one radio configuration "
                         f"({detail}). Re-run them under one before reducing to a table.")
    return next(iter(seen))


def run_chunk(task):
    """One slice of the tested configurations of one flow set, every method.

    The unit of work is a slice rather than a whole flow set because the flow
    sets are wildly uneven: the largest here emits 7700 candidates and the
    median 210, so a flow-set-sized task leaves one worker grinding through
    7700 x len(methods) runs long after every other core has gone idle. A
    slice caps that tail at `--chunk` candidates.
    """
    (flowset, lo, hi, reports_dir, methods, mcs_path, env, targets, bler,
     self_check, run_timeout) = task

    ilp_dir = os.path.join(reports_dir, "ilp", flowset)
    offline = load_json(os.path.join(ilp_dir, "offline_phase_report.json"))

    # Eq. (6): no configuration exists, so there is none to decide.
    if offline.get("infeasible_flows"):
        return []

    n_tested = offline.get("num_combinations_tested", 0)
    if not n_tested:
        return []
    hi = min(hi, n_tested)

    per_flow = flow_choices(load_json(os.path.join(ilp_dir, "planner_report.json")))
    ok_indices = schedulable_indices(offline)
    # Candidates the ILP started but abandoned at the per-candidate timeout.
    # They were tested, so they are inside the 1..n_tested range, but nothing
    # was proved about them either way.
    skipped_indices = set(offline.get("timed_out_combos", []))
    flowset_path = os.path.join(env["FLOWSETS_DIR"], flowset + ".json")

    # The radio configuration comes from the report being reconstructed, not
    # from parameters.sh. Those are two different things the moment anyone
    # edits parameters.sh after a run -- and then the baselines would answer
    # for a cell the ILP never solved for, which is exactly the comparison
    # this table claims to make.
    env = radio_env(env, offline["config"], flowset)

    if self_check and lo == 1:
        for entry in offline.get("schedulable", []):
            stem = os.path.splitext(os.path.basename(entry.get("schedule_file", "")))[0]
            if not stem.startswith("combo_"):
                continue
            mine = decode(int(stem[len("combo_"):]), per_flow)
            for fl in entry["flows"]:
                got = mine[fl["app"]]
                assert (got["segments"], got["m"], got["rbs"], got["attempts"], got["tbs"]) == \
                       (fl["num_segments"], fl["m"], fl["b"], fl["a"], fl["tbs"]), \
                       f"{flowset} {stem}: decoded configuration != the one the ILP recorded"

    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = os.path.join(tmp, "flow_config.json")
        out_path = os.path.join(tmp, "combo.json")
        run_env = dict(env)
        run_env["FLOW_CONFIG"] = cfg_path

        for idx in range(lo, hi + 1):
            with open(cfg_path, "w") as f:
                json.dump(decode(idx, per_flow), f)

            # The ILP's own verdict for this candidate is already on disk: it
            # emitted a schedule for the ones it proved, it proved the rest of
            # what it tested unschedulable, except the ones it abandoned at the
            # per-candidate timeout. See the timeout caveat in
            # collect_verdicts.print_config_caveats.
            if idx in ok_indices:
                ilp_verdict, ilp_evidence = "ok", "proof"
            elif idx in skipped_indices:
                ilp_verdict, ilp_evidence = "timeout", "none"
            else:
                ilp_verdict, ilp_evidence = "ko_unschedulable", "proof"
            rows.append({
                "flowset": flowset, "combo_index": idx, "method": "ilp",
                "verdict": ilp_verdict,
                "evidence": ilp_evidence,
            })

            for method in methods:
                if os.path.exists(out_path):
                    os.remove(out_path)
                try:
                    proc = subprocess.run(
                        [f"./{method}", flowset_path, mcs_path, out_path],
                        cwd=os.path.join(ROOT, "baseline_algorithms", method),
                        env=run_env, capture_output=True, text=True,
                        timeout=run_timeout or None)
                except subprocess.TimeoutExpired:
                    # Past --run-timeout: record it the way the ILP's own
                    # abandoned candidates are recorded, tested and nothing
                    # proved.
                    rows.append({"flowset": flowset, "combo_index": idx,
                                 "method": method, "verdict": "timeout",
                                 "evidence": "none"})
                    continue

                if not os.path.exists(out_path):
                    rows.append({"flowset": flowset, "combo_index": idx,
                                 "method": method, "verdict": "error",
                                 "evidence": (proc.stderr or "").strip()[:200]})
                    continue

                row = baseline_row(load_json(out_path), method, flowset, targets, bler)
                row["combo_index"] = idx
                rows.append(row)

    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reports", required=True, help="output/reports")
    ap.add_argument("--flowsets", required=True, help="output/flowsets")
    ap.add_argument("--mcs", required=True, help="input/mcs_configurations.json")
    ap.add_argument("--out", required=True, help="CSV to write")
    ap.add_argument("--methods", nargs="+", default=None,
                    help="baselines to impose the configurations on "
                         "(default: every method under --reports except ilp)")
    ap.add_argument("--exclude", nargs="+", default=[],
                    help="methods to leave out of the default list, e.g. 5gtps")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--chunk", type=int, default=100,
                    help="candidates per task; caps the tail a single huge "
                         "flow set would otherwise leave one worker grinding on")
    ap.add_argument("--run-timeout", type=float, default=0.0,
                    help="wall-clock cap in seconds on one baseline invocation "
                         "(0 = none). A run killed here is recorded as "
                         "verdict=timeout, like a candidate the ILP abandoned.")
    ap.add_argument("--limit", type=int, default=0, help="use only the first N flow sets")
    ap.add_argument("--flowset-list", type=Path, default=None,
                    help="use only the flow sets this file names (flowset_NNNN, one or more "
                         "per line), as scripts/run_paper_experiment.sh --flowsets does")
    ap.add_argument("--self-check", action="store_true",
                    help="assert every decoded configuration matches the one the "
                         "ILP recorded for that combo index")
    args = ap.parse_args()

    reports = os.path.abspath(args.reports)
    ilp_dir = os.path.join(reports, "ilp")
    if not os.path.isdir(ilp_dir):
        sys.exit(f"No ilp/ under {reports}: the candidate space comes from the "
                 f"planner, which only the ILP path runs.")

    methods = args.methods or [m for m in sorted(os.listdir(reports))
                               if m != "ilp" and os.path.isdir(os.path.join(reports, m))
                               and m not in args.exclude]

    flowsets = sorted(d for d in os.listdir(ilp_dir)
                      if os.path.isdir(os.path.join(ilp_dir, d)))
    if args.flowset_list:
        wanted = set(re.findall(r"flowset_\d+", args.flowset_list.read_text()))
        flowsets = [f for f in flowsets if f in wanted]
    if args.limit:
        flowsets = flowsets[:args.limit]

    env = base_environment()
    env["FLOWSETS_DIR"] = os.path.abspath(args.flowsets)
    targets_by_flowset = flow_targets(args.flowsets)
    bler = mcs_bler(args.mcs)
    mcs_path = os.path.abspath(args.mcs)

    tested = {}
    for name in flowsets:
        r = load_json(os.path.join(ilp_dir, name, "offline_phase_report.json"))
        tested[name] = 0 if r.get("infeasible_flows") else r.get("num_combinations_tested", 0)
    total = sum(tested.values())
    n_rb, m_harq, tti_ms = report_radio(reports, flowsets)
    for method in methods:
        for name in flowsets:
            path = os.path.join(reports, method, name, "combo_0000.json")
            if not os.path.exists(path):
                continue
            c = load_json(path).get("config", {})
            if int(c.get("n_rb", n_rb)) != n_rb or int(c.get("m_harq", m_harq)) != m_harq:
                sys.exit(f"{method}/{name} was produced at n_rb={c.get('n_rb')} "
                         f"m_harq={c.get('m_harq')}, the ILP at n_rb={n_rb} "
                         f"m_harq={m_harq}. The tree is not one experiment.")
            break

    print(f"Radio: RBs={n_rb} HARQs={m_harq} TTI={tti_ms} ms, taken from the reports "
          f"(parameters.sh says RBs={env.get('RBs')} HARQs={env.get('HARQs')})")
    print(f"{len(flowsets)} flow set(s), {total} tested configuration(s) x "
          f"{len(methods)} baseline(s) = {total * len(methods)} runs, {args.jobs} at a time")
    print(f"Methods: {' '.join(methods)}")

    chunk = max(1, args.chunk)
    tasks = [(name, lo, lo + chunk - 1, reports, methods, mcs_path, env,
              targets_by_flowset.get(name, {}), bler, args.self_check,
              args.run_timeout)
             for name in flowsets
             for lo in range(1, tested[name] + 1, chunk)]

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    written = 0
    done = 0
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        workers = max(1, min(args.jobs, len(tasks)))
        print(f"  {len(tasks)} task(s) of <= {chunk} candidate(s), "
              f"{workers} worker(s)", flush=True)
        t0 = last = time.monotonic()
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run_chunk, t) for t in tasks]
            # As they finish, not in submission order: ordered consumption
            # means the first task gates every row, so the CSV stays empty and
            # the progress line stays silent for as long as that one task runs
            # -- which is what a slow sweep looks like when it looks hung.
            for fut in as_completed(futures):
                rows = fut.result()
                for r in rows:
                    w.writerow(r)
                f.flush()
                written += len(rows)
                done += 1
                now = time.monotonic()
                if done % 50 == 0 or done == len(tasks) or now - last >= 30:
                    print(f"  {done}/{len(tasks)} tasks, {written} rows  ({now - t0:.0f} s)", flush=True)
                    last = now

    print(f"Wrote {written} rows to {args.out}")

    # Re-read rather than keep 300k rows in memory just to count the failures.
    with open(args.out) as f:
        errors = sum(1 for r in csv.DictReader(f) if r["verdict"] == "error")
    if errors:
        print(f"WARNING: {errors} run(s) produced no report; see the 'error' rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
