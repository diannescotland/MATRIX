# MATRIX Inbox Management System - Implementation Plan

**Created:** December 5, 2025
**Status:** Ready for Implementation
**Estimated Effort:** ~1500 lines backend + ~1000 lines frontend

---

## Quick Summary

Build a real-time inbox management system with:
- Persistent Telegram connections (auto-connect on startup)
- Full message history storage
- Read receipts ("they read my message")
- Typing indicators + online/offline status
- Message sending with rate limiting
- Campaign-based metrics
- Replace existing `scan_for_replies` entirely

---

## User Decisions

| Decision | Choice |
|----------|--------|
| Architecture | Separate file `backend/inbox_manager.py` |
| Reply Scanning | Replace `scan_for_replies` entirely |
| Connection | Auto-connect ALL active accounts on startup |
| Retention | Keep messages forever |
| Campaigns | Auto-generate from CSV filename |
| Extra Events | Include typing + online status |
| Navigation | Inbox page after Dashboard (position 2) |
| Message Sending | Yes, with rate limiting per DM_SYSTEM_LOGIC.md |

### Technical Decisions (Sync & Scheduling)

| Decision | Choice | Details |
|----------|--------|---------|
| Dialog Sync Interval | **Fixed 30 min** | Hard-coded, runs every 30 minutes |
| Gap Detection Threshold | **Gap >= 2** | Backfill triggered if 2+ messages missing |
| Deleted Messages | **Soft delete** | Mark `is_deleted=TRUE`, show `[Message deleted]` in UI |
| Full Sync Interval | **Every 12 hours** | Complete data integrity check |
| Read Receipts Delivery | **WebSocket only** | Real-time push, no polling fallback |
| Task Scheduler | **Celery + Redis** | Reliable distributed task queue |

---

## Phase 1: Database Schema

### File: `backend/account_manager.py`

Add `init_inbox_tables()` function. Use the existing ad-hoc migration pattern (try/except for ALTER TABLE).

```sql
-- ============================================================================
-- CONVERSATIONS: Track all private chats per account
-- ============================================================================
CREATE TABLE IF NOT EXISTS inbox_conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_phone TEXT NOT NULL,
    peer_id INTEGER NOT NULL,

    -- Peer info (cached)
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    peer_phone TEXT,
    access_hash INTEGER,

    -- MATRIX contact integration
    is_matrix_contact BOOLEAN DEFAULT FALSE,
    contact_type TEXT,                        -- 'dev', 'kol', NULL
    contact_status TEXT,                      -- 'blue', 'yellow', NULL
    campaign_id TEXT,

    -- Last message state (for gap detection)
    last_msg_id INTEGER DEFAULT 0,
    last_msg_date TIMESTAMP,
    last_msg_text TEXT,
    last_msg_from_id INTEGER,
    last_msg_is_outgoing BOOLEAN,

    -- Read state
    our_last_read_msg_id INTEGER DEFAULT 0,
    their_last_read_msg_id INTEGER DEFAULT 0,
    unread_count INTEGER DEFAULT 0,

    -- Sync metadata
    last_sync TIMESTAMP,
    needs_backfill BOOLEAN DEFAULT FALSE,
    backfill_from_msg_id INTEGER,

    -- Flags
    is_archived BOOLEAN DEFAULT FALSE,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(account_phone, peer_id)
);

CREATE INDEX IF NOT EXISTS idx_inbox_conv_account ON inbox_conversations(account_phone);
CREATE INDEX IF NOT EXISTS idx_inbox_conv_matrix ON inbox_conversations(is_matrix_contact);
CREATE INDEX IF NOT EXISTS idx_inbox_conv_campaign ON inbox_conversations(campaign_id);

-- ============================================================================
-- MESSAGES: Full message history
-- ============================================================================
CREATE TABLE IF NOT EXISTS inbox_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_phone TEXT NOT NULL,
    peer_id INTEGER NOT NULL,
    msg_id INTEGER NOT NULL,

    -- Message content
    from_id INTEGER NOT NULL,
    is_outgoing BOOLEAN NOT NULL,
    text TEXT,
    date TIMESTAMP NOT NULL,

    -- Reply context
    reply_to_msg_id INTEGER,

    -- Media
    media_type TEXT,                          -- 'photo', 'document', 'video', etc.
    media_file_id TEXT,

    -- Edit/delete tracking
    edit_date TIMESTAMP,
    is_deleted BOOLEAN DEFAULT FALSE,

    -- Read status (outgoing only)
    is_read BOOLEAN DEFAULT FALSE,
    read_at TIMESTAMP,

    -- Sync metadata
    synced_via TEXT,                          -- 'event', 'dialog', 'backfill'

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(account_phone, peer_id, msg_id)
);

CREATE INDEX IF NOT EXISTS idx_inbox_msg_conv ON inbox_messages(account_phone, peer_id);
CREATE INDEX IF NOT EXISTS idx_inbox_msg_date ON inbox_messages(account_phone, peer_id, date DESC);

-- ============================================================================
-- EVENTS: Event log for notifications and audit
-- ============================================================================
CREATE TABLE IF NOT EXISTS inbox_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_phone TEXT NOT NULL,
    peer_id INTEGER NOT NULL,

    event_type TEXT NOT NULL,                 -- 'new_message', 'message_read', 'first_reply', etc.
    event_data TEXT,                          -- JSON string
    msg_id INTEGER,
    campaign_id TEXT,

    notified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_inbox_events_type ON inbox_events(event_type);

-- ============================================================================
-- CAMPAIGNS: Track outreach campaigns for metrics
-- ============================================================================
CREATE TABLE IF NOT EXISTS inbox_campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    contact_type TEXT,                        -- 'dev', 'kol', 'mixed'

    total_contacts INTEGER DEFAULT 0,
    total_reached INTEGER DEFAULT 0,
    total_replies INTEGER DEFAULT 0,
    total_read INTEGER DEFAULT 0,

    reply_rate REAL DEFAULT 0,
    read_rate REAL DEFAULT 0,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- CONNECTION_STATE: Track connection status per account
-- ============================================================================
CREATE TABLE IF NOT EXISTS inbox_connection_state (
    account_phone TEXT PRIMARY KEY,

    is_connected BOOLEAN DEFAULT FALSE,
    connected_at TIMESTAMP,
    last_disconnect_at TIMESTAMP,
    reconnect_attempts INTEGER DEFAULT 0,

    last_dialog_sync TIMESTAMP,
    last_full_sync TIMESTAMP,
    dialogs_count INTEGER DEFAULT 0,
    messages_count INTEGER DEFAULT 0,

    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- DM_HISTORY: Track sent DMs for duplicate detection & rate limiting
-- ============================================================================
CREATE TABLE IF NOT EXISTS inbox_dm_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_phone TEXT NOT NULL,
    peer_id INTEGER NOT NULL,
    campaign_id TEXT,

    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    msg_id INTEGER,

    UNIQUE(account_phone, peer_id, campaign_id)
);

CREATE INDEX IF NOT EXISTS idx_dm_history_account ON inbox_dm_history(account_phone);
```

