---
description: Simulate GitLab CI runner RAM budget, peak memory, and concurrency fit
argument-hint: "[--stack node|python|go] [--ram MB] [--vcpus N] [--slots N] [--workers N] [--shards N]"
---

Estimate GitLab CI runner memory, wall-clock time, and concurrency fit.

1. For any flag not given in the arguments, read `.gitlab-ci.yml` for the stack, the worker flag (`--maxWorkers`, `pytest -n`, `go test -p`) and `parallel:`. Use the runner's RAM, vCPUs and slots if known; otherwise keep the script defaults and say so.
2. Run the simulator (`--help` lists every flag):
   ```bash
   python3 .agents/skills/gitlab-ci-optimizer/scripts/simulate_budget.py $ARGUMENTS
   ```
3. Report the memory status (`FITS`, `TIGHT`, or `OOM_KILL`), each recommendation, and the YAML that applies the printed cap. The figures are estimates from illustrative constants, not measurements of this project.
