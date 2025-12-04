# MATRIX HTTP API Documentation

**Version:** 1.0
**Base URL:** `http://localhost:5000/api`
**Status:** Production Ready

## Overview

The MATRIX API provides RESTful endpoints for the React Native Web frontend to interact with the Python backend. All responses are JSON-formatted.

## Server

Start the API server with:
```bash
python api_server.py
```

The server runs on `http://localhost:5000` by default.

---

## Health & Status Endpoints

### GET `/health`
Health check endpoint. Returns server status.

**Response (200 OK):**
```json
{
  "status": "healthy",
  "timestamp": "2025-11-17T12:34:56.789123",
  "version": "1.0.0"
}
```

### GET `/status`
Get current operation status.

**Response (200 OK):**
```json
{
  "current_operation": "import_devs",
  "progress": 23,
  "total": 50,
  "status": "in_progress",
  "message": "Processing contact 23 of 50..."
}
```

---

## Configuration Endpoints

### GET `/config`
Get current configuration and rate-limit settings.

**Response (200 OK):**
```json
{
  "api_id": 22318118,
  "api_hash": "e604e75ed...",
  "phone": "88807942561",
  "rate_limit": {
    "batch_size_min": 3,
    "batch_size_max": 7,
    "delay_per_contact_min": 2,
    "delay_per_contact_max": 6,
    "batch_pause_min": 45,
    "batch_pause_max": 90
  }
}
```

### POST `/config/rate-limit`
Update rate-limit configuration.

**Request Body:**
```json
{
  "batch_size_min": 3,
  "batch_size_max": 7,
  "delay_per_contact_min": 2,
  "delay_per_contact_max": 6,
  "batch_pause_min": 45,
  "batch_pause_max": 90
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "message": "Rate-limit settings updated",
  "config": {
    "batch_size_min": 3,
    ...
  }
}
```

---

## Statistics Endpoints

### GET `/stats`
Get contact statistics and breakdowns.

**Response (200 OK):**
```json
{
  "total_contacts": 142,
  "dev_contacts": {
    "total": 89,
    "blue": 56,
    "yellow": 33
  },
  "kol_contacts": {
    "total": 53,
    "blue": 28,
    "yellow": 25
  },
  "timestamp": "2025-11-17T12:34:56.789123"
}
```

---

## Import Endpoints

### POST `/import/devs`
Start importing developer contacts from a CSV file.

**Request Body:**
```json
{
  "csv_path": "/path/to/file.csv",
  "dry_run": false
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "operation": "import_devs",
  "dry_run": false,
  "result": {
    "added_count": 23,
    "skipped_count": 5,
    "failed_count": 2,
    "success_rate": 0.92
  }
}
```

**Response (400/500 Error):**
```json
{
  "error": "CSV file not found",
  "traceback": "..."
}
```

### POST `/import/kols`
Start importing KOL contacts from a CSV file.

**Request Body:**
```json
{
  "csv_path": "/path/to/file.csv",
  "dry_run": true
}
```

**Response:** Same format as `/import/devs`

---

## Operation Endpoints

### POST `/scan-replies`
Scan recent dialogs for replies from blue contacts and auto-update status.

**Request Body:**
```json
{
  "dialog_limit": 100
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "operation": "scan_replies",
  "scan_results": {
    "dev_replied": 12,
    "dev_no_reply": 45,
    "kol_replied": 8,
    "kol_no_reply": 22
  },
  "update_results": {
    "updated_count": 20
  }
}
```

### POST `/organize-folders`
Organize contacts into 4 Telegram folders by type and status.

**Request Body:**
```json
{}
```

**Response (200 OK):**
```json
{
  "success": true,
  "operation": "organize_folders",
  "result": {
    "folders_created": 4,
    "contacts_organized": 142
  }
}
```

---

## Session Management Endpoints

### GET `/sessions`
List all available Telegram sessions.

**Response (200 OK):**
```json
{
  "sessions": [
    {
      "phone": "88807942561",
      "filename": "session_88807942561.session",
      "size": 4096,
      "created": "2025-11-15T10:30:00"
    }
  ],
  "count": 1
}
```

### POST `/sessions/select`
Switch to a different Telegram session.

**Request Body:**
```json
{
  "phone": "88807942561"
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "message": "Switched to session 88807942561",
  "default_session": "88807942561"
}
```

---

## Log Endpoints

### GET `/logs?limit=50`
Get recent operation logs.

**Query Parameters:**
- `limit` (optional, default=50): Number of log lines to return

