"""Paired significance tests for the held-out results.

Two questions the reviewers raised that the logged runs can answer offline, with
no model calls -- the store is needed only to execute the reference queries.

  1. Is the guard+repair gain (80.0 -> 86.7) more than sample noise?
     Both conditions are scored on the SAME 90 questions from the SAME run (the
     no-guard condition is attempt 1 re-executed with the guard bypassed), so the
     comparison is paired: exact McNemar, i.e. a two-sided binomial sign test on
     the discordant pairs.

  2. Do the model-to-model differences in Table 3 survive the same test?
     Same 90 questions, different runs -> paired again.

A raw attempt-1 query can be valid yet computationally runaway (the guard is what
normally stops it reaching the store), so the guard-bypassed execution runs under
the same wall-clock cap the pipeline uses; a timeout counts as invalid, which is
how the pipeline would have treated it.

Gold row sets are cached to `build/gold_cache_<questions>.json` -- the first run
pays for the full-population aggregates, later runs do not.

Usage:
    py -3.11 src/significance.py --store data/oxigraph \
        --questions eval/questions_hard.json --base results/run_hard.json \
        --others results/run_hard_qwen32b.json=Qwen2.5-Coder-32B \
                 results/run_hard_llama70b.json=Llama-3.3-70B \
                 results/run_hard_gpt5.json=gpt-5 \
                 results/run_120b.json=gpt-oss-120b
"""
from __future__ import annotations
import argparse, json, os, sys, time
from math import comb
from pyoxigraph import Store

import score
from pipeline import execute_timeout
from ablate_guard import attempt_pred, resolved_within, correct

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EXEC_TIMEOUT = 120


# ---------------------------------------------------------------- statistics
def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar: a binomial sign test on the discordant pairs.
    b = A right / B wrong, c = A wrong / B right."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (centre - half, centre + half)


def newcombe_paired(b: int, c: int, n: int, z: float = 1.96):
    """Wald CI for a paired difference of proportions. The difference is
    (b - c)/n and its variance depends only on the discordant counts."""
    if n == 0:
        return (0.0, 0.0)
    d = (b - c) / n
    var = (b + c - (b - c) ** 2 / n) / (n * n)
    se = max(var, 0.0) ** 0.5
    return (d - z * se, d + z * se)


# ---------------------------------------------------------------- gold cache
def gold_cache(store, items, questions_path: str):
    """Row set per question id, cached on disk keyed by the questions file."""
    tag = os.path.splitext(os.path.basename(questions_path))[0]
    path = os.path.join("build", f"gold_cache_{tag}.json")
    cache = {}
    if os.path.exists(path):
        cache = json.load(open(path, encoding="utf-8"))
    missing = [q for q in items if q not in cache]
    for i, qid in enumerate(missing, 1):
        t0 = time.time()
        rows = score.gold_rows(store, items[qid]["gold"])
        cache[qid] = sorted(list(r) for r in rows)
        print(f"  gold {qid} ({i}/{len(missing)})  {time.time()-t0:.1f}s", flush=True)
    if missing:
        os.makedirs("build", exist_ok=True)
        json.dump(cache, open(path, "w", encoding="utf-8"))
    return {k: {tuple(r) for r in v} for k, v in cache.items()}


def raw_pred(store, sparql: str):
    """Execute with the guard bypassed, under the pipeline's wall-clock cap.
    Returns the normalised row set, or None if it does not parse, does not
    execute, or runs past the cap."""
    if not sparql.strip():
        return None
    try:
        rows, _cols, _t = execute_timeout(store, sparql, EXEC_TIMEOUT)
    except Exception:
        return None
    return score.pred_rows({"rows": rows})


class RawCache:
    """Guard-bypassed executions, cached on disk and flushed after every item.

    These are the expensive half of the analysis -- a raw attempt-1 query has not
    been through the guard, so some of them run to the wall-clock cap -- and they
    are deterministic against a pinned snapshot, so there is no reason to pay for
    them twice."""

    def __init__(self, run_path: str):
        tag = os.path.splitext(os.path.basename(run_path))[0]
        self.path = os.path.join("build", f"raw_cache_{tag}.json")
        self.data = {}
        if os.path.exists(self.path):
            self.data = json.load(open(self.path, encoding="utf-8"))

    def get(self, store, qid: str, sparql: str):
        if qid not in self.data:
            t0 = time.time()
            pred = raw_pred(store, sparql)
            self.data[qid] = None if pred is None else sorted(list(r) for r in pred)
            os.makedirs("build", exist_ok=True)
            json.dump(self.data, open(self.path, "w", encoding="utf-8"))
            print(f"  raw {qid}  {time.time()-t0:5.1f}s  "
                  f"{'invalid' if pred is None else str(len(pred)) + ' rows'}", flush=True)
        v = self.data[qid]
        return None if v is None else {tuple(r) for r in v}