**Testing Checkpoint:** Start server, verify tables created, existing features still work.

---

## Phase 2: Core Backend

### New File: `backend/inbox_manager.py` (~1500 lines)

#### Class 1: ConnectionPool

```python
class ConnectionPool:
    """
    Manages persistent TelegramClient connections for multiple accounts.

    Key responsibilities:
    - One TelegramClient per account
    - Auto-reconnect on disconnect
    - Event handler registration
    - Health monitoring
    """

    def __init__(self, socketio: SocketIO, max_connections: int = 100):
        self._clients: Dict[str, TelegramClient] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._connected: Set[str] = set()
        self._event_processor: EventProcessor = None
        self._loop: asyncio.AbstractEventLoop = None
        self._loop_thread: threading.Thread = None

    async def connect_account(self, phone: str, api_id: int, api_hash: str,
                              session_path: str, proxy: str = None) -> bool:
        """Connect account and register event handlers"""

    async def disconnect_account(self, phone: str) -> None:
        """Gracefully disconnect account"""

    async def reconnect_account(self, phone: str) -> bool:
        """Reconnect a disconnected account"""

    def get_client(self, phone: str) -> Optional[TelegramClient]:
        """Get client for account (if connected)"""

    def is_connected(self, phone: str) -> bool:
        """Check if account is connected"""

    def get_connected_accounts(self) -> List[str]:
        """Get list of connected account phones"""

    async def shutdown(self) -> None:
        """Gracefully shutdown all connections"""
```

#### Class 2: EventProcessor

```python
class EventProcessor:
    """
    Processes Telegram events and persists to database.

    Handles:
    - NewMessage (incoming/outgoing)
    - MessageRead (outbox read receipts)
    - MessageEdited
    - MessageDeleted
    - UserUpdate (online/offline)
    - UpdateUserTyping
    """

    def __init__(self, socketio: SocketIO, db_path: str):
        self._socketio = socketio
        self._db_path = db_path

    async def handle_new_message(self, account_phone: str, event, incoming: bool) -> None:
        """
        1. Create/update conversation record
        2. Insert message record
        3. Check if first reply from blue contact → trigger 🔵→🟡
        4. Emit WebSocket notification
        """

    async def handle_message_read(self, account_phone: str, event) -> None:
        """
        Telethon: UpdateReadHistoryOutbox
        - event.max_id = highest msg_id they've read

        1. Update messages.is_read = TRUE for all msg_id <= max_id
        2. Update conversation.their_last_read_msg_id
        3. Emit WebSocket notification
        """

    async def handle_user_status(self, account_phone: str, event) -> None:
        """
        Track online/offline status.
        Emit inbox:user_status event.
        """

    async def handle_typing(self, account_phone: str, event) -> None:
        """
        Handle UpdateUserTyping.
        Emit inbox:typing event (expires after 5s).
        """
```

#### Class 3: SyncEngine

```python
class SyncEngine:
    """
    Handles periodic synchronization and gap detection.

    Sync Strategy:
    1. Every 30 min: Fetch all dialogs (1 API call)
    2. For each dialog, compare last_msg_id with database
    3. gap == 0: Skip (no new messages)
    4. gap == 1: Use message from dialog response (0 extra API calls)
    5. gap >= 2: Mark needs_backfill, schedule backfill task
    """

    # Sync intervals (seconds)
    DIALOG_SYNC_INTERVAL = 30 * 60      # 30 minutes
    FULL_SYNC_INTERVAL = 12 * 60 * 60   # 12 hours
    BACKFILL_CHECK_INTERVAL = 5 * 60    # 5 minutes

    async def sync_dialogs(self, account_phone: str) -> SyncResult:
        """
        Gap detection algorithm with EXPLICIT logic:
        """
        client = self._pool.get_client(account_phone)
        if not client:
            raise ValueError(f"Account {account_phone} not connected")

        result = SyncResult()

        # 1. Fetch all dialogs (SINGLE API call)
        dialogs = await client.get_dialogs()
        result.dialogs_fetched = len(dialogs)

        for dialog in dialogs:
            # Skip groups/channels - only private chats
            if not dialog.is_user:
                continue

            peer_id = dialog.entity.id
            dialog_msg = dialog.message
            if not dialog_msg:
                continue

            dialog_last_msg_id = dialog_msg.id

            # Get current state from database
            db_conv = self._get_conversation(account_phone, peer_id)
            db_last_msg_id = db_conv['last_msg_id'] if db_conv else 0

            # ========== GAP DETECTION LOGIC ==========
            gap = dialog_last_msg_id - db_last_msg_id

            if gap == 0:
                # No new messages - SKIP
                result.skipped += 1
                continue

            elif gap == 1:
                # Single new message - use from dialog (0 extra API calls)
                await self._insert_message_from_dialog(account_phone, peer_id, dialog_msg)
                await self._update_conversation_last_msg(account_phone, peer_id, dialog_msg)
                result.synced += 1

            elif gap >= 2:
                # MULTIPLE MESSAGES MISSING - mark for backfill
                # This is the key gap >= 2 condition
                await self._mark_needs_backfill(
                    account_phone,
                    peer_id,
                    backfill_from_msg_id=db_last_msg_id,
                    backfill_to_msg_id=dialog_last_msg_id
                )
                result.gaps_detected += 1
                logger.info(f"Gap detected for {peer_id}: {db_last_msg_id} -> {dialog_last_msg_id} (gap={gap})")

            elif gap < 0:
                # Messages were DELETED (dialog_msg_id < db_last_msg_id)
                await self._handle_deleted_messages(account_phone, peer_id, dialog_last_msg_id)
                result.deletions_detected += 1

        # Update sync timestamp
        await self._update_sync_state(account_phone, 'dialog_sync')
        return result

    async def backfill_conversation(self, account_phone: str, peer_id: int,
                                    from_msg_id: int, to_msg_id: int = None,
                                    limit: int = 100) -> int:
        """
        Fetch messages to fill gap - CONDITIONAL fetch only when gap >= 2
        """
        client = self._pool.get_client(account_phone)

        # Fetch messages NEWER than from_msg_id
        messages = await client.get_messages(
            peer_id,
            min_id=from_msg_id,  # Fetch messages with id > from_msg_id
            max_id=to_msg_id + 1 if to_msg_id else 0,  # Up to to_msg_id
            limit=limit
        )

        inserted = 0
        latest_msg_id = from_msg_id

        for msg in messages:
            if msg.id > latest_msg_id:
                latest_msg_id = msg.id

            # Insert message (ON CONFLICT IGNORE for duplicates)
            try:
                await self._insert_message(account_phone, peer_id, msg, synced_via='backfill')
                inserted += 1
            except sqlite3.IntegrityError:
                pass  # Duplicate, skip

        # Clear backfill flag and update last_msg_id
        await self._clear_backfill_flag(account_phone, peer_id, latest_msg_id)

        logger.info(f"Backfilled {inserted} messages for {peer_id}")
        return inserted

    async def process_pending_backfills(self, account_phone: str) -> int:
        """
        Process all conversations marked needs_backfill=TRUE
        Called every 5 minutes by scheduler
        """
        pending = self._get_conversations_needing_backfill(account_phone)
        total_backfilled = 0

        for conv in pending:
            try:
                count = await self.backfill_conversation(
                    account_phone,
                    conv['peer_id'],
                    from_msg_id=conv['backfill_from_msg_id'],
                    to_msg_id=conv.get('backfill_to_msg_id')
                )
                total_backfilled += count
            except Exception as e:
                logger.error(f"Backfill failed for {conv['peer_id']}: {e}")

        return total_backfilled

    async def full_sync(self, account_phone: str) -> FullSyncResult:
        """
        Complete sync for data integrity (run every 12-24h via scheduler)
        """
        result = FullSyncResult()

        # 1. Sync all dialogs
        sync_result = await self.sync_dialogs(account_phone)
        result.dialogs_synced = sync_result.dialogs_fetched

        # 2. Process ALL pending backfills
        result.messages_backfilled = await self.process_pending_backfills(account_phone)

        # 3. Verify message counts (optional integrity check)
        result.integrity_ok = await self._verify_message_counts(account_phone)

        # 4. Update metrics
        await self._update_account_metrics(account_phone)

        # Update full sync timestamp
        await self._update_sync_state(account_phone, 'full_sync')

        return result
```

