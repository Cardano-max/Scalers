'use client';

/**
 * Reusable feed event row — renders a single FeedEvent as a clickable,
 * expandable row with drill buttons to Activity/Runs (only when relevant IDs exist).
 * Used by both FeedScreen (full feed) and SmokeScreen (Overview.feedPreview).
 */
import { useState } from 'react';
import { useConsole } from '@/state/console-store';
import { Dot } from './icons';
import { clockTime } from './console-bits';
import { WORKER_COLOR, SEVERITY_COLOR } from '@/lib/tokens';
import type { FeedEvent } from '@/lib/data/models';

export function FeedRow({ event }: { event: FeedEvent }) {
  const console = useConsole();
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div style={{ animation: 'feedIn 0.3s ease-out' }}>
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        style={{
          display: 'flex',
          gap: 10,
          alignItems: 'flex-start',
          padding: '9px 2px',
          borderBottom: '1px solid #F2F0EA',
          width: '100%',
          background: 'none',
          border: 'none',
          textAlign: 'left',
          cursor: 'pointer',
          color: 'inherit',
          fontFamily: 'inherit',
          fontSize: 'inherit',
          transition: 'background 0.15s',
        }}
        onMouseEnter={(e) => {
          (e.currentTarget as HTMLElement).style.background = 'rgba(0,0,0,0.01)';
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLElement).style.background = 'none';
        }}
      >
        <Dot color={WORKER_COLOR[event.worker] || '#8C877D'} />
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 3 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9, flexWrap: 'wrap' }}>
            <span
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: WORKER_COLOR[event.worker] || '#8C877D',
                background: 'rgba(0,0,0,0.02)',
                padding: '2px 6px',
                borderRadius: 4,
                textTransform: 'uppercase',
              }}
            >
              {event.worker}
            </span>
            {event.chip && (
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 600,
                  color: SEVERITY_COLOR[event.severity].text,
                  background: SEVERITY_COLOR[event.severity].bg,
                  padding: '2px 6px',
                  borderRadius: 5,
                }}
              >
                {event.chip}
              </span>
            )}
          </div>
          <span style={{ fontSize: 13.5, color: '#1A1A17', lineHeight: 1.45 }}>{event.text}</span>
        </div>
        <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: '#A8A299', flex: '0 0 auto', paddingTop: 2 }}>{clockTime(event.at)}</span>
      </button>

      {/* Expanded panel */}
      {isOpen && (
        <div
          style={{
            padding: '12px 28px 12px 42px',
            background: 'rgba(0,0,0,0.01)',
            borderBottom: '1px solid #F2F0EA',
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
          }}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ fontSize: 10, fontFamily: "'IBM Plex Mono', monospace", color: '#A8A299', letterSpacing: '0.5px' }}>EVENT DETAILS</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 13, flexWrap: 'wrap' }}>
              {event.actionId && (
                <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: '#5C584F' }}>action: {event.actionId}</span>
              )}
              {event.runId && (
                <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: '#5C584F' }}>run: {event.runId}</span>
              )}
              {event.decisionId && (
                <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: '#5C584F' }}>decision: {event.decisionId}</span>
              )}
            </div>
          </div>

          {/* Drill buttons */}
          <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
            {event.actionId && (
              <button
                type="button"
                onClick={() => console.navigate('activity', event.actionId)}
                style={{
                  fontSize: 11.5,
                  fontWeight: 600,
                  color: '#0B6F68',
                  background: '#fff',
                  border: '1px solid #C9E5E1',
                  padding: '6px 10px',
                  borderRadius: 8,
                  cursor: 'pointer',
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.background = '#F1EFEA';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.background = '#fff';
                }}
              >
                Open in Activity →
              </button>
            )}
            {event.runId && (
              <button
                type="button"
                onClick={() => console.navigate('runs', event.runId)}
                style={{
                  fontSize: 11.5,
                  fontWeight: 500,
                  color: '#46423B',
                  background: '#fff',
                  border: '1px solid #E0DCD3',
                  padding: '6px 10px',
                  borderRadius: 8,
                  cursor: 'pointer',
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.background = '#F1EFEA';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.background = '#fff';
                }}
              >
                Open run →
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
