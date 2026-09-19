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
