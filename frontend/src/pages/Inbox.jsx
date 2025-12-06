import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useAccounts } from '../context/AccountContext';
import { useInbox } from '../hooks/useInbox';
import { getInboxConnectionStatus, connectInbox } from '../services/api';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  MessageCircle,
  Send,
  Wifi,
  WifiOff,
  RefreshCw,
  Users,
  Check,
  CheckCheck,
  Loader2,
  AlertCircle,
  ChevronLeft,
  Search,
  AtSign,
  Copy,
  Reply,
  MoreHorizontal,
  MoreVertical,
  ArrowDown,
  Flag,
  UserMinus2,
  Trash2,
} from 'lucide-react';
import { cn } from '@/lib/utils';

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

// Format timestamp for conversation list
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

// Generate avatar URL using DiceBear
function getAvatarUrl(peerId, name) {
  const seed = peerId || name || 'default';
  return `https://api.dicebear.com/9.x/initials/svg?seed=${seed}`;
}

// Copy text to clipboard
async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (err) {
    console.error('Failed to copy:', err);
    return false;
  }
}

// ============================================================================
// STATUS BADGE COMPONENT
// ============================================================================

const STATUS_COLORS = {
  online: 'bg-green-500',
  away: 'bg-yellow-500',
  offline: 'bg-gray-400',
};

function StatusBadge({ status, className = '' }) {
  const statusType = status?.online ? 'online' : status?.last_seen ? 'away' : 'offline';
  
  return (
    <span
      aria-label={statusType}
      className={`inline-block size-3 rounded-full border-2 border-background ${STATUS_COLORS[statusType]} ${className}`}
      title={statusType.charAt(0).toUpperCase() + statusType.slice(1)}
    />
  );
}

// ============================================================================
// MESSAGE ACTIONS COMPONENT (hover menu on messages)
// ============================================================================

