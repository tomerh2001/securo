import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { InvestmentExecutionHistory } from '@/components/investment-executions'
import { investmentAccounts } from '@/lib/api'
import { renderWithProviders } from '@/test/utils'
import type { InvestmentExecution } from '@/types'

vi.mock('@/lib/api', () => ({ investmentAccounts: { executions: vi.fn() } }))
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))

const row: InvestmentExecution = {
  id: 'source-execution', productId: 'source-product', sourceId: 'natural-source', sourceIdKind: 'natural_key',
  kind: 'buy', securityId: 'security', isin: 'US1234567890', symbol: 'EXAMPLE', name: 'Example security',
  tradeDate: '2026-08-15', valueDate: null, settlementDate: '2026-08-17', cancelDate: null, cancelled: false,
  quantity: '3.123456789012', unitPrice: '8.123456789012', netCashAmount: '-25.00', currency: 'USD',
  settlementNetCashAmount: '-90.00', settlementCurrency: 'ILS', sourceTradeType: 'Buy order',
  sourceTransactionType: 'Recorded trade', sourcePaymentType: null, observedAt: '2026-09-08T10:00:00Z',
}

beforeEach(() => {
  vi.resetAllMocks()
  localStorage.removeItem('privacyMode')
  vi.mocked(investmentAccounts.executions).mockResolvedValue({
    items: [row], total: 26, page: 1, limit: 25, available_years: [2026, 2025], available_kinds: ['buy', 'dividend'],
  })
})

describe('Source securities activity', () => {
  it('shows original currencies and exact quantity/price without position-editing controls', async () => {
    const { user } = renderWithProviders(<InvestmentExecutionHistory accountId="account-one" />)
    expect(await screen.findByText('Example security')).toBeInTheDocument()
    expect(screen.getByText('USD')).toBeInTheDocument()
    expect(screen.getByText('-$25.00')).toBeInTheDocument()
    await user.click(screen.getByText('View details'))
    expect(screen.getByText('3.123456789012')).toBeVisible()
    expect(screen.getByText('8.123456789012 USD')).toBeVisible()
    expect(screen.getByText(/90.00.*ILS/)).toBeVisible()
    expect(screen.getByText('Buy order · Recorded trade')).toBeVisible()
    expect(screen.queryByRole('button', { name: /buy|sell|edit/i })).not.toBeInTheDocument()
    expect(screen.queryByText('natural-source')).not.toBeInTheDocument()
  })

  it('paginates and filters source history without changing its currency', async () => {
    const { user } = renderWithProviders(<InvestmentExecutionHistory accountId="account-one" />)
    await screen.findByText('Example security')
    await user.click(screen.getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(investmentAccounts.executions).toHaveBeenLastCalledWith('account-one', { page: 2, limit: 25 }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Year' }), '2025')
    await waitFor(() => expect(investmentAccounts.executions).toHaveBeenLastCalledWith('account-one', { page: 1, limit: 25, year: 2025 }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Activity type' }), 'dividend')
    await waitFor(() => expect(investmentAccounts.executions).toHaveBeenLastCalledWith('account-one', { page: 1, limit: 25, year: 2025, kind: 'dividend' }))
  })

  it('keeps cancelled records and distinguishes missing cash values', async () => {
    vi.mocked(investmentAccounts.executions).mockResolvedValue({ items: [{ ...row, cancelled: true, netCashAmount: null }], total: 1, page: 1, limit: 25, available_years: [2026], available_kinds: ['buy'] })
    renderWithProviders(<InvestmentExecutionHistory accountId="account-one" />)
    expect(await screen.findByText('Cancelled')).toBeInTheDocument()
    expect(screen.getByText('—')).toBeInTheDocument()
    expect(screen.queryByText('$0.00')).not.toBeInTheDocument()
  })

  it('masks amounts, security names and quantities in privacy mode', async () => {
    localStorage.setItem('privacyMode', 'true')
    const { user } = renderWithProviders(<InvestmentExecutionHistory accountId="account-one" />)
    await screen.findByText('View details')
    await user.click(screen.getByText('View details'))
    expect(screen.queryByText('Example security')).not.toBeInTheDocument()
    expect(screen.queryByText('3.123456789012')).not.toBeInTheDocument()
    expect(screen.queryByText('-$25.00')).not.toBeInTheDocument()
  })
})
