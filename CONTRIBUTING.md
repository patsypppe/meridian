# Contributing to Meridian

Thanks for taking a look. This is a small project, so the process is short.

## Setup

Requires Python 3.12 or 3.13, Docker, and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/patsypppe/meridian.git
cd meridian
cp .env.example .env
make sync
make check
```

`make check` runs ruff, `mypy --strict`, and the unit tests. If it does not pass on a clean checkout,
that is a bug worth an issue on its own.

## Before you open a pull request

```bash
make fmt          # apply ruff formatting and autofixes
make check        # lint + typecheck + unit  (must pass)
make integration  # if you touched runtime/, snapshots/, or store/
make e2e          # if you touched gate/, manifest/, or the proxy
```

The pull request also runs the harness against itself via `gate.yml`. A failing gate is not automatically
a blocker, but it does need an explanation in the PR description.

## Standards

- **`mypy --strict` is not negotiable.** No new `# type: ignore` without a comment saying why.
- **New behavior needs a test at the cheapest tier that can actually catch it.** A pure-logic change
  belongs in `tests/unit`. Reach for `integration` or `e2e` only when the cheaper tier genuinely cannot
  observe the behavior, because slow tests get skipped and skipped tests protect nothing.
- **A check that cannot fail is not a check.** If you add an assertion about isolation, determinism, or
  reproducibility, add the case that violates it and assert that it is caught. The contamination probe is
  the pattern to follow.
- **Line length is 100.** Ruff enforces it.

## Changing the manifest

The run manifest is the reproducibility contract. If you change what goes into it or how it is
canonicalized, update `tests/fixtures/golden-manifest.json` and its hash file in the same commit, and say
in the PR description why the old manifests are no longer replayable.

## Commit messages

Present tense, and say what changed rather than which files moved. `gate: block on tolerance and
significance together` is useful. `update decide.py` is not.

## Reporting bugs

Please include the `meridian version` output, the run id if there is one, and whether the run was in
`record` or `replay` mode. If the harness itself errored rather than the agent under test failing, say so
explicitly: those are different problems and the harness error rate is tracked separately for that
reason.

## License

By contributing you agree that your contributions are licensed under the MIT License.
