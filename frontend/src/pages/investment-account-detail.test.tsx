import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import InvestmentAccountDetailPage from '@/pages/investment-account-detail'
import { assets, investmentAccounts } from '@/lib/api'
import { renderWithProviders } from '@/test/utils'
import type { AssetActivity, InvestmentAccount } from '@/types'

const modules = vi.hoisted(() => ({ accounts: true }))
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => ({ hasModule: (module: string) => module !== 'accounts' || modules.accounts }) }))

vi.mock('@/lib/api', () => ({ assets: { values: vi.fn() }, investmentAccounts: { get: vi.fn(), activities: vi.fn() } }))
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: { preferences: { currency_display: 'ILS' } } }) }))
vi.mock('@/hooks/use-mobile', () => ({ useIsMobile: () => false }))

const account: InvestmentAccount = {
  id: 'pension-one', name: 'Long original provider plan description', currency: 'ILS', balance: 12345.67, balance_primary: 12345.67,
  product_kind: 'pension', masked_number: '4321', connection_id: 'connection-one', group_id: 'wallet-one', institution_name: 'Example provider',
  institution_logo_url: null, provider: 'example', is_archived: false,
  details: {
    product_kind: 'pension', liquidity: { status: 'restricted', availableFrom: null, availableAmount: null },
    coverage: { valuations: 'partial', activities: 'partial', tracks: 'unavailable' }, forecast: null, tracks: [],
    report_summaries: [{ id: 'report-one', title: 'Annual summary', fromDate: '2026-01-01', toDate: '2026-08-31', lines: [{ label: 'Fees for this period', amount: '-45.00' }] }],
    source: { provider: 'example', status: 'ok', lastAttemptAt: new Date().toISOString(), lastSuccessAt: new Date().toISOString(), staleAfterHours: 192, inventoryComplete: true },
    valuation_date: '2026-08-31', observed_at: '2026-09-08T10:00:00Z',
  },
}
const rows: AssetActivity[] = Array.from({ length: 6 }, (_, index) => ({
  id: `activity-${index}`, asset_id: account.id, asset_name: account.name, kind: 'employee_contribution', date: '2026-08', date_kind: 'contribution_month',
  amount: 500 + index, currency: 'ILS', description: `Contribution ${index + 1}`, source_id: 'provider-source', observed_at: null,
}))

beforeEach(() => {
  vi.clearAllMocks()
  modules.accounts = true
  vi.mocked(investmentAccounts.get).mockResolvedValue(account)
  vi.mocked(investmentAccounts.activities).mockResolvedValue({ items: rows, total: rows.length, page: 1, limit: 5, available_years: [2026], available_kinds: ['employee_contribution'] })
  vi.mocked(assets.values).mockResolvedValue([{ id: 'value-one', asset_id: account.id, amount: account.balance!, date: '2026-08-31', source: 'sync' }])
})

const options = { route: '/accounts/investments/pension-one', path: '/accounts/investments/:id' }

