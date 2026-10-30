# QoS-PF (`qospf`)

QoS-aware proportional fairness, after M. Seliem, U. Roedig, C. Sreenan and
D. Pesch, *"QoS-Aware Proportional Fairness Scheduling for Multi-Flow 5G UEs:
A Smart Factory Perspective"*, ACM MSWiM 2025, adapted to this framework's job
model.

Source: `baseline_algorithms/qospf/` (`main.cpp`, `qospf.cpp`) and the 3GPP
5QI table in `baseline_algorithms/common/qos_data.h`. Shared behaviour is
described in [Baselines](index.md).

## Per-flow context

Computed once, before simulating:

- **Configuration**: the shared selection rule ([Baselines](index.md#configuration-selection)).
- **5QI and priority**: the flow's `5qi` from the catalogue, or, if absent, one
  inferred by matching `deadline` and `error` against TS 23.501
  Table 5.7.4-1. Its *default priority level* is normalised over the flowset to
  `P_i` in `[0, 1]`, the most important (lowest level) flow getting 1. If every
  flow has the same level, all get 1.
- **GBR target**: `GBR_i = S_i × 8 / T_i` bits/ms, the rate that delivers one
  packet per period.

The log prints, per flow, the 5QI (`declared` or `inferred`), the priority
level and the GBR.

## Policy

At every decision instant (a TTI with a release or a free HARQ process), as in
the paper's per-TTI algorithm, every ready job is ranked once by

```text
M_i(t) = U_i(t) / R_i(t)
U_i(t) = ALPHA × D_i(t) + BETA × G_i(t) + GAMMA × P_i

D_i(t) = 1 / max(TTI, absolute deadline - t_up - t)   delay urgency
G_i(t) = max(0, 1 - R_i(t) / GBR_i)               GBR deficit
P_i                                               normalised 5QI priority
R_i(t)                                            the flow's average throughput, bits/ms
```

and jobs are then started in that order, each on the lowest-index free
process if its `B` fits in the free RBs (otherwise the next job is tried),
until no process is free. The `D` term is floored at one TTI (the smallest
actionable remaining time), which keeps it within about an order of magnitude
of `G` and `P` (both in `[0, 1]`) so the weights mean what they say. The
remaining time is counted to the deadline less the upper-layer delay `t_up`,
the time the TB itself has: the packet is delivered only `t_up` after its last
TB ends. Ties go to the earliest absolute deadline, then the application name.

`R_i` is an exponential moving average with factor `PF_EMA_LAMBDA`, the weight
of the newest sample, updated after each TTI as the authors' implementation
updates it:

- a flow with data queued in the TTI takes the sample `bits_i / TTI`, the
  payload of the jobs it started there (0 if none);
- over the following TTIs, a flow still holding queued data takes zero samples,
  and a flow with nothing queued keeps its average.

`R_i` starts at `1e-6`, so a flow never served has a very large metric.

## Correspondence with the paper and its code

The paper (Sec. III-C) defines the metric and the per-TTI algorithm: compute
`M_i` for every flow, sort, allocate in that order. It names an
"exponential moving average of the throughput" for `R_i` but gives neither
its update rule nor its weight. Its implementation (footnote 1: pull request 1
of `MohamedSeliem/Simu5G`, at the cited commit `6e0daf7f`) is Simu5G's PF
scheduler with its score multiplied by a fixed QoS weight: no `ALPHA`, `BETA`,
`GAMMA`, no delay urgency or GBR deficit. That weight starts with the factor
`(10 − 5QI + 1)`, negative for every 5QI of the catalogue (82 to 88), so it
cannot be followed as written. This scheduler therefore keeps the paper's
metric and per-TTI ranking, and takes from the code what the paper leaves
open: when and for which flows `R_i` is updated, and its weight (Simu5G's
`pfAlpha`, 0.95, which the released configurations do not override).

| Paper / code | Here |
| :--- | :--- |
| `M_i = U_i / R_i`, `U_i = α D_i + β G_i + γ P_i` (Eqs. 1, 2) | the same, with one `α, β, γ` for every flow, as in the paper's evaluation |
| `D_i`: inverse of the remaining time to the deadline | the same, floored at one TTI |
| `G_i`: GBR deficit | `max(0, 1 − R_i / GBR_i)`, `GBR_i` the rate of one packet per period |
| `P_i`: priority from the 5QI, normalised | the 5QI's default priority level, normalised over the flowset |
| each TTI: compute `M_i`, sort, allocate PRBs | each decision instant: rank the ready jobs once, start them in that order |
| `R_i` (code): after the TTI, bytes granted, flows with queued data only, weight `pfAlpha = 0.95` | the same, in bits per ms |
| PRBs per TTI to a flow's buffer | the framework's jobs: `B` RBs on one HARQ process for `C` |

## Parameters

All four are **required**; the binary stops if one is missing.

| Variable | `parameters.sh` | Meaning |
| :--- | :--- | :--- |
| `ALPHA` | `0.4` | weight of delay urgency |
| `BETA` | `0.3` | weight of GBR deficit |
| `GAMMA` | `0.3` | weight of 5QI priority |
| `PF_EMA_LAMBDA` | `0.95` | weight of the newest sample in the throughput average, in `(0, 1]` (the authors' Simu5G default) |

`0.4 / 0.3 / 0.3` is the paper's "Balanced (Default)" setting; it also
publishes Delay-Tuned `0.7 / 0.2 / 0.1` and Fairness-Tuned `0.2 / 0.2 / 0.6`:

```bash
ALPHA=0.7 BETA=0.2 GAMMA=0.1 ./scripts/run_paper_experiment.sh
```

## Specifics

| | |
| :--- | :--- |
| `config.objective` | `qos_pf` |
| Default output (no 3rd argument) | stdout |
| Reads `SINR_DB` | yes |
| Extra report fields | none |
