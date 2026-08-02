"""Score pipeline output against the gold reference queries.

Metrics (see agent-files/initial-proposal.md section 7):

* **Execution accuracy (RelaxedEM)** -- primary. Following FIRESPARQL: variable
  names are stripped, rows are deduplicated, and the result is compared as a SET.
  "Did it run" is not accuracy; a valid query can encode the wrong intent.

* **QALD macro-F1** with the empty-answer convention: empty gold + empty
  prediction scores 1; a non-empty prediction against empty gold scores 0; an
  empty prediction against non-empty gold scores 0. This is precisely how correct
  abstention is credited and how a wrong-query-empty is punished.

* **Reliability score** (TrustSQL-style) for the abstention tiers: reward correct
  answers and correct abstentions, penalise confident wrong answers. Guards
  against a model that abstains indiscriminately -- important here, because 95.6%
  of entities genuinely have no parent.

* first-time / with-repairs / unresolved, validity rate, and per-tier breakdown.

Usage:
    python score.py --store data/oxigraph --questions eval/questions.json \
        --results results/run.json --out results/scored.json
"""

from __future__ import annotations
import argparse, collections, json, math, sys
from pyoxigraph import Store
from verify_gold import PREFIXES, fmt

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion k/n (default 95%)."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def gold_rows(store, gold, limit=5000) -> set[tuple]:
    out = set()
    for i, sol in enumerate(store.query(PREFIXES + gold)):
        if i >= limit:
            break
        out.add(tuple(fmt(t) for t in sol))
    return out


def pred_rows(rec) -> set[tuple]:
    out = set()
    for row in rec.get("rows", []):
        out.add(tuple("-" if v is None else str(v).rsplit("/", 1)[-1]
                      if str(v).startswith("http") else str(v) for v in row))
    return out


