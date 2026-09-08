import type { InvestmentAccount } from '@/types'

export function filterInvestmentAccounts(accounts: InvestmentAccount[], walletIds: string[] | null) {
  return walletIds === null ? accounts : accounts.filter(account => account.group_id !== null && walletIds.includes(account.group_id))
}

export function investmentAccountTotal(accounts: InvestmentAccount[], primaryCurrency: string) {
  const amounts = accounts.map(account => account.balance_primary ?? (account.currency === primaryCurrency ? account.balance : null))
  const known = amounts.filter((amount): amount is number => amount !== null && Number.isFinite(amount))
  return { amount: known.length ? known.reduce((sum, amount) => sum + amount, 0) : null, missing: amounts.length - known.length }
}

export function investmentSourceState(accounts: InvestmentAccount[], now = Date.now()) {
  const sources = accounts.map(account => account.details.source)
  if (sources.some(source => source.status === 'auth_required')) return 'signInRequired'
  if (sources.some(source => source.status === 'error')) return 'unavailable'
  if (sources.some(source => source.status === 'never_synced' || !source.lastSuccessAt)) return 'neverSynced'
  if (sources.some(source => source.status === 'partial' || !source.inventoryComplete)) return 'partial'
  if (sources.some(source => !Number.isFinite(Date.parse(source.lastSuccessAt!)) || now - Date.parse(source.lastSuccessAt!) > source.staleAfterHours * 3_600_000)) return 'stale'
  return 'current'
}

/** A contribution month is a month, not an invented first-of-month date. */
export function formatInvestmentDate(value: string, locale: string): string {
  const isMonth = /^\d{4}-\d{2}$/.test(value)
  const date = new Date(`${isMonth ? `${value}-01` : value.slice(0, 10)}T12:00:00`)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString(locale, isMonth
    ? { month: 'long', year: 'numeric' }
    : { day: 'numeric', month: 'short', year: 'numeric' })
}