### Deleted Message Handling Strategy

```python
async def _handle_deleted_messages(self, account_phone: str, peer_id: int,
                                    new_last_msg_id: int):
    """
    Strategy: SOFT DELETE - mark as deleted, don't remove from DB

    Why soft delete:
    - Preserves audit trail
    - Allows showing "[Message deleted]" in UI
    - Can be cleaned up later by retention policy
    """
    # Mark all messages with id > new_last_msg_id as deleted
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE inbox_messages
        SET is_deleted = TRUE,
            deleted_at = CURRENT_TIMESTAMP,
            text = '[Message deleted]'  -- Optional: clear content
        WHERE account_phone = ?
          AND peer_id = ?
          AND msg_id > ?
    ''', (account_phone, peer_id, new_last_msg_id))

    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()

    logger.info(f"Soft-deleted {deleted_count} messages for {peer_id}")
    return deleted_count
```

---

## Phase 2C: Celery + Redis Setup

### Prerequisites

```bash
# Install Redis (Windows)
# Option 1: Use WSL
wsl --install
sudo apt install redis-server
sudo service redis-server start

# Option 2: Use Docker
docker run -d -p 6379:6379 --name redis redis:alpine

# Install Python dependencies
pip install celery redis
```

### New File: `backend/celery_app.py`

```python
from celery import Celery
from celery.schedules import crontab
import os

# Redis connection URL
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

# Create Celery app
celery_app = Celery(
    'matrix_inbox',
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=['backend.inbox_tasks']
)

# Celery configuration
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,

    # Task result expiration (24 hours)
    result_expires=86400,

    # Retry settings
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)

# Beat schedule (periodic tasks)
celery_app.conf.beat_schedule = {
    # Dialog sync every 30 minutes
    'dialog-sync-every-30-min': {
        'task': 'backend.inbox_tasks.sync_all_dialogs',
        'schedule': 30 * 60,  # 30 minutes in seconds
    },

    # Full sync every 12 hours
    'full-sync-every-12-hours': {
        'task': 'backend.inbox_tasks.full_sync_all_accounts',
        'schedule': 12 * 60 * 60,  # 12 hours in seconds
    },

    # Process pending backfills every 5 minutes
    'process-backfills-every-5-min': {
        'task': 'backend.inbox_tasks.process_all_backfills',
        'schedule': 5 * 60,  # 5 minutes in seconds
    },
}
```

### New File: `backend/inbox_tasks.py`

```python
from celery_app import celery_app
from inbox_manager import InboxManager
from account_manager import get_active_accounts
import asyncio
import logging

logger = logging.getLogger(__name__)

# Shared inbox manager instance (created on worker startup)
_inbox_manager = None

def get_inbox_manager():
    global _inbox_manager
    if _inbox_manager is None:
        from api_server import socketio
        _inbox_manager = InboxManager(socketio)
    return _inbox_manager

# ============================================================================
# PERIODIC TASKS
# ============================================================================

@celery_app.task(bind=True, max_retries=3)
def sync_all_dialogs(self):
    """
    Celery task: Sync dialogs for ALL connected accounts.
    Runs every 30 minutes via beat schedule.
    """
    logger.info("🔄 Starting scheduled dialog sync for all accounts")

    manager = get_inbox_manager()
    results = {}

    for phone in manager._pool.get_connected_accounts():
        try:
            # Run async sync in event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(manager._sync_engine.sync_dialogs(phone))
            loop.close()

            results[phone] = {
                'status': 'success',
                'dialogs': result.dialogs_fetched,
                'gaps': result.gaps_detected
            }
            logger.info(f"✅ Dialog sync complete for {phone}: {result.synced} synced, {result.gaps_detected} gaps")

        except Exception as e:
            logger.error(f"❌ Dialog sync failed for {phone}: {e}")
            results[phone] = {'status': 'error', 'error': str(e)}

    return results


@celery_app.task(bind=True, max_retries=3)
def full_sync_all_accounts(self):
    """
    Celery task: Full deep sync for ALL connected accounts.
    Runs every 12 hours via beat schedule.
    """
    logger.info("🔄 Starting scheduled FULL sync for all accounts")

    manager = get_inbox_manager()
    results = {}

    for phone in manager._pool.get_connected_accounts():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(manager._sync_engine.full_sync(phone))
            loop.close()

            results[phone] = {
                'status': 'success',
                'dialogs_synced': result.dialogs_synced,
                'messages_backfilled': result.messages_backfilled
            }
            logger.info(f"✅ Full sync complete for {phone}")

        except Exception as e:
            logger.error(f"❌ Full sync failed for {phone}: {e}")
            results[phone] = {'status': 'error', 'error': str(e)}

    return results


@celery_app.task(bind=True, max_retries=3)
def process_all_backfills(self):
    """
    Celery task: Process pending backfills for ALL accounts.
    Runs every 5 minutes via beat schedule.
    """
    logger.info("🔄 Processing pending backfills")

    manager = get_inbox_manager()
    total_backfilled = 0

    for phone in manager._pool.get_connected_accounts():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            count = loop.run_until_complete(
                manager._sync_engine.process_pending_backfills(phone)
            )
            loop.close()

            total_backfilled += count
            if count > 0:
                logger.info(f"✅ Backfilled {count} messages for {phone}")

        except Exception as e:
            logger.error(f"❌ Backfill failed for {phone}: {e}")

    return {'total_backfilled': total_backfilled}


# ============================================================================
# ON-DEMAND TASKS (triggered by API)
# ============================================================================

@celery_app.task(bind=True)
def sync_account_dialogs(self, phone: str):
    """On-demand dialog sync for specific account"""
    manager = get_inbox_manager()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(manager._sync_engine.sync_dialogs(phone))
    loop.close()

    return {
        'phone': phone,
        'dialogs_fetched': result.dialogs_fetched,
        'synced': result.synced,
        'gaps_detected': result.gaps_detected
    }


@celery_app.task(bind=True)
def backfill_conversation_task(self, phone: str, peer_id: int, from_msg_id: int):
    """On-demand backfill for specific conversation"""
    manager = get_inbox_manager()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    count = loop.run_until_complete(
        manager._sync_engine.backfill_conversation(phone, peer_id, from_msg_id)
    )
    loop.close()

    return {
        'phone': phone,
        'peer_id': peer_id,
        'messages_backfilled': count
    }
```

