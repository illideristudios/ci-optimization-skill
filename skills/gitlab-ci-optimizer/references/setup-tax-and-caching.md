# The Setup Tax, Caching & Artifact Architecture

## 1. The Three Setup Taxes

Every pipeline job pays up to three setup taxes before executing actual work:
1. **The Image Tax (OS & Tools)**: Pulling bloated generic images, running `apt-get update`, installing system libraries (`git`, `curl`, `jq`, compilers, browser binaries).
2. **The Install Tax (Dependencies)**: Downloading and resolving packages (`node_modules/`, `.venv/`, `.go/pkg/mod/`).
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
FROM node:22-slim
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
  interruptible: false               # A cancelled build is never retried: later pushes do not touch Dockerfile.ci
  resource_group: ci-image           # One push of ci:latest at a time
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

### What this image job requires
- **Bootstrap**: the first pipeline must be a push to the default branch that includes `Dockerfile.ci`. On any other ref `ci:latest` does not exist yet and every job fails on image pull. A change to `Dockerfile.ci` in a merge request is tested against the old image until it merges.
- **A privileged runner**: docker-in-docker needs one. Give `ci-image` a dedicated runner; enabling privileged mode on a shared runner gives every branch pipeline root on the runner host.
- **A protected container repository**: `ci:latest` is a mutable tag that every later pipeline runs in, and the `rules:` above are YAML a Developer can edit on their own branch. Add a container repository protection rule for `ci` with minimum push access Maintainer, and confirm on your GitLab version that it covers CI job tokens. Without it a Developer can replace the image that protected-branch pipelines, and their protected variables, run in.

---

## 3. Layer 2: Install Once, Sync Everywhere

Running dependency installation in parallel jobs (lint, unit-test, integration-test, build) is the #1 source of wasted runner compute and network I/O.

### The Two-Tier Strategy:
1. **One dedicated `install` job**:
   - Downloads/resolves dependencies using strict frozen lockfiles.
   - Writes the download store and the installed tree (`node_modules/`, `.venv/`) to a `cache:` keyed on the lockfile, with `policy: pull-push`.
   - Uploads **no artifacts**. Vendor folders (`node_modules/`, `.venv/`, `.nuget/`, `obj/`) are never artifacts.
2. **Downstream jobs (`lint`, `test`, `build`)**:
   - Depend directly on `needs: [install]`.
   - Pull the same cache with `policy: pull`, so they start with dependencies on disk and never write the cache.
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
      - node_modules/
    policy: pull-push                # Only the install job writes to the cache; it uploads no artifacts

test:
  stage: test
  needs:
    - install                        # Starts as soon as install has filled the cache
  cache:
    key:
      files:
        - package-lock.json
    paths:
      - .npm/
      - node_modules/
    policy: pull                     # Reads node_modules/ from the cache; never writes it
  script:
    - npx jest --maxWorkers=2
```

---

## 4. Cache vs. Artifacts: Technical Comparison

| Dimension | `cache` | `artifacts` |
| :--- | :--- | :--- |
| **Primary Purpose** | Carry dependencies to every job, across pipelines | Hand a later job or a person output it cannot rebuild |
| **Availability Guarantee** | **No**. Cold runners or cache evictions start empty | **Yes**. Guaranteed available to subsequent jobs in pipeline |
| **Storage Location** | Runner host disk or S3/MinIO bucket | GitLab server / object storage |
| **Ideal Payload** | Download stores and installed trees (`.npm`, `node_modules`, `.uv-cache`, `.venv`, Go mod cache) | The bundle a deploy ships, a report someone reads. Never vendor folders |
| **Lifespan** | Long-term (weeks/months) | `expire_in: 1 hour`, always |
| **Optimal Policy** | `policy: pull-push` on install, `policy: pull` downstream | Passed via `needs: [job_name]` |

Keep the project setting "Use separate caches for protected branches" on. With it off, a merge request pipeline can write a cache that a protected-branch pipeline later restores and runs.

Most jobs upload nothing. An artifact exists only when a later job or a person needs that exact output and cannot rebuild it from the cache, and it sets `expire_in: 1 hour`. Uploading `node_modules/` (about 37 MB a run) and `obj/` as artifacts filled one project's GitLab storage to 1.68 GB.

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

The cache has no availability guarantee. A consumer that misses it (a cold runner, an eviction, a runner that cannot see what `install` wrote) starts without dependencies and fails; retrying after `install` refills it. With more than one runner, give them a shared distributed cache (S3, GCS) so every runner sees what `install` wrote.

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
| **Go** | `go mod download` | `.go/pkg/mod/` | `$CI_PROJECT_DIR/.go/pkg/mod/` |
| **Rust (Cargo)** | `cargo fetch --locked` | `.cargo/` | `target/` |

---

## 6. Git Transfer Minimization

1. **Shallow Clone (`GIT_DEPTH`)**:
   Newly created projects default to a depth of 20 in their CI/CD settings; older projects, and any project where the setting was cleared, clone the whole history. Pinning the depth in the file makes it hold either way:
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
