import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import AssetsPage from '@/pages/assets'
import { assets, assetGroups, investmentAccounts, currencies } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'
import type { Asset, AssetGroup, InvestmentAccount, InvestmentDetails } from '@/types'

const collection = vi.hoisted(() => ({ activeWalletIds: null as string[] | null }))
const modules = vi.hoisted(() => ({ accounts: true }))
vi.mock('@/contexts/collection-filter-context', () => ({ useCollectionFilter: () => collection }))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: { preferences: { currency_display: 'ILS' } } }) }))
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => ({ canWrite: true, hasModule: (module: string) => module !== 'accounts' || modules.accounts }) }))
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))
vi.mock('@/lib/page-chat-context', () => ({ useRegisterPageChatContext: vi.fn() }))
vi.mock('@/lib/api', () => ({
  assets: { list: vi.fn(), portfolioTrend: vi.fn() }, assetGroups: { list: vi.fn() }, investmentAccounts: { list: vi.fn() }, currencies: { list: vi.fn() },
}))

const details: InvestmentDetails = {
  product_kind: 'pension', liquidity: { status: 'restricted', availableFrom: null, availableAmount: null },
  coverage: { valuations: 'partial', activities: 'partial', tracks: 'unavailable' }, forecast: null, tracks: [], report_summaries: [],
  source: { provider: 'example', status: 'ok', lastAttemptAt: new Date().toISOString(), lastSuccessAt: new Date().toISOString(), staleAfterHours: 192, inventoryComplete: true },
  valuation_date: '2026-08-31', observed_at: '2026-09-08T10:00:00Z',
}
function holding(id: string, amount: number, groupId: string, managed = true): Asset {
  return {
    id, user_id: 'user', name: `Original long ${id} plan name`, type: 'investment', currency: 'ILS', units: null, valuation_method: 'manual',
    purchase_date: null, purchase_price: null, sell_date: null, sell_price: null, growth_type: null, growth_rate: null, growth_frequency: null, growth_start_date: null,
    is_archived: false, position: 0, current_value: amount, current_value_primary: amount, gain_loss: null, gain_loss_primary: null, value_count: 1,
    source: managed ? 'sync' : 'manual', connection_id: managed ? `connection-${groupId}` : null, isin: null, maturity_date: null, group_id: groupId,
    ticker: null, ticker_exchange: null, last_price: null, last_price_at: null, logo_url: null, average_price: null, total_invested: null, realized_gain: null, transaction_count: 0,
    investment_details: managed ? { ...details, product_kind: id === 'savings' ? 'keren_hishtalmut' : 'pension' } : null,
  }
}
const managedAssets = [holding('pension', 100, 'one'), holding('savings', 200, 'one'), holding('other-provider', 600, 'two')]
const wallets: AssetGroup[] = ['one', 'two'].map((id, index) => ({ id, user_id: 'user', name: index ? 'Another provider' : 'Example provider', icon: 'wallet', color: '#0ea5e9', position: index, source: 'sync', connection_id: `connection-${id}`, institution_name: index ? 'Another provider' : 'Example provider', asset_count: index ? 1 : 2, current_value: index ? 600 : 300, current_value_primary: index ? 600 : 300 }))
const projections: InvestmentAccount[] = managedAssets.map((asset, index) => ({ id: asset.id, name: asset.name, currency: asset.currency, balance: asset.current_value, balance_primary: asset.current_value_primary, product_kind: asset.investment_details!.product_kind, masked_number: `${4321 + index}`, connection_id: asset.connection_id, group_id: asset.group_id, institution_name: asset.group_id === 'one' ? 'Example provider' : 'Another provider', institution_logo_url: null, provider: 'example', is_archived: false, details: asset.investment_details! }))

