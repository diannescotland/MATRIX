#!/usr/bin/env python3
"""
MATRIX Session Migration Script
================================
Converts existing SQLite .session files to StringSession plain text format.

This eliminates database locking issues caused by SQLite session files.

Usage:
    python migrate_sessions.py              # Migrate all sessions
    python migrate_sessions.py --dry-run    # Preview without changing files
    python migrate_sessions.py --backup     # Create backups before migration (default)
    python migrate_sessions.py --no-backup  # Skip backup creation

What it does:
1. Scans sessions/ directory for .session files
2. Detects whether each file is SQLite or StringSession format
3. For SQLite files:
   a. Connects using Telethon to load session data
   b. Exports session as StringSession string
   c. Backs up original SQLite file
   d. Writes StringSession to same filename
4. Cleans up SQLite lock files (-wal, -shm, -journal)
"""

import argparse
import asyncio
import logging
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from telethon import TelegramClient
from telethon.sessions import StringSession

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Directories
BACKEND_DIR = Path(__file__).parent
PROJECT_DIR = BACKEND_DIR.parent
SESSIONS_DIR = PROJECT_DIR / "sessions"
BACKUP_DIR = SESSIONS_DIR / "backup_sqlite"


def is_sqlite_file(path: Path) -> bool:
    """
    Check if file is an SQLite database.

    SQLite files start with "SQLite format 3" header (16 bytes).
    """
    try:
        with open(path, 'rb') as f:
            header = f.read(16)
            return header.startswith(b'SQLite format 3')
    except Exception:
        return False


def is_string_session(path: Path) -> bool:
    """
    Check if file is already a StringSession.

    StringSession files are plain text starting with '1' (base64-ish).
    """
    try:
        content = path.read_text().strip()
        # StringSession starts with '1' and is long (300+ chars)
        return content and len(content) > 100 and content[0] == '1'
    except Exception:
        return False


def get_credentials_from_config() -> Tuple[Optional[int], Optional[str]]:
    """
    Load API credentials from config.json.

    Returns:
        Tuple of (api_id, api_hash) or (None, None) if not found
    """
    import json
    config_path = PROJECT_DIR / "config.json"

    if not config_path.exists():
        logger.error(f"config.json not found at {config_path}")
        return None, None

    try:
        with open(config_path) as f:
            config = json.load(f)

        api_id = config.get('api_id')
        api_hash = config.get('api_hash')

        if api_id and api_hash:
            return int(api_id), api_hash
        else:
            logger.error("api_id or api_hash missing from config.json")
            return None, None
    except Exception as e:
        logger.error(f"Failed to load config.json: {e}")
        return None, None


def get_credentials_from_database(phone: str) -> Tuple[Optional[int], Optional[str]]:
    """
    Load API credentials from accounts.db for specific account.

    Args:
        phone: Phone number (without +)

    Returns:
        Tuple of (api_id, api_hash) or (None, None) if not found
    """
    db_path = PROJECT_DIR / "accounts.db"

    if not db_path.exists():
        return None, None

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Try exact match
        cursor.execute(
            'SELECT api_id, api_hash FROM accounts WHERE phone = ?',
            (phone,)
        )
        row = cursor.fetchone()

        # Try with + prefix
        if not row:
            cursor.execute(
                'SELECT api_id, api_hash FROM accounts WHERE phone = ?',
                (f'+{phone}',)
            )
            row = cursor.fetchone()

        conn.close()

        if row and row[0] and row[1]:
            return int(row[0]), row[1]
        return None, None
    except Exception as e:
        logger.debug(f"Failed to get credentials from database: {e}")
        return None, None


def extract_phone_from_session_name(session_path: Path) -> Optional[str]:
    """
    Extract phone number from session filename.

    Expected format: session_1234567890.session
    """
    name = session_path.stem  # e.g., "session_1234567890"
    if name.startswith('session_'):
        return name[8:]  # Remove "session_" prefix
    return None


