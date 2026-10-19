---
name: gitlab-ci-optimizer
description: >-
  Use when creating, editing, reviewing, or debugging a GitLab CI/CD pipeline (.gitlab-ci.yml or
  Dockerfile.ci), or when a CI job fails with exit 137, "Killed", or heap out of memory. Covers
  worker and heap caps sized to the runner, the prebuilt CI image, the install-once dependency
  cache, DAG dependencies (needs), duplicate merge request pipelines (workflow:rules), artifact
  expiry, and test sharding with parallel. Not for GitHub Actions or other CI systems.
---

# GitLab CI Pipeline Optimizer

Rules and tools for building lean, fast GitLab CI/CD pipelines that survive small runners. Derived from the [Gauntlet GitLab Primer](https://gitlab-primer.netlify.app/).

---

## Quick Diagnostic & Helper Tools

Both scripts live in this skill's `scripts/` directory and need only Python 3. The commands below assume the skill is installed at `.agents/skills/gitlab-ci-optimizer/`; adjust the prefix if it lives elsewhere.

1. **Audit an existing pipeline against the 11-point lean scorecard**:
   ```bash
   python3 .agents/skills/gitlab-ci-optimizer/scripts/audit_pipeline.py .gitlab-ci.yml
   ```
   Pass `--ram` and `--max-workers` when the runner is not 2048 MB / 2 vCPU. Exit code 0 means every applicable check passed, 1 means one failed, 2 means the file could not be audited.
2. **Estimate memory and runner concurrency fit** (slash command `/simulate-budget`):
   ```bash
   python3 .agents/skills/gitlab-ci-optimizer/scripts/simulate_budget.py --stack node --ram 2048 --vcpus 2 --slots 2 --workers 2 --shards 2
   ```
   The simulator does not read the pipeline. Its suite duration and per-worker memory are illustrative constants, so use it to compare configurations, not to predict a real job.

---

## The Core Optimization Doctrine

### 1. The Three Setup Taxes
Every CI job pays up to three setup taxes before executing real work:
* **Image Tax (Tools)**: Downloading bloated base images and installing OS packages (`apt-get`, `curl`, `jq`).
  * *Fix*: Bake tools into a minimal `Dockerfile.ci` pushed to `$CI_REGISTRY_IMAGE/ci:latest` in `.pre`.
* **Install Tax (Dependencies)**: Resolving and downloading packages (`node_modules/`, `.venv/`, `.go/pkg/mod/`).
  * *Fix*: **Install once, sync everywhere**. Run a single `install` job that fills a lockfile-keyed cache, have downstream jobs pull that cache and declare `needs: [install]`. Dependencies never travel as artifacts.
* **Job Tax (Your Code)**: Linting, compiling, and testing. This is the **only** tax you should pay on each push.

### 2. The Six Memory & Runner Survival Rules
Most pipeline failures on 1GB–4GB runner containers are not code bugs—they are **exit 137 OOM kills** caused by tools sized for multi-core laptops.
1. **Know the Kill Signal**: Exit code 137 (`128 + 9 SIGKILL`), a bare `Killed` message, or `heap out of memory` means RAM starvation. Debug memory allocations first, not test logic.
2. **Cap the Workers**: A container can report the host's CPU count (16 or 32 cores), so a tool that sizes its pool from it spawns far too many workers on a 2-vCPU runner. Cap workers at the runner's vCPUs (2 on Gauntlet runners): `jest --maxWorkers=2`, `pytest -n 2`, `go test -p 2`. Pytest is serial unless `pytest-xdist` is installed and `-n` is passed, so do not add `-n` to a suite that fits serially.
3. **Cap the Heap**: The ceilings add up to **75% of runner RAM**, and each one applies **per process**, so divide by the number of processes the job runs. On a 2048 MB runner the budget is 1536 MB:
   * Node, single process (lint, build): `NODE_OPTIONS="--max-old-space-size=1536"`.
   * Node, Jest or Vitest with 2 workers (parent + 2 workers): `NODE_OPTIONS="--max-old-space-size=512"`. Jest's `--workerIdleMemoryLimit=512MB` recycles leaking workers; it is an addition to the cap, not a replacement.
   * Go, `go test -p 2` (2 test binaries): `GOMEMLIMIT="768MiB"` with `GOMAXPROCS="2"`. The limit is soft.
   * JVM: `-XX:MaxRAMPercentage=75`.
   * CPython has no heap cap. `MALLOC_ARENA_MAX="2"` limits glibc fragmentation; the worker cap is what bounds memory.
4. **One Heavy Thing per Job**: Do not build, boot databases, and run full E2E suites inside a single job. Decouple jobs and order them with `needs:`; an artifact passes forward only output a later job cannot rebuild.
5. **Shard Anything Over ~8–10 Minutes**: Use `parallel: N` (e.g. `parallel: 2` or `4`). Split suites with Jest `--shard`, `pytest-split`, or Go package partitioning. **Always ensure `shards <= concurrent_runner_slots`** to prevent queuing in waves.
6. **Don't Run What Didn't Change**: Use `rules: changes:` on lint and unit test jobs to skip execution when files outside that domain are modified.

---

## Step-by-Step Optimization Workflow

Follow this procedure when reviewing or rewriting a pipeline:

### Step 1: Profiling Runner Boundaries
Find the runner constraints. If they are unknown and nobody can be asked, assume 2048 MB / 2 vCPU / 2 slots and say that you assumed it.
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
       - if: $CI_COMMIT_TAG
   ```
2. **Auto-Cancel Stale Runs**: Set `default: interruptible: true`, and `interruptible: false` on every job that must not stop halfway: deploys, publishes, and the `ci-image` build.
3. **Tighten Timeouts**: Set `default: timeout: 15m` so a hung process cannot consume the runner quota.
4. **Shallow Clone**: Configure `variables: GIT_DEPTH: "20"`. New projects already default to 20 in their CI/CD settings; pinning it in the file covers older projects and changed settings.

### Step 3: Implement Install-Once & Caching Architecture
Replace per-job `npm install` / `pip install` / `go mod download` with a dedicated `install` job:
* Key the cache strictly to the lockfile (`package-lock.json`, `uv.lock`, `go.sum`).
* Restrict cache write permissions: `policy: pull-push` on the install job, `policy: pull` on downstream consumer jobs.
* Cache the installed tree with the download store (`node_modules/`, `.venv/`, `.go/pkg/mod/`). The cache is how dependencies reach downstream jobs; vendor folders (`node_modules/`, `.venv/`, `.nuget/`, `obj/`) are never artifacts.
* Downstream jobs (`lint`, `test`, `build`) declare `needs: [install]` and pull the cache. Setting it under `default:` with `policy: pull` and overriding it in `install` with `policy: pull-push` covers every job, as the examples do.
* Upload an artifact only when a later job or a person needs that exact output and cannot rebuild it from the cache: the bundle a deploy ships, a report someone reads. Every such artifact sets `expire_in: 1 hour`. Most jobs upload nothing.

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

### Step 4: Tune Memory & Prevent OOM (Exit 137)
* Ensure test runner workers match runner vCPUs (typically 2).
* Set the heap cap from rule 3 above in `variables:`, divided by the processes the job runs. Override it per job where the process count differs, as `test:unit` does in the Node example.

### Step 5: Convert Linear Stages to DAG (`needs:`)
* Replace stage barriers with explicit job dependencies.
* Build jobs should run immediately when compilation prerequisites are ready, without waiting for the entire test suite to pass.
* If a downstream job only depends on completion order without needing artifacts, pass `needs: [{job: ..., artifacts: false}]`.

### Step 6: Offload Heavy Suites from the Push Path
* E2E tests (Playwright, Cypress, Selenium) and comprehensive security scanners (DAST/SAST) should be scheduled (`if: $CI_PIPELINE_SOURCE == "schedule"`) or made manual (`when: manual`, `allow_failure: true`).

### Step 7: Validate
1. Run `python3 .agents/skills/gitlab-ci-optimizer/scripts/audit_pipeline.py .gitlab-ci.yml`.
2. Ensure the Lean Score reaches >= 90/100.
3. Treat each `FAIL` as a pointer to read that part of the YAML. The auditor matches text in one file and does not follow `include:`, `extends:` or anchors, so confirm a finding before changing the pipeline, and never add a job or keyword only to turn a check green.
4. Validate the syntax with GitLab's CI Lint: `glab ci lint`, or the pipeline editor.

---

## Stack-Specific Implementation Guides

* **Node.js / TypeScript**: See [node.gitlab-ci.yml](./examples/node.gitlab-ci.yml)
* **Python (uv & Pytest)**: See [python.gitlab-ci.yml](./examples/python.gitlab-ci.yml)
* **Go (go mod & go test)**: See [go.gitlab-ci.yml](./examples/go.gitlab-ci.yml)
* **Monorepo (Child Pipelines)**: See [monorepo.gitlab-ci.yml](./examples/monorepo.gitlab-ci.yml)

Each template's header lists what must exist before its first run: a `Dockerfile.ci`, a privileged runner for the image build, and a protected `ci` container repository.

## Deep-Dive References

* [Memory Ceilings & Exit 137 Diagnosis](./references/memory-and-oom.md)
* [The 3 Taxes, Caching vs. Artifacts & Lockfiles](./references/setup-tax-and-caching.md)
* [DAG Dependencies, Parallel Sharding & Hygiene](./references/dag-and-sharding.md)
