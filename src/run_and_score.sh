#!/usr/bin/env bash
# Run the held-out benchmark end-to-end for one LM Studio model, then score it.
# One model is loaded at a time (never two: the 20B and 120B together exceed RAM).
#
# Usage:
#   src/run_and_score.sh qwen2.5-coder-32b-instruct qwen32b
#   src/run_and_score.sh llama-3.3-70b-instruct     llama70b
#
# After the overnight downloads land, run one line per model; then add the
# scored numbers as columns in paper Table 1 (tab:results).
set -euo pipefail
export PATH="$PATH:$HOME/.lmstudio/bin"

MODEL="${1:?model id, e.g. qwen2.5-coder-32b-instruct}"
LABEL="${2:?short label, e.g. qwen32b}"
Q=eval/questions_hard.json
STORE=data/oxigraph

echo ">>> unload any loaded model, then load $MODEL"
lms unload --all || true
lms load "$MODEL" --context-length 4096 --gpu max

echo ">>> run pipeline"
py -3.11 src/pipeline.py --store "$STORE" --questions "$Q" --model "$MODEL" \
    --out "results/run_hard_${LABEL}.json" --max-repairs 2 --timeout 600

echo ">>> score"
py -3.11 src/score.py --store "$STORE" --questions "$Q" \
    --results "results/run_hard_${LABEL}.json" --out "results/scored_hard_${LABEL}.json"

echo ">>> unload"
lms unload --all || true
echo ">>> done: results/scored_hard_${LABEL}.json"
