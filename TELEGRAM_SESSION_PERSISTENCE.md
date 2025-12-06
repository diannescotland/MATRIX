# Telegram Session Persistence - Technical Documentation

This document explains how Telegram session persistence works in this codebase, focusing on how agents avoid re-authentication on subsequent logins and how database locks are prevented.

## Overview

The system uses **Telethon's StringSession** stored as plaintext files, combined with a **singleton pattern** for client instances. This approach:

1. Eliminates database locks entirely (no SQLite session files)
2. Avoids re-authentication by persisting session strings to disk
3. Manages concurrent access through reference counting

---

## Session Storage Architecture

### File-Based StringSession Storage

Unlike Telethon's default SQLite-based sessions, this codebase uses **StringSession** exported to plaintext files:

```
Project Root/
└── tg_sessions/
    ├── abc123def456.session    # Session file for agent abc123def456
    ├── xyz789ghi012.session    # Session file for agent xyz789ghi012
    └── ...
```

**Configuration** (`settings/common.py:301-302`):
```python
TG_SESSIONS_DIR = os.path.join(BASE_DIR.parent.parent, "tg_sessions")
os.makedirs(TG_SESSIONS_DIR, exist_ok=True)
```

**Path Resolution** (`utils/tg_agents.py:6-7`):
```python
def get_tg_session_file_path(inaid: str) -> str:
    return os.path.join(settings.TG_SESSIONS_DIR, f"{inaid}.session")
```

### Why StringSession Instead of SQLite?

| Aspect | SQLite Session (Default) | StringSession (This Codebase) |
|--------|--------------------------|-------------------------------|
| Storage | `.session` SQLite file | Plaintext string in `.session` file |
| Database Locks | **Yes** - SQLite file locking | **No** - Simple file read/write |
| Concurrent Access | Problematic | Safe with singleton pattern |
| Portability | File-based | Can be stored anywhere (DB, Redis, file) |

---

## How Re-Authentication is Avoided

### 1. Session Loading on Client Initialization

When a `TGClient` is instantiated, it checks for an existing session file (`tg/client.py:74-81`):

```python
def __init__(self, session_name: str, api_id: int, api_hash: str, ...):
    # ...

    # Load session if exists
    session_str = None
    if os.path.exists(session_name):
        with open(session_name, 'r') as f:
            session_str = f.read().strip()

    self.session = StringSession(session_str) if session_str else StringSession()
    self.client = TelegramClient(self.session, api_id, api_hash, **kwargs)
```

**Key Points:**
- If the session file exists, its content (the StringSession string) is loaded
- The StringSession contains all authentication data (auth keys, user info, DC info)
- No phone verification needed if session is valid

### 2. Session Saving on Context Exit

Sessions are automatically saved when exiting the async context manager (`tg/client.py:107-113`):

```python
async def __aexit__(self, exc_type, exc, tb):
    try:
        session_str = self.client.session.save()
        with open(self.session_name, "w") as f:
            f.write(session_str)
    finally:
        await self.disconnect()
```

**Key Points:**
- `session.save()` exports the current session state as a string
- The string is written atomically to the session file
- This happens automatically every time a client context exits

### 3. Authentication Flow (One-Time Only)

Authentication only happens during initial agent onboarding:

**Step 1: Create Draft** (`agents_manager_tg/views.py:35-54`)
- User provides phone_number, api_id, api_hash
- Draft is cached in Redis with TTL of 1 hour

**Step 2: Request Verification Code** (`agents_manager_tg/views.py:59-126`)
```python
async with TGClient(sessf, api_id, api_hash, ...) as client:
    return await client.send_code_request(phone_number)
```

**Step 3: Verify Code** (`agents_manager_tg/views.py:131-221`)
```python
async with TGClient(...) as client:
    await client.sign_in_with_code(phone_number, code, phone_code_hash, password)
    # Session is automatically saved on __aexit__
```

After verification, the session file contains full auth data. Future connections load this file and skip authentication entirely.

---

## Singleton Pattern - Preventing Multiple Instances

The `TGClient` implements a singleton pattern to ensure only one instance exists per session (`tg/client.py:21-50`):

```python
class TGClient:
    _instances: Dict[Tuple[str, int, str], 'TGClient'] = {}
    _last_active: Dict[Tuple[str, int, str], float] = {}
    _cleanup_interval = 7200  # 2 hours

    def __new__(cls, session_name: str, api_id: int, api_hash: str, ...):
        instance_key = (session_name, api_id, api_hash)

        # Start cleanup task if not running
        if cls._cleanup_task is None or cls._cleanup_task.done():
            cls._cleanup_task = asyncio.create_task(cls._cleanup_inactive_instances())

        # Return existing instance unless force_init=True
        if not kwargs.pop("force_init", False):
            if instance_key in cls._instances:
                instance = cls._instances[instance_key]
                cls._last_active[instance_key] = time.time()
                return instance

        # Create new instance
        instance = super().__new__(cls)
        cls._instances[instance_key] = instance
        cls._last_active[instance_key] = time.time()
        return instance
```

**Key Features:**
- Instance key is `(session_name, api_id, api_hash)` tuple
- Existing instances are reused, preventing duplicate connections
- `force_init=True` bypasses caching (used in Celery tasks)
- Last activity timestamp updated on each access

---

## Connection Reference Counting

