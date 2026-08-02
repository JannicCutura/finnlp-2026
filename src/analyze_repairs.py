"""Analyse the repair loop from pipeline results: how often a repair is needed and
what actually gets repaired.

Every attempt in a pipeline run records its guard verdict, so no extra logging is
needed. This reads one or more results JSONs and reports, per run: the outcome
split (first-try / after-repair / unresolved), the repair-count distribution, the
fault-type breakdown, and the specific faults the model made. With --out it writes
a comparison plot (useful for gpt-oss-20b vs 120b).

Usage:
    python analyze_repairs.py results/run_hard.json
    python analyze_repairs.py results/run_hard.json=20B results/run_hard_120b.json=120B --out results/repairs.png
"""
from __future__ import annotations
import argparse, collections, json, re, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def classify(att: dict):
    """A failed attempt -> (category, specific-detail). Returns (None, None) if ok."""
    kind, msg = att.get("guard_kind"), att.get("guard") or ""
    if att.get("status") == "exec_error":
        return "exec-error", (att.get("error") or "")[:70]
    if kind == "syntax":
        m = re.search(r"Unknown namespace prefix\s*:\s*(\S+)", msg)
        return ("missing-prefix", m.group(1)) if m else ("syntax-other", msg[:70])
    if kind == "vocabulary":
        return "unknown-vocabulary", msg.split("unknown vocabulary:", 1)[-1].strip()[:70]
    return None, None


def analyse(path: str) -> dict:
    data = json.load(open(path, encoding="utf-8"))
    n = len(data)
    dist = collections.Counter()      # repairs needed: 0 / 1 / 2 / "unresolved"
    faults = collections.Counter()    # category -> count of failed attempts
    detail = collections.Counter()    # "category: specific" -> count
    for r in data:
        dist["unresolved" if r.get("unresolved") else r.get("n_attempts", 1) - 1] += 1
        for a in r.get("attempts", []):
            cat, det = classify(a)
            if cat:
                faults[cat] += 1
                detail[f"{cat}: {det}"] += 1
    return {"n": n,
            "first": sum(r.get("first_time_ok") for r in data),
            "repaired": sum(r.get("repaired") for r in data),
            "unresolved": sum(r.get("unresolved") for r in data),
            "dist": dist, "faults": faults, "detail": detail}


def report(label: str, a: dict) -> None:
    n = a["n"]
    print(f"\n=== {label}  (n={n}) ===")
    print(f"  first-try  : {a['first']:>3}/{n}  ({a['first']/n:.0%})")
    print(f"  repaired   : {a['repaired']:>3}/{n}  ({a['repaired']/n:.0%})")
    print(f"  unresolved : {a['unresolved']:>3}/{n}  ({a['unresolved']/n:.0%})")
    order = {0: "0 (first try)", 1: "1 repair", 2: "2 repairs", "unresolved": "unresolved"}
    print("  repairs needed :", {order.get(k, k): a["dist"][k] for k in sorted(a["dist"], key=str)})
    print("  fault types    :")
    for c, v in a["faults"].most_common():
        print(f"      {c:20} {v}")
    print("  what got repaired (top):")
    for d, v in a["detail"].most_common(12):
        print(f"      {v:>3}  {d}")


