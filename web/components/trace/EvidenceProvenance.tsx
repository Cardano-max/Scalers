'use client';

/**
 * EvidenceProvenance — the clean, real-only EVIDENCE panel for a staged draft. It
 * surfaces what the draft ACTUALLY used (GET /studio/action/{id}/evidence) as
 * clickable chips and small cards: the brand-voice doc it followed, the CSV /
 * customer facts, lead memories, internal notes, the research it cited, its tool
 * calls, the critic/jury verdicts, and the producing agent.
 *
 * HONEST-EMPTY is mandatory and matches the engine's real-only contract: any
 * category with no data is OMITTED entirely (no empty header, no placeholder); if
 * the whole evidence is null/empty we show a single muted line. NEVER raw JSON —
 * there is no JSON.stringify anywhere here; everything renders as labeled UI.
 *
 * Visual style mirrors ConfidenceEvidence's teal reasoning panel + LineageChips.
 */
import { useState, type CSSProperties, type ReactNode } from 'react';
import type { ActionEvidence } from '@/lib/data/models';
import { LineageChips } from './LineageChips';

function hasText(s: string | null | undefined): s is string {
  return typeof s === 'string' && s.trim().length > 0;
}
function pct(n: number): number {
  return Math.round(Math.max(0, Math.min(100, n * 100)));
}
/** Aggregate is either a 0..1 fraction or a raw score; render honestly as a score. */
function fmtAggregate(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(2);
}

const CHIP: CSSProperties = {
  fontSize: 11,
  fontWeight: 600,
  color: '#0B6F68',
  background: '#EAF4F2',
  border: '1px solid #C9E5E1',
  borderRadius: 6,
  padding: '3px 9px',
  display: 'inline-flex',
  alignItems: 'center',
  gap: 5,
};
const SMALL_CHIP: CSSProperties = {
  fontFamily: "'IBM Plex Mono', monospace",
  fontSize: 10.5,
  color: '#4B4640',
  background: '#F2EFE9',
  border: '1px solid #E0DCD3',
  borderRadius: 5,
  padding: '2px 7px',
};
const CARD: CSSProperties = {
  border: '1px solid var(--hairline)',
  borderRadius: 10,
  background: 'var(--surface)',
  padding: '10px 12px',
  display: 'grid',
  gap: 8,
};

function Label({ children }: { children: ReactNode }) {
  return (
    <span className="label" style={{ fontSize: 9.5 }}>
      {children}
    </span>
  );
}

/** A labeled section wrapper used by each evidence category. */
function Category({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: 'grid', gap: 7 }}>
      <Label>{label}</Label>
      {children}
    </div>
  );
}

/** A labeled chip row for one brand-voice lexicon/list — rendered only when present. */
function LexRow({ label, items }: { label: string; items: string[] }) {
  if (!items || items.length === 0) return null;
  return (
    <div style={{ display: 'grid', gap: 4 }}>
      <Label>{label}</Label>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {items.map((it, i) => (
          <span key={`${label}-${i}`} style={SMALL_CHIP}>
            {it}
          </span>
        ))}
      </div>
    </div>
  );
}

function BrandVoice({
  voice,
}: {
  voice: NonNullable<ActionEvidence['brandVoice']>;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Category label="Brand voice">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        style={{ ...CHIP, cursor: 'pointer', font: 'inherit' }}
      >
        <span aria-hidden>{open ? '▼' : '▶'}</span>
        brand-dna · {voice.tenantId}
      </button>
      {open ? (
        <div
          style={{
            display: 'grid',
            gap: 10,
            paddingLeft: 12,
            borderLeft: '2px solid var(--hairline-light)',
          }}
        >
          <LexRow label="Tone" items={voice.tone} />
          <LexRow label="Structure" items={voice.structure} />
          <LexRow label="Preferred lexicon" items={voice.prefer} />
          <LexRow label="Banned lexicon" items={voice.ban} />
          <LexRow label="Approved claims" items={voice.approvedClaims} />
          {hasText(voice.source) ? (
            <div style={{ display: 'grid', gap: 4 }}>
              <Label>Source</Label>
              <span className="mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                {voice.source}
              </span>
            </div>
          ) : null}
        </div>
      ) : null}
    </Category>
  );
}

