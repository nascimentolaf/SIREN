# Output formats

The files a run of `scripts/run_paper_experiment.sh` leaves under
`output/sweep/rb<B>/reports/<method>/<flowset>/`, field by field. The verdict CSV derived from them is described in
[Analysis](analysis/index.md#the-verdict-table).

| File | Written by | Count per flowset |
| :--- | :--- | :--- |
| `planner_report.json` | planner | one (ILP only) |
| `offline_phase_report.json` | `siren` | one (ILP only) |
| `combo_<index>.json` | `siren` | one per schedulable candidate (none if no candidate is schedulable) |
| `combo_0000.json` | each baseline | exactly one, a schedule or an infeasibility verdict |

**Units.** Fields ending in `_tti` are integer TTIs, the values every
consumer should compute with. Fields ending in `_ms` (and the bare `period`,
`deadline`, `c`) are milliseconds; in ILP tables they are `*_tti × tti_ms` and
may be fractional, in baseline tables they are integers.

## `planner_report.json`

Each flow's Pareto set of configurations. Specified in
[Configuration planning](offline_phase/planning.md#output-planner_reportjson).

## `offline_phase_report.json`

The ILP's verdict over a flowset's candidate sweep, written by `siren`.

```jsonc
{
  "config": { "solver": "gurobi 13.0.1",   // the Gurobi library's version
              "n_rb": 135, "m_harq": 3, "objective": "rb_peak", "rb_peak_search": "bounded",
              "tti_ms": 0.25, "t_up_ms": 4.0, "timeout_s": 0.0, "candidate_timeout_s": 60.0,
              "stop_at_first": false },   // true with --stop-at-first
  "num_combinations_total": 945,      // candidates = product of the Pareto-set sizes
  "num_combinations_tested": 945,     // how far the sweep got
  "num_schedulable": 37,
  "num_combinations_timed_out": 2,    // tested but undecided (hit a budget)
  "timed_out_combos": [ 412, 803 ],   // their combo indices
  "num_combinations_errored": 0,      // tested but undecided (solver error, see the log)
  "errored_combos": [],               // their combo indices
  "timed_out": false,                 // true only if the whole-sweep budget ran out
  "infeasible_flows": [],             // flows with an empty Pareto set
  "schedulable": [ { ... } ],         // one entry per schedulable candidate
  "rb_peak_search": { ... }           // only with objective rb_peak, search bounded
}
```

Each `schedulable[]` entry:

| Field | Meaning |
| :--- | :--- |
| `flows` | the candidate's resolved task set, one entry per flow (same fields as in a dispatch table, below) |
| `schedulable` | always `true` |
| `hyperperiod_tti`, `hyperperiod_ms` | `lcm(T_i)` |
| `num_jobs`, `num_variables`, `num_constraints` | size of the ILP |
| `solve_time_ms` | Gurobi's time for this candidate (the pass-1 solve under bounded `rb_peak`) |
| `schedule_file` | path of its `combo_<index>.json` (absent with `--no-schedules`) |
| `harq_used` | objective `harq` only |
| `rb_peak_used` | objective `rb_peak`: the minimum peak RB usage |
| `mean_rb` | bounded `rb_peak` only: `Σ N_i B_i C_i / T_i`, the lower bound used to order and prune |
| `rb_peak_solve_time_ms` | bounded `rb_peak`: time of the pass-2 solve |
| `rb_peak_timed_out` | bounded `rb_peak`: pass 2 ran out of its cap for this candidate |

`rb_peak_search` (bounded search only):

| Field | Meaning |
| :--- | :--- |
| `mode` | `bounded` |
| `num_candidates` | schedulable candidates entering pass 2 |
| `num_solved`, `num_pruned`, `num_timed_out`, `num_errors` | re-solved for the peak; skipped by the bound; capped; ended in a solver error (the entry then has `rb_peak_error`) |
| `budget_exhausted` | the sweep budget ran out during pass 1 or 2 |
| `best_rb_peak`, `best_combo_index` | the smallest peak found and its candidate (`null` if none) |
| `optimal_over_decided` | `true` when no pass-2 solve timed out or failed and the budget held: the best peak is optimal over the candidates decided |

When a flow has an empty Pareto set, the report has `infeasible_flows` set,
all counts `0`, no `schedulable` entries, and its `config` lacks
`rb_peak_search`.

How the analysis turns this into one verdict: `ko_infeasible` if
`infeasible_flows` is non-empty; else `ok` if any candidate is schedulable;
else `timeout` (undecided) if the sweep timed out or any candidate timed out
or ended in a solver error; else `ko_unschedulable`.

## Dispatch table (`combo_*.json`)

The schedule of one hyperperiod, in the same format for every method
(illustrative values, one flow and one TB shown):

```jsonc
{
  "combo_index": 12,
  "config": { "n_rb": 135, "m_harq": 3, "objective": "must", "tti_ms": 0.25,
              "t_up_ms": 4.0, "t_up_tti": 16,
              "exec_time_mode": "wcet", "exec_sample_seed": 42 },
  "hyperperiod_tti": 480,
  "hyperperiod_ms": 120,
  "harq_used": 3,
  "flows": [
    { "app": "ProcessControl_T60", "num_segments": 3, "tbs": 129, "m": 0,
      "a": 3, "b": 29, "c": 3.0, "c_tti": 12,
      "period": 60, "period_tti": 240, "deadline": 30, "deadline_tti": 120 }
  ],
  "dispatch_table": [
    { "app": "ProcessControl_T60", "subtask": 0, "machine": 1, "b": 29,
      "release_tti": 0, "start_tti": 0, "end_tti": 12, "deadline_tti": 120,
      "release_ms": 0, "start_ms": 0, "end_ms": 3, "deadline_ms": 30 }
  ]
}
```

| Field | Meaning |
| :--- | :--- |
| `combo_index` | ILP: the candidate's 1-based position in the enumeration. Baselines: the number in the flowset's file name |
| `config.n_rb`, `config.m_harq`, `config.tti_ms` | `B^max`, `M`, TTI |
| `config.t_up_ms`, `config.t_up_tti` | the upper-layer delay `t_up`: a packet is delivered this long after its last TB ends |
| `config.objective` | the ILP objective, or the baseline's name: `round_robin`, `proportional_fair`, `qos_pf`, `must` |
| `config.exec_time_mode`, `config.exec_sample_seed` | baselines only: the execution-time model of the run |
| `hyperperiod_tti`, `hyperperiod_ms` | the scheduled horizon |
| `harq_used` | baselines: number of distinct processes in the table. ILP: set only with objective `harq`, otherwise `null` |
| `rb_peak_used` | ILP only: set with objective `rb_peak`, otherwise `null` |
| `flows[]` | per flow: segments, TB size, MCS `m`, attempts `a`, RBs per TB `b`, WCTT `c = A t_tx` (ms) and `c_tti`, without `t_up`, period and deadline in ms and TTIs |
| `dispatch_table[]` | one entry per transport block placed |
| `.subtask` | segment index within its packet (`0 … N-1`) |
| `.machine` | HARQ process (`0 … M-1`) |
| `.b` | RBs held for the whole `[start, end)`; `end` is when the TB releases its process and RBs |
| `.release_*`, `.start_*`, `.end_*`, `.deadline_*` | packet release, TB start, TB end, packet's absolute deadline |

A packet's jobs share `app`, `release_tti` and `deadline_tti`; the packet is
late when the latest `end_tti` among them plus `config.t_up_tti` exceeds
`deadline_tti`. In ILP tables
that never happens; in baseline tables it is how deadline misses show up (except
for `must`, which drops instead; see below).

**Infeasible verdict (baselines).** When no configuration of some flow fits the
cell, a baseline writes the same structure with `"schedulable": false`,
`"infeasible_flows": [ ... ]`, `"infeasible_reason": "..."`, empty `flows` and
`dispatch_table`, `hyperperiod_*` and `harq_used` set to `0`, and a `config`
without the `exec_*` fields.

**Method-specific additions:**

| Method | Fields | Page |
| :--- | :--- | :--- |
| `must` | `dropped_packets`, `n_dropped_packets`, `must.plr_estimate`, `must.n_packets` | [MUST](baselines/must.md) |

## Logs

| File | Content |
| :--- | :--- |
| `output/sweep/rb<B>/logs/<method>/<flowset>.log` | a header (`=== <flowset> / <method> ===`, `Host:` the machine, `Output:` the report folder), then everything the method printed. For the ILP: the planner's summary, then one line per candidate (`[k/total] app(N=..,C=..tti,b=..) -> SCHEDULABLE (… ms)` / `not schedulable` / `timed out, skipped` / `solver error` / `infeasible (window)` / `infeasible (D > T)`), then, under bounded `rb_peak`, one line per pass-2 solve. For QoS-PF: the 5QI, priority and GBR of every flow |
| `output/sweep/rb<B>/.parallel/status/<flowset>` | `ok` once every method of the pair ended with status 0, otherwise `fail <methods>` |

`analysis/plot_paper.py --derive` parses the ILP logs for per-candidate solve
times; keep their format if you change the ILP's progress output.
