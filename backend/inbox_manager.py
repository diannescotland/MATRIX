"""
MATRIX Inbox Manager
====================
Real-time inbox management system with persistent Telegram connections.

This module provides:
- EventProcessor: Processes Telegram events and persists to database
- SyncEngine: Handles periodic synchronization and gap detection
- DMRateLimiter: Rate limiting + duplicate detection for sending DMs
- InboxManager: Main orchestrator class

NOTE: ConnectionPool has been replaced by GlobalConnectionManager (connection_manager.py)
to solve session file locking issues between inbox and operations.

Based on INBOX_IMPLEMENTATION_PLAN.md decisions:
- Simple asyncio scheduler (no Celery/Redis)
- Auto-connect all accounts on startup
- Keep messages forever
- Include typing indicators and online status
"""

import asyncio
import logging
import threading
import time
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple, Any, Callable
from dataclasses import dataclass, field
from pathlib import Path

from telethon import TelegramClient, events
from telethon.tl.types import (
    User, Chat, Channel, Message,
    UpdateUserTyping, UpdateUserStatus, UserStatusOnline, UserStatusOffline,
    UpdateReadHistoryOutbox, UpdateReadHistoryInbox,
    UpdateDeleteMessages, UpdateEditMessage,
    PeerUser
)
from telethon.errors import (
    FloodWaitError, SessionPasswordNeededError,
    AuthKeyUnregisteredError, UserDeactivatedBanError
)
from flask_socketio import SocketIO

from account_manager import (
    get_active_accounts, get_account_by_phone, normalize_phone,
    inbox_get_or_create_conversation, inbox_update_conversation,
    inbox_get_conversations, inbox_insert_message, inbox_get_messages,
    inbox_mark_messages_read, inbox_soft_delete_messages,
    inbox_update_connection_state, inbox_get_connection_states,
    inbox_increment_reconnect_attempts, inbox_record_dm_sent, inbox_check_dm_sent,
    inbox_get_dm_count_today, inbox_log_event, inbox_get_conversations_needing_backfill,
    inbox_update_contact_status, inbox_update_campaign_metrics
)

# Import GlobalConnectionManager for shared client management
from connection_manager import GlobalConnectionManager, ConnectionInfo

# Setup logging
logger = logging.getLogger(__name__)

# NOTE: SESSIONS_DIR is now defined in connection_manager.py


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class SyncResult:
    """Result of a dialog sync operation."""
    dialogs_fetched: int = 0
    synced: int = 0
    skipped: int = 0
    gaps_detected: int = 0
    deletions_detected: int = 0
    errors: List[str] = field(default_factory=list)


@dataclass
class FullSyncResult:
    """Result of a full sync operation."""
    dialogs_synced: int = 0
    messages_backfilled: int = 0
    integrity_ok: bool = True
    errors: List[str] = field(default_factory=list)


# NOTE: ConnectionInfo dataclass is now imported from connection_manager.py
# NOTE: ConnectionPool class has been replaced by GlobalConnectionManager


# ============================================================================
# EVENT PROCESSOR
# ============================================================================