function CustomerCard({
  customer,
}: {
  customer: NonNullable<ActionEvidence['customer']>;
}) {
  const rows: Array<[string, string]> = [];
  if (hasText(customer.name)) rows.push(['Name', customer.name]);
  if (hasText(customer.city)) rows.push(['City', customer.city]);
  if (hasText(customer.note)) rows.push(['Note', customer.note]);
  if (hasText(customer.interest)) rows.push(['Interest', customer.interest]);
  if (hasText(customer.lifecycle)) rows.push(['Lifecycle', customer.lifecycle]);
  if (hasText(customer.lastTattooStyle)) rows.push(['Last style', customer.lastTattooStyle]);
  const facts = customer.factsUsed ?? [];
  return (
    <Category label="Customer · CSV">
      <div style={CARD}>
        {rows.length > 0 ? (
          <div style={{ display: 'grid', gap: 4 }}>
            {rows.map(([k, v]) => (
              <div key={k} style={{ display: 'flex', gap: 8, fontSize: 12.5 }}>
                <span style={{ color: 'var(--text-muted)', minWidth: 70 }}>{k}</span>
                <span style={{ color: 'var(--text-secondary)' }}>{v}</span>
              </div>
            ))}
          </div>
        ) : null}
        {customer.winBackCandidate ? <span style={CHIP}>win-back candidate</span> : null}
        {facts.length > 0 ? (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {facts.map((f, i) => (
              <span key={`fact-${i}`} style={SMALL_CHIP}>
                {f}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </Category>
  );
}

export function EvidenceProvenance({ evidence }: { evidence: ActionEvidence | null }) {
  // Brand-voice expand/collapse lives in its own subcomponent so the hook order
  // here stays stable regardless of which categories are present.

  // Resolve each category to a real-or-absent value (real-only honesty).
  const voice = evidence?.brandVoice && evidence.brandVoice.used ? evidence.brandVoice : null;
  const customer = evidence?.customer ?? null;
  const memories = evidence?.leadMemories ?? [];
  const notes = hasText(evidence?.internalNotes) ? evidence!.internalNotes! : null;
  const brandDocs = evidence?.brandDocuments ?? [];
  const sources = evidence?.researchSources ?? [];
  const tools = evidence?.toolCalls ?? [];
  const critic = evidence?.criticReview ?? null;
  const jury = evidence?.jury ?? null;
  const createdBy =
    evidence?.createdBy && (hasText(evidence.createdBy.role) || hasText(evidence.createdBy.model))
      ? evidence.createdBy
      : null;
  const hasConfidence = evidence != null && evidence.confidence != null;
  const reasoningUrl = hasText(evidence?.reasoningUrl) ? evidence!.reasoningUrl! : null;

  const hasAny =
    !!voice ||
    !!customer ||
    memories.length > 0 ||
    !!notes ||
    brandDocs.length > 0 ||
    sources.length > 0 ||
    tools.length > 0 ||
    !!critic ||
    !!jury ||
    !!createdBy ||
    hasConfidence ||
    !!reasoningUrl;

  // Honest-empty: nothing real to show → one muted line, never raw JSON.
  if (!evidence || !hasAny) {
    return (
      <div style={{ fontSize: 12.5, color: 'var(--text-muted)', lineHeight: 1.5 }}>
        No evidence captured for this draft yet.
      </div>
    );
  }

  return (
    <div
      style={{
        border: '1px solid var(--reasoning-border)',
        borderRadius: 'var(--radius-card)',
        background: 'var(--reasoning-bg)',
        padding: 'var(--pad-card)',
        display: 'grid',
        gap: 14,
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span className="label" style={{ color: 'var(--reasoning-text)' }}>
          EVIDENCE — what this draft actually used
        </span>
        {evidence.isRealOnly ? (
          <span
            className="mono"
            style={{
              marginLeft: 'auto',
              fontSize: 11,
              fontWeight: 700,
              color: '#0B6F68',
              background: '#EAF4F2',
              border: '1px solid #C9E5E1',
              padding: '2px 9px',
              borderRadius: 5,
            }}
          >
            real-only
          </span>
        ) : null}
      </div>

      {voice ? <BrandVoice voice={voice} /> : null}

      {customer ? <CustomerCard customer={customer} /> : null}

      {memories.length > 0 ? (
        <Category label="Lead memory">
          <div style={{ display: 'grid', gap: 5 }}>
            {memories.map((m, i) => (
              <div key={`mem-${i}`} style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                <span style={{ fontSize: 12.5, color: 'var(--text-secondary)', flex: 1 }}>
                  {m.text}
                  {hasText(m.kind) ? (
                    <span className="mono" style={{ fontSize: 10.5, color: 'var(--text-muted)' }}>
                      {' '}
                      · {m.kind}
                    </span>
                  ) : null}
                </span>
                {hasText(m.createdAt) ? (
                  <span className="mono" style={{ fontSize: 10.5, color: 'var(--text-muted)' }}>
                    {m.createdAt}
                  </span>
                ) : null}
              </div>
            ))}
          </div>
        </Category>
      ) : null}

      {notes ? (
        <Category label="Internal notes">
          <div style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>
            {notes}
          </div>
        </Category>
      ) : null}

      {brandDocs.length > 0 ? (
        <Category label="Brand documents">
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {brandDocs.map((doc, i) => (
              <span key={`doc-${i}`} style={CHIP} title={doc.documentId ?? undefined}>
                {doc.document}
                {hasText(doc.heading) ? (
                  <span style={{ fontWeight: 400, color: 'var(--text-muted)' }}>
                    {' '}
                    › {doc.heading}
                  </span>
                ) : null}
              </span>
            ))}
          </div>
        </Category>
      ) : null}

      {sources.length > 0 ? (
        <Category label="Research sources">
          <div style={{ display: 'grid', gap: 8 }}>
            {sources.map((s, i) => (
              <div key={`src-${i}`} style={{ display: 'grid', gap: 3 }}>
                <a
                  href={s.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={s.url}
                  style={{ ...CHIP, justifySelf: 'start', textDecoration: 'none', maxWidth: '100%' }}
                >
                  <span
                    style={{
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      maxWidth: 360,
                    }}
                  >
                    {hasText(s.title) ? s.title : s.url}
                  </span>
                  <span aria-hidden>↗</span>
                </a>
                {hasText(s.snippet) ? (
                  <span style={{ fontSize: 11.5, lineHeight: 1.45, color: 'var(--text-muted)' }}>
                    {s.snippet}
                  </span>
                ) : null}
                {hasText(s.query) ? (
                  <span className="mono" style={{ fontSize: 10.5, color: 'var(--text-muted)' }}>
                    query: {s.query}
                  </span>
                ) : null}
              </div>
            ))}
          </div>
        </Category>
      ) : null}

      {tools.length > 0 ? (
        <Category label="Tool calls">
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {tools.map((t, i) => (
              <span key={`tool-${i}`} style={CHIP}>
                {t.name}
                {hasText(t.detail) ? (
                  <span style={{ fontWeight: 400, color: 'var(--text-muted)' }}>· {t.detail}</span>
                ) : null}
              </span>
            ))}
          </div>
        </Category>
      ) : null}

      {critic ? (
        <Category label="Critic review">
          <div style={CARD}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              {hasText(critic.verdict) ? <span style={CHIP}>{critic.verdict}</span> : null}
              {hasText(critic.model) ? (
                <span className="mono" style={{ fontSize: 10.5, color: 'var(--text-muted)' }}>
                  {critic.model}
                </span>
              ) : null}
            </div>
            {hasText(critic.rationale) ? (
              <span style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--text-secondary)' }}>
                {critic.rationale}
              </span>
            ) : null}
          </div>
        </Category>
      ) : null}

      {jury ? (
        <Category label="Jury">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={CHIP}>
              {hasText(jury.decision) ? jury.decision : 'jury'}
              {jury.aggregate != null ? (
                <span className="mono" style={{ fontWeight: 700 }}>· {fmtAggregate(jury.aggregate)}</span>
              ) : null}
            </span>
            {hasText(jury.note) ? (
              <span style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>{jury.note}</span>
            ) : null}
          </div>
        </Category>
      ) : null}

      {createdBy ? (
        <Category label="Created by">
          <div style={{ display: 'grid', gap: 7 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              {hasText(createdBy.role) ? <span style={CHIP}>{createdBy.role}</span> : null}
              {hasText(createdBy.model) ? (
                <span className="mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  {createdBy.model}
                </span>
              ) : null}
            </div>
            {hasText(createdBy.reasoningSummary) ? (
              <span style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--text-secondary)' }}>
                {createdBy.reasoningSummary}
              </span>
            ) : null}
            {reasoningUrl ? (
              <a
                href={reasoningUrl}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  justifySelf: 'start',
                  fontSize: 12.5,
                  fontWeight: 600,
                  color: 'var(--accent-dark)',
                  background: '#fff',
                  border: '1px solid var(--reasoning-border)',
                  borderRadius: 'var(--radius-button)',
                  padding: '7px 12px',
                  textDecoration: 'none',
                }}
              >
                View full reasoning →
              </a>
            ) : null}
          </div>
        </Category>
      ) : null}

      {hasConfidence ? (
        <Category label="Confidence">
          <div style={{ display: 'grid', gap: 4 }}>
            <span style={{ fontSize: 13, color: 'var(--reasoning-text)' }}>
              {pct(evidence.confidence as number)}%
              {evidence.threshold != null ? (
                <span style={{ color: 'var(--text-muted)' }}> vs {pct(evidence.threshold)}% threshold</span>
              ) : null}
            </span>
            {hasText(evidence.confidenceReason) ? (
              <span style={{ fontSize: 12, lineHeight: 1.5, color: 'var(--text-secondary)' }}>
                {evidence.confidenceReason}
              </span>
            ) : null}
          </div>
        </Category>
      ) : null}

      {/* Footer: the linked identity row (campaign / run / this draft). */}
      <LineageChips
        lineage={{
          campaignId: evidence.campaignId,
          runId: evidence.runId,
          actionId: evidence.actionId,
        }}
      />
    </div>
  );
}
