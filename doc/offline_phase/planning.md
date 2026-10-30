# Configuration planning

`offline_phase/planning/` implements Section V: for every flow of a flowset it
lists the transmission configurations `K = (TB size, MCS)` that meet the flow's
reliability target within its deadline, keeps the Pareto-optimal ones, and
writes them to `planner_report.json`, the input of the [ILP](evaluation.md).

The arithmetic itself lives in `common/urllc_config.hpp`, shared with every
baseline (see [Architecture](../architecture.md#configuration-selection-is-shared-section-v));
the planner is that header plus JSON input and output.

## Usage

```bash
cd offline_phase/planning
make                                         # CMake build -> ./planner
set -a; source ../../scripts/parameters.sh; set +a
./planner ../../output/flowsets/flowset_0001.json ../../input/mcs_configurations.json planner_report.json
```

| Argument | Meaning |
| :--- | :--- |
| `<flowset_file>` | a flowset ([format](../input.md#flowset-format)) |
| `<mcs_config_file>` | the MCS table, current format ([format](../input.md#mcs-table-mcs_configurationsjson)) |
| `<output_file>` | where to write the report |

| Environment | Required | Meaning |
| :--- | :--- | :--- |
| `TTI` | yes | seconds |
| `FEEDBACK_PER_RET` | yes | seconds |
| `UPPER_LAYER_DELAY` | yes | seconds |
| `RLC_HEADER_BYTES` | no (8) | bytes |
| `A_MAX` | no (4) | attempts |

From these: `t_tx = (TTI + FEEDBACK_PER_RET)` and `t_up = UPPER_LAYER_DELAY`,
both converted to ms: 1 ms and 4 ms with the defaults.

## What it computes

For every flow and every (TB size, MCS row) of the table:

1. `N = ceil(S_i / (TBS - RLC_HEADER_BYTES))` segments (Eq. (2)); a TB size
   that leaves no payload is skipped.
2. Per-TB target `E^tb = 1 - (1 - E_i)^(1/N)` (Eq. (3)).
3. Attempts: the smallest `A` with `BLER^A <= E^tb`; rejected if `A > A_MAX`
   (Eq. (4)).
4. `C = A * t_tx`, how long a TB holds its HARQ process and RBs; rejected if
   `C + t_up > D_i` (Eq. (5)): the upper-layer delay holds neither, but a
   packet is delivered only `t_up` after its last TB ends.

The survivors are counted as `num_feasible`. They are then reduced to the
Pareto set `P_i` over `(N, A, B)` (Eq. (6)), after first collapsing
configurations equal on all three to the one with the lowest BLER, and counted
as `num_pareto`.

A flow with an empty `P_i` makes the flowset infeasible. The planner reports
that (`feasible: false`, counted in `num_infeasible_flows`, a line
`[infeasible] <app>: ...` on stderr) and still exits 0, so a sweep records a
verdict instead of a failure.

The ILP tries every combination of one configuration per flow, so a flowset
has `∏ num_pareto` candidates. For the example below that is
`9 × 3 × 7 × 5 = 945`.

The planner keeps only the MCS tables whose `sinr` equals `SINR_DB` (default
10 dB), as every baseline does, and fails if there are none.

## Output: `planner_report.json`

The Pareto set of each flow, grouped by segmentation `(N, TBS)`, the shape the
ILP consumes. Real excerpt (`flowset_0001`, default parameters):

```json
{
  "parameters": { "a_max": 4, "rlc_header_bytes": 8, "t_tx_ms": 1.0, "t_up_ms": 4.0 },
  "num_infeasible_flows": 0,
  "applications": [
    {
      "app": "DiscreteAutomation_T30", "type": "RT",
      "packet_size": 512, "error": 0.0001, "period": 30.0, "deadline": 15.0,
      "feasible": true, "num_feasible": 14, "num_pareto": 9,
      "segmentation_options": [
        {
          "num_segments": 2, "tbs": 469, "payload_per_segment": 461,
          "tb_error": 5.000125e-05,
          "mbc_combinations": [
            { "m": 2, "b": 64, "a": 2, "c": 2.0, "bler": 0.006375 },
            { "m": 3, "b": 50, "a": 3, "c": 3.0, "bler": 0.024141 }
          ]
        }
      ]
    }
  ]
}
```

| Field | Meaning |
| :--- | :--- |
| `parameters` | the values used: `a_max`, `rlc_header_bytes`, `t_tx_ms`, `t_up_ms`. The ILP reads `t_up_ms` from here (and refuses a report without it) |
| `applications[].feasible`, `num_feasible`, `num_pareto` | `P_i` non-empty; configurations passing both screens; size of `P_i` |
| `segmentation_options[]` | one per distinct `(num_segments, tbs)` in `P_i` |
| `.payload_per_segment` | `tbs - RLC_HEADER_BYTES` |
| `.tb_error` | `E^tb` of Eq. (3) for this `N` |
| `.mbc_combinations[]` | the configurations of `P_i` at this segmentation: MCS `m`, RBs per attempt `b`, attempts `a`, WCTT `c = a * t_tx` in ms (the HARQ process and RBs held, without `t_up`), per-attempt `bler` |
| `num_infeasible_flows` | number of flows with an empty `P_i` |

## Exit status

`0` when the report was written (including when flows are infeasible); `1` on
a missing or malformed environment variable; `255` (`-1`) when an input file
cannot be opened or the MCS table is in the retired format (it then points at
the generator script).
