import './Sidebar.css';

export default function Sidebar({
  conversations,
  currentConversationId,
  onSelectConversation,
  onNewConversation,
  onDeleteConversation,
}) {
  return (
    <div className="sidebar">
      <div className="sidebar-header">
        <h1>LLM Council</h1>
        <button className="new-conversation-btn" onClick={onNewConversation}>
          + New Conversation
        </button>
      </div>

      <div className="conversation-list">
        {conversations.length === 0 ? (
          <div className="no-conversations">No conversations yet</div>
        ) : (
          conversations.map((conv) => (
            <div
              key={conv.id}
              className={`conversation-item ${
                conv.id === currentConversationId ? 'active' : ''
              }`}
              onClick={() => onSelectConversation(conv.id)}
              style={{ position: 'relative' }}
            >
              <div className="conversation-title">
                {conv.title || 'New conversation'}
              </div>
              <div className="conversation-meta">
                {conv.message_count ?? 0} messages
              </div>

              <button
                type="button"
                title="Delete conversation"
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  onDeleteConversation(conv.id);
                }}
                style={{
                  position: 'absolute',
                  right: 10,
                  top: 10,
                  border: 'none',
                  background: 'transparent',
                  cursor: 'pointer',
                  fontSize: 16,
                  lineHeight: '16px',
                  opacity: 0.7,
                }}
              >
                🗑️
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