### Running Celery

```bash
# Terminal 1: Start Redis (if using Docker)
docker start redis

# Terminal 2: Start Celery Worker
cd "C:\Users\LENOVO\Documents\Toshi System\MATRIX"
celery -A backend.celery_app worker --loglevel=info --pool=solo

# Terminal 3: Start Celery Beat (scheduler)
celery -A backend.celery_app beat --loglevel=info

# Terminal 4: Start Flask API
python backend/api_server.py
```

### WebSocket-Only Read Receipts (No Polling)

```python
# In EventProcessor - read receipts are ONLY delivered via WebSocket
# No REST API endpoint for polling read status

async def handle_message_read(self, account_phone: str, event):
    """
    Handle outbox read receipt - WebSocket ONLY delivery.

    NOTE: Read receipts are real-time only. If frontend disconnects,
    it will NOT receive missed read receipts. This is by design to
    minimize API calls and database queries.
    """
    peer_id = event.peer.user_id
    max_read_id = event.max_id

    # Update database
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE inbox_messages
        SET is_read = TRUE, read_at = CURRENT_TIMESTAMP
        WHERE account_phone = ? AND peer_id = ? AND is_outgoing = TRUE
          AND msg_id <= ? AND is_read = FALSE
    ''', (account_phone, peer_id, max_read_id))

    read_count = cursor.rowcount

    # Update conversation
    cursor.execute('''
        UPDATE inbox_conversations
        SET their_last_read_msg_id = ?
        WHERE account_phone = ? AND peer_id = ?
    ''', (max_read_id, account_phone, peer_id))

    conn.commit()
    conn.close()

    # Emit via WebSocket ONLY (no REST API for this)
    self._socketio.emit('inbox:message_read', {
        'account_phone': account_phone,
        'peer_id': peer_id,
        'max_read_id': max_read_id,
        'read_count': read_count,
        'timestamp': datetime.now().isoformat()
    }, room=f"inbox:{account_phone}")

    # NO polling endpoint - if client missed it, they check on next message load
```

#### Class 4: DMRateLimiter

```python
class DMRateLimiter:
    """
    Rate limiting + duplicate detection for sending DMs.
    Based on DM_SYSTEM_LOGIC.md pattern.
    """

    # Per-account limits
    DM_LIMIT_PER_PERIOD = 40        # Max DMs per 24h period
    DM_PERIOD_HOURS = 24
    MIN_DELAY_BETWEEN_DMS = 30      # Seconds

    # Reply rate bypass threshold
    REPLY_RATE_BYPASS_THRESHOLD = 15.0  # If reply rate > 15%, allow more DMs

    def __init__(self, account_phone: str):
        self.account_phone = account_phone
        self.sent_to_ids: Set[int] = set()  # Layer 1: In-memory
        self.dm_count = 0
        self.limit_reset_time = None
        self.last_dm_time = None

    def can_send(self, peer_id: int) -> Tuple[bool, str]:
        """
        Check if we can send DM.
        Returns (can_send, reason)
        """
        # Check rate limit
        # Check duplicate (3 layers)
        # Check min delay

    def record_sent(self, peer_id: int, msg_id: int, campaign_id: str = None):
        """Record successful DM in all layers"""

    def get_status(self) -> Dict:
        """Get rate limit status for UI"""
```

#### Class 5: InboxManager (Main Orchestrator)

```python
class InboxManager:
    """
    Main entry point for inbox management system.
    """

    def __init__(self, socketio: SocketIO):
        self._socketio = socketio
        self._pool = ConnectionPool(socketio)
        self._processor = EventProcessor(socketio, DB_PATH)
        self._sync_engine = SyncEngine(self._pool, self._processor, DB_PATH)
        self._rate_limiters: Dict[str, DMRateLimiter] = {}
        self._running = False

    async def start(self) -> None:
        """Start inbox manager and background tasks"""

    async def stop(self) -> None:
        """Stop inbox manager and cleanup"""

    async def connect_all_active_accounts(self) -> Dict[str, bool]:
        """Connect all accounts with status='active'"""

    # Query methods
    async def get_conversations(self, phone: str, limit: int = 50) -> List[Dict]:
    async def get_messages(self, phone: str, peer_id: int, limit: int = 50) -> List[Dict]:

    # Send message
    async def send_message(self, phone: str, peer_id: int, text: str) -> Dict:
        """Send message with rate limiting"""

    # Sync triggers
    async def trigger_dialog_sync(self, phone: str) -> SyncResult:
    async def trigger_full_sync(self, phone: str) -> FullSyncResult:
```

#### Event Handler Registration

```python
async def _register_event_handlers(self, client: TelegramClient, phone: str):
    """Register Telethon event handlers for account"""

    @client.on(events.NewMessage(incoming=True))
    async def on_incoming_message(event):
        await self._processor.handle_new_message(phone, event, incoming=True)

    @client.on(events.NewMessage(outgoing=True))
    async def on_outgoing_message(event):
        await self._processor.handle_new_message(phone, event, incoming=False)

    @client.on(events.MessageRead(inbox=False))  # inbox=False = outbox read
    async def on_message_read(event):
        # This fires when THEY read OUR message
        await self._processor.handle_message_read(phone, event)

    @client.on(events.MessageEdited())
    async def on_message_edited(event):
        await self._processor.handle_message_edited(phone, event)

    @client.on(events.UserUpdate())
    async def on_user_status(event):
        await self._processor.handle_user_status(phone, event)

    @client.on(events.Raw())
    async def on_raw_update(event):
        if isinstance(event, UpdateUserTyping):
            await self._processor.handle_typing(phone, event)
```

#### Background Scheduler

```python
async def _start_scheduler(self):
    """Start background sync tasks"""

    # Dialog sync every 30 minutes
    async def dialog_sync_task():
        while self._running:
            for phone in self._pool.get_connected_accounts():
                try:
                    await self._sync_engine.sync_dialogs(phone)
                except Exception as e:
                    logger.error(f"Dialog sync failed for {phone}: {e}")
            await asyncio.sleep(30 * 60)

    # Full sync every 12 hours
    async def full_sync_task():
        while self._running:
            await asyncio.sleep(12 * 60 * 60)
            for phone in self._pool.get_connected_accounts():
                try:
                    await self._sync_engine.full_sync(phone)
                except Exception as e:
                    logger.error(f"Full sync failed for {phone}: {e}")

    # Process backfills every 5 minutes
    async def backfill_task():
        while self._running:
            for phone in self._pool.get_connected_accounts():
                await self._sync_engine.process_pending_backfills(phone)
            await asyncio.sleep(5 * 60)

    asyncio.create_task(dialog_sync_task())
    asyncio.create_task(full_sync_task())
    asyncio.create_task(backfill_task())
```

