#!/usr/bin/env python3
"""RQ4: ILP solving time against the hyperperiod.

Each instance is one ILP model: a flowset with one configuration per flow,
whose periods are stretched to reach a target hyperperiod H*. Every flow keeps
its payload, error target, deadline and configuration; only its period grows,
so the solving time is compared across hyperperiods on otherwise identical
models.

  * Configuration: by default (--tuple sweep) the tuple the RB-budget sweep
    proved schedulable for the flowset, its minimum-peak table (the one
    collect_verdicts.py reports), read from the sweep's ILP report at one
    budget (--reports: rb51, the budget complete first); only flowsets with
    such a table are sampled, so RQ4 times building a table. The instance
    records that budget (n_rb), and run solves it there, so the RB limit is as
    tight as where the tuple was proved. --tuple lowest-mean takes instead
    the tuple of lowest mean RB usage (sum of N*B*C/T at the original
    periods), where the bounded rb_peak search starts; it needs no sweep but
    is often infeasible, and run then needs --n-rb. Either way it is the same
    tuple at every H*.
  * Periods: Goossens and Macq's hyperperiod limitation. Every new period is
    a divisor of H*, at least the original period and at most --stretch times
    it, and together they have lcm exactly H*. Among such choices the one with
    the smallest largest stretch wins, then the smallest total stretch. A
    flowset with no such choice for some H* skips that H*. The deadline is
    unchanged, so D < T' (constrained deadlines).
  * Solve: the feasibility objective, on one thread (as the ILP always
    runs), one model per instance under --cap seconds. A model that hits the
    cap is a timeout. The solver is the binary `run --solver` names:
    offline_phase/siren/siren (Gurobi).

Usage (from the repository root):

    python3 analysis/rq4_hyperperiod.py prepare             # instances in output/rq4/
    python3 analysis/rq4_hyperperiod.py run --solver offline_phase/siren/siren -j 32   # solve; resumable
    python3 analysis/rq4_hyperperiod.py collect             # analysis/csv/rq4_hyperperiod.csv

    # control: a sample solved again alone, on an otherwise idle machine
    python3 analysis/rq4_hyperperiod.py control-prepare --host <host>   # output/rq4/control/
    python3 analysis/rq4_hyperperiod.py --out output/rq4/control run --solver offline_phase/siren/siren -j 1
    python3 analysis/rq4_hyperperiod.py compare             # analysis/csv/rq4_control.csv

The largest hyperperiods need several GB per model: size -j to the machine's
memory (--max-h limits a run to the smaller ones). To share the models out
over N machines that see the same --out directory, run `run --shard K/N` on
the K-th one: the shares are disjoint and together cover every instance.
Each result records the machine that solved it (`host`).
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import itertools
import json
import math
import os
import random
import re
import shutil
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLANNER = ROOT / "offline_phase" / "planning" / "planner"
ILP = ROOT / "offline_phase" / "evaluation" / "ilp" / "ilp_evaluation"
SIREN = ROOT / "offline_phase" / "evaluation" / "siren" / "siren"
OUT = ROOT / "output" / "rq4"
CSV = ROOT / "analysis" / "csv" / "rq4_hyperperiod.csv"
TARGETS = [420, 840, 2520, 8400, 25200]   # ms; multiples of 420 (each catalogue period divides 420)
# The ILP's per-candidate progress line (see analysis/plot_paper.py).
# The ILP prints the time with the stream's default format, so from 1e6 ms
# (1000 s) on in scientific notation ("2.98654e+06 ms").
LINE = re.compile(r"^\[\d+/\d+\] .* -> (SCHEDULABLE|not schedulable|timed out(?:, skipped)?"
                  r"|solver error|infeasible \(window\)|infeasible \(D > T\))"
                  r"(?: \(([\d.]+(?:e[+-]?\d+)?) ms\))?")


def parameters() -> dict:
    """The environment scripts/parameters.sh produces, overrides included."""
    out = subprocess.run(
        ["bash", "-c", 'source "$1" >/dev/null 2>&1 && env -0', "bash",
         str(ROOT / "scripts" / "parameters.sh")],
        capture_output=True, text=True, check=True).stdout
    return dict(line.split("=", 1) for line in out.split("\0") if "=" in line)


def lcm(values) -> int:
    h = 1
    for v in values:
        h = h * v // math.gcd(h, v)
    return h


def tti_ms(env: dict) -> float:
    return float(env["TTI"]) * 1000.0


def ticks(ms: float, tti: float, how: str) -> int:
    """Milliseconds to TTIs as the ILP converts them (scheduler.cpp)."""
    x = ms / tti
    if how == "period":
        return round(x)
    if how == "deadline":
        return math.floor(x + 1e-9)
    return math.ceil(x - 1e-9)                      # execution time


# ---------------------------------------------------------------- prepare ----

def choices(app: dict) -> list:
    """Every (segmentation option, MCS combination) of a planner application,
    in the ILP's odometer order."""
    return [(so, mc) for so in app["segmentation_options"] for mc in so["mbc_combinations"]]


