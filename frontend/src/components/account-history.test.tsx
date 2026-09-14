import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { AccountHistory } from './account-history'
import { accounts, investmentAccounts } from '@/lib/api'
import { renderWithProviders } from '@/test/utils'
import type { AccountHistoryCoverage } from '@/types'
vi.mock('@/lib/api', () => ({ accounts: { historyCoverage: vi.fn() }, investmentAccounts: { historyCoverage: vi.fn() } }))
vi.mock('@/hooks/use-display-locale', () => ({ useDateLocale: () => 'en-US' }))
const coverage: AccountHistoryCoverage = { account_id: 'one', account_kind: 'bank', balance_as_of: null, opening_balance_date: '2022-12-31', note_codes: [], streams: [{ kind: 'transactions', count: 753, first_date: '2023-01-04', last_date: '2026-09-01', availability: 'partial', contains_archive: true, monthly_counts: [{ month: '2023-01', count: 17 }, { month: '2026-09', count: 8 }] }] }
beforeEach(() => { vi.clearAllMocks(); vi.mocked(accounts.historyCoverage).mockResolvedValue(coverage); vi.mocked(investmentAccounts.historyCoverage).mockResolvedValue({ ...coverage, account_kind: 'investment' }) })
describe('stored account history', () => {
  it('shows the recorded range and recovered-source provenance without implying completeness', async () => {
    const { user } = renderWithProviders(<AccountHistory accountId="one" kind="bank" />)
    await screen.findByText('753 records')
    expect(screen.getByText('Jan 4, 2023 – Sep 1, 2026')).toBeInTheDocument()
    expect(screen.getByText('Partial history')).toBeInTheDocument()
    expect(screen.getByText('Includes recovered archive records')).toBeInTheDocument()
    expect(screen.getByText(/do not prove that every transaction/)).toBeInTheDocument()
    await user.click(screen.getByText('View records by month'))
    expect(screen.getByText('January 2023')).toBeVisible()
    expect(screen.getByText('17')).toBeVisible()
  })
  it('opens all bank records with an explicit range including the opening balance', async () => {
    const open = vi.fn()
    const { user } = renderWithProviders(<AccountHistory accountId="one" kind="bank" onShowTransactions={open} />)
    await user.click(await screen.findByRole('button', { name: 'All saved history' }))
    expect(open).toHaveBeenCalledWith('2022-12-31', '2026-09-01')
  })
  it('distinguishes an unsupported history stream from an empty supported stream', async () => {
    vi.mocked(investmentAccounts.historyCoverage).mockResolvedValue({ ...coverage, streams: [
      { kind: 'activities', count: 0, first_date: null, last_date: null, availability: 'unavailable', contains_archive: false, monthly_counts: [] },
      { kind: 'valuations', count: 0, first_date: null, last_date: null, availability: 'empty', contains_archive: false, monthly_counts: [] },
    ] })
    renderWithProviders(<AccountHistory accountId="one" kind="investment" />)
    expect(await screen.findByText('Not supplied by this connection')).toBeInTheDocument()
    expect(screen.getByText('No records saved yet')).toBeInTheDocument()
    expect(accounts.historyCoverage).not.toHaveBeenCalled()
  })
})
