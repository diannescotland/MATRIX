"""
Account Manager - Database operations for multi-account management
Manages Telegram account credentials and session information in SQLite database
"""

import sqlite3
import asyncio
import logging
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime

from telethon.errors import SessionPasswordNeededError

# Import TGClient for StringSession-based connections
from tg_client import TGClient

# Database path (root level, one level up from backend/)
DB_PATH = Path(__file__).parent.parent / "accounts.db"

logger = logging.getLogger(__name__)


def normalize_phone(phone: str) -> str:
    """
    Normalize phone number to consistent format (digits only, no + prefix).
    All phone numbers in the database should be stored without + prefix.

    Args:
        phone: Phone number in any format (+1234567890, 1234567890, +1-234-567-890)

    Returns:
        Normalized phone number (digits only, e.g., "1234567890")
    """
    if not phone:
        return ""
    # Remove all non-digit characters (keeps only 0-9)
    return ''.join(c for c in str(phone) if c.isdigit())


def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')  # Enable Write-Ahead Logging for concurrent access
    return conn


def init_database():
    """Initialize the accounts database"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS accounts (
                phone TEXT PRIMARY KEY,
                name TEXT,
                api_id INTEGER,
                api_hash TEXT,
                session_path TEXT,
                status TEXT DEFAULT 'active',
                is_default INTEGER DEFAULT 0,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used TIMESTAMP
            )
        ''')
        
        # Add is_default column if it doesn't exist (for existing databases)
        try:
            cursor.execute('ALTER TABLE accounts ADD COLUMN is_default INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Add proxy column if it doesn't exist (for existing databases)
        try:
            cursor.execute('ALTER TABLE accounts ADD COLUMN proxy TEXT')
        except sqlite3.OperationalError:
            pass  # Column already exists

        conn.commit()
        conn.close()
        logger.info("✅ Database initialized successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Error initializing database: {str(e)}")
        return False


def add_account(phone: str, name: str = None, api_id: int = None, api_hash: str = None,
                session_path: str = None, notes: str = None, status: str = 'active',
                proxy: str = None) -> bool:
    """
    Add a new account to the database

    Args:
        phone: Phone number (primary key) - will be normalized to digits only
        name: Account name/alias
        api_id: Telegram API ID
        api_hash: Telegram API Hash
        session_path: Path to session file
        notes: Optional notes
        status: Account status (default: 'active')
        proxy: Proxy URL in format "http://ip:port" (optional)

    Returns:
        True if added successfully, False if account already exists
    """
    try:
        # Normalize phone number to prevent duplicates (+15803592485 vs 15803592485)
        clean_phone = normalize_phone(phone)
        if not clean_phone:
            logger.error("❌ Invalid phone number provided")
            return False

        conn = get_db_connection()
        cursor = conn.cursor()

        # Check if account already exists (using normalized phone)
        cursor.execute('SELECT phone FROM accounts WHERE phone = ?', (clean_phone,))
        if cursor.fetchone():
            conn.close()
            logger.warning(f"⚠️ Account already exists: {clean_phone}")
            return False

        cursor.execute('''
            INSERT INTO accounts (phone, name, api_id, api_hash, session_path, status, notes, proxy)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (clean_phone, name, api_id, api_hash, session_path, status, notes, proxy))

        conn.commit()
        conn.close()
        logger.info(f"✅ Added account: {clean_phone}" + (f" with proxy: {proxy}" if proxy else ""))
        return True
    except Exception as e:
        logger.error(f"❌ Error adding account: {str(e)}")
        return False


def get_account_by_phone(phone: str) -> Optional[Dict]:
    """Get account by phone number (normalizes phone before query)"""
    try:
        # Normalize phone to match stored format
        clean_phone = normalize_phone(phone)
        if not clean_phone:
            return None

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM accounts WHERE phone = ?', (clean_phone,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return dict(row)
        return None
    except Exception as e:
        logger.error(f"❌ Error getting account: {str(e)}")
        return None


def get_all_accounts() -> List[Dict]:
    """Get all accounts"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM accounts ORDER BY created_at DESC')
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"❌ Error getting accounts: {str(e)}")
        return []