class EventProcessor:
    """
    Processes Telegram events and persists to database.

    Handles:
    - NewMessage (incoming/outgoing)
    - MessageRead (outbox read receipts)
    - MessageEdited
    - MessageDeleted
    - UserUpdate (online/offline)
    - UpdateUserTyping
    """

    def __init__(self, socketio: SocketIO, conn_manager: GlobalConnectionManager):
        self._socketio = socketio
        self._conn_manager = conn_manager  # Use GlobalConnectionManager
        self._typing_timers: Dict[str, asyncio.Task] = {}

    async def handle_new_message(self, account_phone: str, event, incoming: bool) -> None:
        """
        Handle new message event.

        1. Create/update conversation record
        2. Insert message record
        3. Check if first reply from blue contact → trigger 🔵→🟡
        4. Emit WebSocket notification
        """
        try:
            # Skip non-user messages (groups, channels)
            if not event.is_private:
                return

            message = event.message
            peer_id = event.chat_id

            # Get peer info
            sender = await event.get_sender()
            if not isinstance(sender, User):
                return

            # Determine from_id
            conn_info = self._conn_manager.get_connection_info(account_phone)
            my_id = conn_info.my_id if conn_info else 0
            from_id = my_id if not incoming else sender.id
            is_outgoing = not incoming

            # Get/create conversation
            conv = inbox_get_or_create_conversation(
                account_phone,
                peer_id,
                username=sender.username,
                first_name=sender.first_name,
                last_name=sender.last_name,
                access_hash=sender.access_hash
            )

            # Insert message
            inserted = inbox_insert_message(
                account_phone,
                peer_id,
                message.id,
                from_id,
                is_outgoing,
                message.text or "",
                message.date,
                reply_to_msg_id=message.reply_to_msg_id if message.reply_to else None,
                media_type=self._get_media_type(message),
                synced_via='event'
            )

            # Update conversation with last message info
            update_data = {
                'last_msg_id': message.id,
                'last_msg_date': message.date,
                'last_msg_text': (message.text or "")[:100],  # Truncate
                'last_msg_from_id': from_id,
                'last_msg_is_outgoing': is_outgoing
            }

            # Update unread count for incoming messages
            if incoming:
                update_data['unread_count'] = (conv.get('unread_count', 0) or 0) + 1

            inbox_update_conversation(account_phone, peer_id, **update_data)

            # Check for first reply from blue contact
            if incoming and conv and conv.get('is_matrix_contact') and conv.get('contact_status') == 'blue':
                await self._handle_first_reply(account_phone, peer_id, conv, message)

            # Emit WebSocket notification
            self._socketio.emit('inbox:new_message', {
                'account_phone': account_phone,
                'peer_id': peer_id,
                'message': {
                    'msg_id': message.id,
                    'from_id': from_id,
                    'is_outgoing': is_outgoing,
                    'text': message.text or "",
                    'date': message.date.isoformat() if message.date else None,
                    'media_type': self._get_media_type(message)
                },
                'conversation': {
                    'first_name': sender.first_name,
                    'last_name': sender.last_name,
                    'username': sender.username,
                    'unread_count': update_data.get('unread_count', 0),
                    'last_msg_text': update_data['last_msg_text']
                }
            }, room=f"inbox:{account_phone}")

            # Log event
            inbox_log_event(
                account_phone, peer_id,
                'new_message',
                {'incoming': incoming, 'msg_id': message.id}
            )

        except Exception as e:
            logger.error(f"❌ Error handling new message: {e}")

    async def handle_message_read(self, account_phone: str, event) -> None:
        """
        Handle outbox read receipt - WebSocket ONLY delivery.

        Telethon: UpdateReadHistoryOutbox
        - event.max_id = highest msg_id they've read
        """
        try:
            # Only handle private chats
            if not hasattr(event, 'max_id'):
                return

            peer_id = event.chat_id if hasattr(event, 'chat_id') else None
            if not peer_id:
                # Try to extract from peer
                if hasattr(event, 'peer') and isinstance(event.peer, PeerUser):
                    peer_id = event.peer.user_id
                else:
                    return

            max_read_id = event.max_id

            # Update database
            read_count = inbox_mark_messages_read(account_phone, peer_id, max_read_id)

            # Emit via WebSocket ONLY (no REST API for this)
            self._socketio.emit('inbox:message_read', {
                'account_phone': account_phone,
                'peer_id': peer_id,
                'max_read_id': max_read_id,
                'read_count': read_count,
                'timestamp': datetime.now().isoformat()
            }, room=f"inbox:{account_phone}")

            logger.debug(f"📬 Read receipt: {read_count} messages marked as read for {peer_id}")

        except Exception as e:
            logger.error(f"❌ Error handling message read: {e}")

    async def handle_message_edited(self, account_phone: str, event) -> None:
        """Handle message edit event."""
        try:
            if not event.is_private:
                return

            message = event.message
            peer_id = event.chat_id

            # Update message in database (not implemented yet, just log)
            logger.debug(f"✏️ Message {message.id} edited in chat {peer_id}")

            # Emit WebSocket notification
            self._socketio.emit('inbox:message_edited', {
                'account_phone': account_phone,
                'peer_id': peer_id,
                'msg_id': message.id,
                'new_text': message.text or "",
                'edit_date': message.edit_date.isoformat() if message.edit_date else None
            }, room=f"inbox:{account_phone}")

        except Exception as e:
            logger.error(f"❌ Error handling message edited: {e}")

    async def handle_deleted_messages(self, account_phone: str, event) -> None:
        """Handle deleted messages event."""
        try:
            if not hasattr(event, 'messages'):
                return

            msg_ids = list(event.messages)

            # We don't have peer_id from DeleteMessages, so we can't soft-delete
            # This would require querying the database to find the peer
            logger.debug(f"🗑️ Messages deleted: {msg_ids}")

        except Exception as e:
            logger.error(f"❌ Error handling deleted messages: {e}")

    async def handle_user_status(self, account_phone: str, event) -> None:
        """Handle user online/offline status update."""
        try:
            if not hasattr(event, 'user_id'):
                return

            peer_id = event.user_id
            status = event.status

            online = isinstance(status, UserStatusOnline)
            last_seen = None

            if isinstance(status, UserStatusOffline):
                last_seen = status.was_online.isoformat() if status.was_online else None

            # Emit WebSocket notification
            self._socketio.emit('inbox:user_status', {
                'account_phone': account_phone,
                'peer_id': peer_id,
                'online': online,
                'last_seen': last_seen
            }, room=f"inbox:{account_phone}")

        except Exception as e:
            logger.error(f"❌ Error handling user status: {e}")

    async def handle_typing(self, account_phone: str, event) -> None:
        """Handle typing indicator."""
        try:
            peer_id = event.user_id

            # Emit typing event
            self._socketio.emit('inbox:typing', {
                'account_phone': account_phone,
                'peer_id': peer_id,
                'is_typing': True
            }, room=f"inbox:{account_phone}")

            # Auto-expire typing indicator after 5 seconds
            timer_key = f"{account_phone}:{peer_id}"
            if timer_key in self._typing_timers:
                self._typing_timers[timer_key].cancel()

            async def expire_typing():
                await asyncio.sleep(5)
                self._socketio.emit('inbox:typing', {
                    'account_phone': account_phone,
                    'peer_id': peer_id,
                    'is_typing': False
                }, room=f"inbox:{account_phone}")
                if timer_key in self._typing_timers:
                    del self._typing_timers[timer_key]

            self._typing_timers[timer_key] = asyncio.create_task(expire_typing())

        except Exception as e:
            logger.error(f"❌ Error handling typing: {e}")

    async def _handle_first_reply(self, account_phone: str, peer_id: int,
                                   conversation: Dict, message) -> None:
        """
        Handle first reply from a blue contact (trigger 🔵→🟡).

        This method:
        1. Updates inbox_conversation status to 'yellow'
        2. Updates the actual Telegram contact name (🔵→🟡)
        3. Updates campaign metrics if applicable
        4. Emits WebSocket notification
        """
        try:
            contact_type = conversation.get('contact_type')
            campaign_id = conversation.get('campaign_id')

            logger.info(f"🎉 First reply detected from blue contact {peer_id}!")

            # Update conversation status in database
            inbox_update_contact_status(account_phone, peer_id, 'yellow')

            # Update campaign metrics if applicable
            if campaign_id:
                inbox_update_campaign_metrics(campaign_id)

            # Update the actual Telegram contact name (🔵→🟡)
            client = self._conn_manager.get_connection_info(account_phone)
            client = client.client if client and client.client.is_connected() else None
            if client:
                await self._update_telegram_contact_status(
                    client, peer_id, contact_type
                )

            # Emit WebSocket event
            self._socketio.emit('inbox:first_reply', {
                'account_phone': account_phone,
                'peer_id': peer_id,
                'contact_type': contact_type,
                'campaign_id': campaign_id,
                'message': {
                    'msg_id': message.id,
                    'text': message.text or ""
                }
            }, room=f"inbox:{account_phone}")

            # Log event
            inbox_log_event(
                account_phone, peer_id, 'first_reply',
                {'contact_type': contact_type, 'campaign_id': campaign_id},
                msg_id=message.id, campaign_id=campaign_id
            )

        except Exception as e:
            logger.error(f"❌ Error handling first reply: {e}")

    async def _update_telegram_contact_status(self, client: TelegramClient,
                                               peer_id: int, contact_type: str) -> bool:
        """
        Update Telegram contact name from 🔵 to 🟡.

        Args:
            client: TelegramClient instance
            peer_id: Telegram user ID
            contact_type: 'dev' or 'kol' to determine emoji

        Returns:
            True if updated successfully
        """
        try:
            from telethon.tl.functions.contacts import GetContactsRequest, AddContactRequest

            # Get the current contact
            entity = await client.get_entity(peer_id)
            if not entity:
                logger.warning(f"⚠️ Could not find entity for {peer_id}")
                return False

            # Get current contact info
            contacts_result = await client(GetContactsRequest(hash=0))
            current_contact = None

            for user in contacts_result.users:
                if user.id == peer_id:
                    current_contact = user
                    break

            if not current_contact:
                logger.warning(f"⚠️ User {peer_id} not in contacts")
                return False

            # Get current first name (which contains the formatted MATRIX name)
            current_first_name = current_contact.first_name or ""
            current_last_name = current_contact.last_name or ""

            # Check if it's a blue contact that needs updating
            if '🔵' not in current_first_name:
                logger.debug(f"Contact {peer_id} doesn't have 🔵 emoji, skipping")
                return False

            # Replace 🔵 with 🟡
            new_first_name = current_first_name.replace('🔵', '🟡')

            # Update the contact
            await client(AddContactRequest(
                id=peer_id,
                first_name=new_first_name,
                last_name=current_last_name,
                phone="",
                add_phone_privacy_exception=False
            ))

            logger.info(f"✅ Updated Telegram contact {peer_id}: 🔵→🟡")
            return True

        except Exception as e:
            logger.error(f"❌ Error updating Telegram contact status: {e}")
            return False

    def _get_media_type(self, message) -> Optional[str]:
        """Get media type from message."""
        if not message.media:
            return None
        media_type = type(message.media).__name__
        # Map to simple types
        type_map = {
            'MessageMediaPhoto': 'photo',
            'MessageMediaDocument': 'document',
            'MessageMediaWebPage': 'webpage',
            'MessageMediaContact': 'contact',
            'MessageMediaGeo': 'location',
            'MessageMediaVenue': 'venue',
            'MessageMediaPoll': 'poll',
            'MessageMediaDice': 'dice'
        }
        return type_map.get(media_type, 'other')