def lowest_mean_tuple(apps: list, tti: float) -> list:
    """Per flow, the choice of least N*B*C/T (in TTIs, as the ILP's meanRbUsage):
    the sum is separable, so the per-flow minima form the lowest-mean tuple, and
    taking the first minimum of each flow gives the odometer-first one."""
    picked = []
    for app in apps:
        t = ticks(app["period"], tti, "period")
        best = min(choices(app), key=lambda c: c[0]["num_segments"] * c[1]["b"]
                   * ticks(c[1]["c"], tti, "wcet") / t)
        picked.append(best)
    return picked


def stretch_periods(periods: list, target: int, stretch: float):
    """Divisors of `target`, one per flow in [T, stretch*T], with lcm == target;
    None when there is none."""
    divs = [d for d in range(1, target + 1) if target % d == 0]
    cands = [[d for d in divs if t <= d <= stretch * t] for t in periods]
    best = None
    for combo in itertools.product(*cands):
        if lcm(combo) != target:
            continue
        ratios = [c / t for c, t in zip(combo, periods)]
        key = (max(ratios), sum(ratios), combo)
        if best is None or key < best[0]:
            best = (key, combo)
    return list(best[1]) if best else None


def t_up_of(report: Path, key: str) -> float:
    """The upper-layer delay t_up (ms) a report was produced with, which the
    instance has to carry: the ILP refuses an input without it. `key` is
    "config" for the ILP's report, "parameters" for the planner's."""
    t_up = json.load(open(report)).get(key, {}).get("t_up_ms")
    if t_up is None:
        sys.exit(f"{report}: no {key}.t_up_ms; it predates the upper-layer delay leaving the "
                 "TB occupancy, so its C includes t_up. Use the reports of a current sweep.")
    return float(t_up)


def sweep_tuple(report: Path):
    """The flowset's minimum-peak schedulable tuple from the sweep's ILP report
    (the first schedulable one if none carries a peak), as single-option
    planner applications; None if the sweep found no table."""
    combos = json.load(open(report)).get("schedulable", [])
    if not combos:
        return None
    with_peak = [c for c in combos if c.get("rb_peak_used") is not None]
    best = min(with_peak, key=lambda c: c["rb_peak_used"]) if with_peak else combos[0]
    return [{"app": f["app"], "deadline": f["deadline"], "period": f["period"],
             "segmentation_options": [{"num_segments": f["num_segments"], "tbs": f["tbs"],
                                       "mbc_combinations": [{"m": f["m"], "b": f["b"],
                                                             "c": f["c"], "a": f["a"]}]}]}
            for f in best["flows"]]


