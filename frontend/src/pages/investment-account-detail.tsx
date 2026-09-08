import { useQuery } from '@tanstack/react-query'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, ChevronDown, ChevronRight, Info } from 'lucide-react'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { assets, investmentAccounts } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { formatInvestmentDate, investmentSourceState } from '@/lib/investment-account-utils'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  InvestmentActivityHistory,
  InvestmentActivityList,
  InvestmentOverview,
  InvestmentReports,
} from '@/components/investment-details'
import type { InvestmentAccount } from '@/types'

function AccountOverview({ account, onActivity }: { account: InvestmentAccount; onActivity: () => void }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const { data: values, isError: valuesError, refetch: refetchValues } = useQuery({
    queryKey: ['asset-values', account.id],
    queryFn: () => assets.values(account.id),
  })
  const { data: activity, isLoading, isError, refetch } = useQuery({
    queryKey: ['investment-account-activities', account.id, 'recent'],
    queryFn: () => investmentAccounts.activities(account.id, { page: 1, limit: 5 }),
  })
  const points = [...(values ?? [])].sort((a, b) => a.date.localeCompare(b.date))
  const details = account.details
  return (
    <div className="space-y-6">
      <InvestmentOverview details={details} currency={account.currency} />
      {valuesError && <div role="alert" className="rounded-xl border border-border bg-card p-4 space-y-2">
        <p className="text-sm">{t('investmentAccounts.historyError', { defaultValue: 'Balance history could not be loaded.' })}</p>
        <Button variant="outline" size="sm" onClick={() => refetchValues()}>{t('investments.retry')}</Button>
      </div>}
      {points.length > 1 && (
        <section className="rounded-xl border border-border bg-card p-5" aria-label={t('investmentAccounts.balanceHistory', { defaultValue: 'Balance history' })}>
          <h2 className="mb-5 text-sm font-semibold">{t('investmentAccounts.balanceHistory', { defaultValue: 'Balance history' })}</h2>
          <div className="h-52" role="img" aria-label={t('investmentAccounts.balanceHistoryChart', { defaultValue: 'Recorded account balances over time' })}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={points} margin={{ top: 5, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" />
                <XAxis dataKey="date" axisLine={false} tickLine={false} minTickGap={36} tick={{ fontSize: 11 }} tickFormatter={(value: string) => formatInvestmentDate(value, dateLocale)} />
                <YAxis axisLine={false} tickLine={false} width={65} tick={{ fontSize: 11 }} tickFormatter={(value: number) => mask(new Intl.NumberFormat(locale, { notation: 'compact', maximumFractionDigits: 1 }).format(value))} />
                <Tooltip content={({ active, payload }) => {
                  const point = payload?.[0]?.payload as (typeof points)[number] | undefined
                  if (!active || !point) return null
                  return <div className="rounded-lg border border-border bg-card p-3 text-xs shadow-sm">
                    <p className="text-muted-foreground">{formatInvestmentDate(point.date, dateLocale)}</p>
                    <p className="mt-1 font-semibold tabular-nums">{mask(formatCurrency(point.amount, account.currency, locale))}</p>
                    {point.source_as_of_verified === false && <p className="mt-1 text-muted-foreground">{t('investmentAccounts.recordedDate', { defaultValue: 'Date recorded' })}</p>}
                  </div>
                }} />
                <Area type="linear" dataKey="amount" stroke="var(--primary)" fill="var(--primary)" fillOpacity={0.08} strokeWidth={2} dot={{ r: 3 }} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <details className="mt-4">
            <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">{t('investmentAccounts.viewBalances', { defaultValue: 'View recorded balances' })}</summary>
            <dl className="mt-3 divide-y divide-border">
              {[...points].reverse().map(point => <div key={point.id} className="flex items-start justify-between gap-4 py-2 text-sm">
                <dt className="text-muted-foreground">{formatInvestmentDate(point.date, dateLocale)}{point.source_as_of_verified === false && <span className="block text-xs">{t('investmentAccounts.recordedDate', { defaultValue: 'Date recorded' })}</span>}</dt>
                <dd className="font-medium tabular-nums">{mask(formatCurrency(point.amount, account.currency, locale))}</dd>
              </div>)}
            </dl>
          </details>
        </section>
      )}
      <section className="space-y-3" aria-label={t('investmentAccounts.recentActivity', { defaultValue: 'Recent activity' })}>
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold">{t('investmentAccounts.recentActivity', { defaultValue: 'Recent activity' })}</h2>
          {(activity?.total ?? 0) > 0 && <Button variant="ghost" size="sm" onClick={onActivity}>{t('investmentAccounts.viewActivity', { defaultValue: 'View all activity' })}<ChevronRight size={14} /></Button>}
        </div>
        {isLoading ? <Skeleton className="h-36 rounded-xl" /> : isError ? (
          <div role="alert" className="rounded-xl border border-border p-4 space-y-2">
            <p className="text-sm">{t('investments.activitiesError')}</p>
            <Button variant="outline" size="sm" onClick={() => refetch()}>{t('investments.retry')}</Button>
          </div>
        ) : <InvestmentActivityList rows={(activity?.items ?? []).slice(0, 5)} />}
      </section>
      <details className="group rounded-xl border border-border bg-card">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 p-4 text-sm text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden">
          {t('investmentAccounts.dataDetails', { defaultValue: 'About this account data' })}<ChevronDown size={16} className="group-open:rotate-180" />
        </summary>
        <dl className="grid gap-4 border-t border-border p-4 text-sm sm:grid-cols-2">
          <div className="sm:col-span-2">
            <dt className="mb-1 text-xs text-muted-foreground">{t('investmentAccounts.plan', { defaultValue: 'Plan' })}</dt>
            <dd className="break-words" dir="auto">{account.name}</dd>
          </div>
          <div>
            <dt className="mb-1 text-xs text-muted-foreground">{t('investmentAccounts.lastUpdated', { defaultValue: 'Last updated' })}</dt>
            <dd>{details.source.lastSuccessAt ? new Date(details.source.lastSuccessAt).toLocaleString(dateLocale) : t('investmentAccounts.notUpdated', { defaultValue: 'Not updated yet' })}</dd>
          </div>
          <div>
            <dt className="mb-1 text-xs text-muted-foreground">{t('investmentAccounts.historyAvailable', { defaultValue: 'Available history' })}</dt>
            <dd>{t('investmentAccounts.activityCoverage', { defaultValue: 'Activity: {{coverage}}', coverage: t(`investments.coverage.${details.coverage.activities}`, t('investments.coverage.unknown')) })}</dd>
            <dd>{t('investmentAccounts.balanceCoverage', { defaultValue: 'Balances: {{coverage}}', coverage: t(`investments.coverage.${details.coverage.valuations}`, t('investments.coverage.unknown')) })}</dd>
          </div>
        </dl>
      </details>
    </div>
  )
}

export default function InvestmentAccountDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const { hasModule } = useWorkspace()
  const accountsEnabled = hasModule('accounts')
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const { data: account, isLoading, isError, refetch } = useQuery({
    queryKey: ['investment-account', id],
    queryFn: () => investmentAccounts.get(id!),
    enabled: !!id,
  })
  const tab = ['activity', 'reports'].includes(searchParams.get('tab') ?? '') ? searchParams.get('tab')! : 'overview'
  function changeTab(value: string) {
    const next = new URLSearchParams(searchParams)
    if (value === 'overview') next.delete('tab')
    else next.set('tab', value)
    setSearchParams(next)
  }
  if (isLoading) return <div className="space-y-5"><Skeleton className="h-8 w-48" /><Skeleton className="h-36 rounded-xl" /><Skeleton className="h-64 rounded-xl" /></div>
  if (isError || !account) return (
    <div className="space-y-6">
      <Link to={accountsEnabled ? '/accounts' : '/assets'} className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft size={16} />{t(accountsEnabled ? 'accounts.title' : 'assets.title')}</Link>
      <div role="alert" className="rounded-xl border border-border bg-card p-6 space-y-4">
        <p>{t('investmentAccounts.loadError', { defaultValue: 'This account could not be loaded.' })}</p>
        <Button variant="outline" onClick={() => refetch()}>{t('investments.retry')}</Button>
      </div>
    </div>
  )
  const { details } = account
  const sourceState = investmentSourceState([account])
  const needsAttention = sourceState !== 'current'
  const notice = {
    signInRequired: t('connectionHealth.signInNotice', { defaultValue: 'Sign-in verification is required to update these accounts. Your saved balances are still available.' }),
    unavailable: t('investmentAccounts.updateFailed', { defaultValue: 'The latest update failed. Showing the last available balance.' }),
    neverSynced: t('investmentAccounts.waitingForUpdate', { defaultValue: 'Waiting for the first update.' }),
    partial: t('investmentAccounts.partialUpdate', { defaultValue: 'Some account information is missing.' }),
    stale: t('investmentAccounts.staleUpdate', { defaultValue: 'This balance needs an update.' }),
    current: '',
  }[sourceState]
  return (
    <div className="max-w-5xl space-y-6">
      <nav aria-label={t('investmentAccounts.breadcrumb', { defaultValue: 'Breadcrumb' })} className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <Link to={accountsEnabled ? '/accounts' : '/assets'} className="hover:text-foreground hover:underline">{t(accountsEnabled ? 'accounts.title' : 'assets.title')}</Link>
        {account.connection_id && <><ChevronRight size={14} aria-hidden="true" />{accountsEnabled ? <Link to={`/connections/${account.connection_id}`} className="hover:text-foreground hover:underline" dir="auto">{account.institution_name || account.provider}</Link> : <span dir="auto">{account.institution_name || account.provider}</span>}</>}
        <ChevronRight size={14} aria-hidden="true" /><span className="text-foreground" aria-current="page">{t(`investments.productKinds.${account.product_kind}`, t('investments.productKinds.investment'))}</span>
      </nav>
      <header>
        <h1 className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-2xl font-semibold tracking-tight">{t(`investments.productKinds.${account.product_kind}`, t('investments.productKinds.investment'))}{account.masked_number && <span className="text-base font-normal text-muted-foreground tabular-nums">{mask(`••${account.masked_number}`)}</span>}</h1>
      </header>
      {needsAttention && <div role="status" className="flex items-start gap-3 rounded-lg border border-amber-500/25 bg-amber-500/5 p-4 text-sm">
        <Info size={16} className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" />
        <div className="space-y-1"><p>{notice}</p>{accountsEnabled && account.connection_id && <Link className="inline-block text-xs text-muted-foreground hover:text-foreground underline underline-offset-4" to={`/connections/${account.connection_id}#connection-health`}>{t('connectionHealth.fixConnection', { defaultValue: 'Fix connection' })}</Link>}</div>
      </div>}
      <section className="rounded-xl border border-border bg-card p-5 sm:p-6" aria-label={t('investmentAccounts.currentBalance', { defaultValue: 'Current balance' })}>
        <p className="text-sm text-muted-foreground">{t('investmentAccounts.currentBalance', { defaultValue: 'Current balance' })}</p>
        <p className="mt-2 text-3xl sm:text-4xl font-semibold tracking-tight tabular-nums break-words">{account.balance != null ? mask(formatCurrency(account.balance, account.currency, locale)) : '—'}</p>
        {account.balance_primary != null && account.currency !== userCurrency && <p className="mt-1 text-sm text-muted-foreground tabular-nums">{mask(formatCurrency(account.balance_primary, userCurrency, locale))}</p>}
        <p className="mt-3 text-xs text-muted-foreground">{details.valuation_date
          ? t('investments.forecastAsOf', { date: formatInvestmentDate(details.valuation_date, dateLocale) })
          : t('investmentAccounts.balanceDateUnknown', { defaultValue: 'Balance date unavailable' })}</p>
      </section>
      <Tabs value={tab} onValueChange={changeTab} className="gap-5">
        <TabsList variant="line" aria-label={t('investmentAccounts.accountSections', { defaultValue: 'Account sections' })}>
          <TabsTrigger value="overview">{t('investmentAccounts.overview', { defaultValue: 'Overview' })}</TabsTrigger>
          <TabsTrigger value="activity">{t('investmentAccounts.activity', { defaultValue: 'Activity' })}</TabsTrigger>
          <TabsTrigger value="reports">{t('investmentAccounts.reports', { defaultValue: 'Reports' })}</TabsTrigger>
        </TabsList>
        <TabsContent value="overview"><AccountOverview account={account} onActivity={() => changeTab('activity')} /></TabsContent>
        <TabsContent value="activity"><InvestmentActivityHistory key={account.id} accountId={account.id} /></TabsContent>
        <TabsContent value="reports"><InvestmentReports reports={details.report_summaries ?? []} currency={account.currency} /></TabsContent>
      </Tabs>
    </div>
  )
}
