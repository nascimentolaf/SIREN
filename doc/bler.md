# BLER and MCS tables

`bler/` produces the link-layer table every scheduler reads,
`input/mcs_configurations.json`: for each transport-block size and MCS, the
per-attempt block error rate at a worst-case SINR and the resource blocks one
attempt costs. You only need it to **regenerate** that table; the shipped one
is ready to use.

```text
bler/main.py  (Sionna link simulation, one TBS at a time)
      |
      v
bler/data/jakes_<TBS bytes>_mcs_bler_results_<SINR>.json
      |
      v
bler/mcs_configurations/generate_mcs_configurations.py
      |
      v
mcs_configurations.json  --copy-->  input/mcs_configurations.json
```

| Directory | Content | Status |
| :--- | :--- | :--- |
| `bler/` | Sionna simulation, plotting, table generation | **used**, produced the current table |

## Python / Sionna

### Requirements

Python 3.10 or later. The code uses the **Sionna 1.x** API (`sionna.phy.*`,
`device="cpu"` arguments) and SciPy; Sionna 0.x does not work.
`bler/requirements.txt` pins a set of versions that has been run
together (Sionna 1.2.2, TensorFlow 2.21.0, NumPy 1.26.2, SciPy 1.15.3,
Matplotlib 3.10.8):

```bash
cd bler
python3 -m pip install -r requirements.txt
```

Sionna also installs its ray-tracing package (`sionna-rt`, with `mitsuba` and
`drjit`), which this code does not use.

The simulation runs on CPU only: `main.py` hides every GPU
(`CUDA_VISIBLE_DEVICES=-1`) and parallelises with `multiprocessing` (start
method `spawn`). Run everything from `bler/`; output paths are relative
(`data/`, `img/`).

### Running a simulation: `main.py`

```bash
cd bler/
python main.py jakes        # or: python main.py awgn   (default: awgn)
```

The parameters are set in the `__main__` block of `main.py`; edit them there:

| Parameter | Value in the file | Meaning |
| :--- | :--- | :--- |
| `target_tbs_list` | `[129*8]` (the full set `[66, 129, 261, 469]×8` is commented) | TB sizes, **bits** |
| `snr_db_list` | `[10.0]` | worst-case subcarrier SINR, dB |
| `mcs_table`, `min_mcs`, `max_mcs` | `1`, `0`, `28` | PDSCH MCS table (TS 38.214) and index range; table 2 is capped at 27 |
| `scs`, `fc`, `speed` | 60 kHz, 3.8 GHz, 3 m/s | for the Jakes model (Doppler) |
| `target_num_block_errors` | `100` | stop an MCS after this many block errors |
| `num_blocks_inside_parfor` | `1000` | blocks per worker task |
| `max_num_blocks_per_worker` | `1e8` | safety cap |

For every MCS index it computes modulation order and code rate
(`decode_mcs_index`), the RBs needed (below), runs the Monte Carlo simulation
and records the result. Once an MCS returns BLER 1.0, every higher MCS is
recorded as 1.0 without being simulated.

Output: `data/<channel>_<TBS in bytes>_mcs_bler_results_<SINR>.json`, a list
with one object per MCS:

| Field | Meaning |
| :--- | :--- |
| `mcs_index`, `mcs_table` | MCS |
| `modulation_order`, `code_rate` | from the MCS table |
| `num_rbs` | RBs one transmission needs |
| `bler` | simulated block error rate |
| `snr_db` | SINR |
| `num_block_errors`, `num_blocks`, `execution_time_s` | Monte Carlo statistics |

### The simulation: `bler_core.py`

- `simulate_chunk(...)`: runs in a worker process and imports TensorFlow and
  Sionna there (importing before forking deadlocks). Per block: 5G NR TB
  encoding with `TBEncoder` (CRC, LDPC, rate matching), mapping, the channel,
  demapping and `TBDecoder`. Channels: `awgn`, or `jakes`, time-correlated
  Rayleigh fading generated through a precomputed Cholesky factor of the Jakes
  autocorrelation `J0(2π f_d Δt)`, with perfect equalisation.
- `helper_ldpc_bler_sim(...)`: distributes chunks over a `multiprocessing.Pool`
  until the target number of errors is reached; computes the Doppler
  `f_d = speed × fc / c` and the Cholesky factor for Jakes.
- `get_rbs(tbs, modulation_order, code_rate)` returns the RBs needed for one TB:
  `ceil(coded bits / (156 × Qm × layers))`, with 156 resource elements per RB
  (12 subcarriers × 14 symbols − 12 DMRS).

### Building the table: `mcs_configurations/generate_mcs_configurations.py`

```bash
cd bler/
python mcs_configurations/generate_mcs_configurations.py \
    data/jakes_66_mcs_bler_results_10.0.json data/jakes_129_mcs_bler_results_10.0.json \
    data/jakes_261_mcs_bler_results_10.0.json data/jakes_469_mcs_bler_results_10.0.json \
    --output mcs_configurations.json
cp mcs_configurations.json ../../input/mcs_configurations.json
```

- TBS and SINR are read from the **file name**, which must match
  `jakes_<tbs>_mcs_bler_results_<sinr>.json`; AWGN results are not accepted.
- The BLER is copied as simulated: `bler_core.py` encodes the whole TB and
  counts an error when its TB CRC fails, so it is already the rate at which
  one transmission of the whole TB is not decoded. Splitting a packet into
  several TBs is the planner's business, through Eq. (3) with the flow's own
  `N`.
- Rows whose `bler` is not in `(0, 1)` are dropped: those MCS never decode, or
  never fail within the simulation's precision.
- Output: the current format, described in [Inputs](input.md#mcs-table-mcs_configurationsjson).

`check_catalogue.py` should be re-run after replacing the table: the Pareto
sets, and so the feasibility of the catalogue, depend on it.

### Other scripts

| Script | Usage | Output |
| :--- | :--- | :--- |
| `extract.py` | `python extract.py <results.json> <key>` | the values of one field (e.g. `bler`, `num_rbs`), one per line, in `output.txt` |
| `plot_mcs.py` | `python plot_mcs.py data/<one results file>.json` | MCS vs BLER and MCS vs RBs, `img/<same name>.png` |
| `plot_multi_mcs.py` | `python plot_multi_mcs.py data/<file1>.json data/<file2>.json …` | the same for several files, `img/mcs_charts.png` |
| `mcs_configurations/from_json_to_mcs_conf.py` | `python mcs_configurations/from_json_to_mcs_conf.py data/<results>.json` | `mk_conf_<TBS>B_<SINR>dB.py` and `.tex`: for each decade target 1e-2…1e-8, the `(MCS, attempts)` choices, dominated ones removed (a presentation table) |

### Data

| Path | Content |
| :--- | :--- |
| `data/jakes_{66,129,261,469}_mcs_bler_results_10.0.json` | the results behind the current table (Jakes, 10 dB) |
| `data/past_cellringdata/` | earlier results at 0 and 20 dB (and two uplink files) with their `mk_conf_*` tables |
| `mcs_configurations/mk_conf_*_10dB.{py,tex}` | `from_json_to_mcs_conf.py` output for the 10 dB results |
| `mcs_configurations/mcs_configurations.json` | a table in the **retired** decade-bucket format (`configurations`/`mkb`), not the input table; the planner and baselines reject it, and nothing in the pipeline reads it |
| `output.txt` | last output of `extract.py` |
| `img/` | figures from the plotting scripts |

