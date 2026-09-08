import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, Info, RefreshCw, Settings } from 'lucide-react'
import { toast } from 'sonner'
import { accounts, connections, investmentAccounts } from '@/lib/api'
import { getAccountName, formatAccountMask } from '@/lib/account-utils'
import { getConnectionName } from '@/lib/connection-utils'
import { filterInvestmentAccounts, investmentAccountTotal, investmentSourceState } from '@/lib/investment-account-utils'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { formatCurrency } from '@/lib/format'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { useCollectionFilter } from '@/contexts/collection-filter-context'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { AccountIcon, ConnectionLogo, getAccountTypeConfig } from '@/components/account-icon'
import { InvestmentAccountRow } from '@/components/investment-account-row'
import { ConnectionSettingsDialog } from '@/components/connection-settings-dialog'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'

export default function ConnectionDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { t } = useTranslation()
  const { user } = useAuth()
  const { canWrite, hasModule } = useWorkspace()
  const { activeAccountIds, activeWalletIds } = useCollectionFilter()
  const { mask } = usePrivacyMode()
  const locale = useDisplayLocale()
  const currency = user?.preferences?.currency_display ?? 'USD'
  const queryClient = useQueryClient()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const connectionQuery = useQuery({ queryKey: ['connections'], queryFn: connections.list })
  const bankQuery = useQuery({ queryKey: ['accounts'], queryFn: () => accounts.list() })
  const investmentQuery = useQuery({
    queryKey: ['investment-accounts', 'connection', id],
    queryFn: () => investmentAccounts.list(id!),
    enabled: !!id && hasModule('assets'),
  })
  const { data: providers } = useQuery({ queryKey: ['connections', 'providers'], queryFn: connections.getProviders, staleTime: 600_000 })
  const connection = connectionQuery.data?.find(item => item.id === id)
  const allBankAccounts = (bankQuery.data ?? []).filter(account => account.connection_id === id && !account.is_closed)
  const bankAccounts = allBankAccounts.filter(account => activeAccountIds === null || activeAccountIds.includes(account.id))
  const allInvestments = hasModule('assets') ? investmentQuery.data ?? [] : []
  const investments = filterInvestmentAccounts(allInvestments, activeWalletIds)
  const total = investmentAccountTotal(investments, currency)
  const sourceState = investmentSourceState(allInvestments)
  const refresh = useMutation({
    mutationFn: () => connections.sync(id!),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['connections'] })
      toast.success(t('investmentAccounts.refreshRequested', { defaultValue: 'Update requested' }))
    },
    onError: () => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['connections'] })
      toast.error(t('common.error'))
    },
  })
  const retry = () => {
    connectionQuery.refetch()
    bankQuery.refetch()
    if (hasModule('assets')) investmentQuery.refetch()
  }

  if (connectionQuery.isLoading) return <Skeleton className="h-64 rounded-xl" />
  if (connectionQuery.isError || !connection) return (
    <div className="space-y-4">
      <Link to="/accounts" className="text-sm text-muted-foreground hover:text-foreground">{t('accounts.title')}</Link>
      <p role="alert">{t('investmentAccounts.connectionLoadError', { defaultValue: 'This connection could not be loaded.' })}</p>
      <Button variant="outline" onClick={retry}>{t('investments.retry')}</Button>
    </div>
  )

  const name = getConnectionName(connection, t)
  const loading = bankQuery.isLoading || (hasModule('assets') && investmentQuery.isLoading)
  const failed = bankQuery.isError || (hasModule('assets') && investmentQuery.isError)
  const count = bankAccounts.length + investments.length
  const stateKey = { signInRequired: 'signInNeeded', unavailable: 'updateFailed', neverSynced: 'waitingForUpdate', partial: 'partialUpdate', stale: 'staleUpdate', current: '' }[sourceState]
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <nav aria-label={t('investmentAccounts.breadcrumb', { defaultValue: 'Breadcrumb' })} className="flex items-center gap-2 text-sm text-muted-foreground">
        <Link to="/accounts" className="hover:text-foreground">{t('accounts.title')}</Link><ChevronRight size={14} /><span className="truncate" dir="auto">{name}</span>
      </nav>
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 items-center gap-3">
          <ConnectionLogo logoUrl={connection.institutions?.length > 1 ? null : connection.logo_url} className="h-11 w-11" />
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight break-words" dir="auto">{name}</h1>
            {!loading && !failed && <p className="mt-1 text-sm text-muted-foreground">{t('investmentAccounts.accountCount', { count, defaultValue: '{{count}} accounts' })}</p>}
          </div>
        </div>
        {canWrite && <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => refresh.mutate()} disabled={refresh.isPending || connection.status === 'syncing'}>
            <RefreshCw size={14} className={refresh.isPending ? 'animate-spin' : ''} />{t('investmentAccounts.refresh', { defaultValue: 'Refresh' })}
          </Button>
          <Button variant="ghost" size="icon" onClick={() => setSettingsOpen(true)} aria-label={t('connections.settings')}><Settings size={17} /></Button>
        </div>}
      </header>

      {allInvestments.length > 0 && stateKey && <div role="status" className="flex items-start gap-3 rounded-xl border border-border bg-muted/30 px-4 py-3 text-sm text-muted-foreground">
        <Info size={17} className="mt-0.5 shrink-0" /><p>{t(`investmentAccounts.${stateKey}`)}</p>
      </div>}
      {failed && <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border p-4 text-sm">
        <p>{t('investmentAccounts.accountsLoadError', { defaultValue: 'Some accounts could not be loaded.' })}</p><Button variant="outline" size="sm" onClick={retry}>{t('investments.retry')}</Button>
      </div>}
      {loading ? <Skeleton className="h-60 rounded-xl" /> : <>
        {investments.length > 0 && <section className="overflow-hidden rounded-xl border border-border bg-card" aria-label={t('investmentAccounts.title', { defaultValue: 'Investment accounts' })}>
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-5">
            <h2 className="text-sm font-semibold">{t('investmentAccounts.title', { defaultValue: 'Investment accounts' })}</h2>
            <div className="text-right">
              <p className="text-xs text-muted-foreground">{t('investmentAccounts.totalBalance', { defaultValue: 'Total balance' })}</p>
              <p className="mt-1 text-xl font-semibold tabular-nums">{total.amount === null ? t('investmentAccounts.valueUnavailable', { defaultValue: 'Not available' }) : mask(formatCurrency(total.amount, currency, locale))}</p>
              {total.missing > 0 && <p className="mt-1 text-xs text-muted-foreground">{t('investmentAccounts.incompleteTotal', { defaultValue: 'Some balances are unavailable.' })}</p>}
            </div>
          </div>
          <div className="divide-y divide-border">{investments.map(account => <InvestmentAccountRow key={account.id} account={account} />)}</div>
        </section>}
        {bankAccounts.length > 0 && <section className="overflow-hidden rounded-xl border border-border bg-card" aria-label={t('investmentAccounts.bankAccounts', { defaultValue: 'Bank accounts and cards' })}>
          <h2 className="border-b border-border px-5 py-4 text-sm font-semibold">{t('investmentAccounts.bankAccounts', { defaultValue: 'Bank accounts and cards' })}</h2>
          <div className="divide-y divide-border">{bankAccounts.map(account => <Link key={account.id} to={`/accounts/${account.id}`} className="flex items-center gap-3 px-4 py-4 hover:bg-muted/40 sm:px-5">
            <AccountIcon account={account} /><div className="min-w-0 flex-1"><p className="break-words text-sm font-medium" dir="auto">{getAccountName(account)}</p><p className="mt-1 text-xs text-muted-foreground">{t(getAccountTypeConfig(account.type).label)}{account.masked_number && <span dir="ltr"> · {mask(formatAccountMask(account)!)}</span>}</p></div>
            <span className="shrink-0 text-sm font-semibold tabular-nums">{mask(formatCurrency(Number(account.current_balance), account.currency, locale))}</span><ChevronRight size={15} className="hidden text-muted-foreground sm:block" />
          </Link>)}</div>
        </section>}
        {count === 0 && !failed && <p className="rounded-xl border border-border p-8 text-center text-sm text-muted-foreground">{t('investmentAccounts.noAccountsInView', { defaultValue: 'No accounts in this view.' })}</p>}
      </>}
      <ConnectionSettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} connection={connection}
        supportsAssetSync={providers?.find(provider => provider.name === connection.provider)?.supports_asset_sync ?? false}
        supportsTransactionSettings={allBankAccounts.length > 0 || (connection.provider !== 'investment_feed' && allInvestments.length === 0)} />
    </div>
  )
}
