#!/usr/bin/env python3
"""Independently re-checks a dispatch-table JSON against all ILP constraints:
   - Eq. (7)  each job scheduled exactly once, within its window: it starts
              after its release and ends t_up before its packet's deadline
              (the packet is delivered t_up after its last TB ends)
   - Eq. (8)  no two jobs overlap on the same machine
   - Eq. (9)  resource blocks of concurrently-running jobs never exceed B^max
   - Eq. (10) within a release group, subtask a starts <= subtask a+1

"Exactly once" is checked against the table's own flows: every release of
every flow in [0, H) must hold subtasks 0 .. N-1, each once.

Works in TTIs, the discrete time unit of the model: the *_tti fields are the
integers the solver actually decided on, whereas the *_ms fields alongside
them are a rescaling for display and may be fractional. end_tti is when a TB
releases its HARQ process and RBs; config.t_up_tti (0 in tables written
before it existed, whose end_tti included t_up) is added to it for the
deadline.
"""
import json, sys, glob
from collections import defaultdict

def validate(path):
    d = json.load(open(path))
    n_rb = d["config"]["n_rb"]
    m_harq = d["config"]["m_harq"]
    t_up = d["config"].get("t_up_tti", 0)
    table = d["dispatch_table"]
    H = d["hyperperiod_tti"]

    errors = []

    # deadline / release checks
    for e in table:
        if e["start_tti"] < e["release_tti"]:
            errors.append(f"{e['app']} starts before release: {e}")
        if e["end_tti"] + t_up > e["deadline_tti"]:
            errors.append(f"{e['app']} delivered after deadline (end + t_up = "
                          f"{e['end_tti'] + t_up}): {e}")
        if not (0 <= e["machine"] < m_harq):
            errors.append(f"{e['app']} invalid machine id: {e}")

    # every job exactly once: each release of each flow holds subtasks 0..N-1
    placed = defaultdict(list)
    for e in table:
        placed[(e["app"], e["release_tti"])].append(e["subtask"])
    expected = set()
    for f in d.get("flows", []):
        for r in range(0, H, f["period_tti"]):
            expected.add((f["app"], r))
            got = sorted(placed.get((f["app"], r), []))
            if got != list(range(f["num_segments"])):
                errors.append(f"{f['app']} release {r}: subtasks {got}, "
                              f"expected 0..{f['num_segments'] - 1} once each")
    for key in placed.keys() - expected:
        errors.append(f"{key[0]} release {key[1]}: not a release of a listed flow")

    # per-machine overlap check
    by_machine = defaultdict(list)
    for e in table:
        by_machine[e["machine"]].append(e)
    for mach, jobs in by_machine.items():
        jobs.sort(key=lambda x: x["start_tti"])
        for i in range(len(jobs) - 1):
            if jobs[i]["end_tti"] > jobs[i+1]["start_tti"]:
                errors.append(f"machine {mach} overlap: {jobs[i]} vs {jobs[i+1]}")

    # resource capacity check: sweep over every TTI in [0,H)
    for t in range(H):
        active_b = sum(e["b"] for e in table if e["start_tti"] <= t < e["end_tti"])
        if active_b > n_rb:
            errors.append(f"t={t}: resource usage {active_b} > n_rb={n_rb}")

    # symmetry breaking: group by (app, release_tti), subtask order -> start order
    groups = defaultdict(list)
    for e in table:
        groups[(e["app"], e["release_tti"])].append(e)
    for key, jobs in groups.items():
        jobs.sort(key=lambda x: x["subtask"])
        for i in range(len(jobs) - 1):
            if jobs[i]["start_tti"] > jobs[i+1]["start_tti"]:
                errors.append(f"symmetry violation in {key}: {jobs[i]} starts after {jobs[i+1]}")

    return errors

if __name__ == "__main__":
    paths = sys.argv[1:] if len(sys.argv) > 1 else glob.glob("schedules/*.json")
    total_errors = 0
    for p in paths:
        errs = validate(p)
        if errs:
            total_errors += len(errs)
            print(f"FAIL {p}:")
            for e in errs[:5]:
                print("   ", e)
    print(f"\nChecked {len(paths)} files, {total_errors} total constraint violations.")
