import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useAccounts } from '../context/AccountContext';
import { useInbox } from '../hooks/useInbox';
import { connectInbox, disconnectInbox, getInboxConnectionStatus } from '../services/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import {
  MessageCircle,
  Send,
  Wifi,
  WifiOff,
  RefreshCw,
  User,
  Users,
  Clock,
  Check,
  CheckCheck,
  Circle,
  Loader2,
  AlertCircle,
  ChevronLeft,
  Search,
  Phone,
  AtSign,
} from 'lucide-react';

// Format timestamp for display
function formatTime(timestamp) {
  if (!timestamp) return '';
  const date = new Date(timestamp);
  const now = new Date();
  const isToday = date.toDateString() === now.toDateString();

  if (isToday) {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) {
    return 'Yesterday';
  }

  return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

// Format full timestamp for message bubbles
function formatMessageTime(timestamp) {
  if (!timestamp) return '';
  const date = new Date(timestamp);
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// Get initials from name
function getInitials(firstName, lastName) {
  const first = firstName?.[0] || '';
  const last = lastName?.[0] || '';
  return (first + last).toUpperCase() || '?';
}

// Conversation item component
function ConversationItem({ conversation, isSelected, onClick, typingUsers, userStatuses }) {
  const isTyping = typingUsers[conversation.peer_id];
  const status = userStatuses[conversation.peer_id];
  const isOnline = status?.online;

  return (
    <div
      onClick={onClick}
      className={`flex items-center gap-3 p-3 cursor-pointer transition-colors hover:bg-accent ${
        isSelected ? 'bg-accent' : ''
      }`}
    >
      {/* Avatar */}
      <div className="relative">
        <div className="w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center text-primary font-medium">
          {getInitials(conversation.first_name, conversation.last_name)}
        </div>
        {isOnline && (
          <div className="absolute bottom-0 right-0 w-3 h-3 bg-green-500 rounded-full border-2 border-background" />
        )}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between">
          <span className="font-medium truncate">
            {conversation.first_name} {conversation.last_name}
          </span>
          <span className="text-xs text-muted-foreground">
            {formatTime(conversation.last_msg_date)}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-sm text-muted-foreground truncate">
            {isTyping ? (
              <span className="text-primary italic">typing...</span>
            ) : (
              conversation.last_msg_text || 'No messages'
            )}
          </span>
          {conversation.unread_count > 0 && (
            <Badge variant="default" className="ml-2 h-5 min-w-[20px] flex items-center justify-center">
              {conversation.unread_count}
            </Badge>
          )}
        </div>
      </div>
    </div>
  );
}

// Message bubble component
function MessageBubble({ message, isOutgoing }) {
  return (
    <div className={`flex ${isOutgoing ? 'justify-end' : 'justify-start'} mb-2`}>
      <div
        className={`max-w-[70%] rounded-lg px-4 py-2 ${
          isOutgoing
            ? 'bg-primary text-primary-foreground'
            : 'bg-muted'
        }`}
      >
        <p className="text-sm whitespace-pre-wrap break-words">{message.text}</p>
        <div className={`flex items-center justify-end gap-1 mt-1 ${
          isOutgoing ? 'text-primary-foreground/70' : 'text-muted-foreground'
        }`}>
          <span className="text-xs">{formatMessageTime(message.date)}</span>
          {isOutgoing && (
            message.is_read ? (
              <CheckCheck className="w-3 h-3" />
            ) : (
              <Check className="w-3 h-3" />
            )
          )}
        </div>
      </div>
    </div>
  );
}

// Rate limit indicator
function RateLimitIndicator({ status }) {
  if (!status) return null;

  const remaining = status.remaining || 0;
  const limit = status.limit || 40;
  const percentage = (remaining / limit) * 100;

  let color = 'bg-green-500';
  if (percentage < 25) color = 'bg-red-500';
  else if (percentage < 50) color = 'bg-yellow-500';

  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      <span>DMs today:</span>
      <div className="w-16 h-2 bg-muted rounded-full overflow-hidden">
        <div
          className={`h-full ${color} transition-all`}
          style={{ width: `${percentage}%` }}
        />
      </div>
      <span>{remaining}/{limit}</span>
    </div>
  );
}

// Main Inbox component
function Inbox({ isConnected }) {
  const { accounts, selectedAccounts, setSelectedAccounts } = useAccounts();
  const [selectedPhone, setSelectedPhone] = useState(null);
  const [messageInput, setMessageInput] = useState('');
  const [sending, setSending] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState({});
  const [connecting, setConnecting] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);

  // Use inbox hook for the selected phone
  const {
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
    fetchConversations,
    fetchMessages,
    loadMoreMessages,
    sendMessage,
    fetchRateLimitStatus,
    triggerSync,
    setSelectedPeer,
    clearSelection,
    clearError,
  } = useInbox(selectedPhone);

  // Set initial phone from selected accounts
  useEffect(() => {
    if (!selectedPhone && selectedAccounts.length > 0) {
      const phone = Object.keys(selectedAccounts[0] === true ? {} : selectedAccounts)[0] ||
                    accounts.find(a => selectedAccounts.includes(a.phone))?.phone;
      if (phone) {
        setSelectedPhone(phone);
      }
    }
  }, [selectedAccounts, accounts, selectedPhone]);

  // Fetch conversations when phone changes
  useEffect(() => {
    if (selectedPhone && inboxConnected) {
      fetchConversations();
      fetchRateLimitStatus();
    }
  }, [selectedPhone, inboxConnected, fetchConversations, fetchRateLimitStatus]);

  // Scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Focus input when conversation selected
  useEffect(() => {
    if (selectedPeer) {
      inputRef.current?.focus();
    }
  }, [selectedPeer]);

  // Fetch connection status
  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const response = await getInboxConnectionStatus();
        if (response.data?.success) {
          setConnectionStatus(response.data.connections || {});
        }
      } catch (err) {
        console.error('Failed to fetch connection status:', err);
      }
    };

    fetchStatus();
    const interval = setInterval(fetchStatus, 30000);
    return () => clearInterval(interval);
  }, []);

  // Handle account selection
  const handleAccountSelect = (phone) => {
    setSelectedPhone(phone);
    clearSelection();
  };

  // Handle connect/disconnect
  const handleConnect = async () => {
    if (!selectedPhone) return;

    setConnecting(true);
    try {
      const isCurrentlyConnected = connectionStatus[selectedPhone]?.connected;

      if (isCurrentlyConnected) {
        await disconnectInbox(selectedPhone);
      } else {
        await connectInbox(selectedPhone);
      }

      // Refresh status
      const response = await getInboxConnectionStatus();
      if (response.data?.success) {
        setConnectionStatus(response.data.connections || {});
      }
    } catch (err) {
      console.error('Connection error:', err);
    } finally {
      setConnecting(false);
    }
  };

  // Handle send message
  const handleSendMessage = async (e) => {
    e.preventDefault();
    if (!messageInput.trim() || !selectedPeer || sending) return;

    setSending(true);
    try {
      await sendMessage(selectedPeer, messageInput.trim());
      setMessageInput('');
    } catch (err) {
      console.error('Failed to send message:', err);
    } finally {
      setSending(false);
    }
  };

  // Handle sync
  const handleSync = async () => {
    if (!selectedPhone) return;
    await triggerSync();
    await fetchConversations();
  };

  // Filter conversations by search
  const filteredConversations = conversations.filter(conv => {
    if (!searchQuery) return true;
    const name = `${conv.first_name} ${conv.last_name}`.toLowerCase();
    const username = (conv.username || '').toLowerCase();
    const query = searchQuery.toLowerCase();
    return name.includes(query) || username.includes(query);
  });

  // Get active accounts for selector
  const activeAccounts = accounts.filter(a => a.status === 'active');
  const currentAccount = activeAccounts.find(a => a.phone === selectedPhone);
  const isAccountConnected = connectionStatus[selectedPhone]?.connected;

  return (
    <div className="h-[calc(100vh-4rem)] flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b">
        <div className="flex items-center gap-4">
          <MessageCircle className="w-6 h-6 text-primary" />
          <h1 className="text-2xl font-bold">Inbox</h1>
          {selectedPhone && (
            <Badge variant={isAccountConnected ? 'default' : 'secondary'}>
              {isAccountConnected ? (
                <>
                  <Wifi className="w-3 h-3 mr-1" />
                  Connected
                </>
              ) : (
                <>
                  <WifiOff className="w-3 h-3 mr-1" />
                  Disconnected
                </>
              )}
            </Badge>
          )}
        </div>

        {/* Account selector */}
        <div className="flex items-center gap-2">
          <select
            value={selectedPhone || ''}
            onChange={(e) => handleAccountSelect(e.target.value)}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm"
          >
            <option value="">Select account...</option>
            {activeAccounts.map(account => (
              <option key={account.phone} value={account.phone}>
                {account.name || account.phone}
                {connectionStatus[account.phone]?.connected ? ' (connected)' : ''}
              </option>
            ))}
          </select>

          <Button
            variant="outline"
            size="sm"
            onClick={handleConnect}
            disabled={!selectedPhone || connecting}
          >
            {connecting ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : isAccountConnected ? (
              <WifiOff className="w-4 h-4" />
            ) : (
              <Wifi className="w-4 h-4" />
            )}
          </Button>

          <Button
            variant="outline"
            size="sm"
            onClick={handleSync}
            disabled={!selectedPhone || !isAccountConnected}
          >
            <RefreshCw className="w-4 h-4" />
          </Button>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Conversations list */}
        <div className="w-80 border-r flex flex-col bg-card">
          {/* Search */}
          <div className="p-3 border-b">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <Input
                placeholder="Search conversations..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-9"
              />
            </div>
          </div>

          {/* Conversations */}
          <ScrollArea className="flex-1">
            {!selectedPhone ? (
              <div className="flex flex-col items-center justify-center h-full p-4 text-center text-muted-foreground">
                <Users className="w-12 h-12 mb-4" />
                <p>Select an account to view conversations</p>
              </div>
            ) : !isAccountConnected ? (
              <div className="flex flex-col items-center justify-center h-full p-4 text-center text-muted-foreground">
                <WifiOff className="w-12 h-12 mb-4" />
                <p>Account not connected</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={handleConnect}
                  disabled={connecting}
                >
                  Connect
                </Button>
              </div>
            ) : loading ? (
              <div className="flex items-center justify-center h-full">
                <Loader2 className="w-8 h-8 animate-spin text-primary" />
              </div>
            ) : filteredConversations.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full p-4 text-center text-muted-foreground">
                <MessageCircle className="w-12 h-12 mb-4" />
                <p>{searchQuery ? 'No conversations found' : 'No conversations yet'}</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={handleSync}
                >
                  Sync dialogs
                </Button>
              </div>
            ) : (
              <div>
                {filteredConversations.map(conv => (
                  <ConversationItem
                    key={conv.peer_id}
                    conversation={conv}
                    isSelected={selectedPeer === conv.peer_id}
                    onClick={() => setSelectedPeer(conv.peer_id)}
                    typingUsers={typingUsers}
                    userStatuses={userStatuses}
                  />
                ))}
              </div>
            )}
          </ScrollArea>

          {/* Rate limit */}
          {selectedPhone && isAccountConnected && (
            <div className="p-3 border-t">
              <RateLimitIndicator status={rateLimitStatus} />
            </div>
          )}
        </div>

        {/* Messages panel */}
        <div className="flex-1 flex flex-col bg-background">
          {!selectedPeer ? (
            <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground">
              <MessageCircle className="w-16 h-16 mb-4" />
              <p className="text-lg">Select a conversation to start messaging</p>
            </div>
          ) : (
            <>
              {/* Conversation header */}
              <div className="flex items-center gap-3 p-4 border-b">
                <Button
                  variant="ghost"
                  size="sm"
                  className="md:hidden"
                  onClick={clearSelection}
                >
                  <ChevronLeft className="w-5 h-5" />
                </Button>

                <div className="relative">
                  <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center text-primary font-medium">
                    {getInitials(selectedConversation?.first_name, selectedConversation?.last_name)}
                  </div>
                  {userStatuses[selectedPeer]?.online && (
                    <div className="absolute bottom-0 right-0 w-3 h-3 bg-green-500 rounded-full border-2 border-background" />
                  )}
                </div>

                <div className="flex-1">
                  <div className="font-medium">
                    {selectedConversation?.first_name} {selectedConversation?.last_name}
                  </div>
                  <div className="text-sm text-muted-foreground flex items-center gap-2">
                    {selectedConversation?.username && (
                      <span className="flex items-center gap-1">
                        <AtSign className="w-3 h-3" />
                        {selectedConversation.username}
                      </span>
                    )}
                    {typingUsers[selectedPeer] ? (
                      <span className="text-primary">typing...</span>
                    ) : userStatuses[selectedPeer]?.online ? (
                      <span className="text-green-500">online</span>
                    ) : userStatuses[selectedPeer]?.last_seen ? (
                      <span>last seen {formatTime(userStatuses[selectedPeer].last_seen)}</span>
                    ) : null}
                  </div>
                </div>
              </div>

              {/* Messages */}
              <ScrollArea className="flex-1 p-4">
                {loadingMessages ? (
                  <div className="flex items-center justify-center h-full">
                    <Loader2 className="w-8 h-8 animate-spin text-primary" />
                  </div>
                ) : messages.length === 0 ? (
                  <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
                    <MessageCircle className="w-12 h-12 mb-4" />
                    <p>No messages yet</p>
                  </div>
                ) : (
                  <div>
                    {messages.length >= 50 && (
                      <div className="text-center mb-4">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => loadMoreMessages(selectedPeer)}
                        >
                          Load older messages
                        </Button>
                      </div>
                    )}
                    {messages.map((msg, idx) => (
                      <MessageBubble
                        key={msg.msg_id || idx}
                        message={msg}
                        isOutgoing={msg.is_outgoing}
                      />
                    ))}
                    <div ref={messagesEndRef} />
                  </div>
                )}
              </ScrollArea>

              {/* Message input */}
              <form onSubmit={handleSendMessage} className="p-4 border-t">
                {error && (
                  <div className="flex items-center gap-2 text-destructive text-sm mb-2">
                    <AlertCircle className="w-4 h-4" />
                    {error}
                    <Button variant="ghost" size="sm" onClick={clearError}>
                      Dismiss
                    </Button>
                  </div>
                )}
                <div className="flex gap-2">
                  <Input
                    ref={inputRef}
                    placeholder="Type a message..."
                    value={messageInput}
                    onChange={(e) => setMessageInput(e.target.value)}
                    disabled={sending || !isAccountConnected}
                    className="flex-1"
                  />
                  <Button
                    type="submit"
                    disabled={!messageInput.trim() || sending || !isAccountConnected}
                  >
                    {sending ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Send className="w-4 h-4" />
                    )}
                  </Button>
                </div>
              </form>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default Inbox;
