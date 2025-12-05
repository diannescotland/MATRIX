# DM System Logic Documentation

This document explains the core logic, architecture, and methodology used in the DM (Direct Message) system for the Drexil Mass DM platform.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Dialog Fetching System](#2-dialog-fetching-system)
3. [Inbox Retrieval Flow](#3-inbox-retrieval-flow)
4. [Message Sending](#4-message-sending)
5. [DM Duplicate Detection](#5-dm-duplicate-detection)
6. [Reply Rate Calculation](#6-reply-rate-calculation)
7. [Session Persistence](#7-session-persistence)
8. [State Management](#8-state-management)
9. [API Endpoints Reference](#9-api-endpoints-reference)

---

## 1. Architecture Overview

### Technology Stack

| Component | Technology |
|-----------|------------|
| Backend Framework | Django 5.2.1 + Django REST Framework |
| Task Queue | Celery 5.5.2 + Redis |
| Primary Database | PostgreSQL |
| State Persistence | MongoDB |
| Cache | Redis |
| HTTP Client (X) | httpx (HTTP/2 support) |
| Telegram Client | Telethon 1.40.0 (MTProto) |
| Authentication | JWT (djangorestframework-simplejwt) |

### Core Module Structure

```
drexil_mass_dm/
├── x/                          # Twitter/X client & strategies
│   ├── req.py                  # Base HTTP client (XClient)
│   ├── dm.py                   # DM handler with inbox methods
│   ├── strategies/             # DM strategies
│   │   └── dm_targets.py       # Targeted DM strategy
│   └── db/
│       └── client.py           # MongoDB state managers
├── tg/                         # Telegram client & strategies
│   ├── client.py               # Async TGClient (singleton)
│   ├── strategies/             # TG DM strategies
│   │   └── dm_target_manual_opener.py
│   └── scraping/
│       └── dialog_scraper.py   # Dialog processing
├── inbox/                      # X/Twitter inbox API views
├── inbox_tg/                   # Telegram inbox API views
├── mass_dm/                    # X DM campaigns
├── mass_dm_tg/                 # TG DM campaigns
├── agents_manager/             # X agent management
├── agents_manager_tg/          # TG agent management
└── core/
    └── tasks.py                # Celery tasks
```

### Agent Models

#### Twitter/X Agent (`agents_manager/models.py`)
```python
class Agent(models.Model):
    inaid = models.CharField(max_length=255, unique=True)  # Internal ID
    x_id = models.CharField(max_length=255)                 # Twitter user ID
    xhandler = models.CharField(max_length=255)             # @username
    credentials = models.JSONField()  # {"auth_token": "...", "ct0": "..."}
    credentials_hash = models.CharField(max_length=64, unique=True)
    proxy = models.CharField(max_length=255, blank=True)
    tag = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    config = models.JSONField(default=dict)
    persona = models.TextField(blank=True)
    is_self = models.BooleanField(default=True)  # False if rented
```

#### Telegram Agent (`agents_manager_tg/models.py`)
```python
class TGAgent(models.Model):
    inaid = models.CharField(max_length=255, unique=True)
    username = models.CharField(max_length=255, blank=True)
    auth = models.JSONField()  # {"api_id": 12345, "api_hash": "...", "phone_number": "+1..."}
    credentials_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=50, choices=Status.choices, default=Status.ACTIVE)
    status_detail = models.TextField(blank=True)
    last_status_checked_at = models.DateTimeField(null=True)
```

---

## 2. Dialog Fetching System

### 2.1 Twitter/X Dialog Architecture

#### Key Files
- `x/req.py` - Base HTTP client (`XClient`)
- `x/dm.py` - DM handler with inbox methods (`Handler`)

#### HTTP Client Setup (`x/req.py`)
```python
class XClient:
    def __init__(self, auth_token: str, ct0: str, proxy: str = None):
        self.cookies = {
            "auth_token": auth_token,
            "ct0": ct0,
        }
        self.client = Client(
            http2=True,
            timeout=20,
            cookies=self.cookies,
            proxy=proxy,
        )

    def _headers(self):
        return {
            'authorization': 'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs...',
            'x-csrf-token': self.cookies["ct0"],
            'x-twitter-active-user': 'yes',
            'x-twitter-auth-type': 'OAuth2Session',
            'x-twitter-client-language': 'en',
            # ... other headers
        }
```

#### Dialog Fetching Methods (`x/dm.py`)

**1. Initial Inbox (Single API Call)**
```python
def get_initial_inbox(self, raw: bool = False) -> Dict:
    """
    Fetch inbox with recent conversations in ONE API call.
    Returns the last ~50 conversations with their messages.

    Endpoint: GET https://x.com/i/api/1.1/dm/user_updates.json
    """
    r = self._request(
        "GET",
        "https://x.com/i/api/1.1/dm/user_updates.json",
        params={
            "nsfw_filtering_enabled": "false",
            "include_groups": "true",
            "include_inbox_timelines": "true",
            "supports_reactions": "true",
            "include_ext_is_blue_verified": "1",
            # ... other params
        }
    )
    if raw:
        return r
    return self._structure_inbox_response(r)
```

**2. Paginated Inbox Timeline**
```python
def get_inbox_timeline(self, max_id: str = None, raw: bool = False) -> Dict:
    """
    Fetch older conversations using pagination.

    Endpoint: GET https://x.com/i/api/1.1/dm/inbox_timeline/trusted.json
    """
    params = {
        "filter": "trusted",
        "include_groups": "true",
        "supports_reactions": "true",
        # ... other params
    }
    if max_id:
        params["max_id"] = max_id

    r = self._request("GET",
        "https://x.com/i/api/1.1/dm/inbox_timeline/trusted.json",
        params=params
    )
    return r if raw else self._structure_inbox_response(r)
```

**3. Get All Conversations**
```python
def get_all_conversations(self, max_pages: int = 10) -> Dict:
    """
    Fetch ALL conversations by paginating through the inbox.
    Combines initial_inbox + multiple get_inbox_timeline calls.
    """
    all_conversations = {}
    cursor = None

    # First call - initial inbox
    result = self.get_initial_inbox(raw=False)
    all_conversations.update(result.get("conversations", {}))
    cursor = result.get("cursor")

    # Paginate through older conversations
    for _ in range(max_pages):
        if not cursor or not result.get("has_more"):
            break

        result = self.get_inbox_timeline(max_id=cursor, raw=False)
        all_conversations.update(result.get("conversations", {}))
        cursor = result.get("cursor")

    return {"conversations": all_conversations, "total": len(all_conversations)}
```

#### Response Structuring (`x/dm.py`)

The raw X API response is transformed into a normalized structure:

```python
def _structure_inbox_response(self, raw_response: Dict) -> Dict:
    """
    Transform raw API response into structured format.

    Input: Raw X API response with inbox_initial_state
    Output: Normalized conversation structure
    """
    inbox_state = raw_response.get("inbox_initial_state", {})
    conversations = inbox_state.get("conversations", {})
    entries = inbox_state.get("entries", [])
    users = inbox_state.get("users", {})

    structured = {
        "conversations": {},
        "cursor": inbox_state.get("cursor"),
        "has_more": bool(inbox_state.get("cursor")),
        "last_seen": inbox_state.get("last_seen_event_id"),
    }

    # Build conversation metadata
    for conv_id, conv_data in conversations.items():
        participants = [p["user_id"] for p in conv_data.get("participants", [])]

        # Get participant user info
        conv_users = {}
        for user_id in participants:
            if str(user_id) in users:
                user = users[str(user_id)]
                conv_users[str(user_id)] = {
                    "name": user.get("name"),
                    "screen_name": user.get("screen_name"),
                    "profile_image_url": user.get("profile_image_url_https"),
                    "verified": user.get("is_blue_verified", False),
                }

        structured["conversations"][conv_id] = {
            "participants": participants,
            "users": conv_users,
            "messages": [],
            "sort_timestamp": conv_data.get("sort_timestamp"),
            "is_read": True,  # Updated below
        }

    # Process message entries
    for entry in entries:
        if "message" not in entry:
            continue

        msg = entry["message"]
        conv_id = msg["conversation_id"]
        msg_data = msg["message_data"]

        if conv_id in structured["conversations"]:
            structured["conversations"][conv_id]["messages"].append({
                "id": msg_data["id"],
                "sender_id": msg_data["sender_id"],
                "text": msg_data.get("text", ""),
                "timestamp": int(msg_data["time"]),
                "is_agent": str(msg_data["sender_id"]) == str(self.agent_id),
                "reactions": msg.get("message_reactions", []),
            })

    # Sort messages by timestamp (newest first) and determine read status
    for conv_id, conv in structured["conversations"].items():
        conv["messages"].sort(key=lambda m: m["timestamp"], reverse=True)
        if conv["messages"]:
            conv["last_message_time"] = conv["messages"][0]["timestamp"]
            # Check if latest message from other party is unread
            latest = conv["messages"][0]
            if not latest["is_agent"]:
                # Compare with our last_read_event_id
                conv["is_read"] = False  # Simplified; full logic compares IDs

    return structured
```

#### Structured Response Format (Twitter/X)
```python
{
    "cursor": "DMConversation-...",
    "has_more": True,
    "last_seen": "1234567890123456789",
    "conversations": {
        "recipient_id-agent_id": {
            "participants": ["recipient_id", "agent_id"],
            "users": {
                "recipient_id": {
                    "name": "John Doe",
                    "screen_name": "johndoe",
                    "profile_image_url": "https://pbs.twimg.com/...",
                    "verified": True
                }
            },
            "messages": [
                {
                    "id": "1234567890123456789",
                    "sender_id": "recipient_id",
                    "text": "Hello!",
                    "timestamp": 1699999999000,
                    "is_agent": False,
                    "reactions": []
                }
            ],
            "sort_timestamp": "1699999999000",
            "is_read": False,
            "last_message_time": 1699999999000
        }
    }
}
```

---

### 2.2 Telegram Dialog Architecture

#### Key Files
- `tg/client.py` - Async Telegram client (`TGClient`)
- `tg/scraping/dialog_scraper.py` - Dialog processing
- `inbox_tg/views.py` - API endpoints

#### TGClient Singleton Pattern (`tg/client.py`)

```python
class TGClient:
    """
    Telegram client wrapper with singleton pattern to prevent connection leaks.
    One instance per (session_name, api_id, api_hash) combination.
    """
    _instances: Dict[Tuple[str, int, str], 'TGClient'] = {}
    _last_active: Dict[Tuple[str, int, str], float] = {}
    _cleanup_interval = 7200  # 2 hours

    def __new__(cls, session_name: str, api_id: int, api_hash: str, **kwargs):
        instance_key = (session_name, api_id, api_hash)

        if instance_key in cls._instances:
            instance = cls._instances[instance_key]
            cls._last_active[instance_key] = time.time()
            return instance

        instance = super().__new__(cls)
        cls._instances[instance_key] = instance
        cls._last_active[instance_key] = time.time()
        return instance

    def __init__(self, session_name: str, api_id: int, api_hash: str, proxy: str = None):
        if hasattr(self, '_initialized') and self._initialized:
            return

        self.session_name = session_name
        self.api_id = api_id
        self.api_hash = api_hash

        # Load session from file
        session_str = None
        if os.path.exists(session_name):
            with open(session_name, 'r') as f:
                session_str = f.read().strip()

        self.session = StringSession(session_str) if session_str else StringSession()
        self.client = TelegramClient(
            self.session,
            api_id,
            api_hash,
            proxy=parse_proxy(proxy) if proxy else None
        )
        self._initialized = True
```

#### Dialog Fetching Methods (`tg/client.py`)

**1. Get Inbox Chats**
```python
async def get_inbox_chats(
    self,
    limit: int = 50,
    offset_date: datetime = None,
    offset_id: int = 0,
    offset_peer: InputPeer = None,
    self_scraping: bool = False,
) -> Tuple[List[Dict], Optional[Dict]]:
    """
    Fetch dialogs (conversations) from Telegram.

    Args:
        limit: Maximum number of dialogs to fetch
        offset_date: Start from dialogs older than this date
        offset_id: Message ID offset for pagination
        offset_peer: Peer offset for pagination
        self_scraping: Include full details (username, etc.)

    Returns:
        Tuple of (dialogs_list, next_cursor)
    """
    await self.connect()

    dialogs = await self.client.get_dialogs(
        limit=limit,
        offset_date=offset_date,
        offset_id=offset_id,
        offset_peer=offset_peer,
    )

    result = []
    last_dialog = None

    for dialog in dialogs:
        entity = dialog.entity

        if self_scraping:
            dialog_data = self._build_self_scraping_dialog(dialog, entity)
        else:
            dialog_data = self._build_default_dialog(dialog, entity)

        result.append(dialog_data)
        last_dialog = dialog

    # Build pagination cursor
    cursor = None
    if last_dialog and len(dialogs) >= limit:
        cursor = {
            "offset_date": last_dialog.date.isoformat() if last_dialog.date else None,
            "offset_id": last_dialog.message.id if last_dialog.message else 0,
            "offset_peer": self._serialize_peer(last_dialog.entity),
        }

    return result, cursor
```

**2. Build Dialog Object**
```python
def _build_default_dialog(self, dialog, entity) -> Dict:
    """
    Build dialog dict for API response (default mode).
    Hides username for security.
    """
    return {
        "id": entity.id,
        "name": getattr(entity, 'title', None) or
                getattr(entity, 'first_name', '') + ' ' + getattr(entity, 'last_name', ''),
        "username": None,  # Hidden for security
        "unread_count": dialog.unread_count,
        "is_user": isinstance(entity, types.User),
        "is_group": isinstance(entity, (types.Chat, types.ChatForbidden)),
        "is_channel": isinstance(entity, (types.Channel, types.ChannelForbidden)),
        "last_message": self._format_message(dialog.message) if dialog.message else None,
    }

def _build_self_scraping_dialog(self, dialog, entity) -> Dict:
    """
    Build dialog dict with full details for internal scraping.
    Includes username and additional metadata.
    """
    base = self._build_default_dialog(dialog, entity)
    base.update({
        "username": getattr(entity, 'username', None),
        "access_hash": getattr(entity, 'access_hash', None),
        "is_bot": getattr(entity, 'bot', False),
        "is_verified": getattr(entity, 'verified', False),
        "is_restricted": getattr(entity, 'restricted', False),
        "photo": bool(getattr(entity, 'photo', None)),
    })
    return base

def _format_message(self, msg) -> Dict:
    """Format a message object for API response."""
    return {
        "id": msg.id,
        "text": msg.message or "",
        "date": msg.date.isoformat() if msg.date else None,
        "is_outgoing": msg.out,
    }
```

**3. Get Messages from Chat**
```python
async def get_messages_from_chat(
    self,
    chat_id: int,
    limit: int = 50,
    offset_id: int = 0,
) -> Tuple[List[Dict], Optional[int]]:
    """
    Fetch messages from a specific chat.

    Returns:
        Tuple of (messages_list, next_offset_id)
    """
    await self.connect()

    messages = []
    async for msg in self.client.iter_messages(
        chat_id,
        limit=limit,
        offset_id=offset_id,
    ):
        messages.append({
            "id": msg.id,
            "text": msg.message or "",
            "date": msg.date.isoformat() if msg.date else None,
            "is_outgoing": msg.out,
            "sender_id": msg.sender_id,
            "reply_to_msg_id": msg.reply_to_msg_id,
            "media": self._describe_media(msg.media) if msg.media else None,
        })

    next_offset = messages[-1]["id"] if messages else None
    return messages, next_offset
```

#### Cursor-Based Pagination (Telegram)

The pagination cursor is base64-encoded JSON:

```python
# Encoding cursor
def encode_cursor(cursor_data: dict) -> str:
    return base64.b64encode(json.dumps(cursor_data).encode()).decode()

# Decoding cursor
def decode_cursor(cursor_str: str) -> dict:
    return json.loads(base64.b64decode(cursor_str.encode()).decode())

# Cursor structure
cursor = {
    "offset_date": "2024-01-15T12:30:00+00:00",
    "offset_id": 12345,
    "offset_peer": {
        "id": 123456789,
        "type": "user",  # or "chat", "channel"
        "access_hash": "abc123..."
    }
}
```

#### Structured Response Format (Telegram)
```python
{
    "inbox": [
        {
            "id": 123456789,
            "name": "John Doe",
            "username": null,  # Hidden in default mode
            "unread_count": 3,
            "is_user": true,
            "is_group": false,
            "is_channel": false,
            "last_message": {
                "id": 1234,
                "text": "Hello there!",
                "date": "2024-01-15T12:30:00+00:00",
                "is_outgoing": false
            }
        }
    ],
    "cursor": "eyJvZmZzZXRfZGF0ZSI6ICIyMDI0LTAxLTE1VDEyOjMwOjAwKzAwOjAwIiwgLi4ufQ=="
}
```

---

## 3. Inbox Retrieval Flow

### 3.1 Twitter/X Inbox Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                     API Request                                  │
│            GET /api/v1/inbox/?inaid=agent123                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Check Redis Cache                              │
│         cache_key = f"inbox:{inaid}:{hash(params)}"             │
└─────────────────────────────────────────────────────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │ Cache Hit?        │
                    └─────────┬─────────┘
               Yes ◄──────────┼──────────► No
                │             │             │
                ▼             │             ▼
┌───────────────────┐         │   ┌─────────────────────────────────┐
│  Return Cached    │         │   │   Get Agent from DB             │
│    Response       │         │   │   Agent.objects.get(inaid=...)  │
└───────────────────┘         │   └─────────────────────────────────┘
                              │             │
                              │             ▼
                              │   ┌─────────────────────────────────┐
                              │   │   Create XClient                │
                              │   │   XClient(auth_token, ct0)      │
                              │   └─────────────────────────────────┘
                              │             │
                              │             ▼
                              │   ┌─────────────────────────────────┐
                              │   │   Create Handler                │
                              │   │   Handler(client, agent_id)     │
                              │   └─────────────────────────────────┘
                              │             │
                              │             ▼
                              │   ┌─────────────────────────────────┐
                              │   │   handler.get_initial_inbox()   │
                              │   │                                 │
                              │   │   GET /i/api/1.1/dm/            │
                              │   │       user_updates.json         │
                              │   └─────────────────────────────────┘
                              │             │
                              │             ▼
                              │   ┌─────────────────────────────────┐
                              │   │   _structure_inbox_response()   │
                              │   │   Transform raw → structured    │
                              │   └─────────────────────────────────┘
                              │             │
                              │             ▼
                              │   ┌─────────────────────────────────┐
                              │   │   Cache Response (6 hours)      │
                              │   │   cache.set(key, data, 21600)   │
                              │   └─────────────────────────────────┘
                              │             │
                              └─────────────┘
                                            │
                                            ▼
                              ┌─────────────────────────────────────┐
                              │         Return Response             │
                              │   {"conversations": {...}, ...}     │
                              └─────────────────────────────────────┘
```

### 3.2 Telegram Inbox Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                     API Request                                  │
│      GET /api/v1/tg/inbox/?inaid=agent123&cursor=...            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Get TGAgent from DB                           │
│             TGAgent.objects.get(inaid=...)                      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│               Create TGClient (Singleton)                        │
│   TGClient(session_path, api_id, api_hash, proxy)               │
│   - Returns existing instance if available                       │
│   - Creates new if not exists                                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                Load Session File                                 │
│         tg_sessions/{inaid}.session → StringSession             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│               async with TGClient as client:                     │
│                   await client.connect()                         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│           Decode Cursor (if provided)                            │
│   pagination_params = decode_cursor(cursor)                      │
│   - offset_date, offset_id, offset_peer                          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│            await client.get_inbox_chats()                        │
│                                                                  │
│   Telethon: await client.get_dialogs(                           │
│       limit=50,                                                  │
│       offset_date=...,                                           │
│       offset_id=...,                                             │
│       offset_peer=...                                            │
│   )                                                              │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│           Build Dialog Dicts                                     │
│   for dialog in dialogs:                                         │
│       dialog_data = _build_default_dialog(dialog, entity)        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│           Build Next Cursor                                      │
│   cursor = {                                                     │
│       "offset_date": last_dialog.date,                           │
│       "offset_id": last_dialog.message.id,                       │
│       "offset_peer": serialize_peer(entity)                      │
│   }                                                              │
│   encoded_cursor = base64.b64encode(json.dumps(cursor))          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│               Save Session on Exit                               │
│   __aexit__: session_str = client.session.save()                 │
│              write(session_file, session_str)                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              Return Response                                     │
│   {"inbox": [...], "cursor": "eyJ..."}                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Message Sending

### 4.1 Twitter/X Message Sending

```python
def send_dm(self, recipient_id: str, text: str) -> Dict:
    """
    Send a direct message to a user.

    Endpoint: POST https://x.com/i/api/1.1/dm/new2.json
    """
    conversation_id = f"{recipient_id}-{self.agent_id}"

    return self._request(
        "POST",
        "https://x.com/i/api/1.1/dm/new2.json",
        params={
            "ext": "mediaColor,altText,mediaStats...",
            "include_ext_alt_text": "true",
            "include_ext_limited_action_results": "true",
            "include_reply_count": "1",
            # ... other params
        },
        json={
            "conversation_id": conversation_id,
            "recipient_ids": False,
            "request_id": str(uuid4()),
            "text": text,
            "cards_platform": "Web-12",
            "include_cards": 1,
            "dm_users": False
        },
        extra_headers=self._dm_headers(recipient_id),
    )
```

#### Conversation ID Format
```
conversation_id = "{recipient_id}-{agent_id}"

Example: "123456789-987654321"
         └─recipient ─┘ └─agent─┘
```

### 4.2 Telegram Message Sending

```python
async def send_message(
    self,
    to: Union[str, int],
    message: str,
    parse_mode: str = None
) -> types.Message:
    """
    Send a message to a user, chat, or channel.

    Args:
        to: Username, phone number, or entity ID
        message: Message text
        parse_mode: Optional ("html", "markdown")
    """
    async def _send():
        entity = await self.client.get_input_entity(to)
        return await self.client.send_message(
            entity,
            message,
            parse_mode=parse_mode
        )

    return await self._safe_request(_send, context=f"send_message to={to}")

async def _safe_request(self, func, context: str = ""):
    """
    Execute request with error handling and rate limit management.
    """
    try:
        await self.connect()
        return await func()
    except FloodWaitError as e:
        logger.warning(f"FloodWait: {e.seconds}s - {context}")
        raise
    except PeerIdInvalidError:
        logger.error(f"PeerIdInvalid - Account may be banned - {context}")
        raise
    except SessionPasswordNeededError:
        logger.error(f"2FA password required - {context}")
        raise
```

---

## 5. DM Duplicate Detection

### Three-Layer Detection System

The system prevents sending duplicate DMs using a multi-layer approach:

```
┌─────────────────────────────────────────────────────────────────┐
│                    Layer 1: In-Memory Set                        │
│                       (Session-scoped)                           │
│                                                                  │
│   self.last_recipients_ids = set([...])                         │
│   self.last_usernames = set([...])                              │
│                                                                  │
│   Check: if recipient_id in self.last_recipients_ids: SKIP      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                 Layer 2: MongoDB State                           │
│                  (Campaign persistence)                          │
│                                                                  │
│   {                                                              │
│       "task_id": "sc-user123-uuid456",                          │
│       "last_recipients_ids": [...],                              │
│       "last_usernames": [...],                                   │
│       "target_list": [...],                                      │
│       "dm_count": 25,                                            │
│       "running": true                                            │
│   }                                                              │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              Layer 3: PostgreSQL                                 │
│                (Permanent history)                               │
│                                                                  │
│   class AgentDmedUsernamesHistory(models.Model):                │
│       agent = ForeignKey(Agent)                                  │
│       username = CharField(max_length=255)                       │
│                                                                  │
│       class Meta:                                                │
│           unique_together = ("agent", "username")                │
└─────────────────────────────────────────────────────────────────┘
```

### Implementation

```python
class TargetedDMStrategy:
    def __init__(self, dm_handler, agent, config, usernames, task_id, ...):
        self.state_manager = AgentStateManager()
        self.state = self.load_state()

        # Layer 1: In-memory tracking
        self.last_recipients_ids = set(self.state.get("last_recipients_ids", []))
        self.last_usernames = set(self.state.get("last_usernames", []))

    def should_dm(self, recipient_id: str, username: str) -> bool:
        """
        Check all layers before sending DM.
        """
        # Layer 1: In-memory check (fastest)
        if recipient_id in self.last_recipients_ids:
            return False

        # Layer 3: Permanent DB check (for cross-session)
        exists = AgentDmedUsernamesHistory.objects.filter(
            agent=self.agent,
            username=username.strip().lower()
        ).exists()

        if exists:
            # Update in-memory for this session
            self.last_usernames.add(username)
            return False

        return True

    def record_dm(self, recipient_id: str, username: str):
        """
        Record successful DM in all layers.
        """
        # Layer 1: In-memory
        self.last_recipients_ids.add(recipient_id)
        self.last_usernames.add(username.strip().lower())

        # Layer 2: MongoDB (save state)
        self.save_state()

        # Layer 3: PostgreSQL (permanent)
        AgentDmedUsernamesHistory.objects.get_or_create(
            agent=self.agent,
            username=username.strip().lower(),
        )
```

---

## 6. Reply Rate Calculation

### Overview

Reply rate measures engagement by analyzing conversations within a time frame.

### Formula
```
reply_rate = (replied_conversations / total_conversations) * 100

Where:
- replied_conversations = conversations where BOTH agent AND recipient sent messages
- total_conversations = all conversations within the time frame
```

### Implementation

```python
def get_reply_rate(self, time_frame_hours: int = 24) -> Dict:
    """
    Calculate reply rate statistics using raw inbox data.
    """
    cutoff_timestamp = int(datetime.now().timestamp() * 1000) - (time_frame_hours * 3600 * 1000)

    raw_inbox = self.get_initial_inbox(raw=True)

    stats = {
        "total_conversations": 0,
        "replied_conversations": 0,
        "reply_rate": 0.0,
        "unread_covs": 0,
        "analyzed_period_hours": time_frame_hours,
    }

    inbox_state = raw_inbox.get("inbox_initial_state", {})
    conversations = inbox_state.get("conversations", {})
    entries = inbox_state.get("entries", [])

    # Build conversation message lists
    conv_messages = defaultdict(list)
    for entry in entries:
        if "message" not in entry:
            continue
        msg = entry["message"]
        conv_id = msg["conversation_id"]
        msg_data = msg["message_data"]

        conv_messages[conv_id].append({
            "sender_id": msg_data["sender_id"],
            "timestamp": int(msg_data["time"]),
            "is_agent": str(msg_data["sender_id"]) == str(self.agent_id)
        })

    # Analyze each conversation
    for conv_id, conv_data in conversations.items():
        # Skip old conversations
        if int(conv_data.get("sort_timestamp", 0)) < cutoff_timestamp:
            continue

        messages = conv_messages.get(conv_id, [])
        if not messages:
            continue

        stats["total_conversations"] += 1

        # Check for replies
        agent_msgs = [m for m in messages if m["is_agent"]]
        other_msgs = [m for m in messages if not m["is_agent"]]

        if agent_msgs and other_msgs:
            stats["replied_conversations"] += 1

    # Calculate rate
    if stats["total_conversations"] > 0:
        stats["reply_rate"] = round(
            (stats["replied_conversations"] / stats["total_conversations"]) * 100, 2
        )

    return {"statistics": stats}
```

### Usage in Rate Limiting

```python
def can_dm(self) -> bool:
    """Check if we can send DMs, considering reply rate bypass."""
    # Reset check
    if datetime.now() >= self.limit_reset_time:
        self.dm_count = 0
        self.limit_reset_time = datetime.now() + timedelta(hours=self.limit_period_hours)
        return True

    # Hard limit reached - check reply rate for bypass
    if self.dm_count >= self.hard_limit:
        reply_stats = self.dm_handler.get_reply_rate(24)
        if reply_stats["statistics"]["reply_rate"] > self.min_reply_rate:
            logger.info(f"Bypassing limit (reply rate: {reply_stats['statistics']['reply_rate']}%)")
            return True
        return False

    return True
```

---

## 7. Session Persistence

### 7.1 Twitter/X Session (Cookie-Based)

```python
# Agent model stores credentials
class Agent(models.Model):
    credentials = models.JSONField()
    # {"auth_token": "...", "ct0": "..."}

    credentials_hash = models.CharField(max_length=64, unique=True)

    def save(self, *args, **kwargs):
        if self.credentials:
            creds_str = json.dumps(self.credentials, sort_keys=True)
            self.credentials_hash = sha256(creds_str.encode()).hexdigest()
        super().save(*args, **kwargs)

# Usage in XClient
class XClient:
    def __init__(self, auth_token: str, ct0: str, proxy: str = None):
        self.cookies = {
            "auth_token": auth_token,  # Long-lived (months)
            "ct0": ct0,                 # CSRF token
        }
```

### 7.2 Telegram Session (StringSession)

```python
# Session file storage
TG_SESSIONS_DIR = BASE_DIR / 'tg_sessions'
session_path = TG_SESSIONS_DIR / f"{inaid}.session"

# TGClient session handling
class TGClient:
    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            # Save session string to file
            session_str = self.client.session.save()
            with open(self.session_name, "w") as f:
                f.write(session_str)
        finally:
            # Keep connection alive (singleton pattern)
            pass  # Don't disconnect for reuse
```

### 7.3 Session Status Tracking

```python
class Status(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    BANNED = "BANNED", "Banned"
    SESSION_EXPIRED = "SESSION_EXPIRED", "Session Expired"
    FLOOD_WAIT = "FLOOD_WAIT", "Flood Wait"
    PEER_FLOOD_WAIT = "PEER_FLOOD_WAIT", "Peer Flood Wait"
    PROXY_CONN_ERROR = "PROXY_CONN_ERROR", "Proxy Connection Error"

async def get_account_status(self) -> dict:
    """Verify session validity."""
    try:
        await self.connect()
        if not await self.client.is_user_authorized():
            return {"status": Status.SESSION_EXPIRED}

        # Test by sending self-message
        msg = await self.client.send_message("me", "Status check")
        await self.client.delete_messages("me", msg.id)
        return {"status": Status.ACTIVE}

    except PeerIdInvalidError:
        return {"status": Status.BANNED}
    except Exception as e:
        return {"status": Status.NA, "detail": str(e)}
```

---

## 8. State Management

### MongoDB State Manager (`x/db/client.py`)

```python
class AgentStateManager:
    """Manages campaign state in MongoDB for resumability."""

    def __init__(self):
        self.client = MongoClient(settings.MONGO_URI)
        self.db = self.client[settings.MONGO_DB_NAME]
        self.collection = self.db["agent_states"]

    def get_agent_state(self, task_id: str) -> Optional[Dict]:
        """Retrieve saved state for a campaign task."""
        return self.collection.find_one({"task_id": task_id})

    def save_agent_state(self, task_id: str, state: Dict) -> bool:
        """Save/update campaign state."""
        state["task_id"] = task_id
        state["updated_at"] = datetime.now()

        self.collection.update_one(
            {"task_id": task_id},
            {"$set": state},
            upsert=True
        )
        return True

    def set_running_status(self, task_id: str, running: bool):
        """Update running status for pause/resume."""
        self.collection.update_one(
            {"task_id": task_id},
            {"$set": {"running": running, "updated_at": datetime.now()}}
        )

    def set_pause_status(self, task_id: str, should_pause: bool):
        """Signal campaign to pause."""
        self.collection.update_one(
            {"task_id": task_id},
            {"$set": {"should_pause": should_pause}}
        )
```

### State Structure

```python
{
    "task_id": "sc-{inuid}-{uuid4}",
    "dm_count": 45,
    "limit_reset_time": "2024-01-15T12:00:00",
    "last_dm_time": "2024-01-15T11:45:00",
    "last_recipients_ids": ["123456", "789012", ...],
    "last_usernames": ["user1", "user2", ...],
    "target_list": ["target1", "target2", ...],
    "target_list_hash": "sha256...",
    "running": true,
    "should_pause": false,
    "last_heartbeat": "2024-01-15T11:45:30",
    "updated_at": "2024-01-15T11:45:30"
}
```

---

## 9. API Endpoints Reference

### Twitter/X Inbox Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/inbox/` | GET | Get initial inbox (cached) |
| `/api/v1/conversations/` | GET | Get all conversations with pagination |
| `/api/v1/conversations/{conv_id}/messages/` | GET | Get messages for a conversation |
| `/api/v1/reply-rate/` | GET | Calculate reply rate statistics |
| `/api/v1/inbox/send/` | POST | Send a DM |
| `/api/v1/inbox/mark-read/` | POST | Mark conversation as read |
| `/api/v1/conversations/conv/sum/` | GET | AI-powered conversation summary |

### Telegram Inbox Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/tg/inbox/` | GET | Get dialogs with cursor pagination |
| `/api/v1/tg/messages/` | GET | Get messages from a specific chat |
| `/api/v1/tg/reply-rate/` | GET | Calculate reply rate |
| `/api/v1/tg/inbox/send/` | POST | Send a message |
| `/api/v1/tg/mark-read/` | POST | Mark chat as read |

### Campaign Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/campaigns/campaign/start/` | POST | Start AI DM campaign |
| `/api/v1/campaigns/campaign/predefined/start/` | POST | Start predefined DM campaign |
| `/api/v1/campaigns/campaign/pause/` | POST | Pause running campaign |
| `/api/v1/campaigns/campaign/resume/` | POST | Resume paused campaign |
| `/api/v1/campaigns/campaign/status/` | GET | Get campaign status |
| `/api/v1/campaigns/campaign/ls/` | GET | List all campaigns |

### External APIs Used

| Platform | Endpoint | Purpose |
|----------|----------|---------|
| Twitter/X | `GET /i/api/1.1/dm/user_updates.json` | Initial inbox |
| Twitter/X | `GET /i/api/1.1/dm/inbox_timeline/trusted.json` | Paginated inbox |
| Twitter/X | `POST /i/api/1.1/dm/new2.json` | Send DM |
| Twitter/X | `POST /i/api/1.1/dm/conversation/{id}/mark_read.json` | Mark read |
| Telegram | `TelegramClient.get_dialogs()` | Get dialogs (MTProto) |
| Telegram | `TelegramClient.send_message()` | Send message (MTProto) |
| Telegram | `TelegramClient.iter_messages()` | Get messages (MTProto) |

---

## Summary Checklist

### Dialog Fetching Implementation
- [ ] Use `get_initial_inbox()` for single API call (Twitter)
- [ ] Use `get_dialogs()` with cursor pagination (Telegram)
- [ ] Structure responses with consistent format
- [ ] Cache responses with content-based hash keys
- [ ] Implement pagination with `max_id` (Twitter) or cursor (Telegram)

### Session Management
- [ ] Store Twitter cookies in database (JSONField)
- [ ] Store Telegram sessions in file system (StringSession)
- [ ] Use singleton pattern for Telegram connections
- [ ] Track session status (ACTIVE, BANNED, EXPIRED)
- [ ] Handle session expiration gracefully

### Duplicate Prevention
- [ ] Maintain in-memory set (session-scoped)
- [ ] Persist state to MongoDB (campaign-scoped)
- [ ] Record permanent history in PostgreSQL
- [ ] Normalize usernames (lowercase, strip)
- [ ] Use `unique_together` constraint

### Rate Limiting
- [ ] Track DM count per period
- [ ] Calculate reply rate for bypass logic
- [ ] Implement exponential backoff for errors
- [ ] Handle FloodWait errors (Telegram)
