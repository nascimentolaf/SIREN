# Configuration

Every run parameter is an **environment variable**. `scripts/parameters.sh`
sets the defaults; every value there is written `${VAR:-default}`, so anything
already exported wins. `scripts/run_paper_experiment.sh` sources it, after
setting the paper's experiment settings itself (see
[Scripts](scripts.md#run_paper_experimentsh)).

`parameters.sh` is *sourced*, never executed. It derives every path from its
own location, so the scripts run from any directory. The C++ binaries read
the variables directly from the environment; running one by hand therefore
needs them exported first:

```bash
set -a; source scripts/parameters.sh; set +a
```

The planner fails with a clear message when a required variable is missing.

## Radio and timing model

| Variable | Default | Unit | Read by | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `RBs` | `135` | RBs | baselines (`run_paper_experiment.sh` sets it to each pair's budget); passed to `siren` as `--n-rb` | `B^max`, resource blocks per TTI (135 = 100 MHz FR1 carrier at 60 kHz SCS, TS 38.101-1) |
| `HARQs` | `3` | processes | baselines; passed to `siren` as `--m-harq` | `M`, number of HARQ processes |
| `TTI` | `0.00025` | **seconds** | planner, baselines; passed to `siren` as `--tti` in ms | transmission time interval (0.25 ms, 60 kHz SCS) |
| `FEEDBACK_PER_RET` | `3 * TTI` | **seconds** | planner (required), baselines (default `3*TTI`) | `k1`, HARQ feedback delay per attempt; `t_tx = TTI + FEEDBACK_PER_RET` |
| `UPPER_LAYER_DELAY` | `0.004` | **seconds** | planner (written to its report as `t_up_ms`, which `siren` reads), baselines | `t_up`, the time a packet spends in the other layers (UE + gNB processing). It holds neither a HARQ process nor RBs, but counts towards the deadline: a packet is delivered `t_up` after its last TB ends |
| `A_MAX` | `4` | attempts | planner, baselines | `A^max`, the layer-wide cap on HARQ attempts (Eq. (4)) |
| `RLC_HEADER_BYTES` | `8` | bytes | planner, baselines | RLC header carried by every segment (Eq. (2)); must be below the smallest TB size |
| `SINR_DB` | `10` | dB | planner, every baseline, the analysis scripts' reliability check | keeps only the MCS-table rows whose `sinr` equals this value, and fails if none do. `input/mcs_configurations.json` holds one SINR (10 dB) |

With the defaults one HARQ attempt costs `t_tx = 1 ms`, so a TB needing `A`
attempts holds its HARQ process and RBs for `C = A` ms: 2 to 4 ms. It must end
`t_up = 4` ms before its packet's deadline, so a configuration is admissible
when `C + 4 <= D`.

`parameters.sh` also exports `feedbackPerRet` and `upperLayerDelay`, copies of
two variables above in camelCase; no code in the repository reads them.

## Inputs and outputs

| Variable | Default | Read by | Meaning |
| :--- | :--- | :--- | :--- |
| `MCS_CONFIG` | `input/mcs_configurations.json` | planner, baselines, `check_catalogue.py` | link-layer table |
| `APP_DATABASE` | `input/app_database.json` | `check_catalogue.py` | application catalogue |
| `OUTPUT_DIR` | `output` | every script | root of `flowsets/` (the inputs), `sweep/`, `rq4_siren/` and `paper_run/`. The analysis stage needs the default |
| `NUMBER_OF_APPSETS`, `APP_PER_SET` | `1000`, `4` | `check_catalogue.py` | the flowsets the catalogue must allow (`C(30, 4)` combinations) |
| `FLOWSET_SEED` | `20260922` | `generate_flowset` | shuffle seed; empty means a random seed (not reproducible). The committed flowsets were drawn with it |

`APP_DATABASE`, `MCS_CONFIG` and `OUTPUT_DIR` may be relative paths: they are
resolved against the directory the run was launched from.
`run_paper_experiment.sh` and `sweep_experiment.sh` move into `scripts/` before
reading the parameters, so they record the launch directory in `LAUNCH_DIR`
first; set `LAUNCH_DIR` yourself only to resolve relative paths against some
other directory.

`parameters.sh` also defines variables that no shipped script reads:
`METHODS` (its own default, `ilp rr pf qospf must`; `run_paper_experiment.sh`
sets its own), `METHOD_VARIANTS`, `REUSE_FLOWSETS`, `CLEAN_OUTPUT`, and
`ILP_OBJECTIVE`, `ILP_RB_PEAK_SEARCH`, `ILP_TIMEOUT`, which
`run_paper_experiment.sh` overrides.

## ILP

What `run_paper_experiment.sh` passes to `siren`:

| Variable | Value in the paper's runs | Passed as | Meaning |
| :--- | :--- | :--- | :--- |
| `ILP_OBJECTIVE` | `rb_peak` (fixed) | `--objective` | `feasibility` (any table), `rb_peak` (minimise peak RB usage, Eqs. (13) and (14)) or `harq` (minimise HARQ processes used, Eqs. (11) and (12)) |
| `ILP_RB_PEAK_SEARCH` | `bounded` (fixed) | `--rb-peak-search` | `rb_peak` only. `bounded`: decide every candidate for feasibility, then minimise the peak over the schedulable ones in ascending mean-RB order, stopping at a provable bound. `full`: minimise the peak on every candidate |
| `ILP_TIMEOUT` | `0` (fixed) | `--timeout` | seconds for one flowset's **whole** candidate sweep. `0` = no flowset budget |
| `ILP_CANDIDATE_TIMEOUT` | `60` | `--candidate-timeout` | seconds for **one** candidate. When it runs out that candidate is skipped and the sweep continues. `0` = no cap |

A candidate stopped by either budget is *undecided*, never "unschedulable":
the analysis scores it `timeout`. Details in
[ILP evaluation](offline_phase/evaluation.md#two-time-budgets).

`GUROBI_HOME` (Gurobi's `linux64` directory) is needed to build `siren`, and
`GRB_LICENSE_FILE` tells Gurobi where the license file is when it is not in
one of its default places (see
[ILP evaluation](offline_phase/evaluation.md#license)).

## Baseline-specific

| Variable | Default | Read by | Meaning |
| :--- | :--- | :--- | :--- |
| `ALPHA`, `BETA`, `GAMMA` | `0.4`, `0.3`, `0.3` | `qospf` (required) | weights of the delay-urgency, GBR-deficit and priority terms of the utility. The QoS-PF paper's "Balanced" setting; its others are Delay-Tuned `0.7/0.2/0.1` and Fairness-Tuned `0.2/0.2/0.6` |
| `PF_EMA_LAMBDA` | `0.95` | `qospf` (required) | weight of the newest sample in QoS-PF's throughput average (Simu5G's `pfAlpha` default, as in the authors' code) |
| `PF_ALPHA` | `0.01` | `pf` | `1/t_c` of PF's rate average: the weight of the newest per-TTI sample, so the memory is `1/PF_ALPHA` TTIs (100 TTIs = 25 ms). The binary's own default, if unset, is `0.1` |
| `PF_INITIAL_THROUGHPUT` | `1.0` | `pf` | initial average rate, bytes/ms |

## Read by the baselines only

These are not in `parameters.sh` and no script of this repository sets them.
The paper's runs use their defaults.

| Variable | Default | Meaning |
| :--- | :--- | :--- |
| `EXEC_TIME_MODE` | `wcet` | `wcet`: every TB takes all `A_i` attempts. `sampled`: each TB takes the attempts it would really need, drawn from its BLER (see [Baselines](baselines/index.md#execution-time-model)) |
| `EXEC_SAMPLE_SEED` | `42` | seed of the `sampled` draws; draws are keyed by job identity, so every method faces the same run for a given seed |
| `FLOW_CONFIG` | unset | path to a JSON object imposing one configuration per application instead of the method's own choice (see [Baselines](baselines/index.md#configuration-selection)) |
