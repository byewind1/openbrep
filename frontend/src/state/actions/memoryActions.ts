import type { DistillLessonsResult } from '../../api/types'
import type { WorkbenchActionContext } from '../workbenchStoreTypes'

export function createMemoryActions({ api, set }: WorkbenchActionContext) {
  return {
    async loadMemoryStatus() {
      const result = await api.fetchMemoryStatus()
      if (!result.ok) {
        if (result.error) {
          set({ lastError: result.error })
        }
        return
      }
      set({ memoryStatus: result.memory ?? null })
    },

    async loadMemoryLessons() {
      const result = await api.fetchMemoryLessons()
      if (!result.ok) {
        set({ lastError: result.error ?? 'Failed to load project memory lessons.' })
        return
      }
      set({ memoryLessons: result.lessons })
    },

    async summarizeProjectMemory() {
      set({ memoryBusy: true, lastError: null })
      const result = await api.summarizeProjectMemory()
      if (!result.ok) {
        set({ memoryBusy: false, lastError: result.error ?? 'Failed to summarize project memory.' })
        return
      }
      const [status, lessons] = await Promise.all([
        api.fetchMemoryStatus(),
        api.fetchMemoryLessons(),
      ])
      set((state) => ({
        memoryBusy: false,
        memoryStatus: status.ok ? status.memory ?? null : state.memoryStatus,
        memoryLessons: lessons.ok ? lessons.lessons : state.memoryLessons,
        memorySkillPreview: result.skill ?? '',
        compileLog: [
          result.summary?.message ?? 'Summarized project memory',
          ...state.compileLog,
        ].slice(0, 20),
        lastError: status.ok && lessons.ok ? null : status.error ?? lessons.error ?? state.lastError,
      }))
    },

    async deleteMemoryLesson(fingerprint: string) {
      const cleaned = fingerprint.trim()
      if (!cleaned) {
        set({ lastError: 'Lesson fingerprint is required.' })
        return
      }
      const result = await api.deleteMemoryLesson(cleaned)
      if (!result.ok) {
        set({ lastError: result.error ?? 'Failed to delete project memory lesson.' })
        return
      }
      const [status, lessons] = await Promise.all([
        api.fetchMemoryStatus(),
        api.fetchMemoryLessons(),
      ])
      set((state) => ({
        memoryStatus: status.ok ? status.memory ?? null : state.memoryStatus,
        memoryLessons: lessons.ok
          ? lessons.lessons
          : state.memoryLessons.filter((lesson) => lesson.fingerprint !== cleaned),
        compileLog: ['Deleted memory lesson', ...state.compileLog].slice(0, 20),
        lastError: status.ok && lessons.ok ? null : status.error ?? lessons.error ?? state.lastError,
      }))
    },

    async ignoreMemoryLesson(fingerprint: string) {
      const cleaned = fingerprint.trim()
      if (!cleaned) {
        set({ lastError: 'Lesson fingerprint is required.' })
        return
      }
      const result = await api.ignoreMemoryLesson(cleaned)
      if (!result.ok) {
        set({ lastError: result.error ?? 'Failed to ignore project memory lesson.' })
        return
      }
      const [status, lessons] = await Promise.all([
        api.fetchMemoryStatus(),
        api.fetchMemoryLessons(),
      ])
      set((state) => ({
        memoryStatus: status.ok ? status.memory ?? null : state.memoryStatus,
        memoryLessons: lessons.ok
          ? lessons.lessons
          : state.memoryLessons.filter((lesson) => lesson.fingerprint !== cleaned),
        compileLog: ['Ignored memory lesson', ...state.compileLog].slice(0, 20),
        lastError: status.ok && lessons.ok ? null : status.error ?? lessons.error ?? state.lastError,
      }))
    },

    async updateMemoryLesson(fingerprint: string, updates: Parameters<typeof api.updateMemoryLesson>[1]) {
      const cleaned = fingerprint.trim()
      if (!cleaned) {
        set({ lastError: 'Lesson fingerprint is required.' })
        return
      }
      const result = await api.updateMemoryLesson(cleaned, updates)
      if (!result.ok) {
        set({ lastError: result.error ?? 'Failed to update project memory lesson.' })
        return
      }
      const [status, lessons] = await Promise.all([
        api.fetchMemoryStatus(),
        api.fetchMemoryLessons(),
      ])
      set((state) => ({
        memoryStatus: status.ok ? status.memory ?? null : state.memoryStatus,
        memoryLessons: lessons.ok
          ? lessons.lessons
          : state.memoryLessons.map((lesson) =>
              lesson.fingerprint === cleaned && result.lesson ? result.lesson : lesson,
            ),
        compileLog: ['Updated memory lesson', ...state.compileLog].slice(0, 20),
        lastError: status.ok && lessons.ok ? null : status.error ?? lessons.error ?? state.lastError,
      }))
    },

    async clearProjectMemory() {
      const result = await api.clearProjectMemory()
      if (!result.ok) {
        set({ lastError: result.error ?? 'Failed to clear project memory.' })
        return
      }
      const refreshed = await api.fetchMemoryStatus()
      set((state) => ({
        memoryStatus: refreshed.ok ? refreshed.memory ?? null : state.memoryStatus,
        memoryLessons: [],
        memorySkillPreview: '',
        assistantMessages: [],
        compileLog: ['Cleared project memory', ...state.compileLog].slice(0, 20),
        lastError: refreshed.ok ? null : refreshed.error ?? state.lastError,
      }))
    },

    // ── G4：蒸馏教训确认卡（lesson ≠ skill；状态机与持久化全在后端）──

    async loadDistilledLessons(status?: string) {
      const result = await api.fetchDistilledLessons(status)
      if (!result.ok) {
        set({ lastError: result.error ?? 'Failed to load distilled lessons.' })
        return
      }
      set({ distilledLessons: result.lessons })
    },

    async distillLessons() {
      set({ distilledLessonsBusy: true, distilledLessonsMessage: null, lastError: null })
      const result = await api.distillDistilledLessons()
      if (!result.ok) {
        set({
          distilledLessonsBusy: false,
          distilledLessonsMessage: {
            kind: 'error',
            text: result.error ?? 'Failed to distill quality lessons.',
          },
          lastError: result.error ?? 'Failed to distill quality lessons.',
        })
        return
      }
      const refreshed = await api.fetchDistilledLessons()
      set((state) => ({
        distilledLessonsBusy: false,
        distilledLessons: refreshed.ok ? refreshed.lessons : state.distilledLessons,
        distilledLessonsMessage: distillSummaryMessage(result),
        lastError: refreshed.ok ? null : refreshed.error ?? state.lastError,
      }))
    },

    async setDistilledLessonStatus(fingerprint: string, decision: 'promote' | 'reject' | 'demote') {
      const cleaned = fingerprint.trim()
      if (!cleaned) {
        set({ lastError: 'Lesson fingerprint is required.' })
        return
      }
      set({ distilledLessonsBusy: true, distilledLessonsMessage: null })
      const result = await api.setDistilledLessonStatus({ fingerprint: cleaned, decision })
      if (!result.ok) {
        const errorText =
          typeof result.error === 'string'
            ? result.error
            : (result.error?.message ?? 'Failed to update distilled lesson.')
        set({
          distilledLessonsBusy: false,
          distilledLessonsMessage: { kind: 'error', text: errorText },
          lastError: errorText,
        })
        return
      }
      const refreshed = await api.fetchDistilledLessons()
      set((state) => ({
        distilledLessonsBusy: false,
        distilledLessons: refreshed.ok ? refreshed.lessons : state.distilledLessons,
        distilledLessonsMessage: refreshed.ok
          ? { kind: 'info', text: distillStatusMessage(decision, result.status, result.changed) }
          : state.distilledLessonsMessage,
        lastError: refreshed.ok ? null : refreshed.error ?? state.lastError,
      }))
    },
  }
}

