/**
 * API client for the LLM Council backend.
 */

const API_BASE = 'http://localhost:8001';

export const api = {
  async listConversations() {
    const response = await fetch(`${API_BASE}/api/conversations?limit=500`);
    if (!response.ok) throw new Error('Failed to list conversations');
    return response.json();
  },

  async createConversation() {
    const response = await fetch(`${API_BASE}/api/conversations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    if (!response.ok) throw new Error('Failed to create conversation');
    return response.json();
  },

  async deleteConversation(conversationId) {
    const response = await fetch(`${API_BASE}/api/conversations/${conversationId}`, {
      method: 'DELETE',
    });
    if (!response.ok) throw new Error('Failed to delete conversation');
    return response.json();
  },

  async getConversation(conversationId) {
    const response = await fetch(`${API_BASE}/api/conversations/${conversationId}`);
    if (!response.ok) throw new Error('Failed to get conversation');
    return response.json();
  },

  async sendMessage(conversationId, content) {
    const response = await fetch(`${API_BASE}/api/conversations/${conversationId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    if (!response.ok) throw new Error('Failed to send message');
    return response.json();
  },

  /**
   * Send a message and receive streaming stage events.
   * Supports:
   *  - JSON response (fallback)
   *  - SSE stream (text/event-stream)
   */
  async sendMessageStream(conversationId, content, onEvent) {
    if (!conversationId) throw new Error('Missing conversationId');
    const response = await fetch(`${API_BASE}/api/conversations/${conversationId}/messages`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      },
      body: JSON.stringify({ content }),
    });

    if (!response.ok) throw new Error('Failed to send message');

    const contentType = response.headers.get('content-type') || '';

    // JSON fallback
    if (contentType.includes('application/json')) {
      const data = await response.json();

      const _yield = () => new Promise((r) => setTimeout(r, 50));
      onEvent('stage1_start', { type: 'stage1_start' });
      onEvent('stage1_complete', { type: 'stage1_complete', data: data.stage1 });

      await _yield();

      onEvent('stage2_start', { type: 'stage2_start' });
      await _yield();

      onEvent('stage2_complete', {
        type: 'stage2_complete',
        data: data.stage2,
        metadata: data.metadata || data.meta || null,
      });

      await _yield();

      onEvent('stage3_start', { type: 'stage3_start' });
      await _yield();

      onEvent('stage3_complete', { type: 'stage3_complete', data: data.stage3 });

      await _yield();

      onEvent('title_complete', { type: 'title_complete' });
      onEvent('complete', { type: 'complete' });
      return;
    }

    // SSE parsing
    if (!response.body) throw new Error('Streaming not supported (no response body)');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();

    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Process complete lines; keep remainder in buffer
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        const trimmed = line.trimEnd();

        // Only handle "data:" lines (your backend uses this)
        if (!trimmed.startsWith('data:')) continue;

        const raw = trimmed.slice(5).trim();
        if (!raw) continue;

        try {
          const event = JSON.parse(raw);
          if (event && event.type) onEvent(event.type, event);
        } catch (e) {
          console.error('Failed to parse SSE data line:', raw, e);
        }
      }
    }
  },
};
