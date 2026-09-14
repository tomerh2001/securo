import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import AccountDetailPage from './account-detail'
import { accounts, connections, dashboard, transactions, categories, categoryGroups } from '@/lib/api'
import { bankAccountFixture, connectionFixture } from '@/test/investment-account-fixtures'
import { renderWithProviders } from '@/test/utils'
vi.mock('@/lib/api', () => ({ accounts: { get: vi.fn(), list: vi.fn(), bills: vi.fn(), summary: vi.fn(), historyCoverage: vi.fn() }, connections: { list: vi.fn() }, dashboard: { projectedTransactions: vi.fn() }, transactions: { list: vi.fn() }, categories: { list: vi.fn() }, categoryGroups: { list: vi.fn() } }))
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => ({ canWrite: true }) }))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: { preferences: { currency_display: 'ILS' } } }) }))
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))
vi.mock('@/hooks/use-mobile', () => ({ useIsMobile: () => false }))
vi.mock('@/components/transaction-dialog', () => ({ TransactionDialog: () => null }))
vi.mock('@/components/transfer-dialog', () => ({ TransferDialog: ({ open }: { open: boolean }) => open ? <div role="dialog">Transfer form</div> : null }))
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(accounts.get).mockResolvedValue(bankAccountFixture())
  vi.mocked(accounts.list).mockResolvedValue([bankAccountFixture()])
  vi.mocked(connections.list).mockResolvedValue([connectionFixture({ id: 'bank-connection' })])
  vi.mocked(accounts.bills).mockResolvedValue([])
  vi.mocked(accounts.summary).mockResolvedValue({ account_id: 'bank-account', current_balance: 500, opening_balance: 0, monthly_income: 500, monthly_expenses: 0, current_balance_primary: 500, opening_balance_primary: 0, monthly_income_primary: 500, monthly_expenses_primary: 0 })
  vi.mocked(accounts.historyCoverage).mockResolvedValue({ account_id: 'bank-account', account_kind: 'bank', balance_as_of: null, opening_balance_date: '2023-01-01', streams: [{ kind: 'transactions', count: 2, first_date: '2023-02-01', last_date: '2024-03-10', availability: 'available', contains_archive: true, monthly_counts: [] }], note_codes: [] })
  vi.mocked(transactions.list).mockResolvedValue({ items: [], total: 0, page: 1, limit: 500 })
  vi.mocked(dashboard.projectedTransactions).mockResolvedValue([])
  vi.mocked(categories.list).mockResolvedValue([])
  vi.mocked(categoryGroups.list).mockResolvedValue([])
})
const options = { route: '/accounts/bank-account', path: '/accounts/:id' }
describe('shared bank account sections', () => {
  it('keeps account navigation and transfer entry available across the history and connection tabs', async () => {
    const { user } = renderWithProviders(<AccountDetailPage />, options)
    await screen.findByRole('heading', { level: 1, name: 'Everyday bank' })
    expect(screen.getAllByRole('tab').map(item => item.textContent)).toEqual(['Overview', 'Activity', 'History', 'Connection'])
    await user.click(screen.getByRole('tab', { name: 'Connection' }))
    expect(screen.getByRole('link', { name: 'Manage connection' })).toHaveAttribute('href', '/connections/bank-connection#connection-health')
    await user.click(screen.getByRole('button', { name: 'Transfer' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('Transfer form')
  })
  it('shows all card history independently of bill and unbilled filters, even when the last date is a bill date', async () => {
    vi.mocked(accounts.get).mockResolvedValue(bankAccountFixture({ type: 'credit_card', statement_close_day: 5, payment_due_day: 10 }))
    vi.mocked(accounts.bills).mockResolvedValue([{ id: 'bill-one', account_id: 'bank-account', external_id: 'bill', due_date: '2024-03-10', total_amount: 500, currency: 'ILS', minimum_payment: null }])
    const { user } = renderWithProviders(<AccountDetailPage />, options)
    await screen.findByRole('heading', { level: 1 })
    await user.click(screen.getByRole('tab', { name: 'History' }))
    await user.click(await screen.findByRole('button', { name: 'All saved history' }))
    await waitFor(() => expect(transactions.list).toHaveBeenCalledWith(expect.objectContaining({ from: '2023-01-01', to: '2024-03-10', bill_id: undefined, unbilled_only: undefined, page: 1 })))
    expect(screen.getByRole('tab', { name: 'Activity' })).toHaveAttribute('data-state', 'active')
    await user.click(screen.getByRole('tab', { name: 'Overview' }))
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Everyday bank')
  })
  it('labels a next-statement amount without presenting derived available credit or card-debt utilization', async () => {
    vi.mocked(accounts.get).mockResolvedValue(bankAccountFixture({ type: 'credit_card', balance_semantics: 'next_statement_debit', credit_limit: 25000, available_credit: 20770, current_balance: 4230 }))
    renderWithProviders(<AccountDetailPage />, options)
    await screen.findByRole('region', { name: 'Next statement debit' })
    expect(screen.queryByText('Available credit')).not.toBeInTheDocument()
    expect(screen.queryByText('Utilization')).not.toBeInTheDocument()
    expect(screen.getByText('Net charges in period')).toBeInTheDocument()
  })

})
