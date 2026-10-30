#!/usr/bin/env python3
"""Check input/app_database.json against the invariants the evaluation needs.

The catalogue is the one input the whole evaluation rests on, and three of its
properties are easy to break silently by editing a few numbers:

  * every application must be feasible on its own, or an "unschedulable"
    verdict says nothing about contention between flows;
  * C(catalogue size, flows per set) must be at least the number of flow sets,
    or the generator cannot draw them without repetition;
  * the hyperperiod of the whole period family must stay within HYPERPERIOD_MS
    (420 ms: every period divides 420 = 2^2 * 3 * 5 * 7, Goossens and Macq's
    hyperperiod limitation), or the ILP horizon explodes.

It also checks C_i + t_up <= D_i for every retained configuration (Section
V-C: C_i = A_i t_tx is how long a TB holds its HARQ process and RBs, and the
upper-layer delay t_up, which holds neither, still counts towards the
deadline) and D_i <= T_i (constrained deadlines). Periods are no longer tied to deadlines:
each application keeps its deadline, and its period is a divisor of 420 ms
between 1.6 and 2.5 times it, so that periods rarely divide one another.

Time units. Every period, deadline and TB duration is stated in milliseconds;
the scheduling grid is the TTI (0.25 ms by default, 4 TTIs per ms). The ILP
indexes time in TTIs and converts exactly only when every value is a whole
number of TTIs, and the simulated baselines record their dispatch tables in
whole milliseconds, so this script also requires every period, deadline,
retained C_i and t_up to be an integer number of milliseconds (hence a
multiple of the TTI). An earlier catalogue required 2*C_i <= D_i; the check is
now that two worst-case TBs fit before the latest end of a TB, 2*C_i <= D_i -
t_up, and it is reported as a note only.

Parameters. The radio and timing values (TTI, FEEDBACK_PER_RET,
UPPER_LAYER_DELAY, A_MAX, RLC_HEADER_BYTES, SINR_DB) and the default catalogue,
MCS table, flow-set count and flows per set are those of scripts/parameters.sh,
overrides in the environment included, as for every other component.

    python3 scripts/check_catalogue.py                 # everything from parameters.sh
    A_MAX=3 python3 scripts/check_catalogue.py         # an override, as for a run
    python3 scripts/check_catalogue.py --sets 500 --apps-per-set 5
    python3 scripts/check_catalogue.py --table          # print the paper's table rows

Exits non-zero if any invariant fails, so it can gate a run.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from functools import reduce
from pathlib import Path

HERE = Path(__file__).resolve().parent


def parameters() -> dict:
    """The environment scripts/parameters.sh produces, with any overrides the
    caller's environment holds: the values every component runs with."""
    out = subprocess.run(
        ["bash", "-c", 'source "$1" >/dev/null 2>&1 && env -0', "bash",
         str(HERE / "parameters.sh")],
        capture_output=True, text=True, check=True).stdout
    return dict(line.split("=", 1) for line in out.split("\0") if "=" in line)


ENV = parameters()
# t_tx = TTI + FEEDBACK_PER_RET; C = A*t_tx; admissible when C + t_up <= D.
# Computed as the planner computes them (seconds * 1000, summed), so the test
# is decided on the same numbers.
TTI_MS = float(ENV["TTI"]) * 1000.0
TX_MS = TTI_MS + float(ENV["FEEDBACK_PER_RET"]) * 1000.0
UP_MS = float(ENV["UPPER_LAYER_DELAY"]) * 1000.0
RLC_HEADER_B = int(ENV["RLC_HEADER_BYTES"])
A_MAX = int(ENV["A_MAX"])
SINR_DB = float(ENV["SINR_DB"])
HYPERPERIOD_MS = 420   # bound on the lcm of the catalogue's period family

# The paper groups the catalogue by reliability target; keep in step with
# RELIABILITY_CLASS in analysis/plot_evaluation.py.
RELIABILITY_CLASS = {
    1e-3: "Monitoring and tracking",
    1e-4: "Discrete automation",
    1e-5: "Motion and process control",
    1e-6: "Precision and safety",
    1e-8: "High reliability",
}


