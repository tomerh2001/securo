import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { PrimaryNavLink } from './primary-nav-link'
import { navItems, type NavItem } from '@/lib/nav-items'
import { renderWithProviders } from '@/test/utils'
const accounts = navItems.find(item => item.type === 'link' && item.key === 'accounts') as Extract<NavItem, { type: 'link' }>
describe('primary account navigation', () => {
  it.each(['/accounts', '/accounts/bank-one', '/accounts/investments/pension-one', '/connections/clal'])('keeps one Accounts link selected on %s without a nested list', async pathname => {
    const navigate = vi.fn()
    const { user } = renderWithProviders(<PrimaryNavLink item={accounts} pathname={pathname} onNavigate={navigate} />)
    const link = screen.getByRole('link', { name: 'Accounts' })
    expect(screen.getAllByRole('link')).toHaveLength(1)
    expect(link).toHaveAttribute('aria-current', 'page')
    expect(link).not.toHaveAttribute('aria-expanded')
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
    await user.click(link)
    expect(navigate).toHaveBeenCalledOnce()
  })
  it('does not select Accounts on unrelated pages', () => {
    renderWithProviders(<PrimaryNavLink item={accounts} pathname="/assets" onNavigate={vi.fn()} />)
    expect(screen.getByRole('link', { name: 'Accounts' })).not.toHaveAttribute('aria-current')
  })
})
