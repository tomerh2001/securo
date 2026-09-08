import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import ConnectionDetailPage from '@/pages/connection-detail'
import { createTestQueryClient, renderWithProviders, t } from '@/test/utils'
import { bankAccountFixture, connectionFixture, investmentAccountFixture, findInvestmentAccountLink, investmentAccountLink, queryInvestmentAccountLink } from '@/test/investment-account-fixtures'

const state = vi.hoisted(() => ({
  canWrite: true, assetsEnabled: true,
  activeAccountIds: null as string[] | null,
  activeWalletIds: null as string[] | null,
}))
const api = vi.hoisted(() => ({
  accounts: { list: vi.fn() },
  investmentAccounts: { list: vi.fn() },
  connections: { list: vi.fn(), getProviders: vi.fn(), updateSettings: vi.fn(), sync: vi.fn() },
}))
vi.mock('@/lib/api', () => api)
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: { preferences: { currency_display: 'ILS' } } }) }))
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => ({
  canWrite: state.canWrite, hasModule: (name: string) => name !== 'assets' || state.assetsEnabled,
}) }))
vi.mock('@/contexts/collection-filter-context', () => ({ useCollectionFilter: () => ({
  activeAccountIds: state.activeAccountIds, activeWalletIds: state.activeWalletIds,
}) }))