function MessageActions({ message, isOutgoing, onReply }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    const success = await copyToClipboard(message.text || '');
    if (success) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          aria-label="Message actions"
          className="size-7 rounded bg-background hover:bg-accent"
          size="icon"
          variant="ghost"
        >
          <MoreHorizontal className="size-3.5" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="center" className="w-40 rounded-lg bg-popover p-1 shadow-xl">
        <div className="flex flex-col gap-1">
          <Button
            className="w-full justify-start gap-2 rounded px-2 py-1 text-xs"
            size="sm"
            variant="ghost"
            onClick={() => onReply?.(message)}
          >
            <Reply className="size-3" />
            <span>Reply</span>
          </Button>
          <Button
            className="w-full justify-start gap-2 rounded px-2 py-1 text-xs"
            size="sm"
            variant="ghost"
            onClick={handleCopy}
          >
            <Copy className="size-3" />
            <span>{copied ? 'Copied!' : 'Copy'}</span>
          </Button>
          {isOutgoing && (
            <Button
              className="w-full justify-start gap-2 rounded px-2 py-1 text-xs text-destructive"
              size="sm"
              variant="ghost"
            >
              <Trash2 className="size-3" />
              <span>Delete</span>
            </Button>
          )}
          <Button
            className="w-full justify-start gap-2 rounded px-2 py-1 text-xs text-yellow-600"
            size="sm"
            variant="ghost"
          >
            <Flag className="size-3" />
            <span>Report</span>
          </Button>
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ============================================================================
// USER ACTIONS MENU (for conversation header)
// ============================================================================

function UserActionsMenu() {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          aria-label="User actions"
          className="border-muted-foreground/30"
          size="icon"
          variant="outline"
        >
          <MoreVertical className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="min-w-36 rounded-lg bg-popover p-1 shadow-xl">
        <div className="flex flex-col gap-1">
          <Button
            className="w-full justify-start gap-2 rounded bg-transparent text-rose-600 hover:bg-accent"
            size="sm"
            variant="ghost"
          >
            <UserMinus2 className="size-4" />
            <span className="font-medium text-xs">Block User</span>
          </Button>
          <Button
            className="w-full justify-start gap-2 rounded bg-transparent text-destructive hover:bg-accent"
            size="sm"
            variant="ghost"
          >
            <Trash2 className="size-4" />
            <span className="font-medium text-xs">Delete Conversation</span>
          </Button>
          <Button
            className="w-full justify-start gap-2 rounded bg-transparent text-yellow-600 hover:bg-accent"
            size="sm"
            variant="ghost"
          >
            <Flag className="size-4" />
            <span className="font-medium text-xs">Report User</span>
          </Button>
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ============================================================================
// CONVERSATION ITEM COMPONENT
// ============================================================================

function ConversationItem({ conversation, isSelected, onClick, typingUsers, userStatuses }) {
  const isTyping = typingUsers[conversation.peer_id];
  const status = userStatuses[conversation.peer_id];
  const unreadCount = conversation.unread_count || 0;

  return (
    <div
      onClick={onClick}
      className={`flex items-center gap-3 p-3 cursor-pointer transition-all hover:bg-accent/50 border-l-2 ${
        isSelected ? 'bg-accent border-l-primary' : 'border-l-transparent'
      } ${unreadCount > 0 ? 'bg-primary/5' : ''}`}
    >
      {/* Avatar with status */}
      <div className="relative flex-shrink-0">
        <Avatar className="size-12">
          <AvatarImage 
            src={getAvatarUrl(conversation.peer_id, conversation.first_name)} 
            alt={conversation.first_name || 'User'} 
          />
          <AvatarFallback className="bg-primary/10 text-primary font-medium">
            {getInitials(conversation.first_name, conversation.last_name)}
          </AvatarFallback>
        </Avatar>
        <StatusBadge 
          status={status} 
          className="absolute bottom-0 right-0"
        />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between gap-2">
          <span className={`font-medium truncate ${unreadCount > 0 ? 'text-foreground' : 'text-foreground/80'}`}>
            {conversation.first_name} {conversation.last_name}
          </span>
          <span className="text-xs text-muted-foreground flex-shrink-0">
            {formatTime(conversation.last_msg_date)}
          </span>
        </div>
        <div className="flex items-center justify-between gap-2 mt-0.5">
          <span className={`text-sm truncate ${unreadCount > 0 ? 'text-foreground font-medium' : 'text-muted-foreground'}`}>
            {isTyping ? (
              <span className="text-primary italic flex items-center gap-1">
                <span className="flex gap-0.5">
                  <span className="w-1 h-1 bg-primary rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                  <span className="w-1 h-1 bg-primary rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                  <span className="w-1 h-1 bg-primary rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                </span>
                typing...
              </span>
            ) : conversation.last_msg_is_outgoing ? (
              <span className="flex items-center gap-1">
                <CheckCheck className="size-3 text-muted-foreground" />
                {conversation.last_msg_text || 'No messages'}
              </span>
            ) : (
              conversation.last_msg_text || 'No messages'
            )}
          </span>
          {unreadCount > 0 && (
            <Badge 
              variant="default" 
              className="h-5 min-w-[20px] flex items-center justify-center text-xs font-bold px-1.5 bg-primary"
            >
              +{unreadCount}
            </Badge>
          )}
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// MESSAGE BUBBLE COMPONENT
// ============================================================================

function MessageBubble({ message, isOutgoing, peerInfo, onReply }) {
  return (
    <div className={cn("group my-4 flex gap-2", isOutgoing ? "justify-end" : "justify-start")}>
      <div className={cn("flex max-w-[80%] items-start gap-2", isOutgoing ? "flex-row-reverse" : undefined)}>
        {/* Avatar on every message */}
        <Avatar className="size-8">
          {isOutgoing ? (
            <AvatarFallback className="bg-primary text-primary-foreground text-xs">
              You
            </AvatarFallback>
          ) : (
            <>
              <AvatarImage
                src={getAvatarUrl(peerInfo?.peer_id, peerInfo?.first_name)}
                alt={peerInfo?.first_name || 'User'}
              />
              <AvatarFallback className="bg-accent text-xs">
                {getInitials(peerInfo?.first_name, peerInfo?.last_name)}
              </AvatarFallback>
            </>
          )}
        </Avatar>

        {/* Message content */}
        <div>
          <div
            className={cn(
              "rounded-md px-3 py-2 text-sm",
              isOutgoing
                ? "bg-primary text-primary-foreground"
                : "bg-accent text-foreground"
            )}
          >
            <p className="whitespace-pre-wrap break-words">{message.text}</p>
          </div>

          {/* Time, read status, and actions */}
          <div className="mt-1 flex items-center gap-2">
            <time className="text-muted-foreground text-xs">
              {formatMessageTime(message.date)}
            </time>
            {isOutgoing && (
              <span className="text-muted-foreground">
                {message.is_read ? (
                  <CheckCheck className="size-3 text-primary" />
                ) : (
                  <Check className="size-3" />
                )}
              </span>
            )}
            {/* Message actions with opacity transition */}
            <div className="opacity-0 transition-all group-hover:opacity-100">
              <MessageActions message={message} isOutgoing={isOutgoing} onReply={onReply} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// RATE LIMIT INDICATOR
// ============================================================================

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

// ============================================================================
// MAIN INBOX COMPONENT
// ============================================================================

function Inbox() {
  const { accounts } = useAccounts();
  const [selectedPhone, setSelectedPhone] = useState(null);
  const [messageInput, setMessageInput] = useState('');
  const [replyingTo, setReplyingTo] = useState(null);
  const [sending, setSending] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState({});
  const [searchQuery, setSearchQuery] = useState('');
  const [isConnecting, setIsConnecting] = useState(false);
  const [connectAttempted, setConnectAttempted] = useState({});
  const [showScrollButton, setShowScrollButton] = useState(false);
  const messagesEndRef = useRef(null);
  const scrollAreaRef = useRef(null);
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
    if (!selectedPhone && accounts.length > 0) {
      const activeAccount = accounts.find(a => a.status === 'active');
      if (activeAccount) {
        setSelectedPhone(activeAccount.phone);
      }
    }
  }, [accounts, selectedPhone]);

  // Fetch conversations when phone changes
  useEffect(() => {
    if (selectedPhone) {
      fetchConversations();
      fetchRateLimitStatus();
    }
  }, [selectedPhone, fetchConversations, fetchRateLimitStatus]);

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

  // Auto-connect when account is selected but not connected
  useEffect(() => {
    const autoConnect = async () => {
      if (!selectedPhone) return;

      const isConnected = connectionStatus[selectedPhone]?.connected;
      if (isConnected) return;

      if (connectAttempted[selectedPhone]) return;

      setConnectAttempted(prev => ({ ...prev, [selectedPhone]: true }));
      setIsConnecting(true);

      try {
        console.log('Auto-connecting account:', selectedPhone);
        const response = await connectInbox(selectedPhone);

        if (response.data?.success) {
          console.log('Connected successfully, syncing dialogs...');
          const statusResponse = await getInboxConnectionStatus();
          if (statusResponse.data?.success) {
            setConnectionStatus(statusResponse.data.connections || {});
          }
          await triggerSync();
          await fetchConversations();
        } else {
          console.error('Failed to connect:', response.data?.message);
        }
      } catch (err) {
        console.error('Auto-connect failed:', err);
      } finally {
        setIsConnecting(false);
      }
    };

    const timer = setTimeout(autoConnect, 500);
    return () => clearTimeout(timer);
  }, [selectedPhone, connectionStatus, connectAttempted, triggerSync, fetchConversations]);

  // Handle account selection
  const handleAccountSelect = (phone) => {
    setSelectedPhone(phone);
    clearSelection();
  };

  // Handle reply to message
  const handleReply = (message) => {
    setReplyingTo(message);
    inputRef.current?.focus();
  };

  // Cancel reply
  const cancelReply = () => {
    setReplyingTo(null);
  };

  // Handle send message
  const handleSendMessage = async (e) => {
    e.preventDefault();
    if (!messageInput.trim() || !selectedPeer || sending) return;

    setSending(true);
    try {
      await sendMessage(selectedPeer, messageInput.trim());
      setMessageInput('');
      setReplyingTo(null);
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

  // Scroll to bottom
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  // Filter conversations by search
  const filteredConversations = conversations.filter(conv => {
    if (!searchQuery) return true;
    const name = `${conv.first_name} ${conv.last_name}`.toLowerCase();
    const username = (conv.username || '').toLowerCase();
    const query = searchQuery.toLowerCase();
    return name.includes(query) || username.includes(query);
  });

  // Calculate total unread
  const totalUnread = conversations.reduce((sum, conv) => sum + (conv.unread_count || 0), 0);

  // Get active accounts for selector
  const activeAccounts = accounts.filter(a => a.status === 'active');
  const isAccountConnected = connectionStatus[selectedPhone]?.connected;

  return (
    <div className="h-[calc(100vh-4rem)] flex flex-col bg-background">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b bg-card">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <MessageCircle className="w-6 h-6 text-primary" />
            <h1 className="text-xl font-bold">Inbox</h1>
          </div>
          
          {totalUnread > 0 && (
            <Badge variant="destructive" className="font-bold">
              +{totalUnread} unread
            </Badge>
          )}
          
          {selectedPhone && (
            <Badge variant={
              isAccountConnected ? 'default' :
              isConnecting ? 'outline' :
              connectionStatus[selectedPhone]?.state === 'auth_required' ? 'destructive' :
              'secondary'
            }>
              {isConnecting ? (
                <>
                  <Loader2 className="w-3 h-3 mr-1 animate-spin" />
                  Connecting...
                </>
              ) : isAccountConnected ? (
                <>
                  <Wifi className="w-3 h-3 mr-1" />
                  Connected
                </>
              ) : connectionStatus[selectedPhone]?.state === 'auth_required' ? (
                <>
                  <AlertCircle className="w-3 h-3 mr-1" />
                  Needs Auth
                </>
              ) : (
                <>
                  <WifiOff className="w-3 h-3 mr-1" />
                  Disconnected
                </>
              )}
            </Badge>
          )}

          {/* Show error message if auth required */}
          {selectedPhone && connectionStatus[selectedPhone]?.error && !isAccountConnected && (
            <span className="text-xs text-destructive">
              {connectionStatus[selectedPhone].error}
            </span>
          )}
        </div>

        {/* Account selector */}
        <div className="flex items-center gap-2">
          <select
            value={selectedPhone || ''}
            onChange={(e) => handleAccountSelect(e.target.value)}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <option value="">Select account...</option>
            {activeAccounts.map(account => (
              <option key={account.phone} value={account.phone}>
                {account.name || account.phone}
                {connectionStatus[account.phone]?.connected ? ' ✓' : ''}
              </option>
            ))}
          </select>

          <Button
            variant="outline"
            size="sm"
            onClick={handleSync}
            disabled={!selectedPhone || !isAccountConnected}
            title="Sync dialogs"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
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
                <Users className="w-12 h-12 mb-4 opacity-50" />
                <p className="font-medium">Select an account</p>
                <p className="text-sm">Choose an account to view conversations</p>
              </div>
            ) : isConnecting ? (
              <div className="flex flex-col items-center justify-center h-full p-4 text-center text-muted-foreground">
                <Loader2 className="w-12 h-12 mb-4 animate-spin text-primary" />
                <p className="font-medium">Connecting to Telegram...</p>
                <p className="text-sm">Syncing your conversations</p>
              </div>
            ) : loading ? (
              <div className="flex items-center justify-center h-full">
                <Loader2 className="w-8 h-8 animate-spin text-primary" />
              </div>
            ) : filteredConversations.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full p-4 text-center text-muted-foreground">
                <MessageCircle className="w-12 h-12 mb-4 opacity-50" />
                <p className="font-medium">{searchQuery ? 'No matches found' : 'No conversations'}</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-3"
                  onClick={handleSync}
                >
                  <RefreshCw className="w-4 h-4 mr-2" />
                  Sync dialogs
                </Button>
              </div>
            ) : (
              <div className="divide-y">
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
            <div className="p-3 border-t bg-muted/30">
              <RateLimitIndicator status={rateLimitStatus} />
            </div>
          )}
        </div>

        {/* Messages panel */}
        <div className="flex-1 flex flex-col bg-background">
          {!selectedPeer ? (
            <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground">
              <div className="w-20 h-20 rounded-full bg-muted flex items-center justify-center mb-4">
                <MessageCircle className="w-10 h-10 opacity-50" />
              </div>
              <p className="text-lg font-medium">Select a conversation</p>
              <p className="text-sm">Choose a conversation to start messaging</p>
            </div>
          ) : (
            <>
              {/* Conversation header */}
              <div className="flex items-center gap-3 px-4 py-3 border-b bg-card">
                <Button
                  variant="ghost"
                  size="sm"
                  className="md:hidden -ml-2"
                  onClick={clearSelection}
                >
                  <ChevronLeft className="w-5 h-5" />
                </Button>

                <div className="relative">
                  <Avatar className="size-10">
                    <AvatarImage 
                      src={getAvatarUrl(selectedConversation?.peer_id, selectedConversation?.first_name)} 
                      alt={selectedConversation?.first_name || 'User'} 
                    />
                    <AvatarFallback>
                      {getInitials(selectedConversation?.first_name, selectedConversation?.last_name)}
                    </AvatarFallback>
                  </Avatar>
                  <StatusBadge 
                    status={userStatuses[selectedPeer]} 
                    className="absolute bottom-0 right-0"
                  />
                </div>

                <div className="flex-1 min-w-0">
                  <div className="font-semibold truncate">
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
                      <span className="text-primary font-medium">typing...</span>
                    ) : userStatuses[selectedPeer]?.online ? (
                      <span className="text-green-500">online</span>
                    ) : userStatuses[selectedPeer]?.last_seen ? (
                      <span>last seen {formatTime(userStatuses[selectedPeer].last_seen)}</span>
                    ) : null}
                  </div>
                </div>

                {/* User actions menu */}
                <UserActionsMenu />
              </div>

              {/* Messages */}
              <div className="flex-1 relative overflow-hidden">
                <ScrollArea ref={scrollAreaRef} className="h-full p-4">
                  {loadingMessages ? (
                    <div className="flex items-center justify-center h-full">
                      <Loader2 className="w-8 h-8 animate-spin text-primary" />
                    </div>
                  ) : messages.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
                      <MessageCircle className="w-12 h-12 mb-4 opacity-50" />
                      <p>No messages yet</p>
                      <p className="text-sm">Send a message to start the conversation</p>
                    </div>
                  ) : (
                    <div className="space-y-1">
                      {messages.length >= 50 && (
                        <div className="text-center py-2">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => loadMoreMessages(selectedPeer)}
                            className="text-muted-foreground hover:text-foreground"
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
                          peerInfo={selectedConversation}
                          onReply={handleReply}
                        />
                      ))}
                      <div ref={messagesEndRef} />
                    </div>
                  )}
                </ScrollArea>

                {/* Scroll to bottom button */}
                {showScrollButton && (
                  <Button
                    variant="secondary"
                    size="icon"
                    className="absolute bottom-4 right-4 rounded-full shadow-lg"
                    onClick={scrollToBottom}
                  >
                    <ArrowDown className="w-4 h-4" />
                  </Button>
                )}
              </div>

              {/* Reply preview */}
              {replyingTo && (
                <div className="px-4 py-2 border-t bg-muted/50 flex items-center gap-2">
                  <Reply className="w-4 h-4 text-muted-foreground" />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-muted-foreground">Replying to</p>
                    <p className="text-sm truncate">{replyingTo.text}</p>
                  </div>
                  <Button variant="ghost" size="sm" onClick={cancelReply}>
                    ✕
                  </Button>
                </div>
              )}

              {/* Message input */}
              <form onSubmit={handleSendMessage} className="p-4 border-t bg-card">
                {error && (
                  <div className="flex items-center gap-2 text-destructive text-sm mb-2 p-2 bg-destructive/10 rounded">
                    <AlertCircle className="w-4 h-4" />
                    {error}
                    <Button variant="ghost" size="sm" onClick={clearError} className="ml-auto">
                      Dismiss
                    </Button>
                  </div>
                )}
                <div className="flex gap-2">
                  <Input
                    ref={inputRef}
                    placeholder={isAccountConnected ? "Type a message..." : "Connect to send messages"}
                    value={messageInput}
                    onChange={(e) => setMessageInput(e.target.value)}
                    disabled={sending || !isAccountConnected}
                    className="flex-1"
                  />
                  <Button
                    type="submit"
                    disabled={!messageInput.trim() || sending || !isAccountConnected}
                    className="px-4"
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