def get_active_accounts() -> List[Dict]:
    """Get only active accounts"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM accounts WHERE status = ? ORDER BY created_at DESC', ('active',))
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"❌ Error getting active accounts: {str(e)}")
        return []


def update_account_status(phone: str, status: str) -> bool:
    """Update account status (normalizes phone before query)"""
    try:
        clean_phone = normalize_phone(phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('UPDATE accounts SET status = ? WHERE phone = ?', (status, clean_phone))
        conn.commit()
        conn.close()

        if cursor.rowcount > 0:
            logger.info(f"✅ Updated account status: {clean_phone} -> {status}")
            return True
        return False
    except Exception as e:
        logger.error(f"❌ Error updating account status: {str(e)}")
        return False


def update_account_last_used(phone: str) -> bool:
    """Update account last used timestamp (normalizes phone before query)"""
    try:
        clean_phone = normalize_phone(phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('UPDATE accounts SET last_used = CURRENT_TIMESTAMP WHERE phone = ?', (clean_phone,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"❌ Error updating last used: {str(e)}")
        return False


def update_account_proxy(phone: str, proxy: str = None) -> Tuple[bool, str]:
    """
    Update proxy for an account and invalidate session (force re-authentication).

    When proxy is changed, the session file is deleted to force the user to
    re-authenticate through the new proxy.

    Args:
        phone: Phone number of the account
        proxy: Proxy URL in format "http://ip:port" or None to remove proxy

    Returns:
        Tuple of (success: bool, message: str)
    """
    try:
        clean_phone = normalize_phone(phone)
        if not clean_phone:
            return False, "Invalid phone number"

        conn = get_db_connection()
        cursor = conn.cursor()

        # Get current account info (including session path)
        cursor.execute('SELECT session_path, proxy FROM accounts WHERE phone = ?', (clean_phone,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return False, f"Account {clean_phone} not found"

        old_proxy = row['proxy']
        session_path = row['session_path']

        # Update proxy in database
        cursor.execute('UPDATE accounts SET proxy = ? WHERE phone = ?', (proxy, clean_phone))
        conn.commit()
        conn.close()

        # Delete session files to force re-authentication
        sessions_deleted = 0
        if session_path:
            sessions_deleted = _cleanup_session_files(session_path, clean_phone)
        else:
            # Try default session path
            sessions_dir = Path(__file__).parent.parent / "sessions"
            default_session_path = str(sessions_dir / f"session_{clean_phone}")
            sessions_deleted = _cleanup_session_files(default_session_path, clean_phone)

        proxy_msg = f"set to {proxy}" if proxy else "removed"
        logger.info(f"✅ Proxy {proxy_msg} for account {clean_phone}, {sessions_deleted} session file(s) deleted")

        return True, f"Proxy {proxy_msg}. Session invalidated - please re-authenticate."

    except Exception as e:
        logger.error(f"❌ Error updating proxy: {str(e)}")
        return False, f"Error updating proxy: {str(e)}"


def delete_account(phone: str) -> bool:
    """
    Delete an account and clean up associated session files.

    This will:
    1. Get the session path from the database
    2. Delete the account record from the database
    3. Delete all associated session files (.session, -journal, -wal, -shm)

    Args:
        phone: Phone number of the account to delete

    Returns:
        True if deleted successfully, False otherwise
    """
    try:
        # Normalize phone number
        clean_phone = normalize_phone(phone)
        if not clean_phone:
            logger.error("❌ Invalid phone number provided for deletion")
            return False

        conn = get_db_connection()
        cursor = conn.cursor()

        # Get session path before deleting
        cursor.execute('SELECT session_path FROM accounts WHERE phone = ?', (clean_phone,))
        row = cursor.fetchone()
        session_path = row['session_path'] if row else None

        # Delete from database
        cursor.execute('DELETE FROM accounts WHERE phone = ?', (clean_phone,))
        conn.commit()
        deleted_from_db = cursor.rowcount > 0
        conn.close()

        if deleted_from_db:
            logger.info(f"✅ Deleted account from database: {clean_phone}")

            # Clean up session files
            if session_path:
                session_files_deleted = _cleanup_session_files(session_path, clean_phone)
                if session_files_deleted > 0:
                    logger.info(f"✅ Cleaned up {session_files_deleted} session file(s)")
            else:
                # Try default session path
                sessions_dir = Path(__file__).parent.parent / "sessions"
                default_session_path = str(sessions_dir / f"session_{clean_phone}")
                session_files_deleted = _cleanup_session_files(default_session_path, clean_phone)
                if session_files_deleted > 0:
                    logger.info(f"✅ Cleaned up {session_files_deleted} session file(s) from default location")

            return True
        else:
            logger.warning(f"⚠️ Account not found: {clean_phone}")
            return False
    except Exception as e:
        logger.error(f"❌ Error deleting account: {str(e)}")
        return False


def _cleanup_session_files(session_path: str, phone: str) -> int:
    """
    Clean up session files for a deleted account.

    With StringSession, only the .session file exists (plain text).
    Also cleans up any stale SQLite lock files from old format.

    Args:
        session_path: Base path to session file (without .session extension)
        phone: Phone number for fallback path construction

    Returns:
        Number of files successfully deleted
    """
    files_deleted = 0
    sessions_dir = Path(__file__).parent.parent / "sessions"

    # Primary session file (StringSession format - plain text)
    session_file = sessions_dir / f"session_{phone}.session"
    if session_file.exists():
        try:
            session_file.unlink()
            logger.debug(f"🗑️ Deleted session file: {session_file}")
            files_deleted += 1
        except Exception as e:
            logger.warning(f"⚠️ Could not delete {session_file}: {e}")

    # Clean up any stale SQLite lock files (from old format)
    for ext in ['-journal', '-wal', '-shm']:
        lock_file = sessions_dir / f"session_{phone}.session{ext}"
        if lock_file.exists():
            try:
                lock_file.unlink()
                logger.debug(f"🗑️ Deleted stale lock file: {lock_file}")
                files_deleted += 1
            except Exception as e:
                logger.warning(f"⚠️ Could not delete {lock_file}: {e}")

    return files_deleted


async def validate_account(phone: str, api_id: int = None, api_hash: str = None) -> Tuple[bool, str]:
    """
    Validate an account by attempting to connect
    
    Args:
        phone: Phone number
        api_id: Telegram API ID (optional, uses from database if not provided)
        api_hash: Telegram API Hash (optional, uses from database if not provided)
    
    Returns:
        Tuple of (is_valid: bool, message: str)
    """
    try:
        # Get account from database if credentials not provided
        if not api_id or not api_hash:
            account = get_account_by_phone(phone)
            if not account:
                return False, "Account not found in database"
            api_id = api_id or account.get('api_id')
            api_hash = api_hash or account.get('api_hash')
        
        if not api_id or not api_hash:
            return False, "API credentials not available"

        # Generate session name
        clean_phone = phone.replace('+', '').replace('-', '').replace(' ', '')
        session_name = f"session_{clean_phone}"

        # Try to connect using TGClient (StringSession-based)
        tg_client = TGClient(session_name, api_id, api_hash, force_init=True)

        try:
            await tg_client.connect()

            if await tg_client.is_authorized():
                me = await tg_client.get_me()
                await tg_client.disconnect()
                return True, f"Account validated: {me.first_name}"
            else:
                await tg_client.disconnect()
                return False, "Session expired - needs re-authentication"
        except SessionPasswordNeededError:
            await tg_client.disconnect()
            return False, "Account requires 2FA password"
        except Exception as e:
            try:
                await tg_client.disconnect()
            except:
                pass
            return False, f"Validation error: {str(e)}"

    except Exception as e:
        return False, f"Error validating account: {str(e)}"


async def validate_accounts_batch(phones: List[str]) -> Dict[str, Tuple[bool, str]]:
    """
    Validate multiple accounts
    
    Args:
        phones: List of phone numbers
    
    Returns:
        Dict mapping phone to (is_valid, message) tuple
    """
    results = {}
    for phone in phones:
        is_valid, message = await validate_account(phone)
        results[phone] = (is_valid, message)
    return results


def get_default_account() -> Optional[Dict]:
    """Get the default account (where is_default = 1)"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM accounts WHERE is_default = 1 LIMIT 1')
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return dict(row)
        return None
    except Exception as e:
        logger.error(f"❌ Error getting default account: {str(e)}")
        return None


