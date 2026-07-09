'use client';

/**
 * ArtworkPickerModal — the operator pick for a run paused on `awaiting_selection`
 * (spec section 22). Renders the engine's REAL question and its REAL artwork
 * options (image bytes via /studio/artifacts/{artifactId}/raw, styles/motifs
 * chips, the "why" line) in a 2×2 grid. Clicking an option POSTs select-artwork
 * (via the onSelect callback) and the run resumes server-side — polling continues.
 *
 * HONESTY: only the engine-provided options render; there is no local fallback
 * artwork. "Decide later" merely hides the dialog — the run STAYS paused and the
 * inline paused banner remains visible until a real pick is made.
 */
import { useState } from 'react';
import type { SelectionRequest, SelectionOption } from '@/lib/studio/run-trace';
import { artifactRawUrl } from '@/lib/studio/artists';
import { Chip } from '../console-bits';

const TEAL = '#0F8A82';

export function ArtworkPickerModal({
  request,
  busy = false,
  onSelect,
  onDismiss,
}: {
  request: SelectionRequest;
  /** True while the select-artwork POST is in flight (disables the grid). */
  busy?: boolean;
  onSelect: (assetId: string) => void;
  /** Hide the dialog without resolving — the run stays paused. */
  onDismiss?: () => void;
}) {
  const [picked, setPicked] = useState<string | null>(null);

  const pick = (assetId: string) => {
    if (busy || picked) return;
    setPicked(assetId);
    onSelect(assetId);
  };

  return (
    <div
      role="presentation"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 60,
        background: 'rgba(20, 18, 14, 0.45)',
        display: 'grid',
        placeItems: 'center',
        padding: 20,
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onDismiss?.();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Pick an artwork"
        className="spring-in"
        style={{
          width: 'min(720px, 100%)',
          maxHeight: '86vh',
          overflowY: 'auto',
          background: 'var(--surface, #fff)',
          border: '1px solid var(--hairline)',
          borderRadius: 'var(--radius-card)',
          boxShadow: '0 18px 48px rgba(0,0,0,0.22)',
          padding: 20,
          display: 'grid',
          gap: 14,
        }}
      >
        <header style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          <span
            aria-hidden
            className="active-pulse"
            style={{
              width: 10,
              height: 10,
              borderRadius: '50%',
              background: 'var(--amber-dot, #B58A2A)',
              marginTop: 6,
              flex: '0 0 auto',
              // @ts-expect-error custom prop for the pulse keyframe color
              '--pulse-color': 'rgba(181,138,42,0.5)',
            }}
          />
          <div style={{ minWidth: 0, flex: 1 }}>
            <h2 style={{ margin: 0, fontSize: 16.5, fontWeight: 650, letterSpacing: '-0.01em', color: 'var(--ink)' }}>
              The run is paused — your pick is needed
            </h2>
            <p style={{ margin: '4px 0 0', fontSize: 13, lineHeight: 1.5, color: 'var(--text-secondary)' }}>
              {request.question}
            </p>
          </div>
          {onDismiss && (
            <button
              type="button"
              onClick={onDismiss}
              aria-label="Decide later"
              title="Hide this dialog — the run stays paused until you pick"
              style={{
                font: 'inherit',
                fontSize: 12,
                color: 'var(--text-muted)',
                background: 'transparent',
                border: '1px solid var(--hairline)',
                borderRadius: 'var(--radius-button)',
                padding: '5px 10px',
                cursor: 'pointer',
                flex: '0 0 auto',
              }}
            >
              Decide later
            </button>
          )}
        </header>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
            gap: 12,
          }}
        >
          {request.options.map((opt) => (
            <ArtworkOption
              key={opt.assetId}
              option={opt}
              disabled={busy || (picked !== null && picked !== opt.assetId)}
              selecting={picked === opt.assetId}
              onPick={() => pick(opt.assetId)}
            />
          ))}
        </div>

        <p style={{ margin: 0, fontSize: 11.5, color: 'var(--text-muted)' }}>
          Clicking an option resumes the run with that artwork. Nothing is posted or sent —
          every draft still lands in the Review queue for your approval.
        </p>
      </div>
    </div>
  );
}

function ArtworkOption({
  option,
  disabled,
  selecting,
  onPick,
}: {
  option: SelectionOption;
  disabled: boolean;
  selecting: boolean;
  onPick: () => void;
}) {
  const [imgFailed, setImgFailed] = useState(false);
  return (
    <button
      type="button"
      onClick={onPick}
      disabled={disabled}
      aria-label={`Pick artwork ${option.assetId}`}
      style={{
        font: 'inherit',
        textAlign: 'left',
        padding: 0,
        border: `2px solid ${selecting ? TEAL : 'var(--hairline)'}`,
        borderRadius: 12,
        overflow: 'hidden',
        background: '#fff',
        cursor: disabled ? 'default' : 'pointer',
        opacity: disabled && !selecting ? 0.55 : 1,
        display: 'flex',
        flexDirection: 'column',
      }}
      onMouseEnter={(e) => {
        if (!disabled) (e.currentTarget as HTMLElement).style.borderColor = TEAL;
      }}
      onMouseLeave={(e) => {
        if (!selecting) (e.currentTarget as HTMLElement).style.borderColor = 'var(--hairline)';
      }}
    >
      {imgFailed ? (
        <div
          style={{
            height: 150,
            display: 'grid',
            placeItems: 'center',
            fontSize: 11.5,
            color: 'var(--text-faint)',
            background: 'var(--surface-alt)',
          }}
        >
          image unavailable
        </div>
      ) : (
        // eslint-disable-next-line @next/next/no-img-element -- engine-served bytes; no optimizer configured
        <img
          src={artifactRawUrl(option.artifactId)}
          alt={option.why ?? 'artwork option'}
          loading="lazy"
          onError={() => setImgFailed(true)}
          style={{ width: '100%', height: 150, objectFit: 'cover', display: 'block', background: 'var(--surface-alt)' }}
        />
      )}
      <span style={{ padding: '8px 10px', display: 'grid', gap: 6 }}>
        {(option.styles.length > 0 || option.motifs.length > 0) && (
          <span style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            {option.styles.map((s) => (
              <Chip key={`s_${s}`} tone="teal" style={{ fontSize: 10 }}>
                {s}
              </Chip>
            ))}
            {option.motifs.map((m) => (
              <Chip key={`m_${m}`} tone="neutral" style={{ fontSize: 10 }}>
                {m}
              </Chip>
            ))}
          </span>
        )}
        {option.why && (
          <span style={{ fontSize: 11.5, lineHeight: 1.45, color: 'var(--text-secondary)' }}>{option.why}</span>
        )}
        <span style={{ fontSize: 11.5, fontWeight: 600, color: selecting ? TEAL : 'var(--text-muted)' }}>
          {selecting ? 'Resuming with this artwork…' : 'Use this artwork →'}
        </span>
      </span>
    </button>
  );
}
