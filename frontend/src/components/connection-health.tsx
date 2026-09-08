import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { CheckCircle2, CircleAlert, RefreshCw } from 'lucide-react'
import axios from 'axios'
import { connections } from '@/lib/api'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { useDateLocale } from '@/hooks/use-display-locale'
import { useWorkspace } from '@/contexts/workspace-context'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import type { BankConnection, ConnectionSourceStatus } from '@/types'

function sourceHealthKey(source: ConnectionSourceStatus['source'], now: number) {
  if (source.status === 'auth_required') return 'signIn'
  if (source.status === 'error') return 'failed'
  if (!source.lastSuccessAt || source.status === 'never_synced') return 'waiting'
  if (source.status === 'partial' || !source.inventoryComplete) return 'partial'
  if (!Number.isFinite(Date.parse(source.lastSuccessAt)) || now - Date.parse(source.lastSuccessAt) > source.staleAfterHours * 3_600_000) return 'stale'
  return 'current'
}

/** A source refresh is explicit; viewing or polling health never starts a login. */
export function ConnectionHealth({ connection, onReconnect }: { connection: BankConnection; onReconnect?: () => void }) {
  const { t } = useTranslation()
  const locale = useDateLocale()
  const { canWrite } = useWorkspace()
  const queryClient = useQueryClient()
  const [requested, setRequested] = useState(false)
  const previousFinish = useRef<string | null>(null)
  const [waitExpired, setWaitExpired] = useState(false)
  const [retryAt, setRetryAt] = useState<number | null>(null)
  const importedSuccess = useRef<string | null>(null)
  const health = useQuery({
    queryKey: ['connection-source', connection.id],
    queryFn: () => connections.sourceStatus(connection.id),
    refetchInterval: query => query.state.data?.collection.running || requested ? 3000 : 30_000,
    retry: false,
  })
  const savedImport = useMutation({
    mutationFn: () => connections.sync(connection.id),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['connections'] })
      health.refetch()
    },
  })
  const { mutate: importSaved, isPending: importing } = savedImport
  const data = health.data
  // Only an explicitly requested collection hands off to an import here.
  // Passive health checks remain read-only; scheduled imports run independently.
  useEffect(() => {
    if (!requested || !data || data.collection.running || health.isFetching) return
    if (!data.collection.lastFinishedAt || data.collection.lastFinishedAt === previousFinish.current) return
    setRequested(false)
    const success = data.source.lastSuccessAt
    if (!canWrite || !success || !['ok', 'partial'].includes(data.source.status) || !['ok', 'partial'].includes(data.collection.lastResult ?? '')) return
    if (data.import.lastImportedAt && Date.parse(success) <= Date.parse(data.import.lastImportedAt)) return
    if (importedSuccess.current === success) return
    importedSuccess.current = success
    importSaved()
  }, [requested, canWrite, data, importSaved, health.isFetching])
  useEffect(() => {
    if (!requested) return
    const timer = window.setTimeout(() => { setRequested(false); setWaitExpired(true) }, 12 * 60_000)
    return () => window.clearTimeout(timer)
  }, [requested])
  const refresh = useMutation({
    mutationFn: () => connections.refreshSource(connection.id),
    onMutate: () => { previousFinish.current = data?.collection.lastFinishedAt ?? null; setWaitExpired(false); setRequested(true) },
    onSuccess: () => { setRetryAt(null); health.refetch() },
    onError: error => {
      setRequested(false)
      if (axios.isAxiosError(error) && error.response?.status === 429) {
        const seconds = Number(error.response.headers['retry-after'] ?? error.response.data?.detail?.retryAfterSeconds ?? 60)
        setRetryAt(Date.now() + Math.max(1, Number.isFinite(seconds) ? seconds : 60) * 1000)
      }
      health.refetch()
    },
  })
  const date = (value: string | null | undefined) => value ? new Date(value).toLocaleString(locale) : t('connectionHealth.notYet')
  if (health.isLoading) return <Skeleton className="h-60 rounded-xl" />
  const accessExpired = axios.isAxiosError(health.error) && health.error.response?.data?.detail?.code === 'source_access_expired'
  if (!data || health.isError) return <section className="rounded-xl border border-border bg-card p-5 space-y-3" role="alert">
    <h2 className="font-semibold">{t(accessExpired ? 'connectionHealth.accessExpiredTitle' : 'connectionHealth.unavailableTitle')}</h2>
    <p className="text-sm text-muted-foreground">{t(accessExpired ? 'connectionHealth.accessExpiredHelp' : 'connectionHealth.unavailableHelp')}</p>
    {canWrite && accessExpired && onReconnect && <Button onClick={onReconnect}>{t('accounts.reconnect')}</Button>}
    <Button variant="outline" onClick={() => health.refetch()}>{t('connectionHealth.checkStatus')}</Button>
  </section>
  const observedNow = Math.max(health.dataUpdatedAt, Date.parse(data.observedAt))
  const state = sourceHealthKey(data.source, observedNow)
  const busy = data.collection.running || refresh.isPending || requested
  const otpWait = data.automaticOtp.nextAllowedAt ? Date.parse(data.automaticOtp.nextAllowedAt) : 0
  const waitUntil = Math.max(otpWait, retryAt ?? 0)
  const limited = waitUntil > observedNow
  const needsPhone = data.automaticOtp.enabled && !data.automaticOtp.ready
  const unimported = !!data.source.lastSuccessAt && (!data.import.lastImportedAt || Date.parse(data.source.lastSuccessAt) > Date.parse(data.import.lastImportedAt))
  const skipped = data.collection.lastResult === 'skipped'
  const problem = state !== 'current' || skipped
  const reason = data.automaticOtp.reason
  const pairNeeded = reason === 'not_paired' || reason === 'unpaired' || reason === 'pairing_required' || reason === 'reauth_required'
  const connecting = reason === 'connecting' || reason === 'phone_syncing'
  const receiverUnavailable = reason === 'unavailable'
  const otpHelp = !data.automaticOtp.enabled ? 'manualHelp' : limited || reason === 'rate_limited' ? 'otpLimitHelp' : pairNeeded ? 'pairHelp' : receiverUnavailable ? 'receiverUnavailableHelp' : connecting ? 'phoneConnectingHelp' : needsPhone ? 'phoneHelp' : 'retryHelp'
  const otpLabel = data.automaticOtp.ready ? 'verificationReady' : limited || reason === 'rate_limited' ? 'verificationLimited' : pairNeeded ? 'verificationPairing' : receiverUnavailable ? 'verificationUnavailable' : connecting ? 'verificationConnecting' : 'verificationOffline'
  const displayState = busy ? 'updating' : skipped ? 'skipped' : state
  return <div className="space-y-5">
    <section className="rounded-xl border border-border bg-card p-5 space-y-4" aria-label={t('connectionHealth.status')}>
      <div className="flex items-start gap-3" role="status">
        {problem ? <CircleAlert size={20} className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" /> : <CheckCircle2 size={20} className="mt-0.5 shrink-0 text-emerald-600 dark:text-emerald-400" />}
        <div className="space-y-1">
          <h2 className="font-semibold">{t(`connectionHealth.${displayState}Title`)}</h2>
          <p className="text-sm leading-relaxed text-muted-foreground">{t(`connectionHealth.${displayState}Help`)}</p>
        </div>
      </div>
      {state === 'signIn' && <div className="rounded-lg bg-muted/50 p-4 text-sm space-y-2">
        <p className="font-medium">{t('connectionHealth.nextStep')}</p>
        <p className="text-muted-foreground leading-relaxed">{t(`connectionHealth.${otpHelp}`)}</p>
      </div>}
      {waitExpired && <p role="status" className="text-sm text-muted-foreground">{t('connectionHealth.waitExpired')}</p>}
      {limited && <p className="text-sm text-muted-foreground">{t('connectionHealth.retryAfter', { date: date(new Date(waitUntil).toISOString()) })}</p>}
      {refresh.isError && !limited && <p role="alert" className="text-sm text-destructive">{t('connectionHealth.retryFailed')}</p>}
      {canWrite && <div className="flex flex-wrap items-center gap-3">
        <Button onClick={() => refresh.mutate()} disabled={busy || limited || (state === 'signIn' && !data.automaticOtp.enabled)}>
          <RefreshCw size={15} className={busy ? 'animate-spin' : ''} />{t(busy ? 'connectionHealth.updatingTitle' : problem ? 'connectionHealth.retryConnection' : 'connectionHealth.updateNow')}
        </Button>
        <Button variant="ghost" onClick={() => health.refetch()} disabled={health.isFetching}>{t('connectionHealth.checkStatus')}</Button>
      </div>}
      {data.automaticOtp.enabled && <div className="border-t border-border pt-3 text-xs text-muted-foreground space-y-1">
        <p>{t('connectionHealth.verification')}: <span className="font-medium text-foreground">{t(`connectionHealth.${otpLabel}`)}</span></p>
        <p>{t('connectionHealth.verificationLimit')}</p>
      </div>}
      {!canWrite && problem && <p className="text-sm text-muted-foreground">{t('connectionHealth.readerHelp')}</p>}
    </section>
    <section className="rounded-xl border border-border bg-card overflow-hidden" aria-label={t('connectionHealth.updates')}>
      <h2 className="border-b border-border px-5 py-4 font-semibold text-sm">{t('connectionHealth.updates')}</h2>
      <dl className="grid gap-5 p-5 text-sm sm:grid-cols-2">
        <div><dt className="text-muted-foreground">{t('connectionHealth.lastSuccess')}</dt><dd className="mt-1 font-medium">{date(data.source.lastSuccessAt)}</dd></div>
        <div><dt className="text-muted-foreground">{t('connectionHealth.lastAttempt')}</dt><dd className="mt-1 font-medium">{date(data.source.lastAttemptAt)}</dd></div>
        <div><dt className="text-muted-foreground">{t('connectionHealth.schedule')}</dt><dd className="mt-1 font-medium">{data.schedule.enabled ? data.schedule.description ?? t('connectionHealth.scheduleUnknown') : t('connectionHealth.scheduleDisabled')}</dd>{data.schedule.enabled && <dd className="mt-1 text-xs text-muted-foreground">{data.schedule.timezone}</dd>}</div>
        <div><dt className="text-muted-foreground">{t('connectionHealth.nextRun')}</dt><dd className="mt-1 font-medium">{data.schedule.enabled ? date(data.schedule.nextRunAt) : t('connectionHealth.scheduleDisabled')}</dd></div>
      </dl>
      <div className="border-t border-border px-5 py-4 text-sm space-y-2">
        <p><span className="text-muted-foreground">{t('connectionHealth.inSecuro')}: </span>{date(data.import.lastImportedAt)}</p>
        <details className="text-xs text-muted-foreground"><summary className="cursor-pointer hover:text-foreground">{t('connectionHealth.importDetails')}</summary><p className="mt-2 leading-relaxed">{t('connectionHealth.importTiming', { hours: data.import.minimumIntervalMinutes / 60, minutes: data.import.checkIntervalMinutes })}</p></details>
        {canWrite && unimported && !busy && !importing && !savedImport.isError && <Button variant="outline" size="sm" onClick={() => savedImport.mutate()}>{t('connectionHealth.importSaved')}</Button>}
        {importing && <p role="status" className="text-sm">{t('connectionHealth.importing')}</p>}
        {savedImport.isError && <div role="alert" className="space-y-2"><p>{t('connectionHealth.importFailed')}</p>{canWrite && <Button variant="outline" size="sm" onClick={() => savedImport.mutate()}>{t('connectionHealth.importSaved')}</Button>}</div>}
      </div>
    </section>

  </div>
}