**Testing Checkpoint:** Unit test each class, verify event handling with mock events.

---

## Phase 3: API Endpoints

### File: `backend/api_server.py`

#### Add Global Instance (after line 61)

```python
from inbox_manager import InboxManager

# Global inbox manager instance
inbox_manager: Optional[InboxManager] = None
```

#### New API Endpoints

```python
# ============================================================================
# INBOX CONNECTION ENDPOINTS
# ============================================================================

@app.route('/api/inbox/connect', methods=['POST'])
def inbox_connect():
    """Connect single account to inbox system"""
    phone = request.json.get('phone')
    # ...

@app.route('/api/inbox/connect-all', methods=['POST'])
def inbox_connect_all():
    """Connect all active accounts"""
    # ...

@app.route('/api/inbox/disconnect', methods=['POST'])
def inbox_disconnect():
    """Disconnect account from inbox system"""
    # ...

@app.route('/api/inbox/connection-status', methods=['GET'])
def inbox_connection_status():
    """Get connection states for all accounts"""
    # ...

# ============================================================================
# INBOX CONVERSATION ENDPOINTS
# ============================================================================

@app.route('/api/inbox/<phone>/conversations', methods=['GET'])
def inbox_conversations(phone):
    """Get conversations for account"""
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)
    unread_only = request.args.get('unread_only', 'false') == 'true'
    matrix_only = request.args.get('matrix_only', 'false') == 'true'
    # ...

@app.route('/api/inbox/<phone>/conversations/<int:peer_id>', methods=['GET'])
def inbox_conversation_detail(phone, peer_id):
    """Get single conversation details"""
    # ...

@app.route('/api/inbox/<phone>/conversations/<int:peer_id>/messages', methods=['GET'])
def inbox_messages(phone, peer_id):
    """Get messages for conversation"""
    limit = request.args.get('limit', 50, type=int)
    before_msg_id = request.args.get('before_msg_id', type=int)
    # ...

# ============================================================================
# INBOX SEND MESSAGE ENDPOINT
# ============================================================================

@app.route('/api/inbox/<phone>/send', methods=['POST'])
def inbox_send_message(phone):
    """Send message (rate limited)"""
    peer_id = request.json.get('peer_id')
    text = request.json.get('text')
    # Check rate limit, send, return result
    # ...

@app.route('/api/inbox/<phone>/rate-limit', methods=['GET'])
def inbox_rate_limit_status(phone):
    """Get rate limit status for account"""
    # Returns: remaining DMs, reset time, etc.
    # ...

# ============================================================================
# INBOX SYNC ENDPOINTS
# ============================================================================

@app.route('/api/inbox/<phone>/sync/dialogs', methods=['POST'])
def inbox_sync_dialogs(phone):
    """Trigger dialog sync"""
    # ...

@app.route('/api/inbox/<phone>/sync/full', methods=['POST'])
def inbox_sync_full(phone):
    """Trigger full sync"""
    # ...

# ============================================================================
# INBOX METRICS ENDPOINTS
# ============================================================================

@app.route('/api/inbox/metrics', methods=['GET'])
def inbox_metrics():
    """Get inbox metrics"""
    phone = request.args.get('phone')
    campaign_id = request.args.get('campaign_id')
    # ...

@app.route('/api/inbox/campaigns/<campaign_id>/metrics', methods=['GET'])
def inbox_campaign_metrics(campaign_id):
    """Get metrics for specific campaign"""
    # ...
```

#### WebSocket Events

```python
# ============================================================================
# INBOX WEBSOCKET HANDLERS
# ============================================================================

@socketio.on('inbox:subscribe')
def handle_inbox_subscribe(data):
    """Subscribe to inbox events for an account"""
    phone = data.get('phone')
    join_room(f"inbox:{phone}")
    emit('inbox:subscribed', {'phone': phone})

@socketio.on('inbox:unsubscribe')
def handle_inbox_unsubscribe(data):
    """Unsubscribe from inbox events"""
    phone = data.get('phone')
    leave_room(f"inbox:{phone}")

@socketio.on('inbox:subscribe_all')
def handle_inbox_subscribe_all():
    """Subscribe to all connected accounts"""
    if inbox_manager:
        for phone in inbox_manager._pool.get_connected_accounts():
            join_room(f"inbox:{phone}")
```

**Server-to-Client Events (emitted from EventProcessor):**

```python
# inbox:new_message - New message received/sent
socketio.emit('inbox:new_message', {
    'account_phone': phone,
    'peer_id': peer_id,
    'message': { msg_id, from_id, is_outgoing, text, date },
    'conversation': { unread_count, last_msg_text }
}, room=f"inbox:{phone}")

# inbox:message_read - They read our message
socketio.emit('inbox:message_read', {
    'account_phone': phone,
    'peer_id': peer_id,
    'max_read_id': max_id,
    'read_count': count,
    'timestamp': datetime.now().isoformat()
}, room=f"inbox:{phone}")

# inbox:first_reply - First reply from blue contact (🔵→🟡)
socketio.emit('inbox:first_reply', {
    'account_phone': phone,
    'peer_id': peer_id,
    'contact_type': 'dev',  # or 'kol'
    'campaign_id': campaign_id,
    'message': {...}
}, room=f"inbox:{phone}")

# inbox:typing - User is typing
socketio.emit('inbox:typing', {
    'account_phone': phone,
    'peer_id': peer_id,
    'is_typing': True
}, room=f"inbox:{phone}")

# inbox:user_status - User online/offline
socketio.emit('inbox:user_status', {
    'account_phone': phone,
    'peer_id': peer_id,
    'online': True,
    'last_seen': datetime.now().isoformat()
}, room=f"inbox:{phone}")

# inbox:connection_status - Account connected/disconnected
socketio.emit('inbox:connection_status', {
    'account_phone': phone,
    'connected': True,
    'event': 'connected'  # or 'disconnected', 'reconnecting'
})
```

#### Startup Hook

Modify `if __name__ == '__main__':` section:

```python
# Initialize inbox manager
try:
    inbox_manager = InboxManager(socketio)

    def start_inbox():
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(inbox_manager.start())
        loop.run_until_complete(inbox_manager.connect_all_active_accounts())
        loop.run_forever()

    inbox_thread = threading.Thread(target=start_inbox, daemon=True, name="inbox_manager")
    inbox_thread.start()
    logger.info("✅ Inbox manager started - auto-connecting accounts")
except Exception as e:
    logger.warning(f"⚠️ Could not start inbox manager: {str(e)}")
```

#### Remove/Deprecate scan_for_replies