async def convert_sqlite_to_string_session(
    session_path: Path,
    api_id: int,
    api_hash: str,
    dry_run: bool = False,
    backup: bool = True
) -> bool:
    """
    Convert SQLite session file to StringSession format.

    Args:
        session_path: Path to SQLite .session file
        api_id: Telegram API ID
        api_hash: Telegram API Hash
        dry_run: If True, don't modify files
        backup: If True, backup original file before conversion

    Returns:
        True if conversion successful, False otherwise
    """
    session_name = session_path.stem

    try:
        # Create client with SQLite session
        client = TelegramClient(
            str(session_path).replace('.session', ''),
            api_id,
            api_hash,
            timeout=10
        )

        await client.connect()

        # Check if authorized
        if not await client.is_user_authorized():
            logger.warning(f"  {session_name}: Not authorized - skipping")
            await client.disconnect()
            return False

        # Get user info for verification
        me = await client.get_me()
        user_info = f"{me.first_name}" + (f" {me.last_name}" if me.last_name else "")

        # Export as StringSession
        session_str = StringSession.save(client.session)

        await client.disconnect()

        if not session_str:
            logger.error(f"  {session_name}: Failed to export StringSession")
            return False

        logger.info(f"  {session_name}: Authenticated as {user_info}")
        logger.info(f"  {session_name}: StringSession exported ({len(session_str)} chars)")

        if dry_run:
            logger.info(f"  {session_name}: [DRY RUN] Would convert to StringSession")
            return True

        # Backup original SQLite file
        if backup:
            BACKUP_DIR.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = BACKUP_DIR / f"{session_name}_{timestamp}.session.sqlite"
            shutil.copy2(session_path, backup_path)
            logger.info(f"  {session_name}: Backed up to {backup_path.name}")

        # Write StringSession to file (overwrites SQLite)
        session_path.write_text(session_str)
        logger.info(f"  {session_name}: Converted to StringSession")

        # Clean up SQLite lock files
        for ext in ['-wal', '-shm', '-journal']:
            lock_file = Path(str(session_path) + ext)
            if lock_file.exists():
                lock_file.unlink()
                logger.debug(f"  {session_name}: Deleted {lock_file.name}")

        return True

    except Exception as e:
        logger.error(f"  {session_name}: Conversion failed - {e}")
        return False


async def migrate_all_sessions(dry_run: bool = False, backup: bool = True) -> dict:
    """
    Migrate all SQLite sessions to StringSession format.

    Args:
        dry_run: If True, preview without modifying files
        backup: If True, create backups before conversion

    Returns:
        Dict with migration statistics
    """
    stats = {
        'total': 0,
        'sqlite': 0,
        'string_session': 0,
        'converted': 0,
        'failed': 0,
        'skipped': 0
    }

    if not SESSIONS_DIR.exists():
        logger.error(f"Sessions directory not found: {SESSIONS_DIR}")
        return stats

    # Get default credentials
    default_api_id, default_api_hash = get_credentials_from_config()

    if not default_api_id or not default_api_hash:
        logger.warning("No default API credentials found - will try per-account credentials")

    # Find all .session files
    session_files = list(SESSIONS_DIR.glob('*.session'))
    stats['total'] = len(session_files)

    logger.info(f"\n{'='*60}")
    logger.info(f"MATRIX Session Migration")
    logger.info(f"{'='*60}")
    logger.info(f"Sessions directory: {SESSIONS_DIR}")
    logger.info(f"Found {len(session_files)} session files")
    logger.info(f"Mode: {'DRY RUN' if dry_run else 'LIVE MIGRATION'}")
    logger.info(f"{'='*60}\n")

    for session_path in session_files:
        session_name = session_path.stem
        logger.info(f"Processing: {session_name}")

        # Check if already StringSession
        if is_string_session(session_path):
            logger.info(f"  Already StringSession - skipping")
            stats['string_session'] += 1
            continue

        # Check if SQLite
        if not is_sqlite_file(session_path):
            logger.warning(f"  Unknown format - skipping")
            stats['skipped'] += 1
            continue

        stats['sqlite'] += 1

        # Get credentials for this account
        phone = extract_phone_from_session_name(session_path)
        api_id, api_hash = None, None

        if phone:
            api_id, api_hash = get_credentials_from_database(phone)

        if not api_id or not api_hash:
            api_id, api_hash = default_api_id, default_api_hash

        if not api_id or not api_hash:
            logger.error(f"  No API credentials found - skipping")
            stats['skipped'] += 1
            continue

        # Convert
        success = await convert_sqlite_to_string_session(
            session_path,
            api_id,
            api_hash,
            dry_run=dry_run,
            backup=backup
        )

        if success:
            stats['converted'] += 1
        else:
            stats['failed'] += 1

    # Print summary
    logger.info(f"\n{'='*60}")
    logger.info(f"Migration Summary")
    logger.info(f"{'='*60}")
    logger.info(f"Total sessions: {stats['total']}")
    logger.info(f"Already StringSession: {stats['string_session']}")
    logger.info(f"SQLite sessions found: {stats['sqlite']}")
    logger.info(f"Successfully converted: {stats['converted']}")
    logger.info(f"Failed: {stats['failed']}")
    logger.info(f"Skipped: {stats['skipped']}")
    logger.info(f"{'='*60}\n")

    if backup and stats['converted'] > 0 and not dry_run:
        logger.info(f"Backups saved to: {BACKUP_DIR}")

    return stats


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Migrate MATRIX Telegram sessions from SQLite to StringSession format'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview migration without modifying files'
    )
    parser.add_argument(
        '--backup',
        action='store_true',
        default=True,
        help='Create backups before migration (default: True)'
    )
    parser.add_argument(
        '--no-backup',
        action='store_true',
        help='Skip backup creation'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    backup = not args.no_backup

    # Run migration
    asyncio.run(migrate_all_sessions(
        dry_run=args.dry_run,
        backup=backup
    ))


if __name__ == '__main__':
    main()
