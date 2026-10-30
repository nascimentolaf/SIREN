# Analysis

`analysis/` turns the sweep's reports into verdict tables, and those into the
paper's table, figures and LaTeX macros. Where `run_paper_experiment.sh`
produces reports, these scripts reduce them to **one row per (method,
flowset)**, so the methods can be compared. Nothing here runs a scheduler,
except `rq4_hyperperiod.py run`, which solves the RQ4 models with `siren`.

The distinction everything here is built on: **how a method arrives at "this
flowset is fine"**.

| Method | How it decides | What an `ok` is worth |
| :--- | :--- | :--- |
| baselines (`rr`, `pf`, `qospf`, `must`) | simulate one hyperperiod and look for a late packet | evidence about **the run that was simulated** |
| SIREN (`ilp`, solved by `siren`) | prove that a table meeting every deadline exists | evidence about **every run** |

| Script | Purpose |
| :--- | :--- |
| `collect_verdicts.py` | reports → verdict CSV |
| `plot_paper.py` | the paper's table, figures and macros, from the RB-budget sweep and RQ4 |
| `plot_evaluation.py` | plotting styles and helpers that `plot_paper.py` imports; also draws a set of evaluation figures of its own (see [Figures](figures.md)) |
| `rq4_hyperperiod.py` | RQ4: prepare, solve and collect the ILP models of stretched hyperperiods |

The `analysis` stage of `scripts/run_paper_experiment.sh` runs them in this
order, from empty CSVs:

```bash
(cd scripts && ./sweep_experiment.sh --collect)               # analysis/csv/output_rb<B>.csv, output_sweep.csv
python3 analysis/rq4_hyperperiod.py --out output/rq4_siren collect   # analysis/csv/rq4_hyperperiod.csv
python3 analysis/plot_paper.py --derive                       # analysis/csv/derived_*.csv
python3 analysis/plot_paper.py                                # analysis/figures/paper/
```

## Data flow

```text
 output/sweep/rb<B>/reports/<method>/...           output/rq4_siren/flowset_*/H*.result.json
          |                                                 |
          v  sweep_experiment.sh --collect                  v  rq4_hyperperiod.py collect
          |  (collect_verdicts.py per budget)               |
          v                                                 v
 analysis/csv/output_rb<B>.csv, output_sweep.csv    analysis/csv/rq4_hyperperiod.csv
          |                                                 |
          +-------------> plot_paper.py <-------------------+
                             ^      |
 output/sweep/ (--derive) ---+      v
                         analysis/figures/paper/
```

---

## The verdict table

One row per (method, flowset), as `collect_verdicts.py` writes it.
`scripts/sweep_experiment.sh --collect` prepends `rb` to the stacked table.

**Columns:**