```python
# Modify /api/scan-replies endpoint to return deprecation notice
@app.route('/api/scan-replies', methods=['POST'])
def scan_replies_deprecated():
    return jsonify({
        'error': 'This endpoint is deprecated. Use the Inbox system for real-time reply detection.',
        'alternative': '/api/inbox/{phone}/conversations?matrix_only=true'
    }), 410  # Gone
```

**Testing Checkpoint:** Test all endpoints with curl/Postman, verify WebSocket events.

---

## Phase 4: Frontend

### New File: `frontend/src/hooks/useInbox.js`

```javascript
import { useState, useEffect, useCallback, useRef } from 'react';
import { io } from 'socket.io-client';
import api from '../services/api';

const SOCKET_URL = 'http://localhost:5000';

export function useInbox(phone) {
    const [conversations, setConversations] = useState([]);
    const [messages, setMessages] = useState([]);
    const [selectedPeer, setSelectedPeer] = useState(null);
    const [loading, setLoading] = useState(true);
    const [connected, setConnected] = useState(false);
    const [typingUsers, setTypingUsers] = useState({});
    const [userStatuses, setUserStatuses] = useState({});
    const [rateLimitStatus, setRateLimitStatus] = useState(null);
    const socketRef = useRef(null);

    // Initialize socket connection
    useEffect(() => {
        socketRef.current = io(SOCKET_URL, {
            transports: ['websocket', 'polling'],
            reconnection: true,
        });

        socketRef.current.on('connect', () => setConnected(true));
        socketRef.current.on('disconnect', () => setConnected(false));

        return () => {
            socketRef.current?.disconnect();
        };
    }, []);

    // Subscribe to inbox events for selected phone
    useEffect(() => {
        if (!phone || !socketRef.current) return;

        socketRef.current.emit('inbox:subscribe', { phone });

        // Handle new messages
        const handleNewMessage = (data) => {
            if (data.account_phone !== phone) return;

            // Update conversations list
            setConversations(prev => {
                const updated = [...prev];
                const idx = updated.findIndex(c => c.peer_id === data.peer_id);
                if (idx >= 0) {
                    updated[idx] = { ...updated[idx], ...data.conversation };
                    // Move to top
                    const [conv] = updated.splice(idx, 1);
                    updated.unshift(conv);
                }
                return updated;
            });

            // Update messages if viewing this conversation
            if (selectedPeer === data.peer_id) {
                setMessages(prev => [...prev, data.message]);
            }
        };

        // Handle read receipts
        const handleMessageRead = (data) => {
            if (data.account_phone !== phone) return;

            setMessages(prev => prev.map(msg =>
                msg.is_outgoing && msg.msg_id <= data.max_read_id
                    ? { ...msg, is_read: true, read_at: data.timestamp }
                    : msg
            ));
        };

        // Handle typing indicators
        const handleTyping = (data) => {
            if (data.account_phone !== phone) return;

            setTypingUsers(prev => ({
                ...prev,
                [data.peer_id]: data.is_typing
            }));

            // Auto-clear after 5 seconds
            if (data.is_typing) {
                setTimeout(() => {
                    setTypingUsers(prev => ({
                        ...prev,
                        [data.peer_id]: false
                    }));
                }, 5000);
            }
        };

        // Handle user status
        const handleUserStatus = (data) => {
            if (data.account_phone !== phone) return;

            setUserStatuses(prev => ({
                ...prev,
                [data.peer_id]: { online: data.online, last_seen: data.last_seen }
            }));
        };

        socketRef.current.on('inbox:new_message', handleNewMessage);
        socketRef.current.on('inbox:message_read', handleMessageRead);
        socketRef.current.on('inbox:typing', handleTyping);
        socketRef.current.on('inbox:user_status', handleUserStatus);

        return () => {
            socketRef.current.emit('inbox:unsubscribe', { phone });
            socketRef.current.off('inbox:new_message', handleNewMessage);
            socketRef.current.off('inbox:message_read', handleMessageRead);
            socketRef.current.off('inbox:typing', handleTyping);
            socketRef.current.off('inbox:user_status', handleUserStatus);
        };
    }, [phone, selectedPeer]);

    // Fetch conversations
    const fetchConversations = useCallback(async (options = {}) => {
        if (!phone) return;
        setLoading(true);
        try {
            const response = await api.getInboxConversations(phone, options);
            setConversations(response.data.conversations);
        } finally {
            setLoading(false);
        }
    }, [phone]);

    // Fetch messages for a conversation
    const fetchMessages = useCallback(async (peerId, options = {}) => {
        if (!phone || !peerId) return;
        try {
            const response = await api.getInboxMessages(phone, peerId, options);
            setMessages(response.data.messages);
            setSelectedPeer(peerId);
        } catch (error) {
            console.error('Failed to fetch messages:', error);
        }
    }, [phone]);

    // Send message
    const sendMessage = useCallback(async (peerId, text) => {
        if (!phone || !peerId || !text.trim()) return null;
        try {
            const response = await api.sendInboxMessage(phone, peerId, text);
            setRateLimitStatus(response.data.rate_limit_status);
            return response.data;
        } catch (error) {
            console.error('Failed to send message:', error);
            throw error;
        }
    }, [phone]);

    // Fetch rate limit status
    const fetchRateLimitStatus = useCallback(async () => {
        if (!phone) return;
        try {
            const response = await api.getInboxRateLimitStatus(phone);
            setRateLimitStatus(response.data);
        } catch (error) {
            console.error('Failed to fetch rate limit status:', error);
        }
    }, [phone]);

    return {
        conversations,
        messages,
        selectedPeer,
        loading,
        connected,
        typingUsers,
        userStatuses,
        rateLimitStatus,
        fetchConversations,
        fetchMessages,
        sendMessage,
        fetchRateLimitStatus,
        setSelectedPeer,
    };
}
```

### New File: `frontend/src/pages/Inbox.jsx`

