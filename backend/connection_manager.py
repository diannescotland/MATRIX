"""
MATRIX Global Connection Manager
================================
Singleton that owns ALL TelegramClient instances.
Both InboxManager and UnifiedContactManager use this shared pool.

This solves the session file locking issue where two separate systems
(InboxManager + UnifiedContactManager) would each create their own
TelegramClient instances, causing file lock conflicts.

Architecture:
    GlobalConnectionManager (singleton)
            │
            ├── _clients: Dict[phone, TelegramClient]
            ├── _locks: Dict[phone, asyncio.Lock]
            └── _connection_info: Dict[phone, ConnectionInfo]
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
  InboxManager          UnifiedContactManager
  (uses shared          (uses shared client
   client for            for operations)
   real-time events)
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Optional, Callable, Any, List
from pathlib import Path
from dataclasses import dataclass, field
from contextlib import asynccontextmanager

from telethon import TelegramClient, events
from telethon.tl.types import User
from telethon.errors import (
    AuthKeyUnregisteredError, UserDeactivatedBanError,
    FloodWaitError
)

logger = logging.getLogger(__name__)

# Constants
SESSIONS_DIR = Path(__file__).parent.parent / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)


@dataclass
class ConnectionInfo:
    """Information about a connected account."""
    phone: str
    client: TelegramClient
    connected_at: datetime
    my_id: int = 0
    my_name: str = ""
    api_id: int = 0
    api_hash: str = ""
    session_path: str = ""
    proxy: Any = None
    event_handlers_registered: bool = False
    in_use_by: Optional[str] = None  # 'inbox', 'operation', or None


class GlobalConnectionManager:
    """
    Singleton that owns ALL TelegramClient instances.

    Key features:
    - Single TelegramClient per account (no duplicates)
    - Thread-safe with per-account locks
    - Shared between InboxManager and UnifiedContactManager
    - Event handlers registered once per client
    - Operation locking to prevent concurrent operations on same account
    """

    _instance: Optional['GlobalConnectionManager'] = None
    _initialized: bool = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, socketio=None):
        # Only initialize once
        if GlobalConnectionManager._initialized:
            # Update socketio if provided
            if socketio is not None:
                self._socketio = socketio
            return

        self._socketio = socketio
        self._clients: Dict[str, ConnectionInfo] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._operation_locks: Dict[str, asyncio.Lock] = {}  # For exclusive operations
        self._event_processor = None  # Set by InboxManager
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._shutting_down = False

        GlobalConnectionManager._initialized = True
        logger.info("🔧 GlobalConnectionManager initialized")

    @classmethod
    def get_instance(cls, socketio=None) -> 'GlobalConnectionManager':
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = cls(socketio)
        elif socketio is not None:
            cls._instance._socketio = socketio
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """Reset the singleton (for testing)."""
        cls._instance = None
        cls._initialized = False

    def set_event_processor(self, processor):
        """Set the event processor for handling Telegram events."""
        self._event_processor = processor
        logger.debug("Event processor set")

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        """Set the asyncio event loop."""
        self._loop = loop

    def _normalize_phone(self, phone: str) -> str:
        """Normalize phone number (remove + prefix)."""
        return phone.lstrip('+').strip()

    def _get_lock(self, phone: str) -> asyncio.Lock:
        """Get or create lock for account."""
        clean_phone = self._normalize_phone(phone)
        if clean_phone not in self._locks:
            self._locks[clean_phone] = asyncio.Lock()
        return self._locks[clean_phone]

    def _get_operation_lock(self, phone: str) -> asyncio.Lock:
        """Get or create operation lock for account."""
        clean_phone = self._normalize_phone(phone)
        if clean_phone not in self._operation_locks:
            self._operation_locks[clean_phone] = asyncio.Lock()
        return self._operation_locks[clean_phone]

    def _parse_proxy(self, proxy_url: str) -> Optional[tuple]:
        """Parse proxy URL to Telethon format."""
        if not proxy_url:
            return None

        try:
            if proxy_url.startswith('http://'):
                parts = proxy_url[7:].split(':')
                return ('http', parts[0], int(parts[1]))
            elif proxy_url.startswith('socks5://'):
                parts = proxy_url[9:].split(':')
                return ('socks5', parts[0], int(parts[1]))
            else:
                parts = proxy_url.split(':')
                return ('http', parts[0], int(parts[1]))
        except Exception as e:
            logger.error(f"Failed to parse proxy {proxy_url}: {e}")
            return None

    async def get_client(self, phone: str, api_id: int = None, api_hash: str = None,
                         session_path: str = None, proxy: str = None,
                         register_events: bool = True) -> Optional[TelegramClient]:
        """
        Get or create TelegramClient for account.

        If client exists and is connected, returns it.
        If not, creates new client and connects.

        Args:
            phone: Account phone number
            api_id: Telegram API ID (required for new connections)
            api_hash: Telegram API Hash (required for new connections)
            session_path: Path to session file (optional, auto-generated if not provided)
            proxy: Proxy URL (optional)
            register_events: Whether to register event handlers (default True)

        Returns:
            TelegramClient if connected, None otherwise
        """
        clean_phone = self._normalize_phone(phone)

        # Check if already connected
        if clean_phone in self._clients:
            conn_info = self._clients[clean_phone]
            if conn_info.client.is_connected():
                logger.debug(f"Returning existing client for {clean_phone}")
                return conn_info.client
            else:
                # Client disconnected, will reconnect below
                logger.info(f"Client for {clean_phone} disconnected, reconnecting...")

        # Need credentials for new connection
        if api_id is None or api_hash is None:
            # Try to get from existing connection info
            if clean_phone in self._clients:
                conn_info = self._clients[clean_phone]
                api_id = conn_info.api_id
                api_hash = conn_info.api_hash
                session_path = conn_info.session_path
                proxy = conn_info.proxy
            else:
                logger.error(f"No credentials provided for {clean_phone}")
                return None

        # Generate session path if not provided
        if not session_path:
            session_path = str(SESSIONS_DIR / f"session_{clean_phone}")

        # Parse proxy
        proxy_tuple = self._parse_proxy(proxy) if isinstance(proxy, str) else proxy

        async with self._get_lock(clean_phone):
            try:
                logger.info(f"Connecting account {clean_phone}...")

                # Create client
                client = TelegramClient(
                    session_path.replace('.session', ''),
                    api_id,
                    api_hash,
                    proxy=proxy_tuple,
                    timeout=30
                )

                # Connect
                await client.connect()

                # Check if authorized
                if not await client.is_user_authorized():
                    logger.warning(f"Account {clean_phone} not authorized - needs authentication")
                    await client.disconnect()
                    return None

                # Get user info
                me = await client.get_me()

                # Register event handlers if requested and processor is set
                if register_events and self._event_processor:
                    await self._register_event_handlers(client, clean_phone)

                # Store connection info
                self._clients[clean_phone] = ConnectionInfo(
                    phone=clean_phone,
                    client=client,
                    connected_at=datetime.now(),
                    my_id=me.id,
                    my_name=me.first_name or "",
                    api_id=api_id,
                    api_hash=api_hash,
                    session_path=session_path,
                    proxy=proxy,
                    event_handlers_registered=register_events and self._event_processor is not None
                )

                # Emit WebSocket event if socketio is available
                if self._socketio:
                    self._socketio.emit('inbox:connection_status', {
                        'account_phone': clean_phone,
                        'connected': True,
                        'event': 'connected',
                        'timestamp': datetime.now().isoformat()
                    })

                logger.info(f"Account {clean_phone} connected successfully")
                return client

            except AuthKeyUnregisteredError:
                logger.error(f"Account {clean_phone} auth key invalid - needs re-authentication")
                return None

            except UserDeactivatedBanError:
                logger.error(f"Account {clean_phone} is banned/deactivated")
                return None

            except Exception as e:
                logger.error(f"Failed to connect account {clean_phone}: {e}")
                return None

    async def disconnect_account(self, phone: str) -> None:
        """Gracefully disconnect account."""
        clean_phone = self._normalize_phone(phone)

        if clean_phone not in self._clients:
            return

        async with self._get_lock(clean_phone):
            try:
                conn_info = self._clients.get(clean_phone)
                if conn_info and conn_info.client:
                    if conn_info.client.is_connected():
                        await conn_info.client.disconnect()

                    del self._clients[clean_phone]

                # Emit WebSocket event
                if self._socketio:
                    self._socketio.emit('inbox:connection_status', {
                        'account_phone': clean_phone,
                        'connected': False,
                        'event': 'disconnected',
                        'timestamp': datetime.now().isoformat()
                    })

                logger.info(f"Account {clean_phone} disconnected")

            except Exception as e:
                logger.error(f"Error disconnecting {clean_phone}: {e}")

    @asynccontextmanager
    async def with_client(self, phone: str, api_id: int = None, api_hash: str = None,
                          session_path: str = None, proxy: str = None,
                          operation_name: str = None):
        """
        Context manager for exclusive client access during operations.

        Usage:
            async with conn_manager.with_client(phone, api_id, api_hash) as client:
                # Do operation with client
                contacts = await client(GetContactsRequest(hash=0))

        This ensures only one operation runs on an account at a time.
        The client stays connected after the operation (not disconnected).
        """
        clean_phone = self._normalize_phone(phone)

        # Acquire operation lock
        async with self._get_operation_lock(clean_phone):
            # Get or create client
            client = await self.get_client(phone, api_id, api_hash, session_path, proxy)

            if client is None:
                raise RuntimeError(f"Failed to get client for {clean_phone}")

            # Mark client as in use
            if clean_phone in self._clients:
                self._clients[clean_phone].in_use_by = operation_name

            try:
                yield client
            finally:
                # Mark client as available (but keep connected)
                if clean_phone in self._clients:
                    self._clients[clean_phone].in_use_by = None

    def is_connected(self, phone: str) -> bool:
        """Check if account is connected."""
        clean_phone = self._normalize_phone(phone)
        conn_info = self._clients.get(clean_phone)
        return conn_info is not None and conn_info.client.is_connected()

    def get_connected_accounts(self) -> List[str]:
        """Get list of connected account phones."""
        return [
            phone for phone, info in self._clients.items()
            if info.client.is_connected()
        ]

    def get_connection_info(self, phone: str) -> Optional[ConnectionInfo]:
        """Get connection info for an account."""
        clean_phone = self._normalize_phone(phone)
        return self._clients.get(clean_phone)

    async def shutdown(self) -> None:
        """Gracefully shutdown all connections."""
        self._shutting_down = True
        logger.info("Shutting down GlobalConnectionManager...")

        for phone in list(self._clients.keys()):
            await self.disconnect_account(phone)

        logger.info("All connections closed")

    async def _register_event_handlers(self, client: TelegramClient, phone: str):
        """Register Telethon event handlers for account."""
        if not self._event_processor:
            logger.warning(f"No event processor set for {phone}")
            return

        @client.on(events.NewMessage(incoming=True))
        async def on_incoming_message(event):
            if self._shutting_down:
                return
            await self._event_processor.handle_new_message(phone, event, incoming=True)

        @client.on(events.NewMessage(outgoing=True))
        async def on_outgoing_message(event):
            if self._shutting_down:
                return
            await self._event_processor.handle_new_message(phone, event, incoming=False)

        @client.on(events.MessageRead(inbox=False))
        async def on_message_read(event):
            if self._shutting_down:
                return
            await self._event_processor.handle_message_read(phone, event)

        @client.on(events.MessageEdited())
        async def on_message_edited(event):
            if self._shutting_down:
                return
            await self._event_processor.handle_message_edited(phone, event)

        @client.on(events.UserUpdate())
        async def on_user_status(event):
            if self._shutting_down:
                return
            await self._event_processor.handle_user_status(phone, event)

        from telethon.tl.types import UpdateUserTyping, UpdateDeleteMessages

        @client.on(events.Raw())
        async def on_raw_update(event):
            if self._shutting_down:
                return
            if isinstance(event, UpdateUserTyping):
                await self._event_processor.handle_typing(phone, event)
            elif isinstance(event, UpdateDeleteMessages):
                await self._event_processor.handle_deleted_messages(phone, event)

        logger.debug(f"Event handlers registered for {phone}")


# Module exports
__all__ = ['GlobalConnectionManager', 'ConnectionInfo']
