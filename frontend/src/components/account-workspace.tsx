import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ChevronRight, CircleAlert, Link2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { TabsList, TabsTrigger } from '@/components/ui/tabs'

/** Shared navigation and content width; each account keeps its own ledger. */
export function AccountWorkspace({ children }: { children: ReactNode }) {
  return <div className="mx-auto w-full max-w-6xl space-y-6 pb-6">{children}</div>
}

export function AccountWorkspaceHeader({ title, subtitle, breadcrumbs, actions, icon }: {
  title: ReactNode
  subtitle?: ReactNode
  breadcrumbs: { label: string; to?: string }[]
  actions?: ReactNode
  icon?: ReactNode
}) {
  const { t } = useTranslation()
  return <header className="space-y-5">
    <nav aria-label={t('investmentAccounts.breadcrumb')} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted-foreground">
      {breadcrumbs.map((item, index) => <span key={`${index}:${item.label}`} className="inline-flex min-w-0 items-center gap-2">
        {index > 0 && <ChevronRight size={14} aria-hidden="true" />}
        {item.to ? <Link to={item.to} className="break-words hover:text-foreground hover:underline" dir="auto">{item.label}</Link>
          : <span aria-current={index === breadcrumbs.length - 1 ? 'page' : undefined} className="break-words" dir="auto">{item.label}</span>}
      </span>)}
    </nav>
    <div className="flex flex-col items-start justify-between gap-4 sm:flex-row">
      <div className="flex min-w-0 flex-1 items-start gap-3">
        {icon}
        <div className="min-w-0"><h1 className="break-words text-2xl font-semibold tracking-tight sm:text-3xl" dir="auto">{title}</h1>
          {subtitle && <div className="mt-1.5 text-sm text-muted-foreground">{subtitle}</div>}
        </div>
      </div>
      {actions && <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:justify-end">{actions}</div>}
    </div>
  </header>
}

export function AccountSectionTabs({ reports = false, executions = false }: { reports?: boolean; executions?: boolean }) {
  const { t } = useTranslation()
  return <div className="max-w-full overflow-x-auto">
    <TabsList variant="line" aria-label={t('investmentAccounts.accountSections')} className="min-w-max">
      <TabsTrigger value="overview">{t('investmentAccounts.overview')}</TabsTrigger>
      <TabsTrigger value="activity">{t('investmentAccounts.activity')}</TabsTrigger>
      <TabsTrigger value="history">{t('accountWorkspace.history')}</TabsTrigger>
      <TabsTrigger value="connection">{t('accountWorkspace.connection')}</TabsTrigger>
      {executions && <TabsTrigger value="executions">{t('investmentExecutions.title')}</TabsTrigger>}
      {reports && <TabsTrigger value="reports">{t('investmentAccounts.reports')}</TabsTrigger>}
    </TabsList>
  </div>
}

export function AccountConnectionPanel({ connectionId, institution, children }: { connectionId: string | null; institution?: string | null; children?: ReactNode }) {
  const { t } = useTranslation()
  return <section className="rounded-xl border border-border bg-card p-5 sm:p-6 space-y-4">
    <div className="flex items-center gap-2"><Link2 size={18} className="text-muted-foreground" /><h2 className="font-semibold">{institution || t(connectionId ? 'accountWorkspace.connectedAccount' : 'accountWorkspace.manualAccount')}</h2></div>
    <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">{t(connectionId ? 'accountWorkspace.connectionHelp' : 'accountWorkspace.manualHelp')}</p>
    {children}
    {connectionId && <Button asChild variant="outline"><Link to={`/connections/${connectionId}#connection-health`}>{t('accountWorkspace.manageConnection')}<ChevronRight size={16} /></Link></Button>}
  </section>
}

export function AccountDataStatus({ message, attention = false, connectionId }: { message: string; attention?: boolean; connectionId?: string | null }) {
  const { t } = useTranslation()
  return <div role="status" className={`flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-3 text-sm ${attention ? 'border-amber-500/25 bg-amber-500/5' : 'border-border bg-muted/20'}`}>
    <p className="flex min-w-0 flex-1 items-start gap-2 leading-relaxed">{attention && <CircleAlert size={16} className="mt-0.5 shrink-0 text-amber-600" />}{message}</p>
    {connectionId && <Link className="shrink-0 text-xs font-medium underline underline-offset-4" to={`/connections/${connectionId}#connection-health`}>{t('accountWorkspace.viewConnection')}</Link>}
  </div>
}

export function AccountBalanceSummary({ label, amount, secondary, date }: { label: string; amount: ReactNode; secondary?: ReactNode; date: ReactNode }) {
  return <section className="rounded-xl border border-border bg-card p-5 sm:p-6" aria-label={label}>
    <p className="text-sm text-muted-foreground">{label}</p>
    <p className="mt-2 text-3xl sm:text-4xl font-semibold tracking-tight tabular-nums break-words">{amount}</p>
    {secondary && <p className="mt-1 text-sm text-muted-foreground tabular-nums">{secondary}</p>}
    <p className="mt-3 text-xs text-muted-foreground">{date}</p>
  </section>
}
