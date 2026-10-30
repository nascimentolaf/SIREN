# Inputs

What is in `input/`, the formats the pipeline reads, and how the application
catalogue was built.

| File | Status | Read by |
| :--- | :--- | :--- |
| `app_database.json` | the application catalogue, 30 applications | `generate_flowset`, `scripts/check_catalogue.py`, `analysis/` |
| `mcs_configurations.json` | the link-layer table | planner, every baseline, `analysis/` |

The flowsets themselves live in `output/flowsets/` (the only part of
`output/` tracked in git); see
[Flowset generation](offline_phase/generate_flowset.md).

## Application catalogue (`app_database.json`)

A JSON object keyed by application name. Each application is one periodic
downlink flow:

| Field | Type | Meaning |
| :--- | :--- | :--- |
| `type` | string | traffic class; `"RT"` throughout the catalogue |
| `packetsize` | integer | payload per packet, bytes (`S_i`) |
| `error` | number | target residual packet error rate after HARQ (`E_i`), e.g. `1e-05` |
| `period` | number | packet period, ms (`T_i`) |
| `deadline` | number | relative deadline, ms (`D_i`), measured from the packet's release and **including** the upper-layer delay `UPPER_LAYER_DELAY` |
| `5qi` | integer | optional 3GPP 5QI (TS 23.501 Table 5.7.4-1); used only by QoS-PF (see [below](#the-5qi-field)) |

```json
"ProcessControl_T60": {
  "type": "RT", "packetsize": 256, "error": 1e-05,
  "period": 60, "deadline": 30, "5qi": 84
}
```

### The catalogue

Five reliability classes × six deadlines = 30 applications, named
`<Class>_T<period>`. Deadlines are 10, 15, 20, 30, 40 and 60 ms. Each period
is a divisor of 420 ms = 2² · 3 · 5 · 7 between 1.6 and 2.5 times the
deadline, and the classes cycle through the choices a deadline has, so periods
rarely divide one another (Goossens and Macq's hyperperiod limitation). Any
flowset's hyperperiod therefore divides 420 ms = 1680 TTIs. Packet sizes cycle
over 128/256/512/1024 B, offset per class so that each deadline sees several
sizes. Each cell is period (ms) / packet size (B):

| Application | `E_i` | 5QI | D = 10 | D = 15 | D = 20 | D = 30 | D = 40 | D = 60 |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `Monitoring_T<period>` | 1e-3 | 88 | 20 / 128 | 28 / 256 | 42 / 512 | 60 / 1024 | 70 / 128 | 105 / 256 |
| `DiscreteAutomation_T<period>` | 1e-4 | 82 | 21 / 256 | 30 / 512 | 35 / 1024 | 70 / 128 | 84 / 256 | 140 / 512 |
| `ProcessControl_T<period>` | 1e-5 | 84 | 20 / 512 | 35 / 1024 | 42 / 128 | 60 / 256 | 70 / 512 | 105 / 1024 |
| `PrecisionControl_T<period>` | 1e-6 | 85 | 21 / 1024 | 28 / 128 | 35 / 256 | 70 / 512 | 84 / 1024 | 140 / 128 |
| `HighReliability_T<period>` | 1e-8 | 85 | 20 / 128 | 30 / 256 | 42 / 512 | 60 / 1024 | 70 / 128 | 105 / 256 |

What Section V makes of each class at the default parameters, as printed by
`python3 scripts/check_catalogue.py --table`:

| Reliability class | `E_i` | apps | Pareto set size | segments `N` | attempts `A` | RBs `B` |
| :--- | :--- | ---: | :--- | :--- | :--- | :--- |
| Monitoring and tracking | 1e-3 | 6 | 8 to 11 | 1 to 18 | 2 to 4 | 7 to 64 |
| Discrete automation | 1e-4 | 6 | 7 to 10 | 1 to 18 | 2 to 4 | 9 to 79 |
| Motion and process control | 1e-5 | 6 | 6 to 9 | 1 to 18 | 2 to 4 | 12 to 103 |
| Precision and safety | 1e-6 | 6 | 4 to 7 | 1 to 9 | 3 to 4 | 15 to 64 |
| High reliability | 1e-8 | 6 | 2 to 3 | 1 to 5 | 4 | 29 to 64 |

Every application is feasible on its own; the worst-case transmission time of
any retained configuration is 4 ms (`A^max = 4`, `t_tx = 1 ms`), to which the
upper-layer delay `t_up = 4 ms` adds towards the deadline (it holds no HARQ
process or RB).
A flowset of four draws a median of 1200 and at most 11 000 candidate
configurations for the ILP. `C(30, 4) = 27 405`, so the 1000 flowsets are drawn
without repetition.

### How it is built

- **Five reliability classes** (`1e-3`, `1e-4`, `1e-5`, `1e-6`, `1e-8`), the
  classes of the paper's application table, each anchored on the 5QI row whose
  service description is closest (88 motion tracking, 82 discrete automation,
  84 intelligent transport, 85 for the two strictest classes).
- **Deadlines 10 to 60 ms.**
- **Non-harmonic periods.** Each period is a divisor of 420 ms within
  `[1.6 D, 2.5 D]`, chosen per class from 20/21, 28/30/35, 35/42, 60/70, 70/84
  and 105/140 ms (for deadlines 10 to 60 ms) so that no class repeats a
  period. In the 1000 flowsets, 22% of the period pairs with distinct periods
  are harmonic (27% of all pairs, counting the 7% whose two periods are
  equal), and the hyperperiod is at most 420 ms (median 420 ms).
- **Feasible alone**: every application has at least one configuration with
  `C + t_up <= D`, so an infeasible flowset reflects contention between flows
  rather than an impossible application.

A 10 ms deadline leaves a TB 6 ms once `t_up` is set aside, less than two
worst-case `C` of 4 ms (`2C > D - t_up`) for the five applications with that
deadline. The catalogue does not require `2C <= D`, and `check_catalogue.py`
reports it as a note, not a failure.

### Checking the catalogue

`scripts/check_catalogue.py` re-runs Section V on the catalogue (in Python,
with the parameters of `scripts/parameters.sh`, overrides in the environment
included) and enforces what the evaluation relies on:

- every application has a non-empty Pareto set (feasible alone);
- every retained configuration has `C_i <= D_i`;
- `D_i <= T_i` (constrained deadlines);
- every period, deadline and retained `C_i` is a whole number of
  milliseconds (the baselines record whole milliseconds; see
  [Architecture](architecture.md#time-units));
- the periods' hyperperiod is at most 420 ms;
- `C(apps, apps per set) >=` the number of flowsets requested.

```bash
python3 scripts/check_catalogue.py                        # NUMBER_OF_APPSETS sets of APP_PER_SET
A_MAX=3 python3 scripts/check_catalogue.py                # as a run with A_MAX=3 would see it
python3 scripts/check_catalogue.py --sets 500 --apps-per-set 5
python3 scripts/check_catalogue.py --table                # the class table above
python3 scripts/check_catalogue.py --apps other.json --mcs other_mcs.json
```

It exits non-zero on any failed invariant. `TTI`, `FEEDBACK_PER_RET`,
`UPPER_LAYER_DELAY`, `A_MAX`, `RLC_HEADER_BYTES` and `SINR_DB`, and the
defaults of `--apps` (`APP_DATABASE`), `--mcs` (`MCS_CONFIG`), `--sets`
(`NUMBER_OF_APPSETS`) and `--apps-per-set` (`APP_PER_SET`), come from
`parameters.sh`, so the check follows any change there. It computes `t_tx`
and `C_i` exactly as the planner does, and gives the planner's Pareto sets.

### The `5qi` field

Only the QoS-PF baseline uses it, as the source of the priority term of its
utility: it takes the row's **default priority level** (lower = more important)
and normalises the levels of the flowset into `[0, 1]`. The row's delay budget
and error rate are not used, since the application's own `deadline` and `error`
govern scheduling.

| 5QI | 3GPP service | Priority level | Classes |
| ---: | :--- | ---: | :--- |
| 82 | Discrete Automation | 19 | DiscreteAutomation |
| 85 | Electricity Distribution (high voltage) | 21 | PrecisionControl, HighReliability |
| 84 | Intelligent transport systems | 24 | ProcessControl |
| 88 | Interactive Service (motion tracking) | 25 | Monitoring |

When an application omits `5qi`, QoS-PF infers one by matching `deadline` and
`error` against the 5QI table, preferring rows that satisfy both. Its log says
which happened for every flow (`5QI=84 (declared)` or `(inferred)`).

## MCS table (`mcs_configurations.json`)

The link-layer table: one entry per transport-block size, listing for each
usable MCS the per-attempt block error rate measured by link simulation and the
resource blocks one attempt costs. It is produced by
`bler/mcs_configurations/generate_mcs_configurations.py` from the
simulation results in `bler/data/` (see [BLER and MCS tables](bler.md)).

```json
[
  { "tbs": 66, "sinr": 10,
    "mcs": [ { "m": 0, "bler": 0.01903125, "b": 15 },
             { "m": 1, "bler": 0.037421875, "b": 12 } ] },
  { "tbs": 261, "sinr": 10,
    "mcs": [ { "m": 0, "bler": 0.0021640625, "b": 58 } ] }
]
```

| Field | Meaning |
| :--- | :--- |
| `tbs` | transport block size, **bytes** |
| `sinr` | SINR of the simulation, dB (the planner, the baselines and the analysis scripts keep only the tables at `SINR_DB`; see [Configuration](configuration.md#radio-and-timing-model)) |
| `mcs[].m` | MCS index |
| `mcs[].bler` | per-attempt error rate of a whole TB, as simulated: the value Eq. (4) uses |
| `mcs[].b` | resource blocks per attempt |

The shipped table has one SINR (10 dB) and four sizes: 66 B (29 MCS rows),
129 B (29), 261 B (28) and 469 B (25), costing 1 to 103 RBs. The lowest
per-attempt BLER is 2.16e-3 (261 B, MCS 0), which is what makes an attempt
count of 1 unreachable for every class. The readers ignore any other field
of an entry (older tables also carried `ni` and `bler_raw`).
Rows whose BLER is outside `(0, 1)` are dropped at generation time.

**Retired format.** A table bucketed by decade error targets
(`configurations` → `error` → `mkb: [{m, k, b}]`, `k` a precomputed attempt
count) would force the planner to round its Eq. (3) target to a decade, which
relaxes reliability targets by 2 to 3× for `N = 2` and `N = 3`. The planner and
the baselines reject that format with a message pointing at the generator.
`bler/mcs_configurations/mcs_configurations.json` is a file **in the
retired format**: same name as the input table, different schema; nothing in
the pipeline reads it.

## Flowset format

A flowset is a JSON array of applications: the catalogue entry plus its name
under `app`. Keys are sorted alphabetically.

```json
[
  { "5qi": 82, "app": "DiscreteAutomation_T30", "deadline": 15,
    "error": 0.0001, "packetsize": 512, "period": 30, "type": "RT" },
  { "5qi": 85, "app": "HighReliability_T30", "deadline": 15,
    "error": 1e-08, "packetsize": 256, "period": 30, "type": "RT" }
]
```

The planner reads `app`, `error`, `period`, `deadline`, `packetsize` and
`type`; the baselines additionally read `5qi` when present.
