# Round-robin (`rr`)

Classic cyclic round-robin over flows: channel-independent, deadline-agnostic.
It is the reference "no policy at all" baseline.

Source: `baseline_algorithms/rr/` (`main.cpp`, `rr.cpp`). Shared behaviour
(configuration selection, job model, timing, output) is described in
[Baselines](index.md).

## Policy

The scheduler keeps one FIFO queue per flow and a round-robin pointer over the
flows. At every scheduling instant, while a HARQ process is free:

1. Starting at the pointer, take the first flow whose queue is non-empty **and**
   whose head job's `B` fits in the RBs still free. Flows whose head does not
   fit are skipped for this decision.
2. Start that job on the lowest-index free HARQ process, at the current TTI
   boundary, for its full duration `C`.
3. Move the pointer to the flow after the one just served.

So each decision serves at most one segment of a flow before moving on;
segments of the same packet, and several releases of the same flow, queue
behind each other in FIFO order. When nothing can start, time jumps to the
next release or completion.

## Specifics

| | |
| :--- | :--- |
| `config.objective` | `round_robin` |
| Default output (no 3rd argument) | `./<flowset stem>_rr.json` |
| Reads `SINR_DB` | yes |
| Extra environment | none |
| Extra report fields | none |

A job runs to completion even after its deadline; the verdict comes from
comparing each packet's last TB with its deadline (see
[Analysis](../analysis/index.md#the-verdict-table)).
