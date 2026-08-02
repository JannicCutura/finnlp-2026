"""Benchmark a closed OpenAI model on the held-out set via the Batch API (50% cheaper).

This is a NON-LOCAL REFERENCE only. Sending the schema and question to a hosted API breaks
the paper's locality constraint, so this bounds the gap to a frontier model rather than
offering a deployable baseline. The benchmark data (GLEIF, CC0) and our own questions are
public, so the API calls raise no confidentiality issue; deployment on confidential
supervisory data would.

The repair loop is preserved at the BATCH level: round 0 submits every question; each later
round submits only the queries the guard or execution rejected, with the error fed back, up
to --max-repairs. An empty result is never retried (it is a legitimate answer here). The
output schema matches pipeline.py, so score.py and ablate_guard.py consume it unchanged.

The API key is read from .env (OPENAI_API_KEY); .env is git-ignored.

Usage:
    py -3.11 src/run_openai_batch.py --store data/oxigraph \
        --questions eval/questions_hard.json --model gpt-4o \
        --out results/run_hard_gpt4o.json
    # add --dry-run to write the round-0 request file and stop (no spend)
"""
from __future__ import annotations
import argparse, json, os, sys, threading, time
from pyoxigraph import Store
from dotenv import load_dotenv

# reuse the exact system prompt, guard, repair messages and executor of the local pipeline
from pipeline import (SYSTEM_TEMPLATE, extract_sparql, load_known_terms, guard,
                      repair_message, execute)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def execute_timeout(store, query, timeout_s):
    """Execute a query but abandon it after timeout_s. pyoxigraph exposes no query
    timeout, so we run it in a daemon thread; a strong model can write a runaway query
    (unanchored path, cartesian product) that must not hang the whole run. A timed-out
    query is reported as an execution error, exactly like any other runtime failure."""
    box = {}
    def work():
        try:
            box["v"] = execute(store, query)
        except Exception as e:          # noqa: BLE001 -- surfaced to the caller
            box["e"] = e
    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise TimeoutError(f"execution exceeded {timeout_s}s")
    if "e" in box:
        raise box["e"]
    return box["v"]


def is_reasoning(model: str) -> bool:
    """Reasoning models take max_completion_tokens and reject a custom temperature."""
    m = model.lower()
    return m.startswith(("o1", "o3", "o4")) or m.startswith("gpt-5")


def build_body(model: str, messages: list, max_tokens: int) -> dict:
    body = {"model": model, "messages": messages}
    if is_reasoning(model):
        body["max_completion_tokens"] = max(max_tokens, 4000)   # room for hidden reasoning
    else:
        body["temperature"] = 0
        body["max_tokens"] = max_tokens
    return body


