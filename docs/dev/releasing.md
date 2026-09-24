# Releasing

Releases go to PyPI from GitHub Actions through
[trusted publishing](https://docs.pypi.org/trusted-publishers/): PyPI trusts
the `Release` workflow of this repository, so no upload token exists
anywhere. The version comes from the Git tag through hatch-vcs; there is no
version string to edit.

## One-time setup

1. **PyPI trusted publisher.** Sign in at <https://pypi.org>, open *Your
   account → Publishing*, and add a *pending publisher* (the project is
   created by its first upload):
   - PyPI project name: `paperextract`
   - Owner: `jtravs`, repository: `paperextract`
   - Workflow name: `release.yml`
   - Environment name: `pypi`
2. **GitHub environment.** In the repository, *Settings → Environments →
   New environment*, named `pypi`. Optionally add yourself as a required
   reviewer, so every upload waits for your approval.
3. **Read the Docs.** Sign in at <https://readthedocs.org> with GitHub,
   choose *Add project*, select `jtravs/paperextract`, and keep the name
   `paperextract`. The build follows `.readthedocs.yaml`; each push to `main`
   rebuilds `latest`, and each tag builds a version.
4. **Codecov.** Sign in at <https://codecov.io> with GitHub and enable the
   repository. Uploads from the `Check` workflow work without a token for a
   public repository; adding the repository's upload token as the Actions
   secret `CODECOV_TOKEN` avoids rate limits.
5. **Optional: Zenodo DOI.** At <https://zenodo.org>, *GitHub* settings,
   switch the repository on; each GitHub Release then gets an archived,
   citable DOI. `CITATION.cff` supplies the metadata.

## Each release

1. Make sure `main` is green in the `Check` workflow.
2. In `CHANGELOG.md`, rename `[Unreleased]` to the version with its date,
   for example `## [0.1.0] - 2026-09-24`, and start a new empty
   `[Unreleased]` section. Commit and push.
3. On GitHub, *Releases → Draft a new release*, create the tag `v0.1.0` on
   `main`, paste the changelog section as the notes, and publish.
4. The `Release` workflow builds the source archive and wheel, checks that
   they carry the tag's version, installs the wheel and runs the command
   outside the checkout, then uploads to PyPI. Watch it under *Actions*.
5. Check <https://pypi.org/project/paperextract/> and the versioned manual
   on Read the Docs.

A tag such as `v0.1.0rc1` publishes a pre-release, which pip installs only
when asked with `--pre`.

## What a PyPI install provides

`pip install paperextract` installs the core command and library: search,
lookup, library maintenance, migration and figure descriptions against a
server work from it. Extraction needs the backend worker environments and
the model manifest, which live in a source checkout; see
[getting started](../usage.md).
