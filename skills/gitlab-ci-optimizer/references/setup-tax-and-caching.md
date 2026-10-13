# The Setup Tax, Caching & Artifact Architecture

## 1. The Three Setup Taxes

Every pipeline job pays up to three setup taxes before executing actual work:
1. **The Image Tax (OS & Tools)**: Pulling bloated generic images, running `apt-get update`, installing system libraries (`git`, `curl`, `jq`, compilers, browser binaries).
2. **The Install Tax (Dependencies)**: Downloading and resolving packages (`node_modules/`, `.venv/`, `.go/mod/`).
3. **The Job Tax (Your Work)**: Compiling, linting, executing tests, producing deployment bundles.

> **Golden Principle**: The Job Tax is the **only** tax you should pay on every push. Image setup changes monthly; dependency installation changes weekly.

---

## 2. Layer 1: The Prebuilt CI Image

Instead of running package managers or installing tools in `before_script` on every job:
1. Maintain a minimal `Dockerfile.ci` at the repository root.
2. Build and push it to the GitLab Project Container Registry in the built-in `.pre` stage.
3. Trigger the build **only** when `Dockerfile.ci` changes on the default branch.
4. Set `default: image: $CI_REGISTRY_IMAGE/ci:latest`.

```dockerfile
# Dockerfile.ci - tools only, no app code or dependencies
FROM node:20-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates curl jq \
 && rm -rf /var/lib/apt/lists/*
```

```yaml
# In .gitlab-ci.yml
default:
  image: $CI_REGISTRY_IMAGE/ci:latest

stages:
  - install
  - test
  - build

ci-image:
  stage: .pre                        # Built-in, always executes first if triggered
  image: docker:27
  services:
    - docker:27-dind                 # Requires privileged runner
  variables:
    DOCKER_TLS_CERTDIR: "/certs"
  before_script: []                  # Clear any global before_script
  script:
    - echo "$CI_REGISTRY_PASSWORD" | docker login -u "$CI_REGISTRY_USER" --password-stdin "$CI_REGISTRY"
    - docker build -f Dockerfile.ci -t "$CI_REGISTRY_IMAGE/ci:latest" .
    - docker push "$CI_REGISTRY_IMAGE/ci:latest"
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
      changes:
        - Dockerfile.ci
```

---

## 3. Layer 2: Install Once, Sync Everywhere

Running dependency installation in parallel jobs (lint, unit-test, integration-test, build) is the #1 source of wasted runner compute and network I/O.

### The Two-Tier Strategy:
1. **One dedicated `install` job**:
   - Downloads/resolves dependencies using strict frozen lockfiles.
   - Saves package manager download cache (`cache:`).
   - Exports the prepared vendor folder (`node_modules/`, `.venv/`) as a **short-lived pipeline artifact** (`expire_in: 1 hour`).
2. **Downstream jobs (`lint`, `test`, `build`)**:
   - Depend directly on `needs: [install]`.
   - Start immediately with dependencies already mounted on disk.
   - Do **not** run any install commands in `before_script`.

```yaml
install:
  stage: install
  script:
    - npm ci --cache .npm --prefer-offline
  cache:
    key:
      files:
        - package-lock.json
    paths:
      - .npm/
    policy: pull-push                # Only the install job writes to the cache
  artifacts:
    name: "deps-${CI_COMMIT_REF_SLUG}"
    paths:
      - node_modules/
    expire_in: 1 hour                # Prevent filling runner/GitLab disk
    when: on_success

test:
  stage: test
  needs:
    - install                        # Automatically downloads node_modules/
  script:
    - npx jest --maxWorkers=2
```

---

## 4. Cache vs. Artifacts: Technical Comparison

| Dimension | `cache` | `artifacts` |
| :--- | :--- | :--- |
| **Primary Purpose** | Accelerate downloads across pipelines | Pass verified files to downstream jobs in current pipeline |
| **Availability Guarantee** | **No**. Cold runners or cache evictions start empty | **Yes**. Guaranteed available to subsequent jobs in pipeline |
| **Storage Location** | Runner host disk or S3/MinIO bucket | GitLab server / object storage |
| **Ideal Payload** | Package manager download caches (`.npm`, `.uv-cache`, Go mod cache) | Installed trees (`node_modules`, `.venv`), build output (`dist/`), test reports |
| **Lifespan** | Long-term (weeks/months) | Ephemeral. Always configure `expire_in` (`1 hour` for deps, `1 day` for builds) |
| **Optimal Policy** | `policy: pull-push` on install, `policy: pull` downstream | Passed via `needs: [job_name]` |

---

## 5. Strict Lockfile Installation Matrix

Never use commands that mutate lockfiles in CI (e.g. bare `npm install`, `pip install -r requirements.txt` without hashes).

| Tool / Ecosystem | Strict Command | Download Cache Path | Installed Dependency Output |
| :--- | :--- | :--- | :--- |
| **npm** | `npm ci --cache .npm --prefer-offline` | `.npm/` | `node_modules/` |
| **pnpm** | `pnpm install --frozen-lockfile --prefer-offline` | `.pnpm-store/` | `node_modules/` |
| **yarn (Berry)** | `yarn install --immutable` | `.yarn/cache/` | `.yarn/` or `node_modules/` |
| **Python (uv)** | `uv sync --frozen` | `.uv-cache/` | `.venv/` |
| **Python (Poetry)**| `poetry install --no-root --sync` | `.cache/pypoetry` | `.venv/` |
| **Go** | `go mod download` | `.go/mod/` | `$CI_PROJECT_DIR/.go/mod/` |
| **Rust (Cargo)** | `cargo fetch --locked` | `.cargo/` | `target/` |

---

## 6. Git Transfer Minimization

1. **Shallow Clone (`GIT_DEPTH`)**:
   By default, GitLab clones the entire Git commit history. Set shallow depth:
   ```yaml
   variables:
     GIT_DEPTH: "20"
   ```
2. **Skip Git entirely on artifact-only jobs (`GIT_STRATEGY: none`)**:
   If a downstream job only packages artifacts or deploys previously generated bundles:
   ```yaml
   deploy_review:
     stage: deploy
     variables:
       GIT_STRATEGY: none
     needs:
       - build
     script:
       - rsync -av dist/ user@server:/var/www/
   ```