# ---------------------------------------------------------------- outcomes
def outcomes_guard(store, gold, run, cache):
    """Per-item correctness under (a) the raw model and (b) the full system."""
    alone, full = {}, {}
    for rec in run:
        qid = rec.get("id")
        if qid not in gold:
            continue
        atts = rec.get("attempts", [])
        a0 = atts[0] if atts else {}
        alone[qid] = correct(gold[qid], cache.get(store, qid, a0.get("sparql", "")))
        af = resolved_within(rec, len(atts))
        full[qid] = correct(gold[qid], attempt_pred(af)) if af else False
    return alone, full


def outcomes_run(gold, run):
    """Per-item correctness of a run as executed (i.e. the full system).

    Every question in the set gets an entry. A question the run never resolved --
    the 120B item that induced a generation loop, for instance -- is counted
    wrong rather than dropped, which is how the paper's tables treat it and what
    keeps the comparisons paired over the same 90 items."""
    out = {qid: False for qid in gold}
    for rec in run:
        qid = rec.get("id")
        if qid not in gold:
            continue
        atts = rec.get("attempts", [])
        af = resolved_within(rec, len(atts))
        out[qid] = correct(gold[qid], attempt_pred(af)) if af else False
    return out


def compare(name_a, a, name_b, b):
    ids = sorted(set(a) & set(b))
    n = len(ids)
    ka, kb = sum(a[i] for i in ids), sum(b[i] for i in ids)
    disc_b = sum(1 for i in ids if a[i] and not b[i])
    disc_c = sum(1 for i in ids if b[i] and not a[i])
    p = mcnemar_exact(disc_b, disc_c)
    lo, hi = newcombe_paired(disc_b, disc_c, n)
    print(f"\n{name_a}  vs  {name_b}   (n={n}, paired)")
    print(f"  {name_a:30} {ka}/{n} = {ka/n:6.1%}")
    print(f"  {name_b:30} {kb}/{n} = {kb/n:6.1%}")
    print(f"  difference                     {(ka-kb)/n*100:+.1f} pp"
          f"   95% CI [{lo*100:+.1f}, {hi*100:+.1f}]")
    print(f"  discordant                     b={disc_b} (only {name_a}), "
          f"c={disc_c} (only {name_b})")
    print(f"  exact McNemar                  p = {p:.4f}")
    return {"a": name_a, "b": name_b, "n": n, "k_a": ka, "k_b": kb,
            "disc_b": disc_b, "disc_c": disc_c, "p": p, "ci": [lo, hi]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--questions", default="eval/questions_hard.json")
    ap.add_argument("--base", default="results/run_hard.json")
    ap.add_argument("--others", nargs="*", default=[], help="path=LABEL")
    ap.add_argument("--out", default="results/significance.json")
    a = ap.parse_args()

    store = Store.read_only(a.store)
    items = {q["id"]: q for q in json.load(open(a.questions, encoding="utf-8"))}
    base = json.load(open(a.base, encoding="utf-8"))

    print("resolving gold answers ...", flush=True)
    gold = gold_cache(store, items, a.questions)
    out = []

    # The model comparisons need no store access at all -- every run logs the rows
    # its final query returned -- so they are reported first and cost nothing.
    full = outcomes_run(gold, base)
    if a.others:
        print("\n" + "=" * 68)
        print("1. Each model against gpt-oss-20b")
        print("=" * 68, flush=True)
        for spec in a.others:
            path, _, label = spec.partition("=")
            run = json.load(open(path, encoding="utf-8"))
            out.append(compare(label or path, outcomes_run(gold, run),
                               "gpt-oss-20b", full))

    print("\n" + "=" * 68)
    print("2. Guard + repair vs the raw model (same run, same questions)")
    print("=" * 68, flush=True)
    alone, _full = outcomes_guard(store, gold, base, RawCache(a.base))
    out.insert(0, compare("full system", full, "model alone", alone))

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(out, open(a.out, "w", encoding="utf-8"), indent=1)
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