function distillSummaryMessage(result: DistillLessonsResult): { kind: 'error' | 'info'; text: string } {
  if (result.note === 'llm_unavailable') {
    return { kind: 'info', text: 'Distillation skipped: no LLM configured.' }
  }
  if (result.note === 'distill_error') {
    return { kind: 'error', text: 'Distillation failed unexpectedly; nothing merged.' }
  }
  if (!result.new_lessons) {
    if (result.note === 'llm_failed') {
      return { kind: 'error', text: 'Distillation LLM call failed; will retry on the next run.' }
    }
    if (result.note === 'parse_failed') {
      return { kind: 'error', text: 'Distillation output could not be parsed; nothing merged.' }
    }
    return { kind: 'info', text: 'No new runs to distill.' }
  }
  const rejected = result.rejected ? ` (${result.rejected} rejected)` : ''
  const plural = result.new_lessons === 1 ? '' : 's'
  return { kind: 'info', text: `Distilled ${result.new_lessons} new lesson${plural}${rejected}.` }
}

function distillStatusMessage(
  decision: 'promote' | 'reject' | 'demote',
  status: string | undefined,
  changed: boolean | undefined,
): string {
  if (!changed) return `Lesson already ${status ?? decision}.`
  if (decision === 'promote') return 'Lesson promoted — it now guides future distillations.'
  if (decision === 'reject') return 'Lesson rejected and excluded from future distillations.'
  return 'Lesson demoted back to proposed.'
}
