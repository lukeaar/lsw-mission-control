# Working on lsw-mission-control

A live terminal dashboard (Python ≥ 3.11 + rich) that projects adopt through a small config and
optional plugins. How it is built is [docs/architecture.md](docs/architecture.md); the user docs are README.md
and the rest of docs/. These are the rules that always apply.

## The checkout IS the running dashboard

It is installed editable into `~/.venvs/lsw-mission-control`, and every adopter's open dashboard
reloads itself when a file here changes (once the change passes the gate). So:

- Anything beyond a one-line fix is developed in a **git worktree outside any synced folder**
  (e.g. the session scratchpad), tested there, then fast-forwarded into main. A half-finished edit
  in main reloads the owner's window (a broken one is refused, but a wrong one that passes is not).
- Tests import the tree they run in (`pythonpath = ["src"]`, and conftest asserts it), never the
  editable install; checks against an adopter run with `PYTHONPATH=src` for the same reason.

## Before every commit

```sh
~/.venvs/lsw-mission-control/bin/python -m pytest
~/.venvs/lsw-mission-control/bin/ruff check src tests
PYTHONPATH=src ~/.venvs/lsw-mission-control/bin/python -m lsw_mission_control --project <each adopter> --check-net --self-check
```

Run each command bare (a pipe hides its exit code). All three must pass.

## Rules

- **Golden files change only through `pytest --update-golden`**, and every changed line is
  reviewed (`git diff tests/golden`) before it is committed. A rich bump regenerates them: bump
  `constraints.txt` and the owner's venv together.
- **The network indicator must always be right.** A new kind of network work gets a label in
  `net.py`, a real `ps` line in `NET_CASES`, and a passing `--check-net`, before it is used. The
  phrase `network cases correct` is the reload gate's contract: never change it.
- **`statusline.py` and `templates/*.py` stay standard-library-only and Python 3.9-compatible**
  (macOS's `/usr/bin/python3` runs them before any venv exists). `tests/test_scripts_py39.py` runs
  them under 3.9.
- **Never write an adopter's caches from tests or rehearsals**: `LSW_MC_CACHE_DIR`,
  `LSW_MC_USAGE_DIR`, a throwaway `HOME` (conftest does this for every test).
- **This repository may be made public.** No adopter's names, hosts, repositories, paths or
  accounts, no secrets, in the tree or the history: fixtures are synthetic, process lines use
  placeholders (`hostname`, `example/project`, `/Users/x/`). `tests/test_no_project_specifics.py`
  checks the tree and the history against a denylist kept OUTSIDE the repo
  (`~/.config/lsw-mission-control/denylist`, or `LSW_MC_DENYLIST`); run `gitleaks dir .` and
  `gitleaks git` too before any push.
- **Configs hold no secrets**; authentication comes from ssh's and gh's own stores. `lsw-mc doctor`
  never prints a token or the environment.
- **Commits are authored with the owner's GitHub noreply address** (repo-local `user.email`), carry
  no `Co-Authored-By` trailer, and history is squashed to a clean commit before a first push.
- **Push main to GitHub as soon as a change lands on it** (owner, 2026-09-27): the public repository
  never lags the dashboard that is running. `gitleaks git` and `gitleaks dir .` run first, as above.
- **Keep bytecode and caches out of the checkout** (it may live in a synced folder): pytest runs
  with `-p no:cacheprovider`, ruff's cache is in `~/.cache/lsw-mission-control/ruff`, and the venv's
  `lsw_mc_pycache.pth` sets `sys.pycache_prefix`.
- **The plugin API (`plugin.__all__`) only grows.** A change an adopter would see goes in
  CHANGELOG.md.
- Behaviour changes that an owner would see (wording, colours, layout) are deliberate: say so in
  CHANGELOG.md and regenerate the goldens in the same commit.
