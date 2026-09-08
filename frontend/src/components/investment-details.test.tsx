import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'
import { InvestmentActivities, InvestmentProductDetails } from '@/components/investment-details'
import { assets } from '@/lib/api'
import { renderWithProviders } from '@/test/utils'
import type { AssetActivity, InvestmentDetails } from '@/types'

const mobileState = vi.hoisted(() => ({ value: false }))
vi.mock('@/hooks/use-mobile', () => ({ useIsMobile: () => mobileState.value }))

vi.mock('@/lib/api', () => ({
  assets: { activities: vi.fn() },
  admin: { numberFormat: () => Promise.resolve({ format: 'auto' }), dateFormat: () => Promise.resolve({ format: 'auto' }) },
}))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: null }) }))

function details(overrides: Partial<InvestmentDetails> = {}): InvestmentDetails {
  return {
    product_kind: 'pension',
    liquidity: { status: 'restricted', availableFrom: null, availableAmount: null },
    coverage: { valuations: 'partial', activities: 'unavailable', tracks: 'unavailable' },
    forecast: { monthlyPension: '1234.00', currency: 'ILS', asOf: '2026-08-31' },
    tracks: [],
    source: { provider: 'clal', status: 'ok', lastAttemptAt: new Date().toISOString(), lastSuccessAt: new Date().toISOString(), staleAfterHours: 192, inventoryComplete: true },
    valuation_date: '2026-08-31',
    observed_at: '2026-09-08T10:00:00Z',
    ...overrides,
  }
}

const activity: AssetActivity = {
  id: 'activity-example', asset_id: 'pension-example', asset_name: 'Example pension',
  kind: 'employee_contribution', date: '2026-08', date_kind: 'contribution_month',
  amount: 500, currency: 'ILS', description: 'August employee contribution',
  source_id: 'provider-example', observed_at: '2026-09-08T10:00:00Z',
}

beforeEach(() => {
  vi.resetAllMocks()
  mobileState.value = false
  vi.mocked(assets.activities).mockResolvedValue([])
})

describe('investment product details', () => {
  it('distinguishes actual valuation dates, observed dates, restricted savings and informational forecasts', async () => {
    renderWithProviders(<InvestmentProductDetails asset={{ id: 'pension-example', currency: 'ILS', investment_details: details() }} />)
    expect(screen.getByText('31 Aug 2026')).toBeInTheDocument()
    expect(screen.getByText('Observed on 8 Sept 2026')).toBeInTheDocument()
    expect(screen.getAllByText('Restricted savings')).toHaveLength(2)
    expect(screen.getByText('Projected monthly pension')).toBeInTheDocument()
    expect(screen.getByText('Provider estimate only. Excluded from current net worth, income and cash projections.')).toBeInTheDocument()
    expect(screen.getByText('Investment performance is unavailable without verified contributions and costs.')).toBeInTheDocument()
    expect(await screen.findByText(/No activity records were supplied/)).toBeInTheDocument()
    expect(screen.queryByText('Refresh needed')).not.toBeInTheDocument()
  })

  it('does not substitute collection dates for unknown valuation dates or assume liquidity', () => {
    const value = details({ valuation_date: null, liquidity: { status: 'unknown', availableFrom: null, availableAmount: null }, forecast: null })
    renderWithProviders(<InvestmentProductDetails asset={{ id: 'pension-example', currency: 'ILS', investment_details: value }} />)
    expect(screen.getByText('Valuation date not supplied')).toBeInTheDocument()
    expect(screen.getByText('Observed on 8 Sept 2026')).toBeInTheDocument()
    expect(screen.getAllByText('Withdrawal availability unknown')).toHaveLength(2)
    expect(screen.queryByText('Projected monthly pension')).not.toBeInTheDocument()
  })

  it('keeps authentication failure, stale balances and incomplete inventory visible', () => {
    const value = details()
    value.source = { ...value.source, status: 'auth_required', lastSuccessAt: '2020-01-01T00:00:00Z', inventoryComplete: false }
    renderWithProviders(<InvestmentProductDetails asset={{ id: 'pension-example', currency: 'ILS', investment_details: value }} />)
    expect(screen.getByText('Refresh needed')).toBeInTheDocument()
    expect(screen.getByText('Sign in to Clal again to refresh')).toBeInTheDocument()
    expect(screen.getByText('This balance has not been refreshed recently.')).toBeInTheDocument()
    expect(screen.getByText('Clal has not confirmed that every product was collected.')).toBeInTheDocument()
  })

  it('shows supplied track allocations and preserves zero available amounts', () => {
    const value = details({
      liquidity: { status: 'partially_available', availableFrom: '2027-01-01', availableAmount: '0.00' },
      tracks: [{ id: 'track-example', productId: 'pension-example', name: 'Example equity track', allocationPercent: '60.5', amount: '6050.00', currency: 'ILS', asOf: null, observedAt: '2026-09-08T10:00:00Z' }],
    })
    renderWithProviders(<InvestmentProductDetails asset={{ id: 'pension-example', currency: 'ILS', investment_details: value }} />)
    expect(screen.getByText('Example equity track')).toBeInTheDocument()
    expect(screen.getByText('60.5%')).toBeInTheDocument()
    expect(screen.getByText(/Reported available amount:.*0.00/)).toBeInTheDocument()
  })
})

