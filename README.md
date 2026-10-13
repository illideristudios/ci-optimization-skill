# GitLab CI/CD Optimization Skill (`ci-optimization-skill`)

An authoritative field manual, multi-agent skill, and automated tooling suite for designing, auditing, and optimizing GitLab CI/CD pipelines (`.gitlab-ci.yml`).

Derived from the **[Gauntlet GitLab Primer](https://gitlab-primer.netlify.app/)** and modern CI/CD engineering best practices.

---

## ⚡ The Problem: Why CI Pipelines Break & Waste Budget

1. **Exit Code 137 (OOM Kills)**: Runner containers (typically 1GB–4GB RAM, 2 vCPUs) report host hardware (e.g. 16–32 CPUs). Tools like Jest, Vitest, and Pytest auto-spawn dozens of worker processes, causing catastrophic CPU thrashing and immediate kernel OOM termination (`128 + 9 = SIGKILL`).
2. **The 3 Setup Taxes**: Every job pays taxes pulling images, installing OS packages (`apt-get`, `curl`, `jq`), and reinstalling language dependencies. On small runners, overhead dominates real testing/building time.
3. **Duplicate Pipelines**: Pushing to a branch with an open Merge Request often triggers **two identical pipelines** (branch pipeline + MR pipeline), doubling CI runner billing.
4. **Queue Waves**: Sharding tests into 8 parallel jobs on a cluster with only 2 concurrent runner slots causes jobs to queue in waves, multiplying wall-clock wait times.

---

## 🛡️ The Core Optimization Doctrine

### 1. Zero Exit 137 OOM Kills
* **Cap the Workers**: Always pin test processes to runner vCPUs (max 2):
  * **Node.js**: `npx jest --maxWorkers=2 --workerIdleMemoryLimit=512MB`
  * **Python**: `uv run pytest -n 2`
  * **Go**: `go test -p 2`
* **Cap Runtime Heap**: Limit runtime memory to ~75% of container RAM:
  * **Node.js**: `NODE_OPTIONS="--max-old-space-size=1536"`
  * **Python**: `MALLOC_ARENA_MAX="2"`
  * **Go**: `GOMEMLIMIT="1500MiB"`
  * **JVM**: `JAVA_TOOL_OPTIONS="-XX:+UseContainerSupport -XX:MaxRAMPercentage=75.0"`

### 2. Pay the Setup Tax Once
* **Prebuilt CI Image (Layer 1)**: Keep tools in `Dockerfile.ci` built in `.pre` stage only when the Dockerfile changes on the default branch, pushing to `$CI_REGISTRY_IMAGE/ci:latest`.
* **Install Once, Sync Everywhere (Layer 2)**: A single `install` job with frozen lockfiles (`npm ci`, `uv sync --frozen`, `go mod download`) caches package manager stores and passes installed vendor directories forward via short-lived artifacts (`expire_in: 1 hour`).
* **DAG Execution with `needs:` (Layer 3)**: Downstream jobs (`lint`, `test`, `build`) declare `needs: [install]` to start immediately with dependencies already mounted on disk.

### 3. Pipeline Hygiene & Concurrency Tuning
* **One Pipeline per Push**: Enforce top-level `workflow: rules:` to prioritize MR pipelines and eliminate duplicate branch runs.
* **Auto-Cancel Stale Runs**: `default: interruptible: true` cancels outdated pipelines when new commits are pushed.
* **Shallow Clone**: `variables: GIT_DEPTH: "20"` skips downloading full git commit histories.
* **Shards $\le$ Runner Slots**: Ensure `parallel: N` test shards never exceed available concurrent runner slots.
* **Offload Heavy Scans**: Move end-to-end browser suites (Playwright/Cypress) and full SAST/DAST security scans to schedules (`$CI_PIPELINE_SOURCE == "schedule"`) or manual triggers.

---

## 🤖 Cross-Agent Compatibility

This repository is built to work out-of-the-box across all major AI development tools:

| Platform | Location | Features |
| :--- | :--- | :--- |
| **Google Antigravity** | `skills/gitlab-ci-optimizer/` & `skills/simulate-budget/` | Native skill progressive disclosure and chat `/simulate-budget` slash command. |
| **Anthropic Claude** | `.claude/commands/simulate-budget.md` & `CLAUDE.md` | Claude Code slash command `/simulate-budget` and workspace rules. |
| **Cursor** | `.cursorrules` & `.cursor/rules/*.mdc` | Auto-attaches optimization rules when viewing or editing `.gitlab-ci.yml`. |
| **OpenAI Codex / Copilot** | `CODEX.md`, `AGENTS.md`, `.codex/instructions.md` | Universal multi-agent instructions and CLI memory budgeting guides. |

---

## 🧰 Included Tools & Scripts

### 1. Runner Memory & Concurrency Simulator (`/simulate-budget`)
Simulates peak RAM, detects OOM risk, evaluates CPU saturation, and estimates wall-clock time:

```bash
python3 scripts/simulate_budget.py \
  --stack node \
  --ram 2048 \
  --vcpus 2 \
  --slots 2 \
  --workers 2 \
  --shards 2
```

### 2. 11-Point Lean Scorecard Pipeline Auditor
Audits any `.gitlab-ci.yml` against the Gauntlet Primer scorecard and outputs an actionable score:

```bash
python3 scripts/audit_pipeline.py path/to/.gitlab-ci.yml
```

Evaluated checks:
1. `[✓]` One pipeline per push (`workflow: rules` MR deduplication)
2. `[✓]` Stale pipelines cancel (`interruptible: true`)
3. `[✓]` Tight job timeouts (`timeout: <= 25m`)
4. `[✓]` Artifacts expire quickly (`expire_in: <= 1 day`)
5. `[✓]` Shallow git clone (`GIT_DEPTH: <= 20`)
6. `[✓]` Test workers capped to runner vCPUs (`--maxWorkers=2`, `-n 2`, `-p 2`)
7. `[✓]` Heap memory ceiling configured (~75% RAM)
8. `[✓]` Install once, sync everywhere (DAG artifacts via `needs: [install]`)
9. `[✓]` Prebuilt CI tool image (`Dockerfile.ci` in `.pre`)
10. `[✓]` Skip untouched code (`rules: changes:`)
11. `[✓]` Heavy tests/scans off push path (`schedule` or `manual`)

---

## 📁 Repository Layout

```text
.
├── SKILL.md                          # Master skill entrypoint
├── README.md                         # Project documentation
├── AGENTS.md                         # Universal multi-agent specification
├── CODEX.md                          # OpenAI Codex configuration & field manual
├── .cursorrules                      # Project-level Cursor AI instructions
├── .cursor/rules/
│   ├── gitlab-ci.mdc                 # Auto-trigger rule for .gitlab-ci*.yml files
│   └── simulate-budget.mdc           # Cursor slash command / simulation rule
├── .claude/commands/
│   └── simulate-budget.md            # Claude Code /simulate-budget slash command
├── .codex/
│   └── instructions.md               # Codex workspace prompt
├── scripts/
│   ├── audit_pipeline.py             # 11-point lean scorecard auditor CLI
│   └── simulate_budget.py            # Runner memory & concurrency simulator CLI
├── references/
│   ├── memory-and-oom.md             # Exit 137, cgroups, host-core trap & heap caps
│   ├── setup-tax-and-caching.md      # The 3 taxes, Dockerfile.ci & install-once DAG
│   └── dag-and-sharding.md           # needs:, parallel: N sharding, workflow:rules
├── examples/
│   ├── node.gitlab-ci.yml            # 100/100 lean template for Node.js / TypeScript
│   ├── python.gitlab-ci.yml          # 100/100 lean template for Python (uv + Pytest)
│   ├── go.gitlab-ci.yml              # 100/100 lean template for Go (go mod + go test)
│   └── monorepo.gitlab-ci.yml        # Parent-child pipeline for modular monorepos
└── skills/
    ├── gitlab-ci-optimizer/          # Packaged skill bundle
    └── simulate-budget/              # Dedicated /simulate-budget slash command skill
```

---

## 📜 License

MIT License. See [LICENSE](LICENSE) for details.