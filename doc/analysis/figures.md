# Evaluation figures (`plot_evaluation.py`)

`analysis/plot_evaluation.py` holds the plotting styles that
[`plot_paper.py`](index.md#paper-results-plot_paperpy) imports (method order,
labels, colours, dash patterns and markers). Run on its own, it draws a set of
evaluation figures from a verdict CSV, plus `numbers.tex`, `\newcommand`
macros for every number the prose quotes. These figures are not the paper's:
the paper's come from `plot_paper.py`.

Its default inputs (`analysis/csv/output.csv` and
`analysis/csv/results_per_configuration.csv`) are written by no script of this repository, so pass a
verdict CSV and an output directory, for example the 135 RB verdicts a run
writes:

```bash
python3 analysis/plot_evaluation.py --list             # the figures it can draw
python3 analysis/plot_evaluation.py --verdicts analysis/csv/output_rb135.csv \
    --outdir evaluation_figures --only schedulability
```

| `--only` name | File | Shows |
| :--- | :--- | :--- |
| `schedulability` | `fig1_schedulability` | verdict mix per method (headline) |
| `coverage` | `fig2_configuration_coverage` | how many configurations each method can schedule (reads the per-configuration CSV) |
| `agreement` | `fig3_ilp_agreement` | baselines against the ILP's proof |
| `severity` | `fig4_miss_severity` | how late, and how many flows |
| `ilp-cost` | `fig5_ilp_cost` | ILP solve time and search coverage |
| `headroom` | `fig6_resource_headroom` | RB peak against the budget |
| `config-space` | `fig7_configuration_space` | candidate space per application (reads the planner reports) |
| `config-table` | `table_configurations.tex` | the same as a LaTeX table (reads the planner reports) |
| `numbers` | `numbers.tex` | macros only |

## Inputs

