# Universal Agent Instructions (AGENTS.md)

This file specifies cross-agent conventions and automated toolchains for all AI coding agents (Antigravity, Claude, Codex, Cursor) operating in this repository.

---

## GitLab CI/CD Optimization Rules (.gitlab-ci.yml)
All GitLab pipelines must adhere to the **Gauntlet GitLab Primer** standards:

### 1. Memory Ceilings & Exit 137 Prevention
Runner containers operate under strict cgroups limits (1GB–4GB RAM, 2 vCPUs).
- **Cap Workers**: Never exceed runner vCPUs (typically 2). Uncapped tools will spawn 16+ processes matching the host node and trigger Exit 137.
  - Node: `npx jest --maxWorkers=2 --workerIdleMemoryLimit=512MB`
  - Python: `uv run pytest -n 2`
  - Go: `go test -p 2`
- **Cap Runtime Heap**: Reserve 25% of runner RAM for OS kernel and I/O. Each cap applies per process, so divide the 75% by the processes a job runs.
  - Node: `variables: NODE_OPTIONS: "--max-old-space-size=1536"` for single-process jobs, `512` in a Jest job with 2 workers (2048 MB runner)
  - Go: `variables: GOMEMLIMIT: "768MiB"` and `GOMAXPROCS: "2"` with `go test -p 2`
  - JVM: `variables: JAVA_TOOL_OPTIONS: "-XX:+UseContainerSupport -XX:MaxRAMPercentage=75.0"`

### 2. The Three Setup Taxes
- **Image Tax**: Use prebuilt images via `Dockerfile.ci` pushed to `$CI_REGISTRY_IMAGE/ci:latest` in `.pre`. Avoid dynamic package installs in `before_script`.
- **Install Tax**: Run one `install` job with frozen lockfiles (`npm ci`, `uv sync --frozen`, `go mod download`), and fill a cache keyed on the lockfile with the download store and the installed tree (`policy: pull-push`); consumers pull it (`policy: pull`). Vendor folders (`node_modules/`, `.venv/`, `.nuget/`, `obj/`) are never artifacts.
- **Artifacts only where they must exist**: Most jobs upload nothing. An artifact is allowed only when a later job or a person needs that exact output and cannot rebuild it from the cache (the bundle a deploy ships, a readable report), and it sets `expire_in: 1 hour`.
- **Job Tax**: Downstream jobs (`lint`, `test`, `build`) declare `needs: [install]` to start immediately with dependencies mounted.

```
A(j) := job j uploads artifacts to GitLab
M(j) := a later job or a person needs j's exact output and cannot rebuild it from the cache
V(j) := j's output is a dependency/vendor folder (node_modules/, .venv/, .nuget/, obj/)
E(j) := j's artifacts set expire_in: 1 hour

1.  A(j) → M(j)       [Zach, 2026-10-19: "artifacts THAT MUST BE THERE expire after 1 hour"]
2.  V(j) → ¬M(j)      [the install job's lockfile-keyed cache carries dependencies to every consumer]
3.  A(j) → E(j)       [Zach, 2026-10-19]
4.  V(j) → ¬A(j)      [2, then the contrapositive of 1: ¬M(j) → ¬A(j)]
∴   install jobs upload nothing, and any artifact that does exist expires in 1 hour
```

### 3. Pipeline Deduplication & Hygiene
- **One Pipeline per Push**: Always add `workflow: rules:` to prioritize MR pipelines and eliminate duplicate branch pipelines.
- **Auto-Cancel Stale Runs**: `default: interruptible: true`.
- **Timeouts**: `default: timeout: 15m`.
- **Shallow Clone**: `variables: GIT_DEPTH: "20"`.
- **Shards <= Slots**: `parallel: N` test shards must never exceed concurrent runner slots.
- **Heavy Scans**: E2E suites and security scans run on schedule or manual triggers.

---

## Tooling & Slash Commands

* **/simulate-budget**:
  Simulate memory consumption, peak RAM, wall-clock time, and worker concurrency fit.
  ```bash
  python3 .agents/skills/gitlab-ci-optimizer/scripts/simulate_budget.py [flags]
  ```
* **Pipeline Audit**:
  Audit any `.gitlab-ci.yml` against the 11-point lean scorecard:
  ```bash
  python3 .agents/skills/gitlab-ci-optimizer/scripts/audit_pipeline.py .gitlab-ci.yml
  ```
