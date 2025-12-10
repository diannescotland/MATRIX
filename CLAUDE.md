# MATRIX - AI Context

**Project:** Multi-account Telegram contact manager for Web3/crypto outreach
**Tech:** Python Flask + React 18 + SQLite + Telethon (StringSession)

---

## File Map

```
backend/
├── api_server.py         # Main API + UnifiedContactManager + all REST endpoints
├── tg_client.py          # TGClient wrapper (StringSession + singleton + loop detection)
├── connection_manager.py # GlobalConnectionManager (shared client pool)
├── inbox_manager.py      # Real-time inbox (WebSocket) + ProfilePhotoSyncer
├── account_manager.py    # Database CRUD + schema migrations
└── migrate_sessions.py   # SQLite → StringSession migration (utility)

frontend/src/
├── pages/                # Dashboard, Inbox, Operations, Accounts, Import
├── services/api.js       # API client (all endpoints)
└── hooks/useInbox.js     # WebSocket hook + auto-fetch history
```

---

## Session Architecture

**StringSession** (NOT SQLite):
- Plain text files (~350 chars) in `sessions/session_{phone}.session`
- No lock files, no database conflicts
- TGClient auto-detects event loop changes and creates fresh instances

**Key Classes:**
- `TGClient` - Singleton wrapper with loop detection + auto-cleanup
- `GlobalConnectionManager` - Shared pool for all components
- Both InboxManager and UnifiedContactManager share connections

**Event Loop Handling (CRITICAL):**
- Import operations run in **separate threads** with their own event loops
- TGClient singleton detects loop changes and **auto-disconnects** old clients
- GlobalConnectionManager **removes singleton cache** on disconnect
- This prevents "asyncio event loop must not change" errors

---

## Inbox System

### Database Tables
- `inbox_conversations` - Conversation metadata + profile photos + history status
- `inbox_messages` - Message history (stored locally after fetch)

### Key Columns in `inbox_conversations`
```sql
peer_id, first_name, last_name, username, last_msg_text, last_msg_date,
unread_count, is_contact, contact_status,
profile_photo_base64,      -- Base64 JPEG (~15KB per photo)
profile_photo_id,          -- Telegram photo_id for change detection
profile_photo_status,      -- pending/fetched/no_photo/error
history_fetched            -- Boolean: full history fetched from Telegram?
```

### Profile Photo System
- **Background sync**: `ProfilePhotoSyncer` runs every 10 minutes
- **20 photos max** per account per run (rate limit protection)
- **Change detection**: Only re-downloads if `photo_id` differs
- **Storage**: Base64 in SQLite (~15KB per photo)
- **Frontend**: Falls back to DiceBear initials if no photo

### Message History Fetch
- **On-demand**: Full history fetched when conversation opened first time
- **Trigger**: `useInbox.js` checks `history_fetched` flag
- **API**: `POST /api/inbox/<phone>/conversations/<peer_id>/fetch-history`
- **Batching**: Fetches 100 messages at a time until complete
- **Default display**: 200 messages per conversation

### WebSocket Events (Socket.IO)
```
inbox:subscribe / inbox:unsubscribe
inbox:new_message
inbox:message_read
inbox:typing
inbox:user_status
inbox:first_reply (blue → yellow status change)
inbox:connection_status
```

---

## Contact Status Emojis

| Status | Meaning |
|--------|---------|
| 🔵💻 | Blue Dev - No reply yet |
| 🟡💻 | Yellow Dev - Has replied |
| 🔵📢 | Blue KOL - No reply yet |
| 🟡📢 | Yellow KOL - Has replied |

---

## Rate Limits (NEVER reduce)

```python
# Outreach operations
BATCH_SIZE: 3-7 contacts
PER_CONTACT_DELAY: 2-6 seconds
BATCH_DELAY: 45-90 seconds

# Profile photo sync
PHOTOS_PER_RUN: 20 (every 10 minutes)
DELAY_BETWEEN_PHOTOS: 0.5 seconds
```

---

## Key API Endpoints

### Accounts
- `GET /api/accounts` - List all accounts
- `POST /api/accounts` - Add new account
- `POST /api/accounts/<phone>/connect` - Connect to Telegram
- `DELETE /api/accounts/<phone>` - Remove account

### Inbox
- `GET /api/inbox/<phone>/conversations` - List conversations
- `GET /api/inbox/<phone>/conversations/<peer_id>/messages` - Get messages
- `POST /api/inbox/<phone>/conversations/<peer_id>/fetch-history` - Fetch full history
- `POST /api/inbox/<phone>/send` - Send message

### Contacts
- `GET /api/contacts` - List contacts (with filters)
- `POST /api/import/devs` - Import Dev contacts from CSV
- `POST /api/import/kols` - Import KOL contacts from CSV

### Operations
- `POST /api/operations/scan-replies` - Scan for replies (blue → yellow)
- `POST /api/operations/send-dm` - Send DM campaign

---

## Run

```bash
python backend/api_server.py  # :5000
cd frontend && npm run dev    # :5173
```

---

## Debug

```bash
# Sessions
dir sessions\                              # List sessions
del sessions\session_*.session             # Delete (forces re-auth)

# Server
pkill -f "python.*api_server"              # Kill server (Linux/Git Bash)
taskkill /F /IM python.exe                 # Kill server (Windows)

# Database
sqlite3 data/matrix.db ".schema inbox_conversations"  # View schema
```

## Troubleshooting

**"asyncio event loop must not change" error:**
- Cause: TGClient singleton was created in a different thread's event loop
- Fix: Restart the backend server - singleton cache will be fresh
- Prevention: Code now auto-cleans stale instances on loop change

**Contact import shows 0% success rate:**
- Check if account is connected in Inbox (may hold session lock)
- Restart backend to release all connections
- Verify session file exists: `sessions/session_{phone}.session`

**Session expired / Auth key invalid:**
- Delete session file: `del sessions\session_{phone}.session`
- Re-authenticate via Accounts page

---

**Rule:** Account bans = project failure. Safety over speed.
