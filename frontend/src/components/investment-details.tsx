import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { assets } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useIsMobile } from '@/hooks/use-mobile'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import type { Asset, InvestmentDetails } from '@/types'

/** Month-only source dates must stay month-only, without inventing a day. */
function formatInvestmentDate(value: string, locale: string): string {
  const isMonth = /^\d{4}-\d{2}$/.test(value)
  const date = new Date(`${isMonth ? `${value}-01` : value.slice(0, 10)}T12:00:00`)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString(locale, isMonth
    ? { month: 'long', year: 'numeric' }
    : { day: 'numeric', month: 'short', year: 'numeric' })
}

function sourceIsStale(source: InvestmentDetails['source']): boolean {
  if (!source.lastSuccessAt) return true
  const lastSuccess = Date.parse(source.lastSuccessAt)
  return !Number.isFinite(lastSuccess) || Date.now() - lastSuccess > source.staleAfterHours * 3_600_000
}

export function InvestmentBadges({ details }: { details: InvestmentDetails }) {
  const { t } = useTranslation()
  const needsAttention = details.source.status !== 'ok' || sourceIsStale(details.source)
  return (
    <span className="flex flex-wrap gap-1 mt-1">
      <Badge variant="outline" className="text-[10px]">{t(`investments.liquidity.${details.liquidity.status}`, t('investments.liquidity.unknown'))}</Badge>
      {needsAttention && <Badge variant="outline" className="text-[10px] text-amber-700 dark:text-amber-400">{t('investments.refreshNeeded')}</Badge>}
    </span>
  )
}

