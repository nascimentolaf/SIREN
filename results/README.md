# The paper's results

This folder holds the results of the run behind the Performance Evaluation
section of *SIREN: Table-Driven Real-Time MAC Scheduler*: the CSVs, the two
tables, the figures, the LaTeX macros, and the inputs of the RQ4 models. Every
number of that section comes from these files. SIREN is the method `ilp` in
every file, solved by the program `siren`.

The numbers come from three experiments:

- the **RB-budget sweep**: all 1000 flowsets of `output/flowsets/`, at 51, 79,
  107 and 135 RBs, for the five methods `ilp`, `must`, `qospf`, `rr` and `pf`;
- **RQ4**: all 966 flowsets that SIREN schedules at 135 RBs, each with its
  minimum-peak tuple, at five target hyperperiods;
- the **search**: each baseline run on every tuple of all 1000 flowsets, at
  every budget. A flowset is schedulable for "baseline + search" if one of its
  tuples meets every deadline. Such a search needs SIREN's configuration
  analysis (Section V) and an offline simulation of every tuple, so the paper
  treats it as an ablation of SIREN, not as a scheduler a gNB runs. Its
  per-tuple CSVs are not shipped (about 700 MB per budget);
  `csv/baseline_search.csv` is their reduction.

## The files

| File | What it holds | Written by |
| :--- | :--- | :--- |
| `csv/output_rb51.csv`, `output_rb79.csv`, `output_rb107.csv`, `output_rb135.csv` | the verdict table of one budget: 5000 rows, one per (method, flowset) | `scripts/sweep_experiment.sh --collect`, through `analysis/collect_verdicts.py` |
| `csv/output_sweep.csv` | the four tables stacked, with a leading `rb` column: 20 000 rows | `scripts/sweep_experiment.sh --collect` |
| `csv/derived_baseline_configs.csv` | per (budget, baseline, flowset): `max_attempts`, `max_c_tti` and `c_gt_d`, read from the baselines' schedules: 16 000 rows | `analysis/plot_paper.py --derive` |
| `csv/derived_ilp_timing_rb135.csv` | per flowset at 135 RBs: `n_candidates`, `n_capped`, `n_errored`, `decide_all_s`, `first_table_s`, `median_candidate_s`, `host`, parsed from the ILP's logs: 1000 rows | `analysis/plot_paper.py --derive` |
| `csv/rq4_hyperperiod.csv` | one row per RQ4 model: periods, size (`n_jobs`, `n_binaries`, `n_constraints`), `status`, `solve_s`, `wall_s`, `host`, `solver`: 4816 rows. `peak_mem_mb` is empty in this run | `analysis/rq4_hyperperiod.py collect` |
| `csv/rq4_statistics.csv` | RQ4, one row per target hyperperiod: number of models, mean, median, standard deviation, quartiles, extremes, the Student t and BCa bootstrap 95 % intervals of the mean, and other descriptive statistics, in seconds: 5 rows | `analysis/rq4_statistics.py`, called by `analysis/plot_paper.py` |
| `csv/rq4_regression.json` | RQ4's regressions of the solving time on the hyperperiod and their checks (see below) | `analysis/rq4_statistics.py`, called by `analysis/plot_paper.py` |
| `csv/baseline_search.csv` | the search, per (budget, baseline, flowset): `n_tuples` (tuples run), `n_ok` (tuples on which every deadline is met) and `best_peak` (the lowest `rb_peak` among those, empty if none): 16 000 rows | `analysis/baseline_search.py derive` (or `analysis/plot_paper.py --derive`), from the per-tuple CSVs of `analysis/sweep_configurations.py` |
| `figures/table_rq2.tex` | the paper's Table IV (the tabular): schedulable shares and median peak RB usage per budget | `analysis/plot_paper.py` |
| `figures/table_rq4.tex` | the paper's Table V (the tabular): RQ4's solving time per hyperperiod, in milliseconds | `analysis/rq4_statistics.py`, called by `analysis/plot_paper.py` |
| `figures/numbers_results.tex` | the paper's numbers, as LaTeX macros | `analysis/plot_paper.py` |
| `figures/fig_rq2_verdicts.{pdf,png}`, `fig_rq2_scaling.{pdf,png}`, `fig_rq3_rb_usage.{pdf,png}`, `fig_rq3_residual_gain.{pdf,png}`, `fig_rq4_time.{pdf,png}`, `fig_rq4_hyperperiod.{pdf,png}` | the figures of the same run, which the paper does not print | `analysis/plot_paper.py` |
| `rq4_models/flowset_NNNN/H<ms>.json` | the 4816 RQ4 models, for 966 flowsets, in `siren`'s input format (`<ms>` is the target hyperperiod) | `analysis/rq4_hyperperiod.py prepare` |

