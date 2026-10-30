# Baselines

`baseline_algorithms/` holds the four schedulers SIREN (the method `ilp`) is
compared against in the paper.
They share one command line, one environment, one configuration-selection rule,
one transmission-time model and one output format, so that differences between
them come from the scheduling policy.

| Method | Policy | Kind | Page |
| :--- | :--- | :--- | :--- |
| `rr` | round-robin over flows | channel- and deadline-agnostic | [Round-robin](rr.md) |
| `pf` | proportional fair: achievable rate / average rate | throughput fairness | [Proportional fair](pf.md) |
| `qospf` | QoS-aware PF (Seliem et al., MSWiM 2025): delay, GBR deficit and 5QI priority over average rate | QoS-weighted fairness | [QoS-PF](qospf.md) |
| `must` | MUST (Khorov et al., TMC 2021): deadline-ordered look-ahead with drops | URLLC, deadline-aware | [MUST](must.md) |

The default `METHODS` in `parameters.sh` runs `rr pf qospf must` alongside
the ILP.

## Build

```bash
cd baseline_algorithms
make            # all four, each to <name>/<name>
make must       # one of them
make clean
```

One Makefile compiles every `<name>/main.cpp` with the shared code in
`common/` (`scheduler.cpp`, `qos_data.cc`) and the Section V header
`../common/urllc_config.hpp`, using `g++ -std=c++17`. Every source tree
bundles its own copy of `nlohmann/json`.

## Command line

```bash
set -a; source ../scripts/parameters.sh; set +a
./must/must ../output/flowsets/flowset_0001.json ../input/mcs_configurations.json out.json
```

`<method> <flowset.json> <mcs_configurations.json> [output.json]` for all
four. Without the third argument:

- `rr`, `pf`, `must` write `./<flowset stem>_<method>.json` in the current
  directory;
- `qospf` prints the report to stdout (but still writes an infeasibility
  verdict to `./<flowset stem>_qospf.json`).

`run_paper_experiment.sh` always passes `reports/<method>/<flowset>/combo_0000.json`, and sets `RBs` to the pair's budget.

| Exit status | Meaning |
| :--- | :--- |
| `0` | a report was written: a schedule, or an infeasibility verdict |
| `1` | an error (missing variable, unreadable input, internal check), and, for `qospf`, a usage error |
| `2` | usage error (`rr`, `pf`, `must`) |

The `combo_index` field is taken from the integer in the flowset's file name
(`flowset_0042.json` → 42).

## Environment

Every baseline reads, through `read_runtime_config()`:

| Variable | Required | Default if unset |
| :--- | :--- | :--- |
| `TTI`, `RBs`, `HARQs`, `UPPER_LAYER_DELAY` | yes | none |
| `FEEDBACK_PER_RET` | no | `3 × TTI` |
| `SINR_DB` | no | `10` (selects the MCS tables at this SINR) |
| `RLC_HEADER_BYTES` | no | `8` |
| `A_MAX` | no | `4` |
| `EXEC_TIME_MODE`, `EXEC_SAMPLE_SEED` | no | `wcet`, `42` |
| `PF_ALPHA`, `PF_INITIAL_THROUGHPUT` | no | `0.1`, `1.0` (used by `pf`) |
| `FLOW_CONFIG` | no | unset |

plus `ALPHA`, `BETA`, `GAMMA`, `PF_EMA_LAMBDA` (required by `qospf`). Meanings
in [Configuration](../configuration.md).

## Configuration selection

Every baseline picks **one** configuration per flow through
`select_flow_config()` (`common/scheduler.cpp`).

1. Run Section V (`urllc::SelectConfigurations`) with `enforceLimits = false`:
   Eqs. (2), (3) and (4) are applied, but the `C + t_up <= D` check is
   **not**: that screen is part of the contribution under evaluation, so a
   baseline does not get it. Attempts are not capped at this step either. Then
   Pareto-filter.
2. Discard configurations needing more RBs than the cell has (`B > B^max`):
   no policy can transmit them.
