# Proportional fair (`pf`)

Classic proportional fairness: serve the job whose achievable rate is largest
relative to its flow's average past rate. No deadline and no RB-cost term
enter the metric.

Source: `baseline_algorithms/pf/` (`main.cpp`, `pf.cpp`). Shared behaviour is
described in [Baselines](index.md).

## Policy

At every scheduling instant (a TTI with a release or a free HARQ process),
while a process is free, each flow offers its next ready job if the job's `B`
fits in the free RBs; the offers are ranked by

```text
metric_i = r_i / R_i
r_i      = useful payload bytes of the job / its duration C   (achievable rate, bytes/ms)
R_i      = the flow's average past rate (bytes/ms), as at the start of the TTI
```

and the highest metric starts on the lowest-index free process. The offers are
then taken afresh, so a flow just served offers its next job in the same TTI:
the scheduler is work-conserving, like the other baselines. Ties go to flowset
order, then release order, then segment order. `C` is the worst case, `A`
attempts: under `EXEC_TIME_MODE=sampled` the process is held for the attempts
the job really takes, but ranking on those would assume the scheduler knows
them in advance.

`R_i` is a single exponential moving average over **TTIs**, as in the
standard definition `R_i(t) = (1 - 1/t_c) R_i(t-1) + (1/t_c) r_i(t)`:

- after each TTI the flow takes the sample `r_i(t)`, the summed rate of the
  jobs it started in the TTI (0 if none), with weight `PF_ALPHA` (`= 1/t_c`);
- every TTI the flow is **not** served it takes a zero sample, so its average
  decays by `(1 - PF_ALPHA)` per TTI, compounded over the gap between two
  scheduling events. That decay is what lets an unserved flow climb the
  ranking.

All flows start at `R_i = PF_INITIAL_THROUGHPUT`.

## Parameters

| Variable | `parameters.sh` | Binary default | Meaning |
| :--- | :--- | :--- | :--- |
| `PF_ALPHA` | `0.01` | `0.1` | `1/t_c`: weight of the newest sample. Memory = `1/PF_ALPHA` TTIs; 100 TTIs = 25 ms at the default |
| `PF_INITIAL_THROUGHPUT` | `1.0` | `1.0` | initial `R_i`, bytes/ms; must be `> 0` |

## Specifics

| | |
| :--- | :--- |
| `config.objective` | `proportional_fair` |
| Default output (no 3rd argument) | `./<flowset stem>_pf.json` |
| Reads `SINR_DB` | yes |
| Extra report fields | none |

PF is the only baseline whose ordering depends on *achieved* service. Under
`EXEC_TIME_MODE=sampled` an early completion changes the averages and so every
later decision, which is why PF is the method most affected by early HARQ
termination (see [Baselines](index.md#execution-time-model)).
