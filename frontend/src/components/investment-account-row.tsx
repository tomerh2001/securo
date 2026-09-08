import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronRight, Landmark, Sprout, TrendingUp } from 'lucide-react'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatCurrency } from '@/lib/format'
import type { InvestmentAccount } from '@/types'

export function InvestmentAccountRow({ account }: { account: InvestmentAccount }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const Icon = account.product_kind === 'pension' ? Landmark : account.product_kind === 'keren_hishtalmut' ? Sprout : TrendingUp
  const asOf = account.details.valuation_date
  return (
    <Link
      to={`/accounts/investments/${account.id}`}
      className="group flex items-center gap-3 px-4 py-4 sm:px-5 transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
    >
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground"><Icon size={18} /></span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium leading-snug break-words" dir="auto">{t(`investments.productKinds.${account.product_kind}`)}</span>
        {account.masked_number ? <span className="mt-1 block text-xs text-muted-foreground" dir="ltr">{mask(`•• ${account.masked_number}`)}</span> : <span className="mt-1 block line-clamp-2 text-xs text-muted-foreground" dir="auto">{account.name}</span>}
      </span>
      <span className="shrink-0 text-right">
        <span className="block text-sm font-semibold tabular-nums">
          {account.balance === null ? t('investmentAccounts.valueUnavailable', { defaultValue: 'Not available' }) : mask(formatCurrency(account.balance, account.currency, locale))}
        </span>
        {asOf && <span className="mt-1 block text-[11px] text-muted-foreground">{t('investmentAccounts.asOf', { defaultValue: 'As of {{date}}', date: new Date(`${asOf}T12:00:00`).toLocaleDateString(dateLocale, { day: 'numeric', month: 'short', year: 'numeric' }) })}</span>}
      </span>
      <ChevronRight size={15} className="hidden shrink-0 text-muted-foreground sm:block" />
    </Link>
  )
}
