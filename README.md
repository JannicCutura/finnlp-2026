# Language Models on Knowledge Graphs — GLEIF text-to-SPARQL

A local, prompt-only system that turns plain-English questions about the GLEIF Legal
Entity Identifier register into **SPARQL queries**, executes them against a local
triplestore, and returns each answer with the query and the source records behind it,
so the answer is auditable rather than merely plausible. **No model is trained** —
`gpt-oss-20b` is served off-the-shelf via LM Studio at temperature 0; the only thing
that changes is the in-prompt schema.

This file is the map of what is coded up, where the data lives, and how to run it.

## Where things live

| What | Path | Notes |
|---|---|---|
| Raw GLEIF Golden Copy CSVs | `data/*.csv` (+ `data/raw/`) | ~5.8 GB; git-ignored |
| Converted N-Triples | `data/gleif.nt` | ~12 GiB, 69.7M triples; git-ignored |
| **Triplestore (oxigraph/RocksDB)** | **`data/oxigraph/`** | **~9.4 GiB; the DB every script reads via `--store data/oxigraph`; git-ignored** |
| In-prompt schema (released artifact) | `schema/prompt_schema.md` | generated from the graph; committed |
| GLEIF ontology + code lists | `schema/ontology/`, `schema/codelists/` | committed |
| Vocabulary cache | `build/known_terms.json` | 71 terms the guard checks against |
| Evaluation sets | `eval/*.json`, `eval/question_spec.md` | committed (see below) |
| Run + score outputs | `results/*.json`, `results/*.log` | committed |
| Paper | `paper/paper.tex` → `make paper` | see `Makefile` |
| Blog + figures | `blog/` | `pipeline.tex`, `plot_subsidiary_map.py`, `style_guide.md` |

The store is deliberately kept in `data/` alongside the CSVs. It is large and
git-ignored; a full rebuild from the CSVs is: `gleif_to_rdf` → `load_oxigraph`.

## Data / build flow

```
data/*.csv  --gleif_to_rdf-->  data/gleif.nt  --load_oxigraph-->  data/oxigraph/
                                                                        |
                                    build_schema_prompt  -->  schema/prompt_schema.md
                                                                        |
  eval/questions*.json + schema/prompt_schema.md + data/oxigraph  --pipeline (LM Studio)-->  results/run*.json
                                                                        |
                                     results/run*.json + gold  --score-->  results/scored*.json
```

## Scripts (`src/`)

| Script | Purpose | Inputs | Outputs |
|---|---|---|---|
| `gleif_to_rdf.py` | Convert Golden Copy CSVs → N-Triples with GLEIF's own ontology. Materializes supertypes (no reasoner); mints `kgx:` subclasses for the fund relationships GLEIF's ontology omits; maps only the ~15 question-relevant L1 fields. | `data/*.csv`, `schema/ontology/` | `data/gleif.nt` |
| `load_oxigraph.py` | Bulk-load the `.nt` into a persistent disk-backed oxigraph store and `optimize()`. | `data/gleif.nt` | `data/oxigraph/` |
| `build_schema_prompt.py` | Generate the in-prompt schema **from what is actually in the graph** (every class/property/value is count-backed), joining ontology labels as docs. | `data/oxigraph`, `schema/ontology/` | `schema/prompt_schema.md` |
| `pipeline.py` | **The system under evaluation.** question → LM Studio (`gpt-oss-20b`, temp 0) → SPARQL → guard (parse + vocabulary-existence) → bounded repair (≤2, **never on an empty result**) → execute → answer + provenance. | `data/oxigraph`, `schema/prompt_schema.md`, a questions JSON (or `--ask`), LM Studio @ `localhost:1234` | `results/<run>.json` |
| `verify_gold.py` | **Ingest gate for gold.** Execute every gold reference query; check vs `expect_empty`. Run this on any new question set before trusting it. | `data/oxigraph`, an `eval/*.json` | stdout report (OK/BAD per item) |
| `score.py` | Score results vs gold: execution accuracy (RelaxedEM, set compare), QALD macro-F1 (empty-answer convention), TrustSQL-style reliability, validity, first/repaired/unresolved, per-tier, error taxonomy. | `data/oxigraph`, questions JSON, a run JSON | stdout + `results/scored*.json` |
| `validate_tiers.py` | **Design gate.** One hand-written reference query per tier, run before authoring questions, to confirm each tier is expressible and fast enough (esp. tier c reified transitive paths). | `data/oxigraph` | stdout |
| `trace.py` | Trace one question through the real pipeline stage-by-stage (reuses `pipeline.py`); Part B forces the guard→repair path. | `data/oxigraph`, LM Studio | stdout |
| `ablate_guard.py` | Reconstruct the guard/repair ablation offline from a logged run: model alone, +guard, +guard+repair. No model calls. | `data/oxigraph`, questions JSON, run JSON | stdout (`--latex` for the table) |
| `significance.py` | Paired **exact McNemar** over logged runs: guard+repair vs the raw model, and each model vs `gpt-oss-20b`. Caches gold and guard-bypassed executions under `build/`, so a re-run is cheap. No model calls. | `data/oxigraph`, questions JSON, run JSONs | stdout + `results/significance.json` |

## Evaluation sets (`eval/`)

Item schema: `{id, tier, family, skill, question, gold, [expect_empty]}`. `gold` is a
reference SPARQL query; the gold **answer** is that query's execution result. Tiers:
`a` lookup, `b` single-hop, `c` multi-hop, `d` attribute filter, `e` aggregate, and
`f1/f2/f3` unanswerable (recorded exception / out-of-scope / not-in-graph). The paper
reports `f1+f2+f3` collapsed as tier `f`.

- `question_spec.md` — neutral authoring spec (3 personas, families F1–F11, tier
  mapping, balance rule, authoring rules). **Types are fixed here before instantiation.**
- `questions.json` — **dev set (54)**. Used to refine the prompt/schema; drove three
  schema revisions (92.6% → 98.1%). Optimistic ceiling.
- `questions_hard.json` — **held-out set (90)**. Authored harder; the prompt was frozen;
  informed no revision. The generalization estimate (86.7%).
- `pilot.json` — 10-item smoke test.

The dev/held-out split is a train/test discipline where the "training" is hand-editing
the prompt while watching the dev set. There are no learned parameters.

## Running it

```bash
# 0. Python deps (create the venv outside any cloud-synced folder -- a venv is tens of thousands of files)
python -m venv ~/.venvs/finnlp2026
~/.venvs/finnlp2026/Scripts/python -m pip install -r requirements.txt   # pyoxigraph, rdflib, requests

# 1. (one-time) build the graph from the CSVs
python src/gleif_to_rdf.py --data-dir data --out data/gleif.nt
python src/load_oxigraph.py --nt data/gleif.nt --store data/oxigraph
python src/build_schema_prompt.py --store data/oxigraph --out schema/prompt_schema.md

# 2. serve the model: LM Studio, load openai/gpt-oss-20b, OpenAI-compatible server on :1234

# 3. verify gold, run, score
python src/verify_gold.py --store data/oxigraph --questions eval/questions_hard.json
python src/pipeline.py    --store data/oxigraph --questions eval/questions_hard.json --out results/run_hard.json --max-repairs 2
python src/score.py       --store data/oxigraph --questions eval/questions_hard.json --results results/run_hard.json --out results/scored_hard.json
```

Ad-hoc store queries don't need LM Studio, only `pyoxigraph` (currently installed in
`py -3.11` on this machine).