**Response (200 OK):**
```json
{
  "logs": [
    "2025-11-17 12:34:56 - INFO - ✅ Loaded config from config.json",
    "2025-11-17 12:34:57 - INFO - 📊 Statistics retrieved: 142 total contacts",
    ...
  ],
  "count": 50
}
```

---

## File Upload Endpoints

### POST `/upload-csv`
Upload a CSV file for later import.

**Request:** Multipart form data with file field

**Response (200 OK):**
```json
{
  "success": true,
  "filename": "nyx_20251117_123456.csv",
  "path": "/full/path/to/nyx_20251117_123456.csv",
  "size": 12345,
  "timestamp": "2025-11-17T12:34:56.789123"
}
```

### GET `/uploads`
List uploaded CSV files.

**Response (200 OK):**
```json
{
  "files": [
    {
      "filename": "nyx_20251117_123456.csv",
      "path": "/full/path/to/nyx_20251117_123456.csv",
      "size": 12345,
      "created": "2025-11-17T12:34:56"
    }
  ],
  "count": 1
}
```

---

## Error Handling

### Error Response Format
```json
{
  "error": "Description of what went wrong",
  "traceback": "Full Python traceback (if debug mode)"
}
```

### Common Status Codes
- `200 OK` - Request successful
- `400 Bad Request` - Missing or invalid parameters
- `404 Not Found` - Endpoint doesn't exist
- `500 Internal Server Error` - Server error (check logs)

### Common Errors
- **"No response from server"** - API not running on localhost:5000
- **"CSV file not found"** - Path is incorrect or file doesn't exist
- **"API ID or Hash cannot be empty"** - Config missing credentials
- **"Session file not found"** - Phone number mismatch or not authenticated

---

## Authentication Flow

The API uses Telegram session files for authentication:

1. **First Run:** API loads API credentials from `config.json`
2. **Session Check:** API looks for existing session in `sessions/` folder
3. **Auto-Connect:** If session exists, API auto-connects (no phone needed)
4. **Session Switch:** Frontend can switch sessions via `/sessions/select`

All subsequent requests reuse the authenticated Telegram client.

---

## Rate Limiting

All import and scan operations are rate-limited to prevent Telegram API errors:

- **Batch Size:** 3-7 contacts per batch (randomized)
- **Per-Contact Delay:** 2-6 seconds (randomized)
- **Batch Pause:** 45-90 seconds (randomized)
- **Adaptive Slowdown:** Delays increase by 1.5x if success rate < 50%

See `/config/rate-limit` endpoint to adjust these values.

---

## Concurrency & Thread Safety

- All API endpoints are thread-safe
- Multiple requests can be processed simultaneously
- Session file locking prevents corruption from concurrent CLI + API access
- Operation state is shared across all requests

---

## Response Time Expectations

| Operation | Time | Notes |
|-----------|------|-------|
| GET /health | <100ms | Instant |
| GET /stats | 5-10s | Fetches contact count |
| POST /import/devs (50) | 5-10 min | Rate-limited |
| POST /scan-replies | 5-7 min | Dialog I/O |
| POST /organize-folders | 2-5 min | Fast categorization |
| POST /upload-csv | <1s | File size dependent |

---

## Example Usage (JavaScript)

```javascript
import * as api from './services/api.js';

// Check health
await api.checkHealth();

// Get statistics
const stats = await api.getStatistics();
console.log(`Total contacts: ${stats.total_contacts}`);

// Import dev contacts
const result = await api.importDevContacts('/path/to/devs.csv', false);
console.log(`Added ${result.result.added_count} contacts`);

// Scan for replies
const scanResult = await api.scanReplies(100);
console.log(`${scanResult.scan_results.dev_replied} devs replied`);

// Update rate limit
await api.updateRateLimit({
  batch_size_min: 5,
  batch_size_max: 10
});

// Get logs
const logs = await api.getLogs(100);
logs.logs.forEach(line => console.log(line));
```

---

## Troubleshooting

### API Connection Failed
- Check that Flask server is running: `python api_server.py`
- Verify port 5000 is not in use
- Check firewall settings

### Import Fails with FloodWaitError
- Increase rate-limit delays via `/config/rate-limit`
- Wait 30+ minutes before retrying
- Reduce batch size

### Session Expired
- Delete the old session file in `sessions/` folder
- API will prompt for re-authentication on next request

### Logs Show "API ID cannot be empty"
- Ensure `config.json` has valid API credentials
- Delete `config.json` and run `python api_server.py` to re-enter credentials

---

## Support

For issues or questions:
1. Check the logs: `/logs` endpoint
2. Review MATRIX_OPERATIONAL_GUIDE.md for general usage
3. Check Python API server logs in `logs/` directory
