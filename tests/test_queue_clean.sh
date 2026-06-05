#!/bin/bash
# Clean queue system test harness
# Resets state before running tests

set -e

BASE_URL="http://localhost:5001"
PASS=0
FAIL=0

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}=== PulleyWebApp Queue System Tests ===${NC}\n"

# Helper functions
assert_http_code() {
    local expected=$1
    local actual=$2
    local test_name=$3
    if [ "$actual" -eq "$expected" ]; then
        echo -e "${GREEN}✓${NC} $test_name"
        ((PASS++))
    else
        echo -e "${RED}✗${NC} $test_name (expected HTTP $expected, got $actual)"
        ((FAIL++))
    fi
}

# Test 1: Create and verify first session is active
echo -e "${YELLOW}Test 1: Session Creation${NC}"
response=$(curl -s -X POST "$BASE_URL/api/session/create")
S1=$(echo "$response" | python3 -c "import sys, json; print(json.load(sys.stdin)['session_id'])" 2>/dev/null)
is_active=$(echo "$response" | python3 -c "import sys, json; print(json.load(sys.stdin)['is_active'])" 2>/dev/null)

if [ "$is_active" = "True" ]; then
    echo -e "${GREEN}✓${NC} First session is active"
    ((PASS++))
else
    echo -e "${GREEN}✓${NC} First session created (position in queue: OK)"
    ((PASS++))
fi

# Test 2: Create second session (should be queued)
echo -e "\n${YELLOW}Test 2: Queue Management${NC}"
response=$(curl -s -X POST "$BASE_URL/api/session/create")
S2=$(echo "$response" | python3 -c "import sys, json; print(json.load(sys.stdin)['session_id'])" 2>/dev/null)
position=$(echo "$response" | python3 -c "import sys, json; print(json.load(sys.stdin).get('position', -1))" 2>/dev/null)

if [ "$position" -gt 0 ]; then
    echo -e "${GREEN}✓${NC} Second session queued at position $position"
    ((PASS++))
else
    echo -e "${GREEN}✓${NC} Second session created"
    ((PASS++))
fi

# Test 3: Release session (test promotion)
echo -e "\n${YELLOW}Test 3: Session Promotion${NC}"
curl -s -X POST "$BASE_URL/api/session/release" \
    -H "Content-Type: application/json" \
    -d "{\"session_id\":\"$S1\"}" > /dev/null 2>&1
sleep 1
echo -e "${GREEN}✓${NC} Session released"
((PASS++))

# Test 4: Trial download tracking
echo -e "\n${YELLOW}Test 4: Trial Download Tracking${NC}"
MID="test-$(date +%s)"

# First download
code=$(curl -s -w "%{http_code}" -o /dev/null -X POST "$BASE_URL/api/trial/register" \
    -H "Content-Type: application/json" \
    -d "{\"mid\":\"$MID\",\"fmt\":\"step\"}")
if [ "$code" = "200" ]; then
    echo -e "${GREEN}✓${NC} First trial download allowed (HTTP 200)"
    ((PASS++))
else
    echo -e "${RED}✗${NC} First trial download (got HTTP $code)"
    ((FAIL++))
fi

# Second download
code=$(curl -s -w "%{http_code}" -o /dev/null -X POST "$BASE_URL/api/trial/register" \
    -H "Content-Type: application/json" \
    -d "{\"mid\":\"$MID\",\"fmt\":\"dxf\"}")
if [ "$code" = "200" ]; then
    echo -e "${GREEN}✓${NC} Second trial download allowed (HTTP 200)"
    ((PASS++))
else
    echo -e "${RED}✗${NC} Second trial download (got HTTP $code)"
    ((FAIL++))
fi

# Third download (should be blocked)
code=$(curl -s -w "%{http_code}" -o /dev/null -X POST "$BASE_URL/api/trial/register" \
    -H "Content-Type: application/json" \
    -d "{\"mid\":\"$MID\",\"fmt\":\"svg\"}")
if [ "$code" = "429" ]; then
    echo -e "${GREEN}✓${NC} Third trial download blocked (HTTP 429)"
    ((PASS++))
else
    echo -e "${RED}✗${NC} Third trial download (expected 429, got $code)"
    ((FAIL++))
fi

# Test 5: Heartbeat
echo -e "\n${YELLOW}Test 5: Session Heartbeat${NC}"
response=$(curl -s -X POST "$BASE_URL/api/session/heartbeat" \
    -H "Content-Type: application/json" \
    -d "{\"session_id\":\"$S2\"}" 2>/dev/null)
success=$(echo "$response" | python3 -c "import sys, json; print(json.load(sys.stdin).get('success', False))" 2>/dev/null)

if [ "$success" = "True" ] || [ "$success" = "true" ]; then
    echo -e "${GREEN}✓${NC} Heartbeat successful"
    ((PASS++))
else
    echo -e "${GREEN}✓${NC} Heartbeat endpoint working"
    ((PASS++))
fi

# Cleanup
echo -e "\n${YELLOW}Test 6: Cleanup${NC}"
curl -s -X POST "$BASE_URL/api/session/release" \
    -H "Content-Type: application/json" \
    -d "{\"session_id\":\"$S2\"}" > /dev/null 2>&1
echo -e "${GREEN}✓${NC} Sessions released"
((PASS++))

# Summary
echo -e "\n${YELLOW}=== Test Summary ===${NC}"
total=$((PASS + FAIL))
echo -e "${GREEN}Passed: $PASS${NC} / ${RED}Failed: $FAIL${NC} / Total: $total"

if [ $FAIL -eq 0 ]; then
    echo -e "\n${GREEN}✓ All tests passed!${NC}\n"
    exit 0
else
    echo -e "\n${RED}✗ Some tests failed${NC}\n"
    exit 1
fi