def prf(gold: set, pred: set) -> tuple[float, float, float]:
    """QALD convention, including the empty-answer rule."""
    if not gold and not pred:
        return 1.0, 1.0, 1.0
    if not gold or not pred:
        return 0.0, 0.0, 0.0
    tp = len(gold & pred)
    p = tp / len(pred)
    r = tp / len(gold)
    f = 0.0 if p + r == 0 else 2 * p * r / (p + r)
    return p, r, f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--questions", default="eval/questions.json")
    ap.add_argument("--results", required=True)
    ap.add_argument("--out")
    ap.add_argument("--penalty", type=float, default=1.0,
                    help="reliability penalty for a confident wrong answer")
    a = ap.parse_args()

    store = Store.read_only(a.store)   # read-only: allow concurrent readers alongside a running pipeline
    items = {q["id"]: q for q in json.load(open(a.questions, encoding="utf-8"))}
    results = json.load(open(a.results, encoding="utf-8"))

    scored, tiers = [], collections.defaultdict(list)
    err_kinds = collections.Counter()
    n_first, n_repaired, n_unresolved, n_valid = 0, 0, 0, 0
    rel_total = 0.0
    n_reward, n_penalized = 0, 0     # for the penalty-independent reliability sweep

    for rec in results:
        qid = rec.get("id")
        if qid not in items:
            continue
        item = items[qid]
        g = gold_rows(store, item["gold"])
        p = pred_rows(rec) if rec.get("status") == "ok" else set()
        executed = rec.get("status") == "ok"

        exact = (g == p) and executed
        prec, recl, f1 = prf(g, p) if executed else (0.0, 0.0, 0.0)

        # reliability: abstention = empty prediction
        abstained = executed and len(p) == 0
        gold_empty = len(g) == 0
        if gold_empty:
            rel = 1.0 if abstained else -a.penalty
        else:
            rel = 1.0 if exact else (0.0 if abstained else -a.penalty)
        rel_total += rel
        # penalty-independent buckets: reward (+1), zero (wrong abstention), or
        # penalised (a confident wrong answer, scored -penalty)
        if (gold_empty and abstained) or (not gold_empty and exact):
            n_reward += 1
        elif not (not gold_empty and abstained):
            n_penalized += 1

        n_valid += executed
        n_first += bool(rec.get("first_time_ok"))
        n_repaired += bool(rec.get("repaired"))
        n_unresolved += bool(rec.get("unresolved"))
        if not rec.get("first_time_ok"):
            for att in rec.get("attempts", []):
                if att.get("guard_kind") not in (None, "ok"):
                    err_kinds[att["guard_kind"]] += 1
                elif att.get("status") == "exec_error":
                    err_kinds["exec_error"] += 1
        if executed and not exact:
            err_kinds["wrong_result"] += 1

        s = {"id": qid, "tier": item["tier"], "family": item.get("family"),
             "question": item["question"], "exact": exact, "f1": round(f1, 3),
             "precision": round(prec, 3), "recall": round(recl, 3),
             "gold_n": len(g), "pred_n": len(p), "executed": executed,
             "abstained": abstained, "gold_empty": gold_empty,
             "reliability": rel, "status": rec.get("status"),
             "n_attempts": rec.get("n_attempts"), "sparql": rec.get("sparql")}
        scored.append(s)
        tiers[item["tier"]].append(s)

    n = len(scored)
    if n == 0:
        print("no scored items"); return
    n_exact = sum(s["exact"] for s in scored)
    acc = n_exact / n
    macro_f1 = sum(s["f1"] for s in scored) / n
    ci_lo, ci_hi = wilson(n_exact, n)
    sweep = {pen: (n_reward - pen * n_penalized) / n for pen in (0.5, 1.0, 2.0, 5.0)}

    print(f"items scored: {n}\n")
    print(f"  execution accuracy (RelaxedEM) : {acc:.1%}  ({n_exact}/{n})  "
          f"95% CI [{ci_lo:.1%}, {ci_hi:.1%}]")
    print(f"  QALD macro-F1                  : {macro_f1:.3f}")
    print(f"  validity rate (executed)       : {n_valid/n:.1%}")
    print(f"  reliability (reward={n_reward}, penalised={n_penalized}, zero={n-n_reward-n_penalized}):")
    for pen, val in sweep.items():
        print(f"      penalty c={pen:<3} -> {val:+.3f}")
    print(f"  first-time ok / repaired / unresolved : {n_first} / {n_repaired} / {n_unresolved}")

    print("\n--- per tier ---")
    print(f"  {'tier':5} {'n':>3} {'exact':>7} {'macroF1':>8}")
    for t in sorted(tiers):
        ss = tiers[t]
        e = sum(x["exact"] for x in ss) / len(ss)
        f = sum(x["f1"] for x in ss) / len(ss)
        print(f"  {t:5} {len(ss):>3} {e:>6.0%} {f:>8.3f}")

    if err_kinds:
        print("\n--- error taxonomy ---")
        for k, v in err_kinds.most_common():
            print(f"  {k:16} {v}")

    wrong = [s for s in scored if not s["exact"]]
    if wrong:
        print(f"\n--- {len(wrong)} incorrect ---")
        for s in wrong:
            why = ("did not execute" if not s["executed"] else
                   "empty but gold non-empty" if s["pred_n"] == 0 and s["gold_n"] else
                   "answered but gold empty" if s["gold_empty"] else
                   f"set mismatch (gold {s['gold_n']} vs pred {s['pred_n']}, F1={s['f1']})")
            print(f"  [{s['tier']:3}] {s['id']:5} {why}")

    if a.out:
        json.dump({"summary": {"n": n, "execution_accuracy": acc,
                               "accuracy_ci95": [ci_lo, ci_hi], "macro_f1": macro_f1,
                               "validity_rate": n_valid / n, "reliability": rel_total / n,
                               "reliability_sweep": sweep,
                               "reward": n_reward, "penalized": n_penalized,
                               "first_time": n_first, "repaired": n_repaired,
                               "unresolved": n_unresolved},
                   "items": scored},
                  open(a.out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