```jsx
import React, { useEffect, useState } from 'react';
import { useAccounts } from '../context/AccountContext';
import { useInbox } from '../hooks/useInbox';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Badge } from '../components/ui/badge';
import {
    MessageSquare, Send, Check, CheckCheck,
    Circle, Loader2, RefreshCw, User
} from 'lucide-react';
import AccountSelector from '../components/AccountSelector';

export default function Inbox() {
    const { accounts, selectedAccounts } = useAccounts();
    const [selectedPhone, setSelectedPhone] = useState(null);
    const [messageText, setMessageText] = useState('');
    const [sending, setSending] = useState(false);

    const {
        conversations,
        messages,
        selectedPeer,
        loading,
        connected,
        typingUsers,
        userStatuses,
        rateLimitStatus,
        fetchConversations,
        fetchMessages,
        sendMessage,
        fetchRateLimitStatus,
    } = useInbox(selectedPhone);

    // Set initial phone from selected accounts
    useEffect(() => {
        if (selectedAccounts.length > 0 && !selectedPhone) {
            setSelectedPhone(selectedAccounts[0]);
        }
    }, [selectedAccounts, selectedPhone]);

    // Fetch conversations when phone changes
    useEffect(() => {
        if (selectedPhone) {
            fetchConversations();
            fetchRateLimitStatus();
        }
    }, [selectedPhone, fetchConversations, fetchRateLimitStatus]);

    const handleSend = async () => {
        if (!selectedPeer || !messageText.trim()) return;
        setSending(true);
        try {
            await sendMessage(selectedPeer, messageText);
            setMessageText('');
        } catch (error) {
            // Handle error
        } finally {
            setSending(false);
        }
    };

    return (
        <div className="flex h-[calc(100vh-4rem)]">
            {/* Left Panel: Conversation List */}
            <div className="w-80 border-r flex flex-col">
                <div className="p-4 border-b">
                    <AccountSelector
                        accounts={accounts}
                        selectedPhone={selectedPhone}
                        onSelect={setSelectedPhone}
                    />
                    <div className="flex items-center gap-2 mt-2">
                        <Badge variant={connected ? 'success' : 'secondary'}>
                            {connected ? 'Connected' : 'Disconnected'}
                        </Badge>
                        <Button size="sm" variant="ghost" onClick={() => fetchConversations()}>
                            <RefreshCw className="h-4 w-4" />
                        </Button>
                    </div>
                </div>

                <div className="flex-1 overflow-y-auto">
                    {loading ? (
                        <div className="flex justify-center p-4">
                            <Loader2 className="h-6 w-6 animate-spin" />
                        </div>
                    ) : (
                        conversations.map(conv => (
                            <ConversationItem
                                key={conv.peer_id}
                                conversation={conv}
                                isSelected={selectedPeer === conv.peer_id}
                                isTyping={typingUsers[conv.peer_id]}
                                status={userStatuses[conv.peer_id]}
                                onClick={() => fetchMessages(conv.peer_id)}
                            />
                        ))
                    )}
                </div>
            </div>

            {/* Right Panel: Message Thread */}
            <div className="flex-1 flex flex-col">
                {selectedPeer ? (
                    <>
                        {/* Messages */}
                        <div className="flex-1 overflow-y-auto p-4 space-y-2">
                            {messages.map(msg => (
                                <MessageBubble key={msg.msg_id} message={msg} />
                            ))}
                            {typingUsers[selectedPeer] && (
                                <div className="text-sm text-muted-foreground">
                                    Typing...
                                </div>
                            )}
                        </div>

                        {/* Compose */}
                        <div className="p-4 border-t">
                            {rateLimitStatus && (
                                <div className="text-xs text-muted-foreground mb-2">
                                    DMs remaining: {rateLimitStatus.remaining}/{rateLimitStatus.limit}
                                </div>
                            )}
                            <div className="flex gap-2">
                                <Input
                                    value={messageText}
                                    onChange={(e) => setMessageText(e.target.value)}
                                    placeholder="Type a message..."
                                    onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSend()}
                                    disabled={sending || rateLimitStatus?.remaining === 0}
                                />
                                <Button
                                    onClick={handleSend}
                                    disabled={sending || !messageText.trim() || rateLimitStatus?.remaining === 0}
                                >
                                    {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                                </Button>
                            </div>
                        </div>
                    </>
                ) : (
                    <div className="flex-1 flex items-center justify-center text-muted-foreground">
                        <MessageSquare className="h-12 w-12 mr-2" />
                        Select a conversation
                    </div>
                )}
            </div>
        </div>
    );
}

// Conversation list item component
function ConversationItem({ conversation, isSelected, isTyping, status, onClick }) {
    return (
        <div
            className={`p-3 cursor-pointer hover:bg-muted/50 border-b ${isSelected ? 'bg-muted' : ''}`}
            onClick={onClick}
        >
            <div className="flex items-center gap-2">
                <div className="relative">
                    <User className="h-10 w-10 rounded-full bg-muted p-2" />
                    {status?.online && (
                        <Circle className="absolute bottom-0 right-0 h-3 w-3 fill-green-500 text-green-500" />
                    )}
                </div>
                <div className="flex-1 min-w-0">
                    <div className="flex justify-between">
                        <span className="font-medium truncate">
                            {conversation.first_name} {conversation.last_name}
                        </span>
                        {conversation.unread_count > 0 && (
                            <Badge variant="default">{conversation.unread_count}</Badge>
                        )}
                    </div>
                    <div className="text-sm text-muted-foreground truncate">
                        {isTyping ? 'Typing...' : conversation.last_msg_text}
                    </div>
                </div>
                {conversation.is_matrix_contact && (
                    <Badge variant="outline">
                        {conversation.contact_status === 'blue' ? '🔵' : '🟡'}
                        {conversation.contact_type === 'dev' ? '💻' : '📢'}
                    </Badge>
                )}
            </div>
        </div>
    );
}

// Message bubble component
function MessageBubble({ message }) {
    return (
        <div className={`flex ${message.is_outgoing ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[70%] rounded-lg p-3 ${
                message.is_outgoing
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-muted'
            }`}>
                <p>{message.text}</p>
                <div className="flex items-center justify-end gap-1 mt-1">
                    <span className="text-xs opacity-70">
                        {new Date(message.date).toLocaleTimeString()}
                    </span>
                    {message.is_outgoing && (
                        message.is_read
                            ? <CheckCheck className="h-3 w-3" />
                            : <Check className="h-3 w-3" />
                    )}
                </div>
            </div>
        </div>
    );
}
```

### Modify: `frontend/src/App.jsx`

Add Inbox route after Dashboard:

```jsx
import Inbox from './pages/Inbox';

// In Routes:
<Route path="/" element={<Dashboard />} />
<Route path="/inbox" element={<Inbox />} />  {/* NEW */}
<Route path="/contacts" element={<Contacts />} />
// ... rest
```

### Modify: `frontend/src/components/Sidebar.jsx`

Add Inbox to navigation (position 2):

```jsx
import { MessageSquare } from 'lucide-react';

const navItems = [
    { label: 'Dashboard', path: '/', icon: LayoutDashboard },
    { label: 'Inbox', path: '/inbox', icon: MessageSquare },  // NEW
    { label: 'Contacts', path: '/contacts', icon: Users },
    // ... rest unchanged
];
```

### Modify: `frontend/src/services/api.js`

Add inbox API functions:

```javascript
// ============================================================================
// INBOX API FUNCTIONS
// ============================================================================

// Connection
connectInbox: (phone) => apiClient.post('/inbox/connect', { phone }),
disconnectInbox: (phone) => apiClient.post('/inbox/disconnect', { phone }),
connectAllInbox: () => apiClient.post('/inbox/connect-all'),
getConnectionStatus: () => apiClient.get('/inbox/connection-status'),

// Conversations
getInboxConversations: (phone, params = {}) =>
    apiClient.get(`/inbox/${phone}/conversations`, { params }),
getInboxConversation: (phone, peerId) =>
    apiClient.get(`/inbox/${phone}/conversations/${peerId}`),

// Messages
getInboxMessages: (phone, peerId, params = {}) =>
    apiClient.get(`/inbox/${phone}/conversations/${peerId}/messages`, { params }),

// Send
sendInboxMessage: (phone, peerId, text) =>
    apiClient.post(`/inbox/${phone}/send`, { peer_id: peerId, text }),
getInboxRateLimitStatus: (phone) =>
    apiClient.get(`/inbox/${phone}/rate-limit`),

// Sync
triggerDialogSync: (phone) => apiClient.post(`/inbox/${phone}/sync/dialogs`),
triggerFullSync: (phone) => apiClient.post(`/inbox/${phone}/sync/full`),

// Metrics
getInboxMetrics: (params = {}) => apiClient.get('/inbox/metrics', { params }),
getCampaignMetrics: (campaignId) => apiClient.get(`/inbox/campaigns/${campaignId}/metrics`),
```

**Testing Checkpoint:** Navigate to Inbox page, verify real-time updates.

---

## Phase 5: Campaign Integration

### Modify Import Methods

In `api_server.py`, modify `import_dev_contacts()` and `import_kol_contacts()`:

```python
async def import_dev_contacts(self, csv_path: str, dry_run: bool = False,
                              campaign_id: str = None, ...):
    # Auto-generate campaign_id from CSV filename if not provided
    if not campaign_id:
        campaign_id = Path(csv_path).stem  # e.g., "dev_contacts_q4"

    # Create or update campaign record
    self._ensure_campaign(campaign_id, contact_type='dev')

    # ... rest of import logic

    # When adding contact, also update inbox_conversations
    # to link with campaign_id
```

### Link Existing Contacts

After first sync, link existing emoji contacts to inbox:

```python
async def link_matrix_contacts_to_inbox(self, phone: str):
    """Link existing emoji contacts to inbox_conversations"""
    contacts = await self._contact_cache.get_contacts(self.client, phone=phone)

    for user in contacts.users:
        if not user.first_name:
            continue

        # Check for MATRIX emoji pattern
        if '🔵💻' in user.first_name or '🟡💻' in user.first_name:
            contact_type = 'dev'
            contact_status = 'blue' if '🔵' in user.first_name else 'yellow'
        elif '🔵📢' in user.first_name or '🟡📢' in user.first_name:
            contact_type = 'kol'
            contact_status = 'blue' if '🔵' in user.first_name else 'yellow'
        else:
            continue

        # Update inbox_conversations record
        self._db_update_conversation_matrix_link(
            phone, user.id,
            is_matrix_contact=True,
            contact_type=contact_type,
            contact_status=contact_status
        )
```

---

## Phase 6: Integration & Polish

### First Reply Auto-Update

When inbox detects first reply from blue contact:

```python
async def _check_first_reply(self, account_phone: str, peer_id: int,
                              conversation: Dict) -> bool:
    """Check if this is first reply from a blue contact"""

    if not conversation.get('is_matrix_contact'):
        return False

    if conversation.get('contact_status') != 'blue':
        return False

    # This is a blue contact replying for the first time!

    # 1. Update emoji in Telegram contact name (🔵→🟡)
    await self._update_contact_emoji(account_phone, peer_id, 'yellow')

    # 2. Update inbox_conversations
    self._db_update_conversation(account_phone, peer_id, contact_status='yellow')

    # 3. Emit WebSocket event
    self._socketio.emit('inbox:first_reply', {
        'account_phone': account_phone,
        'peer_id': peer_id,
        'contact_type': conversation.get('contact_type'),
        'campaign_id': conversation.get('campaign_id'),
    }, room=f"inbox:{account_phone}")

    # 4. Update campaign metrics
    if conversation.get('campaign_id'):
        self._increment_campaign_replies(conversation['campaign_id'])

    return True
```

### Graceful Shutdown

```python
import signal
import atexit

def shutdown_handler(signum=None, frame=None):
    logger.info("🛑 Shutting down - disconnecting all inbox connections...")
    if inbox_manager and inbox_manager._running:
        future = asyncio.run_coroutine_threadsafe(
            inbox_manager.stop(),
            inbox_manager._pool._loop
        )
        try:
            future.result(timeout=30)
        except Exception as e:
            logger.error(f"Shutdown error: {e}")

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)
atexit.register(shutdown_handler)
```

---

## Files Summary

| File | Action | Est. Lines |
|------|--------|-----------|
| `backend/inbox_manager.py` | CREATE | ~1500 |
| `backend/celery_app.py` | CREATE | ~60 |
| `backend/inbox_tasks.py` | CREATE | ~150 |
| `backend/account_manager.py` | MODIFY | +150 |
| `backend/api_server.py` | MODIFY | +400 |
| `frontend/src/hooks/useInbox.js` | CREATE | ~200 |
| `frontend/src/pages/Inbox.jsx` | CREATE | ~300 |
| `frontend/src/App.jsx` | MODIFY | +2 |
| `frontend/src/components/Sidebar.jsx` | MODIFY | +1 |
| `frontend/src/services/api.js` | MODIFY | +30 |

### New Dependencies

```bash
pip install celery redis
```

### Required Services

| Service | Purpose | Default Port |
|---------|---------|--------------|
| Redis | Celery message broker + result backend | 6379 |
| Celery Worker | Executes async tasks | N/A |
| Celery Beat | Schedules periodic tasks | N/A |

---

## Rollback Strategy

- **Phase 2 fails:** Don't import InboxManager in api_server.py, existing code works
- **Phase 2C fails:** Remove Celery, fallback to simple asyncio scheduler
- **Phase 3 fails:** Remove new endpoints, keep existing scan_for_replies
- **Phase 4 fails:** Remove Inbox route/nav, backend keeps collecting data

---

## Testing Checklist

### Unit Tests
- [ ] ConnectionPool: connect, disconnect, reconnect
- [ ] EventProcessor: handle each event type
- [ ] SyncEngine: gap detection logic
- [ ] DMRateLimiter: rate limiting + duplicate detection

### Integration Tests
- [ ] Full sync cycle (dialog sync → backfill)
- [ ] Real-time event flow (Telegram → DB → WebSocket → Frontend)
- [ ] Multi-account concurrent connections
- [ ] Reconnection after disconnect

### Manual Tests
- [ ] Receive message → verify in UI
- [ ] Send message → verify rate limiting
- [ ] Have contact read message → verify read receipt
- [ ] Disconnect/reconnect → verify backfill works
- [ ] Blue contact replies → verify 🔵→🟡 update

---

## Next Session Quick Start

```bash
# 1. Start backend
cd "C:\Users\LENOVO\Documents\Toshi System\MATRIX"
python backend/api_server.py

# 2. Start frontend
cd frontend
npm run dev

# 3. Implementation order:
# - Phase 1: Database schema (account_manager.py)
# - Phase 2: Core backend (inbox_manager.py)
# - Phase 3: API endpoints (api_server.py)
# - Phase 4: Frontend (useInbox.js, Inbox.jsx)
# - Phase 5: Campaign integration
# - Phase 6: Polish & testing
```

---

**Document End**

*Ready for implementation. All decisions finalized. No open questions.*
