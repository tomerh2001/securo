import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { ChevronDown, ChevronLeft, ChevronRight } from 'lucide-react'
import { investmentAccounts } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { formatInvestmentDate } from '@/lib/investment-account-utils'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useIsMobile } from '@/hooks/use-mobile'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import type { AssetActivity, InvestmentDetails, InvestmentReportSummary } from '@/types'

export function InvestmentBadges({ details }: { details: InvestmentDetails }) {
  const { t } = useTranslation()
  return <Badge variant="outline" className="font-normal">{t(`investments.liquidity.${details.liquidity.status}`, t('investments.liquidity.unknown'))}</Badge>
}

export function InvestmentOverview({ details, currency }: { details: InvestmentDetails; currency: string }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const { liquidity, forecast, tracks } = details
  return (
    <div className="space-y-6">
      <div className={`grid gap-4 ${forecast ? 'md:grid-cols-2' : ''}`}>
        <section className="rounded-xl border border-border bg-card p-5 space-y-3" aria-label={t('investmentAccounts.availability', { defaultValue: 'Withdrawal availability' })}>
          <h2 className="text-sm font-medium text-muted-foreground">{t('investmentAccounts.availability', { defaultValue: 'Withdrawal availability' })}</h2>
          <p className="font-semibold">{t(`investments.liquidity.${liquidity.status}`, t('investments.liquidity.unknown'))}</p>
          {liquidity.availableFrom && <p className="text-sm text-muted-foreground">{t('investments.availableFrom', { date: formatInvestmentDate(liquidity.availableFrom, dateLocale) })}</p>}
          {liquidity.availableAmount != null && (
            <p className="text-sm tabular-nums">{t('investmentAccounts.availableAmount', { defaultValue: '{{amount}} available', amount: mask(formatCurrency(Number(liquidity.availableAmount), currency, locale)) })}</p>
          )}
        </section>
        {forecast && (
          <section className="rounded-xl border border-border bg-card p-5 space-y-3" aria-label={t('investments.monthlyPensionForecast')}>
            <h2 className="text-sm font-medium text-muted-foreground">{t('investments.monthlyPensionForecast')}</h2>
            <p className="text-xl font-semibold tabular-nums">{mask(formatCurrency(Number(forecast.monthlyPension), forecast.currency, locale))}</p>
            <p className="text-xs text-muted-foreground">{t('investmentAccounts.providerEstimate', { defaultValue: 'Provider estimate' })}{forecast.asOf ? ` · ${formatInvestmentDate(forecast.asOf, dateLocale)}` : ''}</p>
          </section>
        )}
      </div>
      {tracks.length > 0 && (
        <section className="rounded-xl border border-border bg-card overflow-hidden" aria-label={t('investments.investmentTracks')}>
          <h2 className="px-5 py-4 text-sm font-semibold border-b border-border">{t('investments.investmentTracks')}</h2>
          <ul className="divide-y divide-border">
            {tracks.map(track => (
              <li key={track.id} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-5 py-4 text-sm">
                <div className="min-w-0 flex-1 basis-44">
                  <p className="font-medium break-words" dir="auto">{track.name}</p>
                  {track.asOf && <p className="mt-1 text-xs text-muted-foreground">{t('investments.forecastAsOf', { date: formatInvestmentDate(track.asOf, dateLocale) })}</p>}
                </div>
                <div className="flex flex-wrap items-center gap-3 tabular-nums">
                  {track.allocationPercent != null && <span className="text-muted-foreground">{mask(`${Number(track.allocationPercent).toLocaleString(locale)}%`)}</span>}
                  {track.amount != null && <span className="font-medium">{mask(formatCurrency(Number(track.amount), track.currency, locale))}</span>}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

export function InvestmentActivityList({ rows, showAccount = false }: { rows: AssetActivity[]; showAccount?: boolean }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const isMobile = useIsMobile()
  const visibleRows = rows.filter(row => row.amount !== 0)
  const kind = (row: AssetActivity) => t(`investments.activityKinds.${row.kind}`, t('investments.activityKinds.other'))
  if (visibleRows.length === 0) return <p className="py-8 text-center text-sm text-muted-foreground">{t('investmentAccounts.noActivity', { defaultValue: 'No activity to show.' })}</p>
  if (isMobile) return (
    <ul className="divide-y divide-border rounded-xl border border-border bg-card">
      {visibleRows.map(row => {
        const summary = <>
          <span className="flex min-w-0 items-center gap-1.5 font-medium">{kind(row)}{row.description && <ChevronDown size={13} className="shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />}</span>
          <span className="shrink-0 text-right font-medium tabular-nums whitespace-nowrap">{mask(formatCurrency(row.amount, row.currency, locale))}</span>
          <span className="col-span-2 text-xs text-muted-foreground">{formatInvestmentDate(row.date, dateLocale)}</span>
        </>
        return <li key={row.id} className="p-4 text-sm">
          {showAccount && <Link className="mb-2 block text-primary hover:underline" to={`/accounts/investments/${row.asset_id}`} dir="auto">{row.asset_name}</Link>}
          {row.description ? <details className="group">
            <summary className="grid cursor-pointer list-none grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 gap-y-1.5 [&::-webkit-details-marker]:hidden" aria-label={`${kind(row)}: ${t('investmentAccounts.activityDetails', { defaultValue: 'Provider description' })}`}>{summary}</summary>
            <p className="mt-3 border-t border-border pt-3 text-xs text-muted-foreground break-words" dir="auto">{mask(row.description)}</p>
          </details> : <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 gap-y-1.5">{summary}</div>}
        </li>
      })}
    </ul>
  )
  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-card">
      <table className="w-full text-sm text-left">
        <thead className="border-b border-border bg-muted/30 text-xs text-muted-foreground">
          <tr>
            <th scope="col" className="px-4 py-3 font-medium">{t('investmentAccounts.date', { defaultValue: 'Date' })}</th>
            {showAccount && <th scope="col" className="px-4 py-3 font-medium">{t('investmentAccounts.account', { defaultValue: 'Account' })}</th>}
            <th scope="col" className="px-4 py-3 font-medium">{t('investmentAccounts.activity', { defaultValue: 'Activity' })}</th>
            <th scope="col" className="px-4 py-3 text-right font-medium">{t('investments.amount')}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {visibleRows.map(row => (
            <tr key={row.id}>
              <td className="px-4 py-3 whitespace-nowrap text-muted-foreground">{formatInvestmentDate(row.date, dateLocale)}</td>
              {showAccount && <td className="px-4 py-3"><Link className="hover:underline" to={`/accounts/investments/${row.asset_id}`} dir="auto">{row.asset_name}</Link></td>}
              <td className="px-4 py-3">
                {row.description ? <details className="group">
                  <summary className="flex cursor-pointer list-none items-center gap-1.5 font-medium [&::-webkit-details-marker]:hidden" aria-label={`${kind(row)}: ${t('investmentAccounts.activityDetails', { defaultValue: 'Provider description' })}`}><span>{kind(row)}</span><ChevronDown size={13} className="shrink-0 text-muted-foreground transition-transform group-open:rotate-180" /></summary>
                  <p className="mt-2 text-xs text-muted-foreground break-words" dir="auto">{mask(row.description)}</p>
                </details> : <p className="font-medium">{kind(row)}</p>}
              </td>
              <td className="px-4 py-3 text-right font-medium tabular-nums whitespace-nowrap">{mask(formatCurrency(row.amount, row.currency, locale))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function InvestmentActivityHistory({ accountId }: { accountId: string }) {
  const { t } = useTranslation()
  const [page, setPage] = useState(1)
  const [kind, setKind] = useState('')
  const [year, setYear] = useState('')
  const limit = 25
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['investment-account-activities', accountId, page, kind, year],
    queryFn: () => investmentAccounts.activities(accountId, { page, limit, ...(kind ? { kind } : {}), ...(year ? { year: Number(year) } : {}) }),
  })
  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / limit))
  return (
    <section className="space-y-4" aria-label={t('investmentAccounts.activity', { defaultValue: 'Activity' })}>
      <div className="flex flex-wrap items-end gap-3">
        <label className="space-y-1.5 text-xs text-muted-foreground">
          <span className="block">{t('investmentAccounts.year', { defaultValue: 'Year' })}</span>
          <select className="h-9 min-w-32 rounded-lg border border-border bg-card px-3 text-sm text-foreground" value={year} onChange={event => { setYear(event.target.value); setPage(1) }}>
            <option value="">{t('investmentAccounts.allYears', { defaultValue: 'All years' })}</option>
            {(data?.available_years ?? (year ? [Number(year)] : [])).map(value => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label className="space-y-1.5 text-xs text-muted-foreground min-w-0">
          <span className="block">{t('investmentAccounts.activityType', { defaultValue: 'Activity type' })}</span>
          <select className="h-9 w-full max-w-full rounded-lg border border-border bg-card px-3 text-sm text-foreground" value={kind} onChange={event => { setKind(event.target.value); setPage(1) }}>
            <option value="">{t('investmentAccounts.allActivity', { defaultValue: 'All activity' })}</option>
            {(data?.available_kinds ?? (kind ? [kind] : [])).map(value => <option key={value} value={value}>{t(`investments.activityKinds.${value}`, t('investments.activityKinds.other'))}</option>)}
          </select>
        </label>
      </div>
      {isLoading ? <Skeleton className="h-48 w-full rounded-xl" /> : isError ? (
        <div role="alert" className="rounded-xl border border-border bg-card p-5 space-y-3">
          <p className="text-sm">{t('investments.activitiesError')}</p>
          <Button variant="outline" size="sm" onClick={() => refetch()}>{t('investments.retry')}</Button>
        </div>
      ) : (
        <>
          <InvestmentActivityList rows={data?.items ?? []} />
          {(data?.total ?? 0) > 0 && (
            <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
              <p>{t('investmentAccounts.activityCount', { defaultValue: '{{count}} records', count: data?.total ?? 0 })}</p>
              <div className="flex items-center gap-3">
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(value => value - 1)} aria-label={t('investmentAccounts.previousPage', { defaultValue: 'Previous page' })}><ChevronLeft size={16} /></Button>
                <span aria-live="polite">{t('investmentAccounts.pageOf', { defaultValue: '{{page}} of {{total}}', page, total: totalPages })}</span>
                <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage(value => value + 1)} aria-label={t('investmentAccounts.nextPage', { defaultValue: 'Next page' })}><ChevronRight size={16} /></Button>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  )
}

export function InvestmentReports({ reports, currency }: { reports: InvestmentReportSummary[]; currency: string }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  if (reports.length === 0) return <p className="py-10 text-center text-sm text-muted-foreground">{t('investmentAccounts.noReports', { defaultValue: 'No reports available yet.' })}</p>
  return (
    <section className="space-y-3" aria-label={t('investmentAccounts.reports', { defaultValue: 'Reports' })}>
      {reports.map(report => (
        <details key={report.id} className="group overflow-hidden rounded-xl border border-border bg-card">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 p-4 hover:bg-muted/30 [&::-webkit-details-marker]:hidden">
            <span className="min-w-0">
              <span className="block text-sm font-medium break-words" dir="auto">{report.title}</span>
              {(report.fromDate || report.toDate) && <span className="mt-1 block text-xs text-muted-foreground">{report.fromDate && report.toDate
                ? `${formatInvestmentDate(report.fromDate, dateLocale)} – ${formatInvestmentDate(report.toDate, dateLocale)}`
                : report.fromDate ? t('investments.reportFrom', { date: formatInvestmentDate(report.fromDate, dateLocale) })
                  : t('investments.reportThrough', { date: formatInvestmentDate(report.toDate!, dateLocale) })}</span>}
            </span>
            <ChevronDown size={16} className="shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
          </summary>
          <dl className="divide-y divide-border border-t border-border">
            {report.lines.map((line, index) => (
              <div key={`${line.label}:${index}`} className="flex items-start justify-between gap-4 px-4 py-3 text-sm">
                <dt className="min-w-0 break-words" dir="auto">{line.label}</dt>
                <dd className="shrink-0 tabular-nums font-medium">{mask(formatCurrency(Number(line.amount), currency, locale))}</dd>
              </div>
            ))}
          </dl>
        </details>
      ))}
    </section>
  )
}