def set_default_account(phone: str) -> bool:
    """
    Set an account as the default account (normalizes phone before query)

    This will:
    1. Clear all other accounts' is_default flag
    2. Set the specified account's is_default flag to 1

    Args:
        phone: Phone number of the account to set as default

    Returns:
        True if successful, False otherwise
    """
    try:
        clean_phone = normalize_phone(phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        # First, clear all default flags
        cursor.execute('UPDATE accounts SET is_default = 0')

        # Then set the specified account as default
        cursor.execute('UPDATE accounts SET is_default = 1 WHERE phone = ?', (clean_phone,))

        conn.commit()
        conn.close()

        if cursor.rowcount > 0:
            logger.info(f"✅ Set default account: {clean_phone}")
            return True
        else:
            logger.warning(f"⚠️  Account {clean_phone} not found in database")
            return False
    except Exception as e:
        logger.error(f"❌ Error setting default account: {str(e)}")
        return False


def init_backups_table():
    """Initialize the backups table for tracking backup history"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL,
                contacts_count INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (phone) REFERENCES accounts(phone)
            )
        ''')

        conn.commit()
        conn.close()
        logger.info("✅ Backups table initialized successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Error initializing backups table: {str(e)}")
        return False


def log_backup(phone: str, filename: str, filepath: str, contacts_count: int) -> bool:
    """
    Log a backup operation to the database

    Args:
        phone: Phone number of account (will be normalized)
        filename: Name of backup file
        filepath: Full path to backup file
        contacts_count: Number of contacts in backup

    Returns:
        True if logged successfully, False otherwise
    """
    try:
        # Normalize phone to match accounts table format
        clean_phone = normalize_phone(phone)

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO backups (phone, filename, filepath, contacts_count)
            VALUES (?, ?, ?, ?)
        ''', (clean_phone, filename, filepath, contacts_count))

        conn.commit()
        conn.close()
        logger.info(f"✅ Logged backup: {filename} ({contacts_count} contacts)")
        return True
    except Exception as e:
        logger.error(f"❌ Error logging backup: {str(e)}")
        return False


def get_backup_history(phone: str = None, limit: int = 10) -> List[Dict]:
    """
    Get backup history

    Args:
        phone: Optional phone number to filter by
        limit: Maximum number of backups to return

    Returns:
        List of backup records as dictionaries
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        if phone:
            cursor.execute('''
                SELECT id, phone, filename, filepath, contacts_count, created_at
                FROM backups
                WHERE phone = ?
                ORDER BY created_at DESC
                LIMIT ?
            ''', (phone, limit))
        else:
            cursor.execute('''
                SELECT id, phone, filename, filepath, contacts_count, created_at
                FROM backups
                ORDER BY created_at DESC
                LIMIT ?
            ''', (limit,))

        rows = cursor.fetchall()
        conn.close()

        backups = []
        for row in rows:
            backups.append({
                'id': row['id'],
                'phone': row['phone'],
                'filename': row['filename'],
                'filepath': row['filepath'],
                'contacts_count': row['contacts_count'],
                'created_at': row['created_at']
            })

        return backups
    except Exception as e:
        logger.error(f"❌ Error getting backup history: {str(e)}")
        return []


# ============================================================================
# OPERATIONS DATABASE FUNCTIONS
# ============================================================================

def init_operations_tables():
    """Initialize the operations-related tables for tracking operations history"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Main operations table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS operations (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                phones TEXT NOT NULL,
                params TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                total_progress INTEGER DEFAULT 0,
                total_items INTEGER DEFAULT 0,
                results TEXT,
                error TEXT
            )
        ''')

        # Per-account progress table (updated frequently)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS operation_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL,
                phone TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                progress INTEGER DEFAULT 0,
                total INTEGER DEFAULT 0,
                message TEXT,
                error TEXT,
                stats TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (operation_id) REFERENCES operations(id) ON DELETE CASCADE,
                UNIQUE(operation_id, phone)
            )
        ''')

        # Operation logs table (append-only)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS operation_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL,
                phone TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                level TEXT NOT NULL DEFAULT 'info',
                message TEXT NOT NULL,
                FOREIGN KEY (operation_id) REFERENCES operations(id) ON DELETE CASCADE
            )
        ''')

        # Create indexes for efficient querying
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_operations_status ON operations(status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_operations_created ON operations(created_at DESC)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_operation_accounts_opid ON operation_accounts(operation_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_operation_logs_opid ON operation_logs(operation_id)')

        conn.commit()
        conn.close()
        logger.info("✅ Operations tables initialized successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Error initializing operations tables: {str(e)}")
        return False


def db_create_operation(operation_id: str, operation_type: str, phones: List[str],
                        params: Dict = None) -> bool:
    """
    Create a new operation in the database

    Args:
        operation_id: Unique operation ID (8-char UUID)
        operation_type: Type of operation (import_devs, import_kols, scan, backup, folders)
        phones: List of phone numbers involved
        params: Operation parameters (will be JSON encoded)

    Returns:
        True if created successfully, False otherwise
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Insert main operation record
        cursor.execute('''
            INSERT INTO operations (id, type, phones, params, status)
            VALUES (?, ?, ?, ?, 'pending')
        ''', (operation_id, operation_type, json.dumps(phones), json.dumps(params or {})))

        # Create account entries for each phone
        for phone in phones:
            clean_phone = normalize_phone(phone)
            cursor.execute('''
                INSERT INTO operation_accounts (operation_id, phone, status)
                VALUES (?, ?, 'pending')
            ''', (operation_id, clean_phone))

        conn.commit()
        conn.close()
        logger.debug(f"✅ Created operation in DB: {operation_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Error creating operation in DB: {str(e)}")
        return False


def db_get_operation(operation_id: str) -> Optional[Dict[str, Any]]:
    """
    Get operation with all account data and recent logs

    Args:
        operation_id: Operation ID to retrieve

    Returns:
        Full operation dictionary with accounts and logs, or None if not found
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Get main operation record
        cursor.execute('SELECT * FROM operations WHERE id = ?', (operation_id,))
        op_row = cursor.fetchone()
        if not op_row:
            conn.close()
            return None

        op = dict(op_row)
        op['phones'] = json.loads(op['phones']) if op['phones'] else []
        op['params'] = json.loads(op['params']) if op['params'] else {}
        op['results'] = json.loads(op['results']) if op['results'] else {}

        # Get account data
        cursor.execute('''
            SELECT * FROM operation_accounts WHERE operation_id = ?
        ''', (operation_id,))
        accounts = {}
        for row in cursor.fetchall():
            acc = dict(row)
            acc['stats'] = json.loads(acc['stats']) if acc['stats'] else {}
            acc['logs'] = []  # Will be populated below
            accounts[acc['phone']] = acc
        op['accounts'] = accounts

        # Get recent logs (last 200 per operation)
        cursor.execute('''
            SELECT phone, timestamp, level, message
            FROM operation_logs
            WHERE operation_id = ?
            ORDER BY timestamp DESC
            LIMIT 200
        ''', (operation_id,))
        logs = [dict(row) for row in cursor.fetchall()]
        logs.reverse()  # Oldest first

        # Distribute logs to accounts
        for log in logs:
            phone = log['phone']
            if phone in accounts:
                accounts[phone]['logs'].append({
                    'timestamp': log['timestamp'],
                    'level': log['level'],
                    'message': log['message']
                })

        conn.close()
        return op
    except Exception as e:
        logger.error(f"❌ Error getting operation from DB: {str(e)}")
        return None


def db_update_account_progress(operation_id: str, phone: str, progress: int,
                                total: int, status: str, message: str = '',
                                error: str = None, stats: Dict = None) -> bool:
    """
    Update progress for an account in the database

    Args:
        operation_id: Operation ID
        phone: Account phone number
        progress: Current progress count
        total: Total items to process
        status: Current status (pending, running, completed, error)
        message: Status message
        error: Error message if any
        stats: Statistics dictionary (added, skipped, failed, etc.)

    Returns:
        True if updated successfully, False otherwise
    """
    try:
        clean_phone = normalize_phone(phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        # Update account progress
        cursor.execute('''
            UPDATE operation_accounts
            SET progress = ?, total = ?, status = ?, message = ?,
                error = ?, stats = ?, updated_at = CURRENT_TIMESTAMP
            WHERE operation_id = ? AND phone = ?
        ''', (progress, total, status, message, error,
              json.dumps(stats) if stats else None, operation_id, clean_phone))

        # Update operation aggregate progress and status
        cursor.execute('''
            UPDATE operations SET
                total_progress = (SELECT COALESCE(SUM(progress), 0) FROM operation_accounts WHERE operation_id = ?),
                total_items = (SELECT COALESCE(SUM(total), 0) FROM operation_accounts WHERE operation_id = ?),
                started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                status = CASE
                    WHEN (SELECT COUNT(*) FROM operation_accounts WHERE operation_id = ? AND status = 'running') > 0 THEN 'running'
                    WHEN (SELECT COUNT(*) FROM operation_accounts WHERE operation_id = ? AND status IN ('completed', 'error')) =
                         (SELECT COUNT(*) FROM operation_accounts WHERE operation_id = ?) THEN 'completed'
                    ELSE status
                END
            WHERE id = ?
        ''', (operation_id, operation_id, operation_id, operation_id, operation_id, operation_id))

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"❌ Error updating account progress: {str(e)}")
        return False


def db_add_operation_log(operation_id: str, phone: str, message: str,
                         level: str = 'info') -> bool:
    """
    Add a log entry for an operation

    Args:
        operation_id: Operation ID
        phone: Account phone number
        message: Log message
        level: Log level (info, warning, error, success)

    Returns:
        True if added successfully, False otherwise
    """
    try:
        clean_phone = normalize_phone(phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO operation_logs (operation_id, phone, level, message)
            VALUES (?, ?, ?, ?)
        ''', (operation_id, clean_phone, level, message))

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"❌ Error adding operation log: {str(e)}")
        return False


def db_complete_operation(operation_id: str, results: Dict = None,
                          error: str = None) -> bool:
    """
    Mark operation as completed in the database

    Args:
        operation_id: Operation ID
        results: Final results dictionary
        error: Error message if operation failed

    Returns:
        True if updated successfully, False otherwise
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        status = 'error' if error else 'completed'
        cursor.execute('''
            UPDATE operations
            SET status = ?, completed_at = CURRENT_TIMESTAMP,
                results = ?, error = ?
            WHERE id = ?
        ''', (status, json.dumps(results) if results else None, error, operation_id))

        conn.commit()
        conn.close()
        logger.debug(f"✅ Completed operation in DB: {operation_id} ({status})")
        return True
    except Exception as e:
        logger.error(f"❌ Error completing operation in DB: {str(e)}")
        return False


def db_get_active_operations() -> List[Dict[str, Any]]:
    """
    Get all operations that are pending or running

    Returns:
        List of active operation summaries
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, type, phones, status, created_at, started_at,
                   total_progress, total_items
            FROM operations
            WHERE status IN ('pending', 'running')
            ORDER BY created_at DESC
        ''')

        ops = []
        for row in cursor.fetchall():
            op = dict(row)
            op['phones'] = json.loads(op['phones']) if op['phones'] else []
            ops.append(op)

        conn.close()
        return ops
    except Exception as e:
        logger.error(f"❌ Error getting active operations: {str(e)}")
        return []


def db_get_recent_operations(limit: int = 20) -> List[Dict[str, Any]]:
    """
    Get recent operations for history view

    Args:
        limit: Maximum number of operations to return

    Returns:
        List of recent operation summaries
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, type, phones, status, created_at, completed_at,
                   total_progress, total_items, error
            FROM operations
            ORDER BY created_at DESC
            LIMIT ?
        ''', (limit,))

        ops = []
        for row in cursor.fetchall():
            op = dict(row)
            op['phones'] = json.loads(op['phones']) if op['phones'] else []
            ops.append(op)

        conn.close()
        return ops
    except Exception as e:
        logger.error(f"❌ Error getting recent operations: {str(e)}")
        return []


def db_update_operation_status(operation_id: str, status: str) -> bool:
    """
    Update operation status

    Args:
        operation_id: Operation ID
        status: New status (pending, running, completed, error)

    Returns:
        True if updated successfully, False otherwise
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE operations SET status = ? WHERE id = ?
        ''', (status, operation_id))

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"❌ Error updating operation status: {str(e)}")
        return False


