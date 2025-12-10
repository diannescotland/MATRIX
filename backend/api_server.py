"""
MATRIX HTTP API Server
REST API wrapper for the UnifiedContactManager
Allows React Native Web frontend to control MATRIX operations
"""

import asyncio
import json
import logging
import sys
import io
import uuid
import os
import csv
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.utils import secure_filename
import threading
from typing import Dict, Any, Optional, List, Tuple, Set
import traceback
from concurrent.futures import ThreadPoolExecutor
import random
import glob
import math

# Telethon imports (previously in matrix.py)
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    UsernameNotOccupiedError,
    UsernameInvalidError,
    FloodWaitError,
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError,
)
from telethon.tl.functions.contacts import AddContactRequest, GetContactsRequest
from telethon.tl.types import User, Chat, Channel

# Import TGClient for StringSession-based connections (eliminates SQLite locking)
from tg_client import TGClient, get_session_path, session_exists, delete_session

# Fix Windows encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Initialize Flask app
app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Initialize Socket.IO for real-time progress updates
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='threading',  # Use threading mode for compatibility with sync code
    logger=False,
    engineio_logger=False
)

# Thread pool for parallel operations (max 5 concurrent account operations)
operation_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="matrix_op")

# Setup logging for API server
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

log_file = LOG_DIR / f"api_server_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
fh = logging.FileHandler(log_file, encoding='utf-8')
fh.setLevel(logging.DEBUG)
fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(fh)

ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(logging.Formatter('[API] %(message)s'))
logger.addHandler(ch)

# Global state for operations
operation_state = {
    'current_operation': None,
    'progress': 0,
    'total': 0,
    'status': 'idle',
    'message': '',
    'errors': [],
    'rate_limit': None,  # Rate limit info: {wait_seconds, started_at, expires_at, reason}
    'logs': [],  # Real-time log messages for frontend display
}

operation_lock = threading.Lock()


def update_operation_state(operation: str, progress: int, total: int, status: str, message: str = ''):
    """Thread-safe operation state update"""
    with operation_lock:
        operation_state['current_operation'] = operation
        operation_state['progress'] = progress
        operation_state['total'] = total
        operation_state['status'] = status
        operation_state['message'] = message
        logger.info(f"[{operation}] {status}: {progress}/{total} - {message}")


def reset_operation_state():
    """Reset operation state to idle"""
    with operation_lock:
        operation_state.update({
            'current_operation': None,
            'progress': 0,
            'total': 0,
            'status': 'idle',
            'message': '',
            'errors': [],
            'rate_limit': None,
            'logs': [],
        })


def add_operation_log(message: str):
    """Add a log message to operation state (thread-safe) for real-time frontend display"""
    with operation_lock:
        operation_state['logs'].append({
            'time': datetime.now().strftime('%H:%M:%S'),
            'message': message
        })


def set_rate_limit(wait_seconds: int, reason: str = 'FloodWaitError'):
    """Set rate limit info when Telegram rate limits us"""
    with operation_lock:
        started_at = datetime.now()
        expires_at = started_at + timedelta(seconds=wait_seconds)
        operation_state['rate_limit'] = {
            'wait_seconds': wait_seconds,
            'started_at': started_at.isoformat(),
            'expires_at': expires_at.isoformat(),
            'reason': reason,
            'remaining_seconds': wait_seconds,
        }
        operation_state['status'] = 'rate_limited'
        operation_state['message'] = f'Rate limited by Telegram. Waiting {wait_seconds}s...'
        logger.warning(f"⚠️ RATE LIMITED: {reason} - Waiting {wait_seconds}s until {expires_at.strftime('%H:%M:%S')}")


def clear_rate_limit():
    """Clear rate limit info after wait is complete"""
    with operation_lock:
        operation_state['rate_limit'] = None
        if operation_state['status'] == 'rate_limited':
            operation_state['status'] = 'running'
        logger.info("✅ Rate limit wait completed, resuming operation")


def get_operation_state() -> Dict[str, Any]:
    """Get current operation state (thread-safe)"""
    with operation_lock:
        return operation_state.copy()


# ============================================================================
# TELEGRAM RATE LIMIT EXCEPTION (previously in matrix.py)
# ============================================================================

class TelegramRateLimitError(Exception):
    """Custom exception for Telegram rate limiting - stops operation immediately"""
    def __init__(self, wait_seconds: int, message: str = None):
        self.wait_seconds = wait_seconds
        self.message = message or f"Rate limited by Telegram. Please wait {wait_seconds} seconds."
        super().__init__(self.message)


# ============================================================================
# CONTACT CACHE WITH AUTO-BACKUP
# Reduces API calls AND keeps per-account backup CSVs fresh automatically
# ============================================================================

