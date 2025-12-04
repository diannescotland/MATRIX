# MATRIX - AI Assistant Context Document

**Purpose:** Quick reference for Claude/AI assistants in future sessions
**Last Updated:** December 4, 2025
**Project:** Web3/Crypto Telegram Contact Management System

---

## 1-Minute Project Overview

**What is MATRIX?**
Multi-account Telegram contact manager for Web3/crypto outreach campaigns. Tracks developers and KOLs (Key Opinion Leaders) for token launches using emoji-based status indicators.

**Tech Stack:**
- Backend: Python 3.8+ (Flask REST API + Telethon)
- Frontend: React 18 (Vite + shadcn/ui)
- Database: SQLite 3 (accounts.db)
- API: REST JSON over HTTP (localhost:5000 ↔ localhost:5173)

**Core Workflow:**
1. User adds Telegram account(s) via authentication flow
2. Import contacts from CSV (Devs 💻 or KOLs 📢) with 🔵 (no reply) status
3. Scan dialogs for replies → auto-update 🔵→🟡 (replied)
4. Export analytics, organize folders, backup contacts

---

## Critical File Map

### Backend (Python)
```
backend/
├── api_server.py           # 🔴 UNIFIED FILE - API + Telegram core logic (~5000 lines)
│                           # Contains: Flask API, UnifiedContactManager, rate limiting
├── account_manager.py      # 🔴 Database CRUD - accounts & backups tables (447 lines)
├── NEXT_SESSION_README.md  # Future improvements documentation
├── api_server.py.backup    # Backup before unification
└── matrix.py.backup        # Backup of original matrix.py (CLI removed)
```

**Note:** As of December 4, 2025, `matrix.py` was merged into `api_server.py` to create a single unified backend file. CLI functionality was removed (web-only now).

### Frontend (React)
```
frontend/src/
├── services/api.js         # 🔴 API client - validation, caching, retry logic (499 lines)
├── pages/
│   ├── Dashboard.jsx       # Stats, quick actions, account selector
│   ├── Operations.jsx      # Scan, folders, backup operations
│   ├── Accounts.jsx        # Account management, auth flow
│   └── Import.jsx          # CSV import for devs/KOLs
├── components/             # Reusable UI components
└── App.jsx                 # Main router
```

### Database & Storage
```
accounts.db                 # SQLite - accounts + backups tables
sessions/session_*.session  # Telegram encrypted sessions (Telethon)
config.json                 # API credentials (api_id, api_hash)
logs/                       # Operation logs, backups, exports
uploads/                    # Uploaded CSV files
```

---

## Database Schema (SQLite)

### `accounts` Table
```sql
phone TEXT PRIMARY KEY        -- "1234567890" (no +)
name TEXT                     -- Account alias
api_id INTEGER                -- Telegram API ID
api_hash TEXT                 -- Telegram API Hash
session_path TEXT             -- Path to .session file
status TEXT DEFAULT 'active'  -- active | inactive | error
is_default INTEGER DEFAULT 0  -- Only ONE account can be default
notes TEXT
created_at TIMESTAMP
last_used TIMESTAMP
```

**Key Constraint:** Only ONE account has `is_default = 1` at a time.

