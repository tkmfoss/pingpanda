# Contributing to PingPanda

Thanks for helping out! PingPanda is a small command line tool that checks
whether URLs are alive, and every contribution - a bug fix, a test, a
documentation improvement or a new feature - is welcome.

PingPanda started as [Status Checker](https://github.com/BLACK-SCORP10/url-status-checker)
by BLACK-SCORP10 and was enhanced by matteocapricci. The original authors are
credited in the tool banner and in the `LICENSE` file, please keep them there.

## 1. What you need

- **Python 3.10 or newer** (3.13 is used most often, the test suite also runs
  on 3.10, 3.11 and 3.12).
- **Git** to get the code and to send your change back.
- Nothing else. PingPanda is a command line program, there is no database and
  no web server involved.

## 2. Set up the project

```bash
git clone https://github.com/<your-user>/PingPanda.git
cd PingPanda
python -m venv .venv

# Linux and macOS
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1

pip install -r requirements-dev.txt
```

`requirements.txt` holds the runtime dependencies (httpx, tqdm, colorama),
`requirements-dev.txt` adds pytest for the test suite.

## 3. Run the tool

```bash
python url-status-checker.py -d https://example.com
python url-status-checker.py -l urls.txt -o results.txt
python url-status-checker.py --help
```

| Option | Meaning |
| --- | --- |
| `-d`, `--domain` | Check a single URL or domain |
| `-l`, `--list` | Check every URL of a text file, one URL per line |
| `-o`, `--output` | Save the report to a file |
| `-v`, `--version` | Show the version |
| `-update` | Check for a newer release |
| `--quiet` | Suppress the startup banner and progress bar |
| `--no-colour` | Disable coloured terminal output |

These options can also be combined:

```bash
# Suppress the startup banner and progress bar
python url-status-checker.py -l urls.txt --quiet

# Disable coloured terminal output
python url-status-checker.py -l urls.txt --no-colour

# Use both options together
python url-status-checker.py -l urls.txt --quiet --no-colour

## 4. Run the tests

```bash
python -m pytest            # the whole suite
python -m pytest -v         # one test per line
python -m pytest tests/test_cli.py
```

The tests never talk to the internet. HTTP answers are faked with
`httpx.MockTransport`, and the few tests that need a real socket talk to a
throw away HTTP server on `localhost`.

Every bug fix comes with a test that fails without the fix. If you find a bug
that has no test yet, add one, so that the test suite describes what the tool
is supposed to do.

## 5. Pick an issue

Every issue of the FOSSSoC-S1 issue tracker has these labels:

| Label | Meaning |
| --- | --- |
| `soc-s1` | The issue belongs to the FOSSSoC-S1 event |
| `level:easy` | A small change in one place, 10 points |
| `level:medium` | Several functions or edge cases, 25 points |
| `level:hard` | Concurrency, state, aggregation or the whole build, 50 points |
| `type:bug` | Something behaves differently than it should |
| `type:feature` | Something new |
| `type:docs` | Documentation |
| `type:test` | Tests |
| `type:refactor` | Code that works but should be cleaner |
| `type:devex` | Tooling, packaging, developer experience |
| `type:performance` | Speed, memory, concurrency |
| `good first issue` | Small and well described, a good place to start |

Every issue needs exactly one `level:` label and exactly one `type:` label.
The points are the reward for the merged pull request:

- **easy - 10 points**: a small, local fix with a clear reproduction, roughly
  30 minutes to 2 hours of work.
- **medium - 25 points**: you have to debug, touch more than one function or
  think about edge cases, a few hours of work.
- **hard - 50 points**: you have to reason about concurrency, shared state,
  aggregation or resources, or you have to change several parts of the tool.

One issue is one pull request. Small pull requests are reviewed faster and
give you more points, because the reviewer can check every line.

## 6. Make your change

```bash
git checkout -b feature/my-short-branch-name
# ... change the code ...
git add <the files you changed>
git commit -m "Fix the port that is shown for plain HTTP URLs"
git push -u origin feature/my-short-branch-name
```

Commit messages start with a capital letter and describe what you did, for
example `Add a JSON report to the output file`.

## 7. Open a pull request

1. Push your branch.
2. Open a pull request on GitHub from your branch to `main`.
3. Fill in the pull request template: what you changed, why, and how you
   tested it.
4. Link the issue, for example `Closes #12`, so the issue is closed when the
   pull request is merged.

A reviewer will look at your change and may ask for small adjustments. That is
normal, just push another commit to the same branch.

## 8. Code style

- 4 spaces for indentation, no tabs.
- Small functions with a clear name and a short docstring.
- `black` style formatting, 88 characters per line.
- Every bug fix comes with a test that fails without the fix.

## 9. Reporting a bug

Open an issue with the **Bug report** template. Tell us what you expected,
what happened and how somebody else can see it. Please do not include a fix
in the issue, the reviewers will help you find the right place in the code.

## 10. Code of conduct

Please read the [Code of Conduct](CODE_OF_CONDUCT.md). By taking part in this
project you agree to follow it.
