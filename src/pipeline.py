"""NL question -> SPARQL -> guard -> (repair) -> execution -> grounded answer.

This is the system under evaluation. Every answer decomposes into: the question,
the query the model wrote, the rows it returned, and the graph nodes behind them
-- which is the auditability claim the paper rests on.

Repair loop
-----------
A bounded repair loop feeds validation failures back to the model, following
"Ontologies to the Rescue" (arXiv 2405.11706), which reports execution accuracy
rising 42.9% -> 72.6% with an ontology check + LLM repair. We report first-time,
with-repairs and unresolved separately, plus the retry budget.

CRITICAL: repair triggers ONLY on syntax errors, unknown vocabulary, or runtime
execution errors -- NEVER on an empty result set. On this graph an empty result
is very often the CORRECT answer (95.6% of entities have no parent, only a
reporting exception). Retrying until rows come back would destroy the abstention
measurement, which is the paper's core contribution.

Notes
-----
* gpt-oss models return chain-of-thought in a separate `reasoning` field. We read
  ONLY `content`; concatenating the two would corrupt every generated query.
* Temperature 0 for reproducibility.

Usage:
    python pipeline.py --store data/oxigraph --questions eval/pilot.json \
        --out results/pilot.json --max-repairs 2
"""

from __future__ import annotations
import argparse, difflib, json, os, re, sys, threading, time
import requests
from pyoxigraph import Store
from rdflib.plugins.sparql import prepareQuery

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_URL = "http://localhost:1234/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-20b"
ONTOLOGY_NS = ("https://www.gleif.org/ontology/", "https://example.org/gleif-kg/ontology/")

NS_SHORT = [
    ("gleif-L1", "https://www.gleif.org/ontology/L1/"),
    ("gleif-L2", "https://www.gleif.org/ontology/L2/"),
    ("gleif-base", "https://www.gleif.org/ontology/Base/"),
    ("gleif-elf", "https://www.gleif.org/ontology/EntityLegalForm/"),
    ("gleif-ra", "https://www.gleif.org/ontology/RegistrationAuthority/"),
    ("gleif-re", "https://www.gleif.org/ontology/ReportingException/"),
    ("kgx", "https://example.org/gleif-kg/ontology/"),
]

SYSTEM_TEMPLATE = """You translate natural-language questions into SPARQL queries over the GLEIF legal-entity knowledge graph.

Rules:
- Output ONLY the SPARQL query. No prose, no explanation, no markdown fences.
- Always declare the PREFIXes you use.
- Use only the classes and properties listed below. Do not invent terms.
- If the graph genuinely cannot answer the question, write a valid query that
  correctly returns no rows. An empty answer is a legitimate answer here.

{schema}"""


def short(iri: str) -> str:
    for p, ns in NS_SHORT:
        if iri.startswith(ns):
            return f"{p}:{iri[len(ns):]}"
    return iri


class LMStudio:
    def __init__(self, url=DEFAULT_URL, model=DEFAULT_MODEL, temperature=0.0, max_tokens=800,
                 timeout=300):
        self.url, self.model = url, model
        self.temperature, self.max_tokens = temperature, max_tokens
        self.timeout = timeout

    def chat(self, messages: list[dict]) -> tuple[str, dict, float]:
        t0 = time.time()
        last = None
        # A model swap in LM Studio briefly drops the server; a connection error fails fast,
        # so ride it out with several backoff retries. A read timeout, in contrast, already
        # cost `timeout` seconds, so retry it at most once rather than burning the whole
        # budget many times over.
        for attempt in range(8):
            try:
                r = requests.post(self.url, timeout=self.timeout, json={
                    "model": self.model, "temperature": self.temperature,
                    "max_tokens": self.max_tokens, "messages": messages,
                })
                r.raise_for_status()
                d = r.json()
                msg = d["choices"][0]["message"]
                # read ONLY content -- `reasoning` is chain-of-thought, not query text
                return (msg.get("content") or "").strip(), d.get("usage", {}), time.time() - t0
            except requests.ConnectionError as e:      # server down / model swap: wait it out
                last = e
                time.sleep(min(5 * (attempt + 1), 30))
            except requests.RequestException as e:      # read timeout / HTTP error: retry once
                last = e
                if attempt >= 1:
                    break
                time.sleep(3)
        raise last