The columns of the verdict tables are described in
[Analysis](../doc/analysis/index.md#the-verdict-table).

In `derived_ilp_timing_rb135.csv` and `rq4_hyperperiod.csv`, `host` is
`server1` or `server2`, the two servers of the run. `plot_paper.py` does not
read it. `rq4_statistics.py` uses it only to tell the two servers apart, for
the fits of each server alone in `rq4_regression.json`, which name the servers
`slower` and `faster`.

`rq4_models/` is a record: `scripts/run_paper_experiment.sh` prepares these
models again in `output/rq4_siren/`. `rq4_hyperperiod.py run` writes its results
next to the models, so copy a model out of `results/` to solve it.

## Where each number comes from

`numbers_results.tex` holds the numbers of the section as `\newcommand` macros,
for example `\OkIlpHundredThirtyFive`. The macros and the two tables are
computed by `analysis/plot_paper.py`, which calls
`analysis/rq4_statistics.py` and `analysis/baseline_search.py`, from the CSVs
above.

| Numbers | Macros | Source |
| :--- | :--- | :--- |
| Verdicts | `Ok*`, `N*`, `IlpUnsched*`, `IlpUndecided*`, `MissOnProved*`, `OkOnUnsched*`; the upper part of `table_rq2.tex` | the `verdict` column of `output_sweep.csv`, set by `collect_verdicts.py`. `ilp`: `ok` if a tuple is proved schedulable, else `timeout` if a tuple hit a limit or ended in a solver error, else `ko_unschedulable`. Baselines: `ok`, or `ko_deadline_miss` if a packet is late or dropped in the simulated hyperperiod. `plot_paper.py` turns a baseline's verdict into `ko_amax` when `max_attempts` of `derived_baseline_configs.csv` is above 4; in these files it is at most 4. These files hold only `ok` and `ko_unschedulable` for `ilp`, and only `ok` and `ko_deadline_miss` for the baselines |
| Peaks | `Usage*`, `UsageFlowsets*`, `PeakOptimal`; the lower part of `table_rq2.tex` | `rb_peak` of `output_sweep.csv`, the highest RB usage of any TTI of the table, measured by `collect_verdicts.py`. For `ilp` it is the table of the tuple with the smallest peak. `Usage*` is the median of `rb_peak` as a share of the budget, on the flowsets all five methods schedule at that budget. `PeakOptimal` counts the `ok` rows of `ilp` at 135 RBs whose `rb_peak_optimal_over_decided` is true |
| Gains | `Gain*`, `Paired*`, `Worse*`, `WinGain*` | on the flowsets that both `ilp` and one baseline schedule, the residual `rb_capacity - rb_peak` of `ilp` minus the baseline's: median and quartiles (`Gain*`). `WinGain*` does the same with `rb_window_mean_peak`. A budget and baseline with fewer than 10 such flowsets has `--` |
| Per-tuple times | `MedianCandidateSec`, `CappedTupleShare` | `derived_ilp_timing_rb135.csv`: the median over the flowsets of `median_candidate_s`, and the sum of `n_capped` over the sum of `n_candidates`. A tuple cut at the limit counts at the limit, 60 s |
| First-table times | `FirstTableMedianSec`, `FirstTableWithin*` | `first_table_s` of `derived_ilp_timing_rb135.csv`, on the flowsets `ilp` schedules at 135 RBs: the sum of the solving times of the tuples up to the first schedulable one, in sweep order |
| Tuple counts | `MedianTuples`, `MaxTuples` | `n_candidates_total` of the `ilp` rows at 135 RBs in `output_sweep.csv` |
| RQ4, per hyperperiod | `RqFourModels`, `RqFourFlowsets`, `RqFourCapSec`, `RqFourMedianSec*`, `RqFourTimeoutShare*`, `RqFourMedianJobs*`, `RqFourMedianBinaries*`, `RqFourDecidedUpToSec`, `RqFourJobsGrowth`, `RqFourTimeGrowth` | `rq4_hyperperiod.csv`, grouped by `target_h_ms`: the median of `solve_s` and of `n_jobs` and `n_binaries`, and the share of `status` equal to `timeout` (`plot_paper.py`) |
| RQ4's statistics | `table_rq4.tex`, `RqFourNMin`, `RqFourNMax`, `RqFourInfeasible*`, `RqFourSd*`, `RqFourCv*`, `RqFourCiShare`, `RqFourMaxSec`, `RqFourMaxShare`, `RqFourLastMinSec`, `RqFourLastMaxSec`, `RqFourRate*`, `RqFourMsPerJob*` | `rq4_statistics.py`, on `rq4_hyperperiod.csv` grouped by target hyperperiod (`rq4_statistics.csv`). Within a target the models are distinct flowsets, so the 95 % interval of the mean is Student's t over them; the BCa bootstrap interval is written beside it as a check. A model whose status is not a decision would have only a lower bound for its time, and the script then computes no mean and no regression; here every model is decided (16 are proved infeasible, and their times count) |
| RQ4's regression | `RqFourSlope*`, `RqFourIntercept`, `RqFourRsq*`, `RqFourSpearmanMin`, `RqFourLofTest`, `RqFourLastLeverage`, `RqFourBpP`, `RqFourLogBpP`, `RqFourExponent*`, `RqFourHRsq`, `RqFourBinariesRsq`, `RqFourServerGap*`, `RqFour*Server` | `rq4_statistics.py`, in `rq4_regression.json`: the least-squares line of `solve_s` on `H`, both in seconds, with standard errors clustered by flowset (CR1), since a flowset slow at one hyperperiod is slow at all of them; two checks on its slope (the t interval of the per-flowset slopes, and a bootstrap over flowsets); the line without the largest hyperperiod; a test of exact linearity against one mean per target (clustered Wald F); Breusch-Pagan tests; the power law on logarithmic axes, against `H`, jobs and binaries; and the same fits on each server alone (`servers`), with their time ratio per target for a given flowset |
| The search | `NSearchRuns`, `SearchOk*`, `SearchN*`, `SearchOnly*`, `SearchGain*`, `SearchPaired*`, `SearchWorse*`, `SearchOkPfFamily*`, `SearchGainPfFamily*` | `baseline_search.csv`, through `baseline_search.compare()`. `NSearchRuns` is the sum of `n_tuples`. Per budget and baseline: `SearchOk*` and `SearchN*` are the share and number of the flowsets with `n_ok > 0`; `SearchOnly*` is how many of them `ilp` does not schedule; on the flowsets both schedule, `SearchGain*` is the median and quartiles of the baseline's `best_peak` minus the `rb_peak` of `ilp` (SIREN's gain in guaranteed residual over the baseline's lowest-peak tuple), `SearchPaired*` their number and `SearchWorse*` how many have a negative gain. `*PfFamily*` are the lowest and highest of QoS-PF, RR and PF |

`table_rq2.tex` gives, per budget, the share of the flowsets each method
schedules (verdict `ok`), with each budget's highest share in bold, then the
median peak RB usage as a share of the budget on the flowsets all five methods
schedule, for SIREN and for the baselines. The four baselines' medians
coincide at every budget, so they share one row, "All baselines". The last row
counts those flowsets.

`table_rq4.tex` gives, per target hyperperiod `H` (in ms), the mean, its 95 %
interval, the standard deviation, the minimum, the quartiles, the median and
the maximum of the solving time, in milliseconds. The number of models per
hyperperiod is in the file, commented out of the table.

`NGenerated`, `AppAppearancesMin`, `AppAppearancesMax` and `HarmonicPairShare`
are read from `output/flowsets/` and `input/app_database.json`. The times are
measurements on the servers below.

## Regenerating the macros and the tables

`analysis/plot_paper.py` with no option needs the CSVs, `output/flowsets/` and
`input/app_database.json`; it runs no scheduler and needs no Gurobi. `analysis/`
holds its input and output folders, which git ignores. These commands replace
what `analysis/csv/` and `analysis/figures/paper/` hold:

```bash
mkdir -p analysis/csv
cp results/csv/* analysis/csv/
python3 analysis/plot_paper.py
cmp analysis/figures/paper/numbers_results.tex results/figures/numbers_results.tex
cmp analysis/figures/paper/table_rq2.tex results/figures/table_rq2.tex
cmp analysis/figures/paper/table_rq4.tex results/figures/table_rq4.tex
```

The three comparisons find no difference. `plot_paper.py` also writes
`rq4_statistics.csv` and `rq4_regression.json` again, into `analysis/csv/`:
they agree with `results/csv/` to at least 6 significant digits, except
possibly the bootstrap values (the `bca_*` columns and
`slope_check_cluster_bootstrap`), which can move slightly with the versions of
NumPy, pandas and SciPy; no macro or table reads them. The figures are written
again too. A PDF records its creation date and a PNG the Matplotlib version,
so compare them rendered (`pdftoppm`) rather than byte by byte.

## The run

| | |
| :--- | :--- |
| Solver | `siren`, Gurobi 13.0.1, one thread, zero optimality gap |
| Servers | two, each with two AMD EPYC 9374F 32-Core Processor CPUs, 64 physical cores |
| Sweep | `scripts/run_paper_experiment.sh -j 50 --shard K/2`, one shard on each server: 50 workers per server, each pinned to its own physical core, each running one pair (budget, flowset) at a time. 51, 79, 107 and 135 RBs, `M = 3`, objective `rb_peak` with the bounded search, 60 s per tuple, no budget per flowset |
| Model | TTI 0.25 ms, `t_up` 4 ms, `A^max` 4 |
| RQ4 | `rq4_hyperperiod.py prepare --reports output/sweep/rb135/reports/ilp --sample 1000`, with Python 3.10: all 966 flowsets that `siren` schedules at 135 RBs, their minimum-peak tuple, target hyperperiods of 420, 840, 2520, 8400 and 25 200 ms. Then `run --cap 3600 --harqs 3 -j 12 --shard K/2` on each server: 12 models at a time per server at every target, each solved at 135 RBs under the feasibility objective, `M = 3`, 3600 s per model |
| Search | `analysis/sweep_configurations.py`, once per budget on that budget's reports, with the baselines `must`, `pf`, `qospf` and `rr`: 135 and 107 RBs on one server with 100 processes at a time, 79 and 51 RBs on the other with 60. Each budget has 1 821 541 tuples, so 7 286 164 baseline runs and 9 107 705 rows (the `ilp` row and one row per baseline per tuple) |

RQ4 has 4816 models: 14 pairs (flowset, target) have no periods within 1.5
times the original ones that reach the target, and are skipped, which leaves
958 models at 8400 ms, 960 at 25 200 ms and 966 at each other target. All 4816
are decided within the limit: 4800 have a table and 16, from 6 flowsets, are
proved infeasible.

To compare a run of your own with these files, see
[`RUN_EXPERIMENTS.md`](../RUN_EXPERIMENTS.md#6-the-outputs).
