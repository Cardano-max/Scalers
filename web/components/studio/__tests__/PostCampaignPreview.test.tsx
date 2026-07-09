import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PostCampaignPreview } from '../PostCampaignPreview';
import type { StudioPostDraft } from '../PostCampaignPreview';

/**
 * The post-draft preview is presentation over REAL staged drafts. These tests pin the
 * honesty contract: HELD badges on every card, the caption / CTA / hashtags rendered
 * verbatim, the artwork block grounded in the asset's own metadata + id (with the
 * grounded "why"), and an honest "no artwork on file" message when a piece is absent —
 * never a fabricated picture. Shapes mirror the engine drafter's real output.
 */
const igDraft: StudioPostDraft = {
  platform: 'instagram',
  actionId: 'act_ig1',
  held: true,
  caption:
    'your story, made for you. \u{1F338}\nfine-line peony on the forearm.\nfine-line and floral, made for you.\ntake our time and safe space.',
  hashtags: ['finelinetattoo', 'floraltattoo', 'peonytattoo'],
  callToAction: 'dm to start your design. consults are free.',
  draft: 'full ig text',
  artwork: {
    assetId: 'art_ladies8391_maya_00',
    imageRef: 'seed://ladies8391/maya/fine-line-peony.png',
    caption: 'Fine-line peony on the forearm',
    styles: ['fine-line', 'floral'],
    motifs: ['peony', 'botanical'],
    matchedStyles: ['fine-line', 'floral'],
    matchedMotifs: ['peony'],
    exactMatch: true,
    why:
      'Picked "Fine-line peony on the forearm" because it is tagged fine-line and floral, ' +
      'matching Maya\'s style; its peony motif fits this post. This traces to the piece\'s own ' +
      'portfolio tags (asset art_ladies8391_maya_00).',
  },
};

const fbDraft: StudioPostDraft = {
  platform: 'facebook',
  actionId: 'act_fb1',
  held: true,
  caption:
    'Your story, made for you. This one is fine-line peony on the forearm, in fine-line and floral. Take our time and safe space.',
  hashtags: ['finelinetattoo', 'floraltattoo'],
  callToAction: 'Send me a message to start your design. Consults are always free.',
  draft: 'full fb text',
  artwork: igDraft.artwork,
};

describe('PostCampaignPreview — HELD drafts with grounded artwork', () => {
  it('renders one HELD card per platform with caption, CTA and hashtags', () => {
    render(<PostCampaignPreview artist="Maya" drafts={[igDraft, fbDraft]} />);
    expect(screen.getByText('INSTAGRAM')).toBeInTheDocument();
    expect(screen.getByText('FACEBOOK')).toBeInTheDocument();
    expect(screen.getAllByText('HELD')).toHaveLength(2);
    expect(screen.getByText('2 HELD')).toBeInTheDocument();
    expect(
      screen.getByText('dm to start your design. consults are free.'),
    ).toBeInTheDocument();
    expect(screen.getByText('#peonytattoo')).toBeInTheDocument();
  });

  it('grounds the artwork block in the real asset metadata + id and the "why"', () => {
    render(<PostCampaignPreview artist="Maya" drafts={[igDraft]} />);
    // caption of the chosen piece
    expect(screen.getByText('Fine-line peony on the forearm')).toBeInTheDocument();
    // matched-tag chips + the "style match" label
    expect(screen.getByText('style match')).toBeInTheDocument();
    expect(screen.getByText('peony')).toBeInTheDocument();
    // the grounded why + the traceable asset id/ref (the id appears in BOTH the why
    // and the reference footer — both are grounded).
    expect(screen.getByText(/traces to the piece's own portfolio tags/)).toBeInTheDocument();
    expect(screen.getAllByText(/art_ladies8391_maya_00/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/seed:\/\/ladies8391\/maya\/fine-line-peony\.png/)).toBeInTheDocument();
  });

  it('shows an honest no-artwork message and no fabricated asset when artwork is null', () => {
    const noArt: StudioPostDraft = {
      ...igDraft,
      actionId: 'act_ig_noart',
      hashtags: [],
      artwork: null,
    };
    render(<PostCampaignPreview artist="Noor" drafts={[noArt]} />);
    expect(
      screen.getByText(/No artwork on file for Noor yet/),
    ).toBeInTheDocument();
    expect(screen.queryByText('style match')).not.toBeInTheDocument();
    expect(screen.queryByText(/asset art_/)).not.toBeInTheDocument();
  });

  it('renders an honest empty state with no drafts', () => {
    render(<PostCampaignPreview artist="Rae" drafts={[]} />);
    expect(screen.getByText('No staged posts yet.')).toBeInTheDocument();
    expect(screen.queryByText(/HELD/)).not.toBeInTheDocument();
  });
});