function renderPage(queryClient = createTestQueryClient()) {
  return renderWithProviders(<ConnectionDetailPage />, {
    route: '/connections/clal-connection', path: '/connections/:id', queryClient,
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  state.canWrite = true
  state.assetsEnabled = true
  state.activeAccountIds = null
  state.activeWalletIds = null
  api.accounts.list.mockResolvedValue([
    bankAccountFixture({ connection_id: 'clal-connection' }),
    bankAccountFixture({ id: 'unrelated-bank', name: 'Unrelated bank', connection_id: 'other-connection' }),
  ])
  api.investmentAccounts.list.mockImplementation(async (connectionId: string) => [
    investmentAccountFixture(),
    investmentAccountFixture({ id: 'training', name: 'Training fund', product_kind: 'keren_hishtalmut', group_id: 'training-wallet', balance: 2000, balance_primary: 2000 }),
    investmentAccountFixture({ id: 'unrelated-fund', name: 'Unrelated investment', connection_id: 'other-connection' }),
  ].filter(account => account.connection_id === connectionId))
  api.connections.list.mockResolvedValue([connectionFixture()])
  api.connections.getProviders.mockResolvedValue([
    { name: 'investment_feed', display_name: 'Investment collector', supports_asset_sync: true, flow_type: 'token' },
  ])
  api.connections.updateSettings.mockResolvedValue(connectionFixture())
  api.connections.sync.mockResolvedValue(connectionFixture())
})

describe('connection account destination', () => {
  it('loads the requested provider only and shows each bank and investment account once', async () => {
    renderPage()
    expect(await findInvestmentAccountLink('pension-account')).toHaveAttribute('href', '/accounts/investments/pension-account')
    expect(api.investmentAccounts.list).toHaveBeenCalledWith('clal-connection')
    expect(screen.getByRole('link', { name: /Everyday bank/ })).toHaveAttribute('href', '/accounts/bank-account')
    expect(screen.getAllByRole('link').filter(link => link.getAttribute('href') === '/accounts/investments/pension-account')).toHaveLength(1)
    expect(screen.getAllByRole('link').filter(link => link.getAttribute('href') === '/accounts/investments/training')).toHaveLength(1)
    expect(screen.queryByText('Unrelated bank')).not.toBeInTheDocument()
    expect(queryInvestmentAccountLink('unrelated-fund')).not.toBeInTheDocument()
    const bank = screen.getByRole('link', { name: /Everyday bank/ })
    expect(bank).toHaveTextContent('1234')
    const investments = screen.getByRole('region', { name: t('investmentAccounts.title', { defaultValue: 'Investment accounts' }) })
    expect(within(investments).getByText(/12,000\.00/)).toBeInTheDocument()
    expect(within(investments).queryByText(/12,500\.00/)).not.toBeInTheDocument()
  })

  it('applies collection membership to both account types and the investment total', async () => {
    state.activeAccountIds = []
    state.activeWalletIds = ['training-wallet']
    renderPage()
    expect(await findInvestmentAccountLink('training')).toBeInTheDocument()
    expect(queryInvestmentAccountLink('pension-account')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Everyday bank/ })).not.toBeInTheDocument()
    expect(screen.queryByText(/12,000\.00|10,000\.00/)).not.toBeInTheDocument()
    expect(screen.getAllByText(/2,000\.00/)).toHaveLength(2)
  })

  it('keeps mixed-connection bank settings when its bank accounts are outside the current collection', async () => {
    state.activeAccountIds = []
    state.activeWalletIds = ['pension-wallet']
    const { user } = renderPage()
    await findInvestmentAccountLink('pension-account')
    await user.click(screen.getByRole('button', { name: t('connections.settings') }))
    expect(await screen.findByText(t('connections.payeeSource'))).toBeInTheDocument()
    expect(screen.getByLabelText(t('connections.importPending'))).toBeInTheDocument()
  })

  it('identifies a partial total when some values or currency conversions are unavailable', async () => {
    api.investmentAccounts.list.mockResolvedValue([
      investmentAccountFixture({ balance: 100, balance_primary: 100 }),
      investmentAccountFixture({ id: 'unknown', name: 'Unknown pension', balance: null, balance_primary: null }),
      investmentAccountFixture({ id: 'foreign', name: 'Dollar pension', balance: 500, balance_primary: null, currency: 'USD' }),
    ])
    renderPage()
    await findInvestmentAccountLink('unknown')
    expect(screen.getByText(t('investmentAccounts.incompleteTotal', { defaultValue: 'Some balances are unavailable.' }))).toBeInTheDocument()
    expect(screen.queryByText(/600\.00/)).not.toBeInTheDocument()
    const unknown = investmentAccountLink('unknown')
    expect(within(unknown).getByText(t('investmentAccounts.valueUnavailable', { defaultValue: 'Not available' }))).toBeInTheDocument()
    expect(within(investmentAccountLink('foreign')).getByText(/\$500\.00/)).toBeInTheDocument()
  })

  it('hides all cached investment values when Assets is disabled and keeps its settings applicable', async () => {
    state.assetsEnabled = false
    api.accounts.list.mockResolvedValue([])
    const queryClient = createTestQueryClient()
    queryClient.setQueryData(['investment-accounts', 'connection', 'clal-connection'], [investmentAccountFixture()])
    const { user } = renderPage(queryClient)
    await screen.findByRole('heading', { name: 'Clal' })
    expect(api.investmentAccounts.list).not.toHaveBeenCalled()
    expect(queryInvestmentAccountLink('pension-account')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('connections.settings') }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(screen.queryByText(t('connections.payeeSource'))).not.toBeInTheDocument()
  })

  it('uses provider freshness rather than a recent connection pull and masks totals in privacy mode', async () => {
    localStorage.setItem('privacyMode', 'true')
    const account = investmentAccountFixture()
    account.details.source.status = 'auth_required'
    api.investmentAccounts.list.mockResolvedValue([account])
    renderPage()
    await findInvestmentAccountLink('pension-account')
    expect(screen.getByRole('status')).toHaveTextContent(t('investmentAccounts.signInNeeded'))
    expect(screen.queryByText(/10,000\.00/)).not.toBeInTheDocument()
    expect(within(investmentAccountLink('pension-account')).queryByText(/5678/)).not.toBeInTheDocument()
  })

  it('allows a reader to inspect accounts without showing update or settings actions', async () => {
    state.canWrite = false
    renderPage()
    await findInvestmentAccountLink('pension-account')
    expect(screen.queryByRole('button', { name: t('connections.settings') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('investmentAccounts.refresh', { defaultValue: 'Refresh' }) })).not.toBeInTheDocument()
  })

  it('refreshes only the current connection and keeps bank rows visible when investment loading fails', async () => {
    api.investmentAccounts.list.mockRejectedValue(new Error('offline'))
    const { user } = renderPage()
    expect(await screen.findByRole('link', { name: /Everyday bank/ })).toBeInTheDocument()
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.queryByText(t('investmentAccounts.noAccountsInView', { defaultValue: 'No accounts in this view.' }))).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('investmentAccounts.refresh', { defaultValue: 'Refresh' }) }))
    await waitFor(() => expect(api.connections.sync).toHaveBeenCalledWith('clal-connection'))
  })
})