class ContactCache:
    """
    Thread-safe cache for Telegram contacts with auto-backup functionality.

    Features:
    - Caches GetContactsRequest results (reduces API calls)
    - Automatically saves per-account backup CSV when contacts are fetched
    - Zero extra API calls for backups

    Usage:
        cache = ContactCache(ttl_seconds=300)
        contacts = await cache.get_contacts(client, phone="123456")
        # Backup CSV is automatically saved!
    """

    def __init__(self, ttl_seconds: int = 300):
        """
        Initialize cache with TTL.

        Args:
            ttl_seconds: Cache lifetime in seconds (default 5 minutes)
        """
        import time
        self._contacts = None
        self._last_fetch: float = 0
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()
        self._fetch_count = 0
        self._cache_hits = 0
        self._backup_count = 0

        # Backup directory will be set after LOGS_DIR is defined
        self._backup_dir = None

        # Track phone number for backup naming
        self._current_phone = None
        self._last_backup_path = None

    def _ensure_backup_dir(self):
        """Ensure backup directory exists (called lazily after LOGS_DIR is defined)."""
        if self._backup_dir is None:
            self._backup_dir = LOGS_DIR / "backups"
            self._backup_dir.mkdir(parents=True, exist_ok=True)

    async def get_contacts(self, client, phone: str = None, force_refresh: bool = False,
                          auto_backup: bool = True):
        """
        Get contacts from cache or fetch if expired/missing.
        Automatically saves backup CSV when fetching fresh data.

        Args:
            client: TelegramClient instance
            phone: Phone number (for backup file naming)
            force_refresh: If True, bypass cache and fetch fresh
            auto_backup: If True, save backup CSV on fresh fetch (default True)

        Returns:
            Result from GetContactsRequest
        """
        import time

        async with self._lock:
            now = time.time()

            # Update phone if provided
            if phone:
                self._current_phone = phone.replace('+', '').replace('-', '').replace(' ', '')

            cache_valid = (
                self._contacts is not None and
                (now - self._last_fetch) < self._ttl
            )

            # Return cached if valid and not forcing refresh
            if cache_valid and not force_refresh:
                self._cache_hits += 1
                logger.debug(f"Contact cache HIT (hits: {self._cache_hits})")
                return self._contacts

            # Fetch fresh from Telegram
            logger.info("Fetching contacts from Telegram API...")
            self._contacts = await client(GetContactsRequest(hash=0))
            self._last_fetch = now
            self._fetch_count += 1

            # Auto-backup: Save to CSV (FREE - we already have the data!)
            if auto_backup and self._current_phone:
                backup_path = await self._save_backup(self._contacts, self._current_phone)
                if backup_path:
                    self._backup_count += 1
                    logger.info(f"Auto-backup saved: {backup_path.name}")

            return self._contacts

    async def _save_backup(self, contacts_result, phone: str) -> Optional[Path]:
        """
        Save contacts to backup CSV file.
        Creates both timestamped backup AND "latest" file.

        Args:
            contacts_result: Result from GetContactsRequest
            phone: Phone number for file naming

        Returns:
            Path to backup file, or None if failed
        """
        try:
            self._ensure_backup_dir()

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"contacts_{phone}_{timestamp}.csv"
            backup_path = self._backup_dir / filename

            # Also save a "latest" file for easy dashboard access
            latest_path = self._backup_dir / f"contacts_{phone}_latest.csv"

            # Extract contact data
            contacts_data = []
            for user in contacts_result.users:
                contacts_data.append({
                    'user_id': user.id,
                    'username': user.username or '',
                    'first_name': user.first_name or '',
                    'last_name': user.last_name or '',
                    'phone': user.phone or '',
                    'is_bot': getattr(user, 'bot', False),
                    'is_contact': getattr(user, 'contact', False),
                    'is_mutual_contact': getattr(user, 'mutual_contact', False),
                    'backup_date': timestamp,
                })

            fieldnames = ['user_id', 'username', 'first_name', 'last_name', 'phone',
                         'is_bot', 'is_contact', 'is_mutual_contact', 'backup_date']

            # Write timestamped backup
            with open(backup_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(contacts_data)

            # Write/overwrite "latest" file (for dashboard to read)
            with open(latest_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(contacts_data)

            self._last_backup_path = latest_path

            # Register backup in database
            self._register_backup_in_db(phone, str(latest_path), len(contacts_data))

            return backup_path

        except Exception as e:
            logger.error(f"Auto-backup failed: {e}")
            return None

    def _register_backup_in_db(self, phone: str, filepath: str, contacts_count: int):
        """Register backup in the backups database table."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            # Check if entry exists for this phone's "latest" backup
            cursor.execute(
                'SELECT id FROM backups WHERE phone = ? AND filepath LIKE ?',
                (phone, f'%contacts_{phone}_latest.csv')
            )
            existing = cursor.fetchone()

            if existing:
                # Update existing entry
                cursor.execute('''
                    UPDATE backups
                    SET filepath = ?, contacts_count = ?, created_at = ?
                    WHERE id = ?
                ''', (filepath, contacts_count, datetime.now().isoformat(), existing['id']))
            else:
                # Insert new entry
                cursor.execute('''
                    INSERT INTO backups (phone, filename, filepath, contacts_count, created_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (phone, f'contacts_{phone}_latest.csv', filepath, contacts_count, datetime.now().isoformat()))

            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Could not register backup in DB: {e}")

    def invalidate(self):
        """Force cache to refresh on next get_contacts() call."""
        self._last_fetch = 0
        logger.debug("Contact cache invalidated")

    def get_latest_backup_path(self, phone: str = None) -> Optional[Path]:
        """Get path to the latest backup file for a phone number."""
        self._ensure_backup_dir()
        if phone:
            clean_phone = phone.replace('+', '').replace('-', '').replace(' ', '')
            return self._backup_dir / f"contacts_{clean_phone}_latest.csv"
        return self._last_backup_path

    def get_stats(self) -> dict:
        """Get cache and backup statistics."""
        import time
        return {
            'cache_hits': self._cache_hits,
            'fetch_count': self._fetch_count,
            'backup_count': self._backup_count,
            'is_valid': self._contacts is not None and (time.time() - self._last_fetch) < self._ttl,
            'age_seconds': time.time() - self._last_fetch if self._last_fetch > 0 else None,
            'ttl_seconds': self._ttl,
            'last_backup': str(self._last_backup_path) if self._last_backup_path else None
        }


class PerAccountCacheManager:
    """Manages per-account contact caches to prevent cache thrashing with many accounts"""

    def __init__(self, ttl_seconds: int = 300):
        self._caches: Dict[str, ContactCache] = {}
        self._ttl = ttl_seconds
        self._lock = threading.Lock()

    def get_cache(self, phone: str) -> ContactCache:
        """Get or create cache for a specific account"""
        clean_phone = phone.replace('+', '').replace('-', '').replace(' ', '')
        with self._lock:
            if clean_phone not in self._caches:
                self._caches[clean_phone] = ContactCache(ttl_seconds=self._ttl)
            return self._caches[clean_phone]

    def invalidate(self, phone: str = None):
        """Invalidate cache for specific account or all caches"""
        with self._lock:
            if phone:
                clean = phone.replace('+', '').replace('-', '').replace(' ', '')
                if clean in self._caches:
                    self._caches[clean].invalidate()
            else:
                for cache in self._caches.values():
                    cache.invalidate()

    def get_stats(self) -> Dict:
        """Get cache statistics for all accounts"""
        with self._lock:
            return {
                'account_count': len(self._caches),
                'accounts': {phone: cache.get_stats() for phone, cache in self._caches.items()}
            }


# Per-account cache manager (replaces global cache for better scalability)
_account_cache_manager = PerAccountCacheManager(ttl_seconds=300)


# ============================================================================
# GLOBAL CONSTANTS (previously in matrix.py)
# ============================================================================

CONFIG_FILE = Path(__file__).parent.parent / "config.json"
SESSIONS_DIR = Path(__file__).parent.parent / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)
LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)


def cleanup_session_locks():
    """
    Clean up any stale SQLite lock files from old sessions.
    With StringSession, these should not exist, but clean up just in case.
    """
    cleaned = 0
    for ext in ['-wal', '-shm', '-journal']:
        for lock_file in SESSIONS_DIR.glob(f'*{ext}'):
            try:
                lock_file.unlink()
                logger.info(f"🗑️ Deleted stale lock file: {lock_file.name}")
                cleaned += 1
            except Exception as e:
                logger.warning(f"⚠️ Could not delete {lock_file.name}: {e}")

    if cleaned > 0:
        logger.info(f"✅ Cleaned up {cleaned} stale session lock files")


# ============================================================================
# UTILITY FUNCTIONS (previously in matrix.py)
# ============================================================================

def load_config() -> Dict:
    """Load API credentials and default session from config.json"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                logger.info("✅ Loaded config from config.json")
                return config
        except Exception as e:
            logger.warning(f"⚠️  Error loading config: {str(e)}")
            return {}
    return {}


def save_config(config: Dict):
    """Save API credentials and default session to config.json"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        logger.info("✅ Saved config to config.json")
    except Exception as e:
        logger.error(f"❌ Error saving config: {str(e)}")


def get_api_credentials() -> Tuple[int, str]:
    """
    Get API credentials from config or user input.
    Returns: (api_id, api_hash)
    """
    config = load_config()

    if 'api_id' in config and 'api_hash' in config:
        return config['api_id'], config['api_hash']

    # First run: ask for credentials
    print("\n" + "="*70)
    print("🔐 FIRST-TIME SETUP: Telegram API Credentials")
    print("="*70)
    print("\nGo to https://my.telegram.org/apps and get your API credentials:")
    print("\n")

    try:
        api_id = int(input("Enter API_ID (number): ").strip())
        api_hash = input("Enter API_HASH (string): ").strip()

        if not api_id or not api_hash:
            logger.error("❌ API credentials cannot be empty")
            return 0, ""

        # Save to config
        config = load_config()
        config['api_id'] = api_id
        config['api_hash'] = api_hash
        save_config(config)

        print(f"\n✅ Credentials saved to config.json\n")
        return api_id, api_hash

    except ValueError:
        logger.error("❌ API_ID must be a number")
        return 0, ""


def get_default_session() -> Optional[str]:
    """Get default session phone number from database"""
    try:
        default_account = get_default_account()
        if default_account:
            return default_account.get('phone')
        return None
    except Exception:
        # Fallback to config.json for backward compatibility
        config = load_config()
        return config.get('default_session')


def set_default_session(phone_number: str):
    """Set default session in database"""
    try:
        clean_phone = phone_number.replace('+', '').replace('-', '').replace(' ', '')
        set_default_account(clean_phone)
    except Exception:
        # Fallback to config.json for backward compatibility
        config = load_config()
        config['default_session'] = phone_number
        save_config(config)


def find_existing_sessions() -> List[str]:
    """Find all existing session files in sessions/ folder"""
    session_files = list(SESSIONS_DIR.glob("session_*.session"))
    phones = []
    for sf in session_files:
        phone = sf.name.replace("session_", "").replace(".session", "")
        phones.append(phone)
    return sorted(phones)


def distribute_contacts_chunked(contacts: List, accounts: List[str]) -> Dict[str, List]:
    """
    Distribute contacts equally across multiple accounts using chunked distribution.
    """
    if not contacts or not accounts:
        return {}

    num_accounts = len(accounts)
    num_contacts = len(contacts)

    chunk_size = num_contacts // num_accounts
    remainder = num_contacts % num_accounts

    distribution = {}
    current_index = 0

    for i, phone in enumerate(accounts):
        extra = 1 if i < remainder else 0
        size = chunk_size + extra
        distribution[phone] = contacts[current_index:current_index + size]
        current_index += size

    return distribution


def distribute_contacts_interleaved(contacts: List, accounts: List[str]) -> Dict[str, List]:
    """
    Distribute contacts across accounts using interleaved (round-robin) distribution.
    """
    if not contacts or not accounts:
        return {}

    distribution = {phone: [] for phone in accounts}

    for i, contact in enumerate(contacts):
        account_index = i % len(accounts)
        phone = accounts[account_index]
        distribution[phone].append(contact)

    return distribution


def get_distribution_preview(contacts: List, accounts: List[str], method: str = 'chunked') -> Dict:
    """Get a preview of how contacts would be distributed."""
    if method == 'interleaved':
        distribution = distribute_contacts_interleaved(contacts, accounts)
    else:
        distribution = distribute_contacts_chunked(contacts, accounts)

    preview = {
        'total_contacts': len(contacts),
        'total_accounts': len(accounts),
        'method': method,
        'accounts': []
    }

    for phone, account_contacts in distribution.items():
        preview['accounts'].append({
            'phone': phone,
            'count': len(account_contacts),
            'percentage': round(len(account_contacts) / len(contacts) * 100, 1) if contacts else 0
        })

    return preview


# Import account_manager
from account_manager import (
    init_database, add_account, get_all_accounts, get_active_accounts,
    get_account_by_phone, update_account_status, validate_account,
    validate_accounts_batch, delete_account, update_account_last_used,
    get_default_account, set_default_account, get_db_connection,
    normalize_phone, init_operations_tables, update_account_proxy,
    db_create_operation, db_get_operation, db_update_account_progress,
    db_add_operation_log, db_complete_operation, db_get_active_operations,
    db_get_recent_operations,
    # Inbox management functions
    init_inbox_tables, inbox_get_or_create_conversation, inbox_update_conversation,
    inbox_get_conversations, inbox_insert_message, inbox_get_messages,
    inbox_mark_messages_read, inbox_soft_delete_messages,
    inbox_update_connection_state, inbox_get_connection_states,
    inbox_increment_reconnect_attempts, inbox_record_dm_sent, inbox_check_dm_sent,
    inbox_get_dm_count_today, inbox_ensure_campaign, inbox_update_campaign_metrics,
    inbox_get_campaign_metrics, inbox_log_event, inbox_get_conversations_needing_backfill,
    inbox_link_matrix_contact
)
logger.info("✅ Successfully imported account_manager")

# Import inbox manager
from inbox_manager import InboxManager
logger.info("✅ Successfully imported inbox_manager")

# Import GlobalConnectionManager for shared TelegramClient management
from connection_manager import GlobalConnectionManager
logger.info("✅ Successfully imported GlobalConnectionManager")

# Global manager instance (will be initialized on first API call)
manager = None
manager_lock = threading.Lock()

# Global inbox manager instance (for real-time messaging)
inbox_manager: Optional[InboxManager] = None
inbox_manager_thread: Optional[threading.Thread] = None


class AccountLockManager:
    """
    Per-account lock manager for parallel operations.
    Allows different accounts to run operations simultaneously while
    preventing multiple operations on the same account.
    """

    def __init__(self):
        self._locks: Dict[str, threading.Lock] = {}
        self._master_lock = threading.Lock()

    def get_lock(self, phone: str) -> threading.Lock:
        """Get or create a lock for a specific account"""
        clean_phone = normalize_phone(phone)
        with self._master_lock:
            if clean_phone not in self._locks:
                self._locks[clean_phone] = threading.Lock()
            return self._locks[clean_phone]

    def acquire(self, phone: str, blocking: bool = True, timeout: float = -1) -> bool:
        """
        Acquire lock for an account.

        Args:
            phone: Phone number of the account
            blocking: If True, block until lock is available
            timeout: Maximum time to wait (-1 for infinite)

        Returns:
            True if lock acquired, False otherwise
        """
        lock = self.get_lock(phone)
        return lock.acquire(blocking=blocking, timeout=timeout)

    def release(self, phone: str) -> None:
        """Release lock for an account"""
        clean_phone = normalize_phone(phone)
        with self._master_lock:
            if clean_phone in self._locks:
                try:
                    self._locks[clean_phone].release()
                except RuntimeError:
                    pass  # Lock was not held

    def is_locked(self, phone: str) -> bool:
        """Check if an account is currently locked"""
        clean_phone = normalize_phone(phone)
        with self._master_lock:
            if clean_phone not in self._locks:
                return False
            # Try to acquire without blocking
            lock = self._locks[clean_phone]
            acquired = lock.acquire(blocking=False)
            if acquired:
                lock.release()
                return False
            return True

    def get_locked_accounts(self) -> list:
        """Get list of currently locked account phone numbers"""
        locked = []
        with self._master_lock:
            for phone, lock in self._locks.items():
                acquired = lock.acquire(blocking=False)
                if acquired:
                    lock.release()
                else:
                    locked.append(phone)
        return locked


# Per-account lock manager for parallel operations
account_locks = AccountLockManager()

# Active operations tracking for WebSocket updates
active_operations: Dict[str, Dict[str, Any]] = {}
operations_lock = threading.Lock()

# Batched database write system for performance
# Progress updates are queued and flushed to DB every N seconds
_progress_write_queue: Dict[Tuple[str, str], Dict] = {}  # (op_id, phone) -> data
_progress_write_lock = threading.Lock()
_log_write_queue: List[Tuple[str, str, str, str]] = []  # [(op_id, phone, message, level), ...]
_log_write_lock = threading.Lock()
DB_FLUSH_INTERVAL = 5.0  # Flush to database every 5 seconds
_db_flush_thread_started = False


def _flush_progress_to_db():
    """Flush queued progress updates to database (called by background worker)"""
    global _progress_write_queue

    with _progress_write_lock:
        if not _progress_write_queue:
            return
        queue_copy = dict(_progress_write_queue)
        _progress_write_queue = {}

    # Write all queued updates
    for (op_id, phone), data in queue_copy.items():
        try:
            db_update_account_progress(
                op_id, phone, data['progress'], data['total'],
                data['status'], data.get('message', ''),
                data.get('error'), data.get('stats')
            )
        except Exception as e:
            logger.warning(f"⚠️ Failed to flush progress to DB: {e}")


def _flush_logs_to_db():
    """Flush queued log entries to database (called by background worker)"""
    global _log_write_queue

    with _log_write_lock:
        if not _log_write_queue:
            return
        queue_copy = list(_log_write_queue)
        _log_write_queue = []

    # Write all queued logs
    for op_id, phone, message, level in queue_copy:
        try:
            db_add_operation_log(op_id, phone, message, level)
        except Exception as e:
            logger.warning(f"⚠️ Failed to flush log to DB: {e}")


def _db_flush_worker():
    """Background thread that periodically flushes queued writes to database"""
    import time
    while True:
        time.sleep(DB_FLUSH_INTERVAL)
        try:
            _flush_progress_to_db()
            _flush_logs_to_db()
        except Exception as e:
            logger.error(f"❌ Error in DB flush worker: {e}")


def _start_db_flush_thread():
    """Start the background DB flush thread (called once on startup)"""
    global _db_flush_thread_started
    if not _db_flush_thread_started:
        _db_flush_thread_started = True
        flush_thread = threading.Thread(target=_db_flush_worker, daemon=True, name="db_flush_worker")
        flush_thread.start()
        logger.info("📦 Started database flush worker thread")


def create_operation(operation_type: str, phones: List[str], params: Dict = None) -> str:
    """
    Create a new operation and return its ID.
    Persists to both in-memory dict (for fast WebSocket) and database (for recovery).

    Args:
        operation_type: Type of operation (scan, backup, import_devs, etc.)
        phones: List of phone numbers involved
        params: Additional parameters for the operation

    Returns:
        Operation ID (UUID)
    """
    operation_id = str(uuid.uuid4())[:8]  # Short UUID for readability

    # Normalize phone numbers
    normalized_phones = [normalize_phone(p) for p in phones]

    # In-memory storage for fast WebSocket updates
    with operations_lock:
        active_operations[operation_id] = {
            'id': operation_id,
            'type': operation_type,
            'phones': normalized_phones,
            'params': params or {},
            'status': 'pending',
            'created_at': datetime.now().isoformat(),
            'accounts': {
                phone: {
                    'phone': phone,
                    'status': 'pending',
                    'progress': 0,
                    'total': 0,
                    'message': '',
                    'logs': [],
                    'error': None,
                    'stats': {}
                } for phone in normalized_phones
            },
            'results': {}
        }

    # Persist to database (fire-and-forget)
    try:
        db_create_operation(operation_id, operation_type, normalized_phones, params)
    except Exception as e:
        logger.warning(f"⚠️ Failed to persist operation to DB: {e}")

    logger.info(f"📋 Created operation {operation_id}: {operation_type} for {len(phones)} accounts")
    return operation_id


def get_operation(operation_id: str) -> Optional[Dict]:
    """
    Get operation details by ID.
    Tries in-memory first (fast), falls back to database (for reconnection).
    """
    # Try in-memory first (fast path for active operations)
    with operations_lock:
        if operation_id in active_operations:
            return active_operations[operation_id]

    # Fall back to database (reconnection scenario)
    try:
        db_op = db_get_operation(operation_id)
        if db_op:
            # Restore to memory for continued tracking
            with operations_lock:
                if operation_id not in active_operations:
                    active_operations[operation_id] = db_op
            return db_op
    except Exception as e:
        logger.warning(f"⚠️ Failed to get operation from DB: {e}")

    return None


def update_account_progress(operation_id: str, phone: str, progress: int, total: int,
                           status: str, message: str = '', error: str = None,
                           stats: Dict = None) -> None:
    """
    Update progress for an account in an operation and emit via WebSocket.
    Also queues update for batched database write.

    Args:
        operation_id: Operation ID
        phone: Phone number of the account
        progress: Current progress count
        total: Total count
        status: Status (pending, running, completed, error)
        message: Progress message
        error: Error message if status is 'error'
        stats: Structured stats dict (added, skipped, failed, success_rate, speed, eta_seconds, etc.)
    """
    clean_phone = normalize_phone(phone)

    # Update in-memory immediately (for WebSocket responsiveness)
    with operations_lock:
        if operation_id not in active_operations:
            return

        op = active_operations[operation_id]
        if clean_phone in op['accounts']:
            op['accounts'][clean_phone].update({
                'progress': progress,
                'total': total,
                'status': status,
                'message': message,
                'error': error,
                'stats': stats or {}
            })

    # Queue for batched DB write (non-blocking)
    with _progress_write_lock:
        _progress_write_queue[(operation_id, clean_phone)] = {
            'progress': progress,
            'total': total,
            'status': status,
            'message': message,
            'error': error,
            'stats': stats
        }

    # Emit progress via WebSocket (immediate)
    socketio.emit('operation_progress', {
        'operation_id': operation_id,
        'phone': clean_phone,
        'progress': progress,
        'total': total,
        'status': status,
        'message': message,
        'error': error,
        'stats': stats or {}
    }, room=operation_id)

    logger.debug(f"📊 [{operation_id}] {clean_phone}: {progress}/{total} - {status} - {message}")


def add_account_log(operation_id: str, phone: str, message: str, level: str = 'info') -> None:
    """
    Add a log message for an account and emit via WebSocket.
    Also queues log for batched database write.

    Args:
        operation_id: Operation ID
        phone: Phone number of the account
        message: Log message
        level: Log level (info, warning, error, success)
    """
    clean_phone = normalize_phone(phone)
    log_entry = {
        'timestamp': datetime.now().isoformat(),
        'level': level,
        'message': message
    }

    # Update in-memory immediately
    with operations_lock:
        if operation_id in active_operations:
            op = active_operations[operation_id]
            if clean_phone in op['accounts']:
                op['accounts'][clean_phone]['logs'].append(log_entry)

    # Queue for batched DB write (non-blocking)
    with _log_write_lock:
        _log_write_queue.append((operation_id, clean_phone, message, level))

    # Emit log via WebSocket (immediate)
    socketio.emit('operation_log', {
        'operation_id': operation_id,
        'phone': clean_phone,
        'log': log_entry
    }, room=operation_id)


def emit_batch_delay(operation_id: str, phone: str, batch_number: int,
                     total_batches_estimate: int, delay_seconds: float,
                     success_rate: float, reason: str = 'normal') -> None:
    """
    Emit batch delay start event via WebSocket for frontend countdown display.

    Args:
        operation_id: Operation ID
        phone: Phone number of the account
        batch_number: Current batch number
        total_batches_estimate: Estimated total batches
        delay_seconds: Delay duration in seconds
        success_rate: Current success rate (0.0 to 1.0)
        reason: 'normal' or 'slowdown' (if success_rate < 50%)
    """
    clean_phone = normalize_phone(phone)

    socketio.emit('batch_delay_start', {
        'operation_id': operation_id,
        'phone': clean_phone,
        'batch_number': batch_number,
        'total_batches_estimate': total_batches_estimate,
        'delay_seconds': delay_seconds,
        'success_rate': success_rate,
        'reason': reason
    }, room=operation_id)

    logger.debug(f"⏳ [{operation_id}] {clean_phone}: Batch {batch_number} delay {delay_seconds:.1f}s (success: {success_rate:.1%})")


def complete_operation(operation_id: str, results: Dict = None, error: str = None) -> None:
    """
    Mark an operation as completed and emit final result via WebSocket.
    Flushes any pending DB writes and persists final state.

    Args:
        operation_id: Operation ID
        results: Final results dictionary
        error: Error message if operation failed
    """
    # Flush any pending progress/log writes first
    _flush_progress_to_db()
    _flush_logs_to_db()

    with operations_lock:
        if operation_id not in active_operations:
            return

        op = active_operations[operation_id]
        op['status'] = 'error' if error else 'completed'
        op['completed_at'] = datetime.now().isoformat()
        op['results'] = results or {}
        if error:
            op['error'] = error

    # Persist final state to database
    try:
        db_complete_operation(operation_id, results, error)
    except Exception as e:
        logger.warning(f"⚠️ Failed to persist completion to DB: {e}")

    # Emit completion via WebSocket
    socketio.emit('operation_complete', {
        'operation_id': operation_id,
        'status': 'error' if error else 'completed',
        'results': results,
        'error': error
    }, room=operation_id)

    logger.info(f"{'❌' if error else '✅'} Operation {operation_id} completed")


def cleanup_old_operations(max_age_hours: int = 24) -> int:
    """Remove operations older than max_age_hours"""
    cutoff = datetime.now()
    removed = 0

    with operations_lock:
        to_remove = []
        for op_id, op in active_operations.items():
            created = datetime.fromisoformat(op['created_at'])
            age_hours = (cutoff - created).total_seconds() / 3600
            if age_hours > max_age_hours:
                to_remove.append(op_id)

        for op_id in to_remove:
            del active_operations[op_id]
            removed += 1

    return removed


# =============================================================================
# WebSocket Event Handlers
# =============================================================================

@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    logger.info(f"🔌 WebSocket client connected: {request.sid}")
    emit('connected', {'message': 'Connected to MATRIX WebSocket server'})


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    logger.info(f"🔌 WebSocket client disconnected: {request.sid}")


@socketio.on('subscribe_operation')
def handle_subscribe(data):
    """Subscribe to an operation's updates"""
    operation_id = data.get('operation_id')
    if not operation_id:
        emit('error', {'message': 'operation_id is required'})
        return

    # Join the operation's room
    join_room(operation_id)
    logger.info(f"📺 Client {request.sid} subscribed to operation {operation_id}")

    # Send current operation state
    op = get_operation(operation_id)
    if op:
        emit('operation_state', op)
    else:
        emit('error', {'message': f'Operation {operation_id} not found'})


@socketio.on('unsubscribe_operation')
def handle_unsubscribe(data):
    """Unsubscribe from an operation's updates"""
    operation_id = data.get('operation_id')
    if operation_id:
        leave_room(operation_id)
        logger.info(f"📺 Client {request.sid} unsubscribed from operation {operation_id}")



# ============================================================================
# PROXY HELPER FUNCTIONS
# ============================================================================

def parse_proxy_url(proxy_url: str) -> Optional[Tuple]:
    """
    Parse proxy URL into Telethon-compatible format.

    Supports:
    - http://ip:port
    - http://user:pass@ip:port
    - socks5://ip:port
    - socks5://user:pass@ip:port

    Args:
        proxy_url: Proxy URL string

    Returns:
        Tuple for Telethon proxy parameter or None if invalid/empty
    """
    if not proxy_url:
        return None

    try:
        import socks
        from urllib.parse import urlparse

        parsed = urlparse(proxy_url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        port = parsed.port
        username = parsed.username
        password = parsed.password

        if not host or not port:
            logger.warning(f"⚠️  Invalid proxy URL (missing host/port): {proxy_url}")
            return None

        # Map scheme to socks type
        if scheme in ('http', 'https'):
            proxy_type = socks.HTTP
        elif scheme == 'socks5':
            proxy_type = socks.SOCKS5
        elif scheme == 'socks4':
            proxy_type = socks.SOCKS4
        else:
            logger.warning(f"⚠️  Unsupported proxy scheme: {scheme}")
            return None

        # Build proxy tuple for Telethon
        # Format: (type, host, port, rdns, username, password)
        if username and password:
            proxy = (proxy_type, host, port, True, username, password)
        else:
            proxy = (proxy_type, host, port)

        logger.info(f"🔌 Parsed proxy: {scheme}://{host}:{port}" + (f" (with auth)" if username else ""))
        return proxy

    except ImportError:
        logger.error("❌ PySocks not installed. Run: pip install pysocks")
        return None
    except Exception as e:
        logger.error(f"❌ Error parsing proxy URL: {e}")
        return None


# ============================================================================
# UNIFIED CONTACT MANAGER CLASS (previously in matrix.py)
# ============================================================================

class UnifiedContactManager:
    """Unified manager for all contact operations"""

    def __init__(self, api_id: int, api_hash: str, phone_number: str = '', proxy: str = None,
                 conn_manager: GlobalConnectionManager = None):
        """
        Initialize manager with optional proxy support and shared connection manager.

        Args:
            api_id: Telegram API ID
            api_hash: Telegram API Hash
            phone_number: Phone number for this account
            proxy: Proxy URL (e.g., "http://ip:port")
            conn_manager: Optional GlobalConnectionManager instance for shared connections.
                         If provided, uses shared client pool instead of creating own client.
        """
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone_number = phone_number
        self.proxy_url = proxy  # Store raw proxy URL (e.g., "http://ip:port")
        self.proxy = parse_proxy_url(proxy)  # Parsed Telethon-compatible proxy tuple

        # GlobalConnectionManager for shared client access (solves session file locking)
        self._conn_manager = conn_manager

        # Create phone-number-specific session filename
        if phone_number:
            # Clean phone number for filename (remove +, -, spaces)
            clean_phone = phone_number.replace('+', '').replace('-', '').replace(' ', '')
            self.session_name = f"session_{clean_phone}"
        else:
            self.session_name = 'session_temp'

        # Store session in sessions/ subfolder
        self.session_path = SESSIONS_DIR / f"{self.session_name}.session"
        self.client: Optional[TelegramClient] = None

        # Statistics
        self.stats = {
            'dev_added': 0,
            'dev_skipped': 0,
            'dev_failed': 0,
            'kol_added': 0,
            'kol_skipped': 0,
            'kol_failed': 0,
            'contacts_updated': 0,
            'contacts_checked': 0,
            'yellow_replied': 0,  # For data gathering
        }

        # Advanced anti-rate-limit configuration (based on labeler.py approach)
        self.BATCH_SIZE_MIN = 3  # Variable batch size min
        self.BATCH_SIZE_MAX = 7  # Variable batch size max
        self.PER_CONTACT_DELAY_MIN = 2.0  # Per-contact delay min
        self.PER_CONTACT_DELAY_MAX = 6.0  # Per-contact delay max
        self.BATCH_DELAY_MIN = 45  # Batch delay min (seconds)
        self.BATCH_DELAY_MAX = 90  # Batch delay max (seconds)
        self.BATCH_DELAY_SLOWDOWN = 1.5  # Multiplier if success rate < 50%
        self.GENERIC_FLOOD_WAIT = 300  # Wait 5 minutes for generic FLOOD errors

        # Resume tracking
        self.progress_file = LOGS_DIR / f"progress_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        self.progress_data = {}

        # Contact cache with auto-backup (use per-account cache for scalability)
        self._contact_cache = _account_cache_manager.get_cache(self.phone_number)

    def set_rate_limit_config(self, batch_min: int = 3, batch_max: int = 7,
                              batch_delay_min: int = 45, batch_delay_max: int = 90):
        """Configure anti-rate-limit settings"""
        self.BATCH_SIZE_MIN = batch_min
        self.BATCH_SIZE_MAX = batch_max
        self.BATCH_DELAY_MIN = batch_delay_min
        self.BATCH_DELAY_MAX = batch_delay_max
        self.log(f"⚙️  Rate-limit config: {batch_min}-{batch_max} contacts/batch, {batch_delay_min}-{batch_delay_max}s batch delays")

    # Property accessors for snake_case compatibility (used by API)
    @property
    def batch_size_min(self) -> int:
        return self.BATCH_SIZE_MIN

    @batch_size_min.setter
    def batch_size_min(self, value: int):
        self.BATCH_SIZE_MIN = value

    @property
    def batch_size_max(self) -> int:
        return self.BATCH_SIZE_MAX

    @batch_size_max.setter
    def batch_size_max(self, value: int):
        self.BATCH_SIZE_MAX = value

    @property
    def delay_per_contact_min(self) -> float:
        return self.PER_CONTACT_DELAY_MIN

    @delay_per_contact_min.setter
    def delay_per_contact_min(self, value: float):
        self.PER_CONTACT_DELAY_MIN = value

    @property
    def delay_per_contact_max(self) -> float:
        return self.PER_CONTACT_DELAY_MAX

    @delay_per_contact_max.setter
    def delay_per_contact_max(self, value: float):
        self.PER_CONTACT_DELAY_MAX = value

    @property
    def batch_pause_min(self) -> int:
        return self.BATCH_DELAY_MIN

    @batch_pause_min.setter
    def batch_pause_min(self, value: int):
        self.BATCH_DELAY_MIN = value

    @property
    def batch_pause_max(self) -> int:
        return self.BATCH_DELAY_MAX

    @batch_pause_max.setter
    def batch_pause_max(self, value: int):
        self.BATCH_DELAY_MAX = value

    def log(self, message: str, level: str = "INFO"):
        """Log message with timestamp"""
        if level == "ERROR":
            logger.error(message)
        elif level == "WARNING":
            logger.warning(message)
        else:
            logger.info(message)

    def _save_session_to_database(self, phone_number: str, me=None):
        """Save or update session in the database"""
        if not add_account:
            return  # account_manager not available

        try:
            clean_phone = phone_number.replace('+', '').replace('-', '').replace(' ', '')
            session_path = str(self.session_path)

            # Get account name from user info if available
            account_name = None
            if me:
                account_name = f"{me.first_name} {me.last_name}".strip() if me.last_name else me.first_name

            # Check if account already exists
            existing = get_account_by_phone(clean_phone) if get_account_by_phone else None

            if existing:
                # Update last used timestamp
                if update_account_last_used:
                    update_account_last_used(clean_phone)
                self.log(f"   📝 Updated session in database: {clean_phone}")
            else:
                # Add new account to database (including proxy if configured)
                success = add_account(
                    phone=clean_phone,
                    name=account_name,
                    api_id=self.api_id,
                    api_hash=self.api_hash,
                    session_path=session_path,
                    status='active',
                    proxy=self.proxy_url  # Include proxy if configured
                )
                if success:
                    self.log(f"   💾 Saved session to database: {clean_phone}" + (f" with proxy: {self.proxy_url}" if self.proxy_url else ""))

                    # If this is the first account (no default exists), set it as default
                    try:
                        from account_manager import get_default_account, set_default_account
                        default = get_default_account()
                        if not default:
                            set_default_account(clean_phone)
                            self.log(f"   ⭐ Set as default account (first account)")
                    except ImportError:
                        pass
                else:
                    self.log(f"   ⚠️  Session already exists in database: {clean_phone}")
        except Exception as e:
            self.log(f"   ⚠️  Failed to save session to database: {str(e)}", level="WARNING")

    def _get_random_delay(self) -> float:
        """Get random delay for contacts (2-6 seconds)"""
        return random.uniform(self.PER_CONTACT_DELAY_MIN, self.PER_CONTACT_DELAY_MAX)

    async def init_client(self, phone_number: str, force_new: bool = False) -> bool:
        """
        Initialize Telegram client using TGClient (StringSession).

        If GlobalConnectionManager is available (self._conn_manager), uses shared client pool.
        This prevents session file locking conflicts with InboxManager.

        StringSession eliminates SQLite database locking issues entirely.

        Returns True if successful, False otherwise
        """
        try:
            # ========================================
            # Use GlobalConnectionManager if available (shared client pool)
            # GlobalConnectionManager now uses TGClient internally
            # ========================================
            if self._conn_manager is not None and not force_new:
                self.log(f"🔗 Using shared connection pool (StringSession) for {phone_number}")
                self.client = await self._conn_manager.get_client(
                    phone_number,
                    self.api_id,
                    self.api_hash,
                    str(self.session_path),
                    self.proxy_url,
                    register_events=False  # Operations don't need event handlers
                )
                if self.client:
                    self.log(f"✅ Got shared client for {phone_number}")
                    return True
                else:
                    self.log(f"⚠️  Failed to get shared client, account may need authentication")
                    return False

            # ========================================
            # Direct TGClient creation (for authentication flows)
            # Uses StringSession - no more SQLite database locks!
            # ========================================

            clean_phone = phone_number.replace('+', '').replace('-', '').replace(' ', '')
            session_name = f"session_{clean_phone}"

            # Check if session exists
            if self.session_path.exists() and not force_new:
                self.log(f"📁 Found existing session: {self.session_path.name}")

                try:
                    # Create TGClient with StringSession
                    self._tg_client = TGClient(
                        session_name=session_name,
                        api_id=self.api_id,
                        api_hash=self.api_hash,
                        proxy=self.proxy
                    )

                    await self._tg_client.connect()
                    self.client = self._tg_client.client

                    if await self._tg_client.is_authorized():
                        me = await self._tg_client.get_me()
                        session_user_info = f"{me.first_name} {me.last_name}".strip() if me.last_name else me.first_name

                        self.log(f"   ✅ Session authenticated as: {session_user_info}")
                        # Save session to database
                        self._save_session_to_database(self.phone_number, me)
                        self.log(f"✅ Auto-loaded session (StringSession) - ready to go!")
                        return True
                    else:
                        self.log("⚠️  Session expired, creating new...")
                        await self._tg_client.disconnect(force=True)

                except Exception as session_error:
                    error_str = str(session_error).lower()

                    # Handle specific errors
                    if "api id or hash cannot be empty" in error_str:
                        self.log(f"⚠️  API credentials missing or invalid")
                        self.log(f"   💡 This is likely from config.json issue, NOT session corruption")
                        self.log(f"   ✅ Session file PRESERVED - check your config.json")
                        return False

                    self.log(f"⚠️  Session error: {str(session_error)}")
                    self.log(f"   💡 Session file PRESERVED - you can try re-authenticating")
                    try:
                        if hasattr(self, '_tg_client') and self._tg_client:
                            await self._tg_client.disconnect(force=True)
                    except:
                        pass
                    return False

            # Create new session using TGClient
            self._tg_client = TGClient(
                session_name=session_name,
                api_id=self.api_id,
                api_hash=self.api_hash,
                proxy=self.proxy,
                force_init=True  # Force new instance for fresh auth
            )

            await self._tg_client.connect()
            self.client = self._tg_client.client

            self.log(f"\n🔐 Starting authentication with phone: {phone_number}")
            self.log(f"   📝 Session: {session_name} (StringSession)")
            if self.proxy:
                self.log(f"   🔌 Using proxy: {self.proxy_url}")
            self.log(f"   📱 Check your Telegram app - you'll receive an authentication code")

            await self.client.start(phone=phone_number)
            me = await self.client.get_me()

            # Save session immediately
            self._tg_client._save_session()

            self.log(f"✅ Successfully authenticated as: {me.first_name}")
            self.log(f"   📁 Session saved: {session_name}")
            # Save session to database
            self._save_session_to_database(phone_number, me)
            return True

        except Exception as e:
            self.log(f"❌ Authentication failed: {str(e)}", level="ERROR")
            return False

    async def start_authentication(self, phone_number: str, force_new: bool = False) -> Dict:
        """
        Start authentication process - sends code to phone using TGClient (StringSession).
        Returns dict with status and phone_hash if successful

        Args:
            phone_number: Phone number to authenticate
            force_new: If True, delete existing session file before starting
        """
        try:
            # IMPORTANT: First disconnect from GlobalConnectionManager if connected
            clean_phone = phone_number.replace('+', '').replace('-', '').replace(' ', '')
            session_name = f"session_{clean_phone}"

            conn_manager = GlobalConnectionManager.get_instance()
            if conn_manager.is_connected(clean_phone):
                self.log(f"   🔌 Disconnecting from shared pool before auth...")
                await conn_manager.disconnect_account(clean_phone)
                self.log(f"   ✅ Disconnected from shared pool")

            # Also remove from TGClient singleton cache
            TGClient.remove_instance(session_name, self.api_id, self.api_hash)

            # If force_new, delete existing session
            if force_new and self.session_path.exists():
                self.log(f"   🗑️  Deleting existing session file for re-authentication")
                self.session_path.unlink(missing_ok=True)
            elif self.session_path.exists():
                # Check if existing session is valid using TGClient
                try:
                    test_tg = TGClient(
                        session_name=session_name,
                        api_id=self.api_id,
                        api_hash=self.api_hash,
                        proxy=self.proxy,
                        force_init=True
                    )
                    await test_tg.connect()
                    if not await test_tg.is_authorized():
                        # Session exists but is expired - delete it
                        self.log(f"   🗑️  Deleting expired session file")
                        await test_tg.disconnect(force=True, save_session=False)
                        self.session_path.unlink(missing_ok=True)
                    else:
                        await test_tg.disconnect(force=True)
                    TGClient.remove_instance(session_name, self.api_id, self.api_hash)
                except:
                    # Session file is corrupted or invalid - delete it
                    self.log(f"   🗑️  Deleting invalid session file")
                    self.session_path.unlink(missing_ok=True)
                    TGClient.remove_instance(session_name, self.api_id, self.api_hash)

            # Create new TGClient for authentication
            self._tg_client = TGClient(
                session_name=session_name,
                api_id=self.api_id,
                api_hash=self.api_hash,
                proxy=self.proxy,
                force_init=True
            )
            await self._tg_client.connect()
            self.client = self._tg_client.client

            self.log(f"\n🔐 Starting authentication with phone: {phone_number}")
            self.log(f"   📝 Session: {session_name} (StringSession)")

            # Send code request
            result = await self._tg_client.send_code_request(phone_number)
            phone_code_hash = result.phone_code_hash

            self.log(f"   📱 Code sent to {phone_number}")
            self.log(f"   💡 Enter the code you received in Telegram")

            return {
                'success': True,
                'phone_code_hash': phone_code_hash,
                'message': 'Code sent successfully'
            }
        except Exception as e:
            self.log(f"❌ Failed to send code: {str(e)}", level="ERROR")
            return {
                'success': False,
                'error': str(e)
            }

    async def submit_code(self, phone_number: str, code: str, phone_code_hash: str) -> Dict:
        """
        Submit authentication code using TGClient (StringSession).
        Returns dict with status and user info if successful
        """
        try:
            if not self.client and not hasattr(self, '_tg_client'):
                return {
                    'success': False,
                    'error': 'Client not initialized. Call start_authentication first.'
                }

            # Sign in with code using TGClient
            try:
                if hasattr(self, '_tg_client') and self._tg_client:
                    me = await self._tg_client.sign_in(phone_number, code, phone_code_hash)
                else:
                    me = await self.client.sign_in(phone_number, code, phone_code_hash=phone_code_hash)
            except SessionPasswordNeededError:
                # Account has 2FA enabled
                return {
                    'success': False,
                    'requires_password': True,
                    'message': 'This account has 2FA enabled. Password required.'
                }

            # Successfully authenticated - save session immediately
            if hasattr(self, '_tg_client') and self._tg_client:
                self._tg_client._save_session()

            session_user_info = f"{me.first_name} {me.last_name}".strip() if me.last_name else me.first_name
            self.log(f"✅ Successfully authenticated as: {session_user_info}")
            self.log(f"   📁 Session saved (StringSession)")

            # Save session to database
            self._save_session_to_database(phone_number, me)

            return {
                'success': True,
                'user': {
                    'id': me.id,
                    'first_name': me.first_name,
                    'last_name': me.last_name if me.last_name else '',
                    'username': me.username if me.username else '',
                    'phone': phone_number
                },
                'message': 'Authentication successful'
            }
        except Exception as e:
            self.log(f"❌ Code verification failed: {str(e)}", level="ERROR")
            return {
                'success': False,
                'error': str(e)
            }

    async def submit_password(self, password: str) -> Dict:
        """
        Submit 2FA password using TGClient (StringSession).
        Returns dict with status and user info if successful
        """
        try:
            if not self.client and not hasattr(self, '_tg_client'):
                return {
                    'success': False,
                    'error': 'Client not initialized.'
                }

            # Sign in with password using TGClient
            if hasattr(self, '_tg_client') and self._tg_client:
                me = await self._tg_client.sign_in_with_password(password)
            else:
                me = await self.client.sign_in(password=password)

            # Successfully authenticated - save session immediately
            if hasattr(self, '_tg_client') and self._tg_client:
                self._tg_client._save_session()

            session_user_info = f"{me.first_name} {me.last_name}".strip() if me.last_name else me.first_name
            self.log(f"✅ Successfully authenticated with 2FA as: {session_user_info}")
            self.log(f"   📁 Session saved (StringSession)")

            # Save session to database
            phone_number = self.phone_number
            self._save_session_to_database(phone_number, me)

            return {
                'success': True,
                'user': {
                    'id': me.id,
                    'first_name': me.first_name,
                    'last_name': me.last_name if me.last_name else '',
                    'username': me.username if me.username else '',
                    'phone': phone_number
                },
                'message': 'Authentication successful'
            }
        except Exception as e:
            self.log(f"❌ Password verification failed: {str(e)}", level="ERROR")
            return {
                'success': False,
                'error': str(e)
            }

    async def get_user_display_name(self, username: str) -> Tuple[str, str, Optional[Any]]:
        """
        Scrape display name from Telegram
        Returns: (display_name, status, user_entity)

        The user_entity is returned for linking to inbox system.
        """
        try:
            user = await self.client.get_entity(username)
            first_name = user.first_name if user.first_name else ""
            last_name = user.last_name if user.last_name else ""
            display_name = f"{first_name} {last_name}".strip()

            if not display_name:
                return ("Unknown", "error", None)

            return (display_name, "success", user)

        except UsernameNotOccupiedError:
            return ("", "Username doesn't exist", None)
        except UsernameInvalidError:
            return ("", "Invalid username format", None)
        except FloodWaitError as e:
            self.log(f"⚠️  FLOOD WAIT for {e.seconds}s", level="WARNING")
            await asyncio.sleep(e.seconds)
            return ("", f"Rate limited - retrying next contact", None)
        except Exception as e:
            return ("", str(e), None)

    async def check_existing_contacts(self) -> Tuple[Set[str], Set[str], Set[str]]:
        """
        Get existing contacts
        Returns: (all_usernames, dev_contacts, kol_contacts)
        """
        try:
            result = await self._contact_cache.get_contacts(self.client, phone=self.phone_number)
            existing = set()
            dev_contacts = set()
            kol_contacts = set()

            for contact in result.users:
                if contact.username:
                    username = contact.username.lower()
                    existing.add(username)

                    if contact.first_name:
                        if '🔵💻' in contact.first_name or '🟡💻' in contact.first_name:
                            dev_contacts.add(username)
                        if '🔵📢' in contact.first_name or '🟡📢' in contact.first_name:
                            kol_contacts.add(username)

            return (existing, dev_contacts, kol_contacts)
        except Exception as e:
            self.log(f"⚠️  Error checking contacts: {str(e)}", level="WARNING")
            return (set(), set(), set())

    async def gather_contact_stats(self) -> Dict:
        """
        Gather accurate contact statistics by examining emoji in first_name.
        Returns detailed statistics for dashboard display.
        """
        try:
            result = await self._contact_cache.get_contacts(self.client, phone=self.phone_number)

            stats = {
                'total_contacts': 0,
                'dev_contacts': {
                    'total': 0,
                    'blue': 0,      # 🔵💻 (no reply)
                    'yellow': 0,    # 🟡💻 (replied)
                },
                'kol_contacts': {
                    'total': 0,
                    'blue': 0,      # 🔵📢 (no reply)
                    'yellow': 0,    # 🟡📢 (replied)
                },
            }

            for contact in result.users:
                if contact.first_name:
                    stats['total_contacts'] += 1

                    # Check for dev contacts (💻)
                    if '🔵💻' in contact.first_name:
                        stats['dev_contacts']['total'] += 1
                        stats['dev_contacts']['blue'] += 1
                    elif '🟡💻' in contact.first_name:
                        stats['dev_contacts']['total'] += 1
                        stats['dev_contacts']['yellow'] += 1

                    # Check for KOL contacts (📢)
                    if '🔵📢' in contact.first_name:
                        stats['kol_contacts']['total'] += 1
                        stats['kol_contacts']['blue'] += 1
                    elif '🟡📢' in contact.first_name:
                        stats['kol_contacts']['total'] += 1
                        stats['kol_contacts']['yellow'] += 1

            return stats
        except Exception as e:
            self.log(f"⚠️  Error gathering stats: {str(e)}", level="WARNING")
            return {
                'total_contacts': 0,
                'dev_contacts': {'total': 0, 'blue': 0, 'yellow': 0},
                'kol_contacts': {'total': 0, 'blue': 0, 'yellow': 0},
                'error': str(e),
            }

    async def add_contact(self, username: str, first_name: str, last_name: str = "") -> bool:
        """Add contact to Telegram"""
        try:
            user = await self.client.get_entity(username)
            await self.client(AddContactRequest(
                id=user.id,
                first_name=first_name,
                last_name=last_name,
                phone="",
                add_phone_privacy_exception=False
            ))
            return True
        except Exception as e:
            self.log(f"   ⚠️  Error adding contact: {str(e)}", level="WARNING")
            return False

    async def import_dev_contacts(self, csv_path: str, dry_run: bool = False, interactive: bool = True,
                                   operation_id: str = None, progress_callback = None):
        """Import developer contacts with advanced anti-rate-limit system

        Args:
            csv_path: Path to CSV file
            dry_run: If True, don't actually add contacts
            interactive: If True, prompt for confirmation
            operation_id: Optional operation ID for WebSocket progress
            progress_callback: Optional callback(progress, total, message, contact_info) for real-time updates
        """
        self.log("\n" + "="*70)
        self.log("📥 DEV CONTACT IMPORT (ANTI-RATE-LIMIT MODE)")
        self.log("="*70)

        # Read CSV (with utf-8-sig to handle BOM)
        try:
            with open(csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)

                # Validate required columns
                required_columns = ['group_title', 'dex_chain', 'owner']
                if not reader.fieldnames:
                    self.log(f"❌ CSV file is empty", level="ERROR")
                    return

                missing_columns = [col for col in required_columns if col not in reader.fieldnames]
                if missing_columns:
                    self.log(f"❌ Missing required columns: {', '.join(missing_columns)}", level="ERROR")
                    self.log(f"   Found columns: {', '.join(reader.fieldnames)}", level="ERROR")
                    self.log(f"   Required columns: {', '.join(required_columns)}", level="ERROR")
                    return

                entries = []
                for row in reader:
                    if row.get('owner') and row['owner'].strip():
                        owner = row['owner'].strip().lstrip('@')
                        entries.append({
                            'group_title': row['group_title'].strip(),
                            'dex_chain': row['dex_chain'].strip(),
                            'owner': owner,
                        })
        except FileNotFoundError:
            self.log(f"❌ CSV file not found: {csv_path}", level="ERROR")
            return
        except Exception as e:
            self.log(f"❌ Error reading CSV: {str(e)}", level="ERROR")
            return

        self.log(f"\n📊 Found {len(entries)} valid dev entries")
        self.log(f"📦 Variable batch sizes: {self.BATCH_SIZE_MIN}-{self.BATCH_SIZE_MAX} contacts")
        self.log(f"⏳ Batch delays: {self.BATCH_DELAY_MIN}-{self.BATCH_DELAY_MAX} seconds\n")

        # Tracking variables for stats (initialized early for emit_progress closure)
        import_start_time = None  # Set when actual import starts
        success_count = 0
        failed_count = 0
        processed_count = 0
        current_batch_number = 1
        avg_batch_size = (self.BATCH_SIZE_MIN + self.BATCH_SIZE_MAX) / 2

        # Helper to emit progress with structured stats
        def emit_progress(processed, total, message, contact_info=None):
            nonlocal success_count, failed_count, current_batch_number
            # Calculate structured stats for frontend
            if import_start_time:
                elapsed = (datetime.now() - import_start_time).total_seconds()
                speed = processed / max(1, elapsed / 60) if elapsed > 0 else 0
                remaining = total - processed
                eta_seconds = int(remaining / max(0.01, speed) * 60) if speed > 0 else 0
            else:
                speed = 0
                eta_seconds = 0

            total_batches_est = max(1, math.ceil(total / avg_batch_size)) if avg_batch_size > 0 else 1

            stats = {
                'added': success_count,
                'skipped': self.stats.get('dev_skipped', 0),
                'failed': failed_count,
                'success_rate': success_count / max(1, processed) if processed > 0 else 0,
                'speed': round(speed, 2),
                'eta_seconds': eta_seconds,
                'batch_number': current_batch_number,
                'total_batches_estimate': total_batches_est,
                'start_time': import_start_time.isoformat() if import_start_time else None
            }

            if contact_info:
                contact_info['stats'] = stats
            else:
                contact_info = {'stats': stats}

            if progress_callback:
                progress_callback(processed, total, message, contact_info)

        # Get existing contacts
        existing_contacts, dev_contacts, _ = await self.check_existing_contacts()

        # Filter: Only keep contacts that are NOT already added
        already_dev = [e for e in entries if e['owner'].lower() in dev_contacts]
        already_in_contacts = [e for e in entries if e['owner'].lower() in existing_contacts]
        new_entries = [e for e in entries if e['owner'].lower() not in dev_contacts and e['owner'].lower() not in existing_contacts]

        # Count pre-filtered as skipped in stats
        pre_filtered_count = len(already_dev) + len(already_in_contacts)
        self.stats['dev_skipped'] = pre_filtered_count  # Initialize with pre-filtered count

        # Show preview
        self.log("📋 Preview:")
        self.log(f"   Total entries in CSV: {len(entries)}")
        self.log(f"   Already added as dev: {len(already_dev)}")
        self.log(f"   Already in contacts: {len(already_in_contacts)}")
        self.log(f"   ✨ NEW CONTACTS TO ADD: {len(new_entries)}")

        # Emit initial progress
        total_to_process = len(new_entries)
        emit_progress(0, total_to_process, f"Starting import of {total_to_process} contacts", {
            'phase': 'starting',
            'total_csv': len(entries),
            'already_dev': len(already_dev),
            'already_contacts': len(already_in_contacts),
            'new_to_add': total_to_process
        })

        if len(new_entries) == 0:
            self.log("\n✅ All contacts already added! Nothing to do.")
            return

        if dry_run:
            self.log("\n🔍 DRY RUN MODE - No changes will be made\n")

        # Confirmation (skip if non-interactive/API call)
        if not dry_run and interactive:
            confirm = input(f"\nAdd {len(new_entries)} new contacts? (y/n): ").strip().lower()
            if confirm != 'y':
                self.log("⏹️  Operation cancelled")
                return
        elif not dry_run:
            # API mode: auto-approve if not dry_run
            self.log(f"\n✅ Auto-approved in non-interactive mode")

        self.log(f"\n{'='*70}")
        self.log("🔄 Starting anti-rate-limit import...")
        self.log(f"{'='*70}\n")

        # Set import start time for speed/ETA calculations (variables defined earlier)
        import_start_time = datetime.now()
        session_failed_usernames = set()  # Track usernames that fail (don't exist on Telegram)

        # Process ONLY new entries with intelligent batching
        i = 0
        while i < len(new_entries):
            # Randomize batch size for each batch
            batch_size = random.randint(self.BATCH_SIZE_MIN, self.BATCH_SIZE_MAX)
            batch_entries = new_entries[i:i + batch_size]
            current_batch_number = (processed_count // max(1, self.BATCH_SIZE_MIN)) + 1

            self.log(f"\n📦 BATCH #{current_batch_number} ({len(batch_entries)} contacts)")
            self.log(f"{'─'*70}")

            for entry in batch_entries:
                username = entry['owner'].lower()

                # Skip usernames that previously failed in this session
                if username in session_failed_usernames:
                    self.log(f"   ⏭️  @{entry['owner']} - previously failed (username doesn't exist)")
                    self.stats['dev_skipped'] += 1
                    processed_count += 1
                    emit_progress(processed_count, total_to_process, f"⏭️ @{entry['owner']} - previously failed", {
                        'username': entry['owner'],
                        'status': 'skipped',
                        'reason': 'previously_failed',
                        'added': success_count,
                        'skipped': self.stats['dev_skipped'],
                        'failed': failed_count
                    })
                    continue

                # Check if already added
                if username in dev_contacts:
                    self.log(f"   ⏭️  @{entry['owner']} - already marked as dev")
                    self.stats['dev_skipped'] += 1
                    processed_count += 1
                    emit_progress(processed_count, total_to_process, f"Skipped @{entry['owner']} (already dev)", {
                        'username': entry['owner'],
                        'status': 'skipped',
                        'reason': 'already_dev',
                        'added': success_count,
                        'skipped': self.stats['dev_skipped'],
                        'failed': failed_count
                    })
                    await asyncio.sleep(random.uniform(self.PER_CONTACT_DELAY_MIN, self.PER_CONTACT_DELAY_MAX))
                    continue

                if username in existing_contacts:
                    self.log(f"   ⏭️  @{entry['owner']} - already in contacts")
                    self.stats['dev_skipped'] += 1
                    processed_count += 1
                    emit_progress(processed_count, total_to_process, f"Skipped @{entry['owner']} (in contacts)", {
                        'username': entry['owner'],
                        'status': 'skipped',
                        'reason': 'already_contact',
                        'added': success_count,
                        'skipped': self.stats['dev_skipped'],
                        'failed': failed_count
                    })
                    await asyncio.sleep(random.uniform(self.PER_CONTACT_DELAY_MIN, self.PER_CONTACT_DELAY_MAX))
                    continue

                self.log(f"   🔵💻 @{entry['owner']} ({entry['dex_chain']})")
                emit_progress(processed_count, total_to_process, f"Processing @{entry['owner']}...", {
                    'username': entry['owner'],
                    'status': 'processing',
                    'chain': entry['dex_chain'],
                    'added': success_count,
                    'skipped': self.stats['dev_skipped'],
                    'failed': failed_count
                })

                try:
                    # Get display name and user entity
                    display_name, status, user_entity = await self.get_user_display_name(entry['owner'])

                    if status != "success":
                        self.log(f"❌ {status}")
                        # Track usernames that don't exist on Telegram
                        if "No user has" in status:
                            session_failed_usernames.add(username)
                        self.stats['dev_failed'] += 1
                        failed_count += 1
                        processed_count += 1
                        emit_progress(processed_count, total_to_process, f"Failed @{entry['owner']}: {status}", {
                            'username': entry['owner'],
                            'status': 'failed',
                            'reason': status,
                            'added': success_count,
                            'skipped': self.stats['dev_skipped'],
                            'failed': failed_count
                        })
                        await asyncio.sleep(random.uniform(self.PER_CONTACT_DELAY_MIN, self.PER_CONTACT_DELAY_MAX))
                        continue

                    # Format contact
                    formatted_name = f"🔵💻 {display_name} | {entry['dex_chain']} | {entry['group_title']}"

                    if dry_run:
                        self.log(f"✅ Would add")
                        self.stats['dev_added'] += 1
                        success_count += 1
                        processed_count += 1
                        emit_progress(processed_count, total_to_process, f"Would add @{entry['owner']}", {
                            'username': entry['owner'],
                            'status': 'would_add',
                            'display_name': display_name,
                            'added': success_count,
                            'skipped': self.stats['dev_skipped'],
                            'failed': failed_count
                        })
                    else:
                        success = await self.add_contact(entry['owner'], formatted_name)
                        if success:
                            self.log(f"✅ Added")
                            self.stats['dev_added'] += 1
                            existing_contacts.add(username)
                            dev_contacts.add(username)
                            success_count += 1
                            processed_count += 1
                            emit_progress(processed_count, total_to_process, f"✅ Added @{entry['owner']}", {
                                'username': entry['owner'],
                                'status': 'added',
                                'display_name': display_name,
                                'formatted_name': formatted_name,
                                'added': success_count,
                                'skipped': self.stats['dev_skipped'],
                                'failed': failed_count
                            })

                            # Link to inbox system for first-reply detection
                            if user_entity:
                                try:
                                    inbox_link_matrix_contact(
                                        account_phone=self.phone_number,
                                        peer_id=user_entity.id,
                                        username=entry['owner'],
                                        first_name=user_entity.first_name or "",
                                        last_name=user_entity.last_name or "",
                                        access_hash=user_entity.access_hash or 0,
                                        contact_type='dev',
                                        campaign_id=None  # Could be passed as parameter if needed
                                    )
                                except Exception as link_err:
                                    self.log(f"   ⚠️ Failed to link to inbox: {str(link_err)}", level="WARNING")
                        else:
                            self.log(f"❌ Failed")
                            self.stats['dev_failed'] += 1
                            failed_count += 1
                            processed_count += 1
                            emit_progress(processed_count, total_to_process, f"❌ Failed @{entry['owner']}", {
                                'username': entry['owner'],
                                'status': 'failed',
                                'reason': 'add_failed',
                                'added': success_count,
                                'skipped': self.stats['dev_skipped'],
                                'failed': failed_count
                            })

                except FloodWaitError as e:
                    # Telegram's explicit rate limit
                    wait_time = e.seconds + 10
                    self.log(f"⚠️  FLOOD WAIT for {wait_time}s")
                    self.stats['dev_failed'] += 1
                    failed_count += 1
                    processed_count += 1
                    emit_progress(processed_count, total_to_process, f"⚠️ Rate limited - waiting {wait_time}s", {
                        'username': entry['owner'],
                        'status': 'rate_limited',
                        'wait_time': wait_time,
                        'added': success_count,
                        'skipped': self.stats['dev_skipped'],
                        'failed': failed_count
                    })
                    await asyncio.sleep(wait_time)
                    continue

                except Exception as e:
                    # Check for generic FLOOD errors
                    if "FLOOD" in str(e):
                        self.log(f"⚠️  Generic FLOOD error - waiting {self.GENERIC_FLOOD_WAIT}s")
                        emit_progress(processed_count, total_to_process, f"⚠️ Flood error - waiting {self.GENERIC_FLOOD_WAIT}s", {
                            'username': entry['owner'],
                            'status': 'flood_wait',
                            'wait_time': self.GENERIC_FLOOD_WAIT,
                            'added': success_count,
                            'skipped': self.stats['dev_skipped'],
                            'failed': failed_count
                        })
                        await asyncio.sleep(self.GENERIC_FLOOD_WAIT)
                    else:
                        self.log(f"❌ Error: {str(e)}")
                    self.stats['dev_failed'] += 1
                    failed_count += 1
                    processed_count += 1
                    continue

                # Per-contact delay
                await asyncio.sleep(random.uniform(self.PER_CONTACT_DELAY_MIN, self.PER_CONTACT_DELAY_MAX))

            # After batch, check if we need to wait
            if i + batch_size < len(new_entries):
                # Calculate success rate and estimated batches remaining
                current_success_rate = success_count / max(1, processed_count) if processed_count > 0 else 0
                remaining_contacts = len(new_entries) - processed_count
                total_batches_estimate = current_batch_number + math.ceil(remaining_contacts / avg_batch_size)

                # Determine batch delay with adaptive slowdown
                batch_delay = random.uniform(self.BATCH_DELAY_MIN, self.BATCH_DELAY_MAX)
                delay_reason = 'normal'
                if current_success_rate < 0.5 and processed_count > 5:
                    batch_delay *= self.BATCH_DELAY_SLOWDOWN
                    delay_reason = 'slowdown'
                    self.log(f"   ⚠️  Low success rate ({current_success_rate:.1%}) - applying slowdown")

                self.log(f"   ⏳ Batch complete. Success rate: {current_success_rate:.1%}")
                self.log(f"   💤 Waiting {batch_delay:.1f}s before next batch...\n")

                # Emit batch delay event for frontend countdown
                if operation_id:
                    emit_batch_delay(
                        operation_id=operation_id,
                        phone=self.phone_number,
                        batch_number=current_batch_number,
                        total_batches_estimate=int(total_batches_estimate),
                        delay_seconds=batch_delay,
                        success_rate=current_success_rate,
                        reason=delay_reason
                    )

                await asyncio.sleep(batch_delay)

            i += batch_size

        # Summary
        success_rate = (success_count / max(1, processed_count) * 100) if processed_count > 0 else 0
        self.log(f"\n{'='*70}")
        self.log(f"✅ Dev import completed!")
        self.log(f"   ✅ Added: {self.stats['dev_added']}")
        self.log(f"   ⏭️  Skipped: {self.stats['dev_skipped']}")
        self.log(f"   ❌ Failed: {self.stats['dev_failed']}")
        self.log(f"   📊 Total success rate: {success_rate:.1f}%")
        self.log(f"{'='*70}\n")

        # Invalidate cache and trigger fresh backup after import
        if not dry_run and self.stats['dev_added'] > 0:
            self._contact_cache.invalidate()
            await self._contact_cache.get_contacts(self.client, phone=self.phone_number, force_refresh=True)
            self.log(f"💾 Backup automatically refreshed")

    async def import_kol_contacts(self, csv_path: str, dry_run: bool = False, interactive: bool = True,
                                   operation_id: str = None, progress_callback = None):
        """Import KOL contacts with advanced anti-rate-limit system

        Args:
            csv_path: Path to CSV file
            dry_run: If True, don't actually add contacts
            interactive: If True, prompt for confirmation
            operation_id: Optional operation ID for WebSocket progress
            progress_callback: Optional callback(progress, total, message, contact_info) for real-time updates
        """
        self.log("\n" + "="*70)
        self.log("📥 KOL CONTACT IMPORT (ANTI-RATE-LIMIT MODE)")
        self.log("="*70)

        # Tracking variables for stats (initialized early for emit_progress closure)
        import_start_time = None  # Set when actual import starts
        success_count = 0
        failed_count = 0
        processed_count = 0
        current_batch_number = 1
        avg_batch_size = (self.BATCH_SIZE_MIN + self.BATCH_SIZE_MAX) / 2

        # Helper to emit progress with structured stats
        def emit_progress(processed, total, message, contact_info=None):
            nonlocal success_count, failed_count, current_batch_number
            # Calculate structured stats for frontend
            if import_start_time:
                elapsed = (datetime.now() - import_start_time).total_seconds()
                speed = processed / max(1, elapsed / 60) if elapsed > 0 else 0
                remaining = total - processed
                eta_seconds = int(remaining / max(0.01, speed) * 60) if speed > 0 else 0
            else:
                speed = 0
                eta_seconds = 0

            total_batches_est = max(1, math.ceil(total / avg_batch_size)) if avg_batch_size > 0 else 1

            stats = {
                'added': success_count,
                'skipped': self.stats.get('kol_skipped', 0),
                'failed': failed_count,
                'success_rate': success_count / max(1, processed) if processed > 0 else 0,
                'speed': round(speed, 2),
                'eta_seconds': eta_seconds,
                'batch_number': current_batch_number,
                'total_batches_estimate': total_batches_est,
                'start_time': import_start_time.isoformat() if import_start_time else None
            }

            if contact_info:
                contact_info['stats'] = stats
            else:
                contact_info = {'stats': stats}

            if progress_callback:
                progress_callback(processed, total, message, contact_info)

        # Read CSV (with utf-8-sig to handle BOM)
        try:
            with open(csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)

                # Validate required columns
                required_columns = ['Twitter Username', 'TG Usernames']
                if not reader.fieldnames:
                    self.log(f"❌ CSV file is empty", level="ERROR")
                    return

                missing_columns = [col for col in required_columns if col not in reader.fieldnames]
                if missing_columns:
                    self.log(f"❌ Missing required columns: {', '.join(missing_columns)}", level="ERROR")
                    self.log(f"   Found columns: {', '.join(reader.fieldnames)}", level="ERROR")
                    self.log(f"   Required columns: {', '.join(required_columns)}", level="ERROR")
                    return

                entries = []
                for row in reader:
                    if row.get('TG Usernames') and row['TG Usernames'].strip():
                        entries.append({
                            'twitter': row['Twitter Username'].strip().lstrip('@'),
                            'telegram': row['TG Usernames'].strip().lstrip('@')
                        })
        except FileNotFoundError:
            self.log(f"❌ CSV file not found: {csv_path}", level="ERROR")
            return
        except Exception as e:
            self.log(f"❌ Error reading CSV: {str(e)}", level="ERROR")
            return

        self.log(f"\n📊 Found {len(entries)} valid KOL entries")
        self.log(f"📦 Variable batch sizes: {self.BATCH_SIZE_MIN}-{self.BATCH_SIZE_MAX} contacts")
        self.log(f"⏳ Batch delays: {self.BATCH_DELAY_MIN}-{self.BATCH_DELAY_MAX} seconds\n")

        # Get existing contacts
        existing_contacts, _, kol_contacts = await self.check_existing_contacts()

        # Calculate pre-filtered counts
        already_kol = [e for e in entries if e['telegram'].lower() in kol_contacts]
        already_in_contacts = [e for e in entries if e['telegram'].lower() in existing_contacts]

        # Count pre-filtered as skipped in stats
        pre_filtered_count = len(already_kol) + len(already_in_contacts)
        self.stats['kol_skipped'] = pre_filtered_count  # Initialize with pre-filtered count

        # Show preview
        self.log("📋 Preview:")
        self.log(f"   Total entries: {len(entries)}")
        self.log(f"   Already added as KOL: {len(already_kol)}")
        self.log(f"   Already in contacts: {len(already_in_contacts)}")

        if dry_run:
            self.log("\n🔍 DRY RUN MODE - No changes will be made\n")

        # Confirmation (skip if non-interactive/API call)
        if not dry_run and interactive:
            confirm = input("\nProceed? (y/n): ").strip().lower()
            if confirm != 'y':
                self.log("⏹️  Operation cancelled")
                return
        elif not dry_run:
            # API mode: auto-approve if not dry_run
            self.log(f"\n✅ Auto-approved in non-interactive mode")

        self.log(f"\n{'='*70}")
        self.log("🔄 Starting anti-rate-limit import...")
        self.log(f"{'='*70}\n")

        # Set import start time for speed/ETA calculations (variables defined earlier)
        import_start_time = datetime.now()
        total_to_process = len(entries)
        session_failed_usernames = set()  # Track usernames that fail (don't exist on Telegram)

        # Emit initial progress
        emit_progress(0, total_to_process, f"Starting import of {total_to_process} KOLs", {
            'phase': 'starting',
            'total_csv': len(entries)
        })

        # Process entries with intelligent batching
        i = 0
        while i < len(entries):
            # Randomize batch size for each batch
            batch_size = random.randint(self.BATCH_SIZE_MIN, self.BATCH_SIZE_MAX)
            batch_entries = entries[i:i + batch_size]
            current_batch_number = (processed_count // max(1, self.BATCH_SIZE_MIN)) + 1

            self.log(f"\n📦 BATCH #{current_batch_number} ({len(batch_entries)} contacts)")
            self.log(f"{'─'*70}")

            for entry in batch_entries:
                username = entry['telegram'].lower()

                # Skip usernames that previously failed in this session
                if username in session_failed_usernames:
                    self.log(f"   ⏭️  @{entry['telegram']} - previously failed (username doesn't exist)")
                    self.stats['kol_skipped'] += 1
                    processed_count += 1
                    emit_progress(processed_count, total_to_process, f"⏭️ @{entry['telegram']} - previously failed", {
                        'username': entry['telegram'],
                        'status': 'skipped',
                        'reason': 'previously_failed',
                        'added': success_count,
                        'skipped': self.stats['kol_skipped'],
                        'failed': failed_count
                    })
                    continue

                # Check if already added
                if username in kol_contacts:
                    self.log(f"   ⏭️  @{entry['telegram']} - already marked as KOL")
                    self.stats['kol_skipped'] += 1
                    processed_count += 1
                    emit_progress(processed_count, total_to_process, f"Skipped @{entry['telegram']} (already KOL)", {
                        'username': entry['telegram'],
                        'status': 'skipped',
                        'reason': 'already_kol',
                        'added': success_count,
                        'skipped': self.stats['kol_skipped'],
                        'failed': failed_count
                    })
                    await asyncio.sleep(random.uniform(self.PER_CONTACT_DELAY_MIN, self.PER_CONTACT_DELAY_MAX))
                    continue

                if username in existing_contacts:
                    self.log(f"   ⏭️  @{entry['telegram']} - already in contacts")
                    self.stats['kol_skipped'] += 1
                    processed_count += 1
                    emit_progress(processed_count, total_to_process, f"Skipped @{entry['telegram']} (in contacts)", {
                        'username': entry['telegram'],
                        'status': 'skipped',
                        'reason': 'already_contact',
                        'added': success_count,
                        'skipped': self.stats['kol_skipped'],
                        'failed': failed_count
                    })
                    await asyncio.sleep(random.uniform(self.PER_CONTACT_DELAY_MIN, self.PER_CONTACT_DELAY_MAX))
                    continue

                self.log(f"   🔵📢 @{entry['telegram']} (@{entry['twitter']})")
                emit_progress(processed_count, total_to_process, f"Processing @{entry['telegram']}...", {
                    'username': entry['telegram'],
                    'status': 'processing',
                    'added': success_count,
                    'skipped': self.stats['kol_skipped'],
                    'failed': failed_count
                })

                try:
                    # Check if client is still connected before each contact
                    if not self.client or not self.client.is_connected():
                        self.log(f"   ⚠️  Client disconnected, attempting to reconnect...")
                        try:
                            await self.init_client(self.phone_number)
                            self.log(f"   ✅ Reconnected successfully")
                        except Exception as reconnect_error:
                            self.log(f"   ❌ Failed to reconnect: {str(reconnect_error)}", level="ERROR")
                            # Mark remaining as failed and exit
                            remaining = total_to_process - processed_count
                            self.stats['kol_failed'] += remaining
                            emit_progress(total_to_process, total_to_process, f"❌ Import stopped: connection lost", {
                                'status': 'connection_lost',
                                'added': success_count,
                                'skipped': self.stats['kol_skipped'],
                                'failed': self.stats['kol_failed']
                            })
                            return
                    
                    # Get display name and user entity
                    display_name, status, user_entity = await self.get_user_display_name(entry['telegram'])

                    if status != "success":
                        self.log(f"❌ {status}")
                        self.log(f"{'─'*70}")
                        # Track usernames that don't exist on Telegram
                        if "No user has" in status:
                            session_failed_usernames.add(username)
                        self.stats['kol_failed'] += 1
                        failed_count += 1
                        processed_count += 1
                        emit_progress(processed_count, total_to_process, f"Failed @{entry['telegram']}: {status}", {
                            'username': entry['telegram'],
                            'status': 'failed',
                            'reason': status,
                            'added': success_count,
                            'skipped': self.stats['kol_skipped'],
                            'failed': failed_count
                        })
                        await asyncio.sleep(random.uniform(self.PER_CONTACT_DELAY_MIN, self.PER_CONTACT_DELAY_MAX))
                        continue

                    # Format contact: 🔵📢Telegram Username | @Twitter Username
                    formatted_name = f"🔵📢{entry['telegram']} | @{entry['twitter']}"

                    if dry_run:
                        self.log(f"   📝 {formatted_name}")
                        self.log(f"✅ Would add")
                        self.stats['kol_added'] += 1
                        success_count += 1
                        processed_count += 1
                        emit_progress(processed_count, total_to_process, f"Would add @{entry['telegram']}", {
                            'username': entry['telegram'],
                            'status': 'would_add',
                            'display_name': display_name,
                            'formatted_name': formatted_name,
                            'added': success_count,
                            'skipped': self.stats['kol_skipped'],
                            'failed': failed_count
                        })
                    else:
                        success = await self.add_contact(entry['telegram'], formatted_name)
                        if success:
                            self.log(f"   📝 {formatted_name}")
                            self.log(f"✅ Added")
                            self.stats['kol_added'] += 1
                            existing_contacts.add(username)
                            kol_contacts.add(username)
                            success_count += 1
                            processed_count += 1
                            emit_progress(processed_count, total_to_process, f"✅ Added @{entry['telegram']}", {
                                'username': entry['telegram'],
                                'status': 'added',
                                'display_name': display_name,
                                'formatted_name': formatted_name,
                                'added': success_count,
                                'skipped': self.stats['kol_skipped'],
                                'failed': failed_count
                            })

                            # Link to inbox system for first-reply detection
                            if user_entity:
                                try:
                                    inbox_link_matrix_contact(
                                        account_phone=self.phone_number,
                                        peer_id=user_entity.id,
                                        username=entry['telegram'],
                                        first_name=user_entity.first_name or "",
                                        last_name=user_entity.last_name or "",
                                        access_hash=user_entity.access_hash or 0,
                                        contact_type='kol',
                                        campaign_id=None  # Could be passed as parameter if needed
                                    )
                                except Exception as link_err:
                                    self.log(f"   ⚠️ Failed to link to inbox: {str(link_err)}", level="WARNING")
                        else:
                            self.log(f"   📝 {formatted_name}")
                            self.log(f"❌ Failed")
                            self.stats['kol_failed'] += 1
                            failed_count += 1
                            processed_count += 1
                            emit_progress(processed_count, total_to_process, f"❌ Failed @{entry['telegram']}", {
                                'username': entry['telegram'],
                                'status': 'failed',
                                'reason': 'add_failed',
                                'added': success_count,
                                'skipped': self.stats['kol_skipped'],
                                'failed': failed_count
                            })

                    self.log(f"{'─'*70}")

                except FloodWaitError as e:
                    # Telegram's explicit rate limit
                    wait_time = e.seconds + 10
                    self.log(f"⚠️  FLOOD WAIT for {wait_time}s")
                    self.stats['kol_failed'] += 1
                    failed_count += 1
                    processed_count += 1
                    emit_progress(processed_count, total_to_process, f"⚠️ Rate limited - waiting {wait_time}s", {
                        'username': entry['telegram'],
                        'status': 'rate_limited',
                        'wait_time': wait_time,
                        'added': success_count,
                        'skipped': self.stats['kol_skipped'],
                        'failed': failed_count
                    })
                    await asyncio.sleep(wait_time)
                    continue

                except Exception as e:
                    error_str = str(e)
                    # Check for connection-related errors
                    if any(x in error_str for x in ['AUTH_KEY', 'Unauthorized', 'ConnectionError', 'disconnected']):
                        self.log(f"⚠️  Connection error detected: {error_str}", level="ERROR")
                        self.log(f"   Attempting to reconnect...")
                        try:
                            await self.init_client(self.phone_number)
                            self.log(f"   ✅ Reconnected, retrying contact...")
                            # Don't increment counters - we'll retry this contact
                            continue
                        except Exception as reconnect_error:
                            self.log(f"   ❌ Reconnection failed: {str(reconnect_error)}", level="ERROR")
                            remaining = total_to_process - processed_count
                            self.stats['kol_failed'] += remaining
                            emit_progress(total_to_process, total_to_process, f"❌ Import stopped: connection lost", {
                                'status': 'connection_lost',
                                'added': success_count,
                                'skipped': self.stats['kol_skipped'],
                                'failed': self.stats['kol_failed']
                            })
                            return
                    # Check for generic FLOOD errors
                    elif "FLOOD" in error_str:
                        self.log(f"⚠️  Generic FLOOD error - waiting {self.GENERIC_FLOOD_WAIT}s")
                        emit_progress(processed_count, total_to_process, f"⚠️ Flood error - waiting {self.GENERIC_FLOOD_WAIT}s", {
                            'username': entry['telegram'],
                            'status': 'flood_wait',
                            'wait_time': self.GENERIC_FLOOD_WAIT,
                            'added': success_count,
                            'skipped': self.stats['kol_skipped'],
                            'failed': failed_count
                        })
                        await asyncio.sleep(self.GENERIC_FLOOD_WAIT)
                    else:
                        self.log(f"❌ Error: {error_str}")
                    self.stats['kol_failed'] += 1
                    failed_count += 1
                    processed_count += 1
                    continue

                # Per-contact delay
                await asyncio.sleep(random.uniform(self.PER_CONTACT_DELAY_MIN, self.PER_CONTACT_DELAY_MAX))

            # After batch, check if we need to wait
            if i + batch_size < len(entries):
                # Calculate success rate and estimated batches remaining
                current_success_rate = success_count / max(1, processed_count) if processed_count > 0 else 0
                remaining_contacts = len(entries) - processed_count
                total_batches_estimate = current_batch_number + math.ceil(remaining_contacts / avg_batch_size)

                # Determine batch delay with adaptive slowdown
                batch_delay = random.uniform(self.BATCH_DELAY_MIN, self.BATCH_DELAY_MAX)
                delay_reason = 'normal'
                if current_success_rate < 0.5 and processed_count > 5:
                    batch_delay *= self.BATCH_DELAY_SLOWDOWN
                    delay_reason = 'slowdown'
                    self.log(f"   ⚠️  Low success rate ({current_success_rate:.1%}) - applying slowdown")

                self.log(f"   ⏳ Batch complete. Success rate: {current_success_rate:.1%}")
                self.log(f"   💤 Waiting {batch_delay:.1f}s before next batch...\n")

                # Emit batch delay event for frontend countdown
                if operation_id:
                    emit_batch_delay(
                        operation_id=operation_id,
                        phone=self.phone_number,
                        batch_number=current_batch_number,
                        total_batches_estimate=int(total_batches_estimate),
                        delay_seconds=batch_delay,
                        success_rate=current_success_rate,
                        reason=delay_reason
                    )

                await asyncio.sleep(batch_delay)

            i += batch_size

        # Summary
        success_rate = (success_count / max(1, processed_count) * 100) if processed_count > 0 else 0
        self.log(f"\n{'='*70}")
        self.log(f"✅ KOL import completed!")
        self.log(f"   ✅ Added: {self.stats['kol_added']}")
        self.log(f"   ⏭️  Skipped: {self.stats['kol_skipped']}")
        self.log(f"   ❌ Failed: {self.stats['kol_failed']}")
        self.log(f"   📊 Total success rate: {success_rate:.1f}%")
        self.log(f"{'='*70}\n")

        # Invalidate cache and trigger fresh backup after import
        if not dry_run and self.stats['kol_added'] > 0:
            self._contact_cache.invalidate()
            await self._contact_cache.get_contacts(self.client, phone=self.phone_number, force_refresh=True)
            self.log(f"💾 Backup automatically refreshed")

    async def check_reply(self, entity, hours: int = 48) -> bool:
        """Check if contact replied within timeframe"""
        try:
            cutoff_time = datetime.now() - timedelta(hours=hours)
            your_msgs = their_msgs = 0

            async for msg in self.client.iter_messages(entity, limit=100):
                msg_time = msg.date.replace(tzinfo=None) if msg.date.tzinfo else msg.date
                if msg_time < cutoff_time:
                    break
                if msg.out:
                    your_msgs += 1
                else:
                    their_msgs += 1

            return your_msgs > 0 and their_msgs > 0
        except Exception as e:
            self.log(f"   ⚠️  Error checking replies: {str(e)}", level="WARNING")
            return False

    async def scan_for_replies(self, dialog_limit: int = 100, log_callback=None) -> Dict:
        """
        Scan inbox dialogs to detect replies from BLUE CONTACTS ONLY.
        Faster method than checking message history - filters to blue contacts and groups only.

        Args:
            dialog_limit: Number of most recent dialogs to check (default 100)
            log_callback: Optional callback function to send real-time logs to frontend

        Returns:
            Dict with:
              - 'id_statuses': {user_id: True, ...} for update_statuses()
              - 'name_statuses': {contact_name: True, ...} for frontend display
        """
        try:
            # Helper to log and optionally send to frontend
            def log_and_callback(message):
                self.log(message)
                if log_callback:
                    log_callback(message)

            self.log("\n" + "="*70)
            self.log(f"📱 SCANNING DIALOGS FOR REPLIES (Blue Contacts Only)")
            self.log("="*70 + "\n")

            # Step 1: Get all blue contacts (with 🔵 emoji)
            self.log("Fetching blue contacts...")
            result = await self._contact_cache.get_contacts(self.client, phone=self.phone_number)

            blue_dev_contacts = [
                u for u in result.users
                if u.first_name and '🔵💻' in u.first_name
            ]

            blue_kol_contacts = [
                u for u in result.users
                if u.first_name and '🔵📢' in u.first_name
            ]

            all_blue_contacts = blue_dev_contacts + blue_kol_contacts
            total_blue = len(all_blue_contacts)

            if total_blue == 0:
                log_and_callback("No blue contacts found to check.")
                return {'id_statuses': {}, 'name_statuses': {}}

            log_and_callback(f"Found {total_blue} blue contacts:")
            log_and_callback(f"   🔵💻 Blue Developers: {len(blue_dev_contacts)}")
            log_and_callback(f"   🔵📢 Blue KOLs: {len(blue_kol_contacts)}")

            # Build mapping of blue contact user IDs for faster lookup
            blue_contact_map = {}
            for user in all_blue_contacts:
                blue_contact_map[user.id] = user

            # Step 2: Scan dialogs for replies from blue contacts
            log_and_callback(f"Scanning {dialog_limit} dialogs for replies...")
            log_and_callback(f"📊 DEBUG: Blue contacts found: {len(all_blue_contacts)}")
            dialogs = await self.client.get_dialogs(limit=dialog_limit)
            log_and_callback(f"📊 DEBUG: Dialogs scanned: {len(dialogs)}")

            id_statuses = {}      # {user_id: True} for update_statuses()
            name_statuses = {}    # {contact_name: True} for frontend
            blue_dev_replied = 0
            blue_kol_replied = 0
            dialogs_checked = 0

            # Get our user ID for comparison
            me = await self.client.get_me()
            my_id = me.id

            for dialog in dialogs:
                entity = dialog.entity
                dialogs_checked += 1

                # SKIP CHANNELS - only check users and groups
                if isinstance(entity, Channel):
                    continue  # No delay needed - no API call here

                # Check if this dialog is with a blue contact (by user ID)
                if isinstance(entity, User) and entity.id in blue_contact_map:
                    matching_blue_contact = blue_contact_map[entity.id]

                    # Use dialog.message (already cached) - NO API CALL NEEDED!
                    # This is the same approach used in check_seen_no_reply()
                    last_message = dialog.message

                    if last_message:
                        # Check if the last message is FROM them (not from us)
                        # This confirms they replied AFTER we messaged them
                        if last_message.from_id and hasattr(last_message.from_id, 'user_id'):
                            sender_id = last_message.from_id.user_id
                        elif last_message.sender_id:
                            sender_id = last_message.sender_id
                        else:
                            sender_id = None

                        # DEBUG: Log message details
                        log_and_callback(f"   📧 Dialog with {matching_blue_contact.first_name}: "
                               f"last_msg_from={'them' if sender_id == entity.id else 'us' if sender_id == my_id else 'unknown'}, "
                               f"unread={dialog.unread_count}")

                        # If last message is FROM them (not us), they replied
                        if sender_id == entity.id:
                            id_statuses[matching_blue_contact.id] = True
                            name_statuses[matching_blue_contact.first_name] = True

                            # Track dev vs KOL
                            if '🔵💻' in matching_blue_contact.first_name:
                                blue_dev_replied += 1
                            else:
                                blue_kol_replied += 1

                            log_and_callback(f"   ✅ REPLY DETECTED from {matching_blue_contact.first_name}")

                # No delay needed here - all dialog iteration is in-memory processing
                # Dialogs were fetched in ONE API call above (get_dialogs)

            log_and_callback(f"📊 DEBUG: Matches found: {len(id_statuses)}")
            log_and_callback(f"📊 DEBUG: Dialogs checked: {dialogs_checked}")

            # Step 3: Calculate stats
            blue_dev_no_reply = len(blue_dev_contacts) - blue_dev_replied
            blue_kol_no_reply = len(blue_kol_contacts) - blue_kol_replied

            # Update stats
            self.stats['contacts_checked'] += total_blue

            # Step 4: Display results
            self.log(f"{'='*70}")
            log_and_callback(f"📊 SCAN RESULTS - BY TYPE:")
            self.log(f"{'='*70}\n")

            log_and_callback(f"🔵💻 BLUE DEVELOPERS: {len(blue_dev_contacts)} total")
            log_and_callback(f"   ✅ Replied: {blue_dev_replied}")
            log_and_callback(f"   ❌ No reply: {blue_dev_no_reply}")

            log_and_callback(f"🔵📢 BLUE KOLs: {len(blue_kol_contacts)} total")
            log_and_callback(f"   ✅ Replied: {blue_kol_replied}")
            log_and_callback(f"   ❌ No reply: {blue_kol_no_reply}")

            log_and_callback(f"🔵 TOTAL BLUE CONTACTS: {total_blue}")
            log_and_callback(f"   ✅ Total Replied: {blue_dev_replied + blue_kol_replied}")
            log_and_callback(f"   ❌ Total No reply: {blue_dev_no_reply + blue_kol_no_reply}")
            self.log(f"{'='*70}\n")

            # Step 5: Auto-update statuses if any replied
            if id_statuses:
                await self.update_statuses(id_statuses)

            return {'id_statuses': id_statuses, 'name_statuses': name_statuses}

        except FloodWaitError as e:
            self.log(f"⚠️  RATE LIMITED by Telegram! Wait {e.seconds}s before retrying.", level="ERROR")
            # Raise custom error to stop operation immediately and notify frontend
            raise TelegramRateLimitError(
                wait_seconds=e.seconds,
                message=f"Telegram rate limit during scan. Please wait {e.seconds} seconds ({e.seconds // 60} minutes) before trying again."
            )
        except Exception as e:
            self.log(f"❌ Error scanning dialogs: {str(e)}", level="ERROR")
            return {'id_statuses': {}, 'name_statuses': {}}

    async def update_statuses(self, reply_statuses: Dict, interactive: bool = True):
        """Update contacts from 🔵 to 🟡 if they replied - with type tracking"""
        self.log("\n" + "="*70)
        self.log("🎨 UPDATING STATUSES (🔵 → 🟡)")
        self.log("="*70 + "\n")

        # Get all contacts
        result = await self._contact_cache.get_contacts(self.client, phone=self.phone_number)

        # Filter contacts to update by type
        dev_contacts_to_update = []
        kol_contacts_to_update = []

        for user in result.users:
            if user.id in reply_statuses and reply_statuses[user.id]:
                if user.first_name and '🔵' in user.first_name:
                    if '🔵💻' in user.first_name:
                        dev_contacts_to_update.append(user)
                    elif '🔵📢' in user.first_name:
                        kol_contacts_to_update.append(user)

        total_to_update = len(dev_contacts_to_update) + len(kol_contacts_to_update)

        self.log(f"Found {total_to_update} contacts to update:")
        self.log(f"   🔵💻 Developers: {len(dev_contacts_to_update)}")
        self.log(f"   🔵📢 KOLs: {len(kol_contacts_to_update)}\n")

        # Show preview
        if not reply_statuses or total_to_update == 0:
            self.log("No contacts to update")
            return {'updated_count': 0, 'dev_updated': 0, 'kol_updated': 0}

        # Confirmation (skip if non-interactive/API call)
        if interactive:
            confirm = input("Proceed with updates? (y/n): ").strip().lower()
            if confirm != 'y':
                self.log("⏹️  Update cancelled")
                return {'updated_count': 0, 'dev_updated': 0, 'kol_updated': 0}
        else:
            # API mode: auto-approve
            self.log(f"✅ Auto-approved in non-interactive mode")

        self.log(f"\n{'='*70}")
        self.log("🔄 Updating contacts...")
        self.log(f"{'='*70}\n")

        # Update developer contacts
        if dev_contacts_to_update:
            self.log("🔵💻 Updating Developer Contacts:")
            for i, user in enumerate(dev_contacts_to_update, 1):
                username = user.username or "unknown"
                self.log(f"[{i}/{len(dev_contacts_to_update)}] Updating @{username}...")

                try:
                    # Replace emoji 🔵 → 🟡
                    new_first_name = user.first_name.replace('🔵', '🟡')

                    if new_first_name == user.first_name:
                        self.log("⏭️  Already yellow")
                        await asyncio.sleep(self._get_random_delay())
                        continue

                    await self.client(AddContactRequest(
                        id=user.id,
                        first_name=new_first_name,
                        last_name=user.last_name or "",
                        phone=user.phone or "",
                        add_phone_privacy_exception=False
                    ))

                    self.log("✅ Updated")
                    self.stats['contacts_updated'] += 1
                except Exception as e:
                    self.log(f"❌ Error: {str(e)}", level="WARNING")

                await asyncio.sleep(self._get_random_delay())

        # Update KOL contacts
        if kol_contacts_to_update:
            self.log(f"\n🔵📢 Updating KOL Contacts:")
            for i, user in enumerate(kol_contacts_to_update, 1):
                username = user.username or "unknown"
                self.log(f"[{i}/{len(kol_contacts_to_update)}] Updating @{username}...")

                try:
                    # Replace emoji 🔵 → 🟡
                    new_first_name = user.first_name.replace('🔵', '🟡')

                    if new_first_name == user.first_name:
                        self.log("⏭️  Already yellow")
                        await asyncio.sleep(self._get_random_delay())
                        continue

                    await self.client(AddContactRequest(
                        id=user.id,
                        first_name=new_first_name,
                        last_name=user.last_name or "",
                        phone=user.phone or "",
                        add_phone_privacy_exception=False
                    ))

                    self.log("✅ Updated")
                    self.stats['contacts_updated'] += 1
                except Exception as e:
                    self.log(f"❌ Error: {str(e)}", level="WARNING")

                await asyncio.sleep(self._get_random_delay())

        self.log(f"\n{'='*70}")
        self.log(f"✅ Update completed! Updated: {self.stats['contacts_updated']} contacts")
        self.log(f"{'='*70}\n")

        # Invalidate cache and trigger fresh backup after status update
        if self.stats['contacts_updated'] > 0:
            self._contact_cache.invalidate()
            await self._contact_cache.get_contacts(self.client, phone=self.phone_number, force_refresh=True)
            self.log(f"💾 Backup automatically refreshed")

        return {
            'updated_count': self.stats['contacts_updated'],
            'dev_updated': len(dev_contacts_to_update),
            'kol_updated': len(kol_contacts_to_update)
        }

    async def check_seen_no_reply(self, hours: int = 48, dialog_limit: int = 100, export_csv: bool = True) -> List[Dict]:
        """
        Find blue contacts who saw your message but didn't reply.
        Uses Telegram read receipts (read_outbox_max_id).
        
        FAST VERSION: Dialog-driven approach - processes dialogs in bulk.
        
        Args:
            hours: Time window to check (default 48 hours)
            dialog_limit: Number of dialogs to scan (default 100)
            export_csv: If True, automatically export to CSV files by type (default True)
        
        Returns:
            List of dicts: {username, display_name, type, message_sent_date, last_seen_date}
        """
        self.log("\n" + "="*70)
        self.log(f"👀 CHECKING SEEN BUT NO REPLY (Last {hours} hours)")
        self.log("="*70 + "\n")

        try:
            # Step 1: Get all blue contacts (fast - single API call)
            self.log("Fetching blue contacts...")
            result = await self._contact_cache.get_contacts(self.client, phone=self.phone_number)

            blue_contacts = {}
            for user in result.users:
                if user.first_name and '🔵' in user.first_name:
                    if user.id:
                        contact_type = 'dev' if '💻' in user.first_name else 'kol' if '📢' in user.first_name else 'other'
                        blue_contacts[user.id] = {
                            'username': user.username or 'unknown',
                            'display_name': user.first_name,
                            'type': contact_type
                        }
            
            if not blue_contacts:
                self.log("No blue contacts found to check.\n")
                return []
            
            self.log(f"Found {len(blue_contacts)} blue contacts")
            
            # Step 2: Get dialogs with messages (single API call - FAST!)
            self.log(f"Fetching {dialog_limit} dialogs (this is fast)...")
            dialogs = await self.client.get_dialogs(limit=dialog_limit)
            
            # Time cutoff
            cutoff_time = datetime.now() - timedelta(hours=hours)
            
            seen_no_reply = []
            checked_count = 0
            skipped_not_blue = 0
            skipped_no_outgoing = 0
            skipped_not_read = 0
            skipped_replied = 0
            
            self.log(f"Processing dialogs...\n")
            
            for dialog in dialogs:
                entity = dialog.entity
                
                # Skip if not a user (groups, channels, etc.)
                if not isinstance(entity, User):
                    continue
                
                # Skip if not a blue contact (FAST - just a dict lookup)
                if entity.id not in blue_contacts:
                    skipped_not_blue += 1
                    continue
                
                checked_count += 1
                contact_info = blue_contacts[entity.id]
                username = contact_info['username']
                
                # Get the last message in dialog (already available from get_dialogs!)
                last_msg = dialog.message
                
                if not last_msg:
                    skipped_no_outgoing += 1
                    continue
                
                # Check message time
                msg_time = last_msg.date.replace(tzinfo=None) if last_msg.date.tzinfo else last_msg.date
                
                # If last message is within time window and is OURS (outgoing)
                if last_msg.out and msg_time >= cutoff_time:
                    # Check if they read our message using read_outbox_max_id
                    read_outbox_max_id = getattr(dialog.dialog, 'read_outbox_max_id', 0)
                    message_was_read = read_outbox_max_id >= last_msg.id
                    
                    if not message_was_read:
                        skipped_not_read += 1
                        continue
                    
                    # Message was READ but the last message is still ours = NO REPLY!
                    last_seen_str = "Unknown"
                    if hasattr(entity, 'status') and entity.status:
                        if hasattr(entity.status, 'was_online'):
                            last_seen_str = entity.status.was_online.strftime('%Y-%m-%d %H:%M:%S')
                        elif hasattr(entity.status, '__class__'):
                            status_name = entity.status.__class__.__name__
                            if 'Recently' in status_name:
                                last_seen_str = "Recently"
                            elif 'Week' in status_name:
                                last_seen_str = "Within a week"
                            elif 'Month' in status_name:
                                last_seen_str = "Within a month"
                            elif 'Online' in status_name:
                                last_seen_str = "Online now"
                    
                    seen_no_reply.append({
                        'username': username,
                        'display_name': contact_info['display_name'],
                        'type': contact_info['type'],
                        'message_sent_date': msg_time.strftime('%Y-%m-%d %H:%M:%S'),
                        'last_seen_date': last_seen_str
                    })
                    
                    self.log(f"   ✅ Seen but no reply: @{username}")
                
                elif not last_msg.out:
                    # Last message is from THEM - they replied
                    skipped_replied += 1
                else:
                    # Message outside time window
                    skipped_no_outgoing += 1
            
            # Summary
            self.log(f"\n{'='*70}")
            self.log(f"📊 SCAN RESULTS:")
            self.log(f"{'='*70}")
            self.log(f"   Dialogs scanned: {len(dialogs)}")
            self.log(f"   Blue contacts found in dialogs: {checked_count}")
            self.log(f"   Skipped (not blue contact): {skipped_not_blue}")
            self.log(f"   Skipped (they replied): {skipped_replied}")
            self.log(f"   Skipped (not read yet): {skipped_not_read}")
            self.log(f"   Skipped (no recent outgoing): {skipped_no_outgoing}")
            self.log(f"\n   👀 SEEN BUT NO REPLY: {len(seen_no_reply)}")
            
            dev_count = len([x for x in seen_no_reply if x['type'] == 'dev'])
            kol_count = len([x for x in seen_no_reply if x['type'] == 'kol'])
            self.log(f"      🔵💻 Developers: {dev_count}")
            self.log(f"      🔵📢 KOLs: {kol_count}")
            self.log(f"{'='*70}\n")
            
            # Export to CSV files by type if requested
            if export_csv and seen_no_reply:
                csv_files = self.export_noreply_csv_by_type(seen_no_reply, hours)
                if csv_files:
                    self.log(f"\n📁 CSV files exported:")
                    for file_type, file_path in csv_files.items():
                        self.log(f"   {file_type.upper()}: {file_path}")
            
            return seen_no_reply
            
        except FloodWaitError as e:
            self.log(f"⚠️  RATE LIMITED by Telegram! Wait {e.seconds}s before retrying.", level="ERROR")
            # Raise custom error to stop operation immediately and notify frontend
            raise TelegramRateLimitError(
                wait_seconds=e.seconds,
                message=f"Telegram rate limit during check. Please wait {e.seconds} seconds ({e.seconds // 60} minutes) before trying again."
            )
        except Exception as e:
            self.log(f"❌ Error checking seen-no-reply: {str(e)}", level="ERROR")
            return []

    def export_seen_no_reply_csv(self, results: List[Dict]) -> str:
        """
        Export seen-no-reply results to CSV file.
        
        Args:
            results: List of dicts from check_seen_no_reply()
        
        Returns:
            Path to the created CSV file
        """
        if not results:
            self.log("No results to export.")
            return ""
        
        # Generate filename with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        csv_filename = f"seen_no_reply_{timestamp}.csv"
        csv_path = Path(__file__).parent / csv_filename
        
        try:
            with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=['username', 'message_sent_date', 'last_seen_date'])
                writer.writeheader()
                
                for row in results:
                    writer.writerow({
                        'username': row['username'],
                        'message_sent_date': row['message_sent_date'],
                        'last_seen_date': row['last_seen_date']
                    })
            
            self.log(f"✅ Exported {len(results)} contacts to: {csv_filename}")
            return str(csv_path)
            
        except Exception as e:
            self.log(f"❌ Error exporting CSV: {str(e)}", level="ERROR")
            return ""

    def export_noreply_csv_by_type(self, results: List[Dict], hours: int, output_dir: Path = None) -> Dict[str, str]:
        """
        Export no-reply results to separate CSV files by type (DEV/KOL) and timeframe.
        
        Args:
            results: List of dicts from check_seen_no_reply()
            hours: Timeframe in hours (24, 48, or 168)
            output_dir: Output directory (defaults to logs/noreply/)
        
        Returns:
            Dict with file paths: {'dev': path, 'kol': path}
        """
        if output_dir is None:
            output_dir = LOGS_DIR / "noreply"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Determine timeframe label
        timeframe_map = {24: '24h', 48: '48h', 168: '7d'}
        timeframe = timeframe_map.get(hours, f'{hours}h')
        
        # Separate by type
        dev_results = [r for r in results if r.get('type') == 'dev']
        kol_results = [r for r in results if r.get('type') == 'kol']
        
        file_paths = {}
        
        # Export DEV CSV
        if dev_results:
            dev_file = output_dir / f'noreplyDEV_{timeframe}.csv'
            try:
                with open(dev_file, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=['username', 'display_name', 'message_sent_date', 'last_seen_date'])
                    writer.writeheader()
                    for row in dev_results:
                        writer.writerow({
                            'username': row['username'],
                            'display_name': row.get('display_name', ''),
                            'message_sent_date': row['message_sent_date'],
                            'last_seen_date': row['last_seen_date']
                        })
                file_paths['dev'] = str(dev_file)
                self.log(f"✅ Exported {len(dev_results)} DEV contacts to: noreplyDEV_{timeframe}.csv")
            except Exception as e:
                self.log(f"❌ Error exporting DEV CSV: {str(e)}", level="ERROR")
        
        # Export KOL CSV
        if kol_results:
            kol_file = output_dir / f'noreplyKOL_{timeframe}.csv'
            try:
                with open(kol_file, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=['username', 'display_name', 'message_sent_date', 'last_seen_date'])
                    writer.writeheader()
                    for row in kol_results:
                        writer.writerow({
                            'username': row['username'],
                            'display_name': row.get('display_name', ''),
                            'message_sent_date': row['message_sent_date'],
                            'last_seen_date': row['last_seen_date']
                        })
                file_paths['kol'] = str(kol_file)
                self.log(f"✅ Exported {len(kol_results)} KOL contacts to: noreplyKOL_{timeframe}.csv")
            except Exception as e:
                self.log(f"❌ Error exporting KOL CSV: {str(e)}", level="ERROR")
        
        return file_paths

    async def export_all_contacts_backup(self, output_dir: Path = None) -> Tuple[str, int]:
        """
        Export ALL contacts from Telegram as a backup CSV file.
        Includes full contact details: user_id, username, first_name, last_name, phone, etc.

        Args:
            output_dir: Output directory (defaults to logs/)

        Returns:
            Tuple of (path to created CSV file, contact count)
        """
        self.log("\n" + "="*70)
        self.log("💾 BACKING UP ALL TELEGRAM CONTACTS")
        self.log("="*70 + "\n")

        if output_dir is None:
            output_dir = LOGS_DIR

        try:
            # Fetch all contacts from Telegram (force refresh for explicit backup)
            self.log("Fetching all contacts from Telegram...")
            result = await self._contact_cache.get_contacts(self.client, phone=self.phone_number, force_refresh=True)

            if not result.users:
                self.log("⚠️  No contacts found to backup.")
                return ""

            # Generate filename with timestamp and phone number for per-account isolation
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            clean_phone = self.phone_number.replace('+', '').replace('-', '').replace(' ', '')

            # Create per-account backup directory
            backup_dir = LOGS_DIR / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)

            # Per-account timestamped file
            csv_filename = f"contacts_{clean_phone}_{timestamp}.csv"
            csv_path = backup_dir / csv_filename

            # Per-account "latest" file for stats endpoint
            latest_filename = f"contacts_{clean_phone}_latest.csv"
            latest_path = backup_dir / latest_filename

            # Prepare contact data
            contacts_data = []
            for user in result.users:
                contact_info = {
                    'user_id': user.id,
                    'username': user.username or '',
                    'first_name': user.first_name or '',
                    'last_name': user.last_name or '',
                    'phone': user.phone or '',
                    'is_bot': user.bot if hasattr(user, 'bot') else False,
                    'is_verified': user.verified if hasattr(user, 'verified') else False,
                    'is_premium': user.premium if hasattr(user, 'premium') else False,
                    'backup_date': timestamp,
                }
                contacts_data.append(contact_info)

            # Write to CSV
            fieldnames = ['user_id', 'username', 'first_name', 'last_name', 'phone',
                         'is_bot', 'is_verified', 'is_premium', 'backup_date']

            with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(contacts_data)

            # Also create/update "latest" file for stats endpoint
            import shutil
            shutil.copy2(csv_path, latest_path)

            self.log(f"✅ Successfully backed up {len(contacts_data)} contacts")
            self.log(f"📁 Backup saved to: {csv_filename}")
            self.log(f"📊 Updated latest backup: {latest_filename}")
            self.log(f"{'='*70}\n")

            return (str(csv_path), len(contacts_data))

        except Exception as e:
            self.log(f"❌ Error backing up contacts: {str(e)}", level="ERROR")
            return ("", 0)

    def distribute_contacts(self, contacts: List[Dict], accounts: List[str]) -> List[Tuple[str, List[Dict]]]:
        """
        Distribute contacts into equal chunks across accounts.
        
        Args:
            contacts: List of contact entries
            accounts: List of account phone numbers
        
        Returns:
            List of tuples: (account_phone, contact_chunk)
        """
        total_contacts = len(contacts)
        num_accounts = len(accounts)
        
        if num_accounts == 0:
            return []
        
        chunk_size = total_contacts // num_accounts
        remainder = total_contacts % num_accounts
        
        chunks = []
        start_idx = 0
        
        for i, account in enumerate(accounts):
            # Distribute remainder across first accounts
            size = chunk_size + (1 if i < remainder else 0)
            end_idx = start_idx + size
            chunks.append((account, contacts[start_idx:end_idx]))
            start_idx = end_idx
        
        return chunks

    def export_import_results_csv(self, results_dict: Dict[str, List[Dict]], output_path: str = None) -> str:
        """
        Export import results to CSV showing which usernames were added to which accounts.
        
        Args:
            results_dict: {account_phone: [{'username': str, 'status': str, ...}, ...]}
            output_path: Output file path (defaults to logs/import_results_TIMESTAMP.csv)
        
        Returns:
            Path to created CSV file
        """
        if output_path is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_path = str(LOGS_DIR / f"import_results_{timestamp}.csv")
        
        try:
            with open(output_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=['account_phone', 'account_name', 'username', 'status', 'timestamp'])
                writer.writeheader()
                
                for account_phone, contacts in results_dict.items():
                    # Get account name from database if available
                    account_name = account_phone
                    try:
                        from account_manager import get_account_by_phone
                        account = get_account_by_phone(account_phone)
                        if account and account.get('name'):
                            account_name = account['name']
                    except:
                        pass
                    
                    for contact in contacts:
                        writer.writerow({
                            'account_phone': account_phone,
                            'account_name': account_name,
                            'username': contact.get('username', ''),
                            'status': contact.get('status', 'unknown'),
                            'timestamp': contact.get('timestamp', datetime.now().isoformat())
                        })
            
            self.log(f"✅ Exported import results to: {output_path}")
            # Return relative path for frontend URL building (logs/filename.csv)
            return f"logs/{Path(output_path).name}"
        except Exception as e:
            self.log(f"❌ Error exporting import results: {str(e)}", level="ERROR")
            return ""

    async def import_dev_contacts_multi_account(self, csv_path: str, account_phones: List[str], 
                                                 dry_run: bool = False, interactive: bool = True) -> Dict[str, List[Dict]]:
        """
        Import developer contacts across multiple accounts with equal distribution.
        
        Args:
            csv_path: Path to CSV file
            account_phones: List of account phone numbers to import to
            dry_run: If True, preview only
            interactive: If True, prompt for confirmation
        
        Returns:
            Dict mapping account_phone to list of import results
        """
        self.log("\n" + "="*70)
        self.log("📥 MULTI-ACCOUNT DEV CONTACT IMPORT")
        self.log("="*70)
        
        # Read CSV
        try:
            with open(csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                required_columns = ['group_title', 'dex_chain', 'owner']
                
                if not reader.fieldnames:
                    self.log("❌ CSV file is empty", level="ERROR")
                    return {}
                
                missing_columns = [col for col in required_columns if col not in reader.fieldnames]
                if missing_columns:
                    self.log(f"❌ Missing required columns: {', '.join(missing_columns)}", level="ERROR")
                    return {}
                
                entries = []
                for row in reader:
                    if row.get('owner') and row['owner'].strip():
                        owner = row['owner'].strip().lstrip('@')
                        entries.append({
                            'group_title': row['group_title'].strip(),
                            'dex_chain': row['dex_chain'].strip(),
                            'owner': owner,
                        })
        except Exception as e:
            self.log(f"❌ Error reading CSV: {str(e)}", level="ERROR")
            return {}
        
        if not entries:
            self.log("❌ No valid entries found in CSV", level="ERROR")
            return {}
        
        # Distribute contacts across accounts
        account_chunks = self.distribute_contacts(entries, account_phones)
        
        self.log(f"\n📊 Distribution:")
        self.log(f"   Total contacts: {len(entries)}")
        self.log(f"   Accounts: {len(account_phones)}")
        for account_phone, chunk in account_chunks:
            self.log(f"   {account_phone}: {len(chunk)} contacts")
        
        if dry_run:
            self.log("\n🔍 DRY RUN MODE - No changes will be made\n")
        
        if not dry_run and interactive:
            confirm = input(f"\nProceed with multi-account import? (y/n): ").strip().lower()
            if confirm != 'y':
                self.log("⏹️  Operation cancelled")
                return {}
        
        # Import to each account
        all_results = {}
        
        for account_phone, chunk in account_chunks:
            self.log(f"\n{'='*70}")
            self.log(f"📱 Processing account: {account_phone} ({len(chunk)} contacts)")
            self.log(f"{'='*70}")
            
            # Create manager for this account
            from account_manager import get_account_by_phone
            account = get_account_by_phone(account_phone)
            
            if not account:
                self.log(f"❌ Account {account_phone} not found in database", level="ERROR")
                continue
            
            # Create temporary CSV for this chunk
            import tempfile
            temp_csv = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv', encoding='utf-8')
            temp_csv_path = temp_csv.name
            temp_csv.close()
            
            try:
                # Write chunk to temp CSV
                with open(temp_csv_path, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=['group_title', 'dex_chain', 'owner'])
                    writer.writeheader()
                    writer.writerows(chunk)
                
                # Create manager and import (use shared connection pool)
                account_manager = UnifiedContactManager(
                    api_id=account.get('api_id') or self.api_id,
                    api_hash=account.get('api_hash') or self.api_hash,
                    phone_number=account_phone,
                    conn_manager=GlobalConnectionManager.get_instance()
                )

                # Initialize client
                clean_phone = account_phone.replace('+', '').replace('-', '').replace(' ', '')
                if not await account_manager.init_client(account_phone, force_new=False):
                    self.log(f"❌ Failed to initialize client for {account_phone}", level="ERROR")
                    continue
                
                # Import contacts
                await account_manager.import_dev_contacts(temp_csv_path, dry_run=dry_run, interactive=False)
                
                # Collect results
                account_results = []
                for entry in chunk:
                    account_results.append({
                        'username': entry['owner'],
                        'status': 'added' if not dry_run else 'would_add',
                        'timestamp': datetime.now().isoformat()
                    })
                
                all_results[account_phone] = account_results
                
                await account_manager.close()
                
            except Exception as e:
                self.log(f"❌ Error processing account {account_phone}: {str(e)}", level="ERROR")
            finally:
                # Clean up temp file
                try:
                    Path(temp_csv_path).unlink()
                except:
                    pass
        
        # Export results CSV
        if all_results:
            self.export_import_results_csv(all_results)
        
        return all_results

    async def import_kol_contacts_multi_account(self, csv_path: str, account_phones: List[str],
                                                 dry_run: bool = False, interactive: bool = True) -> Dict[str, List[Dict]]:
        """
        Import KOL contacts across multiple accounts with equal distribution.
        Similar to import_dev_contacts_multi_account but for KOL contacts.
        """
        self.log("\n" + "="*70)
        self.log("📥 MULTI-ACCOUNT KOL CONTACT IMPORT")
        self.log("="*70)
        
        # Read CSV
        try:
            with open(csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                required_columns = ['Twitter Username', 'TG Usernames']
                
                if not reader.fieldnames:
                    self.log("❌ CSV file is empty", level="ERROR")
                    return {}
                
                missing_columns = [col for col in required_columns if col not in reader.fieldnames]
                if missing_columns:
                    self.log(f"❌ Missing required columns: {', '.join(missing_columns)}", level="ERROR")
                    return {}
                
                entries = []
                for row in reader:
                    if row.get('TG Usernames') and row['TG Usernames'].strip():
                        entries.append({
                            'twitter': row['Twitter Username'].strip().lstrip('@'),
                            'telegram': row['TG Usernames'].strip().lstrip('@')
                        })
        except Exception as e:
            self.log(f"❌ Error reading CSV: {str(e)}", level="ERROR")
            return {}
        
        if not entries:
            self.log("❌ No valid entries found in CSV", level="ERROR")
            return {}
        
        # Distribute contacts across accounts
        account_chunks = self.distribute_contacts(entries, account_phones)
        
        self.log(f"\n📊 Distribution:")
        self.log(f"   Total contacts: {len(entries)}")
        self.log(f"   Accounts: {len(account_phones)}")
        for account_phone, chunk in account_chunks:
            self.log(f"   {account_phone}: {len(chunk)} contacts")
        
        if dry_run:
            self.log("\n🔍 DRY RUN MODE - No changes will be made\n")
        
        if not dry_run and interactive:
            confirm = input(f"\nProceed with multi-account import? (y/n): ").strip().lower()
            if confirm != 'y':
                self.log("⏹️  Operation cancelled")
                return {}
        
        # Import to each account
        all_results = {}
        
        for account_phone, chunk in account_chunks:
            self.log(f"\n{'='*70}")
            self.log(f"📱 Processing account: {account_phone} ({len(chunk)} contacts)")
            self.log(f"{'='*70}")
            
            # Create manager for this account
            from account_manager import get_account_by_phone
            account = get_account_by_phone(account_phone)
            
            if not account:
                self.log(f"❌ Account {account_phone} not found in database", level="ERROR")
                continue
            
            # Create temporary CSV for this chunk
            import tempfile
            temp_csv = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv', encoding='utf-8')
            temp_csv_path = temp_csv.name
            temp_csv.close()
            
            try:
                # Write chunk to temp CSV
                with open(temp_csv_path, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=['Twitter Username', 'TG Usernames'])
                    writer.writeheader()
                    for entry in chunk:
                        writer.writerow({
                            'Twitter Username': entry['twitter'],
                            'TG Usernames': entry['telegram']
                        })
                
                # Create manager and import (use shared connection pool)
                account_manager = UnifiedContactManager(
                    api_id=account.get('api_id') or self.api_id,
                    api_hash=account.get('api_hash') or self.api_hash,
                    phone_number=account_phone,
                    conn_manager=GlobalConnectionManager.get_instance()
                )

                # Initialize client
                if not await account_manager.init_client(account_phone, force_new=False):
                    self.log(f"❌ Failed to initialize client for {account_phone}", level="ERROR")
                    continue
                
                # Import contacts
                await account_manager.import_kol_contacts(temp_csv_path, dry_run=dry_run, interactive=False)
                
                # Collect results
                account_results = []
                for entry in chunk:
                    account_results.append({
                        'username': entry['telegram'],
                        'status': 'added' if not dry_run else 'would_add',
                        'timestamp': datetime.now().isoformat()
                    })
                
                all_results[account_phone] = account_results
                
                await account_manager.close()
                
            except Exception as e:
                self.log(f"❌ Error processing account {account_phone}: {str(e)}", level="ERROR")
            finally:
                # Clean up temp file
                try:
                    Path(temp_csv_path).unlink()
                except:
                    pass
        
        # Export results CSV
        if all_results:
            self.export_import_results_csv(all_results)
        
        return all_results

    async def organize_folders(self, interactive: bool = True):
        """Organize contacts to folders using organize_combined.py"""
        self.log("\n" + "="*70)
        self.log("📁 FOLDER ORGANIZATION")
        self.log("="*70)

        try:
            # Import organize_combined
            import importlib.util
            spec = importlib.util.spec_from_file_location("organize_combined",
                                                          Path(__file__).parent / "organize_combined.py")
            organize_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(organize_module)

            # Call organize_combined with current authenticated client and interactive flag
            self.log("\nOrganizing contacts into 4 folders...\n")
            await organize_module.organize_combined(client=self.client, interactive=interactive)
            self.log(f"{'='*70}\n")

        except Exception as e:
            self.log(f"❌ Error organizing folders: {str(e)}", level="ERROR")
            self.log("\nYou can also run organize_combined.py separately to organize folders")

    async def show_statistics(self):
        """Show contact statistics"""
        self.log("\n" + "="*70)
        self.log("📊 CONTACT STATISTICS")
        self.log("="*70)

        try:
            result = await self._contact_cache.get_contacts(self.client, phone=self.phone_number)
            all_contacts = [u for u in result.users if u.first_name]

            blue_devs = [u for u in all_contacts if '🔵💻' in u.first_name]
            yellow_devs = [u for u in all_contacts if '🟡💻' in u.first_name]
            blue_kols = [u for u in all_contacts if '🔵📢' in u.first_name]
            yellow_kols = [u for u in all_contacts if '🟡📢' in u.first_name]

            self.log(f"\n📱 Total contacts: {len(all_contacts)}")
            self.log(f"\n💻 Developers: {len(blue_devs) + len(yellow_devs)}")
            self.log(f"   🔵 Blue (no reply): {len(blue_devs)}")
            self.log(f"   🟡 Yellow (replied): {len(yellow_devs)}")
            self.log(f"\n📢 KOLs: {len(blue_kols) + len(yellow_kols)}")
            self.log(f"   🔵 Blue (no reply): {len(blue_kols)}")
            self.log(f"   🟡 Yellow (replied): {len(yellow_kols)}")
            self.log(f"\n{'='*70}\n")

        except Exception as e:
            self.log(f"❌ Error getting statistics: {str(e)}", level="ERROR")

    def show_dashboard(self):
        """Show final dashboard summary"""
        self.log("\n" + "="*70)
        self.log("📊 UNIFIED MANAGER - DASHBOARD SUMMARY")
        self.log("="*70)

        self.log(f"\n💻 DEV CONTACTS:")
        self.log(f"   ✅ Added: {self.stats['dev_added']}")
        self.log(f"   ⏭️  Skipped: {self.stats['dev_skipped']}")
        self.log(f"   ❌ Failed: {self.stats['dev_failed']}")
        self.log(f"   📊 Total processed: {self.stats['dev_added'] + self.stats['dev_skipped'] + self.stats['dev_failed']}")

        self.log(f"\n📢 KOL CONTACTS:")
        self.log(f"   ✅ Added: {self.stats['kol_added']}")
        self.log(f"   ⏭️  Skipped: {self.stats['kol_skipped']}")
        self.log(f"   ❌ Failed: {self.stats['kol_failed']}")
        self.log(f"   📊 Total processed: {self.stats['kol_added'] + self.stats['kol_skipped'] + self.stats['kol_failed']}")

        self.log(f"\n📱 REPLY SCANNING:")
        self.log(f"   🔍 Contacts checked: {self.stats['contacts_checked']}")
        self.log(f"   🟡 Yellow replied (data): {self.stats['yellow_replied']}")

        self.log(f"\n🎨 STATUS UPDATES:")
        self.log(f"   🔵→🟡 Updated: {self.stats['contacts_updated']}")

        total_added = self.stats['dev_added'] + self.stats['kol_added']
        self.log(f"\n🎉 TOTAL ADDED: {total_added}")
        self.log(f"\n📁 Logs saved to: {LOGS_DIR}")
        self.log(f"{'='*70}\n")

    async def close(self):
        """Close Telegram connection"""
        if self.client:
            await self.client.disconnect()
            self.log("🔌 Disconnected from Telegram")




def get_manager() -> Optional[UnifiedContactManager]:
    """Get or initialize the manager instance (thread-safe)"""
    global manager
    with manager_lock:
        if manager is None:
            logger.info("📦 Initializing UnifiedContactManager...")
            try:
                # Get API credentials
                api_id, api_hash = get_api_credentials()

                # Get default account from database
                default_account = get_default_account()

                if not default_account:
                    logger.error("❌ No default account configured. Call POST /api/sessions/select first")
                    return None

                # Get phone number from default account
                default_phone = default_account.get('phone')
                if not default_phone:
                    logger.error("❌ Default account has no phone number")
                    return None

                # Get proxy if configured for default account
                default_proxy = default_account.get('proxy')

                # Add + prefix if not present
                if not default_phone.startswith('+'):
                    phone_number = f"+{default_phone}"
                else:
                    phone_number = default_phone

                # Create manager with phone number, proxy, and shared connection pool
                # Using GlobalConnectionManager prevents session file locking with inbox
                manager = UnifiedContactManager(
                    api_id=api_id,
                    api_hash=api_hash,
                    phone_number=phone_number,
                    proxy=default_proxy,
                    conn_manager=GlobalConnectionManager.get_instance()
                )
                logger.info("✅ Manager initialized with shared connection pool" + (f" (proxy: {default_proxy})" if default_proxy else ""))
            except Exception as e:
                logger.error(f"❌ Failed to initialize manager: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
                return None
        return manager


def get_manager_for_account(phone: str, use_shared_connection: bool = True) -> Optional[UnifiedContactManager]:
    """
    Create a manager instance for a specific account.

    This creates a NEW manager for the specified phone number, using
    either account-specific or global API credentials.

    Args:
        phone: Phone number (with or without + prefix)
        use_shared_connection: If True, uses GlobalConnectionManager for shared
                              client pool (prevents session file locking).
                              Set to False for authentication flows.

    Returns:
        UnifiedContactManager instance or None if account not found
    """
    try:
        # Clean phone number
        clean_phone = phone.replace('+', '').replace('-', '').replace(' ', '')

        # Get account from database
        account = get_account_by_phone(clean_phone)

        if not account:
            logger.error(f"❌ Account {clean_phone} not found in database")
            return None

        # Get API credentials (prefer account-specific, fall back to global)
        account_api_id = account.get('api_id')
        account_api_hash = account.get('api_hash')
        account_proxy = account.get('proxy')  # Get proxy if configured

        if account_api_id and account_api_hash:
            api_id = account_api_id
            api_hash = account_api_hash
            logger.info(f"📱 Using account-specific credentials for {clean_phone}")
        else:
            # Fall back to global credentials
            api_id, api_hash = get_api_credentials()
            if not api_id or not api_hash:
                logger.error(f"❌ No API credentials available for {clean_phone}")
                return None
            logger.info(f"📱 Using global credentials for {clean_phone}")

        # Format phone number with + prefix
        phone_number = f"+{clean_phone}"

        # Get GlobalConnectionManager singleton for shared client pool
        conn_manager = None
        if use_shared_connection:
            conn_manager = GlobalConnectionManager.get_instance()
            logger.info(f"🔗 Using shared connection pool for {clean_phone}")

        # Create manager for this specific account (with proxy and conn_manager)
        mgr = UnifiedContactManager(
            api_id=api_id,
            api_hash=api_hash,
            phone_number=phone_number,
            proxy=account_proxy,
            conn_manager=conn_manager
        )
        logger.info(f"✅ Created manager for account {clean_phone}" + (f" with proxy: {account_proxy}" if account_proxy else ""))

        return mgr

    except Exception as e:
        logger.error(f"❌ Failed to create manager for {phone}: {str(e)}")
        return None


# ============================================================================
# HEALTH & INFO ENDPOINTS
# ============================================================================

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    default_account = get_default_account()
    manager_status = 'initialized' if manager is not None else 'not_initialized'
    
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'version': '1.0.0',
        'manager_status': manager_status,
        'default_account': default_account.get('phone') if default_account else None,
        'has_default_account': default_account is not None
    })


@app.route('/api/status', methods=['GET'])
def get_status():
    """Get current operation status"""
    return jsonify(get_operation_state())


# ============================================================================
# CONFIGURATION ENDPOINTS
# ============================================================================

@app.route('/api/config', methods=['GET'])
def get_config():
    """Get current configuration (rate-limits, API settings)"""
    try:
        mgr = get_manager()
        if not mgr:
            return jsonify({'error': 'Manager not initialized'}), 500

        return jsonify({
            'api_id': mgr.api_id,
            'api_hash': mgr.api_hash[:10] + '...' if mgr.api_hash else None,  # Mask for security
            'phone': mgr.phone if hasattr(mgr, 'phone') else None,
            'rate_limit': {
                'batch_size_min': mgr.batch_size_min,
                'batch_size_max': mgr.batch_size_max,
                'delay_per_contact_min': mgr.delay_per_contact_min,
                'delay_per_contact_max': mgr.delay_per_contact_max,
                'batch_pause_min': mgr.batch_pause_min,
                'batch_pause_max': mgr.batch_pause_max,
            }
        })
    except Exception as e:
        logger.error(f"❌ Error getting config: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/config/rate-limit', methods=['POST'])
def update_rate_limit():
    """Update rate-limit configuration"""
    try:
        data = request.get_json()
        mgr = get_manager()
        if not mgr:
            return jsonify({'error': 'Manager not initialized'}), 500

        # Update rate-limit settings
        if 'batch_size_min' in data:
            mgr.batch_size_min = data['batch_size_min']
        if 'batch_size_max' in data:
            mgr.batch_size_max = data['batch_size_max']
        if 'delay_per_contact_min' in data:
            mgr.delay_per_contact_min = data['delay_per_contact_min']
        if 'delay_per_contact_max' in data:
            mgr.delay_per_contact_max = data['delay_per_contact_max']
        if 'batch_pause_min' in data:
            mgr.batch_pause_min = data['batch_pause_min']
        if 'batch_pause_max' in data:
            mgr.batch_pause_max = data['batch_pause_max']

        logger.info(f"✅ Rate-limit settings updated")
        return jsonify({
            'success': True,
            'message': 'Rate-limit settings updated',
            'config': {
                'batch_size_min': mgr.batch_size_min,
                'batch_size_max': mgr.batch_size_max,
                'delay_per_contact_min': mgr.delay_per_contact_min,
                'delay_per_contact_max': mgr.delay_per_contact_max,
                'batch_pause_min': mgr.batch_pause_min,
                'batch_pause_max': mgr.batch_pause_max,
            }
        })
    except Exception as e:
        logger.error(f"❌ Error updating rate-limit: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


# ============================================================================
# STATISTICS & DASHBOARD ENDPOINTS
# ============================================================================

def get_single_account_stats(phone):
    """Helper function to get stats for a single account.

    ONLY uses the per-account "latest" backup file to prevent cross-account data leakage.
    Returns zeros with has_backup=False if no backup exists for this account.

    Args:
        phone: Phone number (with or without + prefix)

    Returns:
        dict: Stats dictionary with has_backup flag (never None)
    """
    import os
    import csv

    try:
        clean_phone = phone.replace('+', '').replace('-', '').replace(' ', '')

        # ONLY look for this specific account's latest backup file
        # NO FALLBACK to database or global backups (prevents cross-account data leakage)
        backup_dir = LOGS_DIR / "backups"
        latest_backup_path = backup_dir / f"contacts_{clean_phone}_latest.csv"

        if not latest_backup_path.exists():
            # No backup for this account - return zeros with flag
            logger.debug(f"No backup found for {clean_phone} - returning zeros")
            return {
                'phone': clean_phone,
                'has_backup': False,
                'total_contacts': 0,
                'dev_contacts': {'total': 0, 'blue': 0, 'yellow': 0},
                'kol_contacts': {'total': 0, 'blue': 0, 'yellow': 0},
                'message': 'No backup yet - run backup to see contacts'
            }

        backup_path = str(latest_backup_path)
        logger.debug(f"Using backup: {latest_backup_path.name}")

        # Read and analyze the backup file
        stats = {
            'phone': clean_phone,
            'has_backup': True,
            'total_contacts': 0,
            'dev_contacts': {'total': 0, 'blue': 0, 'yellow': 0},
            'kol_contacts': {'total': 0, 'blue': 0, 'yellow': 0},
            'backup_file': os.path.basename(backup_path),
            'backup_date': datetime.fromtimestamp(os.path.getmtime(backup_path)).isoformat(),
        }

        with open(backup_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for contact in reader:
                first_name = contact.get('first_name', '')
                if first_name:
                    stats['total_contacts'] += 1

                    # Check for dev contacts
                    if '🔵💻' in first_name:
                        stats['dev_contacts']['total'] += 1
                        stats['dev_contacts']['blue'] += 1
                    elif '🟡💻' in first_name:
                        stats['dev_contacts']['total'] += 1
                        stats['dev_contacts']['yellow'] += 1

                    # Check for KOL contacts
                    if '🔵📢' in first_name:
                        stats['kol_contacts']['total'] += 1
                        stats['kol_contacts']['blue'] += 1
                    elif '🟡📢' in first_name:
                        stats['kol_contacts']['total'] += 1
                        stats['kol_contacts']['yellow'] += 1

        return stats

    except Exception as e:
        logger.error(f"Error getting stats for {phone}: {str(e)}")
        # Return zeros on error instead of None
        return {
            'phone': phone.replace('+', '').replace('-', '').replace(' ', ''),
            'has_backup': False,
            'total_contacts': 0,
            'dev_contacts': {'total': 0, 'blue': 0, 'yellow': 0},
            'kol_contacts': {'total': 0, 'blue': 0, 'yellow': 0},
            'error': str(e)
        }


@app.route('/api/stats', methods=['GET'])
def get_statistics():
    """Get contact statistics with accurate emoji-based counts

    Supports both single and multi-account queries:
    - ?phone=88807942561 - Single account (backward compatible)
    - ?phones=88807942561,12345678901 - Multiple accounts (aggregated)
    """
    phone = request.args.get('phone')      # Single phone (backward compatible)
    phones = request.args.get('phones')    # Multiple phones (comma-separated)

    try:
        # Find the most recent backup CSV file
        import os
        import csv
        import glob

        # Multi-account stats aggregation
        if phones:
            phone_list = [p.strip() for p in phones.split(',') if p.strip()]

            if not phone_list:
                return jsonify({'error': 'No valid phone numbers provided'}), 400

            # Aggregate stats from multiple accounts
            aggregated_stats = {
                'total_contacts': 0,
                'dev_contacts': {'total': 0, 'blue': 0, 'yellow': 0},
                'kol_contacts': {'total': 0, 'blue': 0, 'yellow': 0},
                'accounts': [],
                'account_count': len(phone_list),
                'timestamp': datetime.now().isoformat()
            }

            for phone_num in phone_list:
                # Get stats for this individual account (always returns dict, never None)
                account_stats = get_single_account_stats(phone_num)

                # Always add to aggregated totals (zeros if no backup)
                aggregated_stats['total_contacts'] += account_stats.get('total_contacts', 0)
                aggregated_stats['dev_contacts']['total'] += account_stats['dev_contacts'].get('total', 0)
                aggregated_stats['dev_contacts']['blue'] += account_stats['dev_contacts'].get('blue', 0)
                aggregated_stats['dev_contacts']['yellow'] += account_stats['dev_contacts'].get('yellow', 0)
                aggregated_stats['kol_contacts']['total'] += account_stats['kol_contacts'].get('total', 0)
                aggregated_stats['kol_contacts']['blue'] += account_stats['kol_contacts'].get('blue', 0)
                aggregated_stats['kol_contacts']['yellow'] += account_stats['kol_contacts'].get('yellow', 0)

                # Store per-account breakdown with has_backup flag
                aggregated_stats['accounts'].append({
                    'phone': phone_num,
                    'stats': account_stats,
                    'has_backup': account_stats.get('has_backup', False)
                })

            logger.info(f"📊 Multi-account stats: {len(phone_list)} accounts, {aggregated_stats['total_contacts']} total contacts")
            return jsonify(aggregated_stats)

        # Single account stats (backward compatible)
        if phone:
            # get_single_account_stats always returns a dict (never None)
            # It includes has_backup flag to indicate if backup exists
            stats = get_single_account_stats(phone)
            stats['timestamp'] = datetime.now().isoformat()
            logger.info(f"📊 Single account stats for {phone}: {stats['total_contacts']} total contacts (has_backup={stats.get('has_backup', False)})")
            return jsonify(stats)
        else:
            # Phone parameter is required - no global fallback
            # This prevents accidentally showing wrong account's stats
            return jsonify({
                'error': 'Phone parameter required',
                'hint': 'Use ?phone=X for single account or ?phones=X,Y for multiple accounts',
                'total_contacts': 0,
                'dev_contacts': {'total': 0, 'blue': 0, 'yellow': 0},
                'kol_contacts': {'total': 0, 'blue': 0, 'yellow': 0},
            }), 400

    except Exception as e:
        logger.error(f"❌ Error getting statistics: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/contacts', methods=['GET'])
def get_contacts():
    """Get all contacts from the latest backup file

    Query params:
    - phone: Account phone number (optional, uses default if not provided)
    - type: Filter by type - 'all', 'dev', 'kol' (default: 'all')
    - status: Filter by status - 'all', 'blue', 'yellow' (default: 'all')
    - search: Search query for name/username (optional)
    - limit: Max contacts to return (default: 100)
    - offset: Pagination offset (default: 0)
    """
    try:
        phone = request.args.get('phone')
        contact_type = request.args.get('type', 'all')
        status = request.args.get('status', 'all')
        search = request.args.get('search', '').lower()
        limit = request.args.get('limit', 100, type=int)
        offset = request.args.get('offset', 0, type=int)

        # Find the backup file
        log_dir = Path(__file__).parent.parent / "logs"
        latest_backup = None

        if phone:
            # Look for account-specific backup
            clean_phone = normalize_phone(phone)
            conn = get_db_connection()
            cursor = conn.cursor()

            # Try different phone formats
            for phone_format in [clean_phone, f'+{clean_phone}']:
                cursor.execute(
                    'SELECT filepath FROM backups WHERE phone = ? ORDER BY created_at DESC LIMIT 1',
                    (phone_format,)
                )
                row = cursor.fetchone()
                if row:
                    latest_backup = row['filepath']
                    break
            conn.close()

        if not latest_backup:
            # Fall back to most recent backup file
            backup_files = list(log_dir.glob("contacts_backup_*.csv"))
            if backup_files:
                latest_backup = str(max(backup_files, key=os.path.getctime))

        if not latest_backup or not os.path.exists(latest_backup):
            return jsonify({
                'contacts': [],
                'total': 0,
                'message': 'No backup file found. Please create a backup first.'
            })

        # Read contacts from backup
        contacts = []
        with open(latest_backup, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                first_name = row.get('first_name', '')
                last_name = row.get('last_name', '')
                username = row.get('username', '')

                # Determine type and status from emoji
                c_type = None
                c_status = None
                display_name = first_name

                if '💻' in first_name:
                    c_type = 'dev'
                    if '🔵' in first_name:
                        c_status = 'blue'
                    elif '🟡' in first_name:
                        c_status = 'yellow'
                elif '📢' in first_name:
                    c_type = 'kol'
                    if '🔵' in first_name:
                        c_status = 'blue'
                    elif '🟡' in first_name:
                        c_status = 'yellow'

                # Apply filters
                if contact_type != 'all' and c_type != contact_type:
                    continue
                if status != 'all' and c_status != status:
                    continue
                if search and search not in first_name.lower() and search not in (username or '').lower():
                    continue

                # Clean display name (remove emoji prefixes for display)
                clean_name = first_name
                for emoji in ['🔵💻', '🟡💻', '🔵📢', '🟡📢', '🔵', '🟡', '💻', '📢']:
                    clean_name = clean_name.replace(emoji, '').strip()

                contacts.append({
                    'id': len(contacts) + 1,
                    'name': clean_name or username or 'Unknown',
                    'full_name': f"{first_name} {last_name}".strip(),
                    'username': username,
                    'type': c_type,
                    'status': c_status,
                    'details': row.get('phone', '') or username or ''
                })

        total = len(contacts)
        # Apply pagination
        paginated = contacts[offset:offset + limit]

        logger.info(f"📋 Retrieved {len(paginated)}/{total} contacts")
        return jsonify({
            'contacts': paginated,
            'total': total,
            'limit': limit,
            'offset': offset,
            'backup_file': os.path.basename(latest_backup)
        })

    except Exception as e:
        logger.error(f"❌ Error getting contacts: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


# ============================================================================
# CONTACT IMPORT ENDPOINTS
# ============================================================================

@app.route('/api/import/devs', methods=['POST'])
def import_devs():
    """Import dev contacts from CSV with real-time WebSocket progress updates.

    Supports specifying which account to use via 'phone' parameter.
    If 'phone' is not provided, uses the default account.

    Returns immediately with operation_id. Subscribe to WebSocket for progress updates.

    WebSocket Events:
    - 'operation_progress': Real-time progress for each contact
    - 'operation_log': Log messages
    - 'operation_complete': Final results when done
    """
    try:
        data = request.get_json()
        csv_path = data.get('csv_path')
        dry_run = data.get('dry_run', False)
        phone = data.get('phone')  # Optional: specific account to use

        if not csv_path:
            return jsonify({'error': 'csv_path required'}), 400

        # Get manager for specific account or default
        # IMPORTANT: use_shared_connection=False because Flask endpoints run in request threads
        # with their own event loops, which are different from InboxManager's background loop.
        if phone:
            mgr = get_manager_for_account(phone, use_shared_connection=False)
            if not mgr:
                return jsonify({'error': f'Account {phone} not found or not configured'}), 404
        else:
            mgr = get_manager()
            if not mgr:
                return jsonify({'error': 'Manager not initialized. No default account configured.'}), 500
            mgr._conn_manager = None  # Clear to avoid loop conflicts

        account_phone = normalize_phone(mgr.phone_number)

        # Create operation for WebSocket tracking
        operation_id = create_operation('import_devs', [account_phone], {
            'csv_path': csv_path,
            'dry_run': dry_run
        })

        # Progress callback that emits via WebSocket
        def progress_callback(processed, total, message, contact_info=None):
            status = 'running'
            stats = None
            if contact_info:
                contact_status = contact_info.get('status', '')
                if contact_status in ['added', 'skipped', 'failed']:
                    status = 'running'
                # Extract stats for real-time frontend updates
                stats = contact_info.get('stats')
            update_account_progress(operation_id, account_phone, processed, total, status, message, stats=stats)
            if contact_info:
                add_account_log(operation_id, account_phone, message,
                               level='success' if contact_info.get('status') == 'added' else 'info')

        # Run import in background thread
        def run_import():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # Disconnect from GlobalConnectionManager to release session file lock
                clean_phone = normalize_phone(mgr.phone_number)
                global_conn_manager = GlobalConnectionManager.get_instance()
                if global_conn_manager.is_connected(clean_phone):
                    logger.info(f"Disconnecting {clean_phone} from GlobalConnectionManager...")
                    try:
                        loop.run_until_complete(global_conn_manager.disconnect_account(clean_phone))
                    except Exception as e:
                        logger.warning(f"⚠️  Error disconnecting from GlobalConnectionManager: {e}")

                # Initialize Telegram client BEFORE importing
                connected = loop.run_until_complete(mgr.init_client(mgr.phone_number))
                if not connected:
                    logger.error(f"❌ Failed to connect to Telegram for {account_phone}")
                    complete_operation(operation_id, error="Failed to connect to Telegram. Please check your session.")
                    return

                result = loop.run_until_complete(
                    mgr.import_dev_contacts(
                        csv_path,
                        dry_run=dry_run,
                        interactive=False,
                        operation_id=operation_id,
                        progress_callback=progress_callback
                    )
                )
                # Complete the operation
                complete_operation(operation_id, results={
                    'added': mgr.stats.get('dev_added', 0),
                    'skipped': mgr.stats.get('dev_skipped', 0),
                    'failed': mgr.stats.get('dev_failed', 0),
                    'dry_run': dry_run
                })
                logger.info(f"✅ Dev import completed for {account_phone}: {result}")

                # Auto-backup after import (only if not dry_run and contacts were added)
                if not dry_run and mgr.stats.get('dev_added', 0) > 0:
                    try:
                        logger.info(f"📦 Auto-backing up contacts after import...")
                        backup_result = loop.run_until_complete(mgr.export_all_contacts_backup())
                        logger.info(f"✅ Auto-backup completed: {backup_result}")
                    except Exception as backup_error:
                        logger.warning(f"⚠️ Auto-backup failed: {backup_error}")
            except Exception as e:
                logger.error(f"❌ Error in import thread: {str(e)}")
                complete_operation(operation_id, error=str(e))
            finally:
                # Disconnect client to release session file lock
                try:
                    if mgr.client:
                        disconnect_coro = mgr.client.disconnect()
                        if asyncio.iscoroutine(disconnect_coro):
                            loop.run_until_complete(disconnect_coro)
                except Exception as disconnect_error:
                    logger.warning(f"⚠️ Error disconnecting client: {disconnect_error}")
                finally:
                    loop.close()

        # Start background thread
        import_thread = threading.Thread(target=run_import, daemon=True)
        import_thread.start()

        # Return immediately with operation_id
        return jsonify({
            'success': True,
            'operation_id': operation_id,
            'operation': 'import_devs',
            'phone': account_phone,
            'dry_run': dry_run,
            'message': 'Import started. Subscribe to WebSocket for real-time progress.'
        })

    except Exception as e:
        logger.error(f"❌ Error starting import: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/import/kols', methods=['POST'])
def import_kols():
    """Import KOL contacts from CSV with real-time WebSocket progress updates.

    Supports specifying which account to use via 'phone' parameter.
    If 'phone' is not provided, uses the default account.

    Returns immediately with operation_id. Subscribe to WebSocket for progress updates.

    WebSocket Events:
    - 'operation_progress': Real-time progress for each contact
    - 'operation_log': Log messages
    - 'operation_complete': Final results when done
    """
    try:
        data = request.get_json()
        csv_path = data.get('csv_path')
        dry_run = data.get('dry_run', False)
        phone = data.get('phone')  # Optional: specific account to use

        if not csv_path:
            return jsonify({'error': 'csv_path required'}), 400

        # Get manager for specific account or default
        # IMPORTANT: use_shared_connection=False because Flask endpoints run in request threads
        # with their own event loops, which are different from InboxManager's background loop.
        if phone:
            mgr = get_manager_for_account(phone, use_shared_connection=False)
            if not mgr:
                return jsonify({'error': f'Account {phone} not found or not configured'}), 404
        else:
            mgr = get_manager()
            if not mgr:
                return jsonify({'error': 'Manager not initialized. No default account configured.'}), 500
            mgr._conn_manager = None  # Clear to avoid loop conflicts

        account_phone = normalize_phone(mgr.phone_number)

        # Create operation for WebSocket tracking
        operation_id = create_operation('import_kols', [account_phone], {
            'csv_path': csv_path,
            'dry_run': dry_run
        })

        # Progress callback that emits via WebSocket
        def progress_callback(processed, total, message, contact_info=None):
            status = 'running'
            stats = None
            if contact_info:
                contact_status = contact_info.get('status', '')
                if contact_status in ['added', 'skipped', 'failed']:
                    status = 'running'
                # Extract stats for real-time frontend updates
                stats = contact_info.get('stats')
            update_account_progress(operation_id, account_phone, processed, total, status, message, stats=stats)
            if contact_info:
                add_account_log(operation_id, account_phone, message,
                               level='success' if contact_info.get('status') == 'added' else 'info')

        # Run import in background thread
        def run_import():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # Disconnect from GlobalConnectionManager to release session file lock
                clean_phone = normalize_phone(mgr.phone_number)
                global_conn_manager = GlobalConnectionManager.get_instance()
                if global_conn_manager.is_connected(clean_phone):
                    logger.info(f"Disconnecting {clean_phone} from GlobalConnectionManager...")
                    try:
                        loop.run_until_complete(global_conn_manager.disconnect_account(clean_phone))
                    except Exception as e:
                        logger.warning(f"⚠️  Error disconnecting from GlobalConnectionManager: {e}")

                # Initialize Telegram client BEFORE importing
                connected = loop.run_until_complete(mgr.init_client(mgr.phone_number))
                if not connected:
                    logger.error(f"❌ Failed to connect to Telegram for {account_phone}")
                    complete_operation(operation_id, error="Failed to connect to Telegram. Please check your session.")
                    return

                result = loop.run_until_complete(
                    mgr.import_kol_contacts(
                        csv_path,
                        dry_run=dry_run,
                        interactive=False,
                        operation_id=operation_id,
                        progress_callback=progress_callback
                    )
                )
                # Complete the operation
                complete_operation(operation_id, results={
                    'added': mgr.stats.get('kol_added', 0),
                    'skipped': mgr.stats.get('kol_skipped', 0),
                    'failed': mgr.stats.get('kol_failed', 0),
                    'dry_run': dry_run
                })
                logger.info(f"✅ KOL import completed for {account_phone}: {result}")

                # Auto-backup after import (only if not dry_run and contacts were added)
                if not dry_run and mgr.stats.get('kol_added', 0) > 0:
                    try:
                        logger.info(f"📦 Auto-backing up contacts after import...")
                        backup_result = loop.run_until_complete(mgr.export_all_contacts_backup())
                        logger.info(f"✅ Auto-backup completed: {backup_result}")
                    except Exception as backup_error:
                        logger.warning(f"⚠️ Auto-backup failed: {backup_error}")
            except Exception as e:
                logger.error(f"❌ Error in KOL import thread: {str(e)}")
                complete_operation(operation_id, error=str(e))
            finally:
                # Disconnect client to release session file lock
                try:
                    if mgr.client:
                        disconnect_coro = mgr.client.disconnect()
                        if asyncio.iscoroutine(disconnect_coro):
                            loop.run_until_complete(disconnect_coro)
                except Exception as disconnect_error:
                    logger.warning(f"⚠️ Error disconnecting client: {disconnect_error}")
                finally:
                    loop.close()

        # Start background thread
        import_thread = threading.Thread(target=run_import, daemon=True)
        import_thread.start()

        # Return immediately with operation_id
        return jsonify({
            'success': True,
            'operation_id': operation_id,
            'operation': 'import_kols',
            'phone': account_phone,
            'dry_run': dry_run,
            'message': 'Import started. Subscribe to WebSocket for real-time progress.'
        })

    except Exception as e:
        logger.error(f"❌ Error starting KOL import: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/import/devs/multi', methods=['POST'])
def import_devs_multi():
    """Import dev contacts across multiple accounts"""
    try:
        data = request.get_json()
        csv_path = data.get('csv_path')
        account_phones = data.get('account_phones', [])
        dry_run = data.get('dry_run', False)

        if not csv_path:
            return jsonify({'error': 'csv_path required'}), 400
        if not account_phones:
            return jsonify({'error': 'account_phones array required'}), 400

        update_operation_state('import_devs_multi', 0, 0, 'starting', f'Importing to {len(account_phones)} accounts...')

        # Get API credentials
        api_id, api_hash = get_api_credentials()
        if not api_id or not api_hash:
            return jsonify({'error': 'API credentials not configured'}), 500

        # Run async multi-account import
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Create a manager with API credentials and shared connection pool
            # The multi-account method will get account-specific credentials from database
            temp_mgr = UnifiedContactManager(
                api_id, api_hash, account_phones[0] if account_phones else '',
                conn_manager=GlobalConnectionManager.get_instance()
            )
            results = loop.run_until_complete(
                temp_mgr.import_dev_contacts_multi_account(csv_path, account_phones, dry_run=dry_run, interactive=False)
            )
            
            # Export results CSV
            import_results_csv = None
            if results:
                import_results_csv = temp_mgr.export_import_results_csv(results)
            
            # Clean up
            try:
                loop.run_until_complete(temp_mgr.close())
            except:
                pass

            reset_operation_state()
            logger.info(f"✅ Multi-account dev import completed for {len(account_phones)} accounts")
            return jsonify({
                'success': True,
                'operation': 'import_devs_multi',
                'dry_run': dry_run,
                'results': results,
                'import_results_csv': import_results_csv
            })
        finally:
            loop.close()
    except Exception as e:
        logger.error(f"❌ Error importing devs to multiple accounts: {str(e)}")
        reset_operation_state()
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/import/kols/multi', methods=['POST'])
def import_kols_multi():
    """Import KOL contacts across multiple accounts"""
    try:
        data = request.get_json()
        csv_path = data.get('csv_path')
        account_phones = data.get('account_phones', [])
        dry_run = data.get('dry_run', False)

        if not csv_path:
            return jsonify({'error': 'csv_path required'}), 400
        if not account_phones:
            return jsonify({'error': 'account_phones array required'}), 400

        update_operation_state('import_kols_multi', 0, 0, 'starting', f'Importing to {len(account_phones)} accounts...')

        # Get API credentials
        api_id, api_hash = get_api_credentials()
        if not api_id or not api_hash:
            return jsonify({'error': 'API credentials not configured'}), 500

        # Run async multi-account import
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Create a manager with API credentials and shared connection pool
            # The multi-account method will get account-specific credentials from database
            temp_mgr = UnifiedContactManager(
                api_id, api_hash, account_phones[0] if account_phones else '',
                conn_manager=GlobalConnectionManager.get_instance()
            )
            results = loop.run_until_complete(
                temp_mgr.import_kol_contacts_multi_account(csv_path, account_phones, dry_run=dry_run, interactive=False)
            )
            
            # Export results CSV
            import_results_csv = None
            if results:
                import_results_csv = temp_mgr.export_import_results_csv(results)
            
            # Clean up
            try:
                loop.run_until_complete(temp_mgr.close())
            except:
                pass

            reset_operation_state()
            logger.info(f"✅ Multi-account KOL import completed for {len(account_phones)} accounts")
            return jsonify({
                'success': True,
                'operation': 'import_kols_multi',
                'dry_run': dry_run,
                'results': results,
                'import_results_csv': import_results_csv
            })
        finally:
            loop.close()
    except Exception as e:
        logger.error(f"❌ Error importing KOLs to multiple accounts: {str(e)}")
        reset_operation_state()
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


# ============================================================================
# OPERATION ENDPOINTS
# ============================================================================


# ============================================================================
# UNIFIED MULTI-ACCOUNT OPERATION ENDPOINT
# ============================================================================

@app.route('/api/operations/start', methods=['POST'])
def start_multi_account_operation():
    """
    Unified endpoint for starting multi-account operations.

    This endpoint starts operations that run in parallel across multiple accounts,
    with real-time progress updates via WebSocket.

    Request Body:
    {
        "operation": "scan" | "backup" | "folders" | "import_devs" | "import_kols",
        "phones": ["88807942561", "12345678901"],  // List of account phone numbers
        "params": {  // Optional parameters specific to each operation
            "dialog_limit": 100,  // For scan
            "hours": 48,          // For scan
            "csv_path": "...",    // For import operations
            "dry_run": false      // For import operations
        }
    }

    Response:
    {
        "success": true,
        "operation_id": "abc12345",
        "message": "Operation started. Subscribe to WebSocket for updates."
    }

    WebSocket Events (subscribe with operation_id):
    - operation_progress: Per-account progress updates
    - operation_log: Per-account log messages
    - operation_complete: Final results when operation finishes
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Request body required'}), 400

        operation = data.get('operation')
        phones = data.get('phones', [])
        params = data.get('params', {})

        # Validate operation type
        valid_operations = ['scan', 'backup', 'folders', 'import_devs', 'import_kols']
        if operation not in valid_operations:
            return jsonify({
                'error': f"Invalid operation '{operation}'. Valid: {valid_operations}"
            }), 400

        # Validate phones
        if not phones or not isinstance(phones, list):
            return jsonify({'error': 'phones must be a non-empty list'}), 400

        # Normalize all phone numbers
        phones = [normalize_phone(p) for p in phones]

        # Check for locked accounts
        locked = []
        for phone in phones:
            if account_locks.is_locked(phone):
                locked.append(phone)

        if locked:
            return jsonify({
                'error': f"Accounts already running operations: {locked}",
                'locked_accounts': locked
            }), 409

        # Create operation record
        operation_id = create_operation(operation, phones, params)

        # Start background execution
        def run_operation():
            try:
                _execute_multi_account_operation(operation_id, operation, phones, params)
            except Exception as e:
                logger.error(f"❌ Operation {operation_id} failed: {e}")
                complete_operation(operation_id, error=str(e))

        operation_executor.submit(run_operation)

        return jsonify({
            'success': True,
            'operation_id': operation_id,
            'phones': phones,
            'operation': operation,
            'message': 'Operation started. Subscribe to WebSocket for updates.'
        })

    except Exception as e:
        logger.error(f"❌ Error starting operation: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/operations/<operation_id>', methods=['GET'])
def get_operation_status(operation_id):
    """Get the current status of an operation"""
    op = get_operation(operation_id)
    if not op:
        return jsonify({'error': f'Operation {operation_id} not found'}), 404
    return jsonify(op)


@app.route('/api/operations/<operation_id>/cancel', methods=['POST'])
def cancel_operation(operation_id):
    """Cancel a running operation (best effort)"""
    with operations_lock:
        if operation_id not in active_operations:
            return jsonify({'error': f'Operation {operation_id} not found'}), 404

        op = active_operations[operation_id]
        if op['status'] in ['completed', 'error', 'cancelled']:
            return jsonify({'error': f'Operation already {op["status"]}'}), 400

        op['status'] = 'cancelled'

        # Release all account locks
        for phone in op['phones']:
            account_locks.release(phone)

    logger.info(f"🛑 Operation {operation_id} marked for cancellation")
    return jsonify({'success': True, 'message': 'Operation cancellation requested'})


@app.route('/api/operations/active', methods=['GET'])
def get_active_operations_endpoint():
    """
    Get list of all active (pending/running) operations.
    Combines in-memory operations with database for reconnection scenarios.
    """
    # Get from memory first
    with operations_lock:
        memory_ops = {op['id']: op for op in active_operations.values()
                      if op['status'] in ['pending', 'running']}

    # Also check database for any running operations not in memory
    try:
        db_ops = db_get_active_operations()
        for op in db_ops:
            if op['id'] not in memory_ops:
                memory_ops[op['id']] = op
                # Restore to memory for tracking
                with operations_lock:
                    if op['id'] not in active_operations:
                        active_operations[op['id']] = op
    except Exception as e:
        logger.warning(f"⚠️ Failed to get active ops from DB: {e}")

    return jsonify({'operations': list(memory_ops.values())})


@app.route('/api/operations/history', methods=['GET'])
def get_operations_history():
    """
    Get recent operations history from database.
    Query params:
        - limit: Number of operations to return (default 20, max 100)
    """
    try:
        limit = min(int(request.args.get('limit', 20)), 100)
        operations = db_get_recent_operations(limit)
        return jsonify({'operations': operations})
    except Exception as e:
        logger.error(f"❌ Failed to get operations history: {e}")
        return jsonify({'error': str(e)}), 500


def _execute_multi_account_operation(operation_id: str, operation: str, phones: List[str], params: Dict):
    """
    Execute an operation across multiple accounts in parallel.
    Runs in a background thread.
    """
    results = {}

    def run_for_account(phone: str):
        """Run operation for a single account"""
        try:
            # Acquire lock
            if not account_locks.acquire(phone, blocking=True, timeout=60):
                add_account_log(operation_id, phone, "Failed to acquire lock (timeout)", "error")
                update_account_progress(operation_id, phone, 0, 0, 'error', error="Lock timeout")
                return None

            add_account_log(operation_id, phone, f"Starting {operation} operation", "info")
            update_account_progress(operation_id, phone, 0, 100, 'running', 'Initializing...')

            # Check if cancelled
            op = get_operation(operation_id)
            if op and op.get('status') == 'cancelled':
                account_locks.release(phone)
                return None

            # Get manager for this account
            # IMPORTANT: use_shared_connection=False because this runs in a worker thread
            # with its own event loop, different from InboxManager's background loop.
            mgr = get_manager_for_account(phone, use_shared_connection=False)
            if not mgr:
                add_account_log(operation_id, phone, f"Account not found", "error")
                update_account_progress(operation_id, phone, 0, 0, 'error', error="Account not found")
                account_locks.release(phone)
                return None

            # Create event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                # Disconnect from GlobalConnectionManager to release session file lock
                clean_phone = normalize_phone(phone)
                global_conn_manager = GlobalConnectionManager.get_instance()
                if global_conn_manager.is_connected(clean_phone):
                    add_account_log(operation_id, phone, "Releasing session lock...", "info")
                    try:
                        loop.run_until_complete(global_conn_manager.disconnect_account(clean_phone))
                    except Exception as e:
                        logger.warning(f"⚠️  Error disconnecting from GlobalConnectionManager: {e}")

                # Initialize client
                add_account_log(operation_id, phone, "Connecting to Telegram...", "info")
                connected = loop.run_until_complete(mgr.init_client(phone))
                if not connected:
                    raise Exception("Failed to connect to Telegram")

                update_account_progress(operation_id, phone, 10, 100, 'running', 'Connected')

                # Execute the operation
                if operation == 'scan':
                    result = _run_scan_operation(loop, mgr, phone, operation_id, params)
                elif operation == 'backup':
                    result = _run_backup_operation(loop, mgr, phone, operation_id, params)
                elif operation == 'folders':
                    result = _run_folders_operation(loop, mgr, phone, operation_id, params)
                elif operation == 'import_devs':
                    result = _run_import_operation(loop, mgr, phone, operation_id, params, 'devs')
                elif operation == 'import_kols':
                    result = _run_import_operation(loop, mgr, phone, operation_id, params, 'kols')
                else:
                    raise Exception(f"Unknown operation: {operation}")

                update_account_progress(operation_id, phone, 100, 100, 'completed', 'Done')
                add_account_log(operation_id, phone, f"Operation completed successfully", "success")
                return result

            except Exception as e:
                add_account_log(operation_id, phone, f"Error: {str(e)}", "error")
                update_account_progress(operation_id, phone, 0, 0, 'error', error=str(e))
                return {'error': str(e)}
            finally:
                # Disconnect client
                if mgr.client and mgr.client.is_connected():
                    try:
                        loop.run_until_complete(mgr.client.disconnect())
                    except:
                        pass
                loop.close()
                account_locks.release(phone)

        except Exception as e:
            add_account_log(operation_id, phone, f"Fatal error: {str(e)}", "error")
            update_account_progress(operation_id, phone, 0, 0, 'error', error=str(e))
            account_locks.release(phone)
            return {'error': str(e)}

    # Run operations SEQUENTIALLY (one account completes before next starts)
    # This reduces Telegram rate limit risk compared to parallel execution
    for phone in phones:
        add_account_log(operation_id, phone, f"Starting operation for account...", "info")
        try:
            result = run_for_account(phone)
            results[phone] = result
        except Exception as e:
            results[phone] = {'error': str(e)}
            add_account_log(operation_id, phone, f"Account failed: {str(e)}", "error")

    # Complete operation
    complete_operation(operation_id, results)


def _run_scan_operation(loop, mgr, phone: str, operation_id: str, params: Dict) -> Dict:
    """Run scan for replies operation"""
    dialog_limit = params.get('dialog_limit', 100)
    hours = params.get('hours', 48)
    export_csv = params.get('export_csv', True)

    add_account_log(operation_id, phone, f"Checking seen-no-reply (last {hours}h)...", "info")
    update_account_progress(operation_id, phone, 30, 100, 'running', 'Checking seen-no-reply...')

    seen_no_reply = loop.run_until_complete(
        mgr.check_seen_no_reply(hours=hours, dialog_limit=dialog_limit, export_csv=export_csv)
    )

    add_account_log(operation_id, phone, f"Scanning for replies...", "info")
    update_account_progress(operation_id, phone, 60, 100, 'running', 'Scanning for replies...')

    reply_statuses = loop.run_until_complete(
        mgr.scan_for_replies(dialog_limit=dialog_limit, hours=hours)
    )

    return {
        'seen_no_reply': seen_no_reply or [],
        'reply_statuses': reply_statuses or [],
        'reply_count': len(reply_statuses) if reply_statuses else 0
    }


def _run_backup_operation(loop, mgr, phone: str, operation_id: str, params: Dict) -> Dict:
    """Run backup contacts operation"""
    add_account_log(operation_id, phone, "Backing up contacts...", "info")
    update_account_progress(operation_id, phone, 30, 100, 'running', 'Backing up contacts...')

    result = loop.run_until_complete(mgr.backup_contacts())

    return {
        'backed_up': result.get('backed_up', 0) if result else 0,
        'csv_path': result.get('csv_path') if result else None
    }


def _run_folders_operation(loop, mgr, phone: str, operation_id: str, params: Dict) -> Dict:
    """Run organize folders operation"""
    add_account_log(operation_id, phone, "Organizing folders...", "info")
    update_account_progress(operation_id, phone, 30, 100, 'running', 'Organizing folders...')

    result = loop.run_until_complete(mgr.organize_folders())

    return {'organized': True, 'result': result}


def _run_import_operation(loop, mgr, phone: str, operation_id: str, params: Dict, contact_type: str) -> Dict:
    """Run import contacts operation"""
    csv_path = params.get('csv_path')
    dry_run = params.get('dry_run', False)
    contacts = params.get('contacts', [])

    if not csv_path and not contacts:
        raise Exception("csv_path or contacts required for import")

    add_account_log(operation_id, phone, f"Importing {contact_type}...", "info")
    update_account_progress(operation_id, phone, 20, 100, 'running', f'Importing {contact_type}...')

    if contact_type == 'devs':
        result = loop.run_until_complete(
            mgr.import_dev_contacts(csv_path=csv_path, dry_run=dry_run)
        )
    else:
        result = loop.run_until_complete(
            mgr.import_kol_contacts(csv_path=csv_path, dry_run=dry_run)
        )

    return {
        'imported': result.get('added_count', 0) if result else 0,
        'skipped': result.get('skipped_count', 0) if result else 0,
        'failed': result.get('failed_count', 0) if result else 0
    }


@app.route('/api/scan-replies', methods=['POST'])
def scan_replies():
    """Scan for replied contacts and auto-update status

    Supports specifying which account to use via 'phone' parameter.
    If 'phone' is not provided, uses the default account.
    """
    try:
        data = request.get_json() if request.get_json() else {}
        dialog_limit = data.get('dialog_limit', 100)
        hours = data.get('hours', 48)  # 24, 48, or 168 (7 days)
        export_csv = data.get('export_csv', True)
        phone = data.get('phone')  # Optional: specific account to use

        # Get manager for specific account or default
        # IMPORTANT: use_shared_connection=False because Flask endpoints run in request threads
        # with their own event loops, which are different from InboxManager's background loop.
        if phone:
            mgr = get_manager_for_account(phone, use_shared_connection=False)
            if not mgr:
                return jsonify({'error': f'Account {phone} not found or not configured'}), 404
        else:
            mgr = get_manager()
            if not mgr:
                return jsonify({'error': 'Manager not initialized. No default account configured.'}), 500
            mgr._conn_manager = None  # Clear to avoid loop conflicts

        # Get the phone number for locking
        account_phone = mgr.phone_number

        # Check if account is already locked (another operation in progress)
        if account_locks.is_locked(account_phone):
            logger.warning(f"Account {account_phone} is busy with another operation")
            return jsonify({
                'error': f'Account is busy with another operation. Please wait.',
                'error_type': 'account_busy'
            }), 409

        # Try to acquire lock with a short timeout (non-blocking check)
        if not account_locks.acquire(account_phone, blocking=False):
            logger.warning(f"Failed to acquire lock for {account_phone}")
            return jsonify({
                'error': f'Account is busy with another operation. Please wait.',
                'error_type': 'account_busy'
            }), 409

        logger.info(f"Acquired lock for account {account_phone}")

        update_operation_state('scan_replies', 0, 0, 'starting', f'Scanning dialogs for {mgr.phone_number}...')

        # Create new event loop for this request
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client_to_cleanup = None

        try:
            # STEP 1: Disconnect from GlobalConnectionManager to release session file lock
            clean_phone = normalize_phone(mgr.phone_number)
            global_conn_manager = GlobalConnectionManager.get_instance()
            if global_conn_manager.is_connected(clean_phone):
                logger.info(f"Disconnecting {clean_phone} from GlobalConnectionManager...")
                try:
                    loop.run_until_complete(global_conn_manager.disconnect_account(clean_phone))
                except Exception as e:
                    logger.warning(f"⚠️  Error disconnecting from GlobalConnectionManager: {e}")

            # Also disconnect mgr.client if it exists (legacy path)
            if mgr.client and mgr.client.is_connected():
                logger.info("Temporarily disconnecting to release session lock...")
                try:
                    disconnect_coro = mgr.client.disconnect()
                    if disconnect_coro:
                        loop.run_until_complete(disconnect_coro)
                    mgr.client = None
                except Exception as e:
                    logger.warning(f"⚠️  Error disconnecting: {e}")

            # STEP 2: Connect fresh client for scan operation
            logger.info("Connecting fresh client for scan operation...")
            connected = loop.run_until_complete(mgr.init_client(mgr.phone_number))
            if not connected:
                reset_operation_state()
                return jsonify({'error': 'Failed to connect to Telegram'}), 500

            client_to_cleanup = mgr.client

            # STEP 3: Run scan operations
            update_operation_state('scan_replies', 10, 100, 'running', 'Checking seen-no-reply...')
            seen_no_reply = loop.run_until_complete(
                mgr.check_seen_no_reply(hours=hours, dialog_limit=dialog_limit, export_csv=export_csv)
            )

            # Get CSV file paths if exported
            csv_files = {}
            if export_csv and seen_no_reply:
                csv_files = mgr.export_noreply_csv_by_type(seen_no_reply, hours)

            update_operation_state('scan_replies', 50, 100, 'running', 'Scanning for replies...')

            # Create callback to send real-time logs to frontend
            def log_callback(message):
                if '✅ REPLY DETECTED' in message or '📧 Dialog' in message or '📊' in message or 'blue contacts' in message.lower():
                    add_operation_log(message)

            scan_result = loop.run_until_complete(
                mgr.scan_for_replies(dialog_limit=dialog_limit, log_callback=log_callback)
            )

            # Extract id_statuses (for update_statuses) and name_statuses (for frontend)
            id_statuses = scan_result.get('id_statuses', {})
            name_statuses = scan_result.get('name_statuses', {})

            # STEP 4: Update statuses if replies found
            if id_statuses:
                update_operation_state('scan_replies', 70, 100, 'running', 'Updating statuses...')
                update_result = loop.run_until_complete(
                    mgr.update_statuses(id_statuses, interactive=False)
                )
            else:
                update_result = {}

            # STEP 5: Auto-backup contacts to update dashboard stats
            backup_info = {}
            try:
                update_operation_state('scan_replies', 90, 100, 'running', 'Backing up contacts for dashboard...')
                logger.info("Auto-backing up contacts after scan...")

                backup_result = loop.run_until_complete(
                    mgr.export_all_contacts_backup()
                )

                if backup_result:
                    backup_path, contacts_count = backup_result
                    if backup_path:
                        # Log backup to database
                        try:
                            from account_manager import init_backups_table, log_backup
                            init_backups_table()
                            log_backup(
                                phone=mgr.phone_number,
                                filename=Path(backup_path).name,
                                filepath=str(backup_path),
                                contacts_count=contacts_count
                            )
                            backup_info = {
                                'path': str(backup_path),
                                'filename': Path(backup_path).name,
                                'contacts_count': contacts_count
                            }
                            logger.info(f"✅ Auto-backup completed: {backup_path} ({contacts_count} contacts)")
                        except Exception as db_err:
                            logger.warning(f"⚠️  Failed to log backup to database: {db_err}")
            except Exception as backup_err:
                logger.warning(f"⚠️  Auto-backup failed (non-critical): {backup_err}")

            # STEP 6: Clean up client before closing loop
            if client_to_cleanup and client_to_cleanup.is_connected():
                logger.info("Disconnecting client after scan...")
                try:
                    loop.run_until_complete(client_to_cleanup.disconnect())
                    mgr.client = None
                except Exception as e:
                    logger.warning(f"⚠️  Error disconnecting after scan: {e}")

            reset_operation_state()
            logger.info(f"✅ Scan completed for {mgr.phone_number}: {len(name_statuses)} replies found")

            return jsonify({
                'success': True,
                'operation': 'scan_replies',
                'phone': mgr.phone_number,
                'scan_results': name_statuses,  # {contact_name: True} for frontend display
                'update_results': update_result,
                'seen_no_reply': seen_no_reply,
                'csv_files': csv_files,
                'backup_info': backup_info
            })

        finally:
            # STEP 6: Ensure client is disconnected before closing loop
            if client_to_cleanup:
                try:
                    if client_to_cleanup.is_connected():
                        loop.run_until_complete(client_to_cleanup.disconnect())
                except Exception as e:
                    logger.warning(f"⚠️  Cleanup error: {e}")
            loop.close()
            # Release account lock
            account_locks.release(account_phone)
            logger.info(f"Released lock for account {account_phone}")

    except TelegramRateLimitError as e:
        # Clean rate limit error from matrix.py - stop immediately and notify frontend
        wait_seconds = e.wait_seconds
        logger.error(f"⚠️ RATE LIMITED: {e.message}")

        set_rate_limit(wait_seconds, 'Telegram rate limit during scan')
        reset_operation_state()

        try:
            account_locks.release(account_phone)
        except:
            pass

        return jsonify({
            'success': False,
            'error': e.message,
            'error_type': 'rate_limit',
            'rate_limit': {
                'wait_seconds': wait_seconds,
                'remaining_seconds': wait_seconds,
                'message': f'Telegram has rate limited this account. Wait {wait_seconds // 60} minutes {wait_seconds % 60} seconds before trying again.'
            }
        }), 429

    except Exception as e:
        error_str = str(e)
        logger.error(f"❌ Error scanning replies: {error_str}")

        # Check for FloodWaitError string in case it wasn't caught by TelegramRateLimitError
        if 'FloodWaitError' in error_str or 'FLOOD' in error_str.upper() or 'rate limit' in error_str.lower():
            import re
            wait_match = re.search(r'(\d+)\s*(?:seconds?|s)', error_str)
            wait_seconds = int(wait_match.group(1)) if wait_match else 300

            set_rate_limit(wait_seconds, 'Telegram rate limit during scan')
            reset_operation_state()
            try:
                account_locks.release(account_phone)
            except:
                pass
            return jsonify({
                'success': False,
                'error': f'Rate limited by Telegram. Please wait {wait_seconds} seconds.',
                'error_type': 'rate_limit',
                'rate_limit': {
                    'wait_seconds': wait_seconds,
                    'remaining_seconds': wait_seconds,
                    'message': f'Telegram has rate limited this account. Wait {wait_seconds // 60} minutes before trying again.'
                }
            }), 429

        reset_operation_state()
        try:
            account_locks.release(account_phone)
            logger.info(f"Released lock for account {account_phone} after error")
        except:
            pass
        return jsonify({'error': error_str, 'traceback': traceback.format_exc()}), 500


@app.route('/api/organize-folders', methods=['POST'])
def organize_folders():
    """Organize contacts into folders

    Supports specifying which account to use via 'phone' parameter.
    If 'phone' is not provided, uses the default account.
    """
    try:
        data = request.get_json() if request.get_json() else {}
        phone = data.get('phone')  # Optional: specific account to use

        # Get manager for specific account or default
        # IMPORTANT: use_shared_connection=False because Flask endpoints run in request threads
        # with their own event loops, which are different from InboxManager's background loop.
        if phone:
            mgr = get_manager_for_account(phone, use_shared_connection=False)
            if not mgr:
                return jsonify({'error': f'Account {phone} not found or not configured'}), 404
        else:
            mgr = get_manager()
            if not mgr:
                return jsonify({'error': 'Manager not initialized. No default account configured.'}), 500
            mgr._conn_manager = None  # Clear to avoid loop conflicts

        update_operation_state('organize_folders', 0, 100, 'starting', f'Creating folders for {mgr.phone_number}...')

        # Run async organize (non-interactive mode for API)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Disconnect from GlobalConnectionManager to release session file lock
            clean_phone = normalize_phone(mgr.phone_number)
            global_conn_manager = GlobalConnectionManager.get_instance()
            if global_conn_manager.is_connected(clean_phone):
                logger.info(f"Disconnecting {clean_phone} from GlobalConnectionManager...")
                try:
                    loop.run_until_complete(global_conn_manager.disconnect_account(clean_phone))
                except Exception as e:
                    logger.warning(f"⚠️  Error disconnecting: {e}")

            # Initialize Telegram client
            connected = loop.run_until_complete(mgr.init_client(mgr.phone_number))
            if not connected:
                reset_operation_state()
                return jsonify({'error': 'Failed to connect to Telegram'}), 500

            result = loop.run_until_complete(
                mgr.organize_folders(interactive=False)
            )

            reset_operation_state()
            logger.info(f"✅ Folder organization completed for {mgr.phone_number}")

            # Auto-backup after organize
            try:
                logger.info(f"📦 Auto-backing up contacts after organize...")
                backup_result = loop.run_until_complete(mgr.export_all_contacts_backup())
                logger.info(f"✅ Auto-backup completed: {backup_result}")
            except Exception as backup_error:
                logger.warning(f"⚠️ Auto-backup failed: {backup_error}")

            return jsonify({
                'success': True,
                'operation': 'organize_folders',
                'phone': mgr.phone_number,
                'result': result
            })
        finally:
            # Disconnect client to release session file lock
            try:
                if mgr.client:
                    disconnect_coro = mgr.client.disconnect()
                    if asyncio.iscoroutine(disconnect_coro):
                        loop.run_until_complete(disconnect_coro)
            except Exception as disconnect_error:
                logger.warning(f"⚠️ Error disconnecting client: {disconnect_error}")
            finally:
                loop.close()
    except Exception as e:
        logger.error(f"❌ Error organizing folders: {str(e)}")
        reset_operation_state()
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/backup-contacts', methods=['POST'])
def backup_contacts():
    """Backup all contacts to CSV file

    Supports specifying which account to use via 'phone' parameter.
    If 'phone' is not provided, uses the default account.
    """
    try:
        data = request.get_json() if request.get_json() else {}
        phone = data.get('phone')  # Optional: specific account to use

        # Get manager for specific account or default
        # IMPORTANT: use_shared_connection=False because Flask endpoints run in request threads
        # with their own event loops, which are different from InboxManager's background loop.
        # Using shared connections would cause "asyncio event loop must not change" errors.
        if phone:
            mgr = get_manager_for_account(phone, use_shared_connection=False)
            if not mgr:
                return jsonify({'error': f'Account {phone} not found or not configured'}), 404
        else:
            mgr = get_manager()
            if not mgr:
                return jsonify({'error': 'Manager not initialized. No default account configured.'}), 500
            # Clear any existing connection manager to avoid loop conflicts
            mgr._conn_manager = None

        update_operation_state('backup_contacts', 0, 100, 'starting', f'Backing up contacts for {mgr.phone_number}...')

        # Run async backup
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client_to_cleanup = None
        try:
            # Disconnect from GlobalConnectionManager to release session file lock
            # This is necessary because InboxManager may have a client connected to this account
            clean_phone = normalize_phone(mgr.phone_number)
            global_conn_manager = GlobalConnectionManager.get_instance()
            if global_conn_manager.is_connected(clean_phone):
                logger.info(f"Disconnecting {clean_phone} from GlobalConnectionManager to release session lock...")
                try:
                    loop.run_until_complete(global_conn_manager.disconnect_account(clean_phone))
                except Exception as e:
                    logger.warning(f"⚠️  Error disconnecting from GlobalConnectionManager: {e}")

            # Also disconnect mgr.client if it exists (legacy path)
            if mgr.client and mgr.client.is_connected():
                logger.info("Temporarily disconnecting to release session lock...")
                try:
                    disconnect_coro = mgr.client.disconnect()
                    if disconnect_coro:
                        loop.run_until_complete(disconnect_coro)
                except Exception as e:
                    logger.warning(f"⚠️  Error disconnecting: {e}")

            # Connect fresh client for backup
            logger.info("Connecting fresh client for backup...")
            connected = loop.run_until_complete(mgr.init_client(mgr.phone_number))
            if not connected:
                reset_operation_state()
                return jsonify({'error': 'Failed to connect to Telegram'}), 500

            # Save reference to client for cleanup
            client_to_cleanup = mgr.client

            backup_result = loop.run_until_complete(
                mgr.export_all_contacts_backup()
            )

            backup_path, contacts_count = backup_result

            if not backup_path:
                reset_operation_state()
                return jsonify({'error': 'Backup failed - no contacts found or error occurred'}), 500

            # Log backup to database
            try:
                from account_manager import init_backups_table, log_backup
                init_backups_table()  # Ensure table exists
                log_backup(
                    phone=mgr.phone_number,
                    filename=Path(backup_path).name,
                    filepath=str(backup_path),
                    contacts_count=contacts_count
                )
            except Exception as e:
                logger.warning(f"⚠️  Failed to log backup to database: {str(e)}")

            # Get relative path for frontend (just the filename, not full path)
            filename = Path(backup_path).name

            # Construct correct download URL - file is saved in logs/backups/
            download_url = f'/logs/backups/{filename}'

            reset_operation_state()
            logger.info(f"✅ Backup completed for {mgr.phone_number}: {backup_path} ({contacts_count} contacts)")

            # Clean up client before closing loop
            if client_to_cleanup and client_to_cleanup.is_connected():
                logger.info("Disconnecting client after backup...")
                try:
                    loop.run_until_complete(client_to_cleanup.disconnect())
                    mgr.client = None
                except Exception as e:
                    logger.warning(f"⚠️  Error disconnecting after backup: {e}")

            return jsonify({
                'success': True,
                'operation': 'backup_contacts',
                'phone': mgr.phone_number,
                'backup_path': str(backup_path),
                'filename': filename,
                'contacts_count': contacts_count,
                'download_url': download_url
            })
        finally:
            # Ensure client is disconnected before closing loop
            if client_to_cleanup:
                try:
                    if client_to_cleanup.is_connected():
                        loop.run_until_complete(client_to_cleanup.disconnect())
                except Exception as e:
                    logger.warning(f"⚠️  Cleanup error: {e}")
            loop.close()
    except Exception as e:
        logger.error(f"❌ Error backing up contacts: {str(e)}")
        reset_operation_state()
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/backup-history', methods=['GET'])
def get_backup_history_endpoint():
    """Get backup history"""
    try:
        phone = request.args.get('phone')
        limit = int(request.args.get('limit', 10))

        from account_manager import init_backups_table, get_backup_history
        init_backups_table()  # Ensure table exists

        backups = get_backup_history(phone=phone, limit=limit)

        return jsonify({
            'success': True,
            'backups': backups,
            'count': len(backups)
        })
    except Exception as e:
        logger.error(f"❌ Error getting backup history: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


# ============================================================================
# SESSION MANAGEMENT ENDPOINTS
# ============================================================================

@app.route('/api/sessions', methods=['GET'])
def list_sessions():
    """List available sessions"""
    try:
        sessions_dir = Path(__file__).parent.parent / "sessions"
        sessions = []

        if sessions_dir.exists():
            for session_file in sessions_dir.glob("session_*.session"):
                phone = session_file.stem.replace("session_", "")
                sessions.append({
                    'phone': phone,
                    'filename': session_file.name,
                    'size': session_file.stat().st_size,
                    'created': datetime.fromtimestamp(session_file.stat().st_ctime).isoformat(),
                })

        logger.info(f"📱 Found {len(sessions)} sessions")
        return jsonify({
            'sessions': sessions,
            'count': len(sessions)
        })
    except Exception as e:
        logger.error(f"❌ Error listing sessions: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/sessions/select', methods=['POST'])
def select_session():
    """
    Switch to a different session/account
    
    This updates the default account in the database (sets is_default = 1).
    The account must exist in the database (will be added when session is loaded).
    """
    try:
        data = request.get_json()
        phone = data.get('phone')

        if not phone:
            return jsonify({'error': 'phone required'}), 400

        # Clean phone number (remove +, -, spaces)
        clean_phone = phone.replace('+', '').replace('-', '').replace(' ', '')
        
        # Verify account exists in database
        account = get_account_by_phone(clean_phone)
        if not account:
            logger.warning(f"⚠️  Account {clean_phone} not found in database")
            logger.info(f"💡 Tip: Account will be added to database when session is loaded")
            return jsonify({
                'error': f'Account {clean_phone} not found in database. Load the session first to add it.',
                'suggestion': 'Load the session first, then select it as default'
            }), 404

        # Set as default account in database
        success = set_default_account(clean_phone)
        if not success:
            return jsonify({'error': 'Failed to set default account'}), 500

        # Reset manager to use new session
        global manager
        with manager_lock:
            manager = None

        logger.info(f"✅ Default account switched to {clean_phone}")
        return jsonify({
            'success': True,
            'message': f'Switched to account {clean_phone}',
            'default_account': clean_phone,
            'account': account
        })
    except Exception as e:
        logger.error(f"❌ Error selecting session: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


# ============================================================================
# AUTHENTICATION ENDPOINTS
# ============================================================================

# Store temporary auth state (phone_code_hash per phone)
auth_state = {}
auth_state_lock = threading.Lock()


def trigger_auto_backup(phone: str):
    """
    Trigger automatic backup for a newly authenticated account.
    Runs in a background thread to not block the auth response.
    Creates per-account backup file (contacts_{phone}_latest.csv) for stats.
    """
    def run_backup():
        try:
            logger.info(f"📦 Auto-backup starting for {phone}...")

            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                # Get account from database
                account = get_account_by_phone(phone)
                if not account:
                    logger.warning(f"⚠️ Auto-backup: Account {phone} not found in database")
                    return

                # Get API credentials
                api_id = account.get('api_id')
                api_hash = account.get('api_hash')
                proxy = account.get('proxy')

                # Fall back to global credentials if not set
                if not api_id or not api_hash:
                    config = load_config()
                    api_id = config.get('api_id')
                    api_hash = config.get('api_hash')

                if not api_id or not api_hash:
                    logger.warning(f"⚠️ Auto-backup: No API credentials for {phone}")
                    return

                # Create manager WITHOUT shared connection pool
                # (running in new thread with new event loop - can't reuse pool clients)
                mgr = UnifiedContactManager(
                    api_id, api_hash, phone, proxy=proxy,
                    conn_manager=None  # Don't use shared pool in background thread
                )

                async def do_backup():
                    # Disconnect from GlobalConnectionManager if connected (release session lock)
                    clean_phone = phone.replace('+', '').replace('-', '').replace(' ', '')
                    global_conn_manager = GlobalConnectionManager.get_instance()
                    if global_conn_manager.is_connected(clean_phone):
                        logger.info(f"Disconnecting {clean_phone} from GlobalConnectionManager for auto-backup...")
                        await global_conn_manager.disconnect_account(clean_phone)

                    connected = await mgr.init_client(phone)
                    if not connected:
                        logger.warning(f"⚠️ Auto-backup: Could not connect for {phone}")
                        return None, 0

                    try:
                        filename, count = await mgr.export_all_contacts_backup()
                        return filename, count
                    finally:
                        # Don't disconnect - shared pool manages connections
                        pass

                filename, count = loop.run_until_complete(do_backup())

                if filename:
                    logger.info(f"✅ Auto-backup complete for {phone}: {count} contacts saved to {filename}")
                else:
                    logger.warning(f"⚠️ Auto-backup failed for {phone}")

            finally:
                loop.close()

        except Exception as e:
            logger.error(f"❌ Auto-backup error for {phone}: {str(e)}")

    # Run backup in background thread
    backup_thread = threading.Thread(target=run_backup, daemon=True)
    backup_thread.start()
    logger.info(f"📦 Auto-backup triggered for {phone} (running in background)")


@app.route('/api/auth/send-code', methods=['POST'])
def send_auth_code():
    """
    Send authentication code to phone

    This endpoint supports per-account API credentials and proxy.
    If api_id and api_hash are provided, they will be used for this specific account.
    Otherwise, falls back to global config.json credentials.
    Proxy can be specified in format "http://ip:port".
    """
    try:
        data = request.get_json()
        phone = data.get('phone')
        account_api_id = data.get('api_id')
        account_api_hash = data.get('api_hash')
        account_proxy = data.get('proxy')  # Optional proxy URL

        if not phone:
            return jsonify({'error': 'phone required'}), 400

        # Use account-specific credentials if provided, otherwise fall back to global
        if account_api_id and account_api_hash:
            # Convert api_id to int if it's a string
            try:
                api_id = int(account_api_id)
            except (ValueError, TypeError):
                return jsonify({'error': 'api_id must be a number'}), 400
            api_hash = str(account_api_hash)
            logger.info(f"📱 Using account-specific API credentials for {phone}")
        else:
            # Fall back to global credentials
            api_id, api_hash = get_api_credentials()
            if not api_id or not api_hash:
                return jsonify({'error': 'API credentials not configured. Please provide api_id and api_hash.'}), 400
            logger.info(f"📱 Using global API credentials for {phone}")

        # Clean phone number
        clean_phone = phone.replace('+', '').replace('-', '').replace(' ', '')
        phone_number = f"+{clean_phone}"

        # Clear any existing auth state for this phone (prevents stale state issues)
        with auth_state_lock:
            old_state = auth_state.pop(clean_phone, None)
            if old_state:
                # Close old event loop if it exists
                old_loop = old_state.get('loop')
                if old_loop and not old_loop.is_closed():
                    try:
                        old_loop.close()
                    except:
                        pass
                logger.info(f"🧹 Cleared previous auth state for {clean_phone}")

        # Create temporary manager for authentication with credentials and optional proxy
        temp_manager = UnifiedContactManager(api_id=api_id, api_hash=api_hash, phone_number=phone_number, proxy=account_proxy)
        if account_proxy:
            logger.info(f"🔌 Using proxy for authentication: {account_proxy}")

        # Check if account exists and might need re-authentication
        account = get_account_by_phone(clean_phone)
        force_new = False
        if account:
            # Account exists - check if we should force new session (for re-auth)
            force_new = True  # Always create new session when re-authenticating
            logger.info(f"🔄 Re-authenticating existing account: {clean_phone}")

        # Start authentication (send code)
        # Create event loop that will be reused for the entire auth flow
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(temp_manager.start_authentication(phone_number, force_new=force_new))

            if result.get('success'):
                # Store phone_code_hash, manager reference, API credentials, proxy, AND the event loop
                # The loop must be reused in verify_code/verify_password to avoid
                # "The asyncio event loop must not change after connection" error
                with auth_state_lock:
                    auth_state[clean_phone] = {
                        'phone_code_hash': result.get('phone_code_hash'),
                        'manager': temp_manager,
                        'phone_number': phone_number,
                        'api_id': api_id,
                        'api_hash': api_hash,
                        'proxy': account_proxy,  # Store proxy for saving to database later
                        'loop': loop  # Store the loop to reuse in subsequent auth steps
                    }

                logger.info(f"✅ Code sent to {phone_number}")
                return jsonify({
                    'success': True,
                    'message': 'Code sent successfully. Check your Telegram app.',
                    'phone': phone_number
                })
            else:
                # Auth failed, close the loop
                loop.close()
                return jsonify({
                    'success': False,
                    'error': result.get('error', 'Failed to send code')
                }), 400
        except Exception as e:
            loop.close()
            raise

    except Exception as e:
        logger.error(f"❌ Error sending auth code: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/auth/start', methods=['POST'])
def start_authentication():
    """Start authentication - send code to phone (legacy endpoint, redirects to send-code)"""
    # This is kept for backward compatibility - redirects to send-code logic
    return send_auth_code()


@app.route('/api/auth/verify-code', methods=['POST'])
def verify_auth_code():
    """Verify authentication code (alias for submit-code)"""
    loop = None
    try:
        data = request.get_json()
        phone = data.get('phone')
        code = data.get('code')

        if not phone or not code:
            return jsonify({'error': 'phone and code required'}), 400

        # Clean phone number
        clean_phone = phone.replace('+', '').replace('-', '').replace(' ', '')

        # Get auth state
        with auth_state_lock:
            auth_info = auth_state.get(clean_phone)
            if not auth_info:
                return jsonify({'error': 'No active authentication session. Call /api/auth/send-code first.'}), 400

            phone_code_hash = auth_info['phone_code_hash']
            temp_manager = auth_info['manager']
            phone_number = auth_info['phone_number']
            api_id = auth_info.get('api_id')
            api_hash = auth_info.get('api_hash')
            loop = auth_info.get('loop')  # Reuse the same loop from send_code

        # Submit code using the SAME event loop that was used to create the client
        # This avoids "The asyncio event loop must not change after connection" error
        if loop is None:
            # Fallback: create new loop if none stored (shouldn't happen normally)
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        result = loop.run_until_complete(
            temp_manager.submit_code(phone_number, code, phone_code_hash)
        )

        if result.get('success'):
            # Authentication successful - save account to database with its API credentials
            # This ensures each account has its own credentials stored

            # Reset global manager
            global manager
            with manager_lock:
                manager = None

            # Clear auth state and close the loop
            with auth_state_lock:
                auth_state.pop(clean_phone, None)
            loop.close()

            logger.info(f"✅ Authentication successful for {phone_number}")

            # Trigger auto-backup in background to populate stats immediately
            trigger_auto_backup(clean_phone)

            return jsonify({
                'success': True,
                'user': result.get('user'),
                'message': 'Authentication successful'
            })
        elif result.get('requires_password'):
            # 2FA required - keep auth state AND the loop for password verification
            logger.info(f"🔐 2FA required for {phone_number}")
            return jsonify({
                'success': False,
                'requires_password': True,
                'message': 'This account has 2FA enabled. Please enter your password.',
                'phone': phone_number
            })
        else:
            # Code verification failed - clean up
            with auth_state_lock:
                auth_state.pop(clean_phone, None)
            loop.close()
            return jsonify({
                'success': False,
                'error': result.get('error', 'Code verification failed')
            }), 400

    except Exception as e:
        # Clean up on error
        if loop:
            try:
                with auth_state_lock:
                    if clean_phone in auth_state:
                        auth_state.pop(clean_phone, None)
                loop.close()
            except:
                pass
        logger.error(f"❌ Error verifying code: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/auth/submit-code', methods=['POST'])
def submit_auth_code():
    """Submit authentication code (legacy endpoint)"""
    # Redirect to verify-code for consistency
    return verify_auth_code()


@app.route('/api/auth/verify-password', methods=['POST'])
def verify_auth_password():
    """Verify 2FA password"""
    loop = None
    try:
        data = request.get_json()
        phone = data.get('phone')
        password = data.get('password')

        if not phone or not password:
            return jsonify({'error': 'phone and password required'}), 400

        # Clean phone number
        clean_phone = phone.replace('+', '').replace('-', '').replace(' ', '')

        # Get auth state
        with auth_state_lock:
            auth_info = auth_state.get(clean_phone)
            if not auth_info:
                return jsonify({'error': 'No active authentication session. Call /api/auth/send-code first.'}), 400

            temp_manager = auth_info['manager']
            loop = auth_info.get('loop')  # Reuse the same loop from send_code

        # Submit password using the SAME event loop that was used to create the client
        # This avoids "The asyncio event loop must not change after connection" error
        if loop is None:
            # Fallback: create new loop if none stored (shouldn't happen normally)
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        result = loop.run_until_complete(temp_manager.submit_password(password))

        if result.get('success'):
            # Authentication successful

            # Reset global manager
            global manager
            with manager_lock:
                manager = None

            # Clear auth state and close the loop
            with auth_state_lock:
                auth_state.pop(clean_phone, None)
            loop.close()

            logger.info(f"✅ 2FA authentication successful for {clean_phone}")

            # Trigger auto-backup in background to populate stats immediately
            trigger_auto_backup(clean_phone)

            return jsonify({
                'success': True,
                'user': result.get('user'),
                'message': 'Authentication successful'
            })
        else:
            # Password failed but don't close loop - user might retry
            return jsonify({
                'success': False,
                'error': result.get('error', 'Password verification failed')
            }), 400

    except Exception as e:
        # Clean up on error
        if loop:
            try:
                with auth_state_lock:
                    if clean_phone in auth_state:
                        auth_state.pop(clean_phone, None)
                loop.close()
            except:
                pass
        logger.error(f"❌ Error verifying password: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/auth/submit-password', methods=['POST'])
def submit_auth_password():
    """Submit 2FA password (legacy endpoint)"""
    # Redirect to verify-password for consistency
    return verify_auth_password()


# ============================================================================
# ACCOUNT MANAGEMENT ENDPOINTS
# ============================================================================

@app.route('/api/accounts', methods=['GET'])
def list_accounts():
    """List all accounts from database"""
    try:
        accounts = get_all_accounts()
        logger.info(f"📱 Found {len(accounts)} accounts")
        return jsonify({
            'accounts': accounts,
            'count': len(accounts)
        })
    except Exception as e:
        logger.error(f"❌ Error listing accounts: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/accounts/active', methods=['GET'])
def list_active_accounts():
    """List only active accounts"""
    try:
        accounts = get_active_accounts()
        logger.info(f"📱 Found {len(accounts)} active accounts")
        return jsonify({
            'accounts': accounts,
            'count': len(accounts)
        })
    except Exception as e:
        logger.error(f"❌ Error listing active accounts: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/accounts/add', methods=['POST'])
def add_account_endpoint():
    """Add a new account to the database"""
    try:
        data = request.get_json()
        phone = data.get('phone')
        name = data.get('name')
        api_id = data.get('api_id')
        api_hash = data.get('api_hash')
        notes = data.get('notes')

        if not phone:
            return jsonify({'error': 'phone required'}), 400

        # Construct session path
        clean_phone = phone.replace('+', '').replace('-', '').replace(' ', '')
        sessions_dir = Path(__file__).parent.parent / "sessions"
        session_path = str(sessions_dir / f"session_{clean_phone}.session")

        success = add_account(
            phone=phone,
            name=name,
            api_id=api_id,
            api_hash=api_hash,
            session_path=session_path,
            notes=notes
        )

        if success:
            logger.info(f"✅ Added account: {phone}")
            return jsonify({
                'success': True,
                'message': f'Account {phone} added successfully',
                'account': get_account_by_phone(phone)
            })
        else:
            return jsonify({'error': 'Account already exists'}), 400
    except Exception as e:
        logger.error(f"❌ Error adding account: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/accounts/validate', methods=['POST'])
def validate_account_endpoint():
    """Validate a single account"""
    try:
        data = request.get_json()
        phone = data.get('phone')
        api_id = data.get('api_id')
        api_hash = data.get('api_hash')

        if not phone:
            return jsonify({'error': 'phone required'}), 400

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            is_valid, message = loop.run_until_complete(
                validate_account(phone, api_id, api_hash)
            )
            
            response = {
                'success': True,
                'phone': phone,
                'is_valid': is_valid,
                'message': message
            }
            
            # If session is expired, suggest re-authentication
            if not is_valid and 'needs authentication' in message.lower():
                response['needs_reauth'] = True
                response['suggestion'] = 'Session expired. Please re-authenticate using the Authentication page (/auth)'
            
            return jsonify(response)
        finally:
            loop.close()
    except Exception as e:
        logger.error(f"❌ Error validating account: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/accounts/validate-batch', methods=['POST'])
def validate_accounts_batch_endpoint():
    """Validate multiple accounts"""
    try:
        data = request.get_json()
        phones = data.get('phones', [])

        if not phones:
            return jsonify({'error': 'phones array required'}), 400

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(
                validate_accounts_batch(phones)
            )
            return jsonify({
                'success': True,
                'results': {phone: {'is_valid': valid, 'message': msg} 
                           for phone, (valid, msg) in results.items()}
            })
        finally:
            loop.close()
    except Exception as e:
        logger.error(f"❌ Error validating accounts batch: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/accounts/<phone>', methods=['DELETE'])
def delete_account_endpoint(phone):
    """Delete an account and clean up session file"""
    try:
        # Clean phone number
        clean_phone = phone.replace('+', '').replace('-', '').replace(' ', '')

        # First disconnect from GlobalConnectionManager if connected
        conn_manager = GlobalConnectionManager.get_instance()
        if conn_manager.is_connected(clean_phone):
            logger.info(f"🔌 Disconnecting account {clean_phone} from shared pool...")
            # Run async disconnect in a new event loop
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(conn_manager.disconnect_account(clean_phone))
            finally:
                loop.close()
            logger.info(f"✅ Disconnected from shared pool")

        # Delete session file to ensure clean state
        session_path = SESSIONS_DIR / f"session_{clean_phone}.session"
        if session_path.exists():
            try:
                session_path.unlink()
                logger.info(f"🗑️  Deleted session file: {session_path.name}")
            except Exception as e:
                logger.warning(f"⚠️  Could not delete session file: {e}")

        # Delete from database
        success = delete_account(phone)
        if success:
            logger.info(f"✅ Deleted account: {phone}")
            return jsonify({
                'success': True,
                'message': f'Account {phone} deleted'
            })
        else:
            return jsonify({'error': 'Account not found'}), 404
    except Exception as e:
        logger.error(f"❌ Error deleting account: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/accounts/<phone>/status', methods=['PUT'])
def update_account_status_endpoint(phone):
    """Update account status. When setting to 'inactive', disconnects from shared pool."""
    try:
        data = request.get_json()
        status = data.get('status')

        if not status:
            return jsonify({'error': 'status required'}), 400

        # Clean phone number
        clean_phone = phone.replace('+', '').replace('-', '').replace(' ', '')

        # If deactivating, disconnect from GlobalConnectionManager first
        if status in ['inactive', 'disabled', 'error']:
            conn_manager = GlobalConnectionManager.get_instance()
            if conn_manager.is_connected(clean_phone):
                logger.info(f"🔌 Disconnecting account {clean_phone} (status -> {status})...")
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(conn_manager.disconnect_account(clean_phone))
                finally:
                    loop.close()
                logger.info(f"✅ Disconnected from shared pool")

        success = update_account_status(phone, status)
        if success:
            return jsonify({
                'success': True,
                'message': f'Account {phone} status updated to {status}'
            })
        else:
            return jsonify({'error': 'Account not found'}), 404
    except Exception as e:
        logger.error(f"❌ Error updating account status: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/accounts/<phone>/proxy', methods=['PUT'])
def update_account_proxy_endpoint(phone):
    """
    Update proxy for an account and invalidate session.

    When proxy is changed, the session file is deleted to force re-authentication.
    The user must re-authenticate through the new proxy.

    Request body:
        {
            "proxy": "http://ip:port" or null to remove proxy
        }

    Returns:
        Success message with re-authentication required notice
    """
    try:
        data = request.get_json() if request.get_json() else {}
        proxy = data.get('proxy')  # Can be None to remove proxy

        # Validate proxy format if provided
        if proxy:
            # Basic validation - must start with http://, https://, socks4://, or socks5://
            if not any(proxy.startswith(scheme) for scheme in ['http://', 'https://', 'socks4://', 'socks5://']):
                return jsonify({
                    'error': 'Invalid proxy format. Use: http://ip:port, socks5://ip:port, etc.'
                }), 400

            # Test parsing the proxy
            parsed = parse_proxy_url(proxy)
            if parsed is None:
                return jsonify({
                    'error': 'Could not parse proxy URL. Ensure format is correct (e.g., http://192.168.1.1:8080)'
                }), 400

        # Update proxy and invalidate session
        success, message = update_account_proxy(phone, proxy)

        if success:
            # Reset global manager if this was the default account
            global manager
            with manager_lock:
                if manager and manager.phone_number.replace('+', '') == normalize_phone(phone):
                    manager = None
                    logger.info(f"🔄 Reset global manager after proxy change for {phone}")

            return jsonify({
                'success': True,
                'message': message,
                'proxy': proxy,
                'requires_reauth': True
            })
        else:
            return jsonify({'error': message}), 400

    except Exception as e:
        logger.error(f"❌ Error updating account proxy: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


# ============================================================================
# LOG ENDPOINTS
# ============================================================================

import re

def parse_log_line(line: str, log_id: int) -> Optional[Dict[str, Any]]:
    """Parse a raw log line into a structured log object"""
    try:
        line = line.strip()
        if not line:
            return None

        # Parse log format: "2025-12-03 03:20:13,411 - INFO - message"
        # or "[API] message" format
        timestamp = ""
        level = "info"
        message = line

        # Try to extract timestamp and level
        timestamp_match = re.match(r'^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}),?\d*\s*-\s*(\w+)\s*-\s*(.*)$', line)
        if timestamp_match:
            timestamp = timestamp_match.group(1)
            level = timestamp_match.group(2).lower()
            message = timestamp_match.group(3)
        else:
            # Try API format: "[API] message"
            api_match = re.match(r'^\[API\]\s*(.*)$', line)
            if api_match:
                message = api_match.group(1)
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Determine operation type from message
        operation = "System Log"
        msg_lower = message.lower()
        if 'import' in msg_lower:
            operation = "Import Contacts"
        elif 'scan' in msg_lower:
            operation = "Scan & Update Status"
        elif 'folder' in msg_lower or 'organize' in msg_lower:
            operation = "Organize Folders"
        elif 'backup' in msg_lower:
            operation = "Backup Contacts"
        elif 'auth' in msg_lower or 'code' in msg_lower or 'login' in msg_lower:
            operation = "Authentication"
        elif 'session' in msg_lower:
            operation = "Session Management"
        elif 'error' in msg_lower or 'failed' in msg_lower or level == 'error':
            operation = "Error"

        # Determine status
        status = "pending"
        if level == 'error' or 'error' in msg_lower or 'failed' in msg_lower or '❌' in message:
            status = "error"
        elif 'success' in msg_lower or 'complete' in msg_lower or '✅' in message:
            status = "success"
        elif level == 'info':
            status = "info"

        return {
            'id': log_id,
            'timestamp': timestamp or datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'operation': operation,
            'status': status,
            'message': message[:200] if len(message) > 200 else message,  # Truncate long messages
            'details': message
        }
    except Exception:
        return None

@app.route('/api/logs', methods=['GET'])
def get_logs():
    """Get recent operation logs as structured objects"""
    try:
        limit = request.args.get('limit', 50, type=int)
        filter_type = request.args.get('filter', 'all')
        log_dir = Path(__file__).parent.parent / "logs"
        structured_logs = []
        log_id = 0

        if log_dir.exists():
            log_files = sorted(log_dir.glob("*.log"), reverse=True)

            for log_file in log_files[:5]:  # Read last 5 log files
                try:
                    with open(log_file, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                        for line in lines[-limit:]:
                            log_id += 1
                            parsed = parse_log_line(line, log_id)
                            if parsed:
                                # Apply filter
                                if filter_type == 'all':
                                    structured_logs.append(parsed)
                                elif filter_type == 'import' and 'import' in parsed['operation'].lower():
                                    structured_logs.append(parsed)
                                elif filter_type == 'scan' and 'scan' in parsed['operation'].lower():
                                    structured_logs.append(parsed)
                                elif filter_type == 'organize' and 'folder' in parsed['operation'].lower():
                                    structured_logs.append(parsed)
                                elif filter_type == 'error' and parsed['status'] == 'error':
                                    structured_logs.append(parsed)
                except:
                    pass

        # Sort by timestamp descending and limit
        structured_logs = sorted(structured_logs, key=lambda x: x['timestamp'], reverse=True)[:limit]

        logger.info(f"📋 Retrieved {len(structured_logs)} log entries")
        return jsonify({
            'logs': structured_logs,
            'count': len(structured_logs)
        })
    except Exception as e:
        logger.error(f"❌ Error getting logs: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


# ============================================================================
# FILE UPLOAD ENDPOINTS
# ============================================================================

@app.route('/api/upload-csv', methods=['POST'])
def upload_csv():
    """Upload a CSV file for import (with path traversal protection)"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['file']

        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        if not file.filename.endswith('.csv'):
            return jsonify({'error': 'Only CSV files allowed'}), 400

        # Create uploads directory
        uploads_dir = Path(__file__).parent.parent / "uploads"
        uploads_dir.mkdir(exist_ok=True)

        # Sanitize filename using secure_filename (prevents path traversal attacks)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        base_filename = secure_filename(file.filename.replace('.csv', ''))
        safe_filename = f"{base_filename}_{timestamp}.csv"
        file_path = uploads_dir / safe_filename

        # Verify the resolved path is within uploads directory (defense in depth)
        resolved_path = file_path.resolve()
        if not str(resolved_path).startswith(str(uploads_dir.resolve())):
            logger.error(f"❌ Path traversal attempt detected: {file.filename}")
            return jsonify({'error': 'Invalid filename'}), 400

        file.save(str(file_path))

        logger.info(f"✅ CSV uploaded: {safe_filename} ({file_path.stat().st_size} bytes)")

        return jsonify({
            'success': True,
            'filename': safe_filename,
            'path': str(file_path),
            'size': file_path.stat().st_size,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"❌ Error uploading CSV: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/uploads', methods=['GET'])
def list_uploads():
    """List uploaded CSV files"""
    try:
        uploads_dir = Path(__file__).parent.parent / "uploads"
        files = []

        if uploads_dir.exists():
            for csv_file in uploads_dir.glob("*.csv"):
                files.append({
                    'filename': csv_file.name,
                    'path': str(csv_file),
                    'size': csv_file.stat().st_size,
                    'created': datetime.fromtimestamp(csv_file.stat().st_ctime).isoformat(),
                })

        files.sort(key=lambda x: x['created'], reverse=True)
        logger.info(f"📁 Found {len(files)} uploaded CSV files")

        return jsonify({
            'files': files,
            'count': len(files)
        })
    except Exception as e:
        logger.error(f"❌ Error listing uploads: {str(e)}")
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


# ============================================================================
# FILE SERVING ENDPOINTS
# ============================================================================

@app.route('/api/files/<path:filepath>', methods=['GET'])
def serve_file(filepath):
    """Serve CSV files for download"""
    try:
        # Security: Only allow files from logs/ and uploads/ directories
        base_path = Path(__file__).parent
        allowed_dirs = ['logs', 'uploads']
        
        # Normalize path and check if it's in allowed directory
        # Handle both forward and backslash paths
        normalized_path = filepath.replace('\\', '/').replace('..', '').lstrip('/')
        file_path = base_path / normalized_path
        resolved_path = file_path.resolve()
        
        # Check if file is in allowed directory
        is_allowed = any(
            str(resolved_path).startswith(str(base_path / allowed_dir))
            for allowed_dir in allowed_dirs
        )
        
        if not is_allowed or not file_path.exists():
            return jsonify({'error': 'File not found'}), 404
        
        return send_file(str(file_path), as_attachment=True, download_name=file_path.name)
    except Exception as e:
        logger.error(f"❌ Error serving file: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/logs/<path:filepath>', methods=['GET'])
def serve_log_file(filepath):
    """Serve files from logs directory directly (for easier frontend access)"""
    try:
        base_path = Path(__file__).parent.parent / "logs"
        file_path = base_path / filepath.replace('..', '').lstrip('/')
        resolved_path = file_path.resolve()
        
        # Security check: ensure file is within logs directory
        if not str(resolved_path).startswith(str(base_path.resolve())):
            return jsonify({'error': 'Access denied'}), 403
        
        if not file_path.exists():
            return jsonify({'error': 'File not found'}), 404
        
        return send_file(str(file_path), as_attachment=True, download_name=file_path.name)
    except Exception as e:
        logger.error(f"❌ Error serving log file: {str(e)}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# INBOX MANAGEMENT ENDPOINTS
# Real-time inbox system with persistent Telegram connections
# ============================================================================

def run_inbox_coroutine(coro):
    """Helper to run inbox manager coroutine from sync context."""
    global inbox_manager
    if not inbox_manager or not inbox_manager._loop:
        raise RuntimeError("Inbox manager not started")

    future = asyncio.run_coroutine_threadsafe(coro, inbox_manager._loop)
    return future.result(timeout=60)


# ============================================================================
# INBOX CONNECTION ENDPOINTS
# ============================================================================

@app.route('/api/inbox/connect', methods=['POST'])
def inbox_connect():
    """Connect single account to inbox system."""
    global inbox_manager
    try:
        data = request.get_json() or {}
        phone = data.get('phone')

        if not phone:
            return jsonify({'error': 'Phone number required'}), 400

        if not inbox_manager:
            return jsonify({'error': 'Inbox manager not started'}), 503

        success = run_inbox_coroutine(inbox_manager.connect_account(phone))

        return jsonify({
            'success': success,
            'phone': normalize_phone(phone),
            'message': 'Connected' if success else 'Failed to connect'
        })

    except Exception as e:
        logger.error(f"❌ Error connecting inbox: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/inbox/connect-all', methods=['POST'])
def inbox_connect_all():
    """Connect all active accounts to inbox system."""
    global inbox_manager
    try:
        if not inbox_manager:
            return jsonify({'error': 'Inbox manager not started'}), 503

        results = run_inbox_coroutine(inbox_manager.connect_all_active_accounts())

        connected = sum(1 for v in results.values() if v)
        total = len(results)

        return jsonify({
            'success': True,
            'connected': connected,
            'total': total,
            'results': results
        })

    except Exception as e:
        logger.error(f"❌ Error connecting all inbox: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/inbox/disconnect', methods=['POST'])
def inbox_disconnect():
    """Disconnect account from inbox system."""
    global inbox_manager
    try:
        data = request.get_json() or {}
        phone = data.get('phone')

        if not phone:
            return jsonify({'error': 'Phone number required'}), 400

        if not inbox_manager:
            return jsonify({'error': 'Inbox manager not started'}), 503

        run_inbox_coroutine(inbox_manager.disconnect_account(phone))

        return jsonify({
            'success': True,
            'phone': normalize_phone(phone),
            'message': 'Disconnected'
        })

    except Exception as e:
        logger.error(f"❌ Error disconnecting inbox: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/inbox/connection-status', methods=['GET'])
def inbox_connection_status():
    """Get connection states for all accounts."""
    global inbox_manager
    try:
        # inbox_get_connection_states returns List[Dict], convert to Dict[phone, state_info]
        states_list = inbox_get_connection_states() or []
        states = {}
        for state_row in states_list:
            phone = state_row.get('account_phone', '')
            if phone:
                states[phone] = state_row

        # Get connected accounts from GlobalConnectionManager if inbox_manager is running
        connected_phones = []
        if inbox_manager and inbox_manager._conn_manager:
            connected_phones = inbox_manager._conn_manager.get_connected_accounts()

        # Build connections dict in the format frontend expects: {phone: {connected: bool, ...}}
        # Include all accounts from database states AND currently connected phones
        all_phones = set(states.keys()) | set(connected_phones)
        
        # Also include all active accounts from the database
        try:
            active_accounts = get_active_accounts()
            for acc in active_accounts:
                phone = normalize_phone(acc.get('phone', ''))
                if phone:
                    all_phones.add(phone)
        except Exception as e:
            logger.warning(f"Could not get active accounts: {e}")
        
        connections = {}
        for phone in all_phones:
            clean_phone = normalize_phone(phone)
            state_info = states.get(clean_phone, {})
            connections[clean_phone] = {
                'connected': clean_phone in connected_phones,
                'state': state_info.get('state', 'disconnected'),
                'last_connected': state_info.get('last_connected'),
                'reconnect_attempts': state_info.get('reconnect_attempts', 0),
                'error': state_info.get('error'),
            }

        return jsonify({
            'success': True,
            'connections': connections,  # Frontend expects this key
            'states': states,  # Keep for backward compatibility
            'connected_accounts': connected_phones,
            'inbox_manager_running': inbox_manager is not None and inbox_manager._running
        })

    except Exception as e:
        logger.error(f"❌ Error getting connection status: {str(e)}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# INBOX CONVERSATION ENDPOINTS
# ============================================================================

@app.route('/api/inbox/<phone>/conversations', methods=['GET'])
def inbox_conversations(phone):
    """Get conversations for account."""
    try:
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        unread_only = request.args.get('unread_only', 'false').lower() == 'true'
        matrix_only = request.args.get('matrix_only', 'false').lower() == 'true'

        conversations = inbox_get_conversations(
            phone, limit, offset, unread_only, matrix_only
        )

        return jsonify({
            'success': True,
            'conversations': conversations,
            'count': len(conversations),
            'limit': limit,
            'offset': offset
        })

    except Exception as e:
        logger.error(f"❌ Error getting conversations: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/inbox/<phone>/conversations/<int:peer_id>', methods=['GET'])
def inbox_conversation_detail(phone, peer_id):
    """Get single conversation details."""
    try:
        conv = inbox_get_or_create_conversation(phone, peer_id)

        if not conv:
            return jsonify({'error': 'Conversation not found'}), 404

        return jsonify({
            'success': True,
            'conversation': conv
        })

    except Exception as e:
        logger.error(f"❌ Error getting conversation: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/inbox/<phone>/conversations/<int:peer_id>/messages', methods=['GET'])
def inbox_messages(phone, peer_id):
    """Get messages for conversation."""
    try:
        limit = request.args.get('limit', 200, type=int)
        before_msg_id = request.args.get('before_msg_id', type=int)

        messages = inbox_get_messages(phone, peer_id, limit, before_msg_id)

        return jsonify({
            'success': True,
            'messages': messages,
            'count': len(messages),
            'peer_id': peer_id
        })

    except Exception as e:
        logger.error(f"❌ Error getting messages: {str(e)}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# INBOX SEND MESSAGE ENDPOINT
# ============================================================================

@app.route('/api/inbox/<phone>/send', methods=['POST'])
def inbox_send_message(phone):
    """Send message (rate limited)."""
    global inbox_manager
    try:
        data = request.get_json() or {}
        peer_id = data.get('peer_id')
        text = data.get('text', '').strip()
        campaign_id = data.get('campaign_id')

        if not peer_id:
            return jsonify({'error': 'peer_id required'}), 400
        if not text:
            return jsonify({'error': 'text required'}), 400

        if not inbox_manager:
            return jsonify({'error': 'Inbox manager not started'}), 503

        result = run_inbox_coroutine(
            inbox_manager.send_message(phone, peer_id, text, campaign_id)
        )

        if result.get('success'):
            return jsonify(result)
        else:
            return jsonify(result), 429 if 'limit' in result.get('error', '').lower() else 400

    except Exception as e:
        logger.error(f"❌ Error sending message: {str(e)}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# INBOX FETCH FULL HISTORY ENDPOINT
# ============================================================================

@app.route('/api/inbox/<phone>/conversations/<int:peer_id>/fetch-history', methods=['POST'])
def inbox_fetch_full_history(phone, peer_id):
    """Fetch complete message history for a conversation (on-demand)."""
    global inbox_manager
    try:
        if not inbox_manager:
            return jsonify({'error': 'Inbox manager not started'}), 503

        result = run_inbox_coroutine(
            inbox_manager.fetch_full_history(phone, peer_id)
        )

        if result.get('success'):
            return jsonify(result)
        else:
            status = 429 if result.get('wait_seconds') else 400
            return jsonify(result), status

    except Exception as e:
        logger.error(f"❌ Error fetching history: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/inbox/<phone>/rate-limit', methods=['GET'])
def inbox_rate_limit_status(phone):
    """Get rate limit status for account."""
    global inbox_manager
    try:
        if inbox_manager:
            status = inbox_manager.get_rate_limit_status(phone)
        else:
            # Return default status if manager not running
            from inbox_manager import DMRateLimiter
            limiter = DMRateLimiter(phone)
            status = limiter.get_status()

        return jsonify({
            'success': True,
            **status
        })

    except Exception as e:
        logger.error(f"❌ Error getting rate limit: {str(e)}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# INBOX SYNC ENDPOINTS
# ============================================================================

@app.route('/api/inbox/<phone>/sync/dialogs', methods=['POST'])
def inbox_sync_dialogs(phone):
    """Trigger dialog sync for account."""
    global inbox_manager
    try:
        if not inbox_manager:
            return jsonify({'error': 'Inbox manager not started'}), 503

        result = run_inbox_coroutine(inbox_manager.trigger_dialog_sync(phone))

        return jsonify({
            'success': True,
            'dialogs_fetched': result.dialogs_fetched,
            'synced': result.synced,
            'skipped': result.skipped,
            'gaps_detected': result.gaps_detected,
            'errors': result.errors
        })

    except Exception as e:
        logger.error(f"❌ Error syncing dialogs: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/inbox/<phone>/sync/full', methods=['POST'])
def inbox_sync_full(phone):
    """Trigger full sync for account."""
    global inbox_manager
    try:
        if not inbox_manager:
            return jsonify({'error': 'Inbox manager not started'}), 503

        result = run_inbox_coroutine(inbox_manager.trigger_full_sync(phone))

        return jsonify({
            'success': True,
            'dialogs_synced': result.dialogs_synced,
            'messages_backfilled': result.messages_backfilled,
            'integrity_ok': result.integrity_ok,
            'errors': result.errors
        })

    except Exception as e:
        logger.error(f"❌ Error full sync: {str(e)}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# INBOX METRICS ENDPOINTS
# ============================================================================

@app.route('/api/inbox/metrics', methods=['GET'])
def inbox_metrics():
    """Get inbox metrics."""
    try:
        phone = request.args.get('phone')
        campaign_id = request.args.get('campaign_id')

        # Get campaign metrics
        campaigns = inbox_get_campaign_metrics(campaign_id)

        # Get connection states
        connection_states = inbox_get_connection_states()

        # Calculate totals
        total_messages = 0
        total_conversations = 0
        for state in connection_states:
            total_messages += state.get('messages_count', 0) or 0
            total_conversations += state.get('dialogs_count', 0) or 0

        return jsonify({
            'success': True,
            'campaigns': campaigns,
            'connection_states': connection_states,
            'totals': {
                'messages': total_messages,
                'conversations': total_conversations,
                'connected_accounts': len([s for s in connection_states if s.get('is_connected')])
            }
        })

    except Exception as e:
        logger.error(f"❌ Error getting metrics: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/inbox/campaigns/<campaign_id>/metrics', methods=['GET'])
def inbox_campaign_metrics(campaign_id):
    """Get metrics for specific campaign."""
    try:
        # Recalculate metrics
        inbox_update_campaign_metrics(campaign_id)

        # Get updated metrics
        metrics = inbox_get_campaign_metrics(campaign_id)

        if not metrics:
            return jsonify({'error': 'Campaign not found'}), 404

        return jsonify({
            'success': True,
            'campaign': metrics[0] if metrics else None
        })

    except Exception as e:
        logger.error(f"❌ Error getting campaign metrics: {str(e)}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# INBOX WEBSOCKET HANDLERS
# ============================================================================

@socketio.on('inbox:subscribe')
def handle_inbox_subscribe(data):
    """Subscribe to inbox events for an account."""
    phone = data.get('phone')
    if phone:
        clean_phone = normalize_phone(phone)
        join_room(f"inbox:{clean_phone}")
        emit('inbox:subscribed', {'phone': clean_phone, 'success': True})
        logger.debug(f"📬 Client subscribed to inbox:{clean_phone}")


@socketio.on('inbox:unsubscribe')
def handle_inbox_unsubscribe(data):
    """Unsubscribe from inbox events."""
    phone = data.get('phone')
    if phone:
        clean_phone = normalize_phone(phone)
        leave_room(f"inbox:{clean_phone}")
        emit('inbox:unsubscribed', {'phone': clean_phone})
        logger.debug(f"📭 Client unsubscribed from inbox:{clean_phone}")


@socketio.on('inbox:subscribe_all')
def handle_inbox_subscribe_all():
    """Subscribe to all connected accounts."""
    global inbox_manager
    subscribed = []

    if inbox_manager and inbox_manager._conn_manager:
        for phone in inbox_manager._conn_manager.get_connected_accounts():
            join_room(f"inbox:{phone}")
            subscribed.append(phone)

    emit('inbox:subscribed_all', {'phones': subscribed, 'count': len(subscribed)})
    logger.debug(f"📬 Client subscribed to {len(subscribed)} inbox rooms")


# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500


@app.before_request
def log_request():
    """Log incoming requests"""
    if request.endpoint and 'health' not in request.endpoint:
        logger.debug(f"📨 {request.method} {request.path}")


if __name__ == '__main__':
    import atexit
    import signal

    logger.info("🚀 Starting MATRIX API Server on http://localhost:5000")
    logger.info("📱 React Native frontend should connect to this server")
    logger.info("🔌 WebSocket server enabled for real-time progress updates")

    # Initialize accounts database if it doesn't exist
    try:
        init_database()
        logger.info("✅ Accounts database ready")
    except Exception as e:
        logger.warning(f"⚠️  Could not initialize accounts database: {str(e)}")

    # Initialize operations tables for persistence
    try:
        init_operations_tables()
        logger.info("✅ Operations tables ready")
    except Exception as e:
        logger.warning(f"⚠️  Could not initialize operations tables: {str(e)}")

    # Initialize inbox tables for real-time message management
    try:
        init_inbox_tables()
        logger.info("✅ Inbox tables ready")
    except Exception as e:
        logger.warning(f"⚠️  Could not initialize inbox tables: {str(e)}")

    # ========================================================================
    # Clean up stale session lock files BEFORE any Telegram connections
    # This prevents "database is locked" errors from previous server instances
    # ========================================================================
    logger.info("🧹 Cleaning up session lock files...")
    cleanup_session_locks()

    # ========================================================================
    # Initialize GlobalConnectionManager FIRST (shared TelegramClient pool)
    # This MUST be done before InboxManager so both systems use the same pool
    # ========================================================================
    logger.info("🔧 Initializing GlobalConnectionManager (shared connection pool)...")
    global_conn_manager = GlobalConnectionManager.get_instance(socketio)
    logger.info("✅ GlobalConnectionManager initialized - all components will share connections")

    # Initialize and start inbox manager (persistent Telegram connections)
    # Use a list to allow modification from nested function (avoids nonlocal issues)
    inbox_loop_ref = [None]

    try:
        # Pass the GlobalConnectionManager to InboxManager
        inbox_manager = InboxManager(socketio, conn_manager=global_conn_manager)

        def start_inbox_manager():
            """Start inbox manager in its own asyncio event loop."""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            inbox_loop_ref[0] = loop  # Store reference for graceful shutdown

            # Set the event loop on GlobalConnectionManager
            global_conn_manager.set_loop(loop)

            # Start the manager
            loop.run_until_complete(inbox_manager.start())

            # Auto-connect all active accounts
            logger.info("📱 Auto-connecting all active accounts to inbox...")
            loop.run_until_complete(inbox_manager.connect_all_active_accounts())

            # Run forever to keep event handlers active
            try:
                loop.run_forever()
            except Exception as e:
                logger.error(f"❌ Inbox manager loop error: {e}")
            finally:
                loop.run_until_complete(inbox_manager.stop())
                loop.close()

        inbox_manager_thread = threading.Thread(
            target=start_inbox_manager,
            daemon=True,
            name="inbox_manager"
        )
        inbox_manager_thread.start()
        logger.info("✅ Inbox manager started - auto-connecting accounts in background")
        logger.info("🔗 Operations will now share connections with inbox (no more file locking!)")

    except Exception as e:
        logger.warning(f"⚠️  Could not start inbox manager: {str(e)}")
        inbox_manager = None
        inbox_manager_thread = None

    # Graceful shutdown handler
    def graceful_shutdown(signum=None, frame=None):
        """Handle graceful shutdown of inbox manager and connections."""
        logger.info("🛑 Received shutdown signal, cleaning up...")

        inbox_loop = inbox_loop_ref[0]

        if inbox_manager is not None and inbox_loop is not None:
            try:
                # Schedule stop() coroutine in the inbox manager's event loop
                future = asyncio.run_coroutine_threadsafe(
                    inbox_manager.stop(),
                    inbox_loop
                )
                # Wait up to 10 seconds for cleanup
                future.result(timeout=10)
                logger.info("✅ Inbox manager stopped gracefully")
            except Exception as e:
                logger.warning(f"⚠️ Error during inbox manager shutdown: {e}")

        # Stop the inbox manager's event loop
        if inbox_loop is not None and inbox_loop.is_running():
            inbox_loop.call_soon_threadsafe(inbox_loop.stop)

        logger.info("✅ Graceful shutdown complete")

    # Register shutdown handlers
    atexit.register(graceful_shutdown)

    # Handle SIGINT (Ctrl+C) and SIGTERM
    try:
        signal.signal(signal.SIGINT, graceful_shutdown)
        signal.signal(signal.SIGTERM, graceful_shutdown)
    except Exception as e:
        logger.debug(f"Could not register signal handlers: {e}")

    # Start background DB flush worker thread
    _start_db_flush_thread()

    # Start Flask app with SocketIO
    socketio.run(
        app,
        host='localhost',
        port=5000,
        debug=True,
        use_reloader=False,  # Disable reloader to prevent double initialization
        allow_unsafe_werkzeug=True  # Required for development mode with SocketIO
    )
