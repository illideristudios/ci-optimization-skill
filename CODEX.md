# Codex Field Manual: GitLab CI Optimization & Runner Budgeting

This document instructs OpenAI Codex and related LLM developer tools operating in this workspace.

## GitLab CI Pipeline Standards (.gitlab-ci.yml)
All GitLab CI/CD configurations must adhere strictly to the **Gauntlet GitLab Primer** standards to prevent Exit 137 OOM kills and eliminate wasted runner billing:

### 1. Zero Exit 137 OOM Kills
Runner containers are small (typically 1GB–4GB RAM, 2 vCPUs). Containers inherit host CPU count, causing tools to spawn dozens of workers and exhaust memory.
- **Node.js**:
  - `variables: NODE_OPTIONS: "--max-old-space-size=1536"` (75% of runner RAM) for single-process jobs, `512` in a Jest job with 2 workers: the cap applies per process
  - Command: `npx jest --maxWorkers=2 --workerIdleMemoryLimit=512MB`
- **Python**:
  - `variables: MALLOC_ARENA_MAX: "2"`
  - Command: `uv run pytest -n 2`
- **Go**:
  - `variables: GOMEMLIMIT: "768MiB"` and `GOMAXPROCS: "2"` (the limit applies per test binary)
  - Command: `go test -p 2`

### 2. Eliminate the 3 Setup Taxes
- **Image Tax**: Tools baked into `Dockerfile.ci` pushed to `$CI_REGISTRY_IMAGE/ci:latest` in `.pre` stage. No dynamic `apt-get` or curl tool installs in `before_script`.
- **Install Tax**: One dedicated `install` job with frozen lockfile (`npm ci`, `uv sync --frozen`, `go mod download`). Cache download folders (`.npm/`, `.uv-cache/`) and pass vendor folders forward via artifacts (`expire_in: 1 hour`).
- **Job Tax**: Downstream jobs (`lint`, `test`, `build`) declare `needs: [install]` to start immediately with dependencies mounted.

### 3. Pipeline Hygiene & Concurrency
- **One Pipeline per Push**: Always include `workflow: rules:` to prevent duplicate branch and MR pipelines.
- **Auto-Cancel Stale Runs**: `default: interruptible: true`.
- **Timeouts**: `default: timeout: 15m`.
- **Shallow Clone**: `variables: GIT_DEPTH: "20"`.
- **Sharding**: `parallel: N` test shards must not exceed available concurrent runner slots.
- **Heavy Scans**: E2E and heavy security scans run on schedule (`schedule`) or manual triggers.

### 4. Simulation & Audit Scripts
- **Audit any `.gitlab-ci.yml`**:
  ```bash
  python3 .agents/skills/gitlab-ci-optimizer/scripts/audit_pipeline.py .gitlab-ci.yml
  ```
- **Simulate Runner Memory Budget (`/simulate-budget`)**:
  ```bash
  python3 .agents/skills/gitlab-ci-optimizer/scripts/simulate_budget.py [options]
  ```
  Options: `--stack <node|python|go> --ram <MB> --vcpus <count> --slots <count> --workers <count> --shards <count>`