### `backups` Table
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
phone TEXT NOT NULL           -- Account that created backup
filename TEXT NOT NULL
filepath TEXT NOT NULL
contacts_count INTEGER NOT NULL
created_at TIMESTAMP
FOREIGN KEY (phone) REFERENCES accounts(phone)
```

---

## Critical Validation Rules

### 🔴 NEVER BYPASS: Telegram Rate Limits
**Location:** `api_server.py` (UnifiedContactManager.__init__ - around line 700)

```python
BATCH_SIZE_MIN = 3              # Min contacts per batch
BATCH_SIZE_MAX = 7              # Max contacts per batch
PER_CONTACT_DELAY_MIN = 2.0     # Min delay between contacts (seconds)
PER_CONTACT_DELAY_MAX = 6.0     # Max delay between contacts (seconds)
BATCH_DELAY_MIN = 45            # Min delay between batches (seconds)
BATCH_DELAY_MAX = 90            # Max delay between batches (seconds)
```

**Why Critical:** Telegram bans accounts that add contacts/send messages too quickly. These delays mimic human behavior and prevent FloodWaitError (24hr+ bans).

**Rule:** NEVER reduce these values below defaults without explicit user confirmation and risk acknowledgment.

### Frontend Validations (api.js:140-176)
- **Phone:** `/^\+\d{7,15}$/` (E.164 format, +1234567890)
- **API ID:** Must be numeric
- **API Hash:** Min 32 characters
- **CSV:** Max 10MB, .csv extension only

---

## Known Issues & Their Locations

### ✅ Issue #1: SQLite Database Locking (FIXED)
**Symptom:** "database is locked" errors
**Cause:** Multiple processes accessing accounts.db or session files simultaneously
**Status:** ✅ FIXED on December 2, 2025

**Locations:**
- `api_server.py:367-382` - Has retry logic with exponential backoff
- `account_manager.py:22-27` - get_db_connection() function

**Fix Implemented:**
```python
# account_manager.py:22-27
def get_db_connection():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')  # ← ADDED
    return conn
```

**Why:** WAL mode allows concurrent reads while writing, reducing lock contention significantly.

### ✅ Issue #2: Reply Scanning Doesn't Work (FIXED)
**Symptom:** Scan operation completes but doesn't detect replies
**Location:** `api_server.py` - `UnifiedContactManager.scan_for_replies()` method
**Status:** ✅ FIXED on December 2, 2025

**Previous Issues:**
1. **Unread Count Logic:** Relied on `dialog.unread_count > 0` which was unreliable
2. **Name Matching:** Used fuzzy string matching (case-sensitive, emoji included)
3. **No Message Verification:** Didn't check WHO sent the last message
4. **Session Lock:** Another client could hold session file

**Old Logic (REPLACED):**
```python
# Gets dialogs with unread_count > 0
# Matches dialog name to blue contact name using fuzzy matching
# Updates status if unread_count > 0
```

**New Logic (IMPLEMENTED):**
```python
# Build mapping of blue contact user IDs
# For each dialog:
#   - Get last message in dialog
#   - Check if message.from_id == entity.id (they sent it, not us)
#   - If yes, mark as replied
# This confirms they replied AFTER we messaged them
```

**Debug Logging Added:**
```python
self.log(f"📊 DEBUG: Blue contacts found: {len(all_blue_contacts)}")
self.log(f"📊 DEBUG: Dialogs scanned: {len(dialogs)}")
self.log(f"📊 DEBUG: Matches found: {len(reply_statuses)}")
self.log(f"   📧 Dialog with {name}: last_msg_from={'them'/'us'}")
self.log(f"   ✅ REPLY DETECTED from {name}")
```

**Key Improvements:**
1. Uses user ID matching instead of name matching (more reliable)
2. Checks actual message sender (from_id) not just unread count
3. Comprehensive debug logging for troubleshooting
4. Detects replies even if user has read them (unread_count = 0)

### ✅ Issue #3: Missing get_db_connection Import (FIXED)
**Symptom:** Stats endpoint returns `NameError: name 'get_db_connection' is not defined`
**Cause:** Function `get_db_connection()` was called in api_server.py but not imported from account_manager
**Status:** ✅ FIXED on December 3, 2025

**Location:** `api_server.py:94-99`

**Fix Implemented:**
```python
# api_server.py:94-99
from account_manager import (
    init_database, add_account, get_all_accounts, get_active_accounts,
    get_account_by_phone, update_account_status, validate_account,
    validate_accounts_batch, delete_account, update_account_last_used,
    get_default_account, set_default_account, get_db_connection  # ← ADDED
)
```

**Why:** The stats endpoint (line 266) calls `get_db_connection()` to query the backups table, but the function wasn't imported. Adding it to the imports resolves the error.

### ✅ Issue #4: Stats 404 Error - Phone Format Mismatch (FIXED)
**Symptom:** Dashboard shows 404 error when loading stats for an account
**Cause:** Phone number format inconsistency between accounts and backups tables
**Status:** ✅ FIXED on December 3, 2025

**Database Inconsistency:**
- `accounts` table stores phone as `88807942561` (no + prefix)
- `backups` table stores phone as `+88807942561` (WITH + prefix)
- Stats endpoint query failed because formats didn't match

**Location:** `api_server.py:264-307`

**Fix Implemented:**
```python
# api_server.py:264-307
if phone:
    conn = get_db_connection()
    cursor = conn.cursor()

    # Try exact match first
    cursor.execute('SELECT filepath FROM backups WHERE phone = ? ORDER BY created_at DESC LIMIT 1', (phone,))
    row = cursor.fetchone()

    # If not found, try with + prefix
    if not row and not phone.startswith('+'):
        cursor.execute('SELECT filepath FROM backups WHERE phone = ? ORDER BY created_at DESC LIMIT 1', (f'+{phone}',))
        row = cursor.fetchone()

    # If not found, try without + prefix
    if not row and phone.startswith('+'):
        cursor.execute('SELECT filepath FROM backups WHERE phone = ? ORDER BY created_at DESC LIMIT 1', (phone[1:],))
        row = cursor.fetchone()
