"""
MATRIX TGClient - StringSession-based Telegram Client Wrapper
==============================================================
Singleton pattern wrapper around TelegramClient that uses StringSession
instead of SQLite sessions to eliminate database locking issues.

Key Features:
- StringSession storage: Plain text files (~350 chars) instead of SQLite (~94KB)
- Singleton pattern: One client instance per (session_name, api_id, api_hash) tuple
- Reference counting: Prevents premature disconnection
- Auto-save: Session saved on context exit
- Auto-cleanup: Inactive instances cleaned up after 2 hours

Usage:
    # As context manager (recommended)
    async with TGClient(session_name, api_id, api_hash) as client:
        me = await client.get_me()
        # ... do operations ...
    # Session auto-saved on exit

    # Manual usage
    tg = TGClient(session_name, api_id, api_hash)
    await tg.connect()
    client = tg.client
    # ... do operations ...
    await tg.disconnect()  # Session saved here
"""

import asyncio
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple, Any

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    AuthKeyUnregisteredError,
    UserDeactivatedBanError,
    FloodWaitError,
    SessionPasswordNeededError
)

logger = logging.getLogger(__name__)

# Session directory - StringSession files stored here as .session (plain text)
SESSIONS_DIR = Path(__file__).parent.parent / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

# Backup directory for migrated SQLite sessions
BACKUP_DIR = SESSIONS_DIR / "backup_sqlite"
BACKUP_DIR.mkdir(exist_ok=True)


