---
name: gitlab-ci-optimizer
description: >-
  Use this skill to design, audit, debug, or optimize GitLab CI/CD pipelines (.gitlab-ci.yml).
  Applies proven patterns from the Gauntlet GitLab Primer and industry best practices: eliminates
  OOM kills (exit 137), caps workers/heap to runner specs, eliminates the 3 setup taxes (prebuilt
  CI image + install-once artifact sync), optimizes DAG dependencies (needs), prevents duplicate
  merge request pipelines (workflow:rules), configures fast artifact expiration, and shards test
  suites with parallel.
---

# GitLab CI Pipeline Optimizer

This skill provides an authoritative field manual and automated toolkit for building lean, fast, and OOM-resilient GitLab CI/CD pipelines. It is derived from the **Gauntlet GitLab Primer** (`labs.gauntletai.com`) and production engineering best practices.

---

## Quick Diagnostic & Helper Tools

When analyzing or modifying a project with `.gitlab-ci.yml`:

1. **Audit an existing pipeline against the 11-point lean scorecard**:
   ```bash
   python3 .agents/skills/gitlab-ci-optimizer/scripts/audit_pipeline.py .gitlab-ci.yml
   ```
2. **Simulate memory consumption and runner concurrency fit** (Available as slash command `/simulate-budget`):
   ```bash
   python3 .agents/skills/gitlab-ci-optimizer/scripts/simulate_budget.py --stack node --ram 2048 --vcpus 2 --slots 2 --workers 2 --shards 2
   ```
   > [!TIP]
   > You or the user can type `/simulate-budget` directly in the chat to launch the interactive runner budget simulator.


---

## The Core Optimization Doctrine

### 1. The Three Setup Taxes
Every CI job pays up to three setup taxes before executing real work:
* **Image Tax (Tools)**: Downloading bloated base images and installing OS packages (`apt-get`, `curl`, `jq`).
  * *Fix*: Bake tools into a minimal `Dockerfile.ci` pushed to `$CI_REGISTRY_IMAGE/ci:latest` in `.pre`.
* **Install Tax (Dependencies)**: Resolving and downloading packages (`node_modules/`, `.venv/`, `.go/mod/`).
  * *Fix*: **Install once, sync everywhere**. Run a single `install` job with lockfile caching, pass dependencies downstream via ephemeral artifacts (`expire_in: 1 hour`), and declare `needs: [install]`.
* **Job Tax (Your Code)**: Linting, compiling, and testing. This is the **only** tax you should pay on each push.

### 2. The Six Memory & Runner Survival Rules
Most pipeline failures on 1GB–4GB runner containers are not code bugs—they are **exit 137 OOM kills** caused by tools sized for multi-core laptops.
1. **Know the Kill Signal**: Exit code 137 (`128 + 9 SIGKILL`), a bare `Killed` message, or `heap out of memory` means RAM starvation. Debug memory allocations first, not test logic.
2. **Cap the Workers**: Containers inherit the host CPU count (e.g. 16 or 32 cores), leading tools to spawn dozens of workers on a 2-vCPU runner. Pin workers to runner vCPUs: `jest --maxWorkers=2`, `pytest -n 2`, `go test -p 2`.
3. **Cap the Heap**: Set the runtime ceiling to **~75% of runner RAM**:
   * Node: `NODE_OPTIONS="--max-old-space-size=1536"` (or `--workerIdleMemoryLimit=512MB`).
   * Go: `GOMEMLIMIT="1500MiB"`.
   * JVM: `-XX:MaxRAMPercentage=75`.
4. **One Heavy Thing per Job**: Do not build, boot databases, and run full E2E suites inside a single job. Decouple jobs and pass artifacts forward using `needs:`.
5. **Shard Anything Over ~8–10 Minutes**: Use `parallel: N` (e.g. `parallel: 2` or `4`). Split suites with Jest `--shard`, `pytest-split`, or Go package partitioning. **Always ensure `shards <= concurrent_runner_slots`** to prevent queuing in waves.
6. **Don't Run What Didn't Change**: Use `rules: changes:` on lint and unit test jobs to skip execution when files outside that domain are modified.

