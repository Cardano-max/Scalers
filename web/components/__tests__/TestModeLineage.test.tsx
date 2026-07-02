/**
 * ju1.5 — TEST MODE badge + review-queue lineage + campaign memory (component tests).
 *
 * Server-driven contract: everything here flows from adapter.getTenantMeta /
 * getActionLineage / getCampaignExamples (the MockAdapter fixtures) — no tenant
 * name is ever hardcoded in a component. Covers: banner on a test-mode tenant,
 * NO banner on the dev fixture (ladies8391 edge case), per-draft chip + disabled
 * Live toggle with reason, lineage fields incl. honest-missing rendering and the
 * SMS "no send path" chip, and the Campaign Memory view.
 */
import { describe, expect, it } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import { DataProvider } from '@/lib/data/DataProvider';
import { ConsoleProvider } from '@/state/console-store';
import { MockAdapter } from '@/lib/data/mock-adapter';
import type { ActionLineage, TenantMeta } from '@/lib/data/models';
import { TestModeBanner } from '../TestModeBanner';
import { MemoryScreen } from '../MemoryScreen';
import { ReviewScreen } from '../ReviewScreen';
import { LineagePanel } from '../trace/LineagePanel';

function mount(ui: React.ReactNode, tenantId: string, adapter = new MockAdapter()) {
  return render(
    <DataProvider adapter={adapter} tenantId={tenantId}>
      <ConsoleProvider>{ui}</ConsoleProvider>
    </DataProvider>,
  );
}

describe('TestModeBanner (server-driven)', () => {
  it('renders the banner when the server reports testMode', async () => {
    mount(<TestModeBanner />, 'skindesign');
    const banner = await screen.findByRole('status', { name: /test mode/i });
    expect(banner).toHaveTextContent(/real customer sends disabled/i);
    expect(banner).toHaveTextContent(/skin design tattoo/i);
  });

  it('renders NOTHING for the ladies8391 dev fixture (unregistered tenant)', async () => {
    const { container } = mount(<TestModeBanner />, 'ladies8391');
    // resolve the async meta read, then assert absence
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('renders nothing for a registered LIVE tenant', async () => {
    const { container } = mount(<TestModeBanner />, 'northwind');
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });
});

describe('ReviewScreen under a test-mode tenant', () => {
  it('badges drafts, disables the Live toggle with the reason, and keeps approve enabled', async () => {
    mount(<ReviewScreen />, 'skindesign');
    // per-draft TEST MODE chips appear once the queue loads
    const chips = await screen.findAllByLabelText('Test mode draft');
    expect(chips.length).toBeGreaterThan(0);
    // the Live side of the send-mode toggle is disabled (server-driven)...
    const group = screen.getByRole('group', { name: /send mode/i });
    const liveBtn = within(group).getByRole('button', { name: /live/i });
    expect(liveBtn).toBeDisabled();
    // ...with a plain-language reason naming the server as the enforcer
    expect(screen.getByRole('note')).toHaveTextContent(/server-enforced/i);
    // approve (stages HELD) keeps working
    expect(screen.getByRole('button', { name: /approve/i })).toBeEnabled();
  });

  it('shows full lineage for a draft with grounded fields', async () => {
    mount(<ReviewScreen />, 'skindesign');
    const panel = await screen.findByRole('region', { name: /draft lineage/i });
    expect(panel).toHaveTextContent('customers.csv');
    expect(panel).toHaveTextContent('Jordan Reyes');
    expect(panel).toHaveTextContent('jordan@example.com');
    expect(panel).toHaveTextContent('+1-702-555-0134');
    expect(panel).toHaveTextContent('Angel');
    expect(panel).toHaveTextContent('Skin Design Tattoo Las Vegas');
    expect(panel).toHaveTextContent('MINIAPP1200');
    expect(panel).toHaveTextContent('reply YES to claim your spot');
    // example provenance is honestly absent until ju1.4
    expect(panel).toHaveTextContent(/per-draft example provenance lands with ju1\.4/i);
    expect(panel).toHaveTextContent(/limited personalization/i);
  });
});

describe('LineagePanel honesty + SMS badge', () => {
  const base: ActionLineage = {
    actionId: 'a1', runId: null, channel: 'sms', sourceFile: null,
    customer: { id: null, name: null, email: null, phone: null },
    artist: null, studio: null, offer: null, cta: null, examples: [],
    limitedPersonalization: null, personalizationNote: null,
  };

  function adapterWith(lineage: ActionLineage | null) {
    const a = new MockAdapter();
    a.getActionLineage = async () => lineage;
    return a;
  }

  it('renders honest "missing" per absent field — never blank', async () => {
    mount(<LineagePanel actionId="a1" />, 'skindesign', adapterWith(base));
    const panel = await screen.findByRole('region', { name: /draft lineage/i });
    // one explicit "missing" per ungrounded field (source/customer/artist/studio/example/offer/cta)
    expect(within(panel).getAllByText(/missing/i).length).toBeGreaterThanOrEqual(7);
    expect(panel).toHaveTextContent(/not recorded on the customer row/i);
    expect(panel).toHaveTextContent(/no grounded customer identity/i);
  });

  it('badges SMS drafts with "no SMS send path yet"', async () => {
    mount(<LineagePanel actionId="a1" />, 'skindesign', adapterWith(base));
    const panel = await screen.findByRole('region', { name: /draft lineage/i });
    expect(panel).toHaveTextContent(/no sms send path yet/i);
  });

  it('states when NO lineage was recorded at all', async () => {
    mount(<LineagePanel actionId="a1" />, 'skindesign', adapterWith(null));
    const panel = await screen.findByRole('region', { name: /draft lineage/i });
    expect(panel).toHaveTextContent(/no lineage recorded for this draft/i);
  });
});

describe('MemoryScreen', () => {
  it('lists the campaign examples with metrics and screenshot states', async () => {
    mount(<MemoryScreen />, 'skindesign');
    const angel = await screen.findByRole('region', {
      name: /campaign example 06\.18 angel mini app/i,
    });
    expect(angel).toHaveTextContent('$1200');
    expect(angel).toHaveTextContent('1466');   // recipients
    expect(angel).toHaveTextContent('1102');   // delivered
    expect(angel).toHaveTextContent(/reply YES/);
    // honest screenshot state (fixture has no local file)
    expect(angel).toHaveTextContent(/screenshot not present locally/i);
    // pattern list with evidence counts
    expect(screen.getByRole('region', { name: /extracted patterns/i }))
      .toHaveTextContent(/reply_keyword_cta/);
  });

  it('is honest-empty for a tenant with no examples', async () => {
    mount(<MemoryScreen />, 'northwind');
    expect(
      await screen.findByText(/no campaign examples ingested/i),
    ).toBeInTheDocument();
  });
});

describe('tenant meta fixtures (mock contract)', () => {
  it('mock adapter reports skindesign as test-mode and ladies8391 as unregistered', async () => {
    const a = new MockAdapter();
    const sd = (await a.getTenantMeta('skindesign')) as TenantMeta;
    expect(sd.testMode).toBe(true);
    const lf = (await a.getTenantMeta('ladies8391')) as TenantMeta;
    expect(lf.registered).toBe(false);
    expect(lf.testMode).toBeNull();
  });
});
