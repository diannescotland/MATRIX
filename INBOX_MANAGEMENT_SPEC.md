# MATRIX Inbox Management System - Technical Specification

**Document Type:** Implementation Specification for Claude Code
**Project:** MATRIX - Multi-Account Telegram Contact Manager
**Feature:** Real-Time Inbox Management System
**Version:** 1.0
**Date:** December 5, 2025

---

## Executive Summary

This document specifies a **real-time inbox management system** to be integrated into the existing MATRIX codebase. The system provides persistent Telegram connections, real-time message/event tracking, and efficient synchronization with minimal API calls.

**Primary Goals:**
1. Real-time inbox sync via persistent Telethon connections
2. Full message history storage (inbox mirror)
3. Read receipt tracking ("they read my message")
4. WebSocket notifications to React frontend
5. Minimal Telegram API calls through smart gap detection
6. Campaign-based reply rate metrics

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Database Schema](#2-database-schema)
3. [Backend Implementation](#3-backend-implementation)
4. [API Endpoints](#4-api-endpoints)
5. [WebSocket Events](#5-websocket-events)
6. [Frontend Integration](#6-frontend-integration)
7. [Sync Logic](#7-sync-logic)
8. [Files to Create](#8-files-to-create)
9. [Files to Modify](#9-files-to-modify)
10. [Migration Guide](#10-migration-guide)
11. [Testing Checklist](#11-testing-checklist)

---

## 1. Architecture Overview

### 1.1 System Design

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     INBOX MANAGEMENT SYSTEM                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐ │
│  │                  CONNECTION POOL MANAGER                          │ │
│  │  - Maintains persistent TelegramClient per active account         │ │
│  │  - Auto-reconnect on disconnect                                   │ │
│  │  - Health monitoring                                              │ │
│  │  - Graceful shutdown                                              │ │
│  └───────────────────────────────────────────────────────────────────┘ │
│                              │                                          │
│              ┌───────────────┼───────────────┐                         │
│              ▼               ▼               ▼                         │
│       ┌──────────┐    ┌──────────┐    ┌──────────┐                    │
│       │ Account1 │    │ Account2 │    │ AccountN │                    │
│       │ Client   │    │ Client   │    │ Client   │                    │
│       └────┬─────┘    └────┬─────┘    └────┬─────┘                    │
│            │               │               │                           │
│            └───────────────┼───────────────┘                           │
│                            ▼                                           │
│  ┌───────────────────────────────────────────────────────────────────┐ │
│  │                    EVENT HANDLERS                                 │ │
│  │  @client.on(events.NewMessage)      → on_new_message()           │ │
│  │  @client.on(events.MessageRead)     → on_message_read()          │ │
│  │  @client.on(events.MessageEdited)   → on_message_edited()        │ │
│  │  @client.on(events.MessageDeleted)  → on_message_deleted()       │ │
│  └───────────────────────────────────────────────────────────────────┘ │
│                            │                                           │
│                            ▼                                           │
│  ┌───────────────────────────────────────────────────────────────────┐ │
│  │                    EVENT PROCESSOR                                │ │
│  │  1. Persist to SQLite (messages, conversations, events)          │ │
│  │  2. Check notification rules                                      │ │
│  │  3. Emit to WebSocket                                            │ │
│  │  4. Update metrics                                               │ │
│  └───────────────────────────────────────────────────────────────────┘ │
│                            │                                           │
│              ┌─────────────┼─────────────┐                            │
│              ▼             ▼             ▼                            │
│       ┌──────────┐  ┌──────────┐  ┌──────────┐                       │
│       │  SQLite  │  │ WebSocket│  │ Metrics  │                       │
│       │    DB    │  │  Emitter │  │  Engine  │                       │
│       └──────────┘  └──────────┘  └──────────┘                       │
│                            │                                           │
│                            ▼                                           │
│                     ┌──────────┐                                      │
│                     │  React   │                                      │
│                     │ Frontend │                                      │
│                     └──────────┘                                      │
│                                                                        │
├─────────────────────────────────────────────────────────────────────────┤
│  PERIODIC TASKS (Background Scheduler)                                 │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐         │
│  │ Dialog Sync     │ │ Full History    │ │ Metrics Update  │         │
│  │ Every 30 min    │ │ Every 12-24h    │ │ Every 5 min     │         │
│  └─────────────────┘ └─────────────────┘ └─────────────────┘         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Connection model | Persistent per account | Real-time events require always-on connections |
| Message storage | Full mirror | User needs complete inbox data |
| Read tracking | Outbox receipts only | "They read MY message" is the requirement |
| Notifications | WebSocket to frontend | Real-time UI updates |
| Sync strategy | Event-driven + periodic reconciliation | Minimize API calls while ensuring consistency |
| Gap detection | Message ID comparison | Efficient, no extra API calls |

### 1.3 API Call Optimization Strategy

The system minimizes Telegram API calls through:

1. **Real-time events** - No polling, events push to us
2. **Dialog sync (every 30min)** - 1 API call returns ALL conversations
3. **Gap detection** - Only fetch messages when `dialog.last_msg_id - db.last_msg_id >= 2`
4. **Single message from dialog** - If gap is 1, use message from dialog response (0 extra calls)
5. **Batch message fetch** - When backfilling, fetch 100 messages per call

**Expected API calls per account:**
- Real-time: 0 (events are pushed)
- Per 30min sync: 1 (GetDialogs)
- Per conversation with gap: 1 (GetHistory)
- Full daily sync: ~1-5 depending on conversation count

---

## 2. Database Schema

### 2.1 New Tables

Add these tables to `accounts.db`:

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
    last_event_at TIMESTAMP,
    needs_backfill BOOLEAN DEFAULT FALSE,
    backfill_from_msg_id INTEGER,
    
    -- Flags
    is_archived BOOLEAN DEFAULT FALSE,
    is_muted BOOLEAN DEFAULT FALSE,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(account_phone, peer_id)
);

CREATE INDEX IF NOT EXISTS idx_inbox_conv_account ON inbox_conversations(account_phone);
CREATE INDEX IF NOT EXISTS idx_inbox_conv_peer ON inbox_conversations(peer_id);
CREATE INDEX IF NOT EXISTS idx_inbox_conv_matrix ON inbox_conversations(is_matrix_contact);
CREATE INDEX IF NOT EXISTS idx_inbox_conv_campaign ON inbox_conversations(campaign_id);
CREATE INDEX IF NOT EXISTS idx_inbox_conv_backfill ON inbox_conversations(needs_backfill) WHERE needs_backfill = TRUE;


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
    media_type TEXT,                          -- 'photo', 'document', 'video', 'voice', 'sticker', NULL
    media_file_id TEXT,
    media_filename TEXT,
    media_size INTEGER,
    
    -- Edit/delete tracking
    edit_date TIMESTAMP,
    is_deleted BOOLEAN DEFAULT FALSE,
    deleted_at TIMESTAMP,
    
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
CREATE INDEX IF NOT EXISTS idx_inbox_msg_unread ON inbox_messages(account_phone, is_outgoing, is_read) 
    WHERE is_outgoing = TRUE AND is_read = FALSE;


-- ============================================================================
-- INBOX_EVENTS: Event log for notifications and audit
-- ============================================================================
CREATE TABLE IF NOT EXISTS inbox_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_phone TEXT NOT NULL,
    peer_id INTEGER NOT NULL,
    
    event_type TEXT NOT NULL,
    event_data TEXT,                          -- JSON string
    
    msg_id INTEGER,
    campaign_id TEXT,
    
    -- Notification tracking
    notified BOOLEAN DEFAULT FALSE,
    notified_at TIMESTAMP,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Event types:
-- 'new_message_incoming'  - They sent us a message
-- 'new_message_outgoing'  - We sent a message (detected from another client)
-- 'message_read'          - They read our message
-- 'message_edited'        - A message was edited
-- 'message_deleted'       - A message was deleted
-- 'first_reply'           - First reply from a blue contact (🔵→🟡)
-- 'conversation_created'  - New conversation started

CREATE INDEX IF NOT EXISTS idx_inbox_events_account ON inbox_events(account_phone);
CREATE INDEX IF NOT EXISTS idx_inbox_events_type ON inbox_events(event_type);
CREATE INDEX IF NOT EXISTS idx_inbox_events_unnotified ON inbox_events(notified) WHERE notified = FALSE;


-- ============================================================================
-- CAMPAIGNS: Track outreach campaigns for metrics
-- ============================================================================
CREATE TABLE IF NOT EXISTS inbox_campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    contact_type TEXT,                        -- 'dev', 'kol', 'mixed'
    
    -- Stats (updated via triggers or application code)
    total_contacts INTEGER DEFAULT 0,
    total_reached INTEGER DEFAULT 0,          -- Contacts we messaged
    total_replies INTEGER DEFAULT 0,          -- Contacts who replied
    total_read INTEGER DEFAULT 0,             -- Messages read by recipients
    
    -- Calculated metrics (updated periodically)
    reply_rate REAL DEFAULT 0,                -- replies / reached
    read_rate REAL DEFAULT 0,                 -- read / sent
    avg_response_time_seconds INTEGER,        -- Average time to first reply
    
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    last_activity_at TIMESTAMP,
    
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
    disconnect_reason TEXT,
    reconnect_attempts INTEGER DEFAULT 0,
    
    -- Sync state
    last_dialog_sync TIMESTAMP,
    last_full_sync TIMESTAMP,
    dialogs_count INTEGER DEFAULT 0,
    messages_count INTEGER DEFAULT 0,
    pending_backfills INTEGER DEFAULT 0,
    
    -- Health
    last_heartbeat TIMESTAMP,
    
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 2.2 Schema Notes

- **`inbox_` prefix**: All new tables use `inbox_` prefix to avoid conflicts with existing tables
- **`account_phone`**: Foreign key to `accounts.phone`, normalized (no `+`)
- **`peer_id`**: Telegram's user ID for the conversation partner
- **`msg_id`**: Telegram's message ID within a conversation (unique per peer)
- **`is_matrix_contact`**: Links to existing emoji contact system
- **`synced_via`**: Tracks how message was obtained for debugging

---

## 3. Backend Implementation

### 3.1 New File: `backend/inbox_manager.py`

Create this file with the following classes:

#### 3.1.1 ConnectionPool

```python
class ConnectionPool:
    """
    Manages persistent Telethon connections for multiple accounts.
    
    Features:
    - One TelegramClient per account
    - Automatic reconnection on disconnect
    - Health monitoring via heartbeat
    - Graceful shutdown
    - Event handler registration
    
    Usage:
        pool = ConnectionPool(max_connections=100)
        await pool.connect_account(phone, api_id, api_hash, session_path)
        await pool.disconnect_account(phone)
        await pool.shutdown()
    """
    
    def __init__(self, max_connections: int = 100):
        self._clients: Dict[str, TelegramClient] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._connected: Set[str] = set()
        self._max_connections = max_connections
        self._event_processor: EventProcessor = None
        self._master_lock = asyncio.Lock()
    
    async def connect_account(self, phone: str, api_id: int, api_hash: str, 
                              session_path: str) -> bool:
        """Connect account and register event handlers"""
        pass
    
    async def disconnect_account(self, phone: str) -> None:
        """Gracefully disconnect account"""
        pass
    
    async def reconnect_account(self, phone: str) -> bool:
        """Reconnect a disconnected account"""
        pass
    
    def get_client(self, phone: str) -> Optional[TelegramClient]:
        """Get client for account (if connected)"""
        pass
    
    def is_connected(self, phone: str) -> bool:
        """Check if account is connected"""
        pass
    
    def get_connected_accounts(self) -> List[str]:
        """Get list of connected account phones"""
        pass
    
    async def health_check(self) -> Dict[str, Any]:
        """Run health check on all connections"""
        pass
    
    async def shutdown(self) -> None:
        """Gracefully shutdown all connections"""
        pass
```

#### 3.1.2 EventProcessor

```python
class EventProcessor:
    """
    Processes Telegram events and persists to database.
    
    Responsibilities:
    - Parse incoming Telegram events
    - Persist to SQLite (messages, conversations, events)
    - Emit to WebSocket for frontend
    - Check notification rules
    - Update metrics
    
    Usage:
        processor = EventProcessor(socketio, db_path)
        await processor.handle_new_message(phone, event)
        await processor.handle_message_read(phone, event)
    """
    
    def __init__(self, socketio: SocketIO, db_path: str):
        self._socketio = socketio
        self._db_path = db_path
        self._notification_rules: List[NotificationRule] = []
    
    async def handle_new_message(self, account_phone: str, event) -> None:
        """
        Handle NewMessage event.
        
        1. Determine if incoming or outgoing
        2. Create/update conversation record
        3. Insert message record
        4. Create inbox_event
        5. Check if first reply from blue contact
        6. Emit WebSocket notification
        """
        pass
    
    async def handle_message_read(self, account_phone: str, event) -> None:
        """
        Handle MessageRead event (outbox read receipt).
        
        Telethon event: UpdateReadHistoryOutbox
        - event.max_id = highest message ID they've read
        - event.peer = who read our messages
        
        1. Update messages.is_read = TRUE for all msg_id <= max_id
        2. Update conversation.their_last_read_msg_id
        3. Create inbox_event
        4. Emit WebSocket notification
        """
        pass
    
    async def handle_message_edited(self, account_phone: str, event) -> None:
        """
        Handle MessageEdited event.
        
        1. Update message.text and message.edit_date
        2. Create inbox_event
        3. Emit WebSocket notification (optional)
        """
        pass
    
    async def handle_message_deleted(self, account_phone: str, event) -> None:
        """
        Handle MessageDeleted event.
        
        1. Mark message.is_deleted = TRUE, set deleted_at
        2. Create inbox_event with event_type='message_deleted'
        3. Emit WebSocket notification
        """
        pass
    
    async def _emit_notification(self, event_type: str, data: Dict) -> None:
        """Emit event via WebSocket to frontend"""
        pass
    
    async def _check_first_reply(self, account_phone: str, peer_id: int, 
                                  conversation: Dict) -> bool:
        """Check if this is first reply from a blue contact"""
        pass
```

#### 3.1.3 SyncEngine

```python
class SyncEngine:
    """
    Handles periodic synchronization and gap detection.
    
    Sync Strategy:
    1. Every 30 minutes: Fetch all dialogs (1 API call)
    2. For each dialog, compare last_msg_id with database
    3. If gap >= 2: Schedule backfill
    4. If gap == 1: Use message from dialog response (no extra call)
    5. Every 12-24 hours: Full verification sync
    
    Usage:
        engine = SyncEngine(pool, processor, db_path)
        await engine.sync_dialogs(phone)
        await engine.backfill_conversation(phone, peer_id, from_msg_id)
        await engine.full_sync(phone)
    """
    
    def __init__(self, pool: ConnectionPool, processor: EventProcessor, db_path: str):
        self._pool = pool
        self._processor = processor
        self._db_path = db_path
    
    async def sync_dialogs(self, account_phone: str) -> SyncResult:
        """
        Fetch all dialogs and detect gaps.
        
        Algorithm:
        1. client.get_dialogs() - returns all conversations with last message
        2. For each dialog where dialog.is_user (private chat only):
           a. Get db_last_msg_id from inbox_conversations
           b. Get dialog_last_msg_id from dialog.message.id
           c. Calculate gap = dialog_last_msg_id - db_last_msg_id
           d. If gap == 0: Skip (no new messages)
           e. If gap == 1: Insert message from dialog.message, update conversation
           f. If gap >= 2: Mark needs_backfill=TRUE, schedule backfill
        3. Update last_dialog_sync timestamp
        
        Returns:
            SyncResult with counts: synced, gaps_detected, errors
        """
        pass
    
    async def backfill_conversation(self, account_phone: str, peer_id: int,
                                    from_msg_id: int, limit: int = 100) -> int:
        """
        Fetch messages to fill gap in conversation.
        
        Algorithm:
        1. client.get_messages(peer, min_id=from_msg_id, limit=100)
        2. Insert all messages into inbox_messages
        3. Update conversation.last_msg_id
        4. Set needs_backfill=FALSE
        
        Returns:
            Number of messages backfilled
        """
        pass
    
    async def full_sync(self, account_phone: str) -> FullSyncResult:
        """
        Complete sync for data integrity (run every 12-24h).
        
        1. Sync all dialogs
        2. Process all pending backfills
        3. Verify message counts
        4. Update metrics
        """
        pass
    
    async def process_pending_backfills(self, account_phone: str) -> int:
        """Process all conversations marked needs_backfill=TRUE"""
        pass
```

#### 3.1.4 InboxManager (Main Class)

```python
class InboxManager:
    """
    Main entry point for inbox management system.
    
    Orchestrates:
    - ConnectionPool for persistent connections
    - EventProcessor for real-time events
    - SyncEngine for periodic synchronization
    - Background tasks (scheduler)
    
    Usage:
        manager = InboxManager(socketio)
        await manager.start()
        await manager.connect_account(phone)
        await manager.get_conversations(phone)
        await manager.get_messages(phone, peer_id)
        await manager.stop()
    """
    
    def __init__(self, socketio: SocketIO):
        self._socketio = socketio
        self._pool = ConnectionPool()
        self._processor = EventProcessor(socketio, DB_PATH)
        self._sync_engine = SyncEngine(self._pool, self._processor, DB_PATH)
        self._scheduler = None
        self._running = False
    
    async def start(self) -> None:
        """Start inbox manager and background tasks"""
        pass
    
    async def stop(self) -> None:
        """Stop inbox manager and cleanup"""
        pass
    
    async def connect_account(self, phone: str) -> bool:
        """Connect an account to the inbox system"""
        pass
    
    async def disconnect_account(self, phone: str) -> None:
        """Disconnect an account"""
        pass
    
    async def connect_all_active_accounts(self) -> Dict[str, bool]:
        """Connect all accounts with status='active'"""
        pass
    
    # Query methods
    async def get_conversations(self, phone: str, limit: int = 50, 
                                 offset: int = 0) -> List[Dict]:
        """Get conversations for account"""
        pass
    
    async def get_conversation(self, phone: str, peer_id: int) -> Optional[Dict]:
        """Get single conversation details"""
        pass
    
    async def get_messages(self, phone: str, peer_id: int, 
                           limit: int = 50, before_msg_id: int = None) -> List[Dict]:
        """Get messages for conversation"""
        pass
    
    async def get_unread_counts(self, phone: str) -> Dict[int, int]:
        """Get unread count per conversation"""
        pass
    
    # Metrics
    async def get_campaign_metrics(self, campaign_id: str) -> Dict:
        """Get metrics for a campaign"""
        pass
    
    async def get_account_metrics(self, phone: str) -> Dict:
        """Get inbox metrics for account"""
        pass
    
    # Sync triggers
    async def trigger_dialog_sync(self, phone: str) -> SyncResult:
        """Manually trigger dialog sync"""
        pass
    
    async def trigger_full_sync(self, phone: str) -> FullSyncResult:
        """Manually trigger full sync"""
        pass
```

### 3.2 Event Handler Registration

When connecting an account, register these Telethon event handlers:

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
    
    @client.on(events.MessageDeleted())
    async def on_message_deleted(event):
        await self._processor.handle_message_deleted(phone, event)
```

### 3.3 Background Task Scheduler

Use `apscheduler` or a simple asyncio task:

```python
async def _start_scheduler(self):
    """Start background sync tasks"""
    
    # Dialog sync every 30 minutes for all connected accounts
    async def dialog_sync_task():
        while self._running:
            for phone in self._pool.get_connected_accounts():
                try:
                    await self._sync_engine.sync_dialogs(phone)
                except Exception as e:
                    logger.error(f"Dialog sync failed for {phone}: {e}")
            await asyncio.sleep(30 * 60)  # 30 minutes
    
    # Full sync every 12 hours
    async def full_sync_task():
        while self._running:
            await asyncio.sleep(12 * 60 * 60)  # 12 hours
            for phone in self._pool.get_connected_accounts():
                try:
                    await self._sync_engine.full_sync(phone)
                except Exception as e:
                    logger.error(f"Full sync failed for {phone}: {e}")
    
    # Process pending backfills every 5 minutes
    async def backfill_task():
        while self._running:
            for phone in self._pool.get_connected_accounts():
                try:
                    await self._sync_engine.process_pending_backfills(phone)
                except Exception as e:
                    logger.error(f"Backfill failed for {phone}: {e}")
            await asyncio.sleep(5 * 60)  # 5 minutes
    
    asyncio.create_task(dialog_sync_task())
    asyncio.create_task(full_sync_task())
    asyncio.create_task(backfill_task())
```

---

## 4. API Endpoints

Add these endpoints to `api_server.py`:

### 4.1 Connection Management

```
POST /api/inbox/connect
    Body: { "phone": "1234567890" }
    Response: { "success": true, "connected": true }
    
POST /api/inbox/disconnect  
    Body: { "phone": "1234567890" }
    Response: { "success": true }

POST /api/inbox/connect-all
    Response: { "success": true, "results": { "123...": true, "456...": false } }

GET /api/inbox/connection-status
    Response: { 
        "connected_accounts": ["123...", "456..."],
        "total_connected": 2,
        "accounts": {
            "123...": { "connected": true, "connected_at": "...", "dialogs": 45 }
        }
    }
```

### 4.2 Conversations

```
GET /api/inbox/{phone}/conversations
    Query: ?limit=50&offset=0&unread_only=false&matrix_only=false
    Response: {
        "conversations": [
            {
                "peer_id": 123456,
                "username": "johndoe",
                "first_name": "John",
                "last_name": "Doe",
                "last_msg_text": "Hey there!",
                "last_msg_date": "2025-12-05T10:30:00Z",
                "last_msg_is_outgoing": false,
                "unread_count": 3,
                "is_matrix_contact": true,
                "contact_type": "dev",
                "contact_status": "blue",
                "their_last_read_msg_id": 45
            }
        ],
        "total": 150,
        "has_more": true
    }

GET /api/inbox/{phone}/conversations/{peer_id}
    Response: { "conversation": { ... full details ... } }
```

### 4.3 Messages

```
GET /api/inbox/{phone}/conversations/{peer_id}/messages
    Query: ?limit=50&before_msg_id=100
    Response: {
        "messages": [
            {
                "msg_id": 99,
                "from_id": 123456,
                "is_outgoing": false,
                "text": "Hello!",
                "date": "2025-12-05T10:29:00Z",
                "is_read": true,
                "read_at": "2025-12-05T10:29:30Z",
                "media_type": null,
                "reply_to_msg_id": null
            }
        ],
        "has_more": true
    }
```

### 4.4 Sync Triggers

```
POST /api/inbox/{phone}/sync/dialogs
    Response: { 
        "success": true, 
        "synced": 45, 
        "gaps_detected": 3,
        "duration_ms": 1234 
    }

POST /api/inbox/{phone}/sync/full
    Response: { 
        "success": true, 
        "dialogs_synced": 45,
        "messages_backfilled": 127,
        "duration_ms": 5678
    }

POST /api/inbox/{phone}/conversations/{peer_id}/backfill
    Response: { "success": true, "messages_fetched": 23 }
```

### 4.5 Metrics

```
GET /api/inbox/metrics
    Query: ?phone=123...&campaign_id=abc
    Response: {
        "total_conversations": 450,
        "total_messages": 12500,
        "unread_messages": 34,
        "messages_read_by_recipients": 890,
        "reply_rate": 0.23,
        "avg_response_time_hours": 4.5
    }

GET /api/inbox/campaigns/{campaign_id}/metrics
    Response: {
        "campaign_id": "abc",
        "name": "Dev Outreach Q4",
        "total_contacts": 500,
        "total_reached": 450,
        "total_replies": 103,
        "reply_rate": 0.229,
        "read_rate": 0.78,
        "avg_response_time_hours": 3.2
    }
```

### 4.6 Events/Notifications

```
GET /api/inbox/events
    Query: ?phone=123...&type=message_read&since=2025-12-05T00:00:00Z&limit=100
    Response: {
        "events": [
            {
                "id": 456,
                "account_phone": "123...",
                "peer_id": 789,
                "event_type": "message_read",
                "event_data": { "max_id": 50, "read_count": 3 },
                "created_at": "2025-12-05T10:30:00Z"
            }
        ]
    }
```

---

## 5. WebSocket Events

### 5.1 Client → Server

```javascript
// Subscribe to inbox events for an account
socket.emit('inbox:subscribe', { phone: '1234567890' });

// Unsubscribe
socket.emit('inbox:unsubscribe', { phone: '1234567890' });

// Subscribe to all connected accounts
socket.emit('inbox:subscribe_all');

// Mark conversation as read (optional)
socket.emit('inbox:mark_read', { phone: '123...', peer_id: 456, msg_id: 100 });
```

### 5.2 Server → Client

```javascript
// New message received
socket.on('inbox:new_message', {
    account_phone: '1234567890',
    peer_id: 123456,
    message: {
        msg_id: 99,
        from_id: 123456,
        is_outgoing: false,
        text: 'Hello!',
        date: '2025-12-05T10:29:00Z'
    },
    conversation: {
        unread_count: 4,
        last_msg_text: 'Hello!'
    }
});

// Message read (they read our message)
socket.on('inbox:message_read', {
    account_phone: '1234567890',
    peer_id: 123456,
    max_read_id: 95,           // Highest msg_id they've read
    read_count: 3,             // How many messages newly marked read
    timestamp: '2025-12-05T10:30:00Z'
});

// Message edited
socket.on('inbox:message_edited', {
    account_phone: '1234567890',
    peer_id: 123456,
    msg_id: 88,
    new_text: 'Edited message',
    edit_date: '2025-12-05T10:31:00Z'
});

// Message deleted
socket.on('inbox:message_deleted', {
    account_phone: '1234567890',
    peer_id: 123456,
    msg_ids: [85, 86],         // Can be multiple
    timestamp: '2025-12-05T10:32:00Z'
});

// First reply from blue contact (triggers emoji update)
socket.on('inbox:first_reply', {
    account_phone: '1234567890',
    peer_id: 123456,
    contact_type: 'dev',
    campaign_id: 'abc123',
    message: { ... }
});

// Connection status change
socket.on('inbox:connection_status', {
    account_phone: '1234567890',
    connected: true,
    event: 'connected'         // 'connected', 'disconnected', 'reconnecting'
});

// Sync completed
socket.on('inbox:sync_complete', {
    account_phone: '1234567890',
    sync_type: 'dialogs',      // 'dialogs' or 'full'
    result: {
        synced: 45,
        gaps_detected: 3
    }
});
```

---

## 6. Frontend Integration

### 6.1 New React Hook: `useInbox`

Create `frontend/src/hooks/useInbox.js`:

```javascript
import { useState, useEffect, useCallback } from 'react';
import { useWebSocket } from './useWebSocket';
import api from '../services/api';

export function useInbox(phone) {
    const [conversations, setConversations] = useState([]);
    const [loading, setLoading] = useState(true);
    const [connected, setConnected] = useState(false);
    const { socket, subscribeToEvent } = useWebSocket();
    
    // Subscribe to inbox events
    useEffect(() => {
        if (!phone || !socket) return;
        
        socket.emit('inbox:subscribe', { phone });
        
        const unsubNewMsg = subscribeToEvent('inbox:new_message', (data) => {
            if (data.account_phone === phone) {
                // Update conversations list
                setConversations(prev => updateConversationWithMessage(prev, data));
            }
        });
        
        const unsubRead = subscribeToEvent('inbox:message_read', (data) => {
            if (data.account_phone === phone) {
                // Update read status
            }
        });
        
        return () => {
            socket.emit('inbox:unsubscribe', { phone });
            unsubNewMsg();
            unsubRead();
        };
    }, [phone, socket]);
    
    // Fetch conversations
    const fetchConversations = useCallback(async () => {
        setLoading(true);
        try {
            const response = await api.getInboxConversations(phone);
            setConversations(response.data.conversations);
        } finally {
            setLoading(false);
        }
    }, [phone]);
    
    return {
        conversations,
        loading,
        connected,
        fetchConversations,
        // ... other methods
    };
}
```

### 6.2 New Page: `Inbox.jsx`

Create `frontend/src/pages/Inbox.jsx` with:
- Account selector (if multiple accounts)
- Conversations list (left panel)
- Message thread (right panel)
- Real-time updates via WebSocket
- Unread badges
- Read receipt indicators

### 6.3 API Service Updates

Add to `frontend/src/services/api.js`:

```javascript
// Inbox connection
connectInbox: (phone) => axios.post('/api/inbox/connect', { phone }),
disconnectInbox: (phone) => axios.post('/api/inbox/disconnect', { phone }),
connectAllInbox: () => axios.post('/api/inbox/connect-all'),
getConnectionStatus: () => axios.get('/api/inbox/connection-status'),

// Conversations
getInboxConversations: (phone, params) => 
    axios.get(`/api/inbox/${phone}/conversations`, { params }),
getInboxConversation: (phone, peerId) => 
    axios.get(`/api/inbox/${phone}/conversations/${peerId}`),

// Messages
getInboxMessages: (phone, peerId, params) => 
    axios.get(`/api/inbox/${phone}/conversations/${peerId}/messages`, { params }),

// Sync
triggerDialogSync: (phone) => axios.post(`/api/inbox/${phone}/sync/dialogs`),
triggerFullSync: (phone) => axios.post(`/api/inbox/${phone}/sync/full`),

// Metrics
getInboxMetrics: (params) => axios.get('/api/inbox/metrics', { params }),
getCampaignMetrics: (campaignId) => axios.get(`/api/inbox/campaigns/${campaignId}/metrics`),
```

---

## 7. Sync Logic

### 7.1 Gap Detection Algorithm

```python
async def sync_dialogs(self, account_phone: str) -> SyncResult:
    """
    ALGORITHM: Dialog Sync with Gap Detection
    
    Goal: Minimize API calls while keeping inbox in sync
    
    Steps:
    1. Fetch all dialogs (1 API call)
    2. For each private chat dialog:
       - Get dialog.message (last message, included in response)
       - Get db.last_msg_id from inbox_conversations
       - Calculate gap = dialog.message.id - db.last_msg_id
       
    3. Handle based on gap:
       - gap == 0: No new messages, skip
       - gap == 1: Single new message
           - Use dialog.message directly (0 extra API calls)
           - Insert into inbox_messages
           - Update inbox_conversations.last_msg_id
       - gap >= 2: Multiple new messages (gap)
           - Mark conversation needs_backfill = TRUE
           - Set backfill_from_msg_id = db.last_msg_id
           - Backfill task will fetch missing messages
           
    4. Handle deleted messages:
       - If dialog.message.id < db.last_msg_id:
           - Messages were deleted
           - Update db.last_msg_id = dialog.message.id
           - Mark deleted messages in inbox_messages
           
    5. Update sync state
    """
    
    client = self._pool.get_client(account_phone)
    if not client:
        raise ValueError(f"Account {account_phone} not connected")
    
    result = SyncResult()
    
    # 1. Fetch all dialogs (single API call)
    dialogs = await client.get_dialogs()
    result.dialogs_fetched = len(dialogs)
    
    # 2. Process each private chat
    for dialog in dialogs:
        # Skip groups/channels - only private chats
        if not dialog.is_user:
            continue
        
        peer_id = dialog.entity.id
        dialog_msg = dialog.message
        
        if not dialog_msg:
            continue
        
        dialog_msg_id = dialog_msg.id
        
        # Get current state from database
        db_conversation = await self._get_conversation(account_phone, peer_id)
        db_last_msg_id = db_conversation['last_msg_id'] if db_conversation else 0
        
        # Calculate gap
        gap = dialog_msg_id - db_last_msg_id
        
        if gap == 0:
            # No new messages
            result.skipped += 1
            continue
        
        elif gap == 1:
            # Single new message - use from dialog (no extra API call)
            await self._insert_message_from_dialog(account_phone, peer_id, dialog_msg)
            await self._update_conversation(account_phone, peer_id, dialog_msg)
            result.synced += 1
        
        elif gap > 1:
            # Multiple messages - mark for backfill
            await self._mark_needs_backfill(account_phone, peer_id, db_last_msg_id)
            result.gaps_detected += 1
        
        elif gap < 0:
            # Messages were deleted (dialog_msg_id < db_last_msg_id)
            await self._handle_deleted_messages(account_phone, peer_id, dialog_msg_id)
            result.deletions_detected += 1
    
    # Update sync timestamp
    await self._update_sync_state(account_phone, 'dialog_sync')
    
    return result
```

### 7.2 Message Backfill Algorithm

```python
async def backfill_conversation(self, account_phone: str, peer_id: int,
                                from_msg_id: int, limit: int = 100) -> int:
    """
    ALGORITHM: Fill message gap for a conversation
    
    When gap >= 2 detected, fetch missing messages.
    
    Steps:
    1. Get messages with min_id = from_msg_id (fetches newer)
    2. Insert all into inbox_messages (ignore duplicates)
    3. Update conversation.last_msg_id
    4. Clear needs_backfill flag
    
    Rate limiting: This is 1 API call per conversation with gap
    """
    
    client = self._pool.get_client(account_phone)
    
    # Fetch messages newer than from_msg_id
    messages = await client.get_messages(
        peer_id,
        min_id=from_msg_id,
        limit=limit
    )
    
    inserted = 0
    latest_msg_id = from_msg_id
    
    for msg in messages:
        if msg.id > latest_msg_id:
            latest_msg_id = msg.id
        
        # Insert message (ignore if exists)
        try:
            await self._insert_message(account_phone, peer_id, msg, synced_via='backfill')
            inserted += 1
        except sqlite3.IntegrityError:
            pass  # Duplicate, skip
    
    # Update conversation state
    await self._update_conversation_after_backfill(account_phone, peer_id, latest_msg_id)
    
    return inserted
```

---

## 8. Files to Create

| File | Purpose | Estimated Lines |
|------|---------|-----------------|
| `backend/inbox_manager.py` | Main inbox management classes | ~1500 |
| `backend/inbox_db.py` | Database operations for inbox tables | ~400 |
| `frontend/src/pages/Inbox.jsx` | Inbox UI page | ~500 |
| `frontend/src/hooks/useInbox.js` | React hook for inbox state | ~200 |
| `frontend/src/components/ConversationList.jsx` | Conversation list component | ~150 |
| `frontend/src/components/MessageThread.jsx` | Message thread component | ~200 |
| `frontend/src/components/MessageBubble.jsx` | Single message display | ~100 |

---

## 9. Files to Modify

### 9.1 `backend/account_manager.py`

Add functions:
```python
# Add to existing file

def init_inbox_tables():
    """Initialize inbox-related tables"""
    # Execute CREATE TABLE statements from schema above
    pass

def get_inbox_db_connection():
    """Get connection with WAL mode for inbox operations"""
    pass
```

### 9.2 `backend/api_server.py`

Add:
1. Import `InboxManager`
2. Initialize global `inbox_manager` instance
3. Add all API endpoints from Section 4
4. Add WebSocket event handlers from Section 5
5. Start inbox manager on server startup

```python
# Add near top of file
from inbox_manager import InboxManager

# Add after socketio initialization
inbox_manager = None

# Add startup hook
@app.before_first_request
async def initialize_inbox():
    global inbox_manager
    inbox_manager = InboxManager(socketio)
    await inbox_manager.start()
    await inbox_manager.connect_all_active_accounts()

# Add all /api/inbox/* endpoints
# ... (see Section 4)
```

### 9.3 `frontend/src/App.jsx`

Add route for Inbox page:
```jsx
<Route path="/inbox" element={<Inbox />} />
```

### 9.4 `frontend/src/services/api.js`

Add all inbox API methods (see Section 6.3)

### 9.5 `frontend/src/components/Sidebar.jsx` (or navigation)

Add navigation link to Inbox page

---

## 10. Migration Guide

### 10.1 Database Migration

Create `backend/migrations/001_inbox_tables.py`:

```python
"""
Migration: Add inbox management tables
Run: python backend/migrations/001_inbox_tables.py
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / 'accounts.db'

def migrate():
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Enable WAL mode
    cursor.execute('PRAGMA journal_mode=WAL')
    
    # Create tables (paste SQL from Section 2.1)
    cursor.executescript('''
        -- inbox_conversations table
        CREATE TABLE IF NOT EXISTS inbox_conversations (...);
        
        -- inbox_messages table  
        CREATE TABLE IF NOT EXISTS inbox_messages (...);
        
        -- inbox_events table
        CREATE TABLE IF NOT EXISTS inbox_events (...);
        
        -- inbox_campaigns table
        CREATE TABLE IF NOT EXISTS inbox_campaigns (...);
        
        -- inbox_connection_state table
        CREATE TABLE IF NOT EXISTS inbox_connection_state (...);
        
        -- Create indexes
        CREATE INDEX IF NOT EXISTS ...;
    ''')
    
    conn.commit()
    conn.close()
    print("✅ Inbox tables created successfully")

if __name__ == '__main__':
    migrate()
```

### 10.2 Linking Existing Contacts

After migration, link existing MATRIX contacts to inbox:

```python
def link_matrix_contacts_to_inbox():
    """
    For each existing emoji contact (🔵/🟡), update inbox_conversations
    to set is_matrix_contact=TRUE, contact_type, contact_status
    """
    pass
```

---

## 11. Testing Checklist

### 11.1 Unit Tests

- [ ] ConnectionPool: connect, disconnect, reconnect
- [ ] EventProcessor: handle each event type
- [ ] SyncEngine: gap detection logic
- [ ] Database: CRUD operations for all tables

### 11.2 Integration Tests

- [ ] Full sync cycle (dialog sync → backfill)
- [ ] Real-time event flow (Telegram → DB → WebSocket → Frontend)
- [ ] Multi-account concurrent operations
- [ ] Reconnection after disconnect

### 11.3 Manual Testing

1. **Basic flow:**
   - Connect account
   - Receive message → verify in DB and WebSocket
   - Send message from phone → verify outgoing detected
   - Have contact read message → verify read receipt

2. **Gap detection:**
   - Disconnect account
   - Send/receive multiple messages via phone
   - Reconnect → verify backfill works

3. **Stress test:**
   - Connect 10+ accounts simultaneously
   - Generate message activity
   - Verify no missed events

---

## 12. Open Questions for Implementation

Before starting implementation, clarify:

1. **Campaign ID assignment:** How should contacts be assigned to campaigns during import? Auto-generate from CSV filename?

2. **Message retention:** Keep all messages forever, or implement cleanup after N days?

3. **Connection limits:** Max simultaneous connections? LRU eviction if exceeded?

4. **Outreach platform:** Does the other platform use these same Telegram sessions? Will its messages appear in Telethon events?

5. **Read receipts precision:** Just boolean `is_read`, or also `read_at` timestamp?

6. **Typing indicators:** Include typing events, or skip for simplicity?

7. **Online status:** Track user online/offline, or skip?

---

## Document End

**Next Steps:**
1. Answer open questions in Section 12
2. Run database migration
3. Implement `inbox_manager.py`
4. Add API endpoints
5. Build frontend Inbox page
6. Test end-to-end

**Estimated Implementation Time:** 15-25 hours

---

*This specification was generated for MATRIX v1.1 on December 5, 2025.*