To prevent premature disconnection when multiple code paths use the same client (`tg/client.py:115-132`):

```python
async def connect(self):
    """Connect only if not already connected."""
    instance_key = (self.session_name, self.api_id, self.api_hash)
    self._last_active[instance_key] = time.time()
    if not self.client.is_connected():
        await self.client.connect()
    self._connection_count += 1

async def disconnect(self, force: bool = False):
    """Disconnect only when no more references exist."""
    instance_key = (self.session_name, self.api_id, self.api_hash)
    self._last_active[instance_key] = time.time()
    if self._connection_count > 0:
        self._connection_count -= 1

    if (force or self._connection_count == 0) and self.client.is_connected():
        await self.client.disconnect()
```

**Key Points:**
- Each `connect()` increments `_connection_count`
- Each `disconnect()` decrements it
- Actual disconnection only happens when count reaches 0 (or `force=True`)

---

## Automatic Cleanup of Inactive Instances

A background task cleans up instances inactive for 2+ hours (`tg/client.py:83-101`):

```python
@classmethod
async def _cleanup_inactive_instances(cls):
    """Background task to clean up inactive instances."""
    while True:
        await asyncio.sleep(cls._cleanup_interval)  # 2 hours
        current_time = time.time()
        inactive_keys = [
            key for key, last_active in cls._last_active.items()
            if (current_time - last_active) >= cls._cleanup_interval
        ]

        for key in inactive_keys:
            if instance := cls._instances.get(key):
                if instance.client.is_connected():
                    await instance.client.disconnect()
                del cls._instances[key]
                del cls._last_active[key]
```

---

## Celery Task Isolation

Celery tasks use `force_init=True` to create isolated client instances (`core/tasks.py:99-106`):

```python
tg_client = TGClient(
    session_name=config["credentials"]["session"],
    api_id=config["credentials"]["api_id"],
    api_hash=config["credentials"]["api_hash"],
    proxy=proxy_url_to_dict(config["proxy"]),
    raise_exceptions=True,
    force_init=True,  # <-- Create new instance, don't reuse
)
```

**Why `force_init=True` in Celery?**
- Celery workers run in separate processes
- The singleton pattern's `_instances` dict is process-local
- `force_init=True` ensures a fresh connection per task
- Prevents stale connections from process forking

---

## Why This Approach Avoids Database Locks

### The Problem with SQLite Sessions

Telethon's default SQLite session (`{name}.session` file) uses SQLite database:
- SQLite has file-level locking
- Concurrent reads are fine, but writes lock the file
- Multiple processes/threads accessing same session = lock contention
- Results in `database is locked` errors

### The Solution: StringSession + File I/O

This codebase avoids the problem entirely:

1. **No SQLite**: Session is a simple string, not a database
2. **Atomic Writes**: Session is written in one operation on context exit
3. **Singleton Pattern**: Only one instance per session in memory
4. **No Concurrent Writes**: Reference counting ensures clean disconnect

---

## Session Lifecycle Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                     FIRST TIME (Onboarding)                      │
├─────────────────────────────────────────────────────────────────┤
│  1. User provides: phone_number, api_id, api_hash               │
│  2. TGClient created with empty StringSession()                 │
│  3. send_code_request() → Telegram sends SMS/call               │
│  4. User enters code                                            │
│  5. sign_in_with_code() → Telethon authenticates                │
│  6. __aexit__ saves session string to file                      │
│                                                                  │
│  File created: tg_sessions/{inaid}.session                      │
│  Content: "1BQANOTEuMTA4LjU2LjE1MQG7ByT..."                     │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    SUBSEQUENT LOGINS                             │
├─────────────────────────────────────────────────────────────────┤
│  1. TGClient checks: os.path.exists(session_name)? → YES        │
│  2. Reads session string from file                               │
│  3. Creates StringSession(session_str)                          │
│  4. TelegramClient connects with existing auth                  │
│  5. No phone verification needed!                                │
│  6. __aexit__ saves (possibly updated) session                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `tg/client.py` | TGClient wrapper with singleton pattern and session persistence |
| `utils/tg_agents.py` | Session file path utility |
| `settings/common.py:299-302` | Session directory configuration |
| `agents_manager_tg/views.py` | Authentication flow (onboarding) |
| `core/tasks.py` | Celery tasks with `force_init=True` |

---

## Applying This to Your Project

If you're experiencing database lock issues with Telethon sessions:

### Option 1: Switch to StringSession (Recommended)

```python
from telethon.sessions import StringSession

# Instead of:
# client = TelegramClient('session_name', api_id, api_hash)

# Use:
session_str = load_from_file_or_db()  # Your storage
client = TelegramClient(StringSession(session_str), api_id, api_hash)

# After operations, save:
new_session_str = client.session.save()
save_to_file_or_db(new_session_str)
```

### Option 2: Implement Singleton Pattern

Ensure only one client instance per session exists in your application.

### Option 3: Use Connection Pooling

Track connection counts and only disconnect when all users are done.

---

## Summary

| Technique | How It Helps |
|-----------|--------------|
| **StringSession** | No SQLite = No database locks |
| **File-based storage** | Simple atomic writes |
| **Singleton pattern** | One instance per session |
| **Reference counting** | Prevents premature disconnect |
| **Auto cleanup** | Memory leak prevention |
| **force_init in Celery** | Process isolation |
