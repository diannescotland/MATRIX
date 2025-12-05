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

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

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

    Deletes:
    - .session file
    - -journal file (SQLite journal)
    - -wal file (Write-Ahead Log)
    - -shm file (Shared memory)

    Args:
        session_path: Base path to session file (without .session extension)
        phone: Phone number for fallback path construction

    Returns:
        Number of files successfully deleted
    """
    files_deleted = 0
    base_path = session_path.replace('.session', '')

    # Extensions to clean up
    extensions = ['.session', '.session-journal', '.session-wal', '.session-shm']

    for ext in extensions:
        file_path = Path(f"{base_path}{ext}")
        if file_path.exists():
            try:
                file_path.unlink()
                logger.debug(f"🗑️ Deleted session file: {file_path}")
                files_deleted += 1
            except Exception as e:
                logger.warning(f"⚠️ Could not delete {file_path}: {e}")

    # Also try with just the phone number in sessions directory
    sessions_dir = Path(__file__).parent.parent / "sessions"
    for ext in extensions:
        file_path = sessions_dir / f"session_{phone}{ext}"
        if file_path.exists():
            try:
                file_path.unlink()
                logger.debug(f"🗑️ Deleted session file: {file_path}")
                files_deleted += 1
            except Exception as e:
                logger.warning(f"⚠️ Could not delete {file_path}: {e}")

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
        
        # Get session path
        account = get_account_by_phone(phone)
        if account and account.get('session_path'):
            session_path = account['session_path'].replace('.session', '')
        else:
            # Construct default session path (root level, one level up from backend/)
            clean_phone = phone.replace('+', '').replace('-', '').replace(' ', '')
            sessions_dir = Path(__file__).parent.parent / "sessions"
            session_path = str(sessions_dir / f"session_{clean_phone}")
        
        # Try to connect
        client = TelegramClient(session_path, api_id, api_hash)
        
        try:
            await client.connect()
            
            if await client.is_user_authorized():
                me = await client.get_me()
                await client.disconnect()
                return True, f"Account validated: {me.first_name}"
            else:
                await client.disconnect()
                return False, "Session expired - needs re-authentication"
        except SessionPasswordNeededError:
            await client.disconnect()
            return False, "Account requires 2FA password"
        except Exception as e:
            await client.disconnect()
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
