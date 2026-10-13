---
name: simulate-budget
description: >-
  Use when the user runs `/simulate-budget` or asks to estimate, model, or stress-test the memory
  and runner-concurrency footprint of a GitLab CI test job, for example to check whether a worker
  count would be OOM killed (exit 137) on a given runner size.
---

# /simulate-budget: GitLab CI Runner Simulator

The simulator is `scripts/simulate_budget.py` in the sibling `gitlab-ci-optimizer` skill, so install both skills together.

## Instructions for the Agent

### 1. Collect the Parameters
The script detects nothing itself. For any value the user did not give, read the project:
- **Stack** (`--stack`, script default `node`): `package.json` means node, `pyproject.toml` / `uv.lock` means python, `go.mod` means go.
- **Workers** (`--workers`, default 2): `--maxWorkers`, `pytest -n`, `go test -p` in `.gitlab-ci.yml`.
- **Shards** (`--shards`, default 1): `parallel: N` on the test job.
- **Runner** (`--ram` 2048, `--vcpus` 2, `--slots` 2): ask, or keep the defaults and say that you assumed them.

### 2. Run the Simulator
```bash
python3 .agents/skills/gitlab-ci-optimizer/scripts/simulate_budget.py --stack node --ram 2048 --vcpus 2 --slots 2 --workers 2 --shards 2
```
`--help` lists every flag, including `--no-prebuilt` and `--no-install-once`. The exit code is 1 when the job would be OOM killed.

### 3. Present the Results
1. **Memory status**: `FITS` (within 75% of runner RAM), `TIGHT` (over 75%), or `OOM_KILL (Exit 137)` (over 100%).
2. **Each recommendation** the script printed, unchanged.
3. **The YAML** that applies the printed cap (`NODE_OPTIONS`, `GOMEMLIMIT`, the worker flag).

Say that the figures come from illustrative per-worker memory and suite-duration constants, so they compare configurations but do not predict this project's real job. A real peak comes from `memory.peak` in a job log.

## Related Resources
* [GitLab CI Optimizer Skill](../gitlab-ci-optimizer/SKILL.md)
* [Memory & Exit 137 Reference](../gitlab-ci-optimizer/references/memory-and-oom.md)
* [DAG & Test Sharding Reference](../gitlab-ci-optimizer/references/dag-and-sharding.md)
