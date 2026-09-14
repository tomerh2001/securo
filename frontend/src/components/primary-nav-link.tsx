import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import type { NavItem } from '@/lib/nav-items'

/** Every primary destination has the same single-link behavior and appearance. */
export function PrimaryNavLink({ item, pathname, onNavigate }: { item: Extract<NavItem, { type: 'link' }>; pathname: string; onNavigate: () => void }) {
  const { t } = useTranslation()
  const active = item.path === '/' ? pathname === '/' : pathname === item.path || pathname.startsWith(`${item.path}/`) || (item.path === '/accounts' && pathname.startsWith('/connections/'))
  const Icon = item.icon
  return <Link to={item.path} data-tour={`nav-${item.key}`} onClick={onNavigate} aria-current={active ? 'page' : undefined}
    className={cn('flex items-center gap-3 text-[13px] font-medium transition-all rounded-lg px-3 py-2', active ? 'bg-primary/[0.08] text-primary border-l-[3px] border-primary pl-[9px]' : 'text-sidebar-muted hover:bg-sidebar-accent hover:text-sidebar-foreground')}>
    <Icon size={17} className={cn('shrink-0', active ? 'text-primary' : 'text-sidebar-muted')} />
    <span>{t(`nav.${item.key}`)}</span>
  </Link>
}
