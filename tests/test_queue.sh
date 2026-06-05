#!/bin/bash
# Queue system test harness for PulleyWebApp
# Tests session management, promotion, trial limits, and cleanup

set -e

BASE_URL="http://localhost:5001"
PASS=0
FAIL=0

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Helper functions
assert_http_code() {
    local expected=$1
    local actual=$2
    local test_name=$3

    if [ "$actual" -eq "$expected" ]; then
        echo -e "${GREEN}✓ PASS${NC}: $test_name (HTTP $actual)"
        ((PASS++))
    else
        echo -e "${RED}✗ FAIL${NC}: $test_name (expected $expected, got $actual)"
        ((FAIL++))
    fi
}

assert_json_field() {
    local json=$1
    local field=$2
    local expected=$3
    local test_name=$4

    local actual=$(echo "$json" | python3 -c "import sys, json; print(json.load(sys.stdin).get('$field', ''))" 2>/dev/null || echo "")

    if [ "$actual" = "$expected" ]; then
        echo -e "${GREEN}✓ PASS${NC}: $test_name ($field=$actual)"
        ((PASS++))
    else
        echo -e "${RED}✗ FAIL${NC}: $test_name (expected $field=$expected, got $actual)"
        ((FAIL++))
    fi
}

# Test 1: Create first session (should be active)
echo -e "\n${YELLOW}=== Test 1: Create first session (should be active) ===${NC}"
response=$(curl -s -X POST "$BASE_URL/api/session/create")
S1=$(echo "$response" | python3 -c "import sys, json; print(json.load(sys.stdin)['session_id'])")
assert_json_field "$response" "is_active" "True" "First session is active"
assert_json_field "$response" "position" "0" "First session position is 0"

# Test 2: Create second session (should be queued)
echo -e "\n${YELLOW}=== Test 2: Create second session (should be queued) ===${NC}"
response=$(curl -s -X POST "$BASE_URL/api/session/create")
S2=$(echo "$response" | python3 -c "import sys, json; print(json.load(sys.stdin)['session_id'])")
assert_json_field "$response" "is_active" "False" "Second session is not active"
assert_json_field "$response" "position" "1" "Second session position is 1"

# Test 3: Create third session (should be second in queue)
echo -e "\n${YELLOW}=== Test 3: Create third session (should be second in queue) ===${NC}"
response=$(curl -s -X POST "$BASE_URL/api/session/create")
S3=$(echo "$response" | python3 -c "import sys, json; print(json.load(sys.stdin)['session_id'])")
assert_json_field "$response" "is_active" "False" "Third session is not active"
assert_json_field "$response" "position" "2" "Third session position is 2"

# Test 4: Check queue status
echo -e "\n${YELLOW}=== Test 4: Check queue status ===${NC}"
response=$(curl -s "$BASE_URL/api/queue/status?session_id=$S1")
assert_json_field "$response" "queue_length" "2" "Queue has 2 waiting sessions"

# Test 5: Session 2 status (queued)
echo -e "\n${YELLOW}=== Test 5: Check queued session status ===${NC}"
response=$(curl -s "$BASE_URL/api/session/status?session_id=$S2")
assert_json_field "$response" "is_active" "False" "Session 2 is not active"
assert_json_field "$response" "position" "1" "Session 2 position is 1"

# Test 6: Release active session (promotion)
echo -e "\n${YELLOW}=== Test 6: Release active session (should promote next) ===${NC}"
curl -s -X POST "$BASE_URL/api/session/release" \
    -H "Content-Type: application/json" \
    -d "{\"session_id\":\"$S1\"}" > /dev/null
sleep 1

# Test 7: Check Session 2 is now active
echo -e "\n${YELLOW}=== Test 7: Verify Session 2 promoted to active ===${NC}"
response=$(curl -s "$BASE_URL/api/session/status?session_id=$S2")
assert_json_field "$response" "is_active" "True" "Session 2 is now active"
assert_json_field "$response" "position" "0" "Session 2 position is now 0"

