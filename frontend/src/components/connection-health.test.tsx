import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { ConnectionHealth } from './connection-health'
import { connections } from '@/lib/api'
import { renderWithProviders, createTestQueryClient } from '@/test/utils'
import { connectionFixture, investmentAccountFixture } from '@/test/investment-account-fixtures'
import type { ConnectionSourceStatus } from '@/types'
const access = vi.hoisted(() => ({ canWrite: true }))
vi.mock('@/lib/api', () => ({ connections: { sourceStatus: vi.fn(), refreshSource: vi.fn(), sync: vi.fn() } }))
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => access }))
vi.mock('@/hooks/use-display-locale', () => ({ useDateLocale: () => 'en-US' }))
const conn = connectionFixture()
function status(): ConnectionSourceStatus {
  return { available: true, observedAt: new Date().toISOString(), source: { ...investmentAccountFixture().details.source, status: 'auth_required' },
    collection: { running: false, lastResult: 'auth_required', lastStartedAt: '2026-09-08T12:00:00Z', lastFinishedAt: '2026-09-08T12:01:00Z' },
    schedule: { enabled: true, expression: '0 7 * * 1', description: 'Monday at 07:00', timezone: 'Asia/Jerusalem', nextRunAt: '2026-09-14T04:00:00Z' },
    automaticOtp: { enabled: true, ready: true, reason: 'ready', nextAllowedAt: null },
    import: { lastImportedAt: '2026-09-08T11:00:00Z', checkIntervalMinutes: 60, minimumIntervalMinutes: 240 } }
}
beforeEach(() => {
  vi.clearAllMocks(); access.canWrite = true
  vi.mocked(connections.sourceStatus).mockResolvedValue(status())
  vi.mocked(connections.refreshSource).mockResolvedValue({ result: 'started', retryAfterSeconds: 0 })
  vi.mocked(connections.sync).mockResolvedValue(conn)
})
describe('connection recovery', () => {
  it('explains recovery and separates collection from import; viewing and checking status never write', async () => {
    const { user } = renderWithProviders(<ConnectionHealth connection={conn} />)
    await screen.findByText('Sign-in verification required')
    expect(screen.getByText(/read from your paired Google Messages phone automatically/)).toBeInTheDocument()
    expect(screen.getByText('Last successful provider update')).toBeInTheDocument()
    expect(screen.getByText(/This time does not mean the provider supplied new data/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Check status' }))
    expect(connections.refreshSource).not.toHaveBeenCalled(); expect(connections.sync).not.toHaveBeenCalled()
  })
  it.each(['ok', 'auth_required', 'skipped'] as const)('imports only after a requested collection succeeds (%s)', async result => {
    const data = status(); const queryClient = createTestQueryClient()
    vi.mocked(connections.sourceStatus).mockImplementation(async () => structuredClone(data))
    const { user } = renderWithProviders(<ConnectionHealth connection={conn} />, { queryClient })
    await screen.findByText('Sign-in verification required')
    data.collection.running = true
    await user.click(screen.getByRole('button', { name: 'Try connection again' }))
    await waitFor(() => expect(connections.refreshSource).toHaveBeenCalledWith(conn.id))
    expect(connections.sync).not.toHaveBeenCalled()
    data.collection.running = false; data.collection.lastResult = result
    data.collection.lastFinishedAt = new Date(Date.now() + 100).toISOString()
    data.source.status = result === 'skipped' ? 'ok' : result; data.source.lastSuccessAt = new Date().toISOString()
    await queryClient.invalidateQueries({ queryKey: ['connection-source', conn.id] })
    if (result === 'ok') {
      await waitFor(() => expect(connections.sync).toHaveBeenCalledTimes(1))
      await queryClient.invalidateQueries({ queryKey: ['connection-source', conn.id] })
      expect(connections.sync).toHaveBeenCalledTimes(1)
    } else {
      await screen.findByText(result === 'skipped' ? 'The update could not start' : 'Sign-in verification required')
      expect(connections.sync).not.toHaveBeenCalled(); expect(screen.queryByText('Provider data is up to date')).not.toBeInTheDocument()
    }
  })
  it('keeps newer successful saved data read-only until the user initiates an action', async () => {
    const data = status(); data.source.status = 'ok'; vi.mocked(connections.sourceStatus).mockResolvedValue(data)
    renderWithProviders(<ConnectionHealth connection={conn} />)
    await screen.findByText('Provider data is up to date'); expect(connections.sync).not.toHaveBeenCalled()
  })
  it('explains offline phone recovery and restricts mutation actions to editors', async () => {
    access.canWrite = false
    const data = status(); data.automaticOtp.ready = false; data.automaticOtp.reason = 'phone_unavailable'
    data.automaticOtp.nextAllowedAt = null
    vi.mocked(connections.sourceStatus).mockResolvedValue(data)
    renderWithProviders(<ConnectionHealth connection={conn} />)
    await screen.findByText('Sign-in verification required')
    expect(screen.getAllByText(/Open Google Messages on your paired phone/).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: 'Try connection again' })).not.toBeInTheDocument()
    expect(screen.getByText('A workspace owner or editor can retry this connection.')).toBeInTheDocument()
  })
  it('shows unavailable controls without making a provider login claim', async () => {
    vi.mocked(connections.sourceStatus).mockRejectedValue(new Error('offline'))
    renderWithProviders(<ConnectionHealth connection={conn} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Connection health is unavailable')
    expect(connections.refreshSource).not.toHaveBeenCalled()
  })
  it('offers the native reconnect flow for an expired feed access token', async () => {
    const reconnect = vi.fn()
    vi.mocked(connections.sourceStatus).mockRejectedValue({ isAxiosError: true, response: { status: 409, data: { detail: { code: 'source_access_expired' } } } })
    const { user } = renderWithProviders(<ConnectionHealth connection={conn} onReconnect={reconnect} />)
    await screen.findByText('Securo access needs reconnecting')
    await user.click(screen.getByRole('button', { name: 'Reconnect' }))
    expect(reconnect).toHaveBeenCalledOnce()
    expect(connections.refreshSource).not.toHaveBeenCalled()
  })
  it('explains the retry limit without asking the user to fix their phone', async () => {
    const data = status(); data.automaticOtp.ready = false; data.automaticOtp.reason = 'rate_limited'
    data.automaticOtp.nextAllowedAt = new Date(Date.now() + 60_000).toISOString()
    vi.mocked(connections.sourceStatus).mockResolvedValue(data)
    renderWithProviders(<ConnectionHealth connection={conn} />)
    await screen.findByText('Sign-in verification required')
    expect(screen.getByText(/The sign-in retry limit has been reached/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try connection again' })).toBeDisabled()
    expect(screen.queryByText(/Open Google Messages on your paired phone/)).not.toBeInTheDocument()
  })

})
