import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { investmentAccounts } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { formatInvestmentDate } from '@/lib/investment-account-utils'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import type { InvestmentExecution } from '@/types'

function ExecutionRow({ row }: { row: InvestmentExecution }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const text = (key: string) => t(`investmentExecutions.${key}`)
  const kind = t(`investmentExecutions.kinds.${row.kind}`)
  return <li className="p-4 sm:p-5">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 flex-1 basis-40">
        <p className="font-medium break-words" dir="auto">{mask(row.name)}</p>
        <p className="mt-1 text-xs text-muted-foreground">{kind} · {formatInvestmentDate(row.tradeDate, dateLocale)}{row.symbol && <> · {mask(row.symbol)}</>}</p>
        {row.cancelled && <p className="mt-1 text-xs font-medium text-amber-700 dark:text-amber-400">{text('cancelled')}</p>}
      </div>
      <div className="text-right tabular-nums">
        <p className="font-medium">{row.netCashAmount == null ? '—' : mask(formatCurrency(Number(row.netCashAmount), row.currency, locale))}</p>
        <p className="mt-1 text-xs text-muted-foreground">{row.currency}</p>
      </div>
    </div>
    <details className="mt-3">
      <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">{text('details')}</summary>
      <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
        {row.quantity != null && <div><dt className="text-xs text-muted-foreground">{text('quantity')}</dt><dd className="tabular-nums break-all">{mask(row.quantity)}</dd></div>}
        {row.unitPrice != null && <div><dt className="text-xs text-muted-foreground">{text('unitPrice')}</dt><dd className="tabular-nums break-all">{mask(row.unitPrice)} {row.currency}</dd></div>}
        {row.settlementNetCashAmount != null && <div><dt className="text-xs text-muted-foreground">{text('settlementAmount')}</dt><dd className="tabular-nums">{mask(formatCurrency(Number(row.settlementNetCashAmount), row.settlementCurrency, locale))} {row.settlementCurrency}</dd></div>}
        {row.valueDate && <div><dt className="text-xs text-muted-foreground">{text('valueDate')}</dt><dd>{formatInvestmentDate(row.valueDate, dateLocale)}</dd></div>}
        {row.settlementDate && <div><dt className="text-xs text-muted-foreground">{text('settlementDate')}</dt><dd>{formatInvestmentDate(row.settlementDate, dateLocale)}</dd></div>}
        {row.cancelDate && <div><dt className="text-xs text-muted-foreground">{text('cancelDate')}</dt><dd>{formatInvestmentDate(row.cancelDate, dateLocale)}</dd></div>}
        {row.isin && <div><dt className="text-xs text-muted-foreground">{text('isin')}</dt><dd>{mask(row.isin)}</dd></div>}
        <div className="sm:col-span-2"><dt className="text-xs text-muted-foreground">{text('providerType')}</dt><dd className="break-words" dir="auto">{mask([row.sourceTradeType, row.sourceTransactionType, row.sourcePaymentType].filter(Boolean).join(' · '))}</dd></div>
      </dl>
    </details>
  </li>
}

export function InvestmentExecutionHistory({ accountId }: { accountId: string }) {
  const { t } = useTranslation()
  const [page, setPage] = useState(1)
  const [year, setYear] = useState('')
  const [kind, setKind] = useState('')
  const limit = 25
  const text = (key: string) => t(`investmentExecutions.${key}`)
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['investment-account-executions', accountId, page, year, kind],
    queryFn: () => investmentAccounts.executions(accountId, {
      page, limit, ...(year ? { year: Number(year) } : {}), ...(kind ? { kind } : {}),
    }),
  })
  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / limit))
  return <section className="space-y-4" aria-label={text('title')}>
    <p className="text-sm text-muted-foreground">{text('description')}</p>
    <div className="flex flex-wrap gap-3">
      <label className="space-y-1.5 text-xs text-muted-foreground">
        <span className="block">{t('investmentAccounts.year')}</span>
        <select className="h-9 rounded-lg border border-border bg-card px-3 text-sm text-foreground" value={year} onChange={event => { setYear(event.target.value); setPage(1) }}>
          <option value="">{t('investmentAccounts.allYears')}</option>
          {(data?.available_years ?? []).map(value => <option key={value} value={value}>{value}</option>)}
        </select>
      </label>
      <label className="space-y-1.5 text-xs text-muted-foreground">
        <span className="block">{t('investmentAccounts.activityType')}</span>
        <select className="h-9 rounded-lg border border-border bg-card px-3 text-sm text-foreground" value={kind} onChange={event => { setKind(event.target.value); setPage(1) }}>
          <option value="">{t('investmentAccounts.allActivity')}</option>
          {(data?.available_kinds ?? []).map(value => <option key={value} value={value}>{t(`investmentExecutions.kinds.${value}`)}</option>)}
        </select>
      </label>
    </div>
    {isLoading ? <Skeleton className="h-48 rounded-xl" /> : isError ? <div role="alert" className="space-y-3 rounded-xl border border-border p-5">
      <p>{text('loadError')}</p><Button variant="outline" onClick={() => refetch()}>{t('investments.retry')}</Button>
    </div> : <>
      {(data?.items.length ?? 0) > 0
        ? <ul className="divide-y divide-border rounded-xl border border-border bg-card">{data!.items.map(row => <ExecutionRow key={row.id} row={row} />)}</ul>
        : <p className="py-8 text-center text-sm text-muted-foreground">{text('empty')}</p>}
      {(data?.total ?? 0) > 0 && <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
        <p>{t('investmentAccounts.activityCount', { count: data?.total ?? 0 })}</p>
        <div className="flex items-center gap-3">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(value => value - 1)} aria-label={t('investmentAccounts.previousPage')}><ChevronLeft size={16} /></Button>
          <span aria-live="polite">{t('investmentAccounts.pageOf', { page, total: totalPages })}</span>
          <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage(value => value + 1)} aria-label={t('investmentAccounts.nextPage')}><ChevronRight size={16} /></Button>
        </div>
      </div>}
    </>}
  </section>
}