3. Keep only the configurations within `A^max` attempts, if any is left:
   `A^max` is a limit of the MAC layer (Section IV-B), not of the analysis.
4. Pick the one with the fewest segments, then the fewest attempts, then the
   fewest RBs, then the **higher** MCS.

If nothing survives step 2, the flowset is infeasible for this baseline: it
writes an infeasibility report (`schedulable: false`, `infeasible_flows`, empty
table) and exits 0.

Consequences of selecting from the unscreened set:

- A flow whose every configuration has `C + t_up > D` is scheduled anyway and
  misses its deadline on every packet, where the ILP proves the flowset
  infeasible beforehand.
- When no configuration within `A^max` fits the cell, step 3 keeps them all
  and the baseline falls back to **more than `A^max` attempts**. On the current
  catalogue and MCS table this never happens: every application has a
  configuration within `A^max` that fits even 51 RBs.
  `analysis/plot_paper.py` reclassifies a baseline run whose
  largest attempt count exceeds `A_MAX` as `ko_amax` ("needs `A > A^max`",
  outside the model); the raw verdict CSVs keep the simulated verdict. See
  [Analysis](../analysis/index.md#ko_amax).

**Imposed configurations.** If `FLOW_CONFIG` names a JSON file, steps 1 to 4 are
replaced by the configuration it gives for each application:

```json
{ "ProcessControl_T60": { "tbs": 129, "m": 0, "segments": 3, "attempts": 3,
                          "rbs": 29, "etb_target": 3.33e-06, "bler": 0.00673,
                          "wctt_ms": 3.0 } }
```

Every application of the flowset must be present (otherwise the run fails
rather than silently falling back). A configuration wider than the cell makes
the flowset infeasible as above. The paper's runs do not use it.

## Job model and timing

- A flow releases a packet at `0, T, 2T, …` inside one hyperperiod
  `H = lcm(T_i)`; each release makes `N` jobs (one per segment) ready at once,
  all with the packet's absolute deadline `release + D`.
- A job holds **one HARQ process and `B` RBs for its whole duration**
  `C = A × (FEEDBACK_PER_RET + TTI)`, the same worst-case model as the ILP's
  `C`. The upper-layer delay `t_up` (`UPPER_LAYER_DELAY`) follows a packet's
  last job and holds neither: a packet is late when its last job's end plus
  `t_up` exceeds its deadline (reports carry `config.t_up_ms` and
  `config.t_up_tti`). The deadline-aware policies plan against the deadline
  less `t_up` (MUST's lifetime, QoS-PF's delay urgency).
- Scheduling is **non-preemptive** and **work-conserving**: whenever a process
  is free, the policy picks among ready jobs whose `B` fits in the RBs still
  free; a job that does not fit is skipped in favour of one that does.
  Free processes are taken lowest index first.
- Jobs start on TTI boundaries. Times are recorded in whole milliseconds and a
  non-integer time is refused rather than truncated (see
  [Architecture](../architecture.md#time-units)).
- A job that misses its deadline still runs to completion (except in `must`,
  which never starts a TB that cannot end in time and discards the packet
  when its lifetime expires). Jobs may finish after `H`. Exactly one
  hyperperiod is simulated.

### Execution-time model {#execution-time-model}

| `EXEC_TIME_MODE` | Attempts per TB |
| :--- | :--- |
| `wcet` (default) | always `A`, the worst case the configuration budgets for |
| `sampled` | the attempts a real run would need: each attempt fails independently with the row's BLER, capped at `A` |

`sampled` exists because non-preemptive work-conserving scheduling is not
sustainable with respect to shorter execution: a TB that decodes early can let
another job start and block a more urgent one. Draws are seeded by **job
identity** (flow, release, segment) XOR `EXEC_SAMPLE_SEED`, not by draw order,
so every method faces the same run for a given seed. The paper's runs use
`wcet`.

## Output

A combo-style report, the same format as the ILP's dispatch tables (see
[Output formats](../output_formats.md#dispatch-table-combo_json)). `config.objective`
names the method (`round_robin`, `proportional_fair`, `qos_pf`, `must`).
`must` adds fields of its own, listed on its page.
