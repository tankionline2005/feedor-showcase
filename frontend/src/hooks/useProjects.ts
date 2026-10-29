import { useCallback, useContext, useEffect, useSyncExternalStore } from 'react'
import { api, type Project, type ProjectsState } from '../api'
import { MeContext, ReloadMeContext } from './useMe'

// One copy for the whole app: the switcher, the settings and the plan screen all show the projects.
let state: ProjectsState | null = null
let loading: Promise<void> | null = null
const listeners = new Set<() => void>()

function set(next: ProjectsState | null) {
  state = next
  listeners.forEach((fn) => fn())
}

export function setProjects(next: ProjectsState) {
  set(next)
}

export function reloadProjects(): Promise<void> {
  loading ??= api
    .projects()
    .then(set)
    .catch(() => {})
    .finally(() => {
      loading = null
    })
  return loading
}

/** The user's projects (null for the admin, and until loaded). */
export function useProjects(): ProjectsState | null {
  const { role } = useContext(MeContext)
  const value = useSyncExternalStore(
    (fn) => {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    () => state,
  )
  useEffect(() => {
    if (role === 'user' && state === null) reloadProjects()
  }, [role])
  return role === 'user' ? value : null
}

export function useCurrentProject(): Project | null {
  return useProjects()?.items.find((p) => p.current) ?? null
}

/** Opens another project: everything on screen reloads for it. */
export function useSwitchProject() {
  const reloadMe = useContext(ReloadMeContext)
  return useCallback(
    async (id: number) => {
      await api.openProject(id)
      await Promise.all([reloadMe(), reloadProjects()])
    },
    [reloadMe],
  )
}

/** Signed out: the next person must not see these. */
export function resetProjects() {
  set(null)
}