def planner_tuple(f: Path, d: Path, env: dict, tti: float):
    """The lowest mean-RB tuple of the flowset, from a planner run, as
    single-option planner applications; None if a flow has no configuration."""
    report = d / "planner_report.json"
    if not report.exists():
        r = subprocess.run([str(PLANNER), str(f), env["MCS_CONFIG"], str(report)],
                           env=env, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"planner failed on {f.name}:\n{r.stderr}")
    apps = json.load(open(report))["applications"]
    if not all(choices(a) for a in apps):
        return None
    return [{**{k: a[k] for k in a if k != "segmentation_options"},
             "segmentation_options": [{**{k: so[k] for k in so if k != "mbc_combinations"},
                                       "mbc_combinations": [mc]}]}
            for a, (so, mc) in zip(apps, lowest_mean_tuple(apps, tti))]


def prepare(args) -> int:
    env = parameters()
    env.update({k: v for k, v in os.environ.items()})
    tti = tti_ms(env)
    files = sorted(Path(args.flowsets).glob("flowset_*.json"))
    if not files:
        sys.exit(f"no flowset_*.json under {args.flowsets}")
    reports = Path(args.reports)
    if args.tuple == "sweep":
        # Only the flowsets the sweep built a table for.
        files = [f for f in files if (reports / f.stem / "offline_phase_report.json").exists()
                 and json.load(open(reports / f.stem / "offline_phase_report.json")).get("schedulable")]
        if not files:
            sys.exit(f"no ILP report with a schedulable tuple under {reports}")
    sample = sorted(random.Random(args.seed).sample(files, min(args.sample, len(files))))
    print(f"{len(sample)} flowsets sampled from {len(files)} ({args.tuple} tuple)")
    args.out.mkdir(parents=True, exist_ok=True)
    made = skipped = 0
    for f in sample:
        d = args.out / f.stem
        d.mkdir(exist_ok=True)
        apps = (sweep_tuple(reports / f.stem / "offline_phase_report.json") if args.tuple == "sweep"
                else planner_tuple(f, d, env, tti))
        if apps is None:
            print(f"  {f.stem}: no tuple, skipped")
            skipped += 1
            continue
        t_up = (t_up_of(reports / f.stem / "offline_phase_report.json", "config")
                if args.tuple == "sweep" else t_up_of(d / "planner_report.json", "parameters"))
        n_rb = (json.load(open(reports / f.stem / "offline_phase_report.json"))["config"]["n_rb"]
                if args.tuple == "sweep" else None)
        periods = [int(round(a["period"])) for a in apps]
        for target in args.targets:
            if not write_instance(d, apps, periods, target, args.stretch, t_up, n_rb):
                print(f"  {f.stem}: no periods within x{args.stretch} reach H = {target} ms")
                skipped += 1
                continue
            made += 1
    print(f"{made} instances in {args.out} ({skipped} skipped)")
    return 0


def write_instance(d: Path, apps: list, periods: list, target: int, stretch: float,
                   t_up: float, n_rb) -> bool:
    """d/H<target>.json: apps, their periods (ms, originally `periods`)
    stretched to reach the hyperperiod target; False if no stretch within
    `stretch` does."""
    new = stretch_periods(periods, target, stretch)
    if new is None:
        return False
    inst = {
        "rq4": {"flowset": d.name, "target_h_ms": target, "h_ms": lcm(new),
                "periods_ms": new, "original_periods_ms": periods,
                "deadlines_ms": [a["deadline"] for a in apps],
                "max_stretch": max(n / p for n, p in zip(new, periods)), "n_rb": n_rb},
        "parameters": {"t_up_ms": t_up},
        "applications": [{**a, "period": float(p)} for a, p in zip(apps, new)],
    }
    json.dump(inst, open(d / f"H{target}.json", "w"), indent=1)
    return True