# Test 8: Check Session 3 position updated
echo -e "\n${YELLOW}=== Test 8: Verify Session 3 position updated ===${NC}"
response=$(curl -s "$BASE_URL/api/session/status?session_id=$S3")
assert_json_field "$response" "is_active" "False" "Session 3 is not active"
assert_json_field "$response" "position" "1" "Session 3 position is now 1"

# Test 9: Trial download tracking - first download
echo -e "\n${YELLOW}=== Test 9: Trial download tracking - first download ===${NC}"
response=$(curl -s -X POST "$BASE_URL/api/trial/register" \
    -H "Content-Type: application/json" \
    -d '{"mid":"test-machine-001","fmt":"step"}')
code=$(curl -s -w "%{http_code}" -o /dev/null -X POST "$BASE_URL/api/trial/register" \
    -H "Content-Type: application/json" \
    -d '{"mid":"test-machine-002","fmt":"step"}')
assert_http_code "200" "$code" "First trial download allowed"

# Test 10: Trial download tracking - second download
echo -e "\n${YELLOW}=== Test 10: Trial download tracking - second download ===${NC}"
code=$(curl -s -w "%{http_code}" -o /dev/null -X POST "$BASE_URL/api/trial/register" \
    -H "Content-Type: application/json" \
    -d '{"mid":"test-machine-002","fmt":"dxf"}')
assert_http_code "200" "$code" "Second trial download allowed"

# Test 11: Trial download tracking - exceed limit
echo -e "\n${YELLOW}=== Test 11: Trial download tracking - exceed limit ===${NC}"
code=$(curl -s -w "%{http_code}" -o /dev/null -X POST "$BASE_URL/api/trial/register" \
    -H "Content-Type: application/json" \
    -d '{"mid":"test-machine-002","fmt":"svg"}')
assert_http_code "429" "$code" "Third trial download blocked (429)"

# Test 12: Trial status check
echo -e "\n${YELLOW}=== Test 12: Trial status check ===${NC}"
response=$(curl -s "$BASE_URL/api/trial/status?mid=test-machine-002")
assert_json_field "$response" "count" "2" "Trial machine has 2 downloads this week"
assert_json_field "$response" "limit" "2" "Trial limit is 2 per week"

# Test 13: Download blocking without session
echo -e "\n${YELLOW}=== Test 13: Download blocking without session ===${NC}"
code=$(curl -s -w "%{http_code}" -o /dev/null \
    "$BASE_URL/api/preview-stl?family=HTD&pitch=3M&teeth=20")
# Note: preview-stl doesn't require session, so this should be 200
assert_http_code "200" "$code" "Preview STL accessible without session"

# Test 14: Heartbeat keeps session alive
echo -e "\n${YELLOW}=== Test 14: Heartbeat functionality ===${NC}"
response=$(curl -s -X POST "$BASE_URL/api/session/heartbeat" \
    -H "Content-Type: application/json" \
    -d "{\"session_id\":\"$S2\"}")
assert_json_field "$response" "success" "True" "Heartbeat successful"

# Test 15: Queue empty after all released
echo -e "\n${YELLOW}=== Test 15: Release remaining sessions ===${NC}"
curl -s -X POST "$BASE_URL/api/session/release" \
    -H "Content-Type: application/json" \
    -d "{\"session_id\":\"$S2\"}" > /dev/null
curl -s -X POST "$BASE_URL/api/session/release" \
    -H "Content-Type: application/json" \
    -d "{\"session_id\":\"$S3\"}" > /dev/null
sleep 1

response=$(curl -s -X POST "$BASE_URL/api/session/create")
assert_json_field "$response" "is_active" "True" "New session is active (queue empty)"

# Summary
echo -e "\n${YELLOW}=== Test Summary ===${NC}"
total=$((PASS + FAIL))
echo -e "${GREEN}Passed: $PASS${NC} / ${RED}Failed: $FAIL${NC} / Total: $total"

if [ $FAIL -eq 0 ]; then
    echo -e "\n${GREEN}All tests passed!${NC}"
    exit 0
else
    echo -e "\n${RED}Some tests failed!${NC}"
    exit 1
fi
