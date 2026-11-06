# Contributing to Sprocket

Thanks for helping. This guide covers how to set up, how the code is written, and
how to name branches, write commits and open a pull request. If something here is
out of date, fix it in the same PR.

## Contents

- [Setting up](#setting-up)
- [Running without a scanner](#running-without-a-scanner)
- [Tests and checks](#tests-and-checks)
- [Code style](#code-style)
- [Branches](#branches)
- [Commits](#commits)
- [Pull requests](#pull-requests)
- [Issues](#issues)

## Setting up

You need Linux, Python 3.10 or newer and Node 18 or newer (Node is only used for the
browser-code tests). SANE's `scanimage` is only needed for a real scanner.

```sh
git clone https://github.com/C0wRat/sprocket.git
cd sprocket
./run.sh --help                              # creates .venv on the first run
.venv/bin/pip install -r requirements-dev.txt
```

`README.md` explains how the app is laid out (the **Development** section lists what
each file does).

## Running without a scanner

The tests use a stand-in for `scanimage` in `tests/fake_scanimage.py`. You can run the
whole app against it to work on the UI:

```sh
cat > /tmp/fake-scanimage <<EOF
#!/bin/sh
exec "$PWD/.venv/bin/python" "$PWD/tests/fake_scanimage.py" "\$@"
EOF
chmod +x /tmp/fake-scanimage
./run.sh --device fake --scanimage /tmp/fake-scanimage --output /tmp/sprocket-scans
```

It produces a small synthetic frame at 100 or 200 dpi. `FAKE_SCANIMAGE_DELAY=0.05` makes
it slow enough to watch the progress bar, and `FAKE_SCANIMAGE_MODE=busy` or `stall`
lets you try the error paths (see the top of `fake_scanimage.py`).

## Tests and checks

Run all of these before you push. A PR should pass every one. GitHub Actions
(`.github/workflows/ci.yml`) runs the same checks on every pull request and push to
`main`, on Python 3.10 and the newest Python. Only merge a PR once they pass.
The Docker image is built on every PR too (`.github/workflows/docker.yml`); it's only
published from `main` (as `latest`) and from tags.
Another check makes sure the PR title is a Conventional Commit (see
[Pull requests](#pull-requests)).

```sh
.venv/bin/ruff check .                  # lint
.venv/bin/ruff format --check .         # formatting (run without --check to fix)
.venv/bin/python -m pytest              # Python tests, use the fake scanimage
node --test tests/*.test.mjs            # crop maths and histogram in the browser code
```

Add tests with your change:

- Python behaviour goes in `tests/test_*.py`. The `fake_scanimage` fixture
  (`tests/conftest.py`) and the `client` fixture (`tests/test_app.py`) let you drive
  the HTTP API end to end.
- Browser logic that isn't tied to the DOM goes in its own module (like
  `geometry.js` and `histogram.js`) so it can be tested in `tests/*.test.mjs`.
- `sprocket/imaging.py` and `sprocket/static/geometry.js` must agree on how rotation,
  mirroring and crops map onto each other. If you change one, change the other and
  test both.

## Code style

### Python

- Formatted and linted by **ruff** (config in `pyproject.toml`): 120-character lines,
  double quotes, sorted imports. Don't argue with the formatter; run it.
- Target Python 3.10: use `str | None`, `list[int]` and so on, not `Optional`/`List`.
- Type hints on every function signature. Use dataclasses or pydantic models for
  structured data, not loose dicts.
- Module and function docstrings say *what* and *why*, in a sentence or two. Comments
  explain things the code can't, such as scanner quirks (the README's
  troubleshooting notes are a good example of the detail worth keeping).
- Error messages that reach the user are written for someone scanning film, not for a
  programmer: say what happened and what to do ("Check the USB cable, or unplug the
  scanner and plug it back in.").

### JavaScript, HTML and CSS

- **No build step and no framework.** The page is plain ES modules served from
  `sprocket/static/`. Don't add a bundler, npm dependencies or a framework without
  discussing it in an issue first.
- Two-space indent, single quotes, semicolons, `const` by default, arrow functions for
  small helpers. Match the code around you.
- Keep DOM-free logic in small modules that can be unit tested (see above).
- **Colours live in CSS custom properties** in `:root` at the top of `style.css`.
  Never hard-code a colour in a rule or in JavaScript; themes depend on this.
- IDs and class names are `kebab-case`.

### User interface

- Everything must work from the keyboard. Shortcuts go in the single `keydown` handler
  in `app.js`, must not fire while typing in a text field, and must be listed in the
  button's `title` and in `README.md`.
- Toggle buttons use `aria-pressed`, and it must stay correct however the state
  changes (button, key or loading saved settings). Route every change through one
  function.
- UI text is short and plain, in British English (*colour*, *grey*), matching the
  README.

### All files

`.editorconfig` sets UTF-8, LF line endings, a final newline, no trailing whitespace,
two-space indent (four for Python). Most editors pick it up automatically.

## Branches

Branch from an up-to-date `main` and follow
[Conventional Branch](https://conventional-branch.github.io/):

```
<type>/<issue>-<short-description>
```

- `type` is one of the commit types below (`feat`, `fix`, `docs`, …).
- `issue` is the GitHub issue number. Every branch should have an issue; open one
  first if needed.
- `short-description` is a few lower-case words joined by hyphens.

Examples:

```
feat/1-preview-zoom
fix/7-mirror-after-rotate
docs/14-contributing-guide
```

## Commits

Commits follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):

```
<type>(<scope>): <summary>

<body: what changed and why, wrapped at about 72 characters>

<footer: Closes #N, BREAKING CHANGE: …>
```

- **summary**: imperative, lower case, no full stop, under about 72 characters.
  Describe the effect, not the code: `fix(api): reject crops that aren't finite numbers`.
- **type**:

  | Type | Use for |
  | --- | --- |
  | `feat` | something new a user can see or use |
  | `fix` | a bug fix |
  | `refactor` | code change that doesn't change behaviour |
  | `perf` | faster or lighter, same behaviour |
  | `test` | adding or fixing tests only |
  | `docs` | README, this guide, docstrings |
  | `style` | formatting only (e.g. running ruff) |
  | `build` | dependencies, tooling config, `run.sh` |
  | `ci` | GitHub Actions workflows |
  | `chore` | anything else that doesn't touch the app |

- **scope** (optional but encouraged) is the part of Sprocket the change touches:

  | Scope | Covers |
  | --- | --- |
  | `ui` | `sprocket/static/` |
  | `api` | `sprocket/app.py`, request validation |
  | `scanner` | `sprocket/scanner.py`, talking to SANE |
  | `imaging` | `sprocket/imaging.py`, conversion and colour |
  | `service` | `sprocket/service.py`, jobs |
  | `library` | `sprocket/library.py`, the recent-scans list |
  | `output` | file names, formats and metadata of saved scans |
  | `deps` | `requirements*.txt` |
  | `docker` | `Dockerfile`, `.dockerignore`, the published image |
  | `readme` / `contributing` | those documents |

- **Breaking changes** are anything that invalidates what users already have: saved
  settings, the layout of the output folder, command-line options. Mark them with `!`
  (`feat(output)!: …`) and a `BREAKING CHANGE:` footer explaining what to do.
- Keep each commit to one logical change, with tests passing. Several small commits in
  a PR are fine.
- Reference the issue in the footer: `Refs #7` on intermediate commits, `Closes #7` on
  the one that finishes it (or in the PR description).

## Pull requests

- One issue per PR. Link it with `Closes #N` in the description.
- The PR title is a Conventional Commit summary, e.g.
  `fix(imaging): mirror left-right after rotating`. PRs are **squash-merged**, so the
  title becomes the commit on `main`.
- Fill in the template: what changed, why, how you tested it, and screenshots for
  anything visible.
- All checks above pass, and new behaviour has tests.
- Update `README.md` when you change keys, options, what gets saved or how to run it.
- Keep PRs small enough to review in one sitting. Split large work into a chain of
  PRs if needed.

## Issues

Use the issue templates. A good issue for someone else to pick up has:

- **Idea / Problem**: what and why, from the user's point of view.
- **Possible approach**: a suggestion, not a spec.
- **Files likely involved**: where to start reading.
- **Done when**: a short checklist.

For scanner bugs, include the scanner model, `scanimage -V`, and the log from
`./run.sh --debug`.
