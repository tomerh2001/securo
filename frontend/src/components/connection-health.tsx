import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { CheckCircle2, CircleAlert, Clock3, Database, RefreshCw, ShieldCheck, Smartphone } from 'lucide-react'
import axios from 'axios'
import { connections } from '@/lib/api'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { useDateLocale } from '@/hooks/use-display-locale'
import { useWorkspace } from '@/contexts/workspace-context'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import type { BankConnection, ConnectionOperation, ConnectionSourceStatus } from '@/types'

function operationIsActive(operation: ConnectionOperation) {
  return ['queued', 'collecting', 'awaiting_verification', 'importing'].includes(operation.status)
}

function sourceHealthKey(source: ConnectionSourceStatus['source'], now: number) {
  if (source.status === 'auth_required') return 'signIn'
  if (source.status === 'error') return 'failed'
  if (!source.lastSuccessAt || source.status === 'never_synced') return 'waiting'
  if (source.status === 'partial' || !source.inventoryComplete) return 'partial'
  if (!Number.isFinite(Date.parse(source.lastSuccessAt)) || now - Date.parse(source.lastSuccessAt) > source.staleAfterHours * 3_600_000) return 'stale'
  return 'current'
}

function errorCode(error: unknown): string | null {
  return axios.isAxiosError(error) && typeof error.response?.data?.detail?.code === 'string' ? error.response.data.detail.code : null
}

