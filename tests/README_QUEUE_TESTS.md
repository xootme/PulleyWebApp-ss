# Queue System Test Suite

Comprehensive test harness for PulleyWebApp queue functionality.

## Prerequisites
- Flask running on http://localhost:5001
- Python 3.x with `requests` library (for pytest)

## Test Scripts

### 1. Bash Test Harness (test_queue.sh)
Quick integration tests for the queue system.

```bash
bash tests/test_queue.sh
```

**Tests Covered:**
1. ✅ Create first session → is_active: true
2. ✅ Create second session → position: 1, is_active: false  
3. ✅ Create third session → position: 2, is_active: false
4. ✅ Queue status reports correct length
5. ✅ Queued session status correct
6. ✅ Release active session → next promoted
7. ✅ Session positions update correctly
8. ✅ Trial download - 1st allowed (HTTP 200)
9. ✅ Trial download - 2nd allowed (HTTP 200)
10. ✅ Trial download - 3rd blocked (HTTP 429)
11. ✅ Trial status check
12. ✅ Preview STL accessible without session
13. ✅ Heartbeat keeps session alive
14. ✅ Release all sessions
15. ✅ Queue empty after all released

### 2. Pytest Test Suite (test_queue_pytest.py)
Unit and integration tests with pytest.

```bash
pip install pytest requests
pytest tests/test_queue_pytest.py -v
```

**Test Classes:**

#### TestSessionManagement
- `test_create_first_session_is_active`: First session is active immediately
- `test_create_second_session_is_queued`: Second session queued at position 1
- `test_session_position_increments`: Each session gets incrementing position

#### TestSessionPromotion
- `test_promotion_on_release`: Releasing active promotes next queued
- `test_positions_update_after_promotion`: Queue positions update correctly

#### TestQueueStatus
- `test_queue_length_reported`: Queue reports correct length
- `test_wait_time_calculated`: Wait time estimated from active session time

#### TestTrialDownloads
- `test_first_download_allowed`: 1st download allowed
- `test_second_download_allowed`: 2nd download allowed
- `test_third_download_blocked`: 3rd download blocked (429)
- `test_trial_status_check`: Status endpoint reports correct counts

#### TestHeartbeat
- `test_heartbeat_keeps_session_alive`: Heartbeat updates successfully
- `test_heartbeat_invalid_session`: Invalid session heartbeat fails

#### TestIntegration
- `test_complete_queue_workflow`: Full workflow: create→queue→promote→release

## Manual Testing

### Test Session Creation and Queueing
```bash
# Create first session (should be active)
curl -X POST http://localhost:5001/api/session/create | jq .

# Create second session (should be queued)
curl -X POST http://localhost:5001/api/session/create | jq .

# Create third session (should be second in queue)
curl -X POST http://localhost:5001/api/session/create | jq .
```

### Test Session Promotion
```bash
# Release active session
curl -X POST http://localhost:5001/api/session/release \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<session_id>"}'

# Check next session is promoted
curl "http://localhost:5001/api/session/status?session_id=<session_id>" | jq .
```

### Test Trial Downloads
```bash
# Register first download
curl -X POST http://localhost:5001/api/trial/register \
  -H "Content-Type: application/json" \
  -d '{"mid":"test-machine-001","fmt":"step"}' | jq .

# Register second download
curl -X POST http://localhost:5001/api/trial/register \
  -H "Content-Type: application/json" \
  -d '{"mid":"test-machine-001","fmt":"dxf"}' | jq .

# Try third download (should be blocked)
curl -X POST http://localhost:5001/api/trial/register \
  -H "Content-Type: application/json" \
  -d '{"mid":"test-machine-001","fmt":"svg"}' | jq .

# Check trial status
curl "http://localhost:5001/api/trial/status?mid=test-machine-001" | jq .
```

## Test Data Files

Trial download data is stored in:
- **File:** `logs/trial_downloads.json`
- **Format:** JSON with machine_id as key, array of download records
- **Cleanup:** Automatic daily cleanup removes entries older than 7 days

Example structure:
```json
{
  "test-machine-001": [
    {"timestamp": "2026-06-05T12:00:00", "format": "step"},
    {"timestamp": "2026-06-05T13:00:00", "format": "dxf"}
  ]
}
```

## Expected Results

| Test | Expected | Status |
|------|----------|--------|
| Create 1st session | is_active: true | ✅ |
| Create 2nd session | position: 1 | ✅ |
| Create 3rd session | position: 2 | ✅ |
| Release → promote | Next becomes active | ✅ |
| 1st trial download | HTTP 200 | ✅ |
| 2nd trial download | HTTP 200 | ✅ |
| 3rd trial download | HTTP 429 | ✅ |
| Trial cleanup | 7-day retention | ✅ |
| Heartbeat | success: true | ✅ |

## CI/CD Integration

Run before deployment:
```bash
# Install dependencies
pip install pytest requests

# Run full test suite
pytest tests/test_queue_pytest.py -v --tb=short

# Or quick integration test
bash tests/test_queue.sh
```

## Troubleshooting

**Tests timeout?**
- Ensure Flask is running: `curl http://localhost:5001`
- Check for any exceptions in Flask logs

**Trial downloads not working?**
- Check `logs/trial_downloads.json` exists and is writable
- Verify no JSON syntax errors in file

**Session promotion failing?**
- Check `release_session()` is properly promoting next user
- Verify no stale sessions blocking queue
