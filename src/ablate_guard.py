"""Guard / repair ablation, reconstructed offline from a pipeline run.

A pipeline run at --max-repairs 2 records every attempt, so we can recover what
the system would have scored under weaker configurations WITHOUT re-querying the
model. The one thing the log cannot give is "no guard but WITH repair" (without
the guard there is no validation signal to trigger a repair on a query that
executes to the wrong rows), so that cell is omitted by construction.

Conditions, all on the same question set:
  * model alone      : execute the first attempt with the guard BYPASSED (raw
                       model output straight to the store). Isolates the model.
  * + guard, 0 repair: first attempt only; a guard rejection is left unresolved.
  * + guard, <=k rep : the run as executed, truncated to k repairs.

We report validity (a query that executes without error and, under the guard,
passes it) and execution accuracy (RelaxedEM set match, incl. the empty-answer
convention) for each, so "100% validity" is shown to be the guard's doing rather
than a property of the raw model.

Usage:
    python ablate_guard.py --store data/oxigraph --questions eval/questions_hard.json \
        results/run_hard.json=Held-out [more.json=Label ...] [--latex]
"""
from __future__ import annotations
import argparse, json, sys
from pyoxigraph import Store

import score
from pipeline import execute

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def correct(gold: set, pred) -> bool:
    """RelaxedEM with the empty-answer convention. `pred` is None if invalid."""
    return pred is not None and gold == pred


def raw_pred(store, sparql: str):
    """Execute a query with the guard bypassed. Returns the normalised row set,
    or None if it does not parse / execute (i.e. invalid)."""
    if not sparql.strip():
        return None
    try:
        rows, _cols, _t = execute(store, sparql)
    except Exception:
        return None
    return score.pred_rows({"rows": rows})


def attempt_pred(att: dict):
    """Row set of a recorded attempt, or None if it did not execute."""
    if att.get("status") != "ok":
        return None
    return score.pred_rows({"rows": att.get("rows", [])})


def resolved_within(rec: dict, k: int):
    """The final attempt if the run resolves within k repairs (k+1 attempts),
    else None. Mirrors the pipeline's stop-on-first-ok behaviour."""
    for att in rec.get("attempts", [])[: k + 1]:
        if att.get("status") == "ok":
            return att
    return None


def analyse(store, items: dict, run: list) -> dict:
    n = 0
    alone_valid = alone_ok = 0
    g0_valid = g0_ok = 0
    g1_valid = g1_ok = 0
    gfull_valid = gfull_ok = 0
    guard_caught_silent = 0   # attempt-0 executed to WRONG rows but guard rejected it
    max_rep = 0
    for rec in run:
        qid = rec.get("id")
        if qid not in items:
            continue
        n += 1
        gold = score.gold_rows(store, items[qid]["gold"])
        atts = rec.get("attempts", [])
        a0 = atts[0] if atts else {}
        max_rep = max(max_rep, len(atts) - 1)

        # model alone: first attempt, guard bypassed
        p_raw = raw_pred(store, a0.get("sparql", ""))
        alone_valid += p_raw is not None
        alone_ok += correct(gold, p_raw)

        # + guard, 0 repair: first attempt must pass guard AND execute
        p0 = attempt_pred(a0)
        g0_valid += p0 is not None
        g0_ok += correct(gold, p0)

        # the guard's unique save: a0 executed (guard-off) to non-gold rows, but
        # the guard rejected it so the system got a chance to repair
        if p_raw is not None and not correct(gold, p_raw) and a0.get("status") != "ok":
            guard_caught_silent += 1

        # + guard, <=1 repair
        a1 = resolved_within(rec, 1)
        p1 = attempt_pred(a1) if a1 else None
        g1_valid += a1 is not None
        g1_ok += correct(gold, p1)

        # + guard, full run (<=2)
        af = resolved_within(rec, len(atts))
        pf = attempt_pred(af) if af else None
        gfull_valid += af is not None
        gfull_ok += correct(gold, pf)

    return {"n": n, "max_rep": max_rep, "guard_caught_silent": guard_caught_silent,
            "rows": [
                ("Model alone (no guard, no repair)", alone_valid, alone_ok),
                ("+ guard, 0 repair",                 g0_valid,    g0_ok),
                ("+ guard, \u22641 repair",           g1_valid,    g1_ok),
                ("+ guard + repair (full system)",    gfull_valid, gfull_ok),
            ]}


def report(label, a):
    n = a["n"]
    print(f"\n=== {label}  (n={n}, max repairs used={a['max_rep']}) ===")
    print(f"  {'condition':36} {'validity':>12} {'exec acc':>12}")
    for name, valid, ok in a["rows"]:
        print(f"  {name:36} {valid:>4}/{n} {valid/n:>5.0%} {ok:>4}/{n} {ok/n:>5.0%}")
    print(f"  guard's unique saves (silent wrong -> repaired): {a['guard_caught_silent']}")


def latex(runs):
    print("\n% --- guard/repair ablation ---")
    print("\\begin{table}[t]\n\\centering\\small")
    print("\\begin{tabular}{l" + "cc" * len(runs) + "}")
    print("\\toprule")
    hdr = " & ".join(f"\\multicolumn{{2}}{{c}}{{\\textbf{{{l}}}}}" for l, _ in runs)
    print(f" & {hdr} \\\\")
    sub = " & ".join("Valid & Acc" for _ in runs)
    print(f"\\textbf{{Configuration}} & {sub} \\\\")
    print("\\midrule")
    rowlabels = [r[0] for r in runs[0][1]["rows"]]
    for i, name in enumerate(rowlabels):
        cells = []
        for _, a in runs:
            _, valid, ok = a["rows"][i]
            cells.append(f"{valid/a['n']:.0%} & {ok/a['n']:.0%}")
        print(f"{name} & " + " & ".join(cells) + " \\\\")
    print("\\bottomrule\n\\end{tabular}")
    print("\\caption{Guard and repair ablation, reconstructed from the logged run. "
          "Validity is the share of queries that execute (and, where the guard is "
          "active, pass it); accuracy is RelaxedEM. The guard plus a bounded repair "
          "loop is what lifts validity to 100\\%.}")
    print("\\label{tab:ablation}\n\\end{table}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--questions", required=True)
    ap.add_argument("runs", nargs="+", help="path or path=LABEL")
    ap.add_argument("--latex", action="store_true")
    a = ap.parse_args()
    store = Store.read_only(a.store)
    items = {q["id"]: q for q in json.load(open(a.questions, encoding="utf-8"))}
    runs = []
    for spec in a.runs:
        path, _, label = spec.partition("=")
        run = json.load(open(path, encoding="utf-8"))
        res = analyse(store, items, run)
        runs.append((label or path.rsplit("/", 1)[-1], res))
    for label, res in runs:
        report(label, res)
    if a.latex:
        latex(runs)


if __name__ == "__main__":
    main()
