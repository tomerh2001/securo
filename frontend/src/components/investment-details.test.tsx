import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { InvestmentActivityHistory, InvestmentActivityList, InvestmentOverview, InvestmentReports } from '@/components/investment-details'
import { investmentAccounts } from '@/lib/api'
import { renderWithProviders } from '@/test/utils'
import type { AssetActivity, InvestmentDetails } from '@/types'

const mobileState = vi.hoisted(() => ({ value: false }))
vi.mock('@/hooks/use-mobile', () => ({ useIsMobile: () => mobileState.value }))
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))
vi.mock('@/lib/api', () => ({ investmentAccounts: { activities: vi.fn() } }))

const details: InvestmentDetails = {
  product_kind: 'pension',
  liquidity: { status: 'partially_available', availableFrom: '2027-01-01', availableAmount: '0.00' },
  coverage: { valuations: 'partial', activities: 'partial', tracks: 'complete' },
  forecast: { monthlyPension: '1234.00', currency: 'ILS', asOf: '2026-08-31' },
  tracks: [{ id: 'track-one', productId: 'pension-one', name: 'מסלול מניות', allocationPercent: '60.5', amount: '6050.00', currency: 'ILS', asOf: null, observedAt: '2026-09-08T10:00:00Z' }],
  source: { provider: 'another-provider', status: 'ok', lastAttemptAt: '2026-09-08T10:00:00Z', lastSuccessAt: '2026-09-08T10:00:00Z', staleAfterHours: 192, inventoryComplete: true },
  valuation_date: '2026-08-31', observed_at: '2026-09-08T10:00:00Z',
}
const activity: AssetActivity = {
  id: 'activity-one', asset_id: 'pension-one', asset_name: 'Example pension',
  kind: 'employee_contribution', date: '2026-08', date_kind: 'contribution_month',
  amount: 500, currency: 'ILS', description: 'August employee contribution',
  source_id: 'provider-example', observed_at: '2026-09-08T10:00:00Z',
}
const response = { items: [activity], total: 51, page: 1, limit: 25, available_years: [2026, 2025], available_kinds: ['employee_contribution', 'management_fee'] }

beforeEach(() => {
  vi.clearAllMocks()
  mobileState.value = false
  vi.mocked(investmentAccounts.activities).mockResolvedValue(response)
})