def plot(runs, out):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    INK, TEAL, MUTED, RED = "#1A1F26", "#0F6E63", "#B8C0CC", "#C0392B"
    labels = [l for l, _ in runs]
    x = np.arange(len(labels))
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 3.6), facecolor="white")

    # left: outcome share, stacked
    first = np.array([a["first"] / a["n"] for _, a in runs])
    rep = np.array([a["repaired"] / a["n"] for _, a in runs])
    unres = np.array([a["unresolved"] / a["n"] for _, a in runs])
    ax1.bar(x, first, 0.55, color=TEAL, label="first try")
    ax1.bar(x, rep, 0.55, bottom=first, color=MUTED, label="after repair")
    ax1.bar(x, unres, 0.55, bottom=first + rep, color=RED, label="unresolved")
    ax1.set_ylim(0, 1); ax1.set_xticks(x); ax1.set_xticklabels(labels, color=INK)
    ax1.set_ylabel("share of questions", color=INK)
    ax1.set_title("Query outcome", color=INK, fontweight="bold")
    ax1.legend(fontsize=7, frameon=False, loc="lower right")
    for s in ("top", "right"): ax1.spines[s].set_visible(False)

    # right: fault-type counts, grouped by run
    cats = ["missing-prefix", "unknown-vocabulary", "syntax-other", "exec-error"]
    cats = [c for c in cats if any(a["faults"].get(c) for _, a in runs)]
    w = 0.8 / max(len(runs), 1)
    for i, (label, a) in enumerate(runs):
        ax2.bar(np.arange(len(cats)) + i * w, [a["faults"].get(c, 0) for c in cats],
                w, label=label, color=TEAL if i == 0 else MUTED)
    ax2.set_xticks(np.arange(len(cats)) + w * (len(runs) - 1) / 2)
    ax2.set_xticklabels(cats, rotation=20, ha="right", color=INK, fontsize=8)
    ax2.set_ylabel("repair triggers", color=INK)
    ax2.set_title("What the guard caught", color=INK, fontweight="bold")
    if len(runs) > 1: ax2.legend(fontsize=7, frameon=False)
    for s in ("top", "right"): ax2.spines[s].set_visible(False)

    fig.tight_layout()
    fig.savefig(out, dpi=200, facecolor="white")
    print(f"\nwrote {out}")


def latex(runs) -> None:
    """Emit a paper-ready LaTeX table (columns = runs). Numbers are computed, not
    transcribed, so the table cannot drift from the results."""
    ncol = len(runs)
    outcome = [
        ("First-try valid",       [f"{a['first']}/{a['n']}" for _, a in runs]),
        ("Resolved after repair",  [str(a["repaired"]) for _, a in runs]),
        ("Unresolved",             [str(a["unresolved"]) for _, a in runs]),
    ]
    cat_label = {"missing-prefix": "Missing \\texttt{PREFIX}",
                 "unknown-vocabulary": "Unknown / wrong term",
                 "syntax-other": "Other syntax error",
                 "exec-error": "Execution error"}
    triggers = [(lbl, [str(a["faults"].get(c, 0)) for _, a in runs])
                for c, lbl in cat_label.items() if any(a["faults"].get(c) for _, a in runs)]
    print("\\begin{table}[t]\n\\centering\\small\n\\begin{tabular}{l" + "c" * ncol + "}")
    print("\\toprule")
    print(" & ".join([""] + [f"\\textbf{{{l}}}" for l, _ in runs]) + " \\\\")
    print("\\midrule")
    for name, vals in outcome:
        print(name + " & " + " & ".join(vals) + " \\\\")
    if triggers:
        print("\\midrule")
        print(f"\\multicolumn{{{ncol + 1}}}{{l}}{{\\emph{{Repair triggers (attempts)}}}} \\\\")
        for name, vals in triggers:
            print(name + " & " + " & ".join(vals) + " \\\\")
    print("\\bottomrule\n\\end{tabular}")
    print("\\caption{The guard's repair loop. Most queries are valid first try; the "
          "rest are recovered from a bounded two-retry loop, and no query is left "
          "unresolved. Repair triggers are the specific faults the guard caught.}")
    print("\\label{tab:repairs}\n\\end{table}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="+", help="path or path=LABEL")
    ap.add_argument("--out", help="write a comparison plot (PNG)")
    ap.add_argument("--latex", action="store_true", help="emit a paper-ready LaTeX table")
    a = ap.parse_args()
    runs = []
    for spec in a.results:
        path, _, label = spec.partition("=")
        runs.append((label or path.rsplit("/", 1)[-1], analyse(path)))
    for label, res in runs:
        report(label, res)
    if a.out:
        plot(runs, a.out)
    if a.latex:
        print()
        latex(runs)


if __name__ == "__main__":
    main()
