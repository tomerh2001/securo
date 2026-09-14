import type { PaginatedTransactions } from '@/types'

/** A balance walk needs every matching row, including accounts with >500 rows. */
export async function loadCompleteAccountTransactions(fetchPage: (page: number) => Promise<PaginatedTransactions>): Promise<PaginatedTransactions> {
  const first = await fetchPage(1)
  const items = [...first.items]
  const ids = new Set(items.map(item => item.id))
  for (let page = 2; items.length < first.total; page++) {
    const next = await fetchPage(page)
    const added = next.items.filter(item => !ids.has(item.id))
    if (added.length === 0 || next.total !== first.total) throw new Error('Account history changed during loading. Please reload.')
    for (const item of added) ids.add(item.id)
    items.push(...added)
  }
  return { ...first, items }
}
