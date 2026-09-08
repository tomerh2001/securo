import { describe, expect, it } from 'vitest'
import type { InvestmentAccount } from '@/types'
import { filterInvestmentAccounts, investmentAccountTotal, investmentSourceState } from './investment-account-utils'

function account(overrides: Partial<InvestmentAccount> = {}): InvestmentAccount {
  return {
    id: 'pension', name: 'Pension', currency: 'ILS', balance: 100, balance_primary: 100,
    product_kind: 'pension', masked_number: '1234', connection_id: 'provider', group_id: 'retirement',
    institution_name: 'Example', institution_logo_url: null, provider: 'example', is_archived: false,
    details: {
      product_kind: 'pension', valuation_date: '2026-08-31', observed_at: '2026-09-08T12:00:00Z',
      liquidity: { status: 'restricted', availableFrom: null, availableAmount: null },
      coverage: { valuations: 'partial', activities: 'partial', tracks: 'partial' }, forecast: null, tracks: [],
      source: { provider: 'example', status: 'ok', lastAttemptAt: '2026-09-08T12:00:00Z', lastSuccessAt: '2026-09-08T12:00:00Z', staleAfterHours: 192, inventoryComplete: true },
    }, ...overrides,
  }
}

describe('investment account navigation and totals', () => {
  it('uses wallet membership for collection scoping and respects an empty selection', () => {
    const rows = [account(), account({ id: 'savings', group_id: 'savings' }), account({ id: 'ungrouped', group_id: null })]
    expect(filterInvestmentAccounts(rows, ['retirement']).map(row => row.id)).toEqual(['pension'])
    expect(filterInvestmentAccounts(rows, [])).toEqual([])
    expect(filterInvestmentAccounts(rows, null)).toHaveLength(3)
  })
  it('never treats unknown balances or unconverted foreign currency as zero or primary currency', () => {
    expect(investmentAccountTotal([account({ balance: null, balance_primary: null })], 'ILS')).toEqual({ amount: null, missing: 1 })
    expect(investmentAccountTotal([account({ balance: 0, balance_primary: null }), account({ currency: 'USD', balance: 500, balance_primary: null })], 'ILS')).toEqual({ amount: 0, missing: 1 })
    expect(investmentAccountTotal([account(), account({ balance: 20, balance_primary: 20 })], 'ILS')).toEqual({ amount: 120, missing: 0 })
  })
  it('uses provider collection freshness even when the connection itself synced recently', () => {
    const row = account()
    expect(investmentSourceState([row], Date.parse('2026-09-09T12:00:00Z'))).toBe('current')
    expect(investmentSourceState([row], Date.parse('2026-10-01T12:00:00Z'))).toBe('stale')
    row.details.source.status = 'auth_required'
    expect(investmentSourceState([row], Date.parse('2026-09-09T12:00:00Z'))).toBe('signInRequired')
  })
})