---

## Step-by-Step Optimization Workflow

Follow this procedure when reviewing or rewriting a pipeline:

### Step 1: Profiling Runner Boundaries
Determine or ask for runner constraints:
* **RAM**: Typically 1024MB, 2048MB, or 4096MB.
* **vCPUs**: Typically 2 vCPUs per job container.
* **Concurrent Slots**: Total simultaneous jobs the runner fleet can execute.

### Step 2: Structural Pipeline Hygiene
In the root `.gitlab-ci.yml`:
1. **Deduplicate Pipelines**: Add top-level `workflow: rules:` to prevent duplicate runs for branch pushes vs open merge requests.
   ```yaml
   workflow:
     rules:
       - if: $CI_PIPELINE_SOURCE == "merge_request_event"
       - if: $CI_COMMIT_BRANCH && $CI_OPEN_MERGE_REQUESTS
         when: never
       - if: $CI_COMMIT_BRANCH
   ```
2. **Auto-Cancel Stale Runs**: Set `default: interruptible: true`.
3. **Tighten Timeouts**: Set `default: timeout: 15m` (or `20m`) to prevent hung processes from consuming monthly runner quotas.
4. **Shallow Clone**: Configure `variables: GIT_DEPTH: "20"`.

### Step 3: Implement Install-Once & Caching Architecture
Replace per-job `npm install` / `pip install` / `go mod download` with a dedicated `install` job:
* Key the cache strictly to the lockfile (`package-lock.json`, `uv.lock`, `go.sum`).
* Restrict cache write permissions: `policy: pull-push` on the install job, `policy: pull` on downstream consumer jobs.
* Export vendor directory as an artifact with `expire_in: 1 hour`.
* Downstream jobs (`lint`, `test`, `build`) declare `needs: [install]` to mount dependencies immediately.

### Step 4: Tune Memory & Prevent OOM (Exit 137)
* Ensure test runner workers match runner vCPUs (typically 2).
* Export stack-appropriate heap cap in `variables:`.
* For Node/Jest test suites, divide the memory ceiling by worker count:
  `NODE_OPTIONS="--max-old-space-size=512"` with `--workerIdleMemoryLimit=512MB`.

### Step 5: Convert Linear Stages to DAG (`needs:`)
* Replace stage barriers with explicit job dependencies.
* Build jobs should run immediately when compilation prerequisites are ready, without waiting for the entire test suite to pass.
* If a downstream job only depends on completion order without needing artifacts, pass `needs: [{job: ..., artifacts: false}]`.

### Step 6: Offload Heavy Suites from the Push Path
* E2E tests (Playwright, Cypress, Selenium) and comprehensive security scanners (DAST/SAST) should be scheduled (`if: $CI_PIPELINE_SOURCE == "schedule"`) or made manual (`when: manual`, `allow_failure: true`).

### Step 7: Validate & Measure
1. Run `python3 .agents/skills/gitlab-ci-optimizer/scripts/audit_pipeline.py .gitlab-ci.yml`.
2. Ensure the Lean Score reaches >= 90/100.
3. Validate GitLab CI syntax with the GitLab CI Lint API or `gitlab-ci-lint` CLI if installed.

---

## Stack-Specific Implementation Guides

* **Node.js / TypeScript**: See [node.gitlab-ci.yml](./examples/node.gitlab-ci.yml)
* **Python (uv & Pytest)**: See [python.gitlab-ci.yml](./examples/python.gitlab-ci.yml)
* **Go (go mod & go test)**: See [go.gitlab-ci.yml](./examples/go.gitlab-ci.yml)
* **Monorepo (Child Pipelines)**: See [monorepo.gitlab-ci.yml](./examples/monorepo.gitlab-ci.yml)

## Deep-Dive References

* [Memory Ceilings & Exit 137 Diagnosis](./references/memory-and-oom.md)
* [The 3 Taxes, Caching vs. Artifacts & Lockfiles](./references/setup-tax-and-caching.md)
* [DAG Dependencies, Parallel Sharding & Hygiene](./references/dag-and-sharding.md)
