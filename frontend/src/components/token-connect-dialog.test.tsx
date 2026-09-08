import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { TokenConnectDialog } from '@/components/token-connect-dialog'
import { connections } from '@/lib/api'
import { renderWithProviders } from '@/test/utils'

vi.mock('@/lib/api', () => ({ connections: { handleCallback: vi.fn() } }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

beforeEach(() => vi.resetAllMocks())

describe('Clal token connection', () => {
  it('connects investment products with a collector token and refreshes the asset views', async () => {
    const onClose = vi.fn()
    const { user, queryClient } = renderWithProviders(<TokenConnectDialog open onClose={onClose} provider="investment_feed" supportsAssetSync />)
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    expect(screen.getByRole('heading', { name: 'Connect Clal investments' })).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Connect' })).toBeDisabled()
    await user.type(screen.getByLabelText('Clal connection token'), 'example-collector-token')
    await user.click(screen.getByRole('button', { name: 'Connect' }))
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce())
    expect(connections.handleCallback).toHaveBeenCalledWith('example-collector-token', 'investment_feed', undefined, { sync_assets: true }, undefined)
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['assets'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['asset-activities'] })
  })

  it('reconnects the existing connection without creating a separate account tree', async () => {
    const { user } = renderWithProviders(<TokenConnectDialog open onClose={vi.fn()} provider="investment_feed" supportsAssetSync reconnectConnectionId="connection-example" />)
    await user.type(screen.getByLabelText('Clal connection token'), 'replacement-example-token')
    await user.click(screen.getByRole('button', { name: 'Reconnect' }))
    await waitFor(() => expect(connections.handleCallback).toHaveBeenCalledWith('replacement-example-token', 'investment_feed', undefined, undefined, 'connection-example'))
  })
})
