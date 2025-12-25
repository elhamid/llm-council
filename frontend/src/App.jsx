import { useState, useEffect } from 'react';
import Sidebar from './components/Sidebar';
import ChatInterface from './components/ChatInterface';
import { api } from './api';
import './App.css';

function App() {
  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(null);

  // Cache conversations so SSE updates can land even if user navigates away
  const [conversationCache, setConversationCache] = useState({});
  // Loading is per conversation (prevents 'all threads spinning')
  const [loadingById, setLoadingById] = useState({});

  const currentConversation = currentConversationId ? conversationCache[currentConversationId] : null;
  const isLoading = !!(currentConversationId && loadingById[currentConversationId]);

const hasAssistantProgress = (conv) => {
  const msgs = conv?.messages || [];
  return msgs.some((m) => m?.role === 'assistant' && (
    m?.error ||
    m?.loading?.stage1 || m?.loading?.stage2 || m?.loading?.stage3 ||
    m?.stage1 || m?.stage2 || m?.stage3
  ));
};

const hasAssistantRunning = (conv) => {
  const msgs = conv?.messages || [];
  return msgs.some((m) => m?.role === 'assistant' && (
    m?.loading?.stage1 || m?.loading?.stage2 || m?.loading?.stage3
  ));
};

const serverLooksComplete = (conv) => {
  const msgs = conv?.messages || [];
  return msgs.some((m) => m?.role === 'assistant' && (
    m?.stage3 || m?.error || (typeof m?.content === 'string' && m.content.trim().length > 0)
  ));
};

  // Load conversations on mount
  useEffect(() => {
    loadConversations();
  }, []);

  // Load conversation details when selected
  useEffect(() => {
    if (currentConversationId) {
      loadConversation(currentConversationId);
    } else {
      // derived from cache (no-op)
    }
  }, [currentConversationId]);

  const loadConversations = async () => {
    try {
      const convs = await api.listConversations();
      convs.sort((a,b) => ((b.updated_at || b.created_at || '').localeCompare(a.updated_at || a.created_at || '')));
      setConversations(convs);
    } catch (error) {
      console.error('Failed to load conversations:', error);
    }
  };

  const loadConversation = async (id) => {
    try {
      const conv = await api.getConversation(id);

      // IMPORTANT:
      // Do not overwrite a richer in-memory conversation (e.g., in-flight SSE placeholder + stage progress)
      // with a poorer server snapshot (which may not include in-progress data yet).
      setConversationCache((prev) => {
        const existing = prev?.[id];
        const existingLen = existing?.messages?.length || 0;
        const serverLen = conv?.messages?.length || 0;

        if (existing && existingLen > serverLen) {
          return { ...prev, [id]: { ...conv, messages: existing.messages } };
        }
        return { ...prev, [id]: conv };
      });
    } catch (error) {
      console.error('Failed to load conversation:', error);
    }
  };

  const handleNewConversation = async () => {
      try {
        // Guardrail: do not create multiple empty "New conversation" threads
        const existingEmpty = conversations
          .filter((c) => {
            const n = (c.messages?.length ?? c.message_count ?? 0);
            const t = (c.title || '').toLowerCase();
            return n === 0 && (t === 'new conversation' || t === 'new' || t === '');
          })
          .sort((a, b) => ((b.updated_at || b.created_at || '').localeCompare(a.updated_at || a.created_at || '')))[0];

        if (existingEmpty) {
          setCurrentConversationId(existingEmpty.id);
          return;
        }

        const newConv = await api.createConversation();

        // Immediately reflect in UI (prevents the "void" / missing-from-list issue)
        setConversations((prev) => {
          const next = [newConv, ...prev.filter((c) => c.id !== newConv.id)];
          next.sort((a, b) => ((b.updated_at || b.created_at || '').localeCompare(a.updated_at || a.created_at || '')));
          return next;
        });

        setCurrentConversationId(newConv.id);
        setConversationCache((prev) => ({ ...prev, [newConv.id]: newConv }));
      } catch (error) {
        console.error("Failed to create conversation:", error);
      }
    };

  const handleDeleteConversation = async (id) => {
    try {
      await api.deleteConversation(id);

      setConversations((prev) => prev.filter((c) => c.id !== id));

      if (currentConversationId === id) {
        // Select next conversation if available, else clear selection
        const remaining = conversations.filter((c) => c.id !== id);
        setCurrentConversationId(remaining.length ? remaining[0].id : null);
      }
    } catch (error) {
      console.error('Failed to delete conversation:', error);
    }
  };

  const handleSelectConversation = (id) => {
    setCurrentConversationId(id);
  };

  const handleSendMessage = async (content) => {
      if (!currentConversationId) return;

      const cid = currentConversationId;
      setLoadingById((prev) => ({ ...prev, [cid]: true }));

      const updateLastAssistant = (messages, updater) => {
        // Find most recent assistant message (the optimistic placeholder)
        for (let i = messages.length - 1; i >= 0; i--) {
          if (messages[i]?.role === 'assistant') {
            const nextMsg = { ...(messages[i] || {}) };
            updater(nextMsg);
            const next = [...messages];
            next[i] = nextMsg;
            return next;
          }
        }
        return messages;
      };

      try {
        // Optimistic user message
        setConversationCache((prev) => {
          const conv = prev?.[cid] || { id: cid, messages: [] };
          const messages = [...(conv.messages || []), { role: 'user', content }];
          return { ...prev, [cid]: { ...conv, messages } };
        });

        // Optimistic assistant placeholder (stages will fill in)
        const assistantMessage = {
          role: 'assistant',
          stage1: null,
          stage2: null,
          stage3: null,
          metadata: null,
          loading: { stage1: true, stage2: false, stage3: false },
        };

        setConversationCache((prev) => {
          const conv = prev?.[cid] || { id: cid, messages: [] };
          const messages = [...(conv.messages || []), assistantMessage];
          return { ...prev, [cid]: { ...conv, messages } };
        });

        await api.sendMessageStream(cid, content, (eventType, event) => {
          switch (eventType) {
            case 'stage1_start':
              setConversationCache((prev) => {
                const conv = prev?.[cid];
                if (!conv) return prev;
                const messages = updateLastAssistant([...(conv.messages || [])], (msg) => {
                  msg.loading = { ...(msg.loading || {}), stage1: true };
                });
                return { ...prev, [cid]: { ...conv, messages } };
              });
              break;

            case 'stage1_complete':
              setConversationCache((prev) => {
                const conv = prev?.[cid];
                if (!conv) return prev;
                const messages = updateLastAssistant([...(conv.messages || [])], (msg) => {
                  msg.stage1 = event.data;
                  msg.loading = { ...(msg.loading || {}), stage1: false };
                });
                return { ...prev, [cid]: { ...conv, messages } };
              });
              break;

            case 'stage2_start':
              setConversationCache((prev) => {
                const conv = prev?.[cid];
                if (!conv) return prev;
                const messages = updateLastAssistant([...(conv.messages || [])], (msg) => {
                  msg.loading = { ...(msg.loading || {}), stage2: true };
                });
                return { ...prev, [cid]: { ...conv, messages } };
              });
              break;

            case 'stage2_complete':
              setConversationCache((prev) => {
                const conv = prev?.[cid];
                if (!conv) return prev;
                const messages = updateLastAssistant([...(conv.messages || [])], (msg) => {
                  msg.stage2 = event.data;
                  msg.metadata = event.metadata;
                  msg.loading = { ...(msg.loading || {}), stage2: false };
                });
                return { ...prev, [cid]: { ...conv, messages } };
              });
              break;

            case 'stage3_start':
              setConversationCache((prev) => {
                const conv = prev?.[cid];
                if (!conv) return prev;
                const messages = updateLastAssistant([...(conv.messages || [])], (msg) => {
                  msg.loading = { ...(msg.loading || {}), stage3: true };
                });
                return { ...prev, [cid]: { ...conv, messages } };
              });
              break;

            case 'stage3_complete':
              setConversationCache((prev) => {
                const conv = prev?.[cid];
                if (!conv) return prev;
                const messages = updateLastAssistant([...(conv.messages || [])], (msg) => {
                  msg.stage3 = event.data;
                  msg.loading = { ...(msg.loading || {}), stage3: false };
                });
                return { ...prev, [cid]: { ...conv, messages } };
              });
              break;

            case 'title_complete':
              loadConversations();
              break;

            case 'complete':
              loadConversations();
              setLoadingById((prev) => ({ ...prev, [cid]: false }));
              break;

            case 'error': {
              const message =
                event?.message ||
                event?.error ||
                (event?.data && (event.data.message || event.data.error)) ||
                'Stream error';

              setConversationCache((prev) => {
                const conv = prev?.[cid];
                if (!conv) return prev;
                const messages = updateLastAssistant([...(conv.messages || [])], (msg) => {
                  msg.error = message;
                  msg.loading = { ...(msg.loading || {}), stage1: false, stage2: false, stage3: false };
                });
                return { ...prev, [cid]: { ...conv, messages } };
              });

              setLoadingById((prev) => ({ ...prev, [cid]: false }));
              break;
            }

            default:
              break;
          }
        });
      } catch (error) {
        console.error('Failed to send message:', error);

        // Remove optimistic user + assistant messages (scoped to cid)
        setConversationCache((prev) => {
          const conv = prev?.[cid];
          if (!conv) return prev;
          const messages = (conv.messages || []).slice(0, -2);
          return { ...prev, [cid]: { ...conv, messages } };
        });

        setLoadingById((prev) => ({ ...prev, [cid]: false }));
      }
    };


  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        currentConversationId={currentConversationId}
        onSelectConversation={handleSelectConversation}
        onNewConversation={handleNewConversation}
        onDeleteConversation={handleDeleteConversation}
      />
      <ChatInterface
        conversation={currentConversation}
        onSendMessage={handleSendMessage}
        isLoading={isLoading}
      />
    </div>
  );
}

export default App;
