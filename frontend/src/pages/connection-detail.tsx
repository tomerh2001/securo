import { useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, CircleAlert, RefreshCw, Settings } from 'lucide-react'
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ConnectionHealth } from '@/components/connection-health'
import { TokenConnectDialog } from '@/components/token-connect-dialog'
import { BankConnectDialog } from '@/components/bank-connect-dialog'

export default function ConnectionDetailPage() {
  const { id } = useParams<{ id: string }>()
  const location = useLocation()
  const navigate = useNavigate()
  const healthTab = location.hash === '#connection-health'
  const [reconnectOpen, setReconnectOpen] = useState(false)
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
  const provider = providers?.find(item => item.name === connection?.provider)
  const sourceRefresh = connection?.source_refresh_available ?? provider?.supports_source_refresh ?? false
  const needsReconnect = !!connection && !['active', 'syncing'].includes(connection.status)
  const changeTab = (value: string) => navigate({ pathname: location.pathname, hash: value === 'health' ? '#connection-health' : '' }, { replace: true })
  const reconnect = async () => {
    if (!connection || !provider) return
    if (provider.flow_type === 'oauth') {
      try { window.location.assign(await connections.getReauthUrl(connection.id)) } catch { toast.error(t('accounts.connectError')) }
    } else setReconnectOpen(true)
  }
  const refresh = useMutation({
    mutationFn: () => connections.sync(id!),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['connections'] })
      toast.success(t(sourceRefresh ? 'connectionHealth.savedImported' : 'accounts.syncDone'))
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
  const attention = sourceState !== 'current' || needsReconnect
  const stateKey = { signInRequired: 'statusSignInRequired', unavailable: 'statusUnavailable', neverSynced: 'statusNeverSynced', partial: 'statusPartial', stale: 'statusStale', current: '' }[sourceState]
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
          <Button variant="ghost" size="icon" onClick={() => setSettingsOpen(true)} aria-label={t('connections.settings')}><Settings size={17} /></Button>
        </div>}
      </header>

      <Tabs value={healthTab ? 'health' : 'accounts'} onValueChange={changeTab} className="gap-5">
        <TabsList variant="line" aria-label={t('connectionHealth.sections')}>
          <TabsTrigger value="accounts">{t('accounts.title')}</TabsTrigger>
          <TabsTrigger value="health">{t('connectionHealth.title')}{attention && <CircleAlert size={14} className="text-amber-600 dark:text-amber-400" aria-label={t('investmentAccounts.needsAttention')} />}</TabsTrigger>
        </TabsList>
        <TabsContent value="accounts" className="space-y-5">
          {attention && <div role="status" className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-500/25 bg-amber-500/5 px-4 py-3">
            <div className="min-w-0 flex-1 space-y-1"><p className="text-sm font-medium">{stateKey ? t(`investmentAccounts.${stateKey}`) : t('connectionHealth.signInTitle')}</p><p className="text-xs text-muted-foreground">{t('connectionHealth.savedAvailable')}</p></div>
            <Button variant="outline" size="sm" onClick={() => changeTab('health')}>{t('connectionHealth.fixConnection')}</Button>
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
        </TabsContent>
        <TabsContent value="health" id="connection-health" className="space-y-5">
          {sourceRefresh ? <ConnectionHealth key={connection.id} connection={connection} onReconnect={reconnect} /> : <section className="rounded-xl border border-border bg-card p-5 space-y-4">
            <h2 className="font-semibold">{t(needsReconnect ? 'connectionHealth.signInTitle' : 'connectionHealth.savedDataTitle')}</h2>
            <p className="text-sm text-muted-foreground">{t(needsReconnect ? 'connectionHealth.reconnectHelp' : 'connectionHealth.savedDataHelp')}</p>
            <p className="text-sm"><span className="text-muted-foreground">{t('connectionHealth.inSecuro')}: </span>{connection.last_sync_at ? new Date(connection.last_sync_at).toLocaleString(locale) : t('connectionHealth.notYet')}</p>
            {allInvestments[0]?.provider === 'hapoalim' ? <div className="space-y-2 text-sm">
              <p className="text-muted-foreground">{t('connectionHealth.hapoalimCachedHelp')}</p>
              <p><span className="text-muted-foreground">{t('connectionHealth.lastSuccess')}: </span>{allInvestments[0].details.source.lastSuccessAt ? new Date(allInvestments[0].details.source.lastSuccessAt).toLocaleString(locale) : t('connectionHealth.notYet')}</p>
              <p><span className="text-muted-foreground">{t('connectionHealth.lastAttempt')}: </span>{allInvestments[0].details.source.lastAttemptAt ? new Date(allInvestments[0].details.source.lastAttemptAt).toLocaleString(locale) : t('connectionHealth.notYet')}</p>
            </div> : allInvestments.length > 0 && <p className="text-sm text-muted-foreground">{t('connectionHealth.unavailableHelp')}</p>}
            {canWrite && <Button variant="outline" onClick={() => needsReconnect ? reconnect() : refresh.mutate()} disabled={refresh.isPending || connection.status === 'syncing'}>
              <RefreshCw size={14} className={refresh.isPending ? 'animate-spin' : ''} />{t(needsReconnect ? 'accounts.reconnect' : 'connectionHealth.importSaved')}
            </Button>}
          </section>}
        </TabsContent>
      </Tabs>
      {reconnectOpen && provider?.flow_type === 'token' && <TokenConnectDialog open onClose={() => setReconnectOpen(false)} provider={provider.name} supportsAssetSync={provider.supports_asset_sync} reconnectConnectionId={connection.id} />}
      {reconnectOpen && provider?.flow_type === 'widget' && <BankConnectDialog open onClose={() => setReconnectOpen(false)} provider={provider.name} supportsAssetSync={provider.supports_asset_sync} reconnectConnectionId={connection.id} updateItemId={connection.external_id} />}
      <ConnectionSettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} connection={connection}
        supportsAssetSync={providers?.find(provider => provider.name === connection.provider)?.supports_asset_sync ?? false}
        supportsTransactionSettings={allBankAccounts.length > 0 || (connection.provider !== 'investment_feed' && allInvestments.length === 0)} />
    </div>
  )
}
