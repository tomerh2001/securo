import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Pencil } from 'lucide-react'
import { assets } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { useWorkspace } from '@/contexts/workspace-context'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import type { InvestmentAccount } from '@/types'

export function InvestmentAccountRename({ account }: { account: InvestmentAccount }) {
  const { t } = useTranslation()
  const { canWrite } = useWorkspace()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const rename = useMutation({
    mutationFn: (displayName: string | null) => assets.update(account.id, { display_name: displayName }),
    onSuccess: (updated) => {
      queryClient.setQueryData<InvestmentAccount>(['investment-account', account.id], current => current && {
        ...current, name: updated.name, display_name: updated.display_name,
      })
      for (const key of ['investment-account', 'investment-accounts', 'assets', 'portfolio-trend', 'search', 'goals', 'reports', 'asset-transactions', 'investment-account-activities', 'asset-activities']) {
        void queryClient.invalidateQueries({ queryKey: [key] })
      }
      setOpen(false)
    },
  })
  if (!canWrite) return null

  function changeOpen(next: boolean) {
    if (rename.isPending) return
    if (next) {
      setName(account.display_name ?? account.name)
      rename.reset()
    }
    setOpen(next)
  }

  return <Dialog open={open} onOpenChange={changeOpen}>
      <DialogTrigger asChild><Button variant="outline"><Pencil size={16} />{t('investmentAccounts.rename')}</Button></DialogTrigger>
      <DialogContent showCloseButton={!rename.isPending}>
        <DialogHeader>
          <DialogTitle>{t('investmentAccounts.rename')}</DialogTitle>
          <DialogDescription>{t('investmentAccounts.renameHint')}</DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={event => {
          event.preventDefault()
          if (canWrite && name.trim() && !rename.isPending) rename.mutate(name.trim())
        }}>
          <div className="space-y-2">
            <Label htmlFor="investment-account-name">{t('common.name')}</Label>
            <Input id="investment-account-name" value={name} onChange={event => setName(event.target.value)} maxLength={255} required disabled={rename.isPending} autoComplete="off" dir="auto" />
          </div>
          {rename.isError && <p role="alert" className="text-sm text-destructive">{extractApiError(rename.error, t('investmentAccounts.renameError'))}</p>}
          {account.display_name && <Button type="button" variant="ghost" size="sm" disabled={rename.isPending} onClick={() => rename.mutate(null)}>{t('investmentAccounts.useProviderName')}</Button>}
          <DialogFooter>
            <Button type="button" variant="outline" disabled={rename.isPending} onClick={() => changeOpen(false)}>{t('common.cancel')}</Button>
            <Button type="submit" disabled={!name.trim() || rename.isPending}>{t(rename.isPending ? 'common.saving' : 'common.save')}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
}