describe('investment overview', () => {
  it('shows useful supplied facts once, with no embedded activity or reports', () => {
    renderWithProviders(<InvestmentOverview details={details} currency="ILS" />)
    expect(screen.getAllByText('Partially available')).toHaveLength(1)
    expect(screen.getByText(/0.00 available/)).toBeInTheDocument()
    expect(screen.getByText('Projected monthly pension')).toBeInTheDocument()
    expect(screen.getByText(/Provider estimate/)).toBeInTheDocument()
    expect(screen.getByText('מסלול מניות')).toHaveAttribute('dir', 'auto')
    expect(screen.getByText('60.5%')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(investmentAccounts.activities).not.toHaveBeenCalled()
    expect(screen.queryByText(/Clal|duplicate bank|net worth|performance is unavailable/i)).not.toBeInTheDocument()
  })

  it('omits absent forecasts and allocations while preserving unknown withdrawal availability', () => {
    renderWithProviders(<InvestmentOverview details={{ ...details, forecast: null, tracks: [], liquidity: { status: 'unknown', availableFrom: null, availableAmount: null } }} currency="ILS" />)
    expect(screen.getByText('Withdrawal availability unknown')).toBeInTheDocument()
    expect(screen.queryByText('Projected monthly pension')).not.toBeInTheDocument()
    expect(screen.queryByText('Investment tracks')).not.toBeInTheDocument()
  })

  it('masks balances, forecasts and allocations in privacy mode', () => {
    localStorage.setItem('privacyMode', 'true')
    renderWithProviders(<InvestmentOverview details={details} currency="ILS" />)
    expect(screen.getAllByText(/•••••/)).toHaveLength(4)
    expect(screen.queryByText(/1,234|6,050|60.5%|0.00/)).not.toBeInTheDocument()
  })
})

describe('investment activity', () => {
  it('keeps month precision and signed costs, without repeated date explanations', () => {
    renderWithProviders(<InvestmentActivityList rows={[activity, { ...activity, id: 'fee', kind: 'management_fee', date: '2026-09-02', date_kind: 'booking', amount: -12.5, description: null }]} />)
    expect(screen.getByText('August 2026')).toBeInTheDocument()
    expect(screen.queryByText(/Aug 1, 2026|exact day not supplied/)).not.toBeInTheDocument()
    const feeRow = screen.getByText('Management fee').closest('tr')!
    expect(within(feeRow).getByText(/-.*12.50/)).toBeInTheDocument()
    expect(screen.getAllByRole('columnheader')).toHaveLength(3)
  })

  it('uses readable mobile rows and stable account links when requested', () => {
    mobileState.value = true
    renderWithProviders(<InvestmentActivityList rows={[activity]} showAccount />)
    expect(screen.getByRole('listitem')).toHaveTextContent('August 2026')
    expect(screen.getByRole('link', { name: 'Example pension' })).toHaveAttribute('href', '/accounts/investments/pension-one')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it.each([false, true])('keeps provider descriptions collapsed on mobile=%s until requested', async mobile => {
    mobileState.value = mobile
    const { user } = renderWithProviders(<InvestmentActivityList rows={[activity]} />)
    expect(screen.getByText(activity.description!)).not.toBeVisible()
    expect(screen.getByText('August 2026')).toBeVisible()
    await user.click(screen.getByText('Employee contribution'))
    expect(screen.getByText(activity.description!)).toBeVisible()
  })

  it('hides cleared records and masks amounts', () => {
    localStorage.setItem('privacyMode', 'true')
    renderWithProviders(<InvestmentActivityList rows={[activity, { ...activity, id: 'cleared', amount: 0, description: 'Cleared record' }]} />)
    expect(screen.getAllByText('•••••').some(item => item.tagName === 'TD')).toBe(true)
    expect(screen.queryByText(/500.00|Cleared record/)).not.toBeInTheDocument()
  })

  it('pages server results and resets to page one when year or kind changes', async () => {
    const { user } = renderWithProviders(<InvestmentActivityHistory accountId="pension-one" />)
    await screen.findByText('August 2026')
    await user.click(screen.getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(investmentAccounts.activities).toHaveBeenLastCalledWith('pension-one', { page: 2, limit: 25 }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Year' }), '2025')
    await waitFor(() => expect(investmentAccounts.activities).toHaveBeenLastCalledWith('pension-one', { page: 1, limit: 25, year: 2025 }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Activity type' }), 'management_fee')
    await waitFor(() => expect(investmentAccounts.activities).toHaveBeenLastCalledWith('pension-one', { page: 1, limit: 25, year: 2025, kind: 'management_fee' }))
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled()
  })

  it('does not mistake failures for empty history and supports retry', async () => {
    vi.mocked(investmentAccounts.activities).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(response)
    const { user } = renderWithProviders(<InvestmentActivityHistory accountId="pension-one" />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Investment activity could not be loaded.')
    expect(screen.queryByText('No activity to show.')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('August 2026')).toBeInTheDocument()
  })
})

it('keeps overlapping reports separate and collapsed until selected', async () => {
  const { user } = renderWithProviders(<InvestmentReports currency="ILS" reports={[
    { id: 'year', title: 'Year to date', fromDate: '2026-01-01', toDate: '2026-08-31', lines: [{ label: 'Reported fee', amount: '42.00' }] },
    { id: 'life', title: 'Lifetime', fromDate: null, toDate: '2026-08-31', lines: [{ label: 'Reported return', amount: '-11.00' }] },
  ]} />)
  expect(screen.getByText('Reported fee')).not.toBeVisible()
  expect(screen.getByText('Reported return')).not.toBeVisible()
  await user.click(screen.getByText('Year to date'))
  expect(screen.getByText('Reported fee')).toBeVisible()
  expect(screen.getByText('Reported return')).not.toBeVisible()
  expect(screen.queryByText(/total/i)).not.toBeInTheDocument()
})
