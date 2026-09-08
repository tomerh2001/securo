import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { getConnectionName } from '@/lib/connection-utils'
import { cn } from '@/lib/utils'
import type { Account, BankConnection, InvestmentAccount } from '@/types'

interface AccountNavigationLinksProps {
  accounts: Account[]
  investments: InvestmentAccount[]
  connections: BankConnection[]
  pathname: string
  hash: string
  onNavigate: () => void
}

/** Provider shortcuts share one hierarchy for every kind of account. */
export function AccountNavigationLinks({ accounts, investments, connections, pathname, hash, onNavigate }: AccountNavigationLinksProps) {
  const { t } = useTranslation()
  const groups = connections.map(connection => {
    const bankAccounts = accounts.filter(account => account.connection_id === connection.id)
    const investmentAccounts = investments.filter(account => account.connection_id === connection.id)
    return {
      id: connection.id,
      name: getConnectionName(connection, t),
      href: `/connections/${connection.id}`,
      count: bankAccounts.length + investmentAccounts.length,
      active: pathname === `/connections/${connection.id}`
        || bankAccounts.some(account => pathname === `/accounts/${account.id}`)
        || investmentAccounts.some(account => pathname === `/accounts/investments/${account.id}`),
    }
  })
  const manualAccounts = accounts.filter(account => account.connection_id === null)
  const standaloneInvestments = investments.filter(account => account.connection_id === null)
  if (manualAccounts.length) groups.push({
    id: 'manual', name: t('accounts.manualAccounts'), href: '/accounts#manual-accounts', count: manualAccounts.length,
    active: (pathname === '/accounts' && hash === '#manual-accounts') || manualAccounts.some(account => pathname === `/accounts/${account.id}`),
  })
  if (standaloneInvestments.length) groups.push({
    id: 'investments', name: t('investmentAccounts.title'), href: '/accounts#investment-accounts', count: standaloneInvestments.length,
    active: (pathname === '/accounts' && hash === '#investment-accounts') || standaloneInvestments.some(account => pathname === `/accounts/investments/${account.id}`),
  })
  if (!groups.length) return null
  return <div className="ml-8 mt-1 space-y-0.5 border-l border-sidebar-border pl-1">
    {groups.map(group => <Link key={group.id} to={group.href} onClick={onNavigate}
      aria-current={group.active ? 'page' : undefined}
      className={cn('flex min-w-0 items-center justify-between gap-2 rounded-lg px-3 py-2 text-xs transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground', group.active ? 'bg-sidebar-accent text-sidebar-foreground' : 'text-sidebar-muted')}>
      <span className="line-clamp-2 min-w-0 break-words font-medium leading-snug" dir="auto">{group.name}</span>
      <span aria-hidden="true" className="flex h-5 min-w-5 shrink-0 items-center justify-center rounded bg-sidebar-accent px-1 text-[10px] tabular-nums">{group.count}</span>
      <span className="sr-only">{t('investmentAccounts.accountCount', { count: group.count, defaultValue: '{{count}} accounts' })}</span>
    </Link>)}
  </div>
}