```

**Why:** The stats endpoint now tries multiple phone formats automatically, handling both with and without the + prefix. This ensures stats load correctly regardless of how the phone number is stored in either table.

**Impact:** Dashboard stats now display correctly for all accounts, showing contact counts from the most recent backup file.

### 🟡 Issue #5: Session Corruption
**Symptom:** "file is not a database" or "session file corrupted"
**Cause:** Forced shutdown during write, disk space issues, concurrent access

**Handling:** `api_server.py` (UnifiedContactManager.init_client method)
- If "database is locked" → delete session (user must re-auth)
- Other errors → preserve session file (don't auto-delete)

**Prevention:** Always disconnect clients in finally blocks, use timeouts

### ✅ Issue #6: Authentication Endpoints Mismatch (FIXED)
**Symptom:** Adding new accounts fails - code never sent, authentication doesn't work
**Cause:** Frontend and backend used different endpoint names
**Status:** ✅ FIXED on December 3, 2025

**Previous Mismatches:**
- Frontend called `/auth/send-code` → Backend had `/auth/start`
- Frontend called `/auth/verify-code` → Backend had `/auth/submit-code`
- Frontend called `/auth/verify-password` → Backend had `/auth/submit-password`
- Frontend sent `api_id` and `api_hash` but backend ignored them

**Location:** `api_server.py:1051-1288`

**Fix Implemented:**
1. Added `/auth/send-code` endpoint that accepts account-specific `api_id` and `api_hash`
2. Added `/auth/verify-code` endpoint (alias for submit-code)
3. Added `/auth/verify-password` endpoint (alias for submit-password)
4. Kept legacy endpoints (`/auth/start`, `/auth/submit-code`, `/auth/submit-password`) for backward compatibility
5. Each account can now have its own API credentials stored in the database

**New Authentication Flow:**
```
POST /api/auth/send-code {phone, api_id, api_hash}
  → Uses account-specific credentials if provided
  → Falls back to global config.json credentials if not

POST /api/auth/verify-code {phone, code}
  → Verifies the code
  → Returns {requires_password: true} if 2FA enabled

POST /api/auth/verify-password {phone, password}
  → Verifies 2FA password
  → Saves account to database on success
```

### ✅ Issue #7: Operations Only Worked on Default Account (FIXED)
**Symptom:** Scan, backup, import only worked on the default account
**Cause:** Endpoints didn't accept a phone parameter to specify which account
**Status:** ✅ FIXED on December 3, 2025

**Location:** `api_server.py` - import/scan/backup/organize endpoints

**Fix Implemented:**
Added optional `phone` parameter to all operation endpoints:
- `POST /api/import/devs` - now accepts `phone` parameter
- `POST /api/import/kols` - now accepts `phone` parameter
- `POST /api/scan-replies` - now accepts `phone` parameter
- `POST /api/organize-folders` - now accepts `phone` parameter
- `POST /api/backup-contacts` - now accepts `phone` parameter

**New Helper Function:**
```python
def get_manager_for_account(phone: str) -> Optional[UnifiedContactManager]:
    """Create a manager for a specific account (not just default)"""
    # Gets account from database
    # Uses account-specific credentials if available
    # Falls back to global credentials
    # Returns manager ready to use