class TGClient:
    """
    Singleton TelegramClient wrapper with StringSession persistence.

    Eliminates SQLite database locking by storing sessions as plain text strings.
    Uses singleton pattern to ensure one client per account.
    """

    # Class-level instance tracking
    _instances: Dict[Tuple[str, int, str], 'TGClient'] = {}
    _instance_loops: Dict[Tuple[str, int, str], asyncio.AbstractEventLoop] = {}  # Track which loop created each instance
    _last_active: Dict[Tuple[str, int, str], float] = {}
    _cleanup_task: Optional[asyncio.Task] = None
    _cleanup_interval = 7200  # 2 hours in seconds

    def __new__(cls, session_name: str, api_id: int, api_hash: str, **kwargs):
        """
        Singleton pattern implementation.
        Returns existing instance if one exists for this session, unless force_init=True.
        Also creates new instance if the event loop has changed (prevents Telethon loop errors).
        """
        instance_key = (session_name, api_id, api_hash)
        force_init = kwargs.pop("force_init", False)

        # Get current event loop
        try:
            current_loop = asyncio.get_event_loop()
        except RuntimeError:
            current_loop = None

        # Start cleanup task if not running
        if cls._cleanup_task is None or cls._cleanup_task.done():
            try:
                if current_loop and current_loop.is_running():
                    cls._cleanup_task = asyncio.create_task(cls._cleanup_inactive_instances())
            except RuntimeError:
                pass

        # Check if existing instance was created in a different loop
        if instance_key in cls._instances and not force_init:
            old_loop = cls._instance_loops.get(instance_key)
            if old_loop is not None and current_loop is not None and old_loop != current_loop:
                # Loop changed - must create new instance to avoid Telethon error
                logger.info(f"Event loop changed for {session_name}, creating fresh TGClient")
                # Save session before discarding old instance
                old_instance = cls._instances[instance_key]
                if hasattr(old_instance, '_save_session'):
                    old_instance._save_session()
                force_init = True

        # Return existing instance unless force_init
        if not force_init and instance_key in cls._instances:
            instance = cls._instances[instance_key]
            cls._last_active[instance_key] = time.time()
            logger.debug(f"Returning existing TGClient instance for {session_name}")
            return instance

        # Create new instance
        instance = super().__new__(cls)
        instance._initialized = False  # Prevent re-initialization
        cls._instances[instance_key] = instance
        cls._instance_loops[instance_key] = current_loop  # Track which loop created this
        cls._last_active[instance_key] = time.time()
        logger.debug(f"Creating new TGClient instance for {session_name}")
        return instance

    def __init__(self, session_name: str, api_id: int, api_hash: str, **kwargs):
        """
        Initialize TGClient.

        Args:
            session_name: Base name for session (without path or extension)
            api_id: Telegram API ID
            api_hash: Telegram API Hash
            proxy: Optional proxy tuple (type, host, port)
            force_init: If True, create new instance even if one exists
        """
        # Skip if already initialized (singleton reuse)
        if hasattr(self, '_initialized') and self._initialized:
            return

        self.session_name = session_name
        self.api_id = api_id
        self.api_hash = api_hash
        self.proxy = kwargs.get('proxy')

        # Generate session file path (StringSession stored as plain text)
        self.session_path = SESSIONS_DIR / f"{session_name}.session"

        # Load existing session or create empty one
        session_str = self._load_session()
        self.session = StringSession(session_str) if session_str else StringSession()

        # Create TelegramClient with StringSession
        self.client = TelegramClient(
            self.session,
            api_id,
            api_hash,
            proxy=self.proxy,
            timeout=30
        )

        # Reference counting for connection management
        self._connection_count = 0
        self._initialized = True

        logger.info(f"TGClient initialized for {session_name} (session {'loaded' if session_str else 'new'})")

    def _load_session(self) -> Optional[str]:
        """
        Load StringSession from file.

        Returns:
            Session string if file exists and is valid, None otherwise
        """
        if not self.session_path.exists():
            return None

        try:
            # First check if it's an SQLite file (binary)
            with open(self.session_path, 'rb') as f:
                header = f.read(16)
                if header.startswith(b'SQLite format 3'):
                    logger.warning(f"Session file {self.session_path.name} is SQLite format, not StringSession. Run migrate_sessions.py to convert.")
                    return None

            # Read as text (StringSession is base64-like plain text)
            content = self.session_path.read_text(encoding='utf-8').strip()

            # Validate it's a StringSession (base64-like string, starts with '1')
            if content and len(content) > 100 and content[0] == '1':
                logger.debug(f"Loaded StringSession from {self.session_path.name}")
                return content
            else:
                logger.warning(f"Session file {self.session_path.name} is not a valid StringSession format")
                return None
        except UnicodeDecodeError:
            # Binary file that's not SQLite
            logger.warning(f"Session file {self.session_path.name} is binary (not StringSession). Run migrate_sessions.py to convert.")
            return None
        except Exception as e:
            logger.error(f"Failed to load session from {self.session_path.name}: {e}")
            return None

    def _save_session(self) -> bool:
        """
        Save current session to file.

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            session_str = self.client.session.save()
            if session_str:
                # Atomic write: write to temp file first, then rename
                temp_path = self.session_path.with_suffix('.session.tmp')
                temp_path.write_text(session_str)
                temp_path.replace(self.session_path)
                logger.debug(f"Session saved to {self.session_path.name}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to save session to {self.session_path.name}: {e}")
            return False

    async def connect(self) -> bool:
        """
        Connect to Telegram.

        Uses reference counting - only connects if not already connected.

        Returns:
            True if connected and authorized, False otherwise
        """
        instance_key = (self.session_name, self.api_id, self.api_hash)
        self._last_active[instance_key] = time.time()

        try:
            if not self.client.is_connected():
                await self.client.connect()
                logger.info(f"TGClient connected: {self.session_name}")

            self._connection_count += 1
            return True

        except Exception as e:
            logger.error(f"Failed to connect {self.session_name}: {e}")
            return False

    async def disconnect(self, force: bool = False, save_session: bool = True) -> None:
        """
        Disconnect from Telegram.

        Uses reference counting - only disconnects when count reaches 0 or force=True.

        Args:
            force: If True, disconnect regardless of reference count
            save_session: If True, save session before disconnecting
        """
        instance_key = (self.session_name, self.api_id, self.api_hash)
        self._last_active[instance_key] = time.time()

        if self._connection_count > 0:
            self._connection_count -= 1

        should_disconnect = force or self._connection_count == 0

        if should_disconnect and self.client.is_connected():
            if save_session:
                self._save_session()
            await self.client.disconnect()
            logger.info(f"TGClient disconnected: {self.session_name}")

    async def __aenter__(self) -> TelegramClient:
        """Async context manager entry - connects and returns client."""
        await self.connect()
        return self.client

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit - saves session and disconnects."""
        try:
            self._save_session()
        finally:
            await self.disconnect()

    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self.client.is_connected()

    async def is_authorized(self) -> bool:
        """Check if client is authorized (logged in)."""
        if not self.client.is_connected():
            return False
        return await self.client.is_user_authorized()

    async def get_me(self):
        """Get current user info."""
        return await self.client.get_me()

    # ========================================
    # Authentication methods
    # ========================================

    async def send_code_request(self, phone: str) -> Any:
        """
        Send authentication code to phone.

        Args:
            phone: Phone number in international format

        Returns:
            SentCode object with phone_code_hash
        """
        if not self.client.is_connected():
            await self.connect()
        return await self.client.send_code_request(phone)

    async def sign_in(self, phone: str, code: str, phone_code_hash: str) -> Any:
        """
        Sign in with phone code.

        Args:
            phone: Phone number
            code: Verification code
            phone_code_hash: Hash from send_code_request

        Returns:
            User object if successful

        Raises:
            SessionPasswordNeededError: If 2FA is enabled
        """
        result = await self.client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        self._save_session()  # Save immediately after successful sign-in
        return result

    async def sign_in_with_password(self, password: str) -> Any:
        """
        Complete sign-in with 2FA password.

        Args:
            password: 2FA password

        Returns:
            User object if successful
        """
        result = await self.client.sign_in(password=password)
        self._save_session()  # Save immediately after successful sign-in
        return result

    # ========================================
    # Class methods for instance management
    # ========================================

    @classmethod
    async def _cleanup_inactive_instances(cls) -> None:
        """Background task to clean up instances inactive for 2+ hours."""
        while True:
            await asyncio.sleep(cls._cleanup_interval)

            current_time = time.time()
            inactive_keys = [
                key for key, last_active in cls._last_active.items()
                if (current_time - last_active) >= cls._cleanup_interval
            ]

            for key in inactive_keys:
                if instance := cls._instances.get(key):
                    try:
                        if instance.client.is_connected():
                            instance._save_session()
                            await instance.client.disconnect()
                        logger.info(f"Cleaned up inactive TGClient: {key[0]}")
                    except Exception as e:
                        logger.error(f"Error cleaning up TGClient {key[0]}: {e}")

                    del cls._instances[key]
                    if key in cls._instance_loops:
                        del cls._instance_loops[key]
                    del cls._last_active[key]

    @classmethod
    def get_instance(cls, session_name: str, api_id: int, api_hash: str) -> Optional['TGClient']:
        """
        Get existing instance without creating new one.

        Args:
            session_name: Session name
            api_id: API ID
            api_hash: API Hash

        Returns:
            Existing TGClient instance or None
        """
        instance_key = (session_name, api_id, api_hash)
        return cls._instances.get(instance_key)

    @classmethod
    def get_all_instances(cls) -> Dict[Tuple[str, int, str], 'TGClient']:
        """Get all active instances."""
        return dict(cls._instances)

    @classmethod
    async def disconnect_all(cls) -> None:
        """Disconnect and clean up all instances."""
        for key, instance in list(cls._instances.items()):
            try:
                if instance.client.is_connected():
                    instance._save_session()
                    await instance.client.disconnect()
                logger.info(f"Disconnected TGClient: {key[0]}")
            except Exception as e:
                logger.error(f"Error disconnecting TGClient {key[0]}: {e}")

        cls._instances.clear()
        cls._instance_loops.clear()
        cls._last_active.clear()

    @classmethod
    def remove_instance(cls, session_name: str, api_id: int, api_hash: str) -> bool:
        """
        Remove instance from cache (does not disconnect).

        Args:
            session_name: Session name
            api_id: API ID
            api_hash: API Hash

        Returns:
            True if instance was removed, False if not found
        """
        instance_key = (session_name, api_id, api_hash)
        if instance_key in cls._instances:
            del cls._instances[instance_key]
            if instance_key in cls._instance_loops:
                del cls._instance_loops[instance_key]
            if instance_key in cls._last_active:
                del cls._last_active[instance_key]
            return True
        return False


# ========================================
# Helper functions
# ========================================

def get_session_path(phone: str) -> Path:
    """
    Get session file path for a phone number.

    Args:
        phone: Phone number (with or without +)

    Returns:
        Path to session file
    """
    clean_phone = phone.lstrip('+').replace('-', '').replace(' ', '')
    return SESSIONS_DIR / f"session_{clean_phone}.session"


def session_exists(phone: str) -> bool:
    """
    Check if a StringSession file exists for this phone.

    Args:
        phone: Phone number

    Returns:
        True if session file exists
    """
    return get_session_path(phone).exists()


def delete_session(phone: str) -> bool:
    """
    Delete session file for a phone number.

    Args:
        phone: Phone number

    Returns:
        True if deleted, False if not found
    """
    path = get_session_path(phone)
    if path.exists():
        path.unlink()
        logger.info(f"Deleted session file: {path.name}")
        return True
    return False


# Module exports
__all__ = [
    'TGClient',
    'SESSIONS_DIR',
    'BACKUP_DIR',
    'get_session_path',
    'session_exists',
    'delete_session'
]