| Option | Default | Used by |
| :--- | :--- | :--- |
| `--verdicts PATH` | `analysis/csv/output.csv` (written by no script here) | every figure except `coverage` |
| `--per-config PATH` | `analysis/csv/results_per_configuration.csv` (written by no script here) | `coverage`, and the coverage macros |
| `--reports PATH` | `output/reports` | `config-space`, `config-table` (the ILP's `planner_report.json` files) |
| `--outdir PATH` | `analysis/figures` | PDFs, PNGs, `numbers.tex`, notes |

The verdict CSV is what `collect_verdicts.py` writes (see
[Analysis](index.md#collect_verdictspy)); any CSV with the same columns works.
The per-configuration CSV comes from a per-configuration sweep that is not
part of this repository, so `coverage` is skipped without it.

> **The coverage cache.** The per-configuration CSV can be hundreds of MB, so
> its per-flowset summary is cached in `<outdir>/.cache/coverage.csv` and
> reused. The cache is keyed to the **output directory, not the input file**:
> give each data set its own `--outdir`, or pass `--refresh`. A stale cache is
> the one way this script can quietly plot old numbers.
>
> The two CSVs must come from the **same run**, or `coverage` describes a
> different sweep from the other figures.

## What each figure needs

Columns are read by name; extra columns and column order do not matter. A
figure whose inputs are missing is **skipped with a message**; the others still
render.

| Figure | Needs, in the verdict CSV |
| :--- | :--- |
| `schedulability` | `flowset`, `method`, `verdict` |
| `coverage` | *(per-configuration CSV)* `flowset`, `method`, `verdict` |
| `agreement` | `flowset`, `method`, `verdict`, **and `ilp` rows** |
| `severity` | `method`, `worst_overrun_tti`, `n_deadline_misses`, `n_flows` |
| `ilp-cost` | **`ilp` rows** with `n_candidates_total`, `n_candidates_tested`, `solve_time_ms_median`, `solve_time_ms_max` |
| `headroom` | `rb_peak`, `rb_capacity`; optionally `rb_violation`, `harq_overlap`, `reliability_ok` |
| `numbers` | as much of the above as exists |

`numbers.tex` is always written, even with `--only`, so the macros never lag
the figures. Each run also writes `<figure>.note.txt` with the figure's data
summary and prints a suggested caption.

## When the data set differs

- **Fewer methods**: the missing ones are dropped from every figure, legend and
  macro, with one `note:` on stderr.
- **No ILP rows**: `agreement` and `ilp-cost` skip themselves; `numbers.tex`
  omits the `\Ilp*` and `\Recover*` macros.
- **A method the script does not know**: ignored with a note (see below).
- **Another flowset count**: every "n = …" and percentage follows the data.
- **Missing columns**: the figure names what it wanted and stands aside;
  `numbers.tex` defines only the macros the data supports.
- **Findings stated in words**, in `agreement` ("no baseline ever reported a
  schedule") and `headroom` ("none exceeded the RB budget"), are **counted**
  and the footnote phrased from the count. Do not hard-code these sentences.

## Values the CSV does not carry

| Option | Default | Why |
| :--- | :--- | :--- |
| `--tti-ms` | `0.25` | `worst_overrun_tti` is in TTIs and the lateness axis in ms; pass the run's TTI if it differs |
| `--candidate-cap` | `30` | drawn as a line in `fig5`; pass the run's `ILP_CANDIDATE_TIMEOUT`, or `0` for none |

## Other options

| Option | Default | Meaning |
| :--- | :--- | :--- |
| `--width` | `single` | `single` (3.5 in, IEEE column), `double` (7.16 in), or inches |
| `--font` | `serif` | `serif` (to sit beside a Times body) or `sans` |
| `--fontsize` | `8` | base point size |
| `--formats` | `pdf png` | any of `pdf`, `png`, `svg` |
| `--dpi` | `400` | raster resolution |
| `--no-legend` | off | `fig1`: no verdict legend (the caption names the colours) |
| `--ilp-cost-panels` | `both` | `fig5`: solve-time CDF and coverage (`both`) or the CDF alone (`time`) |
| `--embed-notes` | off | also draw each note under its figure |
| `--refresh` | off | rebuild the coverage cache |

PDF fonts are embedded and subset as Type 42 (no Type 3), so the PDFs pass IEEE
PDF eXpress as they are.

```latex
\input{figures/numbers.tex}   % once, in the preamble
The ILP schedules \OkIlp\ of the \NFlowsets\ flow sets.
\includegraphics[width=\columnwidth]{figures/fig1_schedulability.pdf}
```

## Adding, renaming or recolouring a method

Five dicts at the top of the script, keyed by the CSV's `method` value:
`METHODS` (also the plotting order), `LABEL`, `COLOR`, `DASH` and `MARKER`.
Renaming for a paper is a `LABEL` edit; adding a method needs an entry in all
five. A method missing from one of them raises a `KeyError`. The dicts also
style methods that the paper's CSVs contain but the paper does not report
(`edf`, `5gtps`, `5gtps_harq`); a method absent from the CSV is dropped from
every figure with a note.

Colours encode identity and were chosen for colour-blind safety: the four hues
of `ilp`, `qospf`, `pf` and `rr` keep every pair about 8 apart in OKLab (×100)
under deuteranopia, protanopia and tritanopia (9.2 at their worst pair) and
16.3 in normal vision. Every series also has its own dash pattern and marker,
so it survives grayscale. `must` (olive) was added afterwards, without a
recorded measurement for the enlarged set: re-check it before relying on
colour alone.

## Troubleshooting

| Symptom | Cause |
| :--- | :--- |
| coverage figure unchanged after new data | stale cache: new `--outdir`, or `--refresh` |
| `unknown figure(s): [...]` | typo in `--only`; `--list` prints the names |
| `note: ... has no rows for [...]` | that method is absent from the CSV; the figures adapt |
| `skipped: ... has no ilp rows` | expected for a baselines-only CSV |
| lateness axis off by a factor | wrong `--tti-ms` |
| a cap line where there was no cap | pass the run's `--candidate-cap`, or `0` |
| `KeyError` on a method name | in the CSV but not in all five dicts |
| a caption prints `n/a` | it names a method this data set lacks |
