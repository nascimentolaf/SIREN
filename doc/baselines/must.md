# MUST (`must`)

Massive URLLC Scheduling Technique, the deadline-aware look-ahead scheduler of
E. Khorov, A. Krasilov, I. Selnitskiy and I. F. Akyildiz, *"A Framework to
Maximize the Capacity of 5G Systems for Ultra-Reliable Low-Latency
Communications"*, IEEE TMC 20(6), 2021, Section 2.1, instantiated on this
framework's job model. Not an official release; only the scheduler is
implemented (not the paper's traffic model nor its priority groups). Method
authors: Khorov, Krasilov, Selnitskiy, Akyildiz.

Source: `baseline_algorithms/must/main.cpp`. Shared behaviour is described in
[Baselines](index.md).

## The algorithm in the paper

Every mini-slot, for the packets queued at the gNB:

1. sort by `(remaining lifetime, 1/c_p)`, where `c_p` is the gradient of a
   logistic utility of the flow's moving-average packet-loss estimate;
2. in that order, give each packet the free RBs of highest rate over the
   (RB × mini-slot) matrix up to its deadline, earliest column first among
   equal rates; a packet that cannot be completed before its lifetime expires
   gets nothing;
3. hand the RBs of the current mini-slot still free to a legacy scheduler so
   packets finish earlier.

Only the current mini-slot of the plan is executed; the plan is rebuilt every
mini-slot, and a packet whose lifetime expires is discarded.

## Mapping onto this framework

| Paper | Here |
| :--- | :--- |
| unit of work: bytes on RBs | the framework's TBs: a packet is `N` TBs, each holding `B` RBs on one HARQ process for its duration `C`, exactly the job model of the other baselines |
| mini-slot | one TTI; the plan is rebuilt every TTI |
| step 2 | for each remaining TB of the packet, the earliest (process, start) at which `B` RBs and a process are free for the TB's whole worst-case duration and it still ends by the deadline less the upper-layer delay `t_up` (the packet's lifetime at the gNB: `t_up` follows the last TB and holds nothing); TBs are placed in segment order and may run in parallel. If any TB has no slot, the packet reserves nothing this TTI |
| highest-rate RBs first | vacuous under the flat channel every method sees, so placement is earliest-slot first |
| step 3 | work-conserving pull-forward of planned TBs into the current TTI; with earliest-first placement it never finds anything to move, and is kept so the step exists if a frequency-selective channel is added |
| loss estimate, Eq. (1) | the same EWMA per flow, `β_i = PLR_target / 10`, `PLR_target` being the flow's error target `E_i`; updated on every delivery (0) and discard (1) |
| cost `c_p` (Lemma 1) and the tie-break of step 1 | `c_p = −F′(PLR_i − PLR_target)`, logistic slope `A_i = 1 / PLR_target` (the paper does not fix `A`). Equal deadlines are ordered by `β_i · c_p`, Lemma 1's first-order utility gain: the paper has one target for every flow, where `β` is a common factor and this is its order by `c_p`; with per-flow targets `β_i · A_i = 1/10` for every flow, so ties go to the flow whose loss estimate is closest to its own target, not to a reliability class. The gain is computed in that form, `1/10` times the logistic slope at `(PLR_i − PLR_target) / PLR_target`, so flows with equal gains tie exactly whatever their targets. Remaining ties: application name |
| HARQ bookkeeping | attempts are inside the TB duration (worst case for the plan; under `EXEC_TIME_MODE=sampled` the process frees when the TB decodes and the next TTI's plan sees it), the job model every method shares, rather than re-planning each retransmission after a NACK |
| discard at lifetime expiry (`d_p = 0`) | the same: a packet still holding an unstarted TB at its lifetime's end (deadline less `t_up`) is discarded, its loss counted in the estimate then, and listed in `dropped_packets`. Before that, step 2 finds no slot for a TB that can no longer end in time |
| MCS: the gNB's conservative link adaptation (per-attempt error below `10^-3`), an input to MUST | the shared configuration rule ([Baselines](index.md#configuration-selection)): no MCS of the 10 dB table reaches `10^-3` per attempt (the lowest is `2.16·10^-3`), so the attempts `A` the rule derives carry the reliability |
| traffic management (NAT) and congestion avoidance (§2.2, §2.3) | not used: the flows are periodic, and no flow is set aside |

## Reading its results

- MUST **never dispatches a late TB** (it drops the packet instead), so its
  `worst_overrun_tti` is always 0. `collect_verdicts.py` counts every dropped
  packet as a deadline miss (`n_dropped_packets`, included in
  `n_deadline_misses`). Compare MUST to the other methods on misses, not on
  overrun.
- Within one hyperperiod a flow's loss estimate moves only when one of its
  packets is lost, so until then every flow has the same `β_i · c_p` and
  step 1's order is earliest-deadline order: deadline, then application name.
  A loss is a deadline miss, which already makes the flowset ko, and the
  discard, too, only acts on a packet that has missed. What sets MUST apart
  from a plain earliest-deadline-first policy is step 2's look-ahead: it keeps
  RBs free for the slot it has reserved for a higher-ranked packet, or fills
  the gap before that slot with a shorter TB, where an earliest-deadline-first
  policy starts whatever fits now.

## Specifics

| | |
| :--- | :--- |
| `config.objective` | `must` |
| Default output (no 3rd argument) | `./<flowset stem>_must.json` |
| Reads `SINR_DB` | yes |

Extra report fields:

```jsonc
"dropped_packets": [ { "app": "...", "instance": 0, "release_tti": 0,
                       "deadline_tti": 64, "segments_not_started": 3,
                       "dropped_at_tti": 64 } ],
"n_dropped_packets": 1,
"must": { "plr_estimate": { "<app>": 1e-06 }, "n_packets": 20 }
```
