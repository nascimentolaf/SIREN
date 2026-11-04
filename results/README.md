# The paper's results

This folder holds the results of the run behind the Performance Evaluation
section of *SIREN: Table-Driven Real-Time MAC Scheduler*: the CSVs, the table,
the figures, the LaTeX macros, and the inputs of the RQ4 models. Every number
of that section comes from these files. SIREN is the method `ilp` in every
file, solved by the program `siren`.

The numbers are on all 1000 flowsets of `output/flowsets/`, at 51, 79, 107 and
135 RBs, for the five methods `ilp`, `must`, `qospf`, `rr` and `pf`.

## The files

| File | What it holds | Written by |
| :--- | :--- | :--- |
| `csv/output_rb51.csv`, `output_rb79.csv`, `output_rb107.csv`, `output_rb135.csv` | the verdict table of one budget: 5000 rows, one per (method, flowset) | `scripts/sweep_experiment.sh --collect`, through `analysis/collect_verdicts.py` |
| `csv/output_sweep.csv` | the four tables stacked, with a leading `rb` column: 20 000 rows | `scripts/sweep_experiment.sh --collect` |
| `csv/derived_baseline_configs.csv` | per (budget, baseline, flowset): `max_attempts`, `max_c_tti` and `c_gt_d`, read from the baselines' schedules: 16 000 rows | `analysis/plot_paper.py --derive` |
| `csv/derived_ilp_timing_rb135.csv` | per flowset at 135 RBs: `n_candidates`, `n_capped`, `n_errored`, `decide_all_s`, `first_table_s`, `median_candidate_s`, `host`, parsed from the ILP's logs: 1000 rows | `analysis/plot_paper.py --derive` |
| `csv/rq4_hyperperiod.csv` | one row per RQ4 model: periods, size (`n_jobs`, `n_binaries`, `n_constraints`), `status`, `solve_s`, `wall_s`, `host`, `solver`: 499 rows. `peak_mem_mb` is empty in this run | `analysis/rq4_hyperperiod.py collect` |
| `figures/table_rq2.tex` | the paper's Table IV (the tabular) | `analysis/plot_paper.py` |
| `figures/numbers_results.tex` | the paper's numbers, as LaTeX macros | `analysis/plot_paper.py` |
| `figures/fig_rq3_rb_usage.{pdf,png}`, `figures/fig_rq4_hyperperiod.{pdf,png}` | the two result figures the paper prints | `analysis/plot_paper.py` |
| `figures/fig_rq2_verdicts.{pdf,png}`, `fig_rq2_scaling.{pdf,png}`, `fig_rq3_residual_gain.{pdf,png}`, `fig_rq4_time.{pdf,png}` | the other figures of the same run, which the paper does not print | `analysis/plot_paper.py` |
| `rq4_models/flowset_NNNN/H<ms>.json` | the 499 RQ4 models, for 100 flowsets, in `siren`'s input format (`<ms>` is the target hyperperiod) | `analysis/rq4_hyperperiod.py prepare` |