FENCE = re.compile(r"```(?:sparql)?\s*(.*?)```", re.S | re.I)
PNAME = re.compile(r"\b([A-Za-z][\w-]*):([A-Za-z_][\w.-]*)")


def extract_sparql(text: str) -> str:
    m = FENCE.search(text)
    if m:
        text = m.group(1)
    return text.strip()


def load_known_terms(store, cache="build/known_terms.json") -> set[str]:
    """Every vocabulary IRI in the graph: predicates, classes, AND controlled
    VALUES used as objects (gleif-L1:ISSUED, gleif-re:NO_LEI, ...).

    Omitting object values was a real bug: the guard rejected valid queries that
    filtered on registration status, which looked like model failure.
    """
    if cache and os.path.exists(cache):
        return set(json.load(open(cache, encoding="utf-8")))

    terms, preds = set(), []
    for r in store.query("SELECT DISTINCT ?p WHERE { ?s ?p ?o }"):
        p = str(r[0]).strip("<>")
        preds.append(p); terms.add(p)
    for r in store.query("SELECT DISTINCT ?c WHERE { ?s a ?c }"):
        terms.add(str(r[0]).strip("<>"))
    # Per-predicate: an unrestricted DISTINCT-object scan over 69M triples never finishes.
    ns_filter = " || ".join(f'STRSTARTS(STR(?o), "{ns}")' for ns in ONTOLOGY_NS)
    for p in preds:
        for r in store.query(f"SELECT DISTINCT ?o WHERE {{ ?s <{p}> ?o . "
                             f"FILTER(isIRI(?o) && ({ns_filter})) }} LIMIT 300"):
            terms.add(str(r[0]).strip("<>"))
    if cache:
        os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
        json.dump(sorted(terms), open(cache, "w", encoding="utf-8"))
    return terms


def guard(query: str, known: set[str]) -> dict:
    """Pre-execution validation. Returns {ok, kind, message, unknown:[...]}."""
    try:
        prepareQuery(query)
    except Exception as e:
        return {"ok": False, "kind": "syntax",
                "message": f"{type(e).__name__}: {str(e)[:200]}", "unknown": []}

    prefixes = dict(re.findall(r"PREFIX\s+([\w-]*):\s*<([^>]+)>", query, re.I))
    body = re.sub(r"PREFIX\s+[\w-]*:\s*<[^>]+>", "", query, flags=re.I)
    unknown = []
    for pfx, local in PNAME.findall(body):
        if pfx not in prefixes:
            continue
        full = prefixes[pfx] + local
        if full.startswith("https://www.gleif.org/data/"):   # specific entities, not vocabulary
            continue
        if not any(full.startswith(ns) for ns in ONTOLOGY_NS):
            continue                                        # non-GLEIF vocab (rdfs, xsd, ...)
        if full not in known:
            unknown.append(f"{pfx}:{local}")
    unknown = sorted(set(unknown))
    if unknown:
        return {"ok": False, "kind": "vocabulary",
                "message": "unknown vocabulary: " + ", ".join(unknown[:6]), "unknown": unknown}
    return {"ok": True, "kind": "ok", "message": "ok", "unknown": []}


def suggest(term: str, known: set[str], n=3) -> list[str]:
    """Closest real vocabulary terms for a hallucinated one."""
    shorts = [short(k) for k in known if any(k.startswith(ns) for ns in ONTOLOGY_NS)]
    return difflib.get_close_matches(term, shorts, n=n, cutoff=0.55)