# ============================================================================
# INBOX MANAGEMENT TABLES
# Real-time message inbox system with persistent connections
# ============================================================================

def init_inbox_tables():
    """
    Initialize inbox management tables for real-time message tracking.

    Tables created:
    - inbox_conversations: Track all private chats per account
    - inbox_messages: Full message history
    - inbox_events: Event log for notifications and audit
    - inbox_campaigns: Track outreach campaigns for metrics
    - inbox_connection_state: Track connection status per account
    - inbox_dm_history: Track sent DMs for duplicate detection & rate limiting
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # ============================================================================
        # CONVERSATIONS: Track all private chats per account
        # ============================================================================
        cursor.execute('''
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
            )
        ''')

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_inbox_conv_account ON inbox_conversations(account_phone)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_inbox_conv_matrix ON inbox_conversations(is_matrix_contact)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_inbox_conv_campaign ON inbox_conversations(campaign_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_inbox_conv_unread ON inbox_conversations(account_phone, unread_count)')

        # ============================================================================
        # MESSAGES: Full message history
        # ============================================================================
        cursor.execute('''
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
                deleted_at TIMESTAMP,

                -- Read status (outgoing only)
                is_read BOOLEAN DEFAULT FALSE,
                read_at TIMESTAMP,

                -- Sync metadata
                synced_via TEXT,                          -- 'event', 'dialog', 'backfill'

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                UNIQUE(account_phone, peer_id, msg_id)
            )
        ''')

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_inbox_msg_conv ON inbox_messages(account_phone, peer_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_inbox_msg_date ON inbox_messages(account_phone, peer_id, date DESC)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_inbox_msg_outgoing ON inbox_messages(account_phone, is_outgoing, is_read)')

        # ============================================================================
        # EVENTS: Event log for notifications and audit
        # ============================================================================
        cursor.execute('''
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
            )
        ''')

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_inbox_events_type ON inbox_events(event_type)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_inbox_events_account ON inbox_events(account_phone, created_at DESC)')

        # ============================================================================
        # CAMPAIGNS: Track outreach campaigns for metrics
        # ============================================================================
        cursor.execute('''
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
            )
        ''')

        # ============================================================================
        # CONNECTION_STATE: Track connection status per account
        # ============================================================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS inbox_connection_state (
                account_phone TEXT PRIMARY KEY,

                is_connected BOOLEAN DEFAULT FALSE,
                connected_at TIMESTAMP,
                last_disconnect_at TIMESTAMP,
                reconnect_attempts INTEGER DEFAULT 0,
                error TEXT,
                state TEXT DEFAULT 'disconnected',

                last_dialog_sync TIMESTAMP,
                last_full_sync TIMESTAMP,
                dialogs_count INTEGER DEFAULT 0,
                messages_count INTEGER DEFAULT 0,

                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Add error and state columns if they don't exist (migration)
        try:
            cursor.execute('ALTER TABLE inbox_connection_state ADD COLUMN error TEXT')
        except sqlite3.OperationalError:
            pass  # Column already exists
        try:
            cursor.execute("ALTER TABLE inbox_connection_state ADD COLUMN state TEXT DEFAULT 'disconnected'")
        except sqlite3.OperationalError:
            pass  # Column already exists

        # ============================================================================
        # DM_HISTORY: Track sent DMs for duplicate detection & rate limiting
        # ============================================================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS inbox_dm_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_phone TEXT NOT NULL,
                peer_id INTEGER NOT NULL,
                campaign_id TEXT,

                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                msg_id INTEGER,

                UNIQUE(account_phone, peer_id, campaign_id)
            )
        ''')

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_dm_history_account ON inbox_dm_history(account_phone)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_dm_history_sent ON inbox_dm_history(account_phone, sent_at DESC)')

        conn.commit()
        conn.close()
        logger.info("✅ Inbox tables initialized successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Error initializing inbox tables: {str(e)}")
        return False


# ============================================================================
# INBOX CONVERSATION CRUD FUNCTIONS
# ============================================================================

def inbox_get_or_create_conversation(account_phone: str, peer_id: int,
                                      username: str = None, first_name: str = None,
                                      last_name: str = None, access_hash: int = None) -> Optional[Dict]:
    """
    Get existing conversation or create a new one.

    Args:
        account_phone: Account phone number
        peer_id: Telegram user ID
        username: Optional username
        first_name: Optional first name
        last_name: Optional last name
        access_hash: Optional access hash for the user

    Returns:
        Conversation dict or None on error
    """
    try:
        clean_phone = normalize_phone(account_phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        # Try to get existing
        cursor.execute('''
            SELECT * FROM inbox_conversations
            WHERE account_phone = ? AND peer_id = ?
        ''', (clean_phone, peer_id))
        row = cursor.fetchone()

        if row:
            # Update peer info if provided
            if username or first_name or last_name:
                cursor.execute('''
                    UPDATE inbox_conversations
                    SET username = COALESCE(?, username),
                        first_name = COALESCE(?, first_name),
                        last_name = COALESCE(?, last_name),
                        access_hash = COALESCE(?, access_hash),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE account_phone = ? AND peer_id = ?
                ''', (username, first_name, last_name, access_hash, clean_phone, peer_id))
                conn.commit()
                # Re-fetch updated row
                cursor.execute('SELECT * FROM inbox_conversations WHERE account_phone = ? AND peer_id = ?',
                             (clean_phone, peer_id))
                row = cursor.fetchone()
            conn.close()
            return dict(row)

        # Create new conversation
        cursor.execute('''
            INSERT INTO inbox_conversations
            (account_phone, peer_id, username, first_name, last_name, access_hash)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (clean_phone, peer_id, username, first_name, last_name, access_hash))
        conv_id = cursor.lastrowid
        conn.commit()

        cursor.execute('SELECT * FROM inbox_conversations WHERE id = ?', (conv_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    except Exception as e:
        logger.error(f"❌ Error get/create conversation: {str(e)}")
        return None


def inbox_update_conversation(account_phone: str, peer_id: int, **updates) -> bool:
    """
    Update conversation fields.

    Args:
        account_phone: Account phone number
        peer_id: Telegram user ID
        **updates: Fields to update (last_msg_id, last_msg_text, unread_count, etc.)

    Returns:
        True if updated, False otherwise
    """
    try:
        if not updates:
            return True

        clean_phone = normalize_phone(account_phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        # Build SET clause
        set_parts = []
        values = []
        for key, value in updates.items():
            set_parts.append(f"{key} = ?")
            values.append(value)
        set_parts.append("updated_at = CURRENT_TIMESTAMP")

        values.extend([clean_phone, peer_id])

        cursor.execute(f'''
            UPDATE inbox_conversations
            SET {", ".join(set_parts)}
            WHERE account_phone = ? AND peer_id = ?
        ''', values)

        conn.commit()
        updated = cursor.rowcount > 0
        conn.close()
        return updated

    except Exception as e:
        logger.error(f"❌ Error updating conversation: {str(e)}")
        return False


def inbox_get_conversations(account_phone: str, limit: int = 50, offset: int = 0,
                            unread_only: bool = False, matrix_only: bool = False) -> List[Dict]:
    """
    Get conversations for an account.

    Args:
        account_phone: Account phone number
        limit: Maximum conversations to return
        offset: Offset for pagination
        unread_only: Only return conversations with unread messages
        matrix_only: Only return MATRIX-tagged contacts

    Returns:
        List of conversation dicts
    """
    try:
        clean_phone = normalize_phone(account_phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        where_parts = ["account_phone = ?"]
        params = [clean_phone]

        if unread_only:
            where_parts.append("unread_count > 0")
        if matrix_only:
            where_parts.append("is_matrix_contact = TRUE")

        query = f'''
            SELECT * FROM inbox_conversations
            WHERE {" AND ".join(where_parts)}
            ORDER BY last_msg_date DESC NULLS LAST
            LIMIT ? OFFSET ?
        '''
        params.extend([limit, offset])

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    except Exception as e:
        logger.error(f"❌ Error getting conversations: {str(e)}")
        return []


# ============================================================================
# INBOX MESSAGE CRUD FUNCTIONS
# ============================================================================

def inbox_insert_message(account_phone: str, peer_id: int, msg_id: int,
                         from_id: int, is_outgoing: bool, text: str,
                         date: datetime, reply_to_msg_id: int = None,
                         media_type: str = None, synced_via: str = 'event') -> bool:
    """
    Insert a new message (or ignore if duplicate).

    Args:
        account_phone: Account phone number
        peer_id: Telegram user ID (conversation partner)
        msg_id: Telegram message ID
        from_id: Sender's user ID
        is_outgoing: True if we sent it
        text: Message text
        date: Message timestamp
        reply_to_msg_id: Optional reply-to message ID
        media_type: Optional media type
        synced_via: How this message was synced ('event', 'dialog', 'backfill')

    Returns:
        True if inserted, False if duplicate or error
    """
    try:
        clean_phone = normalize_phone(account_phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT OR IGNORE INTO inbox_messages
            (account_phone, peer_id, msg_id, from_id, is_outgoing, text, date,
             reply_to_msg_id, media_type, synced_via)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (clean_phone, peer_id, msg_id, from_id, is_outgoing, text, date,
              reply_to_msg_id, media_type, synced_via))

        inserted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return inserted

    except Exception as e:
        logger.error(f"❌ Error inserting message: {str(e)}")
        return False


def inbox_get_messages(account_phone: str, peer_id: int, limit: int = 50,
                       before_msg_id: int = None) -> List[Dict]:
    """
    Get messages for a conversation.

    Args:
        account_phone: Account phone number
        peer_id: Telegram user ID
        limit: Maximum messages to return
        before_msg_id: Get messages older than this ID (for pagination)

    Returns:
        List of message dicts (oldest first)
    """
    try:
        clean_phone = normalize_phone(account_phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        if before_msg_id:
            cursor.execute('''
                SELECT * FROM inbox_messages
                WHERE account_phone = ? AND peer_id = ? AND msg_id < ?
                ORDER BY msg_id DESC
                LIMIT ?
            ''', (clean_phone, peer_id, before_msg_id, limit))
        else:
            cursor.execute('''
                SELECT * FROM inbox_messages
                WHERE account_phone = ? AND peer_id = ?
                ORDER BY msg_id DESC
                LIMIT ?
            ''', (clean_phone, peer_id, limit))

        rows = cursor.fetchall()
        conn.close()

        # Return oldest first
        messages = [dict(row) for row in rows]
        messages.reverse()
        return messages

    except Exception as e:
        logger.error(f"❌ Error getting messages: {str(e)}")
        return []


def inbox_mark_messages_read(account_phone: str, peer_id: int, max_msg_id: int) -> int:
    """
    Mark outgoing messages as read up to max_msg_id.

    Args:
        account_phone: Account phone number
        peer_id: Telegram user ID
        max_msg_id: Mark all messages with ID <= this as read

    Returns:
        Number of messages marked as read
    """
    try:
        clean_phone = normalize_phone(account_phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE inbox_messages
            SET is_read = TRUE, read_at = CURRENT_TIMESTAMP
            WHERE account_phone = ? AND peer_id = ? AND is_outgoing = TRUE
              AND msg_id <= ? AND is_read = FALSE
        ''', (clean_phone, peer_id, max_msg_id))

        read_count = cursor.rowcount

        # Also update conversation
        cursor.execute('''
            UPDATE inbox_conversations
            SET their_last_read_msg_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE account_phone = ? AND peer_id = ?
        ''', (max_msg_id, clean_phone, peer_id))

        conn.commit()
        conn.close()
        return read_count

    except Exception as e:
        logger.error(f"❌ Error marking messages read: {str(e)}")
        return 0


def inbox_soft_delete_messages(account_phone: str, peer_id: int, msg_ids: List[int]) -> int:
    """
    Soft-delete messages (mark is_deleted=TRUE).

    Args:
        account_phone: Account phone number
        peer_id: Telegram user ID
        msg_ids: List of message IDs to delete

    Returns:
        Number of messages deleted
    """
    try:
        if not msg_ids:
            return 0

        clean_phone = normalize_phone(account_phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        placeholders = ','.join('?' * len(msg_ids))
        params = [clean_phone, peer_id] + msg_ids

        cursor.execute(f'''
            UPDATE inbox_messages
            SET is_deleted = TRUE, deleted_at = CURRENT_TIMESTAMP,
                text = '[Message deleted]'
            WHERE account_phone = ? AND peer_id = ? AND msg_id IN ({placeholders})
        ''', params)

        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        return deleted_count

    except Exception as e:
        logger.error(f"❌ Error soft-deleting messages: {str(e)}")
        return 0


# ============================================================================
# INBOX CONNECTION STATE FUNCTIONS
# ============================================================================

def inbox_update_connection_state(account_phone: str, is_connected: bool,
                                   **extra_fields) -> bool:
    """
    Update connection state for an account.

    Args:
        account_phone: Account phone number
        is_connected: Connection status
        **extra_fields: Additional fields (dialogs_count, messages_count, etc.)

    Returns:
        True if updated
    """
    import time
    clean_phone = normalize_phone(account_phone)
    max_retries = 5
    retry_delay = 0.1  # Start with 100ms

    for attempt in range(max_retries):
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            # Upsert
            cursor.execute('''
                INSERT INTO inbox_connection_state (account_phone, is_connected, connected_at, updated_at)
                VALUES (?, ?, CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END, CURRENT_TIMESTAMP)
                ON CONFLICT(account_phone) DO UPDATE SET
                    is_connected = excluded.is_connected,
                    connected_at = CASE WHEN excluded.is_connected AND NOT is_connected THEN CURRENT_TIMESTAMP ELSE connected_at END,
                    last_disconnect_at = CASE WHEN NOT excluded.is_connected AND is_connected THEN CURRENT_TIMESTAMP ELSE last_disconnect_at END,
                    reconnect_attempts = CASE WHEN excluded.is_connected THEN 0 ELSE reconnect_attempts END,
                    updated_at = CURRENT_TIMESTAMP
            ''', (clean_phone, is_connected, is_connected))

            # Apply extra fields if provided
            if extra_fields:
                set_parts = []
                values = []
                for key, value in extra_fields.items():
                    set_parts.append(f"{key} = ?")
                    values.append(value)
                values.append(clean_phone)

                cursor.execute(f'''
                    UPDATE inbox_connection_state
                    SET {", ".join(set_parts)}, updated_at = CURRENT_TIMESTAMP
                    WHERE account_phone = ?
                ''', values)

            conn.commit()
            return True

        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and attempt < max_retries - 1:
                logger.warning(f"⚠️ Database locked, retrying ({attempt + 1}/{max_retries})...")
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
                continue
            logger.error(f"❌ Error updating connection state: {str(e)}")
            return False

        except Exception as e:
            logger.error(f"❌ Error updating connection state: {str(e)}")
            return False

        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass

    return False


def inbox_get_connection_states() -> List[Dict]:
    """Get connection states for all accounts."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM inbox_connection_state ORDER BY account_phone')
        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    except Exception as e:
        logger.error(f"❌ Error getting connection states: {str(e)}")
        return []


