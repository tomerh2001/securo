import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import AccountsPage from '@/pages/accounts'
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
  connections: { list: vi.fn(), getProviders: vi.fn(), updateSettings: vi.fn() },
  currencies: { list: vi.fn() },
}))
vi.mock('@/lib/api', () => api)
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: { preferences: { currency_display: 'ILS' } } }) }))
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => ({
  canWrite: state.canWrite, hasModule: (name: string) => name !== 'assets' || state.assetsEnabled,
}) }))
vi.mock('@/contexts/collection-filter-context', () => ({ useCollectionFilter: () => ({
  activeAccountIds: state.activeAccountIds, activeWalletIds: state.activeWalletIds,
  activeCollection: state.activeWalletIds === null ? null : { name: 'Retirement' },
}) }))
vi.mock('@/components/bank-connect-dialog', () => ({ BankConnectDialog: () => null }))
vi.mock('@/components/connector-select-dialog', () => ({ ConnectorSelectDialog: () => null }))
vi.mock('@/components/oauth-connect-dialog', () => ({ OAuthConnectDialog: () => null }))
vi.mock('@/components/token-connect-dialog', () => ({ TokenConnectDialog: () => null }))

function threeAccounts() {
  return [
    investmentAccountFixture(),
    investmentAccountFixture({ id: 'training-a', name: 'Training fund A', product_kind: 'keren_hishtalmut', group_id: 'training-wallet', masked_number: '1111', balance: 2000, balance_primary: 2000 }),
    investmentAccountFixture({ id: 'training-b', name: 'Training fund B', product_kind: 'keren_hishtalmut', group_id: 'training-wallet', masked_number: '2222', balance: 3000, balance_primary: 3000 }),
  ]
}

beforeEach(() => {
  vi.clearAllMocks()
  state.canWrite = true
  state.assetsEnabled = true
  state.activeAccountIds = null
  state.activeWalletIds = null
  api.accounts.list.mockResolvedValue([bankAccountFixture()])
  api.investmentAccounts.list.mockResolvedValue(threeAccounts())
  api.connections.list.mockResolvedValue([
    connectionFixture(), connectionFixture({ id: 'bank-connection', institution_name: 'Example Bank', provider: 'simplefin' }),
  ])
  api.connections.getProviders.mockResolvedValue([
    { name: 'investment_feed', display_name: 'Investment collector', supports_asset_sync: true, flow_type: 'token' },
  ])
  api.connections.updateSettings.mockResolvedValue(connectionFixture())
  api.currencies.list.mockResolvedValue([{ code: 'ILS', symbol: '₪', name: 'Israeli shekel' }])
})

