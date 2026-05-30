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
  }
}
