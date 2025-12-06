# Plan: Migrate MATRIX to StringSession with Singleton Pattern

## Problem
MATRIX suffers from repeated "Account not authorized - needs authentication" errors due to:
1. **SQLite session files** causing database locks when multiple components access them
2. **No singleton pattern** - multiple TelegramClient instances created for same account
3. **Event loop conflicts** - Flask endpoints create new loops while clients exist in different loops

## Solution
Migrate to **StringSession** with a **TGClient singleton wrapper** (based on reference implementation in `TELEGRAM_SESSION_PERSISTENCE.md`).

---

## Files to Create

### 1. `backend/tg_client.py` (NEW - Core Component)

TGClient wrapper class with:
- **StringSession storage** - Plain text files, no SQLite
- **Singleton pattern** via `__new__` override
- **Reference counting** for connections
- **Async context manager** (`__aenter__`, `__aexit__`)
- **Auto-save session** on exit
- **Auto-cleanup** inactive instances (2 hours)

```python
class TGClient:
    _instances: Dict[Tuple[str, int, str], 'TGClient'] = {}
    _last_active: Dict[Tuple[str, int, str], float] = {}

    def __new__(cls, session_name: str, api_id: int, api_hash: str, **kwargs):
        instance_key = (session_name, api_id, api_hash)
        force_init = kwargs.pop("force_init", False)

        if not force_init and instance_key in cls._instances:
            cls._last_active[instance_key] = time.time()
            return cls._instances[instance_key]  # Return existing

        instance = super().__new__(cls)
        cls._instances[instance_key] = instance
        return instance

    def __init__(self, session_name, api_id, api_hash, **kwargs):
        # Load StringSession from file
        session_str = self._load_session()
        self.session = StringSession(session_str) if session_str else StringSession()
        self.client = TelegramClient(self.session, api_id, api_hash, proxy=kwargs.get('proxy'))
        self._connection_count = 0

    async def connect(self):
        if not self.client.is_connected():
            await self.client.connect()
        self._connection_count += 1

    async def disconnect(self, force=False):
        self._connection_count -= 1
        if force or self._connection_count == 0:
            self._save_session()
            await self.client.disconnect()
```

### 2. `backend/migrate_sessions.py` (NEW - Migration Script)

Converts existing SQLite `.session` files to StringSession text files:
- Detect SQLite vs StringSession format
- Extract session string using Telethon
- Write as plain text file
- Backup original files
- Clean up journal/WAL files

```bash
python migrate_sessions.py --backup  # Migrate with backups
python migrate_sessions.py --dry-run  # Preview only
```

---

## Files to Modify

### 3. `backend/connection_manager.py`

Replace raw TelegramClient with TGClient:

```python
# BEFORE:
client = TelegramClient(session_path, api_id, api_hash, proxy=proxy)
await client.connect()

# AFTER:
from tg_client import TGClient
tg = TGClient(session_name, api_id, api_hash, proxy=proxy)
await tg.connect()
client = tg.client
```

Key changes:
- Remove SQLite retry logic (no longer needed)
- Remove per-account locks (singleton handles this)
- Use TGClient for all client creation

### 4. `backend/api_server.py`

Update `UnifiedContactManager`:

```python
# In init_client():
from tg_client import TGClient

tg = TGClient(
    session_name=self.session_name,
    api_id=self.api_id,
    api_hash=self.api_hash,
    proxy=self.proxy,
    force_init=True  # Create fresh for operations
)
await tg.connect()
self.client = tg.client
self._tg_client = tg  # Keep reference
```

Update authentication endpoints:
- Use `force_init=True` for fresh auth clients
- Session auto-saves on `__aexit__`

### 5. `backend/inbox_manager.py`

Minimal changes - already uses GlobalConnectionManager which will be updated.

---

## Session File Format Change

| Aspect | Before (SQLite) | After (StringSession) |
|--------|-----------------|----------------------|
| File content | SQLite database (~94KB) | Base64 text string (~350 chars) |
| Lock files | `.session-wal`, `.session-shm`, `.session-journal` | None |
| Concurrent access | Database locks | Safe (atomic file I/O) |
| Example | Binary SQLite | `1BQANOTEuMTA4LjU2LjE1MQG7...` |

---

## Implementation Steps

### Step 1: Create TGClient Class
1. Create `backend/tg_client.py` with full implementation
2. Key methods:
   - `__new__` - singleton pattern
   - `__init__` - load session, create client
   - `connect()` / `disconnect()` - reference counting
   - `__aenter__` / `__aexit__` - context manager
   - `_load_session()` / `_save_session()` - file I/O

### Step 2: Create Migration Script
1. Create `backend/migrate_sessions.py`
2. Detect SQLite files in `sessions/`
3. Convert each to StringSession format
4. Backup originals to `sessions/backup_sqlite/`

### Step 3: Update GlobalConnectionManager
1. Import TGClient
2. Replace TelegramClient creation with TGClient
3. Remove SQLite retry logic
4. Simplify locking (singleton handles it)

### Step 4: Update UnifiedContactManager
1. Use TGClient in `init_client()`
2. Use `force_init=True` for operation-specific clients
3. Remove event loop workarounds
4. Keep reference to TGClient for cleanup

### Step 5: Update Auth Flow
1. Use TGClient with `force_init=True` for fresh auth
2. Session auto-saves after successful auth
3. Remove manual session saving code

### Step 6: Run Migration
1. Stop server
2. Run `python migrate_sessions.py --backup`
3. Verify session files converted
4. Start server
5. Test all accounts connect without re-auth

---

## Key Design Decisions

### Why StringSession?
- **No SQLite = No database locks**
- Simple atomic file writes
- Reference project uses this successfully

### Why Singleton Pattern?
- One client per account in memory
- Prevents duplicate connections
- Reference counting prevents premature disconnect

### Why `force_init=True` for Operations?
- Flask endpoints need isolated clients
- Prevents loop conflicts
- Each operation gets its own client lifecycle

---

## Testing Checklist

- [ ] Existing sessions migrate correctly
- [ ] Accounts connect without re-auth after migration
- [ ] InboxManager connects successfully
- [ ] Operations (scan, import, backup) work
- [ ] Multiple concurrent operations don't conflict
- [ ] Server restart preserves sessions
- [ ] Auth flow works for new accounts

---

## Rollback Plan

1. Keep SQLite backups in `sessions/backup_sqlite/`
2. Add `MATRIX_SESSION_FORMAT=sqlite` env var for fallback
3. TGClient can support both formats if needed
