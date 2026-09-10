import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { TokenConnectDialog } from '@/components/token-connect-dialog'
import { connections } from '@/lib/api'
import { renderWithProviders } from '@/test/utils'

vi.mock('@/lib/api', () => ({ connections: { handleCallback: vi.fn() } }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

beforeEach(() => vi.resetAllMocks())

describe('Investment token connection', () => {
  it('connects investment products with a collector token and refreshes the asset views', async () => {
    const onClose = vi.fn()
    const { user, queryClient } = renderWithProviders(<TokenConnectDialog open onClose={onClose} provider="investment_feed" supportsAssetSync />)
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    expect(screen.getByRole('heading', { name: 'Connect investment accounts' })).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Connect' })).toBeDisabled()
    await user.type(screen.getByLabelText('Connection token'), 'example-collector-token')
    await user.click(screen.getByRole('button', { name: 'Connect' }))
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce())
    expect(connections.handleCallback).toHaveBeenCalledWith('example-collector-token', 'investment_feed', undefined, { sync_assets: true }, undefined)
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['assets'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['asset-activities'] })
  })

  it('reconnects the existing connection without creating a separate account tree', async () => {
    const { user } = renderWithProviders(<TokenConnectDialog open onClose={vi.fn()} provider="investment_feed" supportsAssetSync reconnectConnectionId="connection-example" />)
    await user.type(screen.getByLabelText('Connection token'), 'replacement-example-token')
    await user.click(screen.getByRole('button', { name: 'Reconnect' }))
    await waitFor(() => expect(connections.handleCallback).toHaveBeenCalledWith('replacement-example-token', 'investment_feed', undefined, undefined, 'connection-example'))
  })

  it.each([
    { connectionId: undefined, button: 'Connect', title: 'Connect Hachshara Best Invest' },
    { connectionId: 'best-invest-connection', button: 'Reconnect', title: 'Reconnect Hachshara Best Invest' },
  ])('passes the complete Best Invest token unchanged for $button', async ({ connectionId, button, title }) => {
    const onClose = vi.fn()
    const { user } = renderWithProviders(
      <TokenConnectDialog open onClose={onClose} provider="investment_feed" supportsAssetSync reconnectConnectionId={connectionId} />,
    )
    const input = screen.getByLabelText('Connection token')
    const token = 'best-invest.example.collector_token'
    await user.type(input, token)
    expect(screen.getByRole('heading', { name: title })).toBeInTheDocument()
    expect(screen.getByText('Keep the best-invest. prefix. Use a separate connection for your Clal accounts.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: button }))
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce())
    expect(connections.handleCallback).toHaveBeenCalledWith(
      token, 'investment_feed', undefined,
      connectionId ? undefined : { sync_assets: true },
      connectionId,
    )
  })

  it('returns to the generic investment wording when a Best Invest token is removed', async () => {
    const { user } = renderWithProviders(<TokenConnectDialog open onClose={vi.fn()} provider="investment_feed" supportsAssetSync />)
    const input = screen.getByLabelText('Connection token')
    await user.type(input, 'best-invest.example-token')
    expect(screen.getByRole('heading', { name: 'Connect Hachshara Best Invest' })).toBeInTheDocument()
    await user.clear(input)
    expect(screen.getByRole('heading', { name: 'Connect investment accounts' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Connect' })).toBeDisabled()
  })

  it.each([
    { connectionId: undefined, button: 'Connect', title: 'Connect Hapoalim Investments' },
    { connectionId: 'hapoalim-connection', button: 'Reconnect', title: 'Reconnect Hapoalim Investments' },
  ])('preserves the Hapoalim source prefix for $button', async ({ connectionId, button, title }) => {
    const onClose = vi.fn()
    const { user } = renderWithProviders(
      <TokenConnectDialog open onClose={onClose} provider="investment_feed" supportsAssetSync reconnectConnectionId={connectionId} />,
    )
    const token = 'hapoalim.example.collector_token'
    await user.type(screen.getByLabelText('Connection token'), token)
    expect(screen.getByRole('heading', { name: title })).toBeInTheDocument()
    expect(screen.getByText('Keep the hapoalim. prefix. Use a separate connection for your other investment providers.')).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: button }))
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce())
    expect(connections.handleCallback).toHaveBeenCalledWith(
      token, 'investment_feed', undefined,
      connectionId ? undefined : { sync_assets: true }, connectionId,
    )
  })

  it('updates source wording when switching between Hapoalim and another collector', async () => {
    const { user } = renderWithProviders(<TokenConnectDialog open onClose={vi.fn()} provider="investment_feed" />)
    const input = screen.getByLabelText('Connection token')
    await user.type(input, 'hapoalim.example-token')
    expect(screen.getByRole('heading', { name: 'Connect Hapoalim Investments' })).toBeInTheDocument()
    await user.clear(input)
    await user.type(input, 'best-invest.example-token')
    expect(screen.getByRole('heading', { name: 'Connect Hachshara Best Invest' })).toBeInTheDocument()
    await user.clear(input)
    expect(screen.getByRole('heading', { name: 'Connect investment accounts' })).toBeInTheDocument()
  })

})