describe('investment activity', () => {
  it('shows contribution amounts and month precision in readable phone cards', async () => {
    mobileState.value = true
    vi.mocked(assets.activities).mockResolvedValue([activity])
    renderWithProviders(<InvestmentActivities />)
    expect(await screen.findByText('Employee contribution')).toBeInTheDocument()
    const card = screen.getByRole('listitem')
    expect(within(card).getByText(/500.00/)).toBeInTheDocument()
    expect(within(card).getByText('August 2026')).toBeInTheDocument()
    expect(within(card).getByText('Contribution month; exact day not supplied')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('preserves contribution-month precision, contribution types and signed costs', async () => {
    vi.mocked(assets.activities).mockResolvedValue([
      activity,
      { ...activity, id: 'fee-example', kind: 'management_fee', date: '2026-09-02', date_kind: 'booking', amount: -12.5, description: 'September management fee' },
    ])
    renderWithProviders(<InvestmentActivities />)
    expect(await screen.findByText('Employee contribution')).toBeInTheDocument()
    expect(screen.getByText('August 2026')).toBeInTheDocument()
    expect(screen.getByText('Contribution month; exact day not supplied')).toBeInTheDocument()
    expect(screen.queryByText('Aug 1, 2026')).not.toBeInTheDocument()
    const feeRow = screen.getByText('Management fee').closest('tr')!
    expect(within(feeRow).getByText(/-.*12.50/)).toBeInTheDocument()
    expect(within(feeRow).getByText('Booking date')).toBeInTheDocument()
  })

  it('respects the active collection and provides no financial mutation controls', async () => {
    vi.mocked(assets.activities).mockResolvedValue([activity, { ...activity, id: 'other-example', asset_id: 'other-product', asset_name: 'Hidden product' }])
    renderWithProviders(<InvestmentActivities allowedAssetIds={['pension-example']} />)
    expect(await screen.findByText('Example pension')).toBeInTheDocument()
    expect(screen.queryByText('Hidden product')).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('shows failed requests separately from empty history and lets the user retry', async () => {
    vi.mocked(assets.activities).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce([activity])
    const { user } = renderWithProviders(<InvestmentActivities assetId="pension-example" />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Investment activity could not be loaded.')
    expect(screen.queryByText(/No activity records/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Employee contribution')).toBeInTheDocument()
  })

  it('masks financial values in privacy mode', async () => {
    localStorage.setItem('privacyMode', 'true')
    vi.mocked(assets.activities).mockResolvedValue([activity])
    renderWithProviders(<InvestmentActivities />)
    expect(await screen.findByText('•••••')).toBeInTheDocument()
    expect(screen.queryByText(/500.00/)).not.toBeInTheDocument()
  })
})
