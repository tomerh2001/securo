import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Archive, CalendarDays, Info } from 'lucide-react'
import { accounts, investmentAccounts } from '@/lib/api'
import { formatInvestmentDate } from '@/lib/investment-account-utils'
import { useDateLocale } from '@/hooks/use-display-locale'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import type { AccountHistoryCoverage } from '@/types'

export function AccountHistory({ accountId, kind, compact = false, onOpen, onShowTransactions }: {
  accountId: string; kind: 'bank' | 'investment'; compact?: boolean; onOpen?: () => void
  onShowTransactions?: (from: string, to: string) => void
}) {
  const { t } = useTranslation()
  const locale = useDateLocale()
  const query = useQuery({
    queryKey: ['account-history-coverage', kind, accountId],
    queryFn: () => kind === 'bank' ? accounts.historyCoverage(accountId) : investmentAccounts.historyCoverage(accountId),
  })
  if (query.isLoading) return <Skeleton className={compact ? 'h-24 rounded-xl' : 'h-56 rounded-xl'} />
  if (query.isError || !query.data) return <div role="alert" className="rounded-xl border border-border bg-card p-4 flex flex-wrap items-center justify-between gap-3 text-sm">
    <p>{t('accountWorkspace.historyError')}</p><Button size="sm" variant="outline" onClick={() => query.refetch()}>{t('investments.retry')}</Button>
  </div>
  const data = query.data
  const transactionStream = data.streams.find(stream => stream.kind === 'transactions')
  const date = (value: string | null) => value ? formatInvestmentDate(value, locale) : t('accountWorkspace.unknownDate')
  const range = (stream: AccountHistoryCoverage['streams'][number]) => stream.first_date
    ? stream.first_date === stream.last_date ? date(stream.first_date) : `${date(stream.first_date)} – ${date(stream.last_date)}`
    : stream.count > 0 ? t('accountWorkspace.unknownDate') : t(`accountWorkspace.availability.${stream.availability}`)
  return <section className="rounded-xl border border-border bg-card overflow-hidden" aria-label={t('accountWorkspace.historyCoverage')}>
    <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4 border-b border-border">
      <h2 className="flex items-center gap-2 text-sm font-semibold"><CalendarDays size={16} className="text-muted-foreground" />{t('accountWorkspace.historyCoverage')}</h2>
      {compact && onOpen && <Button variant="ghost" size="sm" onClick={onOpen}>{t('accountWorkspace.inspectHistory')}</Button>}
    </div>
    <div className="grid divide-y divide-border sm:grid-cols-2 sm:divide-y-0">
      {data.streams.map(stream => <div key={stream.kind} className="px-5 py-4 space-y-1.5">
        <div className="flex items-center justify-between gap-3"><h3 className="text-sm font-medium">{t(`accountWorkspace.streams.${stream.kind}`)}</h3>
          <span className="text-xs text-muted-foreground">{t('accountWorkspace.recordCount', { count: stream.count })}</span></div>
        <p className="text-sm tabular-nums">{range(stream)}</p>
        {stream.count > 0 && <p className="text-xs text-muted-foreground">{t(`accountWorkspace.availability.${stream.availability}`)}</p>}
        {stream.contains_archive && <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><Archive size={13} />{t('accountWorkspace.includesArchive')}</p>}
        {!compact && stream.monthly_counts.length > 0 && <details className="pt-2 text-xs">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">{t('accountWorkspace.recordsByMonth')}</summary>
          <dl className="mt-2 max-h-64 overflow-y-auto divide-y divide-border">{[...stream.monthly_counts].reverse().map(month => <div key={month.month} className="flex justify-between gap-4 py-2"><dt>{date(month.month)}</dt><dd className="tabular-nums">{month.count}</dd></div>)}</dl>
        </details>}
      </div>)}
    </div>
    <div className="border-t border-border px-5 py-4 space-y-3 text-xs leading-relaxed text-muted-foreground">
      <p className="flex items-start gap-2"><Info size={14} className="mt-0.5 shrink-0" />{t('accountWorkspace.evidenceOnly')}</p>
      {!compact && <>
        <dl className="flex flex-wrap gap-x-8 gap-y-2">
          <div><dt>{t('accountWorkspace.balanceDate')}</dt><dd className="mt-1 text-foreground">{date(data.balance_as_of)}</dd></div>
          {data.opening_balance_date && <div><dt>{t('accountWorkspace.openingBalance')}</dt><dd className="mt-1 text-foreground">{date(data.opening_balance_date)}</dd></div>}
        </dl>
        {data.note_codes.map(code => <p key={code}>{t(`accountWorkspace.notes.${code}`, { defaultValue: t('accountWorkspace.noteUnknown') })}</p>)}
        {onShowTransactions && transactionStream?.first_date && transactionStream.last_date && <Button variant="outline" onClick={() => onShowTransactions(
          [transactionStream.first_date!, data.opening_balance_date].filter((value): value is string => !!value).sort()[0], transactionStream.last_date!,
        )}>{t('accountWorkspace.allHistory')}</Button>}
      </>}
    </div>
  </section>
}
