'use client';

import { useEffect, useRef, useState } from 'react';
import { useData } from '@/lib/data/DataProvider';
import type { ChatMessage } from '@/lib/data/models';

export function CommandScreen() {
  const { adapter, tenantId } = useData();
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: 'sys_init',
      role: 'ASSISTANT',
      text: 'Harness is running for Northwind Heating & Air. 6 actions are in your review queue and outreach batch run_4821 is in progress. Autonomy is at 87% today. What do you want to do?',
      label: 'Harness',
      at: '2026-06-29T13:40:00Z',
    },
  ]);
  const [input, setInput] = useState('');
  const [thinking, setThinking] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const suggestions = ['Pause the engine', 'Raise Gmail threshold to 0.90', 'Run outreach batch for tomorrow'];

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, thinking]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim()) return;

    const userMsg: ChatMessage = {
      id: `msg_${Date.now()}`,
      role: 'OPERATOR',
      text: input,
      at: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setThinking(true);

    try {
      const response = await adapter.sendCommand(tenantId, input);
      setMessages((prev) => [...prev, response]);
    } catch (err) {
      console.error('Failed to send command:', err);
    } finally {
      setThinking(false);
    }
  };

  return (
    <section
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
      }}
    >
      {/* Messages */}
      <div
        ref={scrollRef}
        style={{
          flex: 1,
          width: '100%',
          maxWidth: 780,
          overflowY: 'auto',
          padding: '28px 24px 14px',
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
          minHeight: 0,
        }}
      >
        {messages.map((m) => (
          <div
            key={m.id}
            style={{
              display: 'flex',
              gap: 11,
              alignItems: 'flex-start',
              justifyContent: m.role === 'OPERATOR' ? 'flex-end' : 'flex-start',
              minWidth: 0,
            }}
          >
            {m.role === 'ASSISTANT' && (
              <div
                style={{
                  width: 36,
                  height: 36,
                  borderRadius: '50%',
                  background: '#0F8A82',
                  color: '#fff',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontWeight: 600,
                  fontSize: 14,
                  flex: '0 0 auto',
                }}
              >
                S
              </div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5, maxWidth: '80%', minWidth: 0 }}>
              {m.role === 'ASSISTANT' && m.label && (
                <span style={{ fontSize: 11, fontFamily: "'IBM Plex Mono', monospace", color: '#A8A299', paddingLeft: 2 }}>{m.label}</span>
              )}
              <div
                style={{
                  fontSize: 14,
                  lineHeight: 1.6,
                  padding: '15px 17px',
                  borderRadius: '14px',
                  border: '1px solid var(--hairline)',
                  background: m.role === 'ASSISTANT' ? '#fff' : 'var(--accent)',
                  color: m.role === 'ASSISTANT' ? '#1A1A17' : '#fff',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {m.text}
              </div>
            </div>
          </div>
        ))}

        {/* Thinking indicator */}
        {thinking && (
          <div
            style={{
              display: 'flex',
              gap: 11,
              alignItems: 'flex-start',
            }}
          >
            <div
              style={{
                width: 36,
                height: 36,
                borderRadius: '50%',
                background: '#0F8A82',
                color: '#fff',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontWeight: 600,
                fontSize: 14,
                flex: '0 0 auto',
              }}
            >
              S
            </div>
            <div
              style={{
                background: '#fff',
                border: '1px solid var(--hairline)',
                borderRadius: 14,
                padding: '15px 17px',
                display: 'flex',
                gap: 5,
                alignItems: 'center',
              }}
            >
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: '50%',
                  background: '#A8A299',
                  animation: 'dot 1.2s ease-in-out infinite',
                }}
              />
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: '50%',
                  background: '#A8A299',
                  animation: 'dot 1.2s ease-in-out 0.2s infinite',
                }}
              />
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: '50%',
                  background: '#A8A299',
                  animation: 'dot 1.2s ease-in-out 0.4s infinite',
                }}
              />
            </div>
          </div>
        )}
      </div>

      {/* Input area */}
      <div style={{ width: '100%', maxWidth: 780, padding: '0 24px 22px', flex: '0 0 auto' }}>
        {/* Suggestions */}
        {!thinking && (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 11 }}>
            {suggestions.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setInput(s)}
                style={{
                  background: '#fff',
                  border: '1px solid #E0DCD3',
                  color: '#46423B',
                  fontSize: 12.5,
                  fontWeight: 500,
                  padding: '8px 13px',
                  borderRadius: 9,
                  cursor: 'pointer',
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.background = '#F1EFEA';
                  (e.currentTarget as HTMLElement).style.borderColor = '#D2CCC1';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.background = '#fff';
                  (e.currentTarget as HTMLElement).style.borderColor = '#E0DCD3';
                }}
              >
                {s}
              </button>
            ))}
          </div>
        )}

        {/* Input form */}
        <form
          onSubmit={handleSubmit}
          style={{
            display: 'flex',
            gap: 10,
            alignItems: 'center',
            background: '#fff',
            border: '1px solid #E0DCD3',
            borderRadius: 13,
            padding: '7px 7px 7px 16px',
            boxShadow: '0 1px 3px rgba(26,26,23,0.04)',
          }}
        >
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Send a command to the harness…"
            disabled={thinking}
            style={{
              flex: 1,
              border: 'none',
              outline: 'none',
              background: 'transparent',
              fontSize: 14,
              padding: '8px 0',
            }}
          />
          <button
            type="submit"
            disabled={thinking || !input.trim()}
            style={{
              background: '#0F8A82',
              color: '#fff',
              border: 'none',
              padding: '8px 18px',
              borderRadius: 8,
              fontSize: 13,
              fontWeight: 600,
              cursor: 'pointer',
              opacity: thinking || !input.trim() ? 0.5 : 1,
            }}
            onMouseEnter={(e) => {
              if (!thinking && input.trim()) {
                (e.currentTarget as HTMLElement).style.background = '#0B6F68';
              }
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLElement).style.background = '#0F8A82';
            }}
          >
            Send
          </button>
        </form>

        <div style={{ fontSize: 11, fontFamily: "'IBM Plex Mono', monospace", color: '#A8A299', marginTop: 10, textAlign: 'center' }}>
          The harness can pause agents, retune thresholds, draft content, and answer questions about runs.
        </div>
      </div>

      <style>{`
        @keyframes dot {
          0%, 80%, 100% {
            opacity: 0.25;
            transform: translateY(0);
          }
          40% {
            opacity: 1;
            transform: translateY(-2px);
          }
        }
      `}</style>
    </section>
  );
}
