import type { Me } from '../api'

/** The name the server gives the owner when it says who did something (deps.OWNER_NAME). */
export const OWNER_NAME = 'владелец'

/**
 * Who took a decision that teaches the AI (hid a topic, corrected the smart feed), as
 * this viewer reads it: «вы», «владелец» or the staff member's login. Decisions from before anyone
 * was recorded were the owner's.
 */
export function whoDid(by: string | null | undefined, me: Me): string {
  const name = by || OWNER_NAME
  const mine = me.role === 'member' ? me.member?.login : OWNER_NAME
  return name === mine ? 'вы' : name
}

/** What the server records as «who» for this viewer. */
export const myName = (me: Me) => (me.role === 'member' ? (me.member?.login ?? '') : OWNER_NAME)

/** May this viewer do it: the owner always, a staff member if the owner turned the switch on. */
export function may(me: Me, right: 'ai_teach' | 'ai_configure' | 'ai_chat' | 'ai_publish') {
  return me.role === 'user' || (me.role === 'member' && !!me.member?.[right])
}
