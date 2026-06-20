# Releasing cc-copilot

Releases publish to **PyPI** automatically via **Trusted Publishing (OIDC)** —
there are **no API tokens** stored in the repo. Pushing a `v*` tag runs
`.github/workflows/release.yml`, which builds the sdist + wheel, validates them
(`twine check`, tag-vs-version match, install smoke test), and publishes.

## One-time PyPI setup (do this once, before the first release)

PyPI supports a **pending publisher**, so you can configure trust *before* the
project exists — the first tag push then creates and publishes it.

1. Create an account at <https://pypi.org> (and verify your email).
2. Go to <https://pypi.org/manage/account/publishing/> → **"Add a pending
   publisher"**.
3. Fill in exactly:
   - **PyPI Project Name:** `cc-copilot`
   - **Owner:** `Audiofool934`
   - **Repository name:** `cc-copilot`
   - **Workflow name:** `release.yml`
   - **Environment name:** *(leave blank)*
4. Save.

That's it — no secrets in GitHub.

> **Order matters:** the pending publisher must be listed at
> <https://pypi.org/manage/account/publishing/> **before** you push the first
> `v*` tag. If you tag first, the publish step fails with an
> *"untrusted publisher / not configured"* error and you have to re-run it after
> adding the publisher. Verify it's listed, then tag.

### Optional hardening (recommended once, but not required for the first publish)

Gate the publish behind a GitHub Environment so it can require manual approval:
1. Repo → Settings → Environments → create one named `pypi` (add a required
   reviewer and/or a tag-only deployment rule).
2. Add `environment: pypi` to the `publish` job in `release.yml`.
3. Re-create the PyPI publisher with **Environment name:** `pypi` (this is why
   it's easier to decide up front — changing it later means deleting and
   re-adding the publisher).

Also: the publish action is pinned to `pypa/gh-action-pypi-publish@release/v1`
(PyPA's maintained moving ref). To pin it to an immutable tag/SHA for stricter
supply-chain control, swap that ref and bump it manually on each action release.

## Cutting a release

Make sure the `tests` workflow is green on `main` first — the publish workflow
builds and validates but does **not** run the test matrix.

1. Bump the version in **one place** — `cccopilot/__init__.py` (`__version__`).
   `pyproject.toml` reads it dynamically, so there's nothing else to edit. The
   workflow fails if the tag doesn't match the built wheel's version.
2. Add a `CHANGELOG.md` entry.
3. Sync project docs before the release commit:
   - update `README.md` for any new or changed user-facing behavior;
   - update relevant files in `docs/` for design, architecture, or workflow
     changes;
   - remove stale descriptions of old behavior, especially for TUI flows,
     slash commands, release/install instructions, and safety/tool-access
     guarantees.
4. Commit, then tag and push:
   ```bash
   git tag -a v0.13.2 -F /tmp/notes.md
   git push origin v0.13.2
   ```
5. The `publish` workflow builds, validates, and pushes to PyPI. Within a minute
   `uv tool install "cc-copilot[tui]"` installs the new version.
6. (Optional) create the GitHub Release: `gh release create v0.13.2 -F /tmp/notes.md`.

## Verifying a build locally (no publish)

```bash
uv build                 # writes dist/*.whl and dist/*.tar.gz
uvx twine check dist/*   # PyPI metadata + README render
```