export function InvestmentProductDetails({ asset }: { asset: Pick<Asset, 'id' | 'currency' | 'investment_details'> }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const details = asset.investment_details
  if (!details) return null
  const { source, liquidity, forecast, coverage } = details
  const stale = sourceIsStale(source)
  const formatDate = (value: string) => formatInvestmentDate(value, dateLocale)
  return (
    <section className="border-t border-border px-5 py-5 space-y-4" aria-label={t('investments.productDetails')}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{t(`investments.productKinds.${details.product_kind}`, t('investments.productDetails'))}</h3>
        <InvestmentBadges details={details} />
      </div>
      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
        <div>
          <dt className="text-xs text-muted-foreground">{t('investments.valuationDate')}</dt>
          <dd>{details.valuation_date ? formatDate(details.valuation_date) : t('investments.valuationDateUnknown')}</dd>
          {details.observed_at && <dd className="text-xs text-muted-foreground">{t('investments.observedOn', { date: formatDate(details.observed_at) })}</dd>}
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">{t('investments.lastCollection')}</dt>
          <dd>{source.lastSuccessAt ? new Date(source.lastSuccessAt).toLocaleString(dateLocale) : t('investments.noSuccessfulCollection')}</dd>
          <dd className="text-xs text-muted-foreground">{t(`investments.sourceStatuses.${source.status}`, t('investments.sourceStatuses.unknown'))}</dd>
          {stale && <dd className="text-xs text-amber-700 dark:text-amber-400">{t('investments.staleBalance')}</dd>}
          {!source.inventoryComplete && <dd className="text-xs text-amber-700 dark:text-amber-400">{t('investments.incompleteInventory')}</dd>}
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">{t('investments.withdrawalAvailability')}</dt>
          <dd>{t(`investments.liquidity.${liquidity.status}`, t('investments.liquidity.unknown'))}</dd>
          {liquidity.availableFrom && <dd className="text-xs">{t('investments.availableFrom', { date: formatDate(liquidity.availableFrom) })}</dd>}
          {liquidity.availableAmount != null && <dd className="tabular-nums">{t('investments.availableAmount', { amount: mask(formatCurrency(Number(liquidity.availableAmount), asset.currency, locale)) })}</dd>}
          <dd className="text-xs text-muted-foreground">{t('investments.netWorthNotCash')}</dd>
        </div>
        {forecast && (
          <div>
            <dt className="text-xs text-muted-foreground">{t('investments.monthlyPensionForecast')}</dt>
            <dd className="font-semibold tabular-nums">{mask(formatCurrency(Number(forecast.monthlyPension), forecast.currency, locale))}</dd>
            {forecast.asOf && <dd className="text-xs">{t('investments.forecastAsOf', { date: formatDate(forecast.asOf) })}</dd>}
            <dd className="text-xs text-muted-foreground">{t('investments.forecastInformational')}</dd>
          </div>
        )}
      </dl>
      <div className="text-xs text-muted-foreground space-y-1">
        <p>{t('investments.performanceUnavailable')}</p>
        <p>{t('investments.historyCoverage', {
          valuations: t(`investments.coverage.${coverage.valuations}`, t('investments.coverage.unknown')),
          activities: t(`investments.coverage.${coverage.activities}`, t('investments.coverage.unknown')),
        })}</p>
      </div>
      <div>
        <h4 className="text-xs font-semibold mb-2">{t('investments.investmentTracks')}</h4>
        {details.tracks.length === 0 ? (
          <p className="text-xs text-muted-foreground">{t('investments.tracksUnavailable')}</p>
        ) : (
          <ul className="divide-y divide-border rounded-lg border border-border">
            {details.tracks.map(track => (
              <li key={track.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
                <span>{track.name}<span className="block text-xs text-muted-foreground">{track.asOf ? t('investments.forecastAsOf', { date: formatDate(track.asOf) }) : t('investments.observedOn', { date: formatDate(track.observedAt) })}</span></span>
                <span className="tabular-nums flex gap-3">
                  {track.allocationPercent != null && <span>{mask(`${Number(track.allocationPercent).toLocaleString(locale)}%`)}</span>}
                  {track.amount != null && <span>{mask(formatCurrency(Number(track.amount), track.currency, locale))}</span>}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <InvestmentActivities assetId={asset.id} />
    </section>
  )
}

export function InvestmentActivities({ assetId, allowedAssetIds }: { assetId?: string; allowedAssetIds?: string[] }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const isMobile = useIsMobile()
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['asset-activities', assetId],
    queryFn: () => assets.activities(assetId),
  })
  const rows = (data ?? []).filter(row => !allowedAssetIds || allowedAssetIds.includes(row.asset_id))
  return (
    <section className="space-y-3" aria-label={t('investments.activities')}>
      <h3 className="text-sm font-semibold">{t('investments.activities')}</h3>
      <p className="text-xs text-muted-foreground">{t('investments.activitiesHint')}</p>
      {isLoading ? <Skeleton className="h-20 w-full" /> : isError ? (
        <div role="alert" className="text-sm text-destructive">
          <p>{t('investments.activitiesError')}</p>
          <Button variant="outline" size="sm" onClick={() => refetch()}>{t('investments.retry')}</Button>
        </div>
      ) : rows.length === 0 ? <p className="text-sm text-muted-foreground">{t('investments.noActivities')}</p> : isMobile ? (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {rows.map(row => (
            <li key={row.id} className="space-y-1 p-3 text-sm">
              <div className="flex items-start justify-between gap-3">
                <span className="font-medium">{t(`investments.activityKinds.${row.kind}`, t('investments.activityKinds.other'))}</span>
                <span className="tabular-nums whitespace-nowrap">{mask(formatCurrency(row.amount, row.currency, locale))}</span>
              </div>
              {!assetId && <p>{row.asset_name}</p>}
              <p className="text-xs text-muted-foreground">{formatInvestmentDate(row.date, dateLocale)}</p>
              {row.date.length === 7 && <p className="text-xs text-muted-foreground">{t('investments.monthOnly')}</p>}
              {row.description && <p className="text-xs">{row.description}</p>}
            </li>
          ))}
        </ul>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm text-left">
            <thead className="bg-muted/30 text-xs text-muted-foreground">
              <tr>
                <th scope="col" className="px-3 py-2">{t('investments.activityDate')}</th>
                {!assetId && <th scope="col" className="px-3 py-2">{t('investments.product')}</th>}
                <th scope="col" className="px-3 py-2">{t('investments.activityKind')}</th>
                <th scope="col" className="px-3 py-2">{t('investments.description')}</th>
                <th scope="col" className="px-3 py-2 text-right">{t('investments.amount')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map(row => (
                <tr key={row.id}>
                  <td className="px-3 py-2 whitespace-nowrap">
                    {formatInvestmentDate(row.date, dateLocale)}
                    <span className="block text-[11px] text-muted-foreground">{row.date.length === 7 ? t('investments.monthOnly') : t(`investments.dateKinds.${row.date_kind}`, '')}</span>
                  </td>
                  {!assetId && <td className="px-3 py-2">{row.asset_name}</td>}
                  <td className="px-3 py-2">{t(`investments.activityKinds.${row.kind}`, t('investments.activityKinds.other'))}</td>
                  <td className="px-3 py-2">{row.description || '—'}</td>
                  <td className="px-3 py-2 text-right tabular-nums whitespace-nowrap">{mask(formatCurrency(row.amount, row.currency, locale))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