The columns of the verdict tables are described in
[Analysis](../doc/analysis/index.md#the-verdict-table).

In `derived_ilp_timing_rb135.csv` and `rq4_hyperperiod.csv`, `host` is
`server1` or `server2`, the two servers of the run. No computation reads this
column.

`rq4_models/` is a record: `scripts/run_paper_experiment.sh` prepares these
models again in `output/rq4_siren/`. `rq4_hyperperiod.py run` writes its results
next to the models, so copy a model out of `results/` to solve it.

## Where each number comes from

`numbers_results.tex` holds the numbers of the section as `\newcommand` macros,
for example `\OkIlpHundredThirtyFive`. The macros and the table are computed by
`analysis/plot_paper.py`, from the CSVs above.

| Numbers | Macros | Source |
| :--- | :--- | :--- |
| Verdicts | `Ok*`, `N*`, `IlpUnsched*`, `IlpUndecided*`, `MissOnProved*`, `OkOnUnsched*`, `table_rq2.tex` | the `verdict` column of `output_sweep.csv`, set by `collect_verdicts.py`. `ilp`: `ok` if a tuple is proved schedulable, else `timeout` if a tuple hit a limit or ended in a solver error, else `ko_unschedulable`. Baselines: `ok`, or `ko_deadline_miss` if a packet is late or dropped in the simulated hyperperiod. `plot_paper.py` turns a baseline's verdict into `ko_amax` when `max_attempts` of `derived_baseline_configs.csv` is above 4; in these files it is at most 4. These files hold only `ok` and `ko_unschedulable` for `ilp`, and only `ok` and `ko_deadline_miss` for the baselines |
| Peaks | `Usage*`, `UsageFlowsets*`, `PeakOptimal` | `rb_peak` of `output_sweep.csv`, the highest RB usage of any TTI of the table, measured by `collect_verdicts.py`. For `ilp` it is the table of the tuple with the smallest peak. `Usage*` is the median of `rb_peak` as a share of the budget, on the flowsets all five methods schedule at that budget. `PeakOptimal` counts the `ok` rows of `ilp` at 135 RBs whose `rb_peak_optimal_over_decided` is true |
| Gains | `Gain*`, `Paired*`, `Worse*`, `WinGain*` | on the flowsets that both `ilp` and one baseline schedule, the residual `rb_capacity - rb_peak` of `ilp` minus the baseline's: median and quartiles (`Gain*`). `WinGain*` does the same with `rb_window_mean_peak`. A budget and baseline with fewer than 10 such flowsets has `--` |
| Per-tuple times | `MedianCandidateSec`, `CappedTupleShare` | `derived_ilp_timing_rb135.csv`: the median over the flowsets of `median_candidate_s`, and the sum of `n_capped` over the sum of `n_candidates`. A tuple cut at the limit counts at the limit, 60 s |
| First-table times | `FirstTableMedianSec`, `FirstTableWithin*` | `first_table_s` of `derived_ilp_timing_rb135.csv`, on the flowsets `ilp` schedules at 135 RBs: the sum of the solving times of the tuples up to the first schedulable one, in sweep order |
| Tuple counts | `MedianTuples`, `MaxTuples` | `n_candidates_total` of the `ilp` rows at 135 RBs in `output_sweep.csv` |
| RQ4 | `RqFour*` | `rq4_hyperperiod.csv`, grouped by `target_h_ms`: the median of `solve_s` and of `n_jobs` and `n_binaries`, and the share of `status` equal to `timeout` |

`table_rq2.tex` writes the shares of the verdicts per budget and method: sched.
is `ok`, undec. is `timeout`, unsch. is `ko_unschedulable`, missed is
`ko_deadline_miss`.

`NGenerated`, `AppAppearancesMin`, `AppAppearancesMax` and `HarmonicPairShare`
are read from `output/flowsets/` and `input/app_database.json`. The times are
measurements on the servers below.

## Regenerating the macros and the table

`analysis/plot_paper.py` with no option needs the CSVs, `output/flowsets/` and
`input/app_database.json`; it runs no scheduler and needs no Gurobi. `analysis/`
holds its input and output folders, which git ignores. These commands replace
what `analysis/csv/` and `analysis/figures/paper/` hold:

```bash
mkdir -p analysis/csv
cp results/csv/*.csv analysis/csv/
python3 analysis/plot_paper.py
cmp analysis/figures/paper/numbers_results.tex results/figures/numbers_results.tex
cmp analysis/figures/paper/table_rq2.tex results/figures/table_rq2.tex
```

Both comparisons find no difference. The figures are written again too. A PDF
records its creation date and a PNG the Matplotlib version, so compare them
rendered (`pdftoppm`) rather than byte by byte.

## The run

| | |
| :--- | :--- |
| Script | `scripts/run_paper_experiment.sh -j 50 --shard K/2`, one shard on each server |
| Solver | `siren`, Gurobi 13.0.1, one thread, zero optimality gap |
| Servers | two, each with two AMD EPYC 9374F 32-Core Processor CPUs, 64 physical cores |
| Workers | 50 per server, each pinned to its own physical core, each running one pair (budget, flowset) at a time |
| Sweep | 51, 79, 107 and 135 RBs, `M = 3`, objective `rb_peak` with the bounded search, 60 s per tuple, no budget per flowset |
| Model | TTI 0.25 ms, `t_up` 4 ms, `A^max` 4 |
| RQ4 | 100 flowsets that `siren` schedules at 135 RBs, solved at 135 RBs under the feasibility objective, 3600 s per model: 50 at a time per server up to `H*` = 8400 ms, then 12 at a time |

RQ4 has 499 models and not 500: flowset `flowset_0390` has no periods within
1.5 times the original ones that reach `H*` = 8400 ms, so it skips that
hyperperiod.

To compare a run of your own with these files, see
[`RUN_EXPERIMENTS.md`](../RUN_EXPERIMENTS.md#6-the-outputs).
