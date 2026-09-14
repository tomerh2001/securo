import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { ConnectionHealth } from './connection-health'
import { connections } from '@/lib/api'
import { renderWithProviders, createTestQueryClient } from '@/test/utils'
import { connectionFixture, investmentAccountFixture } from '@/test/investment-account-fixtures'
import type { ConnectionOperation, ConnectionSourceStatus } from '@/types'
const access = vi.hoisted(() => ({ canWrite: true }))
vi.mock('@/lib/api', () => ({ connections: { sourceStatus: vi.fn(), operations: vi.fn(), startOperation: vi.fn(), refreshSource: vi.fn(), sync: vi.fn(), submitVerification: vi.fn(), cancelVerification: vi.fn() } }))
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => access }))
vi.mock('@/hooks/use-display-locale', () => ({ useDateLocale: () => 'en-US' }))
const conn = connectionFixture()
function status(): ConnectionSourceStatus {
  return { available: true, manualVerificationAvailable: false, recovery: null, observedAt: new Date().toISOString(), source: { ...investmentAccountFixture().details.source, status: 'auth_required' },
    collection: { running: false, lastResult: 'auth_required', lastStartedAt: '2026-09-08T12:00:00Z', lastFinishedAt: '2026-09-08T12:01:00Z' },
    schedule: { enabled: true, expression: '0 7 * * 1', description: 'Monday at 07:00', timezone: 'Asia/Jerusalem', nextRunAt: '2026-09-14T04:00:00Z' },
    automaticOtp: { enabled: true, ready: true, reason: 'ready', nextAllowedAt: null },
    session: { status: 'auth_required', lastCheckedAt: '2026-09-08T12:00:00Z', lastRenewedAt: null, expiresAt: null, errorCode: 'OTP_REQUIRED', observedAt: new Date().toISOString(), keepAliveEnabled: true, keepAliveMinutes: 5, expired: false, overdue: false, verifiedActive: false },
    import: { lastImportedAt: '2026-09-08T11:00:00Z', checkIntervalMinutes: 60, minimumIntervalMinutes: 240 } }
}
function operation(overrides: Partial<ConnectionOperation> = {}): ConnectionOperation {
  return { id: 'op-1', connection_id: conn.id, kind: 'refresh', status: 'collecting', message_code: null, requested_at: new Date().toISOString(), started_at: new Date().toISOString(), finished_at: null, source_last_success_before: null, source_last_success_after: null, imported_at: null, result: {}, events: [], retry_after_seconds: null, ...overrides }
}
beforeEach(() => {
  vi.clearAllMocks(); access.canWrite = true
  vi.mocked(connections.sourceStatus).mockResolvedValue(status())
  vi.mocked(connections.operations).mockResolvedValue([])
  vi.mocked(connections.startOperation).mockResolvedValue(operation())
})
describe('durable connection recovery', () => {
  it('separates source sign-in, phone verification and saved-data freshness without writing on view or check', async () => {
    const { user } = renderWithProviders(<ConnectionHealth connection={conn} />)
    await screen.findByRole('heading', { name: 'Sign-in verification required' })
    expect(screen.getByRole('heading', { name: 'Provider sign-in' })).toBeInTheDocument()
    expect(screen.getByText('Phone connected')).toBeInTheDocument()
    expect(screen.getByText('Last successful provider update')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Check status' }))
    expect(connections.startOperation).not.toHaveBeenCalled()
    expect(connections.refreshSource).not.toHaveBeenCalled()
    expect(connections.sync).not.toHaveBeenCalled()
  })
  it('starts one server operation and never triggers a second client-side import after completion', async () => {
    const queryClient = createTestQueryClient()
    let rows: ConnectionOperation[] = []
    vi.mocked(connections.operations).mockImplementation(async () => rows)
    const { user } = renderWithProviders(<ConnectionHealth connection={conn} />, { queryClient })
    await screen.findByRole('heading', { name: 'Sign-in verification required' })
    rows = [operation()]
    await user.click(screen.getByRole('button', { name: 'Update from provider' }))
    await waitFor(() => expect(connections.startOperation).toHaveBeenCalledWith(conn.id, 'refresh'))
    expect(await screen.findByRole('heading', { name: 'Getting data from the provider' })).toBeInTheDocument()
    rows = [operation({ status: 'succeeded', finished_at: new Date().toISOString(), result: { valuations_added: 3, activities_added: 2 } })]
    await queryClient.invalidateQueries({ queryKey: ['connection-operations', conn.id] })
    expect(await screen.findByText('3 balances added')).toBeInTheDocument()
    expect(connections.startOperation).toHaveBeenCalledTimes(1)
    expect(connections.sync).not.toHaveBeenCalled()
  })
  it('restores an in-progress operation on a newly mounted page without requesting another update', async () => {
    vi.mocked(connections.operations).mockResolvedValue([operation({ status: 'importing' })])
    renderWithProviders(<ConnectionHealth connection={conn} />)
    await screen.findByRole('heading', { name: 'Saving data in Securo' })
    expect(screen.getByRole('button', { name: 'Updating…' })).toBeDisabled()
    expect(connections.startOperation).not.toHaveBeenCalled()
    expect(connections.sync).not.toHaveBeenCalled()
  })
  it('shows the pairing failure and blocks provider retries that cannot repair the receiver', async () => {
    const data = status(); data.automaticOtp.ready = false; data.automaticOtp.reason = 'reauth_required'
    vi.mocked(connections.sourceStatus).mockResolvedValue(data)
    renderWithProviders(<ConnectionHealth connection={conn} />)
    await screen.findByText('Pairing needs attention')
    expect(screen.getAllByText(/Google Messages connection needs to be paired again/).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Update from provider' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Import saved updates' })).toBeEnabled()
  })
  it('does not turn an unverified session into a connected claim just because saved data is current', async () => {
    const data = status(); data.source.status = 'ok'; data.session.status = 'unknown'; data.session.errorCode = null
    vi.mocked(connections.sourceStatus).mockResolvedValue(data)
    renderWithProviders(<ConnectionHealth connection={conn} />)
    expect(await screen.findByText('Sign-in has not been verified')).toBeInTheDocument()
    expect(screen.queryByText('Sign-in verified')).not.toBeInTheDocument()
  })
  it('keeps the full read-only status accessible to workspace readers', async () => {
    access.canWrite = false
    renderWithProviders(<ConnectionHealth connection={conn} />)
    await screen.findByRole('heading', { name: 'Sign-in verification required' })
    expect(screen.getByRole('button', { name: 'Check status' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Update from provider' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Import saved updates' })).not.toBeInTheDocument()
  })
  it('reserves reconnect for expired Securo access, separate from provider SMS verification', async () => {
    const reconnect = vi.fn()
    vi.mocked(connections.sourceStatus).mockRejectedValue({ isAxiosError: true, response: { status: 409, data: { detail: { code: 'source_access_expired' } } } })
    const { user } = renderWithProviders(<ConnectionHealth connection={conn} onReconnect={reconnect} />)
    await screen.findByRole('heading', { name: 'Securo access needs reconnecting' })
    await user.click(screen.getByRole('button', { name: 'Reconnect' }))
    expect(reconnect).toHaveBeenCalledOnce()
    expect(connections.startOperation).not.toHaveBeenCalled()
  })
  it('explains cached-only connections and records imports without calling unsupported source controls', async () => {
    const { user } = renderWithProviders(<ConnectionHealth connection={conn} supportsSourceRefresh={false} />)
    await screen.findByRole('heading', { name: 'Recent update attempts' })
    expect(connections.sourceStatus).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Update from provider' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Import saved updates' }))
    expect(connections.startOperation).toHaveBeenCalledWith(conn.id, 'import')
  })
  it('makes zero added records explicit and exposes saved failure steps', async () => {
    vi.mocked(connections.operations).mockResolvedValue([operation({ status: 'partial', finished_at: new Date().toISOString(), message_code: 'source_partial', result: { valuations_added: 0 }, events: [{ at: new Date().toISOString(), stage: 'partial', code: 'INCOMPLETE_RESPONSE' }] })])
    const { user } = renderWithProviders(<ConnectionHealth connection={conn} />)
    const history = await screen.findByRole('region', { name: 'Recent update attempts' })
    expect(within(history).getByText(/No additional historical records were added/)).toBeInTheDocument()
    await user.click(within(history).getByText('View update steps'))
    expect(within(history).getAllByText(/The provider returned only part/)).toHaveLength(2)
  })
  it('requests an SMS only after an explicit action, then submits and clears the code', async () => {
    const data = status(); data.manualVerificationAvailable = true; data.automaticOtp.ready = false; data.automaticOtp.reason = 'reauth_required'
    vi.mocked(connections.sourceStatus).mockImplementation(async () => structuredClone(data))
    const recover = operation({ kind: 'recover', status: 'awaiting_verification' })
    vi.mocked(connections.startOperation).mockResolvedValue(recover)
    vi.mocked(connections.submitVerification).mockResolvedValue(recover)
    const queryClient = createTestQueryClient()
    const { user } = renderWithProviders(<ConnectionHealth connection={conn} />, { queryClient })
    await screen.findByRole('button', { name: 'Sign in with a text message' })
    expect(connections.startOperation).not.toHaveBeenCalled()
    data.recovery = { challengeId: 'op-1', state: 'awaiting_code', expiresAt: new Date(Date.now() + 180000).toISOString(), errorCode: null }
    vi.mocked(connections.operations).mockResolvedValue([recover])
    await user.click(screen.getByRole('button', { name: 'Sign in with a text message' }))
    expect(connections.startOperation).toHaveBeenCalledWith(conn.id, 'recover')
    const input = await screen.findByLabelText('Six-digit verification code')
    expect(screen.getByRole('button', { name: 'Verify and continue' })).toBeDisabled()
    await user.type(input, '123456')
    await user.click(screen.getByRole('button', { name: 'Verify and continue' }))
    await waitFor(() => expect(connections.submitVerification).toHaveBeenCalledWith(conn.id, recover.id, '123456'))
    expect(input).toHaveValue('')
    expect(localStorage.getItem('verificationCode')).toBeNull()
  })
  it('restores an awaiting code challenge on return and lets the user cancel it', async () => {
    const data = status(); data.manualVerificationAvailable = true
    data.recovery = { challengeId: 'op-1', state: 'awaiting_code', expiresAt: new Date(Date.now() + 180000).toISOString(), errorCode: null }
    vi.mocked(connections.sourceStatus).mockResolvedValue(data)
    const recover = operation({ kind: 'recover', status: 'awaiting_verification' })
    vi.mocked(connections.operations).mockResolvedValue([recover])
    vi.mocked(connections.cancelVerification).mockResolvedValue(operation({ kind: 'recover', status: 'failed' }))
    const { user } = renderWithProviders(<ConnectionHealth connection={conn} />)
    await screen.findByLabelText('Six-digit verification code')
    expect(connections.startOperation).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(connections.cancelVerification).toHaveBeenCalledWith(conn.id, recover.id)
  })

  it('does not submit or cancel a challenge belonging to a different operation', async () => {
    const data = status(); data.manualVerificationAvailable = true
    data.recovery = { challengeId: 'another-operation', state: 'awaiting_code', expiresAt: new Date(Date.now() + 180000).toISOString(), errorCode: null }
    vi.mocked(connections.sourceStatus).mockResolvedValue(data)
    vi.mocked(connections.operations).mockResolvedValue([operation({ kind: 'recover', status: 'awaiting_verification' })])
    renderWithProviders(<ConnectionHealth connection={conn} />)
    await screen.findByRole('heading', { name: 'Waiting for sign-in verification' })
    expect(screen.queryByLabelText('Six-digit verification code')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Cancel/ })).not.toBeInTheDocument()
  })
  it('honors the persisted worker cooldown after an accepted request fails with a retry limit', async () => {
    vi.mocked(connections.operations).mockResolvedValue([operation({ status: 'failed', finished_at: new Date().toISOString(), retry_after_seconds: 120 })])
    renderWithProviders(<ConnectionHealth connection={conn} />)
    await screen.findByText(/To avoid repeated sign-in attempts/)
    expect(screen.getByRole('button', { name: 'Update from provider' })).toBeDisabled()
    expect(connections.startOperation).not.toHaveBeenCalled()
  })
  it('shows cancellation errors while a matching sign-in is starting', async () => {
    const data = status(); data.manualVerificationAvailable = true
    data.recovery = { challengeId: 'op-1', state: 'starting', expiresAt: null, errorCode: null }
    vi.mocked(connections.sourceStatus).mockResolvedValue(data)
    vi.mocked(connections.operations).mockResolvedValue([operation({ kind: 'recover', status: 'collecting' })])
    vi.mocked(connections.cancelVerification).mockRejectedValue(new Error('unavailable'))
    const { user } = renderWithProviders(<ConnectionHealth connection={conn} />)
    await user.click(await screen.findByRole('button', { name: 'Cancel sign-in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The sign-in request could not be canceled')
  })

})
