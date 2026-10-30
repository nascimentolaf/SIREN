# Architecture

How the components fit together, what each kind of method promises, and the
few contracts every component respects.

## The model in one paragraph

A gNB serves periodic downlink flows. A flow `F_i` sends one packet of `S_i`
bytes every `T_i` ms, which must be delivered within `D_i` ms (`D_i <= T_i`)
with residual packet error at most `E_i`. Given a transmission configuration
`K = (TB size, MCS)`, a packet is segmented into `N_i` transport blocks (TBs);
each TB is a non-preemptive real-time task that holds **one HARQ process** and
**`B_i` resource blocks** in every TTI it runs, for a worst-case time
`C_i = A_i t_tx` that covers its `A_i` HARQ attempts. After its last TB ends,
a packet still spends the upper-layer delay `t_up` in the other layers; that
time holds neither a HARQ process nor RBs, but counts towards the deadline, so
a packet meets its deadline when its last TB ends by `D_i - t_up` after its
release (fork/join). HARQ processes are `M` identical processors; resource
blocks are a pool of `B^max` replenished every TTI. With synchronous releases
and `D_i <= T_i`, one hyperperiod `H = lcm(T_i)` is a feasibility interval, so
a schedule for `[0, H)` can be replayed forever.

## Pipeline

```text
                       input/app_database.json      input/mcs_configurations.json
                                  |                     (from bler/, see bler.md)
                                  v                               |
                     offline_phase/generate_flowset               |
                                  |                               |
                     output/flowsets/flowset_NNNN.json            |
                                  |                               |
              +-------------------+--------------------+          |
              |                                        |          |
              v                                        v          v
   offline_phase/planning  <------------------------------------ MCS table
   (Section V: P_i per flow)                  baseline_algorithms/<method>
              |                               (pick one K per flow, simulate
   planner_report.json                         one hyperperiod)
              |                                        |
              v                                        |
   offline_phase/siren                      |
   (Section VI: one ILP per candidate, Gurobi)         |
              |                                        |
              +------------------+---------------------+
                                 v
     output/sweep/rb<B>/reports/<method>/flowset_NNNN/combo_*.json   <- the common artefact
                                 |
                                 v
                    analysis/collect_verdicts.py
                    (one verdict row per method x flowset)
                                 |
                                 v
                    analysis/plot_paper.py -> table, figures, LaTeX macros
```

`scripts/run_paper_experiment.sh` drives all of it, once per RB budget
`B^max`; see [Scripts](scripts.md).

## Configuration selection is shared (Section V)

Every component that turns a flow into transport blocks uses **one**
implementation of Eqs. (2) to (6), the header-only `common/urllc_config.hpp`:

| Step | Equation | Function |
| :--- | :--- | :--- |
| segments per packet: `N = ceil(S_i / (S_k - h))`, `h` the RLC header | (2) | `NumSegments` |
| per-TB error target: `E^tb = 1 - (1 - E_i)^(1/N)` | (3) | `PerTbErrorTarget` |
| attempts: smallest `A` with `BLER^A <= E^tb`, rejected if `A > A^max` | (4) | `RequiredAttempts` |
| worst-case time: `C = A * t_tx` (`t_tx = TTI + k1`), rejected if `C + t_up > D_i` | (5) | `Wctt`, `EnumerateConfigurations` |
| keep configurations no other one beats on all of (N, A, B) | (6) | `ParetoFilter` |

`ParetoFilter` also collapses configurations equal on all of `(N, A, B)` to
the one with the lowest BLER, since the scheduler cannot tell them apart. The
equations are pinned by a unit test against the paper's Table II
(`make -C common test`).

The two kinds of method use it differently, and this is deliberate:

- The **planner** (and therefore the ILP) applies both feasibility screens
  (`A <= A^max`, `C + t_up <= D_i`). A flow with no surviving configuration
  makes the flowset infeasible (`ko_infeasible`), proved before any scheduling.
