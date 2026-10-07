import { act, renderHook } from '@testing-library/react'
import { expect, test } from 'vitest'
import { workbenchStore } from '../../state/workbenchStore'
import { usePreviewSource } from './usePreviewSource'

test('marks saved host evidence stale when the source fingerprint changes', () => {
  act(() => {
    workbenchStore.setState({
      sourceFingerprint: 'sha256:new-source',
      draftParameters: {},
      hostVerificationParamsKey: '{}',
      hostVerification: {
        record_id: 'hv_old',
        status: 'passed',
        source_fingerprint: 'sha256:old-source',
        parameter_fingerprint: 'sha256:params',
        requested_parameters: {},
        applied_parameters: [],
        skipped_parameters: [],
      },
    })
  })

  const { result } = renderHook(() => usePreviewSource(null))

  expect(result.current.sourceControl.verificationStatus).toBe('stale')
})

test('shows host parameter readback gaps and requested-to-effective differences', () => {
  act(() => {
    workbenchStore.setState({
      sourceFingerprint: 'sha256:source',
      draftParameters: {},
      hostVerificationParamsKey: '{}',
      hostVerification: {
        record_id: 'hv_diff',
        status: 'failed',
        source_fingerprint: 'sha256:source',
        parameter_fingerprint: 'sha256:params',
        requested_parameters: { HEIGHT: 2.9 },
        effective_parameters: { HEIGHT: 2.8 },
        parameter_readback_status: 'verified',
        parameter_differences: { HEIGHT: { requested: 2.9, effective: 2.8 } },
        applied_parameters: ['HEIGHT'],
        skipped_parameters: [],
      },
    })
  })

  const { result } = renderHook(() => usePreviewSource(null))

  expect(result.current.sourceControl.verificationParametersSummary).toContain('HEIGHT 2.9 → 2.8')

  act(() => {
    workbenchStore.setState({
      hostVerification: {
        ...workbenchStore.getState().hostVerification!,
        parameter_readback_status: 'unavailable',
      },
    })
  })
  expect(result.current.sourceControl.verificationParametersSummary).toBe('宿主未回读有效参数')
})
