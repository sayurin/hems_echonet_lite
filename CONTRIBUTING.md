# Contributing

Use English Conventional Commit subjects in PR titles and non-merge commits.
Allowed types: `feat`, `fix`, `perf`, `deps`, `revert`, `docs`, `style`, `chore`,
`refactor`, `test`, `build`, and `ci`. For example, `fix(climate): handle a
timeout`. Squash-merge PRs using their title as the resulting commit subject.
Do not use `!` solely for compatibility with the next Home Assistant release.

## Development

From this checkout, sync a dedicated uv environment, not the Core environment:

```shell
env -u VIRTUAL_ENV UV_PROJECT_ENVIRONMENT=.venv uv sync --locked --extra dev
env -u VIRTUAL_ENV UV_PROJECT_ENVIRONMENT=.venv uv run --locked --extra dev pytest
env -u VIRTUAL_ENV UV_PROJECT_ENVIRONMENT=.venv uv run --locked --extra dev prek run --all-files
```

Tests use the Home Assistant version resolved in `uv.lock`; the minimum
supported version is not separately tested. The offline tests do not start
Home Assistant or load this integration. Ruff lint and mypy
start with the new tooling; Ruff also lints the tests. Use `gh actions-lock`
v0.4.1 to regenerate the workflow lockfile after changing action references.

## Releases

Install the release GitHub App on this repository with Contents, Pull requests
and Issues read/write access. Set its Client ID as the `APP_CLIENT_ID` Actions
variable and its private key as the `APP_PRIVATE_KEY` Actions secret.

Only merge the release PR when the pinned pyhems version is available on PyPI
and the integration prerequisites are verified. Release Please publishes a
public pre-release with the final version tag. The committed integration
manifest keeps version `0.0.0`; packaging inserts the verified tag version into
the release ZIP without changing the source. Install the ZIP rather than the
tag's source archive to obtain the release version. Wait for the ZIP checks and
upload to finish. Test that exact ZIP on a separate Home Assistant installation
with pre-releases enabled; do not run the HACS integration in WSL. Only a
maintainer should clear the pre-release flag and set the same release as latest
after verification. Do not move the tag or replace the tested ZIP.

To retry a failed ZIP upload, run the Release workflow on `master` with the
existing pre-release tag. An existing ZIP must match the rebuilt file; the
workflow never overwrites it. Do not run recovery while promoting a release.
Release Please marks only `0.x` versions (or versions with a pre-release
suffix) as pre-releases with the current configuration. Do not merge a release
PR for `1.0.0` or later until that publication path is updated.
