import type { Account, BankConnection, InvestmentAccount } from '@/types'
import { screen, waitFor } from '@testing-library/react'

export function queryInvestmentAccountLink(id: string) {
  return screen.queryAllByRole('link').find(link => link.getAttribute('href') === `/accounts/investments/${id}`) ?? null
}

export function investmentAccountLink(id: string) {
  const link = queryInvestmentAccountLink(id)
  if (!link) throw new Error(`Investment account link ${id} was not rendered`)
  return link
}

export function findInvestmentAccountLink(id: string) {
  return waitFor(() => investmentAccountLink(id))
}

export function investmentAccountFixture(overrides: Partial<InvestmentAccount> = {}): InvestmentAccount {
  return {
    id: 'pension-account', name: 'Pension savings', currency: 'ILS', balance: 10000, balance_primary: 10000,
    product_kind: 'pension', masked_number: '5678', connection_id: 'clal-connection', group_id: 'pension-wallet',
    institution_name: 'Clal', institution_logo_url: null, provider: 'clal', is_archived: false,
    details: {
      product_kind: 'pension', valuation_date: '2026-08-31', observed_at: '2026-09-08T12:00:00Z',
      liquidity: { status: 'restricted', availableFrom: null, availableAmount: null },
      coverage: { valuations: 'partial', activities: 'partial', tracks: 'partial' }, forecast: null, tracks: [],
      source: { provider: 'clal', status: 'ok', lastAttemptAt: new Date().toISOString(), lastSuccessAt: new Date().toISOString(), staleAfterHours: 192, inventoryComplete: true },
      report_summaries: [],
    }, ...overrides,
  }
}

export function connectionFixture(overrides: Partial<BankConnection> = {}): BankConnection {
  return {
    id: 'clal-connection', user_id: 'user', provider: 'investment_feed', institution_name: 'Clal',
    display_name: null, logo_url: null, external_id: 'collector', status: 'active',
    settings: { sync_assets: true }, last_sync_at: new Date().toISOString(),
    created_at: '2026-09-01T12:00:00Z', institutions: [], ...overrides,
  }
}

export function bankAccountFixture(overrides: Partial<Account> = {}): Account {
  return {
    id: 'bank-account', user_id: 'user', connection_id: 'bank-connection', external_id: 'bank-source',
    name: 'Everyday bank', display_name: null, masked_number: '1234', institution_name: 'Example Bank', institution_logo_url: null,
    type: 'checking', balance: 500, current_balance: 500, previous_balance: 400, balance_primary: 500, currency: 'ILS',
    credit_limit: null, available_credit: null, statement_close_day: null, payment_due_day: null,
    next_close_date: null, next_due_date: null, minimum_payment: null, card_brand: null, card_level: null,
    is_closed: false, closed_at: null, ...overrides,
  }
}
