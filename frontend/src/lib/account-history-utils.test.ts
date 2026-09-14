import { describe, expect, it, vi } from 'vitest'
import { loadCompleteAccountTransactions } from './account-history-utils'
import type { Transaction } from '@/types'
const rows = Array.from({ length: 1201 }, (_, index) => ({ id: `transaction-${index}` }) as Transaction)
describe('complete account ledger pagination', () => {
  it('includes history beyond the first 500 records so the ledger balance walk sees every transaction', async () => {
    const fetchPage = vi.fn(async (page: number) => ({ items: rows.slice((page - 1) * 500, page * 500), total: rows.length, page, limit: 500 }))
    const result = await loadCompleteAccountTransactions(fetchPage)
    expect(result.items).toEqual(rows)
    expect(fetchPage.mock.calls).toEqual([[1], [2], [3]])
  })
  it('rejects an inconsistent partial response instead of showing it as complete history', async () => {
    const fetchPage = vi.fn(async (page: number) => ({ items: page === 1 ? rows.slice(0, 500) : [], total: rows.length, page, limit: 500 }))
    await expect(loadCompleteAccountTransactions(fetchPage)).rejects.toThrow('history changed')
  })
})
