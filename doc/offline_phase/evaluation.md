# ILP evaluation (`siren`)

`offline_phase/siren/` implements Section VI: for every candidate
configuration of a flowset it builds a **time-indexed ILP** over one
hyperperiod and solves it with [Gurobi](https://www.gurobi.com) 13.0, through
its C API. A schedulable candidate yields a **dispatch table** (the start time
and HARQ process of every TB) that meets every deadline and never exceeds
`B^max` RBs in any TTI. The program is `siren`; in the reports and CSVs,
SIREN is the method `ilp`.

## Build

Requires Gurobi 13.0 (the paper's runs used 13.0.1), CMake, a C++17 compiler
and the system `nlohmann/json` package (`nlohmann-json3-dev`). Gurobi ships
neither a CMake package nor a pkg-config file: `GUROBI_HOME` is the `linux64`
directory of the unpacked Gurobi distribution, which holds
`include/gurobi_c.h` and `lib/libgurobi130.so`.

```bash
export GUROBI_HOME=/path/to/gurobi1301/linux64
cd offline_phase/siren
make                         # CMake build -> ./siren
```

`scripts/install.sh` (or `install_conda.sh`) and the `build` stage of
`scripts/run_paper_experiment.sh` run the same `make`. Without Gurobi under
`GUROBI_HOME`, CMake stops with `Gurobi 13.0 not found under GUROBI_HOME=...`.
The build tree's RPATH points at `$GUROBI_HOME/lib`, so `./siren` runs without
`LD_LIBRARY_PATH`. The `gurobipy` wheel from pip is not enough: it has no C
headers.

## License

Building needs no license; running does. Each `siren` process starts one
Gurobi environment, and so checks out one license token, for its whole sweep
of a flowset. Gurobi looks for the license in `GRB_LICENSE_FILE`, then in its
default places such as `~/gurobi.lic`. A run that gets no license exits with
status 1 before solving anything (`Cannot start Gurobi: ...`), so the pair is
marked failed rather than given a wrong verdict.

With a license that allows a limited number of processes at once, that
number bounds the workers: `run_paper_experiment.sh` runs one `siren` per
worker, so the workers of every machine sharing the license must stay within
it. A license file (`gurobi.lic`) holds private keys: keep it out of the
repository (`.gitignore` ignores every `gurobi.lic`).

## Usage

```bash
./siren planner_report.json \
    --n-rb 135 --m-harq 3 --tti 0.25 \
    --objective rb_peak --rb-peak-search bounded --timeout 0 --candidate-timeout 60 \
    --output-scheduling offline_phase_report.json --schedule-dir out/
```

| Option | Meaning | Default |
| :--- | :--- | :--- |
| `<planner_report.json>` | the planner's output (positional, required) | none |
| `--n-rb <int>` | `B^max`, RBs per TTI | `50` |
| `--m-harq <int>` | `M`, HARQ processes | `2` |
| `--tti <ms>` | the time unit, **milliseconds**; must be the TTI the planner used for `C_i` | `0.25` |
| `--objective <name>` | `feasibility`, `rb_peak` or `harq` (`machines` is accepted for `harq`) | `feasibility` |
| `--rb-peak-search <mode>` | `rb_peak` only: `full` or `bounded` (see below) | `full` |
| `--timeout <s>` | budget for the whole candidate sweep; `0` = none | `300` |
| `--candidate-timeout <s>` | cap per candidate; `0` = none | `0` |
| `--output-scheduling <path>` | write the report to this exact path; otherwise print it to stdout | stdout |
| `--schedule-dir <dir>` | where to write one `combo_<index>.json` per schedulable candidate | `schedules` |
| `--no-schedules` | do not write dispatch tables | off |
| `--stop-at-first` | stop the sweep at the first schedulable candidate (also `SIREN_STOP_AT_FIRST=1` in the environment); the report records it as `config.stop_at_first`. The paper's runs do not use it | off |
| `--quiet`, `-q` | no per-candidate progress on stderr | off |

The defaults above are the binary's. `scripts/run_paper_experiment.sh` always
passes the paper's settings: `--n-rb` the pair's budget, `--m-harq 3`,
`--tti 0.25`, `--objective rb_peak --rb-peak-search bounded`, `--timeout 0`
and `--candidate-timeout 60`. The upper-layer delay `t_up` is not an option:
`siren` reads `parameters.t_up_ms` from the planner report and refuses a
report without it.

## What it does

1. **Load** the planner report. If any flow has an empty Pareto set, write a
   report with `infeasible_flows` set and no candidate tested, and exit 0.
2. **Start Gurobi** (one environment, one license token, for the whole run).
3. **Enumerate** the cross-product of the flows' configurations (one
   `(segmentation option, MCS combination)` per flow) as a mixed-radix
   odometer: the last flow advances fastest, starting from the first choice of
   every flow. A candidate's `combo_index` is its 1-based position in that
   order.
4. For each candidate, **resolve** the task set on the TTI grid:
   `period / TTI` must be an integer (otherwise the run aborts: the hyperperiod
   would not be a feasibility interval), the deadline is rounded **down** and
   `C` and `t_up` **up**, so rounding can never make an infeasible task set
   look feasible. `C = A * t_tx` is how long a TB holds its HARQ process and
   RBs; `t_up` holds neither, but the packet is delivered `t_up` after its last
   TB ends, so every TB must end by its deadline less `t_up`.
5. **Expand jobs** over `H = lcm(T_i)`: `N_i` identical jobs per release of
   flow `i`. Fast paths without the solver: a task with `D > T` is rejected (the
   hyperperiod is a feasibility interval only for constrained deadlines), and a
   job whose window is empty (`C + t_up > D`) makes the candidate infeasible.
6. **Build and solve** the ILP (below) with Gurobi (see
   [Solver settings and outcomes](#solver-settings-and-outcomes)).
7. Write a dispatch table for every schedulable candidate and, at the end, the
   report summarising the sweep.

The sweep does **not** stop at the first schedulable candidate: it decides
every candidate (or as many as the budgets allow), and reports all the
schedulable ones.

### The model

Binary `x[j, p, t] = 1` when job `j` starts at TTI `t` on HARQ process `p`,
created only for `t` in the job's window `[r_j, d_j - t_up - C_j]`; variables
outside it are never generated, which is what keeps the model small. A job
occupies its process and `B_j` RBs during `[t, t + C_j)` only.

| Constraint | Equation | Meaning |
| :--- | :--- | :--- |
| assignment | (7) | every job starts exactly once |
| HARQ capacity | (8) | at most one job runs on a process at any TTI |
| RB capacity | (9) | the RBs of the jobs running at any TTI total at most `B^max` |
| symmetry breaking | (10) | within one release of one flow, segment `a` starts no later than segment `a+1` |

| Objective | Equations | Adds |
| :--- | :--- | :--- |
| `feasibility` | none | nothing: any feasible table |
| `harq` | (11) and (12) | one binary per process; minimises the number of processes used; reports `harq_used` |
| `rb_peak` | (13) and (14) | an integer peak `P <= B^max` bounding every TTI's usage; minimises it; reports `rb_peak_used` |

Because of the shared RB constraint, the table is not the schedule of any
fixed-priority or EDF rule: the solver may delay a job that a priority rule
would start. Treat the table itself as the schedule.

The formulation is written up in LaTeX in `src/latex/` (`main.tex`,
`sysmodel.tex`, and the compiled `main.pdf`).

### Solver settings and outcomes

Set once on the Gurobi environment, so every model inherits them:

| Parameter | Value | Why |
| :--- | :--- | :--- |
| `OutputFlag` | 0 | no log on stdout, where the report goes without `--output-scheduling` |
| `Threads` | 1 | the experiments run one process per core |
| `MIPGap`, `MIPGapAbs` | 0 | every objective is an integer: the optimum is closed exactly |
| `TimeLimit` | per candidate | what is left of `--timeout`, or `--candidate-timeout`, at least 1 s |

| Gurobi status | Outcome of the candidate |
| :--- | :--- |
| `OPTIMAL` | schedulable: the table is read from the variables at 1 (value > 0.5) |
| `INFEASIBLE`, `INF_OR_UNBD` | not schedulable (every variable is bounded, so the model cannot be unbounded) |
| `TIME_LIMIT`, `ITERATION_LIMIT` | **timed out**: undecided, even if a feasible solution was found |
| anything else, or an API error | **solver error**: undecided, logged with the status and listed in `errored_combos` |

Every report records the Gurobi library's version in `config.solver` (for
example `gurobi 13.0.1`). `run_paper_experiment.sh` refuses a sweep tree whose
ILP reports lack it.

### Two time budgets {#two-time-budgets}

| Budget | Scope | When it runs out | Recorded as |
| :--- | :--- | :--- | :--- |
| `--candidate-timeout` | one candidate | that candidate is skipped; the sweep continues | its index in `timed_out_combos`, counted in `num_combinations_timed_out` |
| `--timeout` | the whole sweep | the sweep ends; untested candidates are never reached | `timed_out: true`; `num_combinations_tested` says how far it got |

A candidate gets whichever is smaller: the per-candidate cap or what is left
of the sweep budget. Both are wall-clock times. Either way a timed-out
candidate is **undecided**, never "unschedulable", and the analysis scores the
flowset `timeout` when no candidate was proved schedulable. A candidate ending
in a solver error is undecided too (`num_combinations_errored`), and the sweep
goes on; `collect_verdicts.py` prints a warning when any flowset has one. The
per-candidate cap exists so one pathological candidate cannot consume the
whole flowset's budget.

### Bounded `rb_peak` search

With `--objective rb_peak --rb-peak-search bounded` (what the paper's runs
use), the sweep runs in two passes:

1. Decide every candidate with the cheaper `feasibility` objective.
2. Sort the schedulable ones by their mean RB usage over the hyperperiod,
   `Σ N_i · B_i · C_i / T_i` (no table's peak can be below its mean), and
   re-solve them with the `rb_peak` objective in that order. Stop as soon as
   the best peak found is `<= ceil(mean)` of the next candidate: no remaining
   candidate can beat it.

The report then carries an `rb_peak_search` block (candidates solved, pruned by
the bound, timed out, ended in a solver error; best peak and its candidate; and
whether that best is optimal over the decided candidates). `full` minimises the
peak on every candidate directly.

## Outputs

- `offline_phase_report.json` (`--output-scheduling`): the verdict over the
  sweep: candidate counts, timed-out and solver-error candidates, and one entry
  per schedulable candidate with its flows, model size and solve time.
- `combo_<index>.json` (`--schedule-dir`): one dispatch table per schedulable
  candidate, `<index>` zero-padded to the width of the candidate count. Under
  the bounded `rb_peak` search, the tables re-solved in pass 2 are overwritten
  with their minimum-peak version.

Both formats are specified in [Output formats](../output_formats.md).

## Checking a table independently

`validate_schedule.py` re-checks dispatch tables against constraints (7) to (10)
from the `*_tti` fields alone, without the solver: every release of every flow
holds each of its subtasks exactly once, every TB ends `config.t_up_tti` before
its deadline, no process runs two TBs at once, the RBs never exceed `B^max`,
and segments start in order:

```bash
python3 offline_phase/siren/validate_schedule.py \
    output/sweep/rb135/reports/ilp/flowset_0001/combo_*.json
```

With no argument it checks `schedules/*.json`.

## Cost

The sweep, not a single ILP, dominates the offline cost: a flowset is one
independent ILP per candidate, a median of 1200 for the shipped catalogue
(see [Inputs](../input.md#the-catalogue)). A single model is small
(variables are `O(jobs × M × window)`), but a candidate the solver cannot settle
can take its whole cap. The 0.25 ms TTI multiplies the hyperperiod and every
job's window by four compared with a 1 ms grid; that is the correct grid,
since `B^max` is a per-TTI capacity. Gurobi is pinned to one thread so that one
process per core does not oversubscribe the machine.