/** Server-owned operations survive navigation. GETs never start a login or import. */
export function ConnectionHealth({ connection, onReconnect, supportsSourceRefresh = true }: {
  connection: BankConnection; onReconnect?: () => void; supportsSourceRefresh?: boolean
}) {
  const { t } = useTranslation()
  const locale = useDateLocale()
  const { canWrite } = useWorkspace()
  const queryClient = useQueryClient()
  const [now, setNow] = useState(Date.now)
  const [retryAt, setRetryAt] = useState<number | null>(null)
  const [verificationCode, setVerificationCode] = useState('')
  const observedCompletion = useRef<string | null>(null)
  const operations = useQuery({
    queryKey: ['connection-operations', connection.id],
    queryFn: () => connections.operations(connection.id),
    refetchInterval: query => query.state.data?.some(operationIsActive) ? 2000 : 30_000,
    retry: false,
  })
  const active = operations.data?.find(operationIsActive)
  const latest = operations.data?.[0]
  const health = useQuery({
    queryKey: ['connection-source', connection.id],
    queryFn: () => connections.sourceStatus(connection.id),
    enabled: supportsSourceRefresh,
    refetchInterval: active ? 3000 : 30_000,
    retry: false,
  })
  const data = health.data
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  useEffect(() => {
    if (!latest?.finished_at || latest.id === observedCompletion.current) return
    observedCompletion.current = latest.id
    invalidateFinancialQueries(queryClient)
    for (const key of ['connections', 'connection-source', 'account-history-coverage']) queryClient.invalidateQueries({ queryKey: [key] })
  }, [latest?.id, latest?.finished_at, latest, queryClient])
  const start = useMutation({
    mutationFn: (kind: ConnectionOperation['kind']) => connections.startOperation(connection.id, kind),
    onSuccess: operation => {
      queryClient.setQueryData<ConnectionOperation[]>(['connection-operations', connection.id], previous => [operation, ...(previous ?? []).filter(item => item.id !== operation.id)])
      setRetryAt(null)
      operations.refetch()
      if (supportsSourceRefresh) health.refetch()
    },
    onError: error => {
      if (axios.isAxiosError(error) && error.response?.status === 429) {
        const seconds = Number(error.response.headers?.['retry-after'] ?? error.response.data?.detail?.retryAfterSeconds ?? 60)
        setRetryAt(Date.now() + Math.max(1, Number.isFinite(seconds) ? seconds : 60) * 1000)
      }
      operations.refetch()
      if (supportsSourceRefresh) health.refetch()
    },
  })
  const verification = useMutation({
    mutationFn: () => connections.submitVerification(connection.id, active!.id, verificationCode),
    onSettled: () => { setVerificationCode(''); operations.refetch(); health.refetch() },
  })
  const cancelVerification = useMutation({
    mutationFn: () => connections.cancelVerification(connection.id, active!.id),
    onSettled: () => { setVerificationCode(''); operations.refetch(); health.refetch() },
  })
  const date = (value: string | null | undefined, timeZone?: string) => {
    if (!value || !Number.isFinite(Date.parse(value))) return t('connectionHealth.notYet')
    try { return new Date(value).toLocaleString(locale, timeZone ? { timeZone } : undefined) }
    catch { return t('connectionHealth.notYet') }
  }
  const codeText = (code: string | null, fallback = 'operationUnknown') => code ? t(`connectionHealth.codes.${code}`, { defaultValue: t(`connectionHealth.${fallback}`) }) : t(`connectionHealth.${fallback}`)
  const check = () => { operations.refetch(); if (supportsSourceRefresh) health.refetch() }
  if (operations.isLoading || (supportsSourceRefresh && health.isLoading)) return <Skeleton className="h-64 rounded-xl" />
  const state = data ? sourceHealthKey(data.source, now) : null
  const sourceError = errorCode(health.error)
  const accessExpired = sourceError === 'source_access_expired'
  const otp = data?.automaticOtp
  const session = data?.session
  const phoneReason = otp?.reason
  const pairingRequired = ['reauth_required', 'not_paired', 'unpaired', 'pairing_required'].includes(phoneReason ?? '')
  const needsSignIn = session?.status === 'auth_required' || session?.expired || state === 'signIn'
  const operationRetryAt = latest?.retry_after_seconds && latest.finished_at
    ? (Date.parse(latest.finished_at) || 0) + latest.retry_after_seconds * 1000 : 0
  const waitUntil = Math.max(otp?.nextAllowedAt ? Date.parse(otp.nextAllowedAt) || 0 : 0, retryAt ?? 0, operationRetryAt)
  const limited = waitUntil > now
  const importLimited = latest?.message_code === 'provider_rate_limited' && operationRetryAt > now
  const blockedPhone = !!needsSignIn && (!otp?.enabled || !otp.ready)
  const busy = !!active || start.isPending || !!data?.collection.running
  const reconnectNeeded = !['active', 'syncing'].includes(connection.status)
  const sessionKey = session?.verifiedActive ? 'sessionActive' : session?.expired ? 'sessionExpired' : session?.status === 'auth_required' ? 'signInTitle' : session?.status === 'error' ? 'sessionFailed' : 'sessionUnknown'
  const otpKey = otp?.ready ? 'verificationReady' : limited || phoneReason === 'rate_limited' ? 'verificationLimited' : pairingRequired ? 'verificationPairing' : phoneReason === 'unavailable' ? 'verificationUnavailable' : ['connecting', 'phone_syncing'].includes(phoneReason ?? '') ? 'verificationConnecting' : !otp?.enabled ? 'verificationUnconfigured' : 'verificationOffline'
  const guidance = pairingRequired ? data?.manualVerificationAvailable ? 'pairHelp' : 'pairAutomaticHelp' : phoneReason === 'unavailable' ? 'receiverUnavailableHelp' : !otp?.enabled ? 'manualHelp' : limited ? 'otpLimitHelp' : ['connecting', 'phone_syncing'].includes(phoneReason ?? '') ? 'phoneConnectingHelp' : 'phoneHelp'
  const sessionProblem = session?.errorCode || data?.source.errorCode
  const canRefresh = supportsSourceRefresh && !!data && !health.isError && !blockedPhone && !limited && !busy && !operations.isError && !['INVALID_CREDENTIALS', 'ACCOUNT_BLOCKED', 'CREDENTIAL_RESOLUTION_FAILED'].includes(sessionProblem ?? '')
  const recovery = data?.recovery
  const recoveryExpired = !!recovery?.expiresAt && Date.parse(recovery.expiresAt) <= now
  const operationMessage = active ? t(`connectionHealth.operationStages.${active.status}`) : latest ? t(`connectionHealth.operationStages.${latest.status}`) : t('connectionHealth.noOperations')
  return <div className="space-y-5">
    <section className="rounded-xl border border-border bg-card p-5 sm:p-6 space-y-4" aria-label={t('connectionHealth.status')}>
      <div className="flex items-start gap-3" role="status" aria-live="polite">
        {busy ? <RefreshCw size={21} className="mt-0.5 shrink-0 animate-spin text-primary" /> : state === 'current' && !needsSignIn && session?.status !== 'error' ? <CheckCircle2 size={21} className="mt-0.5 shrink-0 text-emerald-600" /> : <CircleAlert size={21} className="mt-0.5 shrink-0 text-amber-600" />}
        <div className="space-y-1.5">
          <h2 className="font-semibold">{active ? operationMessage : data?.collection.running ? t('connectionHealth.updatingTitle') : supportsSourceRefresh ? t(accessExpired ? 'connectionHealth.accessExpiredTitle' : !data || health.isError ? 'connectionHealth.unavailableTitle' : needsSignIn ? 'connectionHealth.signInTitle' : session?.status === 'error' ? 'connectionHealth.sessionFailed' : `connectionHealth.${state}Title`) : t(reconnectNeeded ? 'connectionHealth.accessExpiredTitle' : 'connectionHealth.savedDataTitle')}</h2>
          <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">{t(active ? 'connectionHealth.serverContinues' : supportsSourceRefresh ? accessExpired ? 'connectionHealth.accessExpiredHelp' : !data || health.isError ? 'connectionHealth.unavailableHelp' : needsSignIn ? 'connectionHealth.signInHelp' : `connectionHealth.${state}Help` : connection.provider === 'investment_feed' ? 'connectionHealth.hapoalimCachedHelp' : 'connectionHealth.savedDataHelp')}</p>
        </div>
      </div>
      {supportsSourceRefresh && needsSignIn && blockedPhone && active?.kind !== 'recover' && <div className="rounded-lg border border-amber-500/25 bg-amber-500/5 p-4 space-y-2">
        <p className="text-sm font-medium">{t('connectionHealth.nextStep')}</p>
        <p className="text-sm leading-relaxed text-muted-foreground">{t(`connectionHealth.${guidance}`)}</p>
      </div>}
      {recovery?.errorCode && <p role="alert" className="text-sm text-destructive">{codeText(recovery.errorCode, 'codeFailed')}</p>}
      {sessionProblem && sessionProblem !== 'OTP_REQUIRED' && <p className="text-sm text-muted-foreground">{codeText(sessionProblem, 'providerProblem')}</p>}
      {sourceError && !accessExpired && <p role="alert" className="text-sm text-muted-foreground">{codeText(sourceError, 'controlsProblem')}</p>}
      {limited && <p className="text-sm text-muted-foreground">{t('connectionHealth.retryAfter', { date: date(new Date(waitUntil).toISOString()) })}</p>}
      {start.isError && <p role="alert" className="text-sm text-destructive">{codeText(errorCode(start.error), 'retryFailed')}</p>}
      {operations.isError && <p role="alert" className="text-sm text-destructive">{t('connectionHealth.operationLoadError')}</p>}
      {cancelVerification.isError && <p role="alert" className="text-sm text-destructive">{t('connectionHealth.cancelFailed')}</p>}
      {active?.kind === 'recover' && recovery?.challengeId === active.id && recovery.state === 'awaiting_code' && canWrite && <form className="rounded-lg border border-primary/25 bg-primary/5 p-4 space-y-3" onSubmit={event => { event.preventDefault(); if (/^\d{6}$/.test(verificationCode) && !recoveryExpired) verification.mutate() }}>
        <div className="space-y-1"><Label htmlFor={`verification-${connection.id}`}>{t('connectionHealth.enterCode')}</Label><p className="text-sm text-muted-foreground">{t('connectionHealth.codeHelp')}</p></div>
        <Input id={`verification-${connection.id}`} className="max-w-48 font-mono text-lg tracking-widest" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={verificationCode} onChange={event => setVerificationCode(event.target.value.replace(/\D/g, '').slice(0, 6))} disabled={verification.isPending || recoveryExpired} aria-describedby={`verification-help-${connection.id}`} />
        <p id={`verification-help-${connection.id}`} className="text-xs text-muted-foreground">{recoveryExpired ? t('connectionHealth.codeExpired') : t('connectionHealth.codeExpires', { date: date(recovery.expiresAt) })}</p>
        <div className="flex flex-wrap gap-2"><Button type="submit" disabled={verificationCode.length !== 6 || verification.isPending || cancelVerification.isPending || recoveryExpired}>{t(verification.isPending ? 'connectionHealth.verifyingCode' : 'connectionHealth.verifyCode')}</Button><Button type="button" variant="outline" onClick={() => cancelVerification.mutate()} disabled={verification.isPending || cancelVerification.isPending}>{t('common.cancel')}</Button></div>
        {verification.isError && <p role="alert" className="text-sm text-destructive">{t('connectionHealth.codeFailed')}</p>}
      </form>}
      <div className="flex flex-wrap items-center gap-2">
        {canWrite && (accessExpired || (!supportsSourceRefresh && reconnectNeeded)) && onReconnect && <Button onClick={onReconnect}>{t('accounts.reconnect')}</Button>}
        {canWrite && supportsSourceRefresh && active?.kind !== 'recover' && !accessExpired && (!blockedPhone || !data?.manualVerificationAvailable || busy) && <Button onClick={() => start.mutate('refresh')} disabled={!canRefresh}><RefreshCw size={15} className={busy ? 'animate-spin' : ''} />{t(busy ? 'connectionHealth.updatingTitle' : 'connectionHealth.updateNow')}</Button>}
        {canWrite && !active && data?.manualVerificationAvailable && needsSignIn && !accessExpired && <Button variant={blockedPhone ? 'default' : 'outline'} onClick={() => start.mutate('recover')} disabled={busy || limited || operations.isError || health.isError}><Smartphone size={15} />{t('connectionHealth.signInByText')}</Button>}
        {canWrite && active?.kind !== 'recover' && !accessExpired && !(reconnectNeeded && !supportsSourceRefresh) && <Button variant="outline" onClick={() => start.mutate('import')} disabled={busy || operations.isError || importLimited}>{t('connectionHealth.importSaved')}</Button>}
        {canWrite && active?.kind === 'recover' && recovery?.challengeId === active.id && ['starting', 'verifying'].includes(recovery.state) && <Button variant="outline" onClick={() => cancelVerification.mutate()} disabled={cancelVerification.isPending}>{t('connectionHealth.cancelSignIn')}</Button>}
        <Button variant="ghost" onClick={check} disabled={health.isFetching || operations.isFetching}>{t('connectionHealth.checkStatus')}</Button>
      </div>
      {!canWrite && <p className="text-xs text-muted-foreground">{t('connectionHealth.readerHelp')}</p>}
    </section>

    <section className="grid gap-4 sm:grid-cols-2" aria-label={t('connectionHealth.connectionChecks')}>
      {supportsSourceRefresh && <>
        <div className="rounded-xl border border-border bg-card p-5 space-y-3">
          <h3 className="flex items-center gap-2 text-sm font-semibold"><ShieldCheck size={17} className="text-muted-foreground" />{t('connectionHealth.providerSignIn')}</h3>
          <p className="text-sm">{t(`connectionHealth.${sessionKey}`)}</p>
          <dl className="text-xs text-muted-foreground space-y-2"><div><dt>{t('connectionHealth.sessionChecked')}</dt><dd className="mt-1">{date(session?.lastCheckedAt)}</dd></div><div><dt>{t('connectionHealth.sessionExpires')}</dt><dd className="mt-1">{date(session?.expiresAt)}</dd></div></dl>
          {session?.overdue && <p className="text-xs text-amber-700 dark:text-amber-400">{t('connectionHealth.sessionOverdue')}</p>}
        </div>
        <div className="rounded-xl border border-border bg-card p-5 space-y-3">
          <h3 className="flex items-center gap-2 text-sm font-semibold"><Smartphone size={17} className="text-muted-foreground" />{t('connectionHealth.verification')}</h3>
          <p className="text-sm">{t(`connectionHealth.${otpKey}`)}</p>
          <p className="text-xs leading-relaxed text-muted-foreground">{t(otp?.ready ? 'connectionHealth.phoneReady' : `connectionHealth.${guidance}`)}</p>
        </div>
      </>}
      <div className="rounded-xl border border-border bg-card p-5 space-y-3">
        <h3 className="flex items-center gap-2 text-sm font-semibold"><Database size={17} className="text-muted-foreground" />{t('connectionHealth.savedDataTitle')}</h3>
        {supportsSourceRefresh && <div className="text-sm"><p className="text-xs text-muted-foreground">{t('connectionHealth.lastSuccess')}</p><p className="mt-1">{date(data?.source.lastSuccessAt)}</p></div>}
        <div className="text-sm"><p className="text-xs text-muted-foreground">{t('connectionHealth.inSecuro')}</p><p className="mt-1">{date(data?.import.lastImportedAt ?? connection.last_sync_at)}</p></div>
        <p className="text-xs leading-relaxed text-muted-foreground">{t('connectionHealth.importDistinction')}</p>
      </div>
      <div className="rounded-xl border border-border bg-card p-5 space-y-3">
        <h3 className="flex items-center gap-2 text-sm font-semibold"><Clock3 size={17} className="text-muted-foreground" />{t('connectionHealth.schedule')}</h3>
        <p className="text-sm">{supportsSourceRefresh ? data?.schedule.enabled ? data.schedule.description || t('connectionHealth.scheduleUnknown') : t('connectionHealth.scheduleDisabled') : t('connectionHealth.providerSchedule')}</p>
        {data?.schedule.enabled && <p className="text-xs text-muted-foreground">{t('connectionHealth.nextRun')}: {date(data.schedule.nextRunAt, data.schedule.timezone)} · {data.schedule.timezone}</p>}
        <p className="text-xs leading-relaxed text-muted-foreground">{t('connectionHealth.serverContinues')}</p>
      </div>
    </section>

    <section className="rounded-xl border border-border bg-card overflow-hidden" aria-label={t('connectionHealth.operationHistory')}>
      <h2 className="border-b border-border px-5 py-4 text-sm font-semibold">{t('connectionHealth.operationHistory')}</h2>
      {!operations.data?.length && <p className="p-5 text-sm text-muted-foreground">{t('connectionHealth.noOperations')}</p>}
      <ol className="divide-y divide-border">{operations.data?.map(operation => <li key={operation.id} className="p-5 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-medium">{t(operation.kind === 'refresh' ? 'connectionHealth.updateNow' : operation.kind === 'recover' ? 'connectionHealth.signInByText' : 'connectionHealth.importSaved')}</p>
          <p className={`text-xs font-medium ${operation.status === 'failed' ? 'text-destructive' : operation.status === 'partial' ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'}`}>{t(`connectionHealth.operationStages.${operation.status}`)}</p>
        </div>
        <p className="text-xs text-muted-foreground">{date(operation.requested_at)}{operation.finished_at && ` · ${t('connectionHealth.finishedAt', { date: date(operation.finished_at) })}`}</p>
        {operation.message_code && <p className="text-sm text-muted-foreground">{codeText(operation.message_code)}</p>}
        {['succeeded', 'partial'].includes(operation.status) && <div className="text-sm text-muted-foreground">
          {Object.values(operation.result).some(value => (value ?? 0) > 0) ? <ul className="flex flex-wrap gap-x-5 gap-y-1">{Object.entries(operation.result).filter(([, count]) => count != null).map(([kind, count]) => <li key={kind}>{t(`connectionHealth.results.${kind}`, { count })}</li>)}</ul> : <p>{t('connectionHealth.noNewRecords')}</p>}
        </div>}
        {operation.events.length > 0 && <details className="text-xs"><summary className="cursor-pointer text-muted-foreground hover:text-foreground">{t('connectionHealth.viewSteps')}</summary>
          <ol className="mt-3 space-y-3 border-l border-border pl-4">{operation.events.map((event, index) => <li key={`${event.at}:${index}`}><p className="font-medium">{t(`connectionHealth.operationStages.${event.stage}`, { defaultValue: t('connectionHealth.operationStep') })}</p><p className="mt-0.5 text-muted-foreground">{date(event.at)}{event.code ? ` · ${codeText(event.code)}` : ''}</p></li>)}</ol>
        </details>}
      </li>)}</ol>
    </section>
  </div>
}
