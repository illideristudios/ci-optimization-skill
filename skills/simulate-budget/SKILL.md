---
name: simulate-budget
description: >-
  Simulate runner memory, peak RAM, wall-clock time, and concurrency fit for a GitLab CI pipeline
  to prevent Exit 137 OOM kills. Triggered by the `/simulate-budget` slash command.
---

# /simulate-budget: GitLab CI Runner Simulator

Use this skill when the user triggers the `/simulate-budget` slash command or asks to model, simulate, or stress-test the memory and concurrency footprint of a GitLab CI test suite.

---

## Instructions for the Agent

When `/simulate-budget` is triggered:

### 1. Extract Parameters or Auto-Detect
Check if the user provided specific flags, e.g.:
* `--ram <MB>` (default: 2048)
* `--vcpus <count>` (default: 2)
* `--slots <count>` (default: 2)
* `--stack <node|python|go>` (default: auto-detect from project)
* `--workers <count>` (default: auto-detect from `.gitlab-ci.yml` or default 2)
* `--shards <count>` (default: auto-detect from `parallel:` or default 1)

If `.gitlab-ci.yml` exists in the repository, inspect it to auto-detect:
- **Stack**: Node/TypeScript (`package.json`), Python (`pyproject.toml` / `uv.lock`), Go (`go.mod`).
- **Configured Workers**: Look for `--maxWorkers`, `pytest -n`, `go test -p`.
- **Configured Shards**: Look for `parallel: N`.

### 2. Execute the Simulator
Run the Python simulation script:
```bash
python3 .agents/skills/gitlab-ci-optimizer/scripts/simulate_budget.py [ARGS]
```

Example invocations:
```bash
# Default 2GB / 2 vCPU runner with Node
python3 .agents/skills/gitlab-ci-optimizer/scripts/simulate_budget.py --stack node --ram 2048 --vcpus 2 --slots 2 --workers 2 --shards 2

# Python test suite simulation
python3 .agents/skills/gitlab-ci-optimizer/scripts/simulate_budget.py --stack python --ram 2048 --vcpus 2 --slots 2 --workers 2 --shards 2

# Stress test (e.g. 8 workers on 2GB RAM to verify OOM threshold)
python3 .agents/skills/gitlab-ci-optimizer/scripts/simulate_budget.py --stack node --ram 2048 --vcpus 2 --slots 2 --workers 8 --shards 1
```

### 3. Present Results & Concrete Remediation
Present the simulation findings to the user:
1. **Memory Verdict**:
   * `FITS`: Headroom is safe for OS and disk I/O buffers.
   * `TIGHT`: Within 85% of limit. A few large test suites or memory leaks could trigger Exit 137. Recommend worker memory recycling.
   * `OOM_KILL (Exit 137)`: Peak allocation exceeds container limits. Immediate reduction of workers is mandatory.
2. **Workers vs. vCPUs**:
   * If workers exceed vCPUs, warn that extra processes cause CPU thrashing and memory bloat without improving test execution speed.
3. **Shards vs. Runner Slots**:
   * If `parallel: N` exceeds concurrent slots, warn that shards will queue in waves.
4. **Copy-Paste Remediation**:
   Provide the exact YAML snippet for `.gitlab-ci.yml` to apply the recommended caps (`NODE_OPTIONS`, `GOMEMLIMIT`, `--maxWorkers`, `--workerIdleMemoryLimit`, etc.).

---

## Related Resources
* [GitLab CI Optimizer Skill](../gitlab-ci-optimizer/SKILL.md)
* [Memory & Exit 137 Reference](../gitlab-ci-optimizer/references/memory-and-oom.md)
* [DAG & Test Sharding Reference](../gitlab-ci-optimizer/references/dag-and-sharding.md)