```

**Usage:**
```python
# Backend now handles:
phone = data.get('phone')  # Optional
if phone:
    mgr = get_manager_for_account(phone)  # Specific account
else:
    mgr = get_manager()  # Default account
```

---

## Key API Endpoints

### Most Used Endpoints
```
GET  /api/health                  # Check API is running
GET  /api/stats?phone={phone}     # Get contact counts (blue/yellow devs/KOLs)
GET  /api/accounts                # List all accounts
GET  /api/accounts/active         # List active accounts only

POST /api/auth/send-code          # Step 1: Send verification code
POST /api/auth/verify-code        # Step 2: Verify code
POST /api/auth/verify-password    # Step 3: Verify 2FA (if needed)

POST /api/import/devs             # Import devs from CSV
POST /api/import/kols             # Import KOLs from CSV
POST /api/scan-replies            # Scan dialogs & update status
POST /api/organize-folders        # Create 4 Telegram folders
POST /api/backup-contacts         # Backup all contacts to CSV
```

### Authentication Flow
```
1. POST /auth/send-code        → Telegram sends code to phone
2. POST /auth/verify-code      → Returns {requires_password: bool}
3. If requires_password:
   POST /auth/verify-password  → Creates session
4. POST /accounts/add          → Saves to database
```

### Import Flow
```
1. POST /upload-csv            → Returns {path: "uploads/file.csv"}
2. POST /import/devs           → {csv_path: path, dry_run: false}
   Body: {csv_path, dry_run}
   Response: {added_count, skipped_count, failed_count, success_rate}
```

---

## Contact Emoji System

**Format:** `{status}{type} {name} | {metadata}`

### Developers
- 🔵💻 = Blue Dev (no reply yet)
- 🟡💻 = Yellow Dev (replied)
- Format: `🔵💻 Alice Smith | SOL | TokenName`

### KOLs
- 🔵📢 = Blue KOL (no reply yet)
- 🟡📢 = Yellow KOL (replied)
- Format: `🔵📢 Bob Jones | @twitter_handle`

### Folder Organization
Auto-creates 4 Telegram folders:
1. 🔵💻 Blue Devs
2. 🟡💻 Yellow Devs
3. 🔵📢 Blue KOLs
4. 🟡📢 Yellow KOLs

---

## Rate Limiting Logic

**Location:** `matrix.py` - import_dev_contacts, import_kol_contacts methods

**Three-Layer Protection:**
```python
# Layer 1: Per-contact delay
for contact in contacts:
    delay = random.uniform(2.0, 6.0)
    await asyncio.sleep(delay)
    await add_contact(contact)

# Layer 2: Batch processing
batch_size = random.randint(3, 7)
process_batch(contacts, batch_size)

# Layer 3: Batch pause
batch_delay = random.uniform(45, 90)
await asyncio.sleep(batch_delay)
```

**Adaptive Slowdown:**
```python
success_rate = successful / total
if success_rate < 0.5:
    # Increase delays by 1.5x if failing too much
    BATCH_DELAY_MIN *= 1.5
    BATCH_DELAY_MAX *= 1.5
```

**FloodWaitError Handling:**
```python
except FloodWaitError as e:
    wait_time = e.seconds + 300  # Wait required time + 5 min buffer
    await asyncio.sleep(wait_time)
    # Continue processing