describe('InvestmentAccountDetailPage', () => {
  it('shows the account identity, one balance and a bounded activity preview', async () => {
    renderWithProviders(<InvestmentAccountDetailPage />, options)
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent('Pension••4321')
    expect(screen.getByRole('link', { name: 'Example provider' })).toHaveAttribute('href', '/connections/connection-one')
    expect(screen.getByRole('link', { name: 'Accounts' })).toHaveAttribute('href', '/accounts')
    expect(screen.getAllByText(/12,345.67/)).toHaveLength(1)
    expect(screen.getByText('As of Aug 31, 2026')).toBeInTheDocument()
    expect(await screen.findByText('Contribution 5')).toBeInTheDocument()
    expect(screen.queryByText('Contribution 6')).not.toBeInTheDocument()
    expect(investmentAccounts.activities).toHaveBeenCalledWith('pension-one', { page: 1, limit: 5 })
    expect(screen.queryByText('Annual summary')).not.toBeInTheDocument()
    expect(screen.getByText(account.name)).not.toBeVisible()
    expect(screen.queryByRole('img', { name: 'Recorded account balances over time' })).not.toBeInTheDocument()
  })

  it('opens full activity and reports in separate tabs without cash transaction tools', async () => {
    const { user } = renderWithProviders(<InvestmentAccountDetailPage />, options)
    await screen.findByText('Contribution 1')
    await user.click(screen.getByRole('button', { name: 'View all activity' }))
    await waitFor(() => expect(investmentAccounts.activities).toHaveBeenLastCalledWith('pension-one', { page: 1, limit: 25 }))
    expect(screen.getByRole('tab', { name: 'Activity' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('combobox', { name: 'Year' })).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Reports' }))
    expect(await screen.findByText('Annual summary')).toBeInTheDocument()
    expect(screen.getByText('Fees for this period')).not.toBeVisible()
    await user.click(screen.getByText('Annual summary'))
    expect(screen.getByText('Fees for this period')).toBeVisible()
    expect(screen.queryByRole('button', { name: /add transaction|transfer|buy|sell/i })).not.toBeInTheDocument()
  })

  it('restores a report tab from its URL', async () => {
    renderWithProviders(<InvestmentAccountDetailPage />, { ...options, route: `${options.route}?tab=reports` })
    expect(await screen.findByText('Annual summary')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Reports' })).toHaveAttribute('aria-selected', 'true')
    expect(investmentAccounts.activities).not.toHaveBeenCalled()
  })

  it('preserves unknown valuation dates and uses a generic connection notice', async () => {
    vi.mocked(investmentAccounts.get).mockResolvedValue({ ...account, details: { ...account.details, valuation_date: null, source: { ...account.details.source, status: 'auth_required', inventoryComplete: false } } })
    renderWithProviders(<InvestmentAccountDetailPage />, options)
    const notice = await screen.findByRole('status')
    expect(notice).toHaveTextContent('Sign-in verification is required to update these accounts. Your saved balances are still available.')
    expect(within(notice).getByRole('link', { name: 'Fix connection' })).toHaveAttribute('href', '/connections/connection-one#connection-health')
    expect(screen.getByText('Balance date unavailable')).toBeInTheDocument()
    expect(screen.queryByText(/As of|Clal|last successful collection/i)).not.toBeInTheDocument()
  })

  it('masks the balance, account number, activity and report amounts', async () => {
    localStorage.setItem('privacyMode', 'true')
    const { user } = renderWithProviders(<InvestmentAccountDetailPage />, options)
    await screen.findByText('Recent activity')
    await waitFor(() => expect(investmentAccounts.activities).toHaveBeenCalled())
    expect(screen.queryByText(/12,345.67|4321|500.00/)).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Pension•••••')
    await user.click(screen.getByRole('tab', { name: 'Reports' }))
    await user.click(await screen.findByText('Annual summary'))
    expect(screen.queryByText(/45.00/)).not.toBeInTheDocument()
    expect(screen.getAllByText('•••••').length).toBeGreaterThan(1)
  })

  it('returns to Assets and hides connection actions when Accounts is disabled', async () => {
    modules.accounts = false
    vi.mocked(investmentAccounts.get).mockResolvedValue({ ...account, details: { ...account.details, source: { ...account.details.source, status: 'auth_required' } } })
    renderWithProviders(<InvestmentAccountDetailPage />, options)
    expect(await screen.findByRole('link', { name: 'Assets' })).toHaveAttribute('href', '/assets')
    expect(screen.getByText('Example provider')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Example provider' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Fix connection' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Accounts' })).not.toBeInTheDocument()
  })

  it('keeps the Assets return path after a load failure when Accounts is disabled', async () => {
    modules.accounts = false
    vi.mocked(investmentAccounts.get).mockRejectedValue(new Error('not found'))
    renderWithProviders(<InvestmentAccountDetailPage />, options)
    await screen.findByRole('alert')
    expect(screen.getByRole('link', { name: 'Assets' })).toHaveAttribute('href', '/assets')
    expect(screen.queryByRole('link', { name: 'Accounts' })).not.toBeInTheDocument()
  })

  it('handles a missing or inaccessible account without showing an empty balance', async () => {
    vi.mocked(investmentAccounts.get).mockRejectedValue(new Error('not found'))
    renderWithProviders(<InvestmentAccountDetailPage />, options)
    expect(await screen.findByRole('alert')).toHaveTextContent('This account could not be loaded.')
    expect(screen.queryByText('Current balance')).not.toBeInTheDocument()
  })
})