def run_batch(client, tasks: dict, model: str, max_tokens: int, workdir: str,
              round_i: int, poll: int = 20, reuse_id: str | None = None) -> dict:
    """tasks: {custom_id: messages}. Returns {custom_id: (content|None, usage, error|None)}.
    If reuse_id is given, re-fetch that already-submitted batch instead of paying again."""
    if reuse_id:
        batch = client.batches.retrieve(reuse_id)
        print(f"  round {round_i}: reusing batch {reuse_id}", flush=True)
    else:
        os.makedirs(workdir, exist_ok=True)
        inp = os.path.join(workdir, f"round{round_i}_in.jsonl")
        with open(inp, "w", encoding="utf-8") as f:
            for cid, messages in tasks.items():
                f.write(json.dumps({"custom_id": cid, "method": "POST",
                                    "url": "/v1/chat/completions",
                                    "body": build_body(model, messages, max_tokens)}) + "\n")
        up = client.files.create(file=open(inp, "rb"), purpose="batch")
        batch = client.batches.create(input_file_id=up.id, endpoint="/v1/chat/completions",
                                      completion_window="24h")
        print(f"  round {round_i}: batch {batch.id} submitted ({len(tasks)} requests)", flush=True)
    while True:
        batch = client.batches.retrieve(batch.id)
        c = batch.request_counts
        print(f"    status={batch.status}  {c.completed}/{c.total} done, {c.failed} failed",
              flush=True)
        if batch.status in ("completed", "failed", "expired", "cancelled"):
            break
        time.sleep(poll)
    if batch.status != "completed":
        print(f"  !! batch ended {batch.status}: {getattr(batch, 'errors', None)}", flush=True)

    out = {}
    if batch.output_file_id:
        for line in client.files.content(batch.output_file_id).text.splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            resp = d.get("response") or {}
            if d.get("error") or resp.get("status_code") != 200:
                out[d["custom_id"]] = (None, {}, str(d.get("error") or resp.get("status_code")))
            else:
                body = resp["body"]
                content = (body["choices"][0]["message"].get("content") or "").strip()
                out[d["custom_id"]] = (content, body.get("usage", {}), None)
    if batch.error_file_id:
        for line in client.files.content(batch.error_file_id).text.splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            out.setdefault(d["custom_id"], (None, {}, str(d.get("error"))))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--schema", default="schema/prompt_schema.md")
    ap.add_argument("--questions", required=True)
    ap.add_argument("--model", default="gpt-4o")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-repairs", type=int, default=2)
    ap.add_argument("--max-tokens", type=int, default=800)
    ap.add_argument("--workdir", default="build/openai_batch")
    ap.add_argument("--exec-timeout", type=int, default=90,
                    help="per-query execution timeout in seconds (a runaway query is scored as an error)")
    ap.add_argument("--reuse-batch",
                    help="reuse an existing completed round-0 batch id (skips re-submission and cost)")
    ap.add_argument("--dry-run", action="store_true",
                    help="write the round-0 request file and stop (no API spend)")
    a = ap.parse_args()

    load_dotenv()
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY not found; put it in .env")

    store = Store.read_only(a.store)
    schema = open(a.schema, encoding="utf-8").read()
    known = load_known_terms(store)
    system = SYSTEM_TEMPLATE.format(schema=schema)
    questions = json.load(open(a.questions, encoding="utf-8"))
    print(f"{len(questions)} questions, model={a.model}, repair budget={a.max_repairs}", flush=True)

    state = {it["id"]: {"item": it, "attempts": [], "resolved": False, "tokens": 0,
                        "messages": [{"role": "system", "content": system},
                                     {"role": "user", "content": it["question"]}]}
             for it in questions}

    if a.dry_run:
        os.makedirs(a.workdir, exist_ok=True)
        p = os.path.join(a.workdir, "round0_in.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            for it in questions:
                f.write(json.dumps({"custom_id": it["id"], "method": "POST",
                                    "url": "/v1/chat/completions",
                                    "body": build_body(a.model, state[it["id"]]["messages"],
                                                       a.max_tokens)}) + "\n")
        print(f"dry-run: wrote {p} ({len(questions)} requests); no API call made")
        return

    from openai import OpenAI
    client = OpenAI()

    pending = [it["id"] for it in questions]
    for round_i in range(a.max_repairs + 1):
        if not pending:
            break
        results = run_batch(client, {cid: state[cid]["messages"] for cid in pending},
                            a.model, a.max_tokens, a.workdir, round_i,
                            reuse_id=(a.reuse_batch if round_i == 0 else None))
        next_pending = []
        for cid in pending:
            st = state[cid]
            content, usage, err = results.get(cid, (None, {}, "no response"))
            if err is not None:
                st["attempts"].append({"attempt": len(st["attempts"]) + 1, "sparql": "",
                                       "guard": err, "guard_kind": "request_error",
                                       "gen_seconds": None, "usage": usage,
                                       "status": "request_error", "rows": [], "row_count": 0})
                continue
            st["tokens"] += (usage or {}).get("total_tokens", 0)
            q = extract_sparql(content)
            g = guard(q, known)
            att = {"attempt": len(st["attempts"]) + 1, "sparql": q, "guard": g["message"],
                   "guard_kind": g["kind"], "gen_seconds": None, "usage": usage}
            if g["ok"]:
                try:
                    rows, cols, ex = execute_timeout(store, q, a.exec_timeout)
                    att.update({"status": "ok", "columns": cols, "rows": rows,
                                "row_count": len(rows), "exec_seconds": round(ex, 3)})
                    st["attempts"].append(att); st["resolved"] = True
                    continue
                except Exception as e:
                    errmsg = (f"execution exceeded {a.exec_timeout}s" if isinstance(e, TimeoutError)
                              else f"{type(e).__name__}: {str(e)[:200]}")
                    att.update({"status": "exec_error", "error": errmsg, "rows": [], "row_count": 0})
                    st["attempts"].append(att)
                    if round_i < a.max_repairs:
                        st["messages"] += [{"role": "assistant", "content": q},
                                           {"role": "user",
                                            "content": repair_message(g, known, exec_error=errmsg)}]
                        next_pending.append(cid)
                    continue
            att.update({"status": "guard_failed", "rows": [], "row_count": 0})
            st["attempts"].append(att)
            if round_i < a.max_repairs:
                st["messages"] += [{"role": "assistant", "content": q},
                                   {"role": "user", "content": repair_message(g, known)}]
                next_pending.append(cid)
        print(f"  round {round_i} done: {len(next_pending)} to repair", flush=True)
        pending = next_pending

    out = []
    for it in questions:
        st = state[it["id"]]
        atts = st["attempts"]
        final = atts[-1] if atts else {"status": "request_error", "sparql": "",
                                       "guard": "no attempts", "rows": [], "row_count": 0,
                                       "columns": []}
        resolved = final["status"] == "ok"
        out.append({"id": it["id"], "tier": it.get("tier"), "question": it["question"],
                    "attempts": atts, "n_attempts": len(atts),
                    "first_time_ok": bool(atts) and atts[0]["status"] == "ok",
                    "repaired": resolved and len(atts) > 1, "unresolved": not resolved,
                    "status": final["status"], "sparql": final.get("sparql", ""),
                    "guard": final.get("guard", ""), "rows": final.get("rows", []),
                    "row_count": final.get("row_count", 0), "columns": final.get("columns", []),
                    "total_gen_seconds": None, "usage_tokens": st["tokens"]})

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(out, open(a.out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    n = len(out)
    print(f"\nwrote {a.out}: {sum(r['status'] == 'ok' for r in out)}/{n} resolved, "
          f"{sum(r['first_time_ok'] for r in out)} first-try, "
          f"{sum(r['unresolved'] for r in out)} unresolved, "
          f"{sum(r['usage_tokens'] for r in out)} tokens")


if __name__ == "__main__":
    main()
