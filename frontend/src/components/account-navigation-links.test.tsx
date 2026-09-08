import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { AccountNavigationLinks } from '@/components/account-navigation-links'
import { renderWithProviders, t } from '@/test/utils'
import { bankAccountFixture, connectionFixture, investmentAccountFixture } from '@/test/investment-account-fixtures'

describe('account navigation hierarchy', () => {
  it('presents bank and investment providers as peers and selects the provider of an open account', async () => {
    const onNavigate = vi.fn()
    const { user } = renderWithProviders(<AccountNavigationLinks
      accounts={[bankAccountFixture()]}
      investments={[investmentAccountFixture()]}
      connections={[connectionFixture({ id: 'bank-connection', institution_name: 'Example Bank', provider: 'simplefin' }), connectionFixture()]}
      pathname="/accounts/bank-account" hash="" onNavigate={onNavigate}
    />)
    const bank = screen.getByRole('link', { name: /Example Bank/ })
    const investment = screen.getByRole('link', { name: /Clal/ })
    expect(bank).toHaveAttribute('href', '/connections/bank-connection')
    expect(bank).toHaveAttribute('aria-current', 'page')
    expect(investment).toHaveAttribute('href', '/connections/clal-connection')
    expect(investment).not.toHaveAttribute('aria-current')
    expect(screen.queryByText(/500|10,000/)).not.toBeInTheDocument()
    await user.click(investment)
    expect(onNavigate).toHaveBeenCalledOnce()
  })

  it('keeps manual and disconnected accounts in the same hierarchy without inventing providers', () => {
    renderWithProviders(<AccountNavigationLinks
      accounts={[bankAccountFixture({ connection_id: null })]}
      investments={[investmentAccountFixture({ connection_id: null })]}
      connections={[]} pathname="/accounts/investments/pension-account" hash="" onNavigate={vi.fn()}
    />)
    expect(screen.getByRole('link', { name: new RegExp(t('accounts.manualAccounts')) })).toHaveAttribute('href', '/accounts#manual-accounts')
    expect(screen.getByRole('link', { name: new RegExp(t('investmentAccounts.title')) })).toHaveAttribute('href', '/accounts#investment-accounts')
    expect(screen.getByRole('link', { current: 'page' })).toHaveAttribute('href', '/accounts#investment-accounts')
    expect(screen.queryByRole('link', { name: /Clal/ })).not.toBeInTheDocument()
  })
})
