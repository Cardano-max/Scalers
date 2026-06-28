# evals

Gold sets + eval suite (eval-on-every-change).

Phase 1 ships a **stub** eval gate (`promptfooconfig.yaml`) wired into
`scripts/done_gate.py` behind `EVAL_GATE=1`. The real gold set + assertions land
in Phase 2 (see `.planning/ROADMAP.md`). See [`../docs/ci.md`](../docs/ci.md) for
how the eval gate plugs into CI.
