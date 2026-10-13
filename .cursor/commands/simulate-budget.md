# /simulate-budget

Estimate GitLab CI runner memory, wall-clock time, and concurrency fit.

1. Read `.gitlab-ci.yml` for the stack, the worker flag (`--maxWorkers`, `pytest -n`, `go test -p`) and `parallel:`. Use the runner's RAM, vCPUs and slots if known; otherwise keep the defaults and say so.
2. Run `python3 .agents/skills/gitlab-ci-optimizer/scripts/simulate_budget.py` with those values (`--help` lists the flags).
3. Report the memory status and each recommendation. The figures are estimates from illustrative constants, not measurements of this project.
