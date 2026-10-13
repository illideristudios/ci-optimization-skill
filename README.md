# GitLab CI/CD Optimization Skill (`ci-optimization-skill`)

An agent skill, two small Python tools, and four pipeline templates for designing, auditing, and optimizing GitLab CI/CD pipelines (`.gitlab-ci.yml`).

Derived from the **[Gauntlet GitLab Primer](https://gitlab-primer.netlify.app/)** and modern CI/CD engineering best practices.

---

## ⚡ The Problem: Why CI Pipelines Break & Waste Budget

1. **Exit Code 137 (OOM Kills)**: Runner containers (typically 1GB–4GB RAM, 2 vCPUs) report host hardware (e.g. 16–32 CPUs). Tools that size their worker pool from the CPU count (Jest, Vitest, `pytest -n auto`) spawn dozens of worker processes, causing catastrophic CPU thrashing and immediate kernel OOM termination (`128 + 9 = SIGKILL`).
2. **The 3 Setup Taxes**: Every job pays taxes pulling images, installing OS packages (`apt-get`, `curl`, `jq`), and reinstalling language dependencies. On small runners, overhead dominates real testing/building time.
3. **Duplicate Pipelines**: Pushing to a branch with an open Merge Request often triggers **two identical pipelines** (branch pipeline + MR pipeline), doubling CI runner billing.
4. **Queue Waves**: Sharding tests into 8 parallel jobs on a cluster with only 2 concurrent runner slots causes jobs to queue in waves, multiplying wall-clock wait times.

---

## 🛡️ The Core Optimization Doctrine

### 1. Zero Exit 137 OOM Kills
* **Cap the Workers**: Pin test processes to the runner's vCPUs (2 on Gauntlet runners):
  * **Node.js**: `npx jest --maxWorkers=2 --workerIdleMemoryLimit=512MB`
  * **Python**: `uv run pytest -n 2` (needs `pytest-xdist`; without `-n` pytest is already serial)
  * **Go**: `go test -p 2` with `GOMAXPROCS="2"`
* **Cap Runtime Heap**: The ceilings add up to 75% of container RAM, and each applies per process, so divide by the processes the job runs. On a 2048 MB runner:
  * **Node.js**: `NODE_OPTIONS="--max-old-space-size=1536"` for one process, `512` under Jest with 2 workers
  * **Go**: `GOMEMLIMIT="768MiB"` with `go test -p 2`
  * **JVM**: `JAVA_TOOL_OPTIONS="-XX:+UseContainerSupport -XX:MaxRAMPercentage=75.0"`
  * **Python**: CPython has no heap cap; `MALLOC_ARENA_MAX="2"` limits fragmentation and the worker cap bounds memory

### 2. Pay the Setup Tax Once
* **Prebuilt CI Image (Layer 1)**: Keep tools in `Dockerfile.ci` built in `.pre` stage only when the Dockerfile changes on the default branch, pushing to `$CI_REGISTRY_IMAGE/ci:latest`.
* **Install Once, Sync Everywhere (Layer 2)**: A single `install` job with frozen lockfiles (`npm ci`, `uv sync --frozen`, `go mod download`) caches package manager stores and passes installed vendor directories forward via short-lived artifacts (`expire_in: 1 hour`).
* **DAG Execution with `needs:` (Layer 3)**: Downstream jobs (`lint`, `test`, `build`) declare `needs: [install]` to start immediately with dependencies already mounted on disk.

### 3. Pipeline Hygiene & Concurrency Tuning
* **One Pipeline per Push**: Enforce top-level `workflow: rules:` to prioritize MR pipelines and eliminate duplicate branch runs.
* **Auto-Cancel Stale Runs**: `default: interruptible: true` cancels outdated pipelines when new commits are pushed. Deploy, publish and image-build jobs set `interruptible: false`.
* **Tight Timeouts**: `default: timeout: 15m` stops a hung job from draining the runner quota.
* **Shallow Clone**: `variables: GIT_DEPTH: "20"` skips downloading full git commit histories.
* **Shards <= Runner Slots**: Ensure `parallel: N` test shards never exceed available concurrent runner slots.
* **Offload Heavy Scans**: Move end-to-end browser suites (Playwright/Cypress) and full SAST/DAST security scans to schedules (`$CI_PIPELINE_SOURCE == "schedule"`) or manual triggers.

---

## 📦 Install

The scripts need Python 3 and nothing else. Every agent file refers to the skill at `.agents/skills/gitlab-ci-optimizer/`, so copy `skills/` there in the project whose pipeline you are working on:

```bash
git clone https://github.com/locriani/ci-optimization-skill
cd your-project
mkdir -p .agents && cp -R ../ci-optimization-skill/skills .agents/skills
```

Then copy the adapter for each agent you use:

| Agent | Copy into your project | What it does |
| :--- | :--- | :--- |
| **Google Antigravity** | nothing more | Loads both skills from `.agents/skills/`. |
| **Claude Code** | `.claude/commands/simulate-budget.md` | Adds the `/simulate-budget` command. |
| **Cursor** | `.cursorrules`, `.cursor/rules/*.mdc` | Attaches the optimization rules when a `.gitlab-ci*.yml` or `Dockerfile.ci` is open; `/simulate-budget` rule. |
| **OpenAI Codex / Copilot** | `CODEX.md`, `AGENTS.md`, `.codex/instructions.md` | Multi-agent instructions and CLI memory budgeting guides. |

In this repository `.agents/skills` is a symlink to `skills/`, so the same paths work here.

---

## 🧰 Included Tools & Scripts

### 1. Runner Memory & Concurrency Simulator (`/simulate-budget`)
Estimates peak RAM per test job, flags OOM risk, and estimates wall-clock time on a given number of runner slots:

```bash
python3 skills/gitlab-ci-optimizer/scripts/simulate_budget.py \
  --stack node \
  --ram 2048 \
  --vcpus 2 \
  --slots 2 \
  --workers 2 \
  --shards 2
```

`--no-prebuilt` and `--no-install-once` model a pipeline without those two fixes. The exit code is 1 when the job would be OOM killed. The simulator does not read your pipeline: suite duration and per-worker memory are illustrative constants, so it compares configurations and does not predict a real job.

### 2. 11-Point Lean Scorecard Pipeline Auditor
Audits any `.gitlab-ci.yml` against the scorecard below and prints a Lean Score from 0 to 100, the share of applicable checks that pass:

```bash
python3 skills/gitlab-ci-optimizer/scripts/audit_pipeline.py path/to/.gitlab-ci.yml
```

Pass `--ram` and `--max-workers` when the runner is not 2048 MB / 2 vCPU. The exit code is 0 when every applicable check passes, 1 when one fails, and 2 when the file cannot be audited. A check that does not apply to the pipeline (no artifacts, no test command, a parent pipeline that only triggers children) is reported as `N/A`.

The checks:
1. One pipeline per push (`workflow: rules`)
2. Stale pipelines cancel (`default: interruptible: true`)
3. Default job timeout <= 15m
4. Every `artifacts:` block expires within 1 day
5. Shallow git clone (`GIT_DEPTH` 1-20)
6. Test workers capped to runner vCPUs (`--maxWorkers`, `-n`, `-p`, `--workers`)
7. Runtime heap ceiling, summed over a job's processes, <= 75% of runner RAM (`NODE_OPTIONS`, `GOMEMLIMIT`)
8. Dependencies installed in one job only
9. No tool installs at job time (prebuilt CI image)
10. Every test or trigger job on the push path skips untouched code (`rules: changes:`)
11. E2E and security scans off the push path (`schedule` or `manual`)

The auditor matches text in one block-style YAML file with comments removed. It does not follow `include:`, `extends:` or anchors, so read the YAML before acting on a result.

### Tests

```bash
python3 -m unittest discover -s tests
```

---

## 📁 Repository Layout

```text
.
├── SKILL.md                          # Master skill entrypoint
├── README.md                         # Project documentation
├── AGENTS.md                         # Universal multi-agent specification
├── CODEX.md                          # OpenAI Codex configuration & field manual
├── .cursorrules                      # Project-level Cursor AI instructions
├── .agents/skills -> ../skills       # The path every agent file uses
├── .claude/commands/
│   └── simulate-budget.md            # Claude Code /simulate-budget slash command
├── .cursor/rules/
│   ├── gitlab-ci.mdc                 # Auto-trigger rule for .gitlab-ci*.yml files
│   └── simulate-budget.mdc           # Cursor slash command / simulation rule
├── .codex/
│   └── instructions.md               # Codex workspace prompt
├── scripts/ references/ examples/    # Root copy of the skill's directories (a test keeps them identical)
├── skills/
│   ├── gitlab-ci-optimizer/          # Packaged skill bundle
│   │   ├── SKILL.md                  # Skill entrypoint: the rules and the workflow
│   │   ├── scripts/
│   │   │   ├── audit_pipeline.py     # 11-point lean checklist auditor CLI
│   │   │   └── simulate_budget.py    # Runner memory & concurrency simulator CLI
│   │   ├── references/
│   │   │   ├── memory-and-oom.md             # Exit 137, cgroups, host-core trap & heap caps
│   │   │   ├── setup-tax-and-caching.md      # The 3 taxes, Dockerfile.ci & install-once DAG
│   │   │   └── dag-and-sharding.md           # needs:, parallel: N sharding, workflow:rules
│   │   └── examples/
│   │       ├── node.gitlab-ci.yml        # 100/100 lean template for Node.js / TypeScript
│   │       ├── python.gitlab-ci.yml      # 100/100 lean template for Python (uv + Pytest)
│   │       ├── go.gitlab-ci.yml          # 100/100 lean template for Go (go mod + go test)
│   │       └── monorepo.gitlab-ci.yml    # Parent pipeline that triggers per-package children
│   └── simulate-budget/              # /simulate-budget skill (uses the simulator above)
└── tests/
    └── test_scripts.py               # Tests for both scripts; the templates must pass the auditor
```

---

## 📜 License

MIT License. See [LICENSE](LICENSE) for details.