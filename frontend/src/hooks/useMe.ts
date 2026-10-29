import { createContext, useContext } from 'react'
import type { Me } from '../api'

export const MeContext = createContext<Me>({
  role: 'user',
  user: null,
  username: null,
  project: null,
  plan: null,
  can_edit_channels: true,
  member: null,
  ai_linked: false,
  ai_own: false,
  notice: null,
  needs_channel: false,
  intros_seen: [],
})

/** Reloads who is signed in, e.g. after a promo code changed the plan. */
export const ReloadMeContext = createContext<() => Promise<void>>(async () => {})

export function useReloadMe() {
  return useContext(ReloadMeContext)
}

/** Who is signed in. The admin manages everything; a user sees only their own part. */
export function useMe() {
  return useContext(MeContext)
}

export function useIsAdmin() {
  return useMe().role === 'admin'
}