def extend(args) -> int:
    """Adds --targets to the flowsets prepared in --out, from one of their
    models (same tuple, deadlines, budget and original periods), so that the
    sample stays the one prepare drew, whatever the sweep holds by now."""
    made = skipped = 0
    for d in sorted(p for p in args.out.glob("flowset_*") if p.is_dir()):
        models = sorted(p for p in d.glob("H*.json") if re.fullmatch(r"H\d+\.json", p.name))
        if not models:
            continue
        m = json.load(open(models[0]))
        for target in args.targets:
            if (d / f"H{target}.json").exists():
                continue
            if write_instance(d, m["applications"], m["rq4"]["original_periods_ms"], target,
                              args.stretch, m["parameters"]["t_up_ms"], m["rq4"].get("n_rb")):
                made += 1
            else:
                print(f"  {d.name}: no periods within x{args.stretch} reach H = {target} ms")
                skipped += 1
    print(f"{made} instances added in {args.out} ({skipped} skipped)")
    return 0


# -------------------------------------------------------------------- run ----

def solve(inst: Path, n_rb: int, args, env: dict) -> str:
    res = inst.with_suffix(".result.json")
    if res.exists() and not args.force:
        return f"{inst.parent.name}/{inst.name}: done"
    out = inst.with_suffix(".ilp.json")
    cmd = [str(args.solver), str(inst), "--n-rb", str(n_rb), "--m-harq", str(args.harqs),
           "--tti", repr(tti_ms(env)), "--objective", "feasibility", "--timeout", "0",
           "--candidate-timeout", str(args.cap), "--no-schedules", "--output-scheduling", str(out)]
    timer = shutil.which("time") or "/usr/bin/time"
    use_timer = os.path.exists(timer)
    if use_timer:
        cmd = [timer, "-f", "RQ4TIME %e %M"] + cmd
    t0 = time.monotonic()
    p = subprocess.run(cmd, capture_output=True, text=True)
    wall = time.monotonic() - t0
    status, solve_ms, peak_kb = "error", None, None
    for line in p.stderr.splitlines():
        m = LINE.match(line)
        if m:
            word = m.group(1)
            status = ("schedulable" if word == "SCHEDULABLE" else "infeasible" if word == "not schedulable"
                      else "timeout" if word.startswith("timed out")
                      else "error" if word == "solver error" else "infeasible_fast")
            solve_ms = float(m.group(2)) if m.group(2) else None
        if line.startswith("RQ4TIME "):
            _, e, k = line.split()
            wall, peak_kb = float(e), int(k)
    if status == "timeout":
        solve_ms = args.cap * 1000.0
    result = {"status": status, "solve_s": None if solve_ms is None else solve_ms / 1000.0,
              "wall_s": wall, "peak_mem_mb": None if peak_kb is None else peak_kb / 1024.0,
              "exit_code": p.returncode, "cap_s": args.cap, "n_rb": n_rb, "m_harq": args.harqs,
              "host": socket.gethostname().split(".")[0], "solver": Path(args.solver).name}
    if p.returncode != 0 or status == "error":
        result["stderr_tail"] = p.stderr.strip().splitlines()[-5:]
    json.dump(result, open(res, "w"), indent=1)
    out.unlink(missing_ok=True)
    return f"{inst.parent.name}/{inst.name}: {status} {result['solve_s']}"


def parse_shard(spec: str) -> tuple:
    """'K/N' -> (K, N), with 1 <= K <= N."""
    m = re.fullmatch(r"(\d+)/(\d+)", spec)
    if not m or not 1 <= int(m.group(1)) <= int(m.group(2)):
        sys.exit(f"--shard {spec}: expected K/N with 1 <= K <= N")
    return int(m.group(1)), int(m.group(2))


