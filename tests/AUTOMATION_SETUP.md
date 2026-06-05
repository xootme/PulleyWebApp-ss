# Test Automation Setup Guide

Complete guide to automating queue system tests locally and in CI/CD.

## Quick Start

### 1. Local Pre-Commit Hooks (Recommended)
Automatically run tests before committing:

```bash
# Install pre-commit
pip install pre-commit

# Setup hooks
pre-commit install

# Run hooks on all files
pre-commit run --all-files
```

After setup, tests run automatically when you `git commit`.

### 2. Manual Test Running

**Quick tests:**
```bash
# All tests using Makefile
make -f Makefile.test test

# Just queue tests
make -f Makefile.test test-queue

# Code quality checks
make -f Makefile.test test-quality
```

**Individual test suites:**
```bash
# Bash integration tests
bash tests/test_queue.sh

# Pytest suite
pytest tests/test_queue_pytest.py -v

# Code formatting
black --check app.py exporters/

# Linting
flake8 app.py exporters/
```

### 3. CI/CD Pipeline (GitHub Actions)

The `.github/workflows/test-queue.yml` workflow runs automatically:

- **On push** to main/develop branches
- **On pull requests** to main/develop
- **Daily at 2 AM UTC** (nightly builds)
- Tests run on Python 3.11 and 3.12

View results: Go to your GitHub repo → Actions tab

---

## Setup Instructions

### Local Development

#### Step 1: Install Python Tools
```bash
pip install -r requirements.txt
pip install pytest requests black flake8 mypy pre-commit
```

#### Step 2: Start Flask
```bash
# Terminal 1
.venv312/Scripts/python.exe app.py
```

#### Step 3: Configure Pre-Commit
```bash
# Terminal 2
pre-commit install
pre-commit run --all-files  # Run once to verify setup
```

#### Step 4: Make a Commit
```bash
git add .
git commit -m "test: add queue tests"
# Pre-commit hooks will run automatically
```

---

## Makefile Targets

```bash
make -f Makefile.test              # Show help
make -f Makefile.test test         # All tests
make -f Makefile.test test-queue   # Queue tests only
make -f Makefile.test test-bash    # Bash integration
make -f Makefile.test test-pytest  # Pytest suite
make -f Makefile.test test-quality # Code quality
make -f Makefile.test setup-hooks  # Setup pre-commit
make -f Makefile.test run-hooks    # Run pre-commit manually
make -f Makefile.test watch        # Watch & test (requires entr)
make -f Makefile.test clean        # Clean artifacts
```

---

## CI/CD Pipeline Details

### GitHub Actions Workflow (`.github/workflows/test-queue.yml`)

**Triggers:**
- Push to `main` or `develop`
- Pull request to `main` or `develop`  
- Daily at 02:00 UTC

**Steps:**
1. Checkout code
2. Set up Python (3.11 & 3.12)
3. Install dependencies
4. Wait for Flask to start
5. Run pytest suite
6. Run bash integration tests
7. Upload test results

**Notifications:**
- ✅ Pass: Green checkmark on GitHub
- ❌ Fail: Red X, with details available

**View Results:**
```
GitHub → Your Repo → Actions → test-queue.yml
  ↓
  Click on a run
  ↓
  See logs & artifacts
```

---

## Pre-Commit Hooks (`.pre-commit-config.yaml`)

Hooks that run **automatically before each commit**:

| Hook | Purpose | Runs On |
|------|---------|---------|
| `black` | Code formatting | `.py` files |
| `flake8` | Linting | `.py` files |
| `mypy` | Type checking | `.py` files |
| `trailing-whitespace` | Remove trailing spaces | All files |
| `end-of-file-fixer` | Fix file endings | All files |
| `check-json` | Validate JSON | `.json` files |
| `queue-tests` | Run queue tests | On commit |

**Skip hooks (not recommended):**
```bash
git commit --no-verify
```

---

## Test Report

After running tests, check `test_report.txt`:

```bash
cat test_report.txt
```

Contains:
- Test names and commands
- Pass/fail results
- Error details if any failures
- Summary line

---

## Continuous Integration Tips

### 1. Fast Feedback Loop
```bash
# Run tests on file changes
make -f Makefile.test watch
```

### 2. Pre-Push Check
```bash
# Before pushing, run all tests locally
make -f Makefile.test test
```

### 3. Monitor CI/CD
- Set GitHub branch protection: Require CI status checks to pass
- PR won't merge until all tests pass

### 4. Debug CI Failures
```bash
# Replicate CI environment locally
pytest tests/test_queue_pytest.py -v --tb=long

# Check Flask is accessible
curl http://localhost:5001
```

---

## Troubleshooting

### Pre-Commit Hook Failures

**Tests fail on commit:**
```bash
# Check what hooks are configured
pre-commit run --all-files --verbose

# Run just queue tests
bash tests/test_queue.sh

# Fix issues, then try commit again
git add .
git commit -m "fix: queue test"
```

**Skip problematic hook temporarily:**
```bash
# List hooks
pre-commit validate-manifest

# Edit .pre-commit-config.yaml to disable one
# Then re-run
pre-commit run --all-files
```

### CI/CD Failures

**Check GitHub Actions logs:**
1. Go to GitHub repo
2. Actions → test-queue → Latest run
3. Click failing job
4. Read error in logs

**Common issues:**
- Flask not starting → Check port 5001 is available
- Dependencies missing → Verify requirements.txt
- Python version → Use 3.12 (default in tests)

### Local Test Failures

**Flask not running:**
```bash
curl http://localhost:5001
# If fails, start Flask:
.venv312/Scripts/python.exe app.py &
sleep 3
pytest tests/test_queue_pytest.py
```

**Missing dependencies:**
```bash
pip install -r requirements.txt
pip install pytest requests
```

**Stale data:**
```bash
# Clean and retry
make -f Makefile.test clean
make -f Makefile.test test
```

---

## Integration with Your Workflow

### Recommended Process

```
1. Make code changes
   ↓
2. Run pre-commit checks (auto on git commit)
   ↓
3. Tests fail? → Fix and re-commit
   ↓
4. Push to GitHub
   ↓
5. GitHub Actions runs full CI/CD
   ↓
6. All green? → Safe to merge
```

### GitHub Branch Protection

Set in repo settings:
```
Settings → Branches → main → Branch protection rules
✓ Require status checks to pass before merging
✓ Require branches to be up to date
✓ Require code reviews
```

This ensures no code lands on main without passing tests.

---

## Advanced: Custom CI Triggers

To run tests on specific branches or schedules:

Edit `.github/workflows/test-queue.yml`:

```yaml
on:
  push:
    branches: [ main, develop, release/* ]  # Add patterns
  pull_request:
    branches: [ main ]
  schedule:
    - cron: '0 */6 * * *'  # Run every 6 hours
    - cron: '0 0 * * 0'    # Weekly on Sunday
```

---

## Summary

| Tool | Purpose | When |
|------|---------|------|
| Pre-commit | Instant feedback | Before commit |
| Makefile | Manual testing | During development |
| GitHub Actions | Full CI/CD | On push/PR |
| Test Report | Documentation | After any test run |

**Get started:**
```bash
pre-commit install
make -f Makefile.test test
```