function setPortfolio(rows: Asset[]) {
  vi.mocked(assets.list).mockResolvedValue(rows)
  vi.mocked(assets.portfolioTrend).mockResolvedValue({
    assets: rows.map(({ id, name, type, group_id }) => ({ id, name, type, group_id })),
    trend: [{ date: '2026-08-31', ...Object.fromEntries(rows.map(row => [row.id, row.current_value])), _total: rows.reduce((sum, row) => sum + row.current_value!, 0) }],
    total: rows.reduce((sum, row) => sum + row.current_value!, 0),
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  collection.activeWalletIds = null
  modules.accounts = true
  setPortfolio(managedAssets)
  vi.mocked(assetGroups.list).mockResolvedValue(wallets)
  vi.mocked(investmentAccounts.list).mockResolvedValue(projections)
  vi.mocked(currencies.list).mockResolvedValue([{ code: 'ILS', symbol: '₪', name: 'Israeli new shekel', flag: '' }])
})

describe('investment accounts in the portfolio', () => {
  it('shows institution and account links while keeping the existing total exactly once', async () => {
    renderWithProviders(<AssetsPage />)
    expect(await screen.findByRole('link', { name: 'Example provider' })).toHaveAttribute('href', '/connections/connection-one')
    expect(await screen.findByRole('link', { name: /Pension.*4321/ })).toHaveAttribute('href', '/accounts/investments/pension')
    expect(screen.getByRole('link', { name: /Keren hishtalmut.*4322/ })).toHaveAttribute('href', '/accounts/investments/savings')
    expect(screen.getAllByText(/900.00/)).toHaveLength(1)
    expect(screen.queryByText(t('assets.colQuantity'))).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('assets.tabTransactions') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('assets.newWallet') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('assetImport.action') })).not.toBeInTheDocument()
    expect(screen.queryByText(/Original long/)).not.toBeInTheDocument()
    expect(screen.queryByText(t('investments.activities'))).not.toBeInTheDocument()
  })

  it('uses wallet collection membership for both links and portfolio total', async () => {
    collection.activeWalletIds = ['one']
    renderWithProviders(<AssetsPage />)
    expect(await screen.findByRole('link', { name: 'Example provider' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Another provider' })).not.toBeInTheDocument()
    expect(screen.queryByText(/600.00|900.00/)).not.toBeInTheDocument()
    expect(screen.getByText(/300.00/)).toBeInTheDocument()
    expect(screen.getAllByRole('link').filter(link => link.getAttribute('href')?.startsWith('/accounts/investments/'))).toHaveLength(2)
  })

  it('keeps ordinary holding controls available in a mixed portfolio', async () => {
    setPortfolio([...managedAssets, holding('manual-property', 50, 'one', false)])
    renderWithProviders(<AssetsPage />)
    expect(await screen.findByText('Original long manual-property plan name')).toBeInTheDocument()
    expect(screen.getByText(t('assets.colQuantity'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('assets.tabTransactions') })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('assets.newWallet') })).toBeInTheDocument()
    expect(screen.getAllByText(/950.00/)).toHaveLength(1)
    expect(screen.getByRole('link', { name: /Keren hishtalmut/ })).toBeInTheDocument()
  })

  it('keeps investment details accessible without linking into a disabled Accounts module', async () => {
    modules.accounts = false
    renderWithProviders(<AssetsPage />)
    expect(await screen.findByRole('link', { name: /Pension.*4321/ })).toHaveAttribute('href', '/accounts/investments/pension')
    expect(screen.getByRole('heading', { name: 'Example provider' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Example provider' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'View accounts' })).not.toBeInTheDocument()
  })

  it('masks the aggregate, account balances and identifiers', async () => {
    localStorage.setItem('privacyMode', 'true')
    renderWithProviders(<AssetsPage />)
    await screen.findByRole('link', { name: 'Example provider' })
    await waitFor(() => expect(investmentAccounts.list).toHaveBeenCalled())
    expect(screen.queryByText(/900.00|100.00|200.00|600.00|4321|4322/)).not.toBeInTheDocument()
    expect(screen.getAllByText('•••••').length).toBeGreaterThan(3)
  })
})