def run(args) -> int:
    env = parameters()
    env.update({k: v for k, v in os.environ.items()})
    k, n = parse_shard(args.shard)
    every = sorted(p for p in args.out.glob("flowset_*/H*.json")
                   if re.fullmatch(r"H\d+\.json", p.name))
    # Largest models first, so the long ones do not trail at the end.
    every.sort(key=lambda p: -int(p.stem[1:]))
    # The shares are dealt over every instance before --max-h applies, so a
    # machine's share does not depend on --max-h: a later run with another
    # --max-h never picks up a model that a different machine is still
    # solving. Dealing in size order keeps the shares alike in work.
    insts = [p for i, p in enumerate(every) if i % n == k - 1 and int(p.stem[1:]) <= args.max_h]
    # B^max: --n-rb, else the budget prepare recorded (that of the tuple's sweep).
    n_rb = {p: args.n_rb or json.load(open(p))["rq4"].get("n_rb") for p in insts}
    if None in n_rb.values():
        sys.exit("some instances record no budget (prepare --tuple lowest-mean): pass --n-rb")
    print(f"{len(insts)} instances (share {k}/{n} of {len(every)}), -j {args.jobs}, "
          f"cap {args.cap} s, B^max {'/'.join(map(str, sorted(set(n_rb.values()))))}, M {args.harqs}")
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for line in ex.map(lambda p: solve(p, n_rb[p], args, env), insts):
            print(" ", line, flush=True)
    return 0


# ---------------------------------------------------------------- collect ----

def model_size(inst: dict, m_harq: int, tti: float) -> dict:
    """Jobs, binaries and rows of the feasibility model, as scheduler.cpp builds
    it: one binary per (job, process, start in [r, d - t_up - C]); rows: one per
    job, one per (process, covered TTI), one per covered TTI for the RBs, and
    N - 1 symmetry rows per release. Below, d is the deadline less t_up: the
    latest end of a TB."""
    apps = inst["applications"]
    t_up = ticks(inst["parameters"]["t_up_ms"], tti, "wcet")
    tasks = []
    for a in apps:
        so = a["segmentation_options"][0]
        tasks.append((ticks(a["period"], tti, "period"),
                      ticks(a["deadline"], tti, "deadline") - t_up,
                      ticks(so["mbc_combinations"][0]["c"], tti, "wcet"), so["num_segments"]))
    h = lcm(t for t, _, _, _ in tasks)
    jobs = binaries = sym = 0
    covered = bytearray(h)
    for t, d, c, n in tasks:
        reps = h // t
        jobs += reps * n
        binaries += reps * n * m_harq * max(0, d - c + 1)
        sym += reps * (n - 1)
        if d >= c:
            for r in range(reps):
                covered[r * t: r * t + d] = b"\x01" * d
    cov = sum(covered)
    return {"h_tti": h, "n_jobs": jobs, "n_binaries": binaries,
            "n_constraints": jobs + m_harq * cov + cov + sym}