def inbox_increment_reconnect_attempts(account_phone: str) -> int:
    """Increment reconnect attempts counter and return new value."""
    try:
        clean_phone = normalize_phone(account_phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE inbox_connection_state
            SET reconnect_attempts = reconnect_attempts + 1, updated_at = CURRENT_TIMESTAMP
            WHERE account_phone = ?
        ''', (clean_phone,))

        cursor.execute('SELECT reconnect_attempts FROM inbox_connection_state WHERE account_phone = ?',
                      (clean_phone,))
        row = cursor.fetchone()
        conn.commit()
        conn.close()

        return row['reconnect_attempts'] if row else 1

    except Exception as e:
        logger.error(f"❌ Error incrementing reconnect attempts: {str(e)}")
        return 0


# ============================================================================
# INBOX DM HISTORY FUNCTIONS (for rate limiting)
# ============================================================================

def inbox_record_dm_sent(account_phone: str, peer_id: int, msg_id: int = None,
                         campaign_id: str = None) -> bool:
    """
    Record a sent DM for duplicate detection and rate limiting.

    Args:
        account_phone: Account phone number
        peer_id: Recipient's user ID
        msg_id: Optional message ID
        campaign_id: Optional campaign ID

    Returns:
        True if recorded (False if duplicate)
    """
    try:
        clean_phone = normalize_phone(account_phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT OR IGNORE INTO inbox_dm_history
            (account_phone, peer_id, campaign_id, msg_id)
            VALUES (?, ?, ?, ?)
        ''', (clean_phone, peer_id, campaign_id, msg_id))

        inserted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return inserted

    except Exception as e:
        logger.error(f"❌ Error recording DM: {str(e)}")
        return False