def num_segments(packet_b: int, tbs_b: int, header_b: int = RLC_HEADER_B) -> int:
    if tbs_b <= header_b:
        return -1
    payload = tbs_b - header_b
    return max(1, -(-packet_b // payload))


def per_tb_target(packet_error: float, segments: int) -> float:
    if segments <= 1:
        return packet_error
    return -math.expm1(math.log1p(-packet_error) / segments)


def required_attempts(target: float, bler: float, a_max: int = A_MAX) -> int:
    """Smallest A with bler**A <= target, or -1 if none within a_max."""
    if not (target > 0.0) or not (0.0 < bler < 1.0):
        return -1
    attempts = 1
    if bler > target:
        attempts = max(1, math.ceil(math.log(target) / math.log(bler)))
    while attempts > 1 and bler ** (attempts - 1) <= target:
        attempts -= 1
    while attempts <= a_max and bler**attempts > target:
        attempts += 1
    return attempts if attempts <= a_max else -1


def pareto(app: dict, tables: list) -> list:
    """Section V: admissible configurations of one flow, Pareto-filtered."""
    packet_b = app["packetsize"]
    cands = {}
    for table in tables:
        segs = num_segments(packet_b, table["tbs"])
        if segs < 0:
            continue
        target = per_tb_target(app["error"], segs)
        for row in table["mcs"]:
            attempts = required_attempts(target, row["bler"])
            if attempts < 0:
                continue
            wctt = attempts * TX_MS
            if wctt + UP_MS > app["deadline"]:
                continue
            key = (segs, attempts, row["b"])
            cand = {"tbs": table["tbs"], "m": row["m"], "N": segs,
                    "A": attempts, "B": row["b"], "C": wctt, "bler": row["bler"]}
            if key not in cands or cand["bler"] < cands[key]["bler"]:
                cands[key] = cand

    uniq = list(cands.values())

    def dominates(a, b):
        if a["N"] > b["N"] or a["A"] > b["A"] or a["B"] > b["B"]:
            return False
        return a["N"] < b["N"] or a["A"] < b["A"] or a["B"] < b["B"]

    keep = [c for c in uniq if not any(dominates(o, c) for o in uniq if o is not c)]
    return sorted(keep, key=lambda c: (c["N"], c["A"], c["B"], c["m"]))


def span(values) -> str:
    values = list(values)
    if not values:   # no admissible configuration at all
        return "-"
    lo, hi = min(values), max(values)
    return str(lo) if lo == hi else f"{lo}--{hi}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apps", type=Path, default=Path(ENV["APP_DATABASE"]),
                    help="catalogue (default: APP_DATABASE)")
    ap.add_argument("--mcs", type=Path, default=Path(ENV["MCS_CONFIG"]),
                    help="MCS table (default: MCS_CONFIG)")
    ap.add_argument("--sets", type=int, default=int(ENV["NUMBER_OF_APPSETS"]),
                    help="flow sets to be drawn (default: NUMBER_OF_APPSETS)")
    ap.add_argument("--apps-per-set", type=int, default=int(ENV["APP_PER_SET"]),
                    help="flows per set (default: APP_PER_SET)")
    ap.add_argument("--table", action="store_true",
                    help="print the paper's catalogue table rows and exit")
    args = ap.parse_args()

    apps = json.loads(args.apps.read_text())
    tables = [t for t in json.loads(args.mcs.read_text())
              if abs(t["sinr"] - SINR_DB) <= 1e-9]
    if not tables:
        print(f"{args.mcs}: no MCS configurations at SINR_DB={SINR_DB:g} dB")
        return 1
    sets_ = {name: pareto(app, tables) for name, app in apps.items()}

    if args.table:
        print(f"{'Reliability class':28} {'E_i':8} {'#':>2} {'|P|':>6} "
              f"{'N':>6} {'A':>5} {'B':>8}")
        for err in sorted(RELIABILITY_CLASS, reverse=True):
            members = [n for n, a in apps.items() if a["error"] == err]
            if not members:
                continue
            cfgs = [c for n in members for c in sets_[n]]
            print(f"{RELIABILITY_CLASS[err]:28} {err:<8.0e} {len(members):>2} "
                  f"{span([len(sets_[n]) for n in members]):>6} "
                  f"{span([c['N'] for c in cfgs]):>6} "
                  f"{span([c['A'] for c in cfgs]):>5} "
                  f"{span([c['B'] for c in cfgs]):>8}")
        return 0

    problems = []

    empty = [n for n, p in sets_.items() if not p]
    if empty:
        problems.append(f"no admissible configuration (infeasible alone): {empty}")

    late = [n for n, p in sets_.items()
            if p and max(c["C"] for c in p) + UP_MS > apps[n]["deadline"]]
    if late:
        problems.append(f"C_i + t_up > D_i for a retained configuration: {late}")
    slack = [n for n, p in sets_.items()
             if p and 2 * max(c["C"] for c in p) > apps[n]["deadline"] - UP_MS]
    if slack:
        print(f"note: less than one WCTT of slack (2*C_i > D_i - t_up) for {len(slack)} "
              f"application(s): {slack}")

    def whole_ms(x):
        return abs(x - round(x)) < 1e-9

    not_ms = [n for n, a in apps.items()
              if not (whole_ms(a["period"]) and whole_ms(a["deadline"]))]
    if not_ms:
        problems.append(f"period or deadline is not a whole number of ms: {not_ms}")
    c_not_ms = [n for n, p in sets_.items() if any(not whole_ms(c["C"]) for c in p)]
    if c_not_ms:
        problems.append(f"a retained C_i is not a whole number of ms: {c_not_ms}")
    if not whole_ms(UP_MS):
        problems.append(f"t_up = {UP_MS} ms (UPPER_LAYER_DELAY) is not a whole number of ms")
    if not whole_ms(1.0 / TTI_MS):
        problems.append(f"TTI_MS = {TTI_MS} ms does not divide 1 ms")

    constrained = [n for n, a in apps.items() if a["deadline"] > a["period"]]
    if constrained:
        problems.append(f"D_i > T_i, outside the constrained-deadline model: {constrained}")

    combos = math.comb(len(apps), args.apps_per_set)
    if combos < args.sets:
        problems.append(
            f"only C({len(apps)},{args.apps_per_set}) = {combos} distinct combinations, "
            f"fewer than the {args.sets} flow sets requested")

    periods_tti = sorted({round(a["period"] / TTI_MS) for a in apps.values()})
    hyper = reduce(lambda x, y: x * y // math.gcd(x, y), periods_tti)
    if hyper * TTI_MS > HYPERPERIOD_MS + 1e-9:
        problems.append(
            f"periods {periods_tti} TTIs have hyperperiod {hyper} TTIs = "
            f"{hyper * TTI_MS:.0f} ms, above the {HYPERPERIOD_MS} ms bound")

    print(f"catalogue            {args.apps}")
    print(f"MCS tables           {args.mcs} at {SINR_DB:g} dB")
    print(f"applications         {len(apps)}")
    print(f"reliability classes  "
          f"{sorted({a['error'] for a in apps.values()}, reverse=True)}")
    print(f"deadlines (ms)       {sorted({a['deadline'] for a in apps.values()})}")
    print(f"periods (ms)         {sorted({a['period'] for a in apps.values()})}")
    print(f"Pareto set sizes     {span([len(p) for p in sets_.values()])}")
    worst_c = [c["C"] for p in sets_.values() for c in p]
    print(f"worst-case C (ms)    {f'{max(worst_c):g}' if worst_c else '-'} "
          f"(A^max = {A_MAX}, t_tx = {TX_MS:g} ms; plus t_up = {UP_MS:g} ms towards D, "
          f"RLC header {RLC_HEADER_B} B)")
    print(f"combinations         C({len(apps)},{args.apps_per_set}) = {combos} "
          f"for {args.sets} flow sets")
    print(f"hyperperiod bound    {hyper} TTIs = {hyper * TTI_MS:.0f} ms")

    candidates = sorted(math.prod(len(sets_[n]) for n in combo)
                        for combo in __import__("itertools").combinations(sets_, args.apps_per_set))
    mid = candidates[len(candidates) // 2]
    print(f"candidates per set   median {mid}, max {candidates[-1]} "
          f"(ILP sweeps all of them)")

    if problems:
        print("\nFAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nall invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