# ============================================================================
# SYNC ENGINE
# ============================================================================

class SyncEngine:
    """
    Handles periodic synchronization and gap detection.

    Sync Strategy:
    1. Every 30 min: Fetch all dialogs (1 API call)
    2. For each dialog, compare last_msg_id with database
    3. gap == 0: Skip (no new messages)
    4. gap == 1: Use message from dialog response (0 extra API calls)
    5. gap >= 2: Mark needs_backfill, schedule backfill task
    """

    # Sync intervals (seconds)
    DIALOG_SYNC_INTERVAL = 30 * 60      # 30 minutes
    FULL_SYNC_INTERVAL = 12 * 60 * 60   # 12 hours
    BACKFILL_CHECK_INTERVAL = 5 * 60    # 5 minutes

    def __init__(self, conn_manager: GlobalConnectionManager, processor: EventProcessor, socketio: SocketIO):
        self._conn_manager = conn_manager  # Use GlobalConnectionManager
        self._processor = processor
        self._socketio = socketio

    async def sync_dialogs(self, account_phone: str) -> SyncResult:
        """
        Sync dialogs for an account with gap detection.

        Returns:
            SyncResult with statistics
        """
        result = SyncResult()
        clean_phone = normalize_phone(account_phone)

        conn_info = self._conn_manager.get_connection_info(clean_phone)
        if not conn_info or not conn_info.client.is_connected():
            result.errors.append(f"Account {clean_phone} not connected")
            return result

        client = conn_info.client

        try:
            logger.info(f"🔄 Starting dialog sync for {clean_phone}")

            # Get my_id from connection info
            my_id = conn_info.my_id if conn_info else 0

            # Fetch all dialogs (SINGLE API call)
            dialogs = await client.get_dialogs()
            result.dialogs_fetched = len(dialogs)

            for dialog in dialogs:
                # Skip groups/channels - only private chats
                if not dialog.is_user:
                    continue

                entity = dialog.entity
                if not isinstance(entity, User):
                    continue

                peer_id = entity.id
                dialog_msg = dialog.message
                if not dialog_msg:
                    continue

                dialog_last_msg_id = dialog_msg.id

                # Get/create conversation in database
                conv = inbox_get_or_create_conversation(
                    clean_phone,
                    peer_id,
                    username=entity.username,
                    first_name=entity.first_name,
                    last_name=entity.last_name,
                    access_hash=entity.access_hash
                )

                db_last_msg_id = conv.get('last_msg_id', 0) or 0

                # ========== GAP DETECTION LOGIC ==========
                gap = dialog_last_msg_id - db_last_msg_id

                if gap == 0:
                    # No new messages - SKIP
                    result.skipped += 1
                    continue

                elif gap == 1:
                    # Single new message - use from dialog (0 extra API calls)
                    await self._insert_message_from_dialog(clean_phone, peer_id, dialog_msg, my_id)
                    await self._update_conversation_last_msg(clean_phone, peer_id, dialog_msg, my_id)
                    result.synced += 1

                elif gap >= 2:
                    # MULTIPLE MESSAGES MISSING - mark for backfill
                    inbox_update_conversation(
                        clean_phone, peer_id,
                        needs_backfill=True,
                        backfill_from_msg_id=db_last_msg_id
                    )
                    result.gaps_detected += 1
                    logger.info(f"📊 Gap detected for {peer_id}: {db_last_msg_id} -> {dialog_last_msg_id} (gap={gap})")

                elif gap < 0:
                    # Messages were DELETED (dialog_msg_id < db_last_msg_id)
                    # For now, just log it
                    result.deletions_detected += 1
                    logger.info(f"🗑️ Deletion detected for {peer_id}")

            # Update sync timestamp
            inbox_update_connection_state(
                clean_phone, True,
                last_dialog_sync=datetime.now().isoformat(),
                dialogs_count=result.dialogs_fetched
            )

            logger.info(f"✅ Dialog sync complete for {clean_phone}: "
                       f"{result.synced} synced, {result.skipped} skipped, "
                       f"{result.gaps_detected} gaps")

            return result

        except FloodWaitError as e:
            result.errors.append(f"Rate limited, wait {e.seconds}s")
            logger.warning(f"⚠️ Rate limited during sync: {e.seconds}s")
            return result

        except Exception as e:
            result.errors.append(str(e))
            logger.error(f"❌ Error syncing dialogs: {e}")
            return result

    async def backfill_conversation(self, account_phone: str, peer_id: int,
                                    from_msg_id: int, limit: int = 100) -> int:
        """
        Fetch messages to fill gap.

        Args:
            account_phone: Account phone number
            peer_id: Peer user ID
            from_msg_id: Fetch messages newer than this ID
            limit: Maximum messages to fetch

        Returns:
            Number of messages backfilled
        """
        clean_phone = normalize_phone(account_phone)
        conn_info = self._conn_manager.get_connection_info(clean_phone)
        if not conn_info or not conn_info.client.is_connected():
            return 0

        client = conn_info.client

        try:
            my_id = conn_info.my_id if conn_info else 0

            # Fetch messages NEWER than from_msg_id
            messages = await client.get_messages(
                peer_id,
                min_id=from_msg_id,
                limit=limit
            )

            inserted = 0
            latest_msg_id = from_msg_id

            for msg in messages:
                if not msg or not msg.id:
                    continue

                if msg.id > latest_msg_id:
                    latest_msg_id = msg.id

                # Determine from_id
                is_outgoing = msg.out
                from_id = my_id if is_outgoing else (msg.sender_id or peer_id)

                # Insert message (ON CONFLICT IGNORE for duplicates)
                if inbox_insert_message(
                    clean_phone, peer_id, msg.id, from_id, is_outgoing,
                    msg.text or "", msg.date,
                    reply_to_msg_id=msg.reply_to_msg_id if msg.reply_to else None,
                    media_type=self._processor._get_media_type(msg),
                    synced_via='backfill'
                ):
                    inserted += 1

            # Clear backfill flag and update last_msg_id
            inbox_update_conversation(
                clean_phone, peer_id,
                needs_backfill=False,
                backfill_from_msg_id=None,
                last_msg_id=latest_msg_id
            )

            logger.info(f"✅ Backfilled {inserted} messages for {peer_id}")
            return inserted

        except FloodWaitError as e:
            logger.warning(f"⚠️ Rate limited during backfill: {e.seconds}s")
            return 0

        except Exception as e:
            logger.error(f"❌ Error backfilling conversation: {e}")
            return 0

    async def process_pending_backfills(self, account_phone: str) -> int:
        """
        Process all conversations marked needs_backfill=TRUE.

        Returns:
            Total messages backfilled
        """
        clean_phone = normalize_phone(account_phone)
        pending = inbox_get_conversations_needing_backfill(clean_phone)
        total_backfilled = 0

        for conv in pending:
            try:
                count = await self.backfill_conversation(
                    clean_phone,
                    conv['peer_id'],
                    from_msg_id=conv.get('backfill_from_msg_id', 0) or 0
                )
                total_backfilled += count

                # Small delay between backfills to avoid rate limiting
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"❌ Backfill failed for {conv['peer_id']}: {e}")

        return total_backfilled

    async def full_sync(self, account_phone: str) -> FullSyncResult:
        """
        Complete sync for data integrity (run every 12 hours).
        """
        result = FullSyncResult()
        clean_phone = normalize_phone(account_phone)

        try:
            # 1. Sync all dialogs
            sync_result = await self.sync_dialogs(clean_phone)
            result.dialogs_synced = sync_result.dialogs_fetched
            result.errors.extend(sync_result.errors)

            # 2. Process ALL pending backfills
            result.messages_backfilled = await self.process_pending_backfills(clean_phone)

            # 3. Update full sync timestamp
            inbox_update_connection_state(
                clean_phone, True,
                last_full_sync=datetime.now().isoformat()
            )

            logger.info(f"✅ Full sync complete for {clean_phone}")
            return result

        except Exception as e:
            result.errors.append(str(e))
            result.integrity_ok = False
            logger.error(f"❌ Full sync failed: {e}")
            return result

    async def _insert_message_from_dialog(self, account_phone: str, peer_id: int,
                                           message, my_id: int) -> None:
        """Insert a message from dialog response."""
        is_outgoing = message.out
        from_id = my_id if is_outgoing else (message.sender_id or peer_id)

        inbox_insert_message(
            account_phone, peer_id, message.id, from_id, is_outgoing,
            message.text or "", message.date,
            reply_to_msg_id=message.reply_to_msg_id if message.reply_to else None,
            media_type=self._processor._get_media_type(message),
            synced_via='dialog'
        )

    async def _update_conversation_last_msg(self, account_phone: str, peer_id: int,
                                             message, my_id: int) -> None:
        """Update conversation with last message info."""
        is_outgoing = message.out
        from_id = my_id if is_outgoing else (message.sender_id or peer_id)

        inbox_update_conversation(
            account_phone, peer_id,
            last_msg_id=message.id,
            last_msg_date=message.date,
            last_msg_text=(message.text or "")[:100],
            last_msg_from_id=from_id,
            last_msg_is_outgoing=is_outgoing
        )


