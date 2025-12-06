# MATRIX - AI Context

**Project:** Multi-account Telegram contact manager for Web3/crypto outreach
**Tech:** Python Flask + React 18 + SQLite + Telethon (StringSession)

---

## File Map

```
backend/
├── api_server.py         # Main API + UnifiedContactManager
├── tg_client.py          # TGClient wrapper (StringSession + singleton + loop detection)
├── connection_manager.py # GlobalConnectionManager (shared client pool)
├── inbox_manager.py      # Real-time inbox (WebSocket)
├── account_manager.py    # Database CRUD
└── migrate_sessions.py   # SQLite → StringSession migration (utility)

frontend/src/
├── pages/                # Dashboard, Inbox, Operations, Accounts, Import
├── services/api.js       # API client
└── hooks/useInbox.js     # WebSocket hook
```

---

## Session Architecture

**StringSession** (NOT SQLite):
- Plain text files (~350 chars) in `sessions/session_{phone}.session`
- No lock files, no database conflicts
- TGClient auto-detects event loop changes and creates fresh instances

**Key Classes:**
- `TGClient` - Singleton wrapper with loop detection
- `GlobalConnectionManager` - Shared pool for all components
- Both InboxManager and UnifiedContactManager share connections

---

## Rate Limits (NEVER reduce)

```python
BATCH_SIZE: 3-7 contacts
PER_CONTACT_DELAY: 2-6 seconds
BATCH_DELAY: 45-90 seconds
```

---

## Emojis

🔵💻 Blue Dev | 🟡💻 Yellow Dev | 🔵📢 Blue KOL | 🟡📢 Yellow KOL

---

## Run

```bash
python backend/api_server.py  # :5000
cd frontend && npm run dev    # :5173
```

---

## Debug

```bash
dir sessions\                              # List sessions
del sessions\session_*.session             # Delete (forces re-auth)
pkill -f "python.*api_server"              # Kill server (Linux/Git Bash)
```

---

**Rule:** Account bans = project failure. Safety over speed.
