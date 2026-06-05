#!/bin/bash
# Complete test runner for PulleyWebApp
# Runs all test suites and generates a report
# Usage: bash tests/run_all_tests.sh

set -e

REPORT_FILE="test_report.txt"
FAILURES=0
SUCCESSES=0

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║          PulleyWebApp Test Suite                           ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}\n"

# Clear report
> "$REPORT_FILE"

# Function to run a test and report
run_test() {
    local test_name=$1
    local command=$2

    echo -e "\n${YELLOW}▶ Running: $test_name${NC}"
    echo "Running: $test_name" >> "$REPORT_FILE"
    echo "Command: $command" >> "$REPORT_FILE"

    if eval "$command" >> "$REPORT_FILE" 2>&1; then
        echo -e "${GREEN}✓ PASSED: $test_name${NC}"
        echo "Result: PASSED" >> "$REPORT_FILE"
        ((SUCCESSES++))
    else
        echo -e "${RED}✗ FAILED: $test_name${NC}"
        echo "Result: FAILED" >> "$REPORT_FILE"
        ((FAILURES++))
    fi
    echo "" >> "$REPORT_FILE"
}

# Check if Flask is running
echo -e "\n${YELLOW}Checking Flask server...${NC}"
if curl -s http://localhost:5001 > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Flask is running on http://localhost:5001${NC}"
else
    echo -e "${RED}✗ Flask is NOT running on http://localhost:5001${NC}"
    echo -e "${YELLOW}Start Flask with: .venv312/Scripts/python.exe app.py${NC}"
    exit 1
fi

# Test 1: Bash Integration Tests
run_test "Bash Queue Integration Tests" "bash tests/test_queue.sh"

# Test 2: Pytest Suite
echo -e "\n${YELLOW}Checking pytest...${NC}"
if python -m pip show pytest > /dev/null 2>&1; then
    run_test "Pytest Queue Tests" "pytest tests/test_queue_pytest.py -v --tb=short"
else
    echo -e "${YELLOW}⚠ pytest not installed, skipping pytest suite${NC}"
    echo "Run: pip install pytest requests" >> "$REPORT_FILE"
fi

# Test 3: Code Quality (optional)
echo -e "\n${YELLOW}Checking code quality tools...${NC}"
if python -m pip show black > /dev/null 2>&1; then
    run_test "Black Code Formatting Check" "black --check app.py exporters/"
else
    echo -e "${YELLOW}⚠ black not installed, skipping formatting check${NC}"
fi

if python -m pip show flake8 > /dev/null 2>&1; then
    run_test "Flake8 Linting" "flake8 app.py exporters/ --max-line-length=100 --ignore=E203,W503"
else
    echo -e "${YELLOW}⚠ flake8 not installed, skipping linting${NC}"
fi

# Generate Report
echo -e "\n${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║                      TEST SUMMARY                           ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}\n"

total=$((SUCCESSES + FAILURES))
echo -e "Total Tests:    $total"
echo -e "${GREEN}Passed:         $SUCCESSES${NC}"
echo -e "${RED}Failed:         $FAILURES${NC}"

if [ $FAILURES -eq 0 ]; then
    echo -e "\n${GREEN}✓ All tests passed!${NC}"
    echo "" >> "$REPORT_FILE"
    echo "Summary: All tests PASSED" >> "$REPORT_FILE"
    exit 0
else
    echo -e "\n${RED}✗ Some tests failed${NC}"
    echo "" >> "$REPORT_FILE"
    echo "Summary: $FAILURES test(s) FAILED" >> "$REPORT_FILE"
    exit 1
fi
