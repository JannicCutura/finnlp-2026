"""Bulk-load the converted N-Triples into a persistent oxigraph store.

The store is disk-backed (RocksDB), so the full ~69M-triple graph does not need
to fit in memory. Both the intermediate .nt and the store live under data/ and are
git-ignored; the canonical store path is data/oxigraph.

Usage:
    python load_oxigraph.py --nt data/gleif.nt --store data/oxigraph
"""

from __future__ import annotations
import argparse, os, time
from pyoxigraph import Store, RdfFormat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nt", required=True)
    ap.add_argument("--store", required=True)
    ap.add_argument("--no-optimize", action="store_true")
    a = ap.parse_args()

    size = os.path.getsize(a.nt)
    print(f"source : {a.nt}  ({size/2**30:.2f} GiB)", flush=True)
    print(f"store  : {a.store}", flush=True)

    t0 = time.time()
    store = Store(a.store)
    store.bulk_load(path=a.nt, format=RdfFormat.N_TRIPLES)
    print(f"bulk_load done in {time.time()-t0:.0f}s", flush=True)

    if not a.no_optimize:
        t1 = time.time()
        store.optimize()
        print(f"optimize done in {time.time()-t1:.0f}s", flush=True)

    try:
        n = len(store)
        print(f"quads in store: {n:,}", flush=True)
    except Exception as e:  # len() is not guaranteed cheap/available
        print(f"(len unavailable: {e})", flush=True)

    du = sum(os.path.getsize(os.path.join(dp, f))
             for dp, _, fs in os.walk(a.store) for f in fs)
    print(f"store on disk: {du/2**30:.2f} GiB")
    print(f"TOTAL {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
