'use client';

import { useEffect, useRef, useState } from 'react';
import { useData } from '@/lib/data/DataProvider';
import { useConsole } from '@/state/console-store';

interface StudioState {
  goal: string;
  audience: string;
  channels: string[];
  constraints: string;
  hooks: string;
}

interface CampaignResult {
  runId: string;
  actionIds: string[];
  status: string;
}

export function CommandScreen() {
  const { adapter, tenantId } = useData();
  const { navigate } = useConsole();

  const [formData, setFormData] = useState<StudioState>({
    goal: '',
    audience: '',
    channels: [],
    constraints: '',
    hooks: '',
  });

  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<CampaignResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);

  const channelOptions = ['Instagram', 'Facebook', 'Email'];

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [result, running]);

  useEffect(() => {
    let interval: NodeJS.Timeout | null = null;

    if (running) {
      setElapsedSeconds(0);
      interval = setInterval(() => {
        setElapsedSeconds((prev) => prev + 1);
      }, 1000);
    }

    return () => {
      if (interval) clearInterval(interval);
    };
  }, [running]);

  const formatElapsed = (seconds: number): string => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  const toggleChannel = (channel: string) => {
    setFormData((prev) => ({
      ...prev,
      channels: prev.channels.includes(channel)
        ? prev.channels.filter((c) => c !== channel)
        : [...prev.channels, channel],
    }));
  };

  const handleRunCampaign = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setResult(null);

    if (!formData.goal.trim() || !formData.audience.trim() || formData.channels.length === 0) {
      setError('Goal, audience, and at least one channel are required');
      return;
    }

    setRunning(true);

    try {
      const hooksArray = formData.hooks
        .split(',')
        .map((h) => h.trim())
        .filter((h) => h.length > 0);

      const brief = {
        goal: formData.goal,
        audience: formData.audience,
        channels: formData.channels.map((c) => c.toLowerCase()),
        constraints: formData.constraints || undefined,
        hooks: hooksArray.length > 0 ? hooksArray : undefined,
      };

      const res = await adapter.startCampaign(tenantId, brief);
      setResult(res);
    } catch (err) {
      console.error('Failed to start campaign:', err);
      setError(err instanceof Error ? err.message : 'Failed to start campaign');
    } finally {
      setRunning(false);
    }
  };

  const handleReset = () => {
    setFormData({
      goal: '',
      audience: '',
      channels: [],
      constraints: '',
      hooks: '',
    });
    setResult(null);
    setError(null);
  };

  const steps = ['Research', 'Strategy', 'Draft', 'Jury', 'Route'];

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
      {/* Main content */}
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
          gap: 24,
          minHeight: 0,
        }}
      >
        {!result ? (
          <>
            {/* Header */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <h1 style={{ margin: 0, fontSize: 24, fontWeight: 600, color: '#1A1A17' }}>Campaign Studio</h1>
              <p style={{ margin: 0, fontSize: 14, lineHeight: 1.5, color: '#6B6461' }}>
                Design a brief and launch a multi-step campaign across your channels. The harness will research, strategize, draft content, and route with your autonomy settings (HELD = approve-first).
              </p>
            </div>

            {/* Form */}
            <form onSubmit={handleRunCampaign} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              {/* Goal */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                <label style={{ fontSize: 12, fontWeight: 600, color: '#46423B', textTransform: 'uppercase', letterSpacing: 0.5 }}>
                  Goal
                </label>
                <input
                  type="text"
                  value={formData.goal}
                  onChange={(e) => setFormData((prev) => ({ ...prev, goal: e.target.value }))}
                  placeholder="e.g., Increase engagement for summer campaign"
                  disabled={running}
                  style={{
                    fontSize: 14,
                    padding: '10px 12px',
                    border: '1px solid var(--hairline)',
                    borderRadius: 9,
                    background: '#fff',
                    fontFamily: 'inherit',
                    outline: 'none',
                    opacity: running ? 0.6 : 1,
                  }}
                  onFocus={(e) => {
                    if (!running) (e.target as HTMLInputElement).style.borderColor = '#0F8A82';
                  }}
                  onBlur={(e) => {
                    (e.target as HTMLInputElement).style.borderColor = 'var(--hairline)';
                  }}
                />
              </div>

              {/* Audience */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                <label style={{ fontSize: 12, fontWeight: 600, color: '#46423B', textTransform: 'uppercase', letterSpacing: 0.5 }}>
                  Audience
                </label>
                <input
                  type="text"
                  value={formData.audience}
                  onChange={(e) => setFormData((prev) => ({ ...prev, audience: e.target.value }))}
                  placeholder="e.g., Women ages 25-35 interested in sustainable fashion"
                  disabled={running}
                  style={{
                    fontSize: 14,
                    padding: '10px 12px',
                    border: '1px solid var(--hairline)',
                    borderRadius: 9,
                    background: '#fff',
                    fontFamily: 'inherit',
                    outline: 'none',
                    opacity: running ? 0.6 : 1,
                  }}
                  onFocus={(e) => {
                    if (!running) (e.target as HTMLInputElement).style.borderColor = '#0F8A82';
                  }}
                  onBlur={(e) => {
                    (e.target as HTMLInputElement).style.borderColor = 'var(--hairline)';
                  }}
                />
              </div>

              {/* Channels */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                <label style={{ fontSize: 12, fontWeight: 600, color: '#46423B', textTransform: 'uppercase', letterSpacing: 0.5 }}>
                  Channels
                </label>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  {channelOptions.map((ch) => (
                    <button
                      key={ch}
                      type="button"
                      onClick={() => toggleChannel(ch)}
                      disabled={running}
                      style={{
                        fontSize: 13,
                        fontWeight: 500,
                        padding: '8px 14px',
                        border: '1px solid',
                        borderColor: formData.channels.includes(ch) ? '#0F8A82' : 'var(--hairline)',
                        borderRadius: 9,
                        background: formData.channels.includes(ch) ? '#0F8A82' : '#fff',
                        color: formData.channels.includes(ch) ? '#fff' : '#46423B',
                        cursor: running ? 'not-allowed' : 'pointer',
                        opacity: running ? 0.6 : 1,
                        transition: 'all 0.15s ease',
                      }}
                      onMouseEnter={(e) => {
                        if (!running) {
                          (e.currentTarget as HTMLElement).style.borderColor = '#0F8A82';
                          if (!formData.channels.includes(ch)) {
                            (e.currentTarget as HTMLElement).style.background = '#F5F3F0';
                          }
                        }
                      }}
                      onMouseLeave={(e) => {
                        if (!running) {
                          (e.currentTarget as HTMLElement).style.borderColor = formData.channels.includes(ch) ? '#0F8A82' : 'var(--hairline)';
                          (e.currentTarget as HTMLElement).style.background = formData.channels.includes(ch) ? '#0F8A82' : '#fff';
                        }
                      }}
                    >
                      {ch}
                    </button>
                  ))}
                </div>
              </div>

              {/* Constraints */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                <label style={{ fontSize: 12, fontWeight: 600, color: '#46423B', textTransform: 'uppercase', letterSpacing: 0.5 }}>
                  Constraints (optional)
                </label>
                <textarea
                  value={formData.constraints}
                  onChange={(e) => setFormData((prev) => ({ ...prev, constraints: e.target.value }))}
                  placeholder="e.g., No profanity, brand-safe only, max 280 chars per post"
                  disabled={running}
                  style={{
                    fontSize: 14,
                    padding: '10px 12px',
                    border: '1px solid var(--hairline)',
                    borderRadius: 9,
                    background: '#fff',
                    fontFamily: 'inherit',
                    outline: 'none',
                    minHeight: 80,
                    resize: 'vertical',
                    opacity: running ? 0.6 : 1,
                  }}
                  onFocus={(e) => {
                    if (!running) (e.currentTarget as HTMLTextAreaElement).style.borderColor = '#0F8A82';
                  }}
                  onBlur={(e) => {
                    (e.currentTarget as HTMLTextAreaElement).style.borderColor = 'var(--hairline)';
                  }}
                />
              </div>

              {/* Hooks */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                <label style={{ fontSize: 12, fontWeight: 600, color: '#46423B', textTransform: 'uppercase', letterSpacing: 0.5 }}>
                  Hooks (optional, comma-separated)
                </label>
                <input
                  type="text"
                  value={formData.hooks}
                  onChange={(e) => setFormData((prev) => ({ ...prev, hooks: e.target.value }))}
                  placeholder="e.g., summer vibes, new collection, limited time"
                  disabled={running}
                  style={{
                    fontSize: 14,
                    padding: '10px 12px',
                    border: '1px solid var(--hairline)',
                    borderRadius: 9,
                    background: '#fff',
                    fontFamily: 'inherit',
                    outline: 'none',
                    opacity: running ? 0.6 : 1,
                  }}
                  onFocus={(e) => {
                    if (!running) (e.target as HTMLInputElement).style.borderColor = '#0F8A82';
                  }}
                  onBlur={(e) => {
                    (e.target as HTMLInputElement).style.borderColor = 'var(--hairline)';
                  }}
                />
              </div>

              {/* Error */}
              {error && (
                <div
                  style={{
                    fontSize: 13,
                    padding: '12px 14px',
                    borderRadius: 9,
                    background: '#FEF3F0',
                    border: '1px solid #FDCCC1',
                    color: '#8B3A1F',
                  }}
                >
                  {error}
                </div>
              )}

              {/* Run Campaign Button */}
              <button
                type="submit"
                disabled={running}
                style={{
                  fontSize: 14,
                  fontWeight: 600,
                  padding: '12px 20px',
                  border: 'none',
                  borderRadius: 9,
                  background: '#0F8A82',
                  color: '#fff',
                  cursor: running ? 'not-allowed' : 'pointer',
                  opacity: running ? 0.7 : 1,
                  transition: 'all 0.15s ease',
                }}
                onMouseEnter={(e) => {
                  if (!running) (e.currentTarget as HTMLElement).style.background = '#0B6F68';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.background = '#0F8A82';
                }}
              >
                {running ? 'Running Campaign...' : 'Run Campaign'}
              </button>
            </form>

            {/* In-progress timeline */}
            {running && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#46423B', textTransform: 'uppercase', letterSpacing: 0.5 }}>
                    Campaign Pipeline
                  </div>
                  <div style={{ fontSize: 13, color: '#6B6461', lineHeight: 1.4 }}>
                    Working… {formatElapsed(elapsedSeconds)} elapsed
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                  {steps.map((step, idx) => (
                    <div key={step} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <div
                        style={{
                          width: 24,
                          height: 24,
                          borderRadius: '50%',
                          background: '#0F8A82',
                          color: '#fff',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          fontSize: 11,
                          fontWeight: 600,
                          flex: '0 0 auto',
                          animation: 'pulse 1.5s ease-in-out infinite',
                        }}
                      >
                        ·
                      </div>
                      <span style={{ fontSize: 13, color: '#6B6461', whiteSpace: 'nowrap' }}>{step}</span>
                      {idx < steps.length - 1 && (
                        <div style={{ width: 8, height: 1, background: 'var(--hairline)', margin: '0 2px' }} />
                      )}
                    </div>
                  ))}
                </div>
                <div
                  style={{
                    padding: '12px 14px',
                    background: '#F0F9F8',
                    borderRadius: 9,
                    border: '1px solid #C8E7E4',
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: 10,
                  }}
                >
                  <div style={{ fontSize: 18, flex: '0 0 auto' }}>⏱</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: '#0B6F68' }}>Pipeline in Progress</div>
                    <div style={{ fontSize: 13, lineHeight: 1.4, color: '#0F5A52' }}>
                      This runs the real multi-step pipeline (research → strategy → draft → cross-family jury → route). Typically 1-3 minutes — please wait, it is not frozen.
                    </div>
                  </div>
                </div>
              </div>
            )}
          </>
        ) : (
          <>
            {/* Success State */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{ fontSize: 20, fontWeight: 600, color: '#0F8A82' }}>✓ Campaign Launched</div>
                <p style={{ margin: 0, fontSize: 14, lineHeight: 1.5, color: '#6B6461' }}>
                  Your campaign has been queued through the pipeline. Drafts are pending your approve-first sign-off in the review queue.
                </p>
              </div>

              {/* Run ID */}
              <div style={{ padding: '14px 16px', background: '#F9F7F5', borderRadius: 9, border: '1px solid var(--hairline)' }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: '#A8A299', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>
                  Run ID
                </div>
                <div style={{ fontSize: 13, fontFamily: "'IBM Plex Mono', monospace", color: '#1A1A17', wordBreak: 'break-all' }}>
                  {result.runId}
                </div>
              </div>

              {/* Action IDs */}
              {result.actionIds.length > 0 && (
                <div style={{ padding: '14px 16px', background: '#F9F7F5', borderRadius: 9, border: '1px solid var(--hairline)' }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: '#A8A299', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 }}>
                    Draft Action{result.actionIds.length !== 1 ? 's' : ''} ({result.actionIds.length})
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {result.actionIds.map((id, idx) => (
                      <div
                        key={id}
                        style={{
                          fontSize: 12,
                          fontFamily: "'IBM Plex Mono', monospace",
                          color: '#46423B',
                          padding: '6px 8px',
                          background: '#fff',
                          borderRadius: 6,
                          border: '1px solid var(--hairline)',
                          wordBreak: 'break-all',
                        }}
                      >
                        {idx + 1}. {id}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Status Banner */}
              <div
                style={{
                  padding: '12px 14px',
                  background: '#FEF9F5',
                  borderRadius: 9,
                  border: '1px solid #F0E6D8',
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 10,
                }}
              >
                <div style={{ fontSize: 18, flex: '0 0 auto' }}>⏸</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#8B6F47' }}>Drafts PENDING</div>
                  <div style={{ fontSize: 13, lineHeight: 1.4, color: '#6B5E47' }}>
                    Autonomy is HELD. Each draft must be reviewed and approved before sending. View them in the Review queue.
                  </div>
                </div>
              </div>

              {/* Coming Soon Banner */}
              <div
                style={{
                  padding: '12px 14px',
                  background: '#F0F9F8',
                  borderRadius: 9,
                  border: '1px solid #C8E7E4',
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 10,
                }}
              >
                <div style={{ fontSize: 18, flex: '0 0 auto' }}>🚀</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#0B6F68' }}>Richer Insights Coming</div>
                  <div style={{ fontSize: 13, lineHeight: 1.4, color: '#0F5A52' }}>
                    Multi-agent team chat, live research citations, and cost tracking are on the roadmap. For now, we show the real results only — no fabricated reasoning.
                  </div>
                </div>
              </div>

              {/* Navigation Actions */}
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                <button
                  type="button"
                  onClick={() => {
                    navigate('review', result.actionIds[0]);
                  }}
                  style={{
                    flex: '1 1 auto',
                    minWidth: 200,
                    fontSize: 13,
                    fontWeight: 600,
                    padding: '12px 16px',
                    border: '1px solid #0F8A82',
                    borderRadius: 9,
                    background: '#0F8A82',
                    color: '#fff',
                    cursor: 'pointer',
                    transition: 'all 0.15s ease',
                  }}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLElement).style.background = '#0B6F68';
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLElement).style.background = '#0F8A82';
                  }}
                >
                  View Draft in Review Queue
                </button>
                <button
                  type="button"
                  onClick={() => {
                    navigate('runs', result.runId);
                  }}
                  style={{
                    flex: '1 1 auto',
                    minWidth: 200,
                    fontSize: 13,
                    fontWeight: 600,
                    padding: '12px 16px',
                    border: '1px solid var(--hairline)',
                    borderRadius: 9,
                    background: '#fff',
                    color: '#46423B',
                    cursor: 'pointer',
                    transition: 'all 0.15s ease',
                  }}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLElement).style.background = '#F1EFEA';
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLElement).style.background = '#fff';
                  }}
                >
                  View Run Details
                </button>
              </div>

              {/* Reset Button */}
              <button
                type="button"
                onClick={handleReset}
                style={{
                  fontSize: 13,
                  fontWeight: 500,
                  padding: '10px 16px',
                  border: '1px solid var(--hairline)',
                  borderRadius: 9,
                  background: '#fff',
                  color: '#46423B',
                  cursor: 'pointer',
                  transition: 'all 0.15s ease',
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.background = '#F1EFEA';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.background = '#fff';
                }}
              >
                Start Another Campaign
              </button>
            </div>
          </>
        )}
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% {
            opacity: 1;
          }
          50% {
            opacity: 0.6;
          }
        }
      `}</style>
    </section>
  );
}
