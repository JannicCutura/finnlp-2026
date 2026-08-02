"""Trace one question through the real pipeline, printing every stage.

Reuses the actual functions from pipeline.py (guard, extract_sparql,
repair_message, execute, LMStudio) so the trace is faithful, not a re-description.

Part A runs the live loop on the question.
Part B forces the guard-refusal -> repair path on a deliberately broken query, so
the repair mechanism is visible even when the live model gets it right first try.

    python src/trace.py --store data/oxigraph
"""
from __future__ import annotations
import argparse, sys, textwrap
sys.path.insert(0, "src")
from pyoxigraph import Store
import pipeline as P

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QUESTION = "Which entity manages the fund whose legal name is 'VDDKO - MASTERFONDS'? Give the manager's legal name."

BROKEN = """PREFIX gleif-base: <https://www.gleif.org/ontology/Base/>
PREFIX kgx: <https://example.org/gleif-kg/ontology/>
SELECT ?name WHERE {
  ?f gleif-base:hasLegalName "VDDKO - MASTERFONDS" ; kgx:hasFundManager ?m .
  ?m gleif-base:hasLegalName ?name .
}"""


def rule(t):
    print("\n" + "=" * 74 + f"\n{t}\n" + "=" * 74)


def show_sparql(q, indent="    "):
    print(textwrap.indent(q.strip(), indent))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--schema", default="schema/prompt_schema.md")
    ap.add_argument("--max-repairs", type=int, default=2)
    ap.add_argument("--question", default=QUESTION)
    a = ap.parse_args()
    question = a.question

    store = Store(a.store)
    schema = open(a.schema, encoding="utf-8").read()
    print("loading known vocabulary (cached) ...")
    known = P.load_known_terms(store)
    llm = P.LMStudio()
    print(f"  {len(known)} known terms; schema {len(schema)} chars; repair budget {a.max_repairs}")

    # -------- Part A: live loop --------
    rule("PART A  --  live run through answer()'s loop")
    print("QUESTION:", QUESTION)
    messages = [{"role": "system", "content": P.SYSTEM_TEMPLATE.format(schema=schema)},
                {"role": "user", "content": QUESTION}]
    print(f"\n[system prompt = rules + schema, {len(messages[0]['content'])} chars — schema elided]")

    for i in range(a.max_repairs + 1):
        rule(f"attempt {i+1}   (messages in context: {len(messages)})")
        raw, usage, secs = llm.chat(messages)          # pipeline.LMStudio.chat -> content only
        q = P.extract_sparql(raw)                      # strip any ```fences```
        print(f"model returned in {secs:.1f}s ({usage.get('completion_tokens','?')} completion tokens)")
        print("  [gpt-oss 'reasoning' field is dropped; only 'content' is parsed]\n")
        show_sparql(q)

        g = P.guard(q, known)                          # the guard: syntax + vocabulary
        print(f"\nGUARD -> ok={g['ok']}  kind={g['kind']}  {g['message']}")

        if g["ok"]:
            rows, cols, ex = P.execute(store, q)
            print(f"EXECUTE -> {len(rows)} row(s) in {ex:.3f}s")
            for r in rows[:5]:
                print("   ", " | ".join(str(v) for v in r))
            print(f"\nRESULT: {'answered' if rows else 'empty (a legitimate answer)'} — stop, {i+1} attempt(s).")
            break

        if i == a.max_repairs:
            print("\nbudget exhausted -> UNRESOLVED")
            break
        msg = P.repair_message(g, known)               # targeted feedback + difflib suggestion
        print("\nREPAIR MESSAGE fed back to the model:")
        print(textwrap.indent(msg, "  | "))
        messages += [{"role": "assistant", "content": q}, {"role": "user", "content": msg}]

    # -------- Part B: forced guard-refusal, deterministic --------
    rule("PART B  --  the guard/repair mechanism on a deliberately broken query")
    print("A query using gleif-base:hasLegalName (wrong namespace; the real term is gleif-L1:):")
    show_sparql(BROKEN)
    g = P.guard(BROKEN, known)
    print(f"\nGUARD -> ok={g['ok']}  kind={g['kind']}  {g['message']}")
    print(f"  unknown terms: {g['unknown']}")
    print("\nrepair_message() builds this feedback (note the difflib suggestion):")
    print(textwrap.indent(P.repair_message(g, known), "  | "))


if __name__ == "__main__":
    main()
