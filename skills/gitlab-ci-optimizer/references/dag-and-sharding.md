# DAG Execution, Test Sharding & Pipeline Deduplication

## 1. DAG (Directed Acyclic Graph) Pipelines with `needs:`

Standard GitLab CI stages force sequential barriers: every job in `stage: lint` must finish before any job in `stage: test` can begin.

```text
Standard Stages (Linear Wait):
[ lint-frontend ] ───┐
[ lint-backend  ] ───┼──> (Stage Barrier Wait) ──> [ test-frontend ] ──> [ build-frontend ]
[ security-scan ] ───┘ (Waiting for slowest)       [ test-backend  ] ──> [ build-backend  ]

With DAG (needs:):
[ lint-frontend ] ─────────────────────────┐
[ security-scan ]                          ├─> [ build-frontend ]
[ install-frontend ] ─> [ test-frontend ] ─┘
```

### DAG Best Practices
1. **Independent Lanes**: Fast jobs (linting, typechecks) run without blocking compilation or unit tests.
2. **Selective Artifact Downloading**:
   By default, `needs: [job_a]` downloads artifacts from `job_a`. If a job only depends on the completion order but does not need files:
   ```yaml
   integration-test:
     stage: test
     needs:
       - job: db-migrate
         artifacts: false            # Saves download bandwidth and disk I/O
   ```
3. **Run Immediate Fast Jobs (`needs: []`)**:
   Jobs in downstream stages that require 0 preceding work can start at second zero of the pipeline:
   ```yaml
   markdown-lint:
     stage: test
     needs: []                       # Starts instantly when pipeline starts
     script: markdownlint docs/
   ```

---

## 2. Test Sharding (`parallel: N`)

When a test suite takes longer than 8–10 minutes, shard it horizontally using GitLab's built-in `$CI_NODE_INDEX` (1-indexed) and `$CI_NODE_TOTAL`.

> **Runner Slot Constraint**: Never set `parallel: N` higher than the number of available concurrent runner slots.
> If your runner cluster has 4 execution slots, `parallel: 8` will queue in waves, creating idle pipeline overhead. Match shards to slots!

### Test Runner Configurations

#### 1. Jest (v28+)
```yaml
test:unit:
  stage: test
  parallel: 4
  needs: [install]
  script:
    - npx jest --maxWorkers=2 --shard=$CI_NODE_INDEX/$CI_NODE_TOTAL
```

#### 2. Vitest
```yaml
test:unit:
  stage: test
  parallel: 4
  needs: [install]
  script:
    - npx vitest run --pool=forks --poolOptions.forks.maxForks=2 --shard=$CI_NODE_INDEX/$CI_NODE_TOTAL
```

#### 3. Playwright E2E
```yaml
test:e2e:
  stage: e2e
  parallel: 4
  needs: [build]
  script:
    - npx playwright test --workers=2 --shard=$CI_NODE_INDEX/$CI_NODE_TOTAL
```

#### 4. Python (Pytest with `pytest-split`)
`pytest-split` uses previous timing data (a committed `.test_durations` file) or, without it, test count to distribute tests across shards. `-n` needs `pytest-xdist`:
```yaml
test:python:
  stage: test
  parallel: 3
  needs: [install]
  script:
    - uv run pytest -n 2 --splits $CI_NODE_TOTAL --group $CI_NODE_INDEX
```

#### 5. Go
Go does not have a native `--shard` flag, but packages can be partitioned cleanly via `awk`:
```yaml
test:go:
  stage: test
  parallel: 3
  needs: [install]
  script:
    - PKGS=$(go list ./... | awk "NR % $CI_NODE_TOTAL == ($CI_NODE_INDEX - 1)")
    - '[ -n "$PKGS" ] || { echo "no packages in this shard"; exit 0; }'
    - go test -p 2 -v $PKGS
```

---

## 3. Pipeline Deduplication & Hygiene

### 1. One Pipeline Per Push (`workflow: rules:`)
A common GitLab antipattern is triggering **two duplicate pipelines** for every push: a branch pipeline and a merge request pipeline.
Halve runner consumption with this top-level rule:

```yaml
workflow:
  rules:
    # 1. Run pipeline on merge request events
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    # 2. Don't run branch pipeline if an open MR exists for this branch
    - if: $CI_COMMIT_BRANCH && $CI_OPEN_MERGE_REQUESTS
      when: never
    # 3. Run branch pipeline for pushes to branches without an open MR
    - if: $CI_COMMIT_BRANCH
```

### 2. Auto-Cancel Stale Pipelines (`interruptible: true`)
When developers push multiple commits in quick succession, older running pipelines become obsolete:

```yaml
default:
  interruptible: true                # Automatically terminates running job when new commit is pushed
```

Set `interruptible: false` on every job that must not stop halfway: deploys, publishes, and the `ci-image` build. A cancelled deploy is left half-applied, and a cancelled image build is never retried.

### 3. Skip Unchanged Code (`rules: changes:`)
Don't run frontend tests when only Python backend files changed, and don't run tests on documentation updates:

```yaml
test:frontend:
  stage: test
  rules:
    - changes:
        - frontend/**/*
        - package-lock.json
        - .gitlab-ci.yml

test:backend:
  stage: test
  rules:
    - changes:
        - backend/**/*
        - pyproject.toml
        - uv.lock
        - .gitlab-ci.yml
```

### 4. Offload Heavy Work from Push Path
Full security scans (SAST/DAST) and end-to-end browser suites do not belong on every feature branch push:

```yaml
e2e:
  stage: e2e
  rules:
    - if: $CI_PIPELINE_SOURCE == "schedule"    # Runs nightly on cron
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH # Runs on merge to main
    - when: manual                            # Can be triggered manually in MR
      allow_failure: true
```

### 5. Concurrency Protection (`resource_group:`)
Stop two deployments from running at once:

```yaml
deploy:production:
  stage: deploy
  resource_group: production        # Serializes jobs so only one deployment runs at a time
  interruptible: false              # A new push must not cancel a deploy halfway
  script:
    - ./deploy.sh
```
