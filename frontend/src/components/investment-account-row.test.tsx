import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { InvestmentAccountRow } from '@/components/investment-account-row'
import { investmentAccountFixture } from '@/test/investment-account-fixtures'
import { renderWithProviders } from '@/test/utils'

vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))

const account = investmentAccountFixture({ name: 'Clal Study Fund ··5678', display_name: 'Clal Study Fund ··5678', masked_number: '5678', product_kind: 'keren_hishtalmut' })

describe('InvestmentAccountRow names', () => {
  it('identifies the chosen account and shows its ending once', () => {
    renderWithProviders(<InvestmentAccountRow account={account} />)
    const link = screen.getByRole('link')
    expect(link).toHaveTextContent('Clal Study Fund')
    expect(link).toHaveAttribute('href', `/accounts/investments/${account.id}`)
    expect(link.textContent?.match(/5678/g)).toHaveLength(1)
  })

  it('keeps the chosen name while hiding its ending in privacy mode', () => {
    localStorage.setItem('privacyMode', 'true')
    renderWithProviders(<InvestmentAccountRow account={account} />)
    expect(screen.getByRole('link')).toHaveTextContent('Clal Study Fund')
    expect(screen.queryByText(/5678/)).not.toBeInTheDocument()
  })
})