| Column | Meaning |
| :--- | :--- |
| `flowset`, `method` | what was run |
| `verdict` | see below |
| `evidence` | `proof` (ILP), `simulation` (baselines), `configuration selection` (infeasible before scheduling), or `crash` |
| `n_flows`, `n_flows_placed` | flows in the set; flows actually placed |
| `hyperperiod_tti` | `H` |
| `n_deadline_misses` | late **packets** (a packet is late when its last TB's end plus `t_up`, the report's `config.t_up_tti`, exceeds its deadline), plus packets dropped by `must` |
| `n_dropped_packets` | `must` only: packets dropped at lifetime expiry |
| `worst_overrun_tti` | largest lateness of a packet |
| `rb_peak`, `rb_capacity`, `rb_violation` | peak RBs in any TTI of the table, `B^max`, and whether the peak exceeds it |
| `harq_overlap` | two TBs on one HARQ process at once |
| `harq_used` | HARQ processes appearing in the table |
| `t_min_tti`, `rb_window_mean_peak` | shortest period of the set, and the highest mean RB usage over any window of that length (cyclic over `H`). `rb_capacity - rb_window_mean_peak` is the mean capacity left to a non-real-time flow of period `t_min` (RQ3) |
| `rb_peak_optimal` | ILP under `rb_peak`: the peak the solver reports (equals `rb_peak` measured on the table) |
| `n_rb_peak_solved`, `n_rb_peak_pruned`, `n_rb_peak_timed_out`, `rb_peak_optimal_over_decided` | ILP under bounded `rb_peak`: the `rb_peak_search` block of the report |
| `worst_reliability_ratio`, `reliability_ok` | achieved packet error / `E_i`, worst flow; `<= 1` is safe |
| `n_candidates_total`, `n_candidates_tested`, `n_schedulable`, `n_candidates_timed_out`, `n_candidates_errored`, `timed_out` | ILP sweep counters (`n_candidates_errored`: candidates that ended in a solver error, undecided) |
| `solve_time_ms_median`, `solve_time_ms_max` | ILP per-candidate solve times |
| `n_jobs`, `n_variables`, `n_constraints` | ILP model size |
| `infeasible_flows` | the flows that made the set infeasible or unplaced, or a crash's stderr |
| `verdict_only` | ILP: the report comes from `siren --stop-at-first`, so `rb_peak` is left empty (never in the paper's runs) |

Under the `rb_peak` objective the ILP row describes the schedulable candidate
with the **smallest peak**; otherwise the first schedulable candidate.

**Verdicts:**

| Value | Meaning |
| :--- | :--- |
| `ok` | baselines: no packet missed its deadline in the simulated run. ILP: a schedule was proved to exist |
| `ko_deadline_miss` | at least one packet finished late (or, for `must`, was dropped) |
| `ko_infeasible` | some flow has no admissible configuration: Section V failed, scheduling never happened. For the ILP: empty Pareto set. For a baseline: every configuration is wider than the cell |
| `ko_unplaced` | a baseline declined to place a flow (none of the four baselines does) |
| `ko_unschedulable` | ILP: every candidate was proved unschedulable |
| `timeout` | ILP: no candidate proved schedulable and at least one hit a budget or ended in a solver error (`collect_verdicts.py` warns about the latter). Never "unschedulable" |
| `error` | the method wrote no report at all; recorded, so a hole in the grid is visible |
| `ko_amax` | only in `plot_paper.py`: a baseline run that used more than `A^max` attempts (see [below](#ko_amax)) |

How each report becomes a verdict is listed in
[Output formats](../output_formats.md#offline_phase_reportjson) for the ILP;
for baselines, in this order: `ko_infeasible` if the report says
`schedulable: false` with `infeasible_flows`; `ko_unplaced` if `unscheduled` is
non-empty; otherwise `ko_deadline_miss` or `ok` from the table.

---

## `collect_verdicts.py`

Reduces a `reports/` tree to the verdict CSV and prints a method × verdict
summary.

```bash
python3 analysis/collect_verdicts.py \
    --reports output/sweep/rb135/reports --flowsets output/flowsets \
    --mcs input/mcs_configurations.json --out verdicts_rb135.csv
```

| Option | Meaning |
| :--- | :--- |
| `--reports <dir>` | a `reports/<method>/<flowset>/` tree, such as `output/sweep/rb<B>/reports` |
| `--flowsets <dir>`, `--mcs <file>` | needed for the reliability check, which takes the BLERs of the tables at `SINR_DB` (default 10 dB) |
| `--out <file>` | CSV to write |
| `--per-report` | also print a summary counting **dispatch tables** rather than flowsets (they differ only for the ILP, which writes one per schedulable candidate), with how far the ILP's sweeps got; the CSV is unchanged |
| `--merge` | update `--out` instead of replacing it: the rows of the methods found under `--reports` are rewritten, every other row is kept as it is (fails if the columns differ) |

It reads `offline_phase_report.json` for the ILP and `combo_0000.json` for
every other method. A method with report directories but no reports appears in
`--per-report` with `reports = 0` and is absent from the verdict table; that is
how a crashed run becomes visible.

---

## Paper results (`plot_paper.py`)

The tables, figures and numbers of the paper's Performance Evaluation section,
from the RB-budget sweep (`B^max` in {51, 79, 107, 135}, 1000 flowsets of 4
applications, `M = 3`, 60 s per ILP candidate, no flowset budget) and from
RQ4 ([below](#rq4-solving-time-against-the-hyperperiod-rq4_hyperperiodpy)).

```bash
python3 analysis/plot_paper.py --derive   # the two derived CSVs, from output/sweep/
python3 analysis/plot_paper.py            # analysis/figures/paper/
```

Its inputs are in `analysis/csv/`:

| Input | Content |
| :--- | :--- |
| `output_sweep.csv` | verdicts of every budget (`sweep_experiment.sh --collect`) |
| `derived_baseline_configs.csv` | attempts and `C` each baseline committed to, per (budget, method, flowset), extracted from `output/sweep/` by `--derive` |
| `derived_ilp_timing_rb135.csv` | ILP time per flowset at 135 RBs, parsed from `output/sweep/rb135/logs/ilp/*.log` by `--derive`: `first_table_s` (sequential time to the first schedulable candidate) and `decide_all_s` (time to decide every candidate; both lower bounds when a candidate hit the cap), Gurobi's solve times without model building; `median_candidate_s`; `n_candidates`, `n_capped` (at the cap) and `n_errored` (solver errors); `host`, the machine that ran the flowset (the log's `Host:` line) |
| `rq4_hyperperiod.csv` | optional: one row per RQ4 model, from `rq4_hyperperiod.py collect`; without it the RQ4 figure and macros are not written |

`--derive` prints `wrote derived_ilp_timing_rb135.csv: <n> rows`. If there are
no ILP logs, it says the timing CSV was kept instead. It updates the derived
CSVs from whatever `output/sweep/` holds: a (budget, method) whose reports are
absent keeps its rows, which is why the analysis stage deletes the CSVs first.

**Methods.** The figures draw the methods of `PLOTTED` in `plot_paper.py`:
SIREN, MUST, QoS-PF, RR and PF. They label the ILP with the paper's name for
it, SIREN (`NAME`). The macros cover the ILP and the `BASELINES` (MUST,
QoS-PF, RR, PF).

| Output (`analysis/figures/paper/`) | Shows |
| :--- | :--- |
| `table_rq2.tex` | RQ2: the tabular of the verdict shares (%) per budget and method (sched., undec., unsch., missed), in bold each budget's highest schedulable share and SIREN's missed share |
| `fig_rq2_verdicts` | RQ2: verdict mix per method at the main budget (135) |
| `fig_rq2_scaling` | RQ2: schedulable share against the RB budget |
| `fig_rq3_rb_usage` | RQ3: each method's median peak RB usage as a share of `B^max`, on the flowsets all five plotted methods schedule at that budget, so that every method is measured on the same flowsets; the value is written at every point. The four baselines' peaks coincide on most flowsets (their configuration, the same for all, sets them), so their points are spread sideways and a value they share is written once, in black. A budget with no such flowset is left out |
| `fig_rq3_residual_gain` | RQ3: guaranteed residual capacity the ILP leaves beyond each baseline, paired on the flowsets both schedule (median, IQR). A budget where no drawn baseline has 10 pairs (`MIN_N`) is left off the x axis and has no median macro (`--`) |
| `fig_rq4_time` | the ILP's time to a first table, and to decide every candidate (sequential), at 135 RBs, from `derived_ilp_timing_rb135.csv` |
| `fig_rq4_hyperperiod` | RQ4: time to decide one ILP model against its hyperperiod, log-log; one point per model, coloured by outcome (table found, proved infeasible, time limit), with the median per target hyperperiod and the time limit |
| `numbers_results.tex` | `\newcommand` macros for every number the text quotes; its second line says what they were computed on. With RQ4 results, also `\RqFourModels`, `\RqFourFlowsets`, `\RqFourCapSec` and, per target hyperperiod spelled out digit by digit (`TwoFiveTwoZeroZero` for 25200), `\RqFourMedianSec…`, `\RqFourTimeoutShare…`, `\RqFourMedianJobs…` and `\RqFourMedianBinaries…` |

Each figure is written as `.pdf` and `.png`. Options: `--outdir` (default
`analysis/figures/paper`), `--width`/`--height` (inches, 1.7 × 1.45),
`--fontsize` (7), `--formats` (`pdf png`).

Some macros, for reading `numbers_results.tex`:

- the shares (`Ok*`) are of all the flowsets of the CSVs (1000 in the paper's
  run), and so is `HarmonicPairShare`, the share of their pairs of distinct
  periods in which one period divides the other (read from
  `output/flowsets/`);
- `NGenerated` is the number of flowsets in `output/flowsets/`;
  `AppAppearancesMin` and `AppAppearancesMax` are how many flowsets hold the
  least and the most frequent application;
- `LeadMustMin` and `LeadMustMax` are the ILP's smallest and largest lead over
  MUST across the budgets, in percentage points, from the shares as the RQ2
  table rounds them;
- `OkPfFamilyMin*` and `OkPfFamilyMax*` (per budget), and
  `MissOnProvedPfFamilyMin` and `MissOnProvedPfFamilyMax` (rb135), are the
  lowest and highest of QoS-PF, RR and PF (`PF_FAMILY`);
- the per-tuple times (`MedianCandidateSec`, `CappedTupleShare`) and the
  first-table times (`FirstTableMedianSec`, `FirstTableWithin*`) come from
  `derived_ilp_timing_rb135.csv`; the tuple counts (`MedianTuples`,
  `MaxTuples`, `MedianTuplesAll`) from `n_candidates_total` at 135 RBs.

### `ko_amax` {#ko_amax}

Baselines select from the unscreened configuration set, and fall back to
configurations needing more than `A^max` attempts when none within `A^max`
fits the cell (see [Baselines](../baselines/index.md#configuration-selection)).
The paper defines `A^max` as a layer-wide limit, so a baseline run whose
largest attempt count exceeds `A_MAX` is outside the model: `plot_paper.py`
reports it as `ko_amax` ("needs `A > A^max`"), leaving the raw CSV untouched.
On the shipped catalogue and MCS table it never happens: every application has
a configuration within `A^max` that fits even 51 RBs, so `ko_amax` counts 0 at
every budget (the `Amax*` macros).

---

## RQ4: solving time against the hyperperiod (`rq4_hyperperiod.py`)

Each model is one flowset with one configuration per flow, solved for a table
under the feasibility objective, on one thread, with a time limit (`--cap`,
1 h in the paper's runs). The flowsets are a sample (`--sample`, 100) of those
the sweep proved schedulable at the budget `--reports` names (135 RBs in
`run_paper_experiment.sh`; the default is `rb51`), and the configuration is
the sweep's minimum-peak tuple, read from
`output/sweep/rb<B>/reports/ilp/<flowset>/offline_phase_report.json`. Each model
records that budget (`n_rb`) and is solved at it, so the RB limit is as tight
as where the tuple was proved. Every flow keeps its payload, error target,
deadline and configuration; only its period grows, to a divisor of a target
hyperperiod `H*` in {420, 840, 2520, 8400, 25200} ms (multiples of 420, which
every catalogue period divides: Goossens and Macq's hyperperiod limitation),
at least the original period and at most 1.5 times it (`--stretch`), with lcm
exactly `H*`. A flowset with no such periods skips that `H*`. Since the
deadline stays, a longer period lowers the load, but the new release
alignments can still make a model infeasible: the figure tells the outcomes
apart.

The `rq4` stage of `run_paper_experiment.sh` runs, in `output/rq4_siren/`:

```bash
python3 analysis/rq4_hyperperiod.py --out output/rq4_siren prepare \
    --reports output/sweep/rb135/reports/ilp --sample 100
python3 analysis/rq4_hyperperiod.py --out output/rq4_siren run \
    --solver offline_phase/siren/siren -j <workers> --max-h 8400 --cap 3600 --harqs 3
python3 analysis/rq4_hyperperiod.py --out output/rq4_siren run \
    --solver offline_phase/siren/siren -j 12 --cap 3600 --harqs 3
python3 analysis/rq4_hyperperiod.py --out output/rq4_siren collect   # analysis stage
```

The first `run` solves the models up to 8400 ms, the second the larger ones
(the models already solved are skipped), fewer at a time since each needs
several GB. Always pass `--solver`: its default names a binary this repository
does not ship.

`run` solves the largest models first and records, per model, the outcome,
the solving time (the time limit for a timeout), the wall time, the peak memory
(only where `/usr/bin/time` exists), the machine (`host`) and the solver
binary's name (`solver`); it skips models that already have a result
(`--force` solves them again). `collect` adds each model's size (jobs,
binaries, rows, computed as the ILP builds it) and prints a summary per target
hyperperiod. The ILP prints times from 1000 s on in scientific notation;
`collect` times a decided model without a solving time by its wall time, and
says so in its `solve_s_source` column (`solver` or `wall`). Models solved at
the same time share the machine's caches and memory bandwidth, so the times
depend on how many run at once.

| Subcommand | Options |
| :--- | :--- |
| `prepare` | `--sample N` (100), `--seed S`, `--targets ...`, `--stretch 1.5`, `--tuple {sweep,lowest-mean}`, `--reports DIR`, `--flowsets DIR`; it adds to what `--out` holds |
| `run` | `--solver PATH`, `-j N`, `--cap SECONDS` (3600), `--n-rb B` (default: the budget each model records), `--harqs 3`, `--max-h MS`, `--force`, `--shard K/N` (only the K-th of N disjoint shares of the models, so N machines sharing `--out` can split them) |
| `collect` | `--csv FILE` (`analysis/csv/rq4_hyperperiod.csv`) |
| `extend` | `--targets ...`, `--stretch`: add targets to the flowsets already prepared |
| `control-prepare`, `compare` | a sample of decided models solved again one at a time, and its times compared with the first run's; the paper's runs do not use it |

`--out DIR`, before the subcommand, is the model directory (`output/rq4_siren`
in the paper's runs).

---

## Things to know before trusting a number

- **Separate configuration failures from scheduling failures.** A flowset that
  fails Section V (`ko_infeasible`) never reached a scheduler. With the shipped
  catalogue every application is feasible alone, so the ILP reports none.
- **Baselines select from the unscreened set**, so they can schedule a flow
  whose configuration misses its own deadline (it then misses on every packet),
  or use more than `A^max` attempts (`ko_amax` in the paper's figures). See
  [Baselines](../baselines/index.md#configuration-selection).
- **`reliability_ok` is true by construction** for every method: all derive
  their attempts from the reliability target through the same Section V code.
  It is a regression check, and would only discriminate if a method got its own
  link adaptation.
- **ILP counts depend on the budgets.** With a flowset budget, how many tables a
  flowset has says as much about where the budget ran out as about the flowset;
  a count is a property of the flowset only when the sweep completed. The
  paper's sweep sets no flowset budget for this reason.
- **ILP times are wall-clock times on the machine that ran them.** The
  per-candidate cap is wall-clock time too, so a candidate near the cap may be
  decided on one machine and not on another. The baselines are deterministic.