# ============================================================================
# DM RATE LIMITER
# ============================================================================

class DMRateLimiter:
    """
    Rate limiting + duplicate detection for sending DMs.
    Based on DM_SYSTEM_LOGIC.md pattern.
    """

    # Per-account limits
    DM_LIMIT_PER_PERIOD = 40        # Max DMs per 24h period
    DM_PERIOD_HOURS = 24
    MIN_DELAY_BETWEEN_DMS = 30      # Seconds

    def __init__(self, account_phone: str):
        self.account_phone = normalize_phone(account_phone)
        self._sent_to_ids: Set[int] = set()  # Layer 1: In-memory
        self._last_dm_time: Optional[datetime] = None

    def can_send(self, peer_id: int, campaign_id: str = None) -> Tuple[bool, str]:
        """
        Check if we can send DM.

        Returns:
            Tuple of (can_send, reason)
        """
        # Layer 1: Check in-memory cache
        if peer_id in self._sent_to_ids:
            return False, "Already sent (in-memory cache)"

        # Layer 2: Check database
        if inbox_check_dm_sent(self.account_phone, peer_id, campaign_id):
            self._sent_to_ids.add(peer_id)
            return False, "Already sent (database)"

        # Check daily rate limit
        dm_count = inbox_get_dm_count_today(self.account_phone)
        if dm_count >= self.DM_LIMIT_PER_PERIOD:
            return False, f"Daily limit reached ({self.DM_LIMIT_PER_PERIOD} DMs)"

        # Check min delay between DMs
        if self._last_dm_time:
            elapsed = (datetime.now() - self._last_dm_time).total_seconds()
            if elapsed < self.MIN_DELAY_BETWEEN_DMS:
                wait_time = self.MIN_DELAY_BETWEEN_DMS - elapsed
                return False, f"Wait {wait_time:.0f}s between DMs"

        return True, "OK"

    def record_sent(self, peer_id: int, msg_id: int = None, campaign_id: str = None):
        """Record successful DM in all layers."""
        # Layer 1: In-memory
        self._sent_to_ids.add(peer_id)
        self._last_dm_time = datetime.now()

        # Layer 2: Database
        inbox_record_dm_sent(self.account_phone, peer_id, msg_id, campaign_id)

    def get_status(self) -> Dict:
        """Get rate limit status for UI."""
        dm_count = inbox_get_dm_count_today(self.account_phone)
        remaining = max(0, self.DM_LIMIT_PER_PERIOD - dm_count)

        return {
            'account_phone': self.account_phone,
            'sent_today': dm_count,
            'remaining': remaining,
            'limit': self.DM_LIMIT_PER_PERIOD,
            'period_hours': self.DM_PERIOD_HOURS,
            'min_delay_seconds': self.MIN_DELAY_BETWEEN_DMS
        }