describe('investment accounts in Accounts', () => {
  it('shows three product accounts once beneath their provider and links directly to each account', async () => {
    renderWithProviders(<AccountsPage />, { route: '/accounts' })
    expect(await findInvestmentAccountLink('pension-account')).toHaveAttribute('href', '/accounts/investments/pension-account')
    expect(investmentAccountLink('training-a')).toHaveAttribute('href', '/accounts/investments/training-a')
    expect(investmentAccountLink('training-b')).toHaveAttribute('href', '/accounts/investments/training-b')
    expect(screen.getByRole('link', { name: /Clal/ })).toHaveAttribute('href', '/connections/clal-connection')
    expect(screen.getByRole('link', { name: /Everyday bank/ })).toHaveAttribute('href', '/accounts/bank-account')
    expect(screen.getAllByRole('link').filter(link => link.getAttribute('href')?.startsWith('/accounts/investments/'))).toHaveLength(3)
    expect(screen.queryByText('Clal Pension')).not.toBeInTheDocument()
    expect(screen.queryByText('Clal Keren Hishtalmut')).not.toBeInTheDocument()
  })

  it('does not turn missing balances into zero, while retaining a real zero balance', async () => {
    api.investmentAccounts.list.mockResolvedValue([
      investmentAccountFixture({ name: 'Unavailable pension', balance: null, balance_primary: null }),
      investmentAccountFixture({ id: 'zero', name: 'Empty savings', balance: 0, balance_primary: 0 }),
    ])
    renderWithProviders(<AccountsPage />, { route: '/accounts' })
    const unknown = await findInvestmentAccountLink('pension-account')
    expect(within(unknown).getByText(t('investmentAccounts.valueUnavailable', { defaultValue: 'Not available' }))).toBeInTheDocument()
    expect(within(unknown).queryByText(/0\.00/)).not.toBeInTheDocument()
    expect(within(investmentAccountLink('zero')).getByText(/0\.00/)).toBeInTheDocument()
  })

  it('masks product identifiers and balances in privacy mode', async () => {
    localStorage.setItem('privacyMode', 'true')
    renderWithProviders(<AccountsPage />, { route: '/accounts' })
    const pension = await findInvestmentAccountLink('pension-account')
    expect(within(pension).queryByText(/5678|10,000/)).not.toBeInTheDocument()
    expect(within(pension).getAllByText(/•••••/)).toHaveLength(2)
  })

  it('honors collection wallet and bank membership in provider account rows', async () => {
    state.activeAccountIds = []
    state.activeWalletIds = ['pension-wallet']
    renderWithProviders(<AccountsPage />, { route: '/accounts' })
    expect(await findInvestmentAccountLink('pension-account')).toBeInTheDocument()
    expect(queryInvestmentAccountLink('training-a')).not.toBeInTheDocument()
    expect(queryInvestmentAccountLink('training-b')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Everyday bank/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Example Bank/ })).not.toBeInTheDocument()
  })

  it('shows a collection empty state rather than unrelated provider rows for an empty selection', async () => {
    state.activeAccountIds = []
    state.activeWalletIds = []
    renderWithProviders(<AccountsPage />, { route: '/accounts' })
    expect(await screen.findByText(t('investmentAccounts.noAccountsInView', { defaultValue: 'No accounts in this view.' }))).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Clal|Example Bank/ })).not.toBeInTheDocument()
    expect(queryInvestmentAccountLink('pension-account')).not.toBeInTheDocument()
  })

  it('does not show cached investment rows when the Assets module is hidden', async () => {
    state.assetsEnabled = false
    const queryClient = createTestQueryClient()
    queryClient.setQueryData(['investment-accounts'], threeAccounts())
    renderWithProviders(<AccountsPage />, { route: '/accounts', queryClient })
    expect(await screen.findByRole('link', { name: /Everyday bank/ })).toBeInTheDocument()
    expect(api.investmentAccounts.list).not.toHaveBeenCalled()
    expect(queryInvestmentAccountLink('pension-account')).not.toBeInTheDocument()
  })

  it('offers only applicable settings and never saves bank transaction controls for an investment-only provider', async () => {
    api.accounts.list.mockResolvedValue([])
    api.connections.list.mockResolvedValue([connectionFixture()])
    const { user } = renderWithProviders(<AccountsPage />, { route: '/accounts' })
    await findInvestmentAccountLink('pension-account')
    await user.click(screen.getByRole('button', { name: t('connections.settings') }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(screen.queryByText(t('connections.payeeSource'))).not.toBeInTheDocument()
    expect(screen.queryByLabelText(t('connections.importPending'))).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('common.save') }))
    await waitFor(() => expect(api.connections.updateSettings).toHaveBeenCalledTimes(1))
    const settings = api.connections.updateSettings.mock.calls[0][1]
    expect(settings).not.toHaveProperty('payee_source')
    expect(settings).not.toHaveProperty('import_pending')
  })

  it('retains bank settings for mixed connections even when the collection only shows investments', async () => {
    api.accounts.list.mockResolvedValue([bankAccountFixture({ connection_id: 'clal-connection' })])
    api.connections.list.mockResolvedValue([connectionFixture()])
    state.activeAccountIds = []
    state.activeWalletIds = ['pension-wallet']
    const { user } = renderWithProviders(<AccountsPage />, { route: '/accounts' })
    await findInvestmentAccountLink('pension-account')
    expect(screen.queryByRole('link', { name: /Everyday bank/ })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('connections.settings') }))
    expect(await screen.findByText(t('connections.payeeSource'))).toBeInTheDocument()
    expect(screen.getByLabelText(t('connections.importPending'))).toBeInTheDocument()
  })

  it('shows an investment load failure without hiding bank accounts or claiming an empty source', async () => {
    api.investmentAccounts.list.mockRejectedValue(new Error('offline'))
    renderWithProviders(<AccountsPage />, { route: '/accounts' })
    expect(await screen.findByRole('link', { name: /Everyday bank/ })).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(t('investmentAccounts.loadError', { defaultValue: 'Investment accounts could not be loaded.' }))
    expect(screen.queryByText(t('accounts.noAccountsFound'))).not.toBeInTheDocument()
  })

  it('keeps disconnected investment accounts discoverable without connection controls', async () => {
    api.accounts.list.mockResolvedValue([])
    api.connections.list.mockResolvedValue([])
    api.investmentAccounts.list.mockResolvedValue([
      investmentAccountFixture({ connection_id: null, institution_name: null }),
    ])
    renderWithProviders(<AccountsPage />, { route: '/accounts' })
    expect(await findInvestmentAccountLink('pension-account')).toHaveAttribute('href', '/accounts/investments/pension-account')
    const standalone = screen.getByRole('region', { name: t('investmentAccounts.title') })
    expect(within(standalone).getAllByRole('link')).toHaveLength(1)
    expect(screen.queryByRole('button', { name: t('connections.settings') })).not.toBeInTheDocument()
    expect(screen.queryByText(t('accounts.noBankConnections'))).not.toBeInTheDocument()
  })

  it('applies collection membership to disconnected investment accounts', async () => {
    api.accounts.list.mockResolvedValue([])
    api.connections.list.mockResolvedValue([])
    api.investmentAccounts.list.mockResolvedValue([
      investmentAccountFixture({ connection_id: null }),
      investmentAccountFixture({ id: 'other-detached', connection_id: null, group_id: 'other-wallet' }),
    ])
    state.activeAccountIds = []
    state.activeWalletIds = ['pension-wallet']
    const view = renderWithProviders(<AccountsPage />, { route: '/accounts' })
    expect(await findInvestmentAccountLink('pension-account')).toBeInTheDocument()
    expect(queryInvestmentAccountLink('other-detached')).not.toBeInTheDocument()
    state.activeWalletIds = []
    view.rerender(<AccountsPage />)
    expect(queryInvestmentAccountLink('pension-account')).not.toBeInTheDocument()
    expect(screen.getByText(t('investmentAccounts.noAccountsInView'))).toBeInTheDocument()
  })
})
