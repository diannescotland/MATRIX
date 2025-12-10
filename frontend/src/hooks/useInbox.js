/**
 * useInbox Hook - Real-time inbox management for MATRIX
 *
 * Provides real-time messaging functionality with:
 * - WebSocket subscriptions for live updates
 * - Conversations list management
 * - Message history with pagination
 * - Typing indicators and online status
 * - Rate limit status tracking
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { io } from 'socket.io-client';
import {
  getInboxConversations,
  getInboxMessages,
  sendInboxMessage,
  getInboxRateLimitStatus,
  triggerDialogSync,
  fetchInboxHistory
} from '../services/api';

const SOCKET_URL = 'http://localhost:5000';

export function useInbox(phone) {
  // State
  const [conversations, setConversations] = useState([]);
  const [messages, setMessages] = useState([]);
  const [selectedPeer, setSelectedPeer] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [connected, setConnected] = useState(false);
  const [inboxConnected, setInboxConnected] = useState(false);
  const [typingUsers, setTypingUsers] = useState({});
  const [userStatuses, setUserStatuses] = useState({});
  const [rateLimitStatus, setRateLimitStatus] = useState(null);
  const [error, setError] = useState(null);

  // Refs
  const socketRef = useRef(null);
  const typingTimeoutsRef = useRef({});

  // Initialize socket connection
  useEffect(() => {
    const socket = io(SOCKET_URL, {
      transports: ['websocket', 'polling'],
      reconnection: true,
      reconnectionAttempts: 5,
      reconnectionDelay: 1000,
    });

    socketRef.current = socket;

    socket.on('connect', () => {
      console.log('Inbox WebSocket connected');
      setConnected(true);
    });

    socket.on('disconnect', () => {
      console.log('Inbox WebSocket disconnected');
      setConnected(false);
    });

    socket.on('error', (data) => {
      console.error('Inbox WebSocket error:', data);
      setError(data.message || 'WebSocket error');
    });

    return () => {
      // Clean up typing timeouts
      Object.values(typingTimeoutsRef.current).forEach(clearTimeout);
      socket.disconnect();
    };
  }, []);

  // Subscribe to inbox events for selected phone
  useEffect(() => {
    if (!phone || !socketRef.current) return;

    const socket = socketRef.current;

    // Subscribe to this account's inbox
    socket.emit('inbox:subscribe', { phone });

    // Handle subscription confirmation
    const handleSubscribed = (data) => {
      if (data.phone === phone) {
        console.log('Subscribed to inbox:', phone);
        setInboxConnected(true);
      }
    };

    // Handle new messages
    const handleNewMessage = (data) => {
      if (data.account_phone !== phone) return;

      // Update conversations list
      setConversations((prev) => {
        const updated = [...prev];
        const idx = updated.findIndex((c) => c.peer_id === data.peer_id);

        if (idx >= 0) {
          // Update existing conversation
          updated[idx] = {
            ...updated[idx],
            last_msg_text: data.conversation?.last_msg_text || data.message.text?.slice(0, 100),
            last_msg_date: data.message.date,
            unread_count: data.conversation?.unread_count || (updated[idx].unread_count || 0) + 1,
          };
          // Move to top
          const [conv] = updated.splice(idx, 1);
          updated.unshift(conv);
        } else {
          // New conversation - add to top
          updated.unshift({
            peer_id: data.peer_id,
            first_name: data.conversation?.first_name || 'Unknown',
            last_name: data.conversation?.last_name || '',
            username: data.conversation?.username || '',
            last_msg_text: data.message.text?.slice(0, 100) || '',
            last_msg_date: data.message.date,
            unread_count: 1,
          });
        }

        return updated;
      });

      // Update messages if viewing this conversation
      if (selectedPeer === data.peer_id) {
        setMessages((prev) => {
          // Avoid duplicates
          if (prev.some((m) => m.msg_id === data.message.msg_id)) {
            return prev;
          }
          return [...prev, data.message];
        });
      }
    };

    // Handle read receipts
    const handleMessageRead = (data) => {
      if (data.account_phone !== phone) return;

      setMessages((prev) =>
        prev.map((msg) =>
          msg.is_outgoing && msg.msg_id <= data.max_read_id
            ? { ...msg, is_read: true, read_at: data.timestamp }
            : msg
        )
      );
    };

    // Handle typing indicators
    const handleTyping = (data) => {
      if (data.account_phone !== phone) return;

      setTypingUsers((prev) => ({
        ...prev,
        [data.peer_id]: data.is_typing,
      }));

      // Auto-clear typing indicator after 5 seconds (as backup)
      if (data.is_typing) {
        const key = `${phone}:${data.peer_id}`;
        if (typingTimeoutsRef.current[key]) {
          clearTimeout(typingTimeoutsRef.current[key]);
        }
        typingTimeoutsRef.current[key] = setTimeout(() => {
          setTypingUsers((prev) => ({
            ...prev,
            [data.peer_id]: false,
          }));
        }, 6000);
      }
    };

    // Handle user status (online/offline)
    const handleUserStatus = (data) => {
      if (data.account_phone !== phone) return;

      setUserStatuses((prev) => ({
        ...prev,
        [data.peer_id]: {
          online: data.online,
          last_seen: data.last_seen,
        },
      }));
    };

    // Handle first reply (blue -> yellow transition)
    const handleFirstReply = (data) => {
      if (data.account_phone !== phone) return;

      // Update conversation status
      setConversations((prev) =>
        prev.map((conv) =>
          conv.peer_id === data.peer_id
            ? { ...conv, contact_status: 'yellow' }
            : conv
        )
      );

      console.log('First reply detected:', data);
    };

    // Handle connection status changes
    const handleConnectionStatus = (data) => {
      if (data.account_phone !== phone) return;
      setInboxConnected(data.connected);
    };

    // Register event handlers
    socket.on('inbox:subscribed', handleSubscribed);
    socket.on('inbox:new_message', handleNewMessage);
    socket.on('inbox:message_read', handleMessageRead);
    socket.on('inbox:typing', handleTyping);
    socket.on('inbox:user_status', handleUserStatus);
    socket.on('inbox:first_reply', handleFirstReply);
    socket.on('inbox:connection_status', handleConnectionStatus);

    // Cleanup
    return () => {
      socket.emit('inbox:unsubscribe', { phone });
      socket.off('inbox:subscribed', handleSubscribed);
      socket.off('inbox:new_message', handleNewMessage);
      socket.off('inbox:message_read', handleMessageRead);
      socket.off('inbox:typing', handleTyping);
      socket.off('inbox:user_status', handleUserStatus);
      socket.off('inbox:first_reply', handleFirstReply);
      socket.off('inbox:connection_status', handleConnectionStatus);
      setInboxConnected(false);
    };
  }, [phone, selectedPeer]);

  // Fetch conversations
  const fetchConversations = useCallback(
    async (options = {}) => {
      if (!phone) return;

      setLoading(true);
      setError(null);

      try {
        const response = await getInboxConversations(phone, options);
        if (response.data.success) {
          setConversations(response.data.conversations || []);
        } else {
          setError(response.data.error || 'Failed to fetch conversations');
        }
      } catch (err) {
        console.error('Failed to fetch conversations:', err);
        setError(err.message || 'Failed to fetch conversations');
      } finally {
        setLoading(false);
      }
    },
    [phone]
  );

  // Fetch messages for a conversation
  const fetchMessages = useCallback(
    async (peerId, options = {}) => {
      if (!phone || !peerId) return;

      setLoadingMessages(true);
      setError(null);

      try {
        // Check if this conversation needs full history fetch
        const conv = conversations.find((c) => c.peer_id === peerId);
        if (conv && !conv.history_fetched) {
          // Fetch full history first (one-time)
          console.log('Fetching full history for', peerId);
          try {
            const historyResponse = await fetchInboxHistory(phone, peerId);
            if (historyResponse.data?.success) {
              console.log('Fetched', historyResponse.data.total_fetched, 'messages');
              // Update conversation to mark history as fetched
              setConversations((prev) =>
                prev.map((c) =>
                  c.peer_id === peerId ? { ...c, history_fetched: true } : c
                )
              );
            }
          } catch (historyErr) {
            console.warn('Failed to fetch full history:', historyErr);
            // Continue anyway - we'll show what we have
          }
        }

        // Now fetch messages from DB
        const response = await getInboxMessages(phone, peerId, options);
        if (response.data.success) {
          setMessages(response.data.messages || []);
          setSelectedPeer(peerId);

          // Reset unread count for this conversation
          setConversations((prev) =>
            prev.map((c) =>
              c.peer_id === peerId ? { ...c, unread_count: 0 } : c
            )
          );
        } else {
          setError(response.data.error || 'Failed to fetch messages');
        }
      } catch (err) {
        console.error('Failed to fetch messages:', err);
        setError(err.message || 'Failed to fetch messages');
      } finally {
        setLoadingMessages(false);
      }
    },
    [phone, conversations]
  );

  // Load more messages (pagination)
  const loadMoreMessages = useCallback(
    async (peerId) => {
      if (!phone || !peerId || messages.length === 0) return;

      const oldestMsgId = messages[0]?.msg_id;
      if (!oldestMsgId) return;

      try {
        const response = await getInboxMessages(phone, peerId, {
          before_msg_id: oldestMsgId,
          limit: 50,
        });

        if (response.data.success && response.data.messages.length > 0) {
          setMessages((prev) => [...response.data.messages, ...prev]);
        }
      } catch (err) {
        console.error('Failed to load more messages:', err);
      }
    },
    [phone, messages]
  );

  // Send message
  const sendMessage = useCallback(
    async (peerId, text, campaignId = null) => {
      if (!phone || !peerId || !text.trim()) return null;

      try {
        const response = await sendInboxMessage(phone, peerId, text, campaignId);

        if (response.data.success) {
          // Update rate limit status
          if (response.data.rate_limit_status) {
            setRateLimitStatus(response.data.rate_limit_status);
          }
          return response.data;
        } else {
          setError(response.data.error || 'Failed to send message');
          return null;
        }
      } catch (err) {
        console.error('Failed to send message:', err);
        setError(err.response?.data?.error || err.message || 'Failed to send message');
        throw err;
      }
    },
    [phone]
  );

  // Fetch rate limit status
  const fetchRateLimitStatus = useCallback(async () => {
    if (!phone) return;

    try {
      const response = await getInboxRateLimitStatus(phone);
      if (response.data.success) {
        setRateLimitStatus(response.data);
      }
    } catch (err) {
      console.error('Failed to fetch rate limit status:', err);
    }
  }, [phone]);

  // Trigger dialog sync
  const triggerSync = useCallback(async () => {
    if (!phone) return null;

    try {
      const response = await triggerDialogSync(phone);
      return response.data;
    } catch (err) {
      console.error('Failed to trigger sync:', err);
      setError(err.message || 'Failed to sync');
      return null;
    }
  }, [phone]);

  // Clear selected conversation
  const clearSelection = useCallback(() => {
    setSelectedPeer(null);
    setMessages([]);
  }, []);

  // Get selected conversation details
  const selectedConversation = conversations.find((c) => c.peer_id === selectedPeer);

  return {
    // State
    conversations,
    messages,
    selectedPeer,
    selectedConversation,
    loading,
    loadingMessages,
    connected,
    inboxConnected,
    typingUsers,
    userStatuses,
    rateLimitStatus,
    error,

    // Actions
    fetchConversations,
    fetchMessages,
    loadMoreMessages,
    sendMessage,
    fetchRateLimitStatus,
    triggerSync,
    setSelectedPeer: (peerId) => {
      if (peerId) {
        fetchMessages(peerId);
      } else {
        clearSelection();
      }
    },
    clearSelection,
    clearError: () => setError(null),
  };
}

export default useInbox;