# ============================================================================
# INBOX MANAGER (Main Orchestrator)
# ============================================================================

class InboxManager:
    """
    Main entry point for inbox management system.

    Coordinates:
    - GlobalConnectionManager: Shared TelegramClient pool (replaces old ConnectionPool)
    - EventProcessor: Handles Telegram events
    - SyncEngine: Periodic synchronization
    - DMRateLimiter: Rate limiting per account
    - Background scheduler: Asyncio-based periodic tasks

    NOTE: Uses GlobalConnectionManager singleton to share connections with
    UnifiedContactManager, solving session file locking issues.
    """

    def __init__(self, socketio: SocketIO, conn_manager: GlobalConnectionManager = None):
        self._socketio = socketio
        # Use provided connection manager or get the singleton
        self._conn_manager = conn_manager or GlobalConnectionManager.get_instance(socketio)
        self._processor = EventProcessor(socketio, self._conn_manager)
        self._sync_engine = SyncEngine(self._conn_manager, self._processor, socketio)
        self._rate_limiters: Dict[str, DMRateLimiter] = {}
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._scheduler_tasks: List[asyncio.Task] = []

        # Wire up connection manager and processor
        self._conn_manager.set_event_processor(self._processor)

    async def start(self) -> None:
        """Start inbox manager and background tasks."""
        if self._running:
            return

        self._running = True
        self._loop = asyncio.get_event_loop()
        self._conn_manager.set_loop(self._loop)

        logger.info("🚀 Starting Inbox Manager...")

        # Start background scheduler tasks
        await self._start_scheduler()

        logger.info("✅ Inbox Manager started")

    async def stop(self) -> None:
        """Stop inbox manager and cleanup."""
        if not self._running:
            return

        self._running = False
        logger.info("🛑 Stopping Inbox Manager...")

        # Cancel scheduler tasks
        for task in self._scheduler_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Disconnect all accounts via GlobalConnectionManager
        await self._conn_manager.shutdown()

        logger.info("✅ Inbox Manager stopped")

    async def connect_all_active_accounts(self) -> Dict[str, bool]:
        """Connect all accounts with status='active'."""
        results = {}
        accounts = get_active_accounts()

        for account in accounts:
            phone = account['phone']
            api_id = account.get('api_id')
            api_hash = account.get('api_hash')

            if not api_id or not api_hash:
                logger.warning(f"⚠️ Account {phone} missing API credentials")
                results[phone] = False
                continue

            session_path = account.get('session_path')
            proxy = account.get('proxy')

            # Use GlobalConnectionManager to get/create client
            client = await self._conn_manager.get_client(
                phone, api_id, api_hash, session_path, proxy,
                register_events=True
            )
            success = client is not None
            results[phone] = success

            # Initialize rate limiter for connected accounts
            if success:
                self._rate_limiters[phone] = DMRateLimiter(phone)
                # Update database connection state
                inbox_update_connection_state(phone, True, error=None, state='connected')
            else:
                # Save error state so frontend can display it
                inbox_update_connection_state(
                    phone,
                    False,
                    error='Account needs re-authentication',
                    state='auth_required'
                )

            # Small delay between connections
            await asyncio.sleep(0.5)

        logger.info(f"📱 Connected {sum(results.values())}/{len(accounts)} accounts")
        return results

    async def connect_account(self, phone: str) -> bool:
        """
        Connect a single account.
        
        After connecting, triggers an initial dialog sync to populate
        conversations and messages in the database.
        """
        account = get_account_by_phone(phone)
        if not account:
            return False

        session_path = account.get('session_path')
        proxy = account.get('proxy')

        # Use GlobalConnectionManager to get/create client
        client = await self._conn_manager.get_client(
            phone,
            account['api_id'],
            account['api_hash'],
            session_path,
            proxy,
            register_events=True
        )
        success = client is not None

        if success:
            self._rate_limiters[phone] = DMRateLimiter(phone)
            inbox_update_connection_state(phone, True, error=None, state='connected')

            # Trigger initial sync to populate conversations and messages
            logger.info(f"📱 Running initial sync for {phone}...")
            try:
                await self.trigger_dialog_sync(phone)
            except Exception as e:
                logger.warning(f"⚠️ Initial sync failed for {phone}: {e}")
        else:
            # Save the error to database so frontend can display it
            inbox_update_connection_state(
                phone,
                False,
                error='Account needs re-authentication',
                state='auth_required'
            )
            logger.warning(f"⚠️ Account {phone} connection failed - saved error to database")

        return success

    async def disconnect_account(self, phone: str) -> None:
        """Disconnect a single account."""
        await self._conn_manager.disconnect_account(phone)
        clean_phone = normalize_phone(phone)
        if clean_phone in self._rate_limiters:
            del self._rate_limiters[clean_phone]

    # ==================== Query Methods ====================

    def get_conversations(self, phone: str, limit: int = 50, offset: int = 0,
                          unread_only: bool = False, matrix_only: bool = False) -> List[Dict]:
        """Get conversations for an account."""
        return inbox_get_conversations(phone, limit, offset, unread_only, matrix_only)

    def get_messages(self, phone: str, peer_id: int, limit: int = 50,
                     before_msg_id: int = None) -> List[Dict]:
        """Get messages for a conversation."""
        return inbox_get_messages(phone, peer_id, limit, before_msg_id)

    def get_connection_status(self) -> List[Dict]:
        """Get connection status for all accounts."""
        return inbox_get_connection_states()

    def get_rate_limit_status(self, phone: str) -> Dict:
        """Get rate limit status for an account."""
        clean_phone = normalize_phone(phone)
        if clean_phone not in self._rate_limiters:
            self._rate_limiters[clean_phone] = DMRateLimiter(clean_phone)
        return self._rate_limiters[clean_phone].get_status()

    # ==================== Send Message ====================

    async def send_message(self, phone: str, peer_id: int, text: str,
                           campaign_id: str = None) -> Dict:
        """
        Send message with rate limiting.

        Returns:
            Dict with success, msg_id, error, rate_limit_status
        """
        clean_phone = normalize_phone(phone)

        # Check rate limit
        if clean_phone not in self._rate_limiters:
            self._rate_limiters[clean_phone] = DMRateLimiter(clean_phone)

        can_send, reason = self._rate_limiters[clean_phone].can_send(peer_id, campaign_id)
        if not can_send:
            return {
                'success': False,
                'error': reason,
                'rate_limit_status': self._rate_limiters[clean_phone].get_status()
            }

        # Get client from GlobalConnectionManager
        conn_info = self._conn_manager.get_connection_info(clean_phone)
        if not conn_info or not conn_info.client.is_connected():
            return {
                'success': False,
                'error': 'Account not connected'
            }
        client = conn_info.client

        try:
            # Send message
            message = await client.send_message(peer_id, text)

            # Record in rate limiter
            self._rate_limiters[clean_phone].record_sent(peer_id, message.id, campaign_id)

            return {
                'success': True,
                'msg_id': message.id,
                'rate_limit_status': self._rate_limiters[clean_phone].get_status()
            }

        except FloodWaitError as e:
            return {
                'success': False,
                'error': f'Rate limited by Telegram, wait {e.seconds}s',
                'wait_seconds': e.seconds
            }

        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }

    # ==================== Sync Triggers ====================

    async def trigger_dialog_sync(self, phone: str) -> SyncResult:
        """
        Trigger dialog sync for an account.
        
        IMPORTANT: After detecting gaps, immediately process backfills
        so messages are available right away (not waiting 5 min for scheduler).
        """
        result = await self._sync_engine.sync_dialogs(phone)
        
        # If gaps detected, immediately backfill so messages appear now
        if result.gaps_detected > 0:
            logger.info(f"📥 Immediately backfilling {result.gaps_detected} conversations for {phone}")
            backfilled = await self._sync_engine.process_pending_backfills(phone)
            logger.info(f"✅ Backfilled {backfilled} messages for {phone}")
        
        return result

    async def trigger_full_sync(self, phone: str) -> FullSyncResult:
        """Trigger full sync for an account."""
        return await self._sync_engine.full_sync(phone)

    # ==================== Background Scheduler ====================

    async def _start_scheduler(self):
        """Start background sync tasks using asyncio (no Celery)."""
        logger.info("🕐 Starting background scheduler...")

        # Dialog sync every 30 minutes
        async def dialog_sync_task():
            while self._running:
                await asyncio.sleep(SyncEngine.DIALOG_SYNC_INTERVAL)
                if not self._running:
                    break
                for phone in self._conn_manager.get_connected_accounts():
                    try:
                        # Use trigger_dialog_sync which includes immediate backfill
                        await self.trigger_dialog_sync(phone)
                    except Exception as e:
                        logger.error(f"❌ Scheduled dialog sync failed for {phone}: {e}")
                    await asyncio.sleep(1)  # Small delay between accounts

        # Full sync every 12 hours
        async def full_sync_task():
            while self._running:
                await asyncio.sleep(SyncEngine.FULL_SYNC_INTERVAL)
                if not self._running:
                    break
                for phone in self._conn_manager.get_connected_accounts():
                    try:
                        await self._sync_engine.full_sync(phone)
                    except Exception as e:
                        logger.error(f"❌ Scheduled full sync failed for {phone}: {e}")
                    await asyncio.sleep(2)

        # Process backfills every 5 minutes
        async def backfill_task():
            while self._running:
                await asyncio.sleep(SyncEngine.BACKFILL_CHECK_INTERVAL)
                if not self._running:
                    break
                for phone in self._conn_manager.get_connected_accounts():
                    try:
                        count = await self._sync_engine.process_pending_backfills(phone)
                        if count > 0:
                            logger.info(f"📥 Backfilled {count} messages for {phone}")
                    except Exception as e:
                        logger.error(f"❌ Scheduled backfill failed for {phone}: {e}")

        # Create tasks
        self._scheduler_tasks = [
            asyncio.create_task(dialog_sync_task()),
            asyncio.create_task(full_sync_task()),
            asyncio.create_task(backfill_task())
        ]

        logger.info("✅ Background scheduler started (dialog sync: 30min, full sync: 12h, backfill: 5min)")


# ============================================================================
# MODULE EXPORTS
# ============================================================================

__all__ = [
    'InboxManager',
    'EventProcessor',
    'SyncEngine',
    'DMRateLimiter',
    'SyncResult',
    'FullSyncResult',
    'ConnectionInfo'
]
