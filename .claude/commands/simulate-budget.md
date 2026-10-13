---
description: Simulate GitLab CI runner RAM budget, peak memory, and concurrency fit
---

Run a GitLab CI runner budget simulation to evaluate memory consumption, peak RAM, wall-clock time, and worker fit to prevent Exit 137 OOM kills.

### Usage
When invoked as `/simulate-budget` (with optional arguments like `--stack node --ram 2048 --vcpus 2 --workers 2 --shards 2`):

1. Check for `.gitlab-ci.yml` in the project root to detect the current stack, worker flags, and parallelism settings.
2. Execute the simulator:
   ```bash
   python3 .agents/skills/gitlab-ci-optimizer/scripts/simulate_budget.py "$@"
   ```
3. Report the memory verdict (`FITS`, `TIGHT`, or `OOM_KILL`), CPU saturation, and provide the exact YAML snippet to cap workers and runtime heap limits.