```

---

## Common Operations & Locations

### Import Contacts
**Frontend:** `Import.jsx:89-143` (handleImport function)
**API:** `api_server.py:672-732` (import_devs endpoint)
**Core:** `api_server.py` - `UnifiedContactManager.import_dev_contacts()` method

**CSV Format (Devs):**
```csv
group_title,dex_chain,owner
TokenName,SOL,telegram_username
```

**CSV Format (KOLs):**
```csv
Twitter Username,TG Usernames
twitter_handle,telegram_username
```

### Scan for Replies
**Frontend:** `Operations.jsx:57-87` (selectedOperation === 'scan')
**API:** `api_server.py:566-630` (scan_replies endpoint)
**Core:** `api_server.py` - `UnifiedContactManager.check_seen_no_reply()`, `scan_for_replies()` methods

**Current Logic:**
1. Get all blue contacts (🔵) from Telegram
2. Iterate dialogs (limit 100)
3. Check `dialog.unread_count > 0`
4. Match dialog name to contact name
5. Return list of replied contacts
6. Auto-update 🔵→🟡

### Account Authentication
**Frontend:** `Accounts.jsx:58-122` (handleSendCode, handleVerifyCode, handleVerifyPassword)
**API:** `api_server.py:1055-1148` (auth endpoints)
**Core:** `api_server.py` - `UnifiedContactManager.start_authentication()`, `verify_code()`, `verify_password()` methods

**Session Storage:** `api_server.py` - `UnifiedContactManager._save_session_to_database()` method

---

## Error Handling Patterns

### Frontend (api.js:26-91)
**Retry Logic:** Up to 3 retries with exponential backoff for network errors and 5xx
**Error Messages:**
- 429 → "Rate limit exceeded"
- 401 → "Authentication failed"
- 400 → "Invalid request"
- 500 + "FLOOD" → "Telegram rate limit hit"
- 500 + "database is locked" → "Database busy, retry"

### Backend (api_server.py)
**Standard Response:**
```python
# Success
return jsonify({'success': True, 'data': result})

# Error
return jsonify({
    'error': str(e),
    'traceback': traceback.format_exc()  # Only in debug mode
}), 500
```

**Session Lock Handling:** `api_server.py:587-605`
```python
# ALWAYS disconnect before new operation
if mgr.client and mgr.client.is_connected():
    await mgr.client.disconnect()
    mgr.client = None

# Create fresh client
connected = await mgr.init_client(mgr.phone_number)
```

---

## Environment Setup

### Running Backend
```bash
cd "C:\Users\LENOVO\Documents\Toshi System\MATRIX"
python backend/api_server.py
# Runs on http://localhost:5000
```

### Running Frontend
```bash
cd "C:\Users\LENOVO\Documents\Toshi System\MATRIX\frontend"
npm run dev
# Runs on http://localhost:5173
```

### Database Access
```bash
sqlite3 accounts.db
> SELECT * FROM accounts;
> SELECT * FROM backups ORDER BY created_at DESC LIMIT 5;
> .quit
```

### Log Locations
```
logs/api_server_*.log              # API server logs
logs/unified_manager_*.log         # Matrix operation logs
logs/contacts_backup_*.csv         # Contact backups
logs/noreplyDEV_*h.csv            # Dev no-reply exports
logs/noreplyKOL_*h.csv            # KOL no-reply exports
```

---

## Quick Debugging Checklist

**API Not Responding:**
```bash
# Check if running
curl http://localhost:5000/api/health

# Check processes
tasklist | findstr python

# Kill stale processes
taskkill /F /PID <pid>
```

**Database Issues:**
```bash
# Check database integrity
sqlite3 accounts.db "PRAGMA integrity_check;"

# Check locks
sqlite3 accounts.db "PRAGMA locking_mode;"

# View sessions
sqlite3 accounts.db "SELECT phone, status, is_default FROM accounts;"
```

**Session Issues:**
```bash
# List sessions
dir sessions\

# Check session size (should be >0 bytes)
dir sessions\ /s

# Delete corrupted session (user must re-auth)
del sessions\session_1234567890.session
```

**Enable Debug Logging:**
```python
# api_server.py:34
logger.setLevel(logging.DEBUG)  # Change from INFO