def inbox_check_dm_sent(account_phone: str, peer_id: int, campaign_id: str = None) -> bool:
    """
    Check if we already sent a DM to this user (for this campaign).

    Args:
        account_phone: Account phone number
        peer_id: Recipient's user ID
        campaign_id: Optional campaign ID (if None, checks any campaign)

    Returns:
        True if DM was already sent
    """
    try:
        clean_phone = normalize_phone(account_phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        if campaign_id:
            cursor.execute('''
                SELECT 1 FROM inbox_dm_history
                WHERE account_phone = ? AND peer_id = ? AND campaign_id = ?
            ''', (clean_phone, peer_id, campaign_id))
        else:
            cursor.execute('''
                SELECT 1 FROM inbox_dm_history
                WHERE account_phone = ? AND peer_id = ?
            ''', (clean_phone, peer_id))

        exists = cursor.fetchone() is not None
        conn.close()
        return exists

    except Exception as e:
        logger.error(f"❌ Error checking DM history: {str(e)}")
        return False


def inbox_get_dm_count_today(account_phone: str) -> int:
    """
    Get count of DMs sent today by this account (for rate limiting).

    Args:
        account_phone: Account phone number

    Returns:
        Number of DMs sent today
    """
    try:
        clean_phone = normalize_phone(account_phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT COUNT(*) as count FROM inbox_dm_history
            WHERE account_phone = ? AND sent_at >= date('now', 'start of day')
        ''', (clean_phone,))

        row = cursor.fetchone()
        conn.close()
        return row['count'] if row else 0

    except Exception as e:
        logger.error(f"❌ Error getting DM count: {str(e)}")
        return 0


# ============================================================================
# INBOX CAMPAIGNS FUNCTIONS
# ============================================================================

def inbox_ensure_campaign(campaign_id: str, name: str = None,
                          contact_type: str = None) -> bool:
    """
    Create campaign if it doesn't exist.

    Args:
        campaign_id: Unique campaign ID
        name: Campaign name (defaults to campaign_id)
        contact_type: Type of contacts ('dev', 'kol', 'mixed')

    Returns:
        True if created or already exists
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT OR IGNORE INTO inbox_campaigns (id, name, contact_type)
            VALUES (?, ?, ?)
        ''', (campaign_id, name or campaign_id, contact_type))

        conn.commit()
        conn.close()
        return True

    except Exception as e:
        logger.error(f"❌ Error ensuring campaign: {str(e)}")
        return False


def inbox_update_campaign_metrics(campaign_id: str) -> bool:
    """
    Recalculate campaign metrics from conversations.

    Args:
        campaign_id: Campaign ID to update

    Returns:
        True if updated
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Count metrics from conversations
        cursor.execute('''
            SELECT
                COUNT(*) as total_contacts,
                SUM(CASE WHEN last_msg_is_outgoing = TRUE THEN 1 ELSE 0 END) as total_reached,
                SUM(CASE WHEN contact_status = 'yellow' THEN 1 ELSE 0 END) as total_replies,
                SUM(CASE WHEN their_last_read_msg_id > 0 THEN 1 ELSE 0 END) as total_read
            FROM inbox_conversations
            WHERE campaign_id = ? AND is_matrix_contact = TRUE
        ''', (campaign_id,))

        row = cursor.fetchone()
        if row:
            total_contacts = row['total_contacts'] or 0
            total_reached = row['total_reached'] or 0
            total_replies = row['total_replies'] or 0
            total_read = row['total_read'] or 0

            reply_rate = (total_replies / total_reached * 100) if total_reached > 0 else 0
            read_rate = (total_read / total_reached * 100) if total_reached > 0 else 0

            cursor.execute('''
                UPDATE inbox_campaigns
                SET total_contacts = ?, total_reached = ?, total_replies = ?,
                    total_read = ?, reply_rate = ?, read_rate = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (total_contacts, total_reached, total_replies, total_read,
                  reply_rate, read_rate, campaign_id))

        conn.commit()
        conn.close()
        return True

    except Exception as e:
        logger.error(f"❌ Error updating campaign metrics: {str(e)}")
        return False


def inbox_get_campaign_metrics(campaign_id: str = None) -> List[Dict]:
    """
    Get campaign metrics.

    Args:
        campaign_id: Optional specific campaign (None for all)

    Returns:
        List of campaign metric dicts
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        if campaign_id:
            cursor.execute('SELECT * FROM inbox_campaigns WHERE id = ?', (campaign_id,))
        else:
            cursor.execute('SELECT * FROM inbox_campaigns ORDER BY created_at DESC')

        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    except Exception as e:
        logger.error(f"❌ Error getting campaign metrics: {str(e)}")
        return []


# ============================================================================
# INBOX EVENT LOG FUNCTIONS
# ============================================================================

def inbox_log_event(account_phone: str, peer_id: int, event_type: str,
                    event_data: Dict = None, msg_id: int = None,
                    campaign_id: str = None) -> bool:
    """
    Log an inbox event.

    Args:
        account_phone: Account phone number
        peer_id: Related user ID
        event_type: Type of event ('new_message', 'message_read', 'first_reply', etc.)
        event_data: Optional JSON-serializable event data
        msg_id: Optional related message ID
        campaign_id: Optional related campaign ID

    Returns:
        True if logged
    """
    try:
        clean_phone = normalize_phone(account_phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO inbox_events
            (account_phone, peer_id, event_type, event_data, msg_id, campaign_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (clean_phone, peer_id, event_type,
              json.dumps(event_data) if event_data else None,
              msg_id, campaign_id))

        conn.commit()
        conn.close()
        return True

    except Exception as e:
        logger.error(f"❌ Error logging event: {str(e)}")
        return False


def inbox_get_conversations_needing_backfill(account_phone: str) -> List[Dict]:
    """
    Get conversations that need message backfill.

    Args:
        account_phone: Account phone number

    Returns:
        List of conversation dicts with needs_backfill=True
    """
    try:
        clean_phone = normalize_phone(account_phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM inbox_conversations
            WHERE account_phone = ? AND needs_backfill = TRUE
        ''', (clean_phone,))

        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    except Exception as e:
        logger.error(f"❌ Error getting backfill conversations: {str(e)}")
        return []


# ============================================================================
# MATRIX CONTACT LINKING
# ============================================================================

def inbox_link_matrix_contact(account_phone: str, peer_id: int, username: str,
                               first_name: str, last_name: str, access_hash: int,
                               contact_type: str, campaign_id: str = None) -> bool:
    """
    Link a MATRIX-imported contact to inbox system.

    Called after successfully importing a contact via import_dev_contacts
    or import_kol_contacts. Creates or updates the inbox_conversation
    record with MATRIX metadata.

    Args:
        account_phone: Account phone number
        peer_id: Telegram user ID
        username: Telegram username
        first_name: Contact first name
        last_name: Contact last name (often includes MATRIX metadata)
        access_hash: Telegram access hash for the user
        contact_type: 'dev' or 'kol'
        campaign_id: Optional campaign ID for tracking

    Returns:
        True if linked successfully
    """
    try:
        clean_phone = normalize_phone(account_phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        # Create or update conversation with MATRIX metadata
        cursor.execute('''
            INSERT INTO inbox_conversations
            (account_phone, peer_id, username, first_name, last_name,
             access_hash, is_matrix_contact, contact_type, contact_status, campaign_id)
            VALUES (?, ?, ?, ?, ?, ?, TRUE, ?, 'blue', ?)
            ON CONFLICT(account_phone, peer_id) DO UPDATE SET
                username = COALESCE(excluded.username, username),
                first_name = COALESCE(excluded.first_name, first_name),
                last_name = COALESCE(excluded.last_name, last_name),
                access_hash = COALESCE(excluded.access_hash, access_hash),
                is_matrix_contact = TRUE,
                contact_type = excluded.contact_type,
                contact_status = COALESCE(contact_status, 'blue'),
                campaign_id = COALESCE(excluded.campaign_id, campaign_id),
                updated_at = CURRENT_TIMESTAMP
        ''', (clean_phone, peer_id, username, first_name, last_name,
              access_hash, contact_type, campaign_id))

        conn.commit()
        conn.close()

        logger.debug(f"✅ Linked MATRIX contact {peer_id} as {contact_type} for {clean_phone}")
        return True

    except Exception as e:
        logger.error(f"❌ Error linking MATRIX contact: {str(e)}")
        return False


def inbox_get_matrix_contact(account_phone: str, peer_id: int = None,
                              username: str = None) -> Optional[Dict]:
    """
    Get a MATRIX contact from inbox by peer_id or username.

    Args:
        account_phone: Account phone number
        peer_id: Optional Telegram user ID
        username: Optional Telegram username

    Returns:
        Conversation dict if found, None otherwise
    """
    if not peer_id and not username:
        return None

    try:
        clean_phone = normalize_phone(account_phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        if peer_id:
            cursor.execute('''
                SELECT * FROM inbox_conversations
                WHERE account_phone = ? AND peer_id = ? AND is_matrix_contact = TRUE
            ''', (clean_phone, peer_id))
        else:
            cursor.execute('''
                SELECT * FROM inbox_conversations
                WHERE account_phone = ? AND LOWER(username) = LOWER(?) AND is_matrix_contact = TRUE
            ''', (clean_phone, username))

        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    except Exception as e:
        logger.error(f"❌ Error getting MATRIX contact: {str(e)}")
        return None


def inbox_get_blue_contacts(account_phone: str, contact_type: str = None) -> List[Dict]:
    """
    Get all blue (not replied) MATRIX contacts for an account.

    Args:
        account_phone: Account phone number
        contact_type: Optional filter by 'dev' or 'kol'

    Returns:
        List of conversation dicts
    """
    try:
        clean_phone = normalize_phone(account_phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        if contact_type:
            cursor.execute('''
                SELECT * FROM inbox_conversations
                WHERE account_phone = ? AND is_matrix_contact = TRUE
                AND contact_status = 'blue' AND contact_type = ?
            ''', (clean_phone, contact_type))
        else:
            cursor.execute('''
                SELECT * FROM inbox_conversations
                WHERE account_phone = ? AND is_matrix_contact = TRUE
                AND contact_status = 'blue'
            ''', (clean_phone,))

        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    except Exception as e:
        logger.error(f"❌ Error getting blue contacts: {str(e)}")
        return []


def inbox_update_contact_status(account_phone: str, peer_id: int,
                                 new_status: str) -> bool:
    """
    Update a MATRIX contact's status (blue -> yellow).

    Args:
        account_phone: Account phone number
        peer_id: Telegram user ID
        new_status: 'blue' or 'yellow'

    Returns:
        True if updated
    """
    try:
        clean_phone = normalize_phone(account_phone)
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE inbox_conversations
            SET contact_status = ?, first_reply_at = CASE
                WHEN ? = 'yellow' AND first_reply_at IS NULL THEN CURRENT_TIMESTAMP
                ELSE first_reply_at
            END,
            updated_at = CURRENT_TIMESTAMP
            WHERE account_phone = ? AND peer_id = ? AND is_matrix_contact = TRUE
        ''', (new_status, new_status, clean_phone, peer_id))

        updated = cursor.rowcount > 0
        conn.commit()
        conn.close()

        if updated:
            logger.info(f"✅ Updated contact {peer_id} status to {new_status}")

        return updated

    except Exception as e:
        logger.error(f"❌ Error updating contact status: {str(e)}")
        return False