def repair_message(g: dict, known: set[str], exec_error: str | None = None) -> str:
    if exec_error:
        return ("Your query failed at execution.\n\n"
                f"Error: {exec_error}\n\n"
                "Return a corrected SPARQL query. Output ONLY the query.")
    if g["kind"] == "syntax":
        return ("Your query is not valid SPARQL.\n\n"
                f"Parser error: {g['message']}\n\n"
                "Return a corrected SPARQL query. Output ONLY the query.")
    lines = ["Your query references terms that do not exist in the graph.\n"]
    for t in g["unknown"][:6]:
        alts = suggest(t, known)
        lines.append(f"- `{t}` does not exist."
                     + (f" Did you mean: {', '.join('`'+a+'`' for a in alts)}?" if alts else ""))
    lines.append("\nUse only terms from the schema. Return a corrected SPARQL query. "
                 "Output ONLY the query.")
    return "\n".join(lines)


def execute(store, query: str, limit_rows=1000):
    # NB: must exceed the largest gold answer set (currently 34 rows) or correct
    # answers get truncated and scored as wrong.
    t0 = time.time()
    res = store.query(query)
    cols = []
    try:
        cols = [str(v)[1:] for v in res.variables]
    except Exception:
        pass
    rows = []
    for i, sol in enumerate(res):
        if i >= limit_rows:
            break
        vals = []
        for term in sol:
            if term is None:
                vals.append(None); continue
            s = str(term)
            if s.startswith("<") and s.endswith(">"):
                vals.append(s[1:-1])
            else:
                s = s.split('"^^')[0]
                vals.append(s[1:].rsplit('"', 1)[0] if s.startswith('"') else s)
        rows.append(vals)
    return rows, cols, time.time() - t0


def execute_timeout(store, query: str, timeout_s: int):
    """execute() with a hard wall-clock cap. pyoxigraph exposes no query timeout, so we
    run it in a daemon thread; a capable model can emit a valid but runaway query (an
    unanchored path, a cartesian product) that would otherwise hang the whole run. A
    timed-out query is surfaced as an ordinary execution error and repaired like any other."""
    box = {}
    def work():
        try:
            box["v"] = execute(store, query)
        except Exception as e:          # noqa: BLE001 -- re-raised to the caller
            box["e"] = e
    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise TimeoutError(f"execution exceeded {timeout_s}s")
    if "e" in box:
        raise box["e"]
    return box["v"]