- The **baselines** call it with `enforceLimits = false`: the deadline screen
  is part of the contribution being evaluated, so a method compared against it
  does not get it. Each baseline picks, among the configurations that fit the
  cell (`B <= B^max`), the one with the fewest segments, then fewest attempts,
  then fewest RBs, then the higher MCS. `A^max` is a limit of the MAC layer,
  not of the analysis, so only configurations within it compete whenever one
  fits the cell; the rule falls back to more attempts only when none does,
  and such a run is counted outside the model (`ko_amax`). A flow whose every
  configuration violates its deadline is scheduled anyway, and misses.

`scripts/check_catalogue.py` re-implements the same arithmetic in Python to
validate the catalogue without building anything.

## What a verdict means, per method

| | How it decides | What an `ok` is worth |
| :--- | :--- | :--- |
| **SIREN (`ilp`, solved by `siren`)** | Enumerates every combination of one configuration per flow (`∏ P_i` candidates) and solves a time-indexed ILP for each (it does not stop at the first schedulable one) until all are decided or the flowset's budget runs out; every schedulable candidate yields a table | a **proof** that a table meeting every deadline exists, valid for every run |
| **Baselines** (`rr`, `pf`, `qospf`, `must`) | Simulate one hyperperiod of an online, work-conserving policy on the configuration they picked | evidence about **the run that was simulated** |

A table-driven schedule is immune to early completions: start times are fixed,
so a TB that decodes on its first attempt just leaves its HARQ process idle.
Online work-conserving policies are not: finishing early lets another job start,
which can then block a more urgent one. The baselines' `EXEC_TIME_MODE=sampled`
exists to measure exactly that; see
[Baselines](baselines/index.md#execution-time-model).

## The common artefact: `combo_*.json`

Every method writes the same **dispatch table** format, so one plotting path
and one verdict reducer serve all of them:

- the ILP writes one `combo_<index>.json` per schedulable candidate, `<index>`
  being the candidate's 1-based position in its enumeration;
- every baseline writes exactly one, `combo_0000.json`, even when the flowset is
  infeasible for it (then with an empty table and `schedulable: false`).

Each entry places one TB: application, segment index (`subtask`), HARQ process
(`machine`), release / start / end / absolute deadline, and RBs held. `end` is
when the TB releases its process and RBs; the packet is delivered
`config.t_up_tti` later, and that delivery is what is compared with the
deadline. Full schema in [Output formats](output_formats.md).

## Time units

| Where | Unit |
| :--- | :--- |
| `TTI`, `UPPER_LAYER_DELAY`, `FEEDBACK_PER_RET` in the environment | **seconds** (the binaries read them so) |
| catalogue `period`, `deadline`; planner `c`; every report field ending in `_ms` | **milliseconds** |
| report fields ending in `_tti`; the ILP's internal time | **TTIs** (0.25 ms by default) |

The ILP schedules on the TTI grid. The simulated baselines record dispatch
events in **whole milliseconds** (`DispatchEntry` stores integers) and refuse,
rather than truncate, a time that is not a whole millisecond. That is safe
because the catalogue's periods, deadlines, every retained `C_i` and `t_up` are
whole milliseconds, which `check_catalogue.py` enforces.

## Comparing per configuration

A baseline normally answers for the one configuration it picked; the ILP
decides many. To compare on the same denominator, the environment variable
`FLOW_CONFIG` makes any baseline schedule a configuration imposed from outside
(one `urllc::Candidate` per application, as JSON). See
[Baselines](baselines/index.md#configuration-selection).

## Directory map

```text
./
├── common/                 urllc_config.hpp (Section V) and its unit test
├── input/                  application catalogue, MCS table
├── offline_phase/
│   ├── generate_flowset/   catalogue -> flowsets
│   ├── planning/           flowset -> planner_report.json (Section V)
│   └── siren/   planner_report.json -> schedule tables (Section VI), Gurobi
├── baseline_algorithms/    rr, pf, qospf, must + common/
├── analysis/               verdict reduction, paper table, figures and macros, RQ4
├── results/                will hold the paper's results
├── bler/                   link-level BLER simulation -> MCS table
├── scripts/                build, the paper's experiments, catalogue check
├── output/                 flowsets (tracked) and run outputs (ignored by git)
└── doc/                    this documentation; doc/html/ is generated
```