def collect(args) -> int:
    env = parameters()
    tti = tti_ms(env)
    rows = []
    for inst_path in sorted(args.out.glob("flowset_*/H*.json")):
        if not re.fullmatch(r"H\d+\.json", inst_path.name):
            continue
        res_path = inst_path.with_suffix(".result.json")
        if not res_path.exists():
            continue
        inst, res = json.load(open(inst_path)), json.load(open(res_path))
        meta = inst["rq4"]
        # A model decided in 1000 s or more before LINE read "1.2e+06 ms" has
        # no solve_s: the process's wall time stands in, which exceeds it only
        # by the model's building (under 1 s up to 8.4 s, ~2 s at 25.2 s).
        solve_s, source = res.get("solve_s"), "solver"
        if solve_s is None and res["status"] in ("schedulable", "infeasible"):
            solve_s, source = res["wall_s"], "wall"
        rows.append({"flowset": meta["flowset"], "target_h_ms": meta["target_h_ms"], "h_ms": meta["h_ms"],
                     "periods_ms": ";".join(map(str, meta["periods_ms"])),
                     "original_periods_ms": ";".join(map(str, meta["original_periods_ms"])),
                     "deadlines_ms": ";".join(f"{x:g}" for x in meta["deadlines_ms"]),
                     "max_stretch": round(meta["max_stretch"], 4),
                     **model_size(inst, res["m_harq"], tti),
                     "status": res.get("status"), "solve_s": solve_s, "solve_s_source": source,
                     **{k: res.get(k) for k in ("wall_s", "peak_mem_mb",
                                                "cap_s", "n_rb", "m_harq", "exit_code", "host", "solver")}})
    if not rows:
        sys.exit(f"no results under {args.out}")
    with open(args.csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    by = {}
    for r in rows:
        by.setdefault(r["target_h_ms"], []).append(r)
    for h in sorted(by):
        rs = by[h]
        st = {s: sum(r["status"] == s for r in rs) for s in ("schedulable", "infeasible", "timeout", "error")}
        print(f"  H* = {h:6} ms: {len(rs):4} models  {st}")
    hosts = {}
    for r in rows:
        hosts[r["host"] or "unknown"] = hosts.get(r["host"] or "unknown", 0) + 1
    print("  solved on: " + ", ".join(f"{h} {c}" for h, c in sorted(hosts.items())))
    wall = sum(r["solve_s_source"] == "wall" for r in rows)
    if wall:
        print(f"  {wall} decided model(s) timed by their wall time (solve_s_source = wall)")
    print(f"wrote {args.csv} ({len(rows)} rows)")
    return 0


# ---------------------------------------------------------------- control ----
# RQ4 solves many models at a time per machine, each on its own core but
# sharing L3 caches and memory bandwidth. The control solves a sample of them
# again one at a time on an otherwise idle machine (`run -j 1` on --control):
# the ratio of the two times is what the concurrency cost.

def control_prepare(args) -> int:
    """Copies --per-h decided models per target (seeded), solved on --host
    within --max-solve s, into --control."""
    if any(args.control.glob("flowset_*/H*.json")):
        sys.exit(f"{args.control} already holds models: compare them, or move it aside first")
    by = {}
    for res in sorted(args.out.glob("flowset_*/H*.result.json")):
        if not res.with_name(res.name.replace(".result.json", ".json")).exists():
            continue   # a model dropped since (the 240 ms ones)
        r = json.load(open(res))
        if (r["status"] in ("schedulable", "infeasible") and r["solve_s"] is not None
                and r["solve_s"] <= args.max_solve
                and (args.host is None or r.get("host") == args.host)):
            by.setdefault(int(res.name[1:].split(".")[0]), []).append(res)
    rng = random.Random(args.seed)
    for h in sorted(by):
        for res in sorted(rng.sample(by[h], min(args.per_h, len(by[h])))):
            inst = res.with_name(res.name.replace(".result.json", ".json"))
            (args.control / inst.parent.name).mkdir(parents=True, exist_ok=True)
            shutil.copy(inst, args.control / inst.parent.name / inst.name)
            print(f"  {inst.parent.name}/{inst.name}: {json.load(open(res))['solve_s']:.1f} s in RQ4")
    n = len(list(args.control.glob("flowset_*/H*.json")))
    print(f"{n} models in {args.control}: solve them with --out {args.control} run -j 1")
    return 0


def compare(args) -> int:
    """RQ4's time of each control model against the control's."""
    rows = []
    for res in sorted(args.control.glob("flowset_*/H*.result.json")):
        c, o = json.load(open(res)), json.load(open(args.out / res.relative_to(args.control)))
        rows.append({"flowset": res.parent.name, "target_h_ms": int(res.name[1:].split(".")[0]),
                     "status_rq4": o["status"], "status_control": c["status"],
                     "solve_s_rq4": o["solve_s"], "solve_s_control": c["solve_s"],
                     "ratio": o["solve_s"] / c["solve_s"] if c["solve_s"] else None,
                     "host_rq4": o.get("host"), "host_control": c.get("host")})
    if not rows:
        sys.exit(f"no control results under {args.control}")
    with open(args.csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        same = "" if r["status_rq4"] == r["status_control"] else f"  (control: {r['status_control']})"
        print(f"  {r['flowset']} H*={r['target_h_ms']:6}: RQ4 {r['solve_s_rq4']:8.1f} s, "
              f"alone {r['solve_s_control']:8.1f} s, x{r['ratio']:.2f}{same}")
    ratios = sorted(r["ratio"] for r in rows if r["ratio"])
    print(f"RQ4 / alone: median x{statistics.median(ratios):.2f}, from x{ratios[0]:.2f} "
          f"to x{ratios[-1]:.2f} over {len(ratios)} models; wrote {args.csv}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT, help="instance and result directory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="sample flowsets, fix a tuple, stretch the periods")
    p.add_argument("--flowsets", default=str(ROOT / "output" / "flowsets"))
    p.add_argument("--tuple", choices=["sweep", "lowest-mean"], default="sweep",
                   help="configuration fixed per flowset (default: the sweep's table)")
    p.add_argument("--reports", default=str(ROOT / "output" / "sweep" / "rb51" / "reports" / "ilp"),
                   help="the sweep's ILP reports at one budget, <flowset>/offline_phase_report.json "
                        "(default rb51)")
    p.add_argument("--sample", type=int, default=100, help="flowsets drawn (default 100)")
    p.add_argument("--seed", type=int, default=20261017)
    p.add_argument("--targets", type=int, nargs="+", default=TARGETS, help="target hyperperiods, ms")
    p.add_argument("--stretch", type=float, default=1.5, help="largest period stretch T'/T")
    r = sub.add_parser("run", help="solve every prepared instance")
    r.add_argument("-j", "--jobs", type=int, default=1)
    r.add_argument("--cap", type=float, default=3600.0, help="time limit per model, s (default 1 h)")
    r.add_argument("--n-rb", type=int, default=None,
                   help="B^max (default: the budget each instance records, its tuple's sweep)")
    r.add_argument("--harqs", type=int, default=3)
    r.add_argument("--solver", type=Path, default=ILP,
                   help=f"the ILP binary (default {ILP.relative_to(ROOT)}, HiGHS; "
                        f"{SIREN.relative_to(ROOT)} for Gurobi)")
    r.add_argument("--max-h", type=int, default=10 ** 9, help="skip targets above this, ms")
    r.add_argument("--force", action="store_true", help="re-solve instances that have a result")
    r.add_argument("--shard", default="1/1",
                   help="K/N: solve only the K-th of N disjoint shares of the instances, "
                        "to run on N machines sharing --out (default 1/1, all)")
    c = sub.add_parser("collect", help="gather the results into one CSV")
    c.add_argument("--csv", type=Path, default=CSV)
    e = sub.add_parser("extend", help="add targets to the flowsets already prepared")
    e.add_argument("--targets", type=int, nargs="+", required=True, help="target hyperperiods, ms")
    e.add_argument("--stretch", type=float, default=1.5, help="largest period stretch T'/T")
    cp = sub.add_parser("control-prepare", help="copy a sample of decided models, to solve alone")
    cp.add_argument("--control", type=Path, default=OUT / "control", help="where they go")
    cp.add_argument("--per-h", type=int, default=2, help="models per target hyperperiod (default 2)")
    cp.add_argument("--max-solve", type=float, default=900.0,
                    help="only models decided within this many seconds in RQ4 (default 900)")
    cp.add_argument("--host", default=None, help="only models solved on this machine")
    cp.add_argument("--seed", type=int, default=20261024)
    cc = sub.add_parser("compare", help="RQ4's times of the control models against theirs alone")
    cc.add_argument("--control", type=Path, default=OUT / "control")
    cc.add_argument("--csv", type=Path, default=CSV.with_name("rq4_control.csv"))
    args = ap.parse_args()
    return {"prepare": prepare, "extend": extend, "run": run, "collect": collect,
            "control-prepare": control_prepare, "compare": compare}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