def answer(store, llm, schema, question, known, max_repairs=2, exec_timeout=120) -> dict:
    """Generate, validate, and repair up to `max_repairs` times.

    Repair fires on syntax / vocabulary / execution errors ONLY. An empty result
    set is a valid answer and is never retried -- see module docstring.
    """
    messages = [{"role": "system", "content": SYSTEM_TEMPLATE.format(schema=schema)},
                {"role": "user", "content": question}]
    attempts, total_gen = [], 0.0

    for attempt_i in range(max_repairs + 1):
        raw, usage, gen_s = llm.chat(messages)
        total_gen += gen_s
        q = extract_sparql(raw)
        g = guard(q, known)
        att = {"attempt": attempt_i + 1, "sparql": q, "guard": g["message"],
               "guard_kind": g["kind"], "gen_seconds": round(gen_s, 2), "usage": usage}

        if g["ok"]:
            try:
                rows, cols, ex_s = execute_timeout(store, q, exec_timeout)
                att.update({"status": "ok", "columns": cols, "rows": rows,
                            "row_count": len(rows), "exec_seconds": round(ex_s, 3)})
                attempts.append(att)
                break                                   # success (empty rows included)
            except Exception as e:
                err = f"{type(e).__name__}: {str(e)[:200]}"
                att.update({"status": "exec_error", "error": err, "rows": [], "row_count": 0})
                attempts.append(att)
                if attempt_i == max_repairs:
                    break
                messages += [{"role": "assistant", "content": q},
                             {"role": "user", "content": repair_message(g, known, exec_error=err)}]
                continue

        att.update({"status": "guard_failed", "rows": [], "row_count": 0})
        attempts.append(att)
        if attempt_i == max_repairs:
            break
        messages += [{"role": "assistant", "content": q},
                     {"role": "user", "content": repair_message(g, known)}]

    final = attempts[-1]
    return {
        "question": question,
        "attempts": attempts,
        "n_attempts": len(attempts),
        "first_time_ok": attempts[0]["status"] == "ok",
        "repaired": final["status"] == "ok" and len(attempts) > 1,
        "unresolved": final["status"] != "ok",
        "status": final["status"],
        "sparql": final["sparql"],
        "guard": final["guard"],
        "rows": final.get("rows", []),
        "row_count": final.get("row_count", 0),
        "columns": final.get("columns", []),
        "total_gen_seconds": round(total_gen, 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--schema", default="schema/prompt_schema.md")
    ap.add_argument("--questions")
    ap.add_argument("--ask")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out")
    ap.add_argument("--max-repairs", type=int, default=2)
    ap.add_argument("--no-schema", action="store_true", help="ablation: omit schema from prompt")
    ap.add_argument("--timeout", type=int, default=300, help="per-request timeout in seconds")
    ap.add_argument("--exec-timeout", type=int, default=120,
                    help="per-query execution timeout in seconds (a runaway query is scored as an error)")
    ap.add_argument("--resume", action="store_true",
                    help="skip questions already present in --out (continue an interrupted run)")
    a = ap.parse_args()

    store = Store.read_only(a.store)   # read-only: never mutate the store, and allow concurrent readers
    schema = "" if a.no_schema else open(a.schema, encoding="utf-8").read()
    print("loading known vocabulary ...", flush=True)
    known = load_known_terms(store)
    print(f"  {len(known)} known terms;  repair budget = {a.max_repairs}\n", flush=True)
    llm = LMStudio(model=a.model, timeout=a.timeout)

    questions = ([{"id": "adhoc", "tier": "-", "question": a.ask}] if a.ask
                 else json.load(open(a.questions, encoding="utf-8")))

    def flush():                                      # write after every question: never lose progress
        if a.out:
            os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
            json.dump(out, open(a.out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

    out, done = [], set()
    if a.resume and a.out and os.path.exists(a.out):
        out = json.load(open(a.out, encoding="utf-8"))
        done = {r.get("id") for r in out}
        print(f"resume: {len(done)} question(s) already done, skipping them\n", flush=True)
    for item in questions:
        if item.get("id") in done:
            continue
        try:
            rec = answer(store, llm, schema, item["question"], known, a.max_repairs, a.exec_timeout)
        except Exception as e:        # a hung/failed request must not kill a multi-hour run
            rec = {"question": item["question"], "attempts": [], "n_attempts": 0,
                   "first_time_ok": False, "repaired": False, "unresolved": True,
                   "status": "request_error", "sparql": "", "guard": str(e)[:200],
                   "rows": [], "row_count": 0, "columns": [], "total_gen_seconds": 0.0}
            print(f"  !! {item.get('id')} request failed: {str(e)[:120]}")
        rec["id"], rec["tier"] = item.get("id"), item.get("tier")
        out.append(rec)
        flush()
        mark = ("first-try" if rec["first_time_ok"] else
                f"REPAIRED after {rec['n_attempts']} attempts" if rec["repaired"] else "UNRESOLVED")
        print(f"[{rec['tier']}] {rec['id']}  {item['question'][:80]}")
        print(f"   {mark}  status={rec['status']}  rows={rec['row_count']}  "
              f"gen={rec['total_gen_seconds']}s")
        if not rec["first_time_ok"]:
            for att in rec["attempts"][:-1]:
                print(f"     attempt {att['attempt']} failed: {att['guard'][:110]}")
        for row in rec.get("rows", [])[:3]:
            print("   ->", " | ".join(str(v)[:60] if v is not None else "-" for v in row))
        print()

    n = len(out)
    ft = sum(r["first_time_ok"] for r in out)
    rp = sum(r["repaired"] for r in out)
    un = sum(r["unresolved"] for r in out)
    print("=" * 60)
    print(f"first-time ok : {ft}/{n} ({ft/n:.0%})")
    print(f"after repair  : {ft+rp}/{n} ({(ft+rp)/n:.0%})   (+{rp} repaired)")
    print(f"unresolved    : {un}/{n} ({un/n:.0%})")

    if a.out:
        flush()
        print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