# api_server.py (logging setup)
logger.setLevel(logging.DEBUG)
```

---

## Next Session Quick Start

1. **Read this file first** (claude.md)
2. **Check for updates:**
   - User's latest issue/request
   - Recent log files in `logs/`
   - Database state: `SELECT * FROM accounts;`
3. **Common requests:**
   - Fix SQLite locking → Add WAL mode (account_manager.py:26)
   - Debug reply scanning → Add logging (matrix.py scan_for_replies)
   - Add new endpoint → api_server.py + api.js + this file
4. **Reference files:**
   - MATRIX_TECHNICAL_GUIDE.md (detailed docs)
   - API_DOCUMENTATION.md (endpoint specs)
   - MATRIX_OPERATIONAL_GUIDE.md (user guide)

---

## Recent Changes (December 4, 2025)

### ✅ Real-Time Import Progress via WebSocket (COMPLETED)
**Status:** DONE
**Date:** December 4, 2025

Implemented real-time progress updates for contact imports, replacing the static loading spinner with a live progress panel.

**User Request:**
> "I want for contact import to see the preview, just like I see in the backend when a contact gets added. I don't want to have that loading screen that keeps me waiting, I want to see progress."

**Backend Changes (`api_server.py`):**

1. **Modified `/api/import/devs` endpoint** (lines 3644-3741):
   - Creates operation with `create_operation()` for WebSocket tracking
   - Runs import in background thread so API returns immediately
   - Returns `operation_id` that frontend subscribes to
   - Passes `progress_callback` to emit real-time updates via WebSocket

2. **Modified `/api/import/kols` endpoint** (lines 3840-3937):
   - Same WebSocket integration as devs endpoint

3. **Updated `import_kol_contacts` method** (lines 1774-2033):
   - Added `operation_id` and `progress_callback` parameters
   - Added progress emissions for: starting, skipped, processing, added, failed, rate_limited, flood_wait

**Frontend Changes (`Import.jsx`):**

1. **Added WebSocket integration**:
   - Imported `useWebSocket` hook
   - Added state for `progressLogs`, `currentProgress`
   - Added `progressContainerRef` for auto-scrolling

2. **Updated `handleImport` function**:
   - Resets progress state before starting
   - Subscribes to `operation_id` after API returns
   - Operation completion handled via WebSocket events

3. **Replaced LoadingSpinner with Real-Time Progress Panel**:
   - Progress bar with percentage
   - Current status message with icon
   - Live log feed with auto-scroll and color-coded messages (green=success, red=error, yellow=warning)
   - Running counts for Added/Skipped/Failed

**WebSocket Events Used:**
- `operation_progress`: Real-time progress updates (processed/total, message)
- `operation_log`: Individual log messages with timestamps
- `operation_complete`: Final results when import finishes

**How It Works:**
```
1. User clicks "Start Import"
2. Frontend calls /api/import/devs (or kols)
3. Backend creates operation, starts background thread, returns immediately with operation_id
4. Frontend subscribes to WebSocket room for that operation
5. As each contact is processed, backend emits progress events
6. Frontend displays real-time progress bar, logs, and counts
7. When complete, backend emits operation_complete, frontend shows final results
```

**Files Changed:**
- `backend/api_server.py`: Modified import endpoints and `import_kol_contacts` method
- `frontend/src/pages/Import.jsx`: Added WebSocket integration and progress panel UI

---

### ✅ Account Isolation Bug Fixes (COMPLETED)
**Status:** DONE
**Date:** December 4, 2025

Fixed critical bugs where all accounts showed the same stats and backups went to wrong account:

**Issues Fixed:**
1. **Dashboard backup didn't pass phone parameter** - `backupContacts()` was called without phone, always backing up default account
2. **Per-account backup files never created** - Stats couldn't find account-specific data
3. **Cache showed stale data** - No invalidation when switching accounts
4. **Confusing error messages** - No helpful guidance when accounts had no backup

**Files Changed:**
- `frontend/src/pages/Dashboard.jsx`:
  - Line 84: `backupContacts(phone)` now passes selected account's phone
  - Lines 28-31: Added `invalidateCache()` on account selection change
  - Lines 57-83: Improved error handling with "No backup found - run backup first" message
- `frontend/src/services/api.js`:
  - Lines 245-267: Removed wrong `Content-Type: multipart/form-data` headers from `importDevs()` and `importKols()`
- `backend/api_server.py`:
  - Lines 395-432: Added `PerAccountCacheManager` class for per-account contact caching
  - Lines 2444-2491: Modified `export_all_contacts_backup()` to create per-account files (`logs/backups/contacts_{phone}_latest.csv`)
  - Line 980: Updated `UnifiedContactManager` to use per-account cache

**New Backup File Structure:**
```
logs/backups/
├── contacts_88807942561_latest.csv      # High Bureau's latest backup
├── contacts_88807942561_20251204_*.csv  # Timestamped backups
├── contacts_15803592485_latest.csv      # James Bland's latest backup
└── contacts_15803592485_20251204_*.csv  # Timestamped backups
```

### ✅ Import 415 Error Fix (COMPLETED)
**Status:** DONE
**Date:** December 4, 2025

Fixed "415 Unsupported Media Type" error when importing contacts.

**Root Cause:** `importDevs()` and `importKols()` in `api.js` incorrectly set `Content-Type: multipart/form-data` while sending JSON data. Backend expected `application/json`.

**Fix:** Removed the Content-Type header override, letting axios use the default `application/json`.

### ✅ Codebase Unification (COMPLETED)
**Status:** DONE
**Date:** December 4, 2025

`matrix.py` was merged into `api_server.py` to create a single unified backend file:

**Changes Made:**
- Moved `TelegramRateLimitError` class to api_server.py
- Moved global constants (`CONFIG_FILE`, `SESSIONS_DIR`, `LOGS_DIR`) to api_server.py
- Moved utility functions (`load_config`, `save_config`, `get_api_credentials`, etc.) to api_server.py
- Moved `UnifiedContactManager` class (~2000 lines) to api_server.py
- Removed CLI menu functionality (web-only now)
- Created `NEXT_SESSION_README.md` documenting future improvements

**Files Changed:**
- `api_server.py` - Now ~5000 lines (unified file)
- `matrix.py` - DELETED (merged into api_server.py)
- `api_server.py.backup` - Backup before unification
- `matrix.py.backup` - Backup of original file

---

## Recent Fixes (December 4, 2025)

### ✅ Multi-Account Operations UI (COMPLETED)
**Status:** DONE
**Date:** December 4, 2025

Implemented multi-account selection for Operations page with WebSocket real-time progress.

**Changes Made:**
1. **Title Fix:** Changed "WhatsApp" to "Telegram" in `frontend/index.html`
2. **Operations.jsx:** Replaced single-account dropdown with `MultiAccountSelector` checkboxes
3. **WebSocket Integration:** Real-time progress updates for scan/backup/organize operations
4. **Per-Account Progress UI:** Shows status for each account during multi-account operations
5. **Sequential Execution:** Backend runs accounts one-by-one (safer for Telegram rate limits)

**Files Changed:**
- `frontend/index.html` - Title fix
- `frontend/src/services/api.js` - Added `startMultiAccountOperation()` function
- `frontend/src/pages/Operations.jsx` - Complete rewrite for multi-account + WebSocket
- `backend/api_server.py` - Changed `_execute_multi_account_operation()` to sequential

**How It Works:**
1. User selects one or more accounts using checkboxes
2. Clicks "Start Operation"
3. Frontend calls `/api/operations/start` with all selected phone numbers
4. Backend runs operation on each account sequentially (one at a time)
5. WebSocket sends real-time progress updates per account
6. UI shows overall progress bar + per-account status indicators

---

## Pending Features & Next Steps

*No pending features at this time. All requested features have been implemented.*

---

## Important Reminders

1. **Rate Limits Are Sacred:** Never reduce below defaults without explicit user permission
2. **Session Management:** Always disconnect in finally blocks, handle locks gracefully
3. **Database Safety:** Use WAL mode, timeouts, and retry logic for all DB operations
4. **Error Transparency:** Return detailed errors in dev mode, sanitize in production
5. **Telegram API:** Telethon is async-only, must use event loop (asyncio)
6. **Web3 Context:** This is for crypto outreach, not spam - respect rate limits strictly

---

**Key Principle:** This system manages real Telegram accounts for legitimate business outreach. Account bans = project failure. When in doubt, prioritize safety (longer delays, more validation) over speed.

**End of AI Context Document**
