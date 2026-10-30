# Flowset generation

`offline_phase/generate_flowset/` draws **flowsets** (sets of distinct
applications) from the application catalogue. Every method is then run on the
same flowsets, so they are generated once and reused; the
1000 used by the paper are committed in `output/flowsets/`.

## Usage

```bash
cd offline_phase/generate_flowset
make                                                   # CMake build -> ./generate_flowset
FLOWSET_SEED=20260922 ./generate_flowset ../../input/app_database.json 4 1000 /tmp/flowsets
```

| Argument | Meaning |
| :--- | :--- |
| `<apps.json>` | the application catalogue (a JSON object; see [Inputs](../input.md#application-catalogue-app_databasejson)) |
| `<apps_per_set>` | applications per flowset, `> 0` and at most the catalogue size |
| `<number_of_sets>` | flowsets to write, `> 0` |
| `[output_dir]` | where to write them, created if missing. Default: `../../output/flowsets`, relative to the current directory (see [below](#where-it-writes)) |

| Environment | Meaning |
| :--- | :--- |
| `FLOWSET_SEED` | seed of the shuffle. Unset or empty: seeded from `std::random_device`, and the draw is not reproducible |

It exits 1 on a bad argument, an unreadable catalogue, more sets requested
than combinations exist, an output directory that cannot be created, or any
flowset that could not be written; 0 when all were written.

## What it does

1. Lists the catalogue's application names. The JSON library keeps object keys
   in **alphabetical** order, so this order does not depend on how the file is
   laid out.
2. Enumerates every combination of `apps_per_set` distinct applications
   (`C(n, k)` of them) in lexicographic order of those indices.
3. **Refuses** (exit 1) if more sets are requested than combinations exist,
   rather than silently writing fewer.
4. Shuffles the combinations with `std::mt19937` seeded from `FLOWSET_SEED` and
   writes the first `number_of_sets`.
5. Writes each as `flowset_0001.json`, `flowset_0002.json`, …: the catalogue
   entry of each application plus its name under `app` (a `packet_size` field
   is renamed `packetsize`). Format: [Inputs](../input.md#flowset-format).

Every flowset is a distinct combination; an application appears at most once
per flowset.

## Where it writes

To `output_dir` when given. The paper's experiments
(`scripts/run_paper_experiment.sh`) never run the generator: they use the
committed flowsets of `output/flowsets/`.

Without `output_dir` it writes to `../../output/flowsets` **relative to the
current directory**: the repository's `output/flowsets/` when run from
`offline_phase/generate_flowset/`. **Run that way by hand, it overwrites the
committed flowsets**; pass an `output_dir`, or restore the committed batch with
`git checkout -- output/flowsets`.

It overwrites `flowset_NNNN.json` files with the same numbers and leaves
higher-numbered ones in place. The absolute path written to is printed at the
end.

## Reproducibility

The same catalogue, `apps_per_set`, `number_of_sets` and `FLOWSET_SEED` give
the same flowsets with the same standard library. `std::shuffle`'s algorithm is
implementation-defined, so a different C++ standard library (libc++ instead of
libstdc++) may draw a different batch from the same seed. The committed batch
was drawn with `FLOWSET_SEED=20260922`, 4 applications per set, 1000 sets.
