import { AnimatePresence, motion } from 'framer-motion'
import { Loader2 } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, onUnauthorized, type Me } from './api'
import { Sidebar } from './components/Sidebar'
import { TabBar, tabsFor, type Tab } from './components/TabBar'
import { ToastProvider } from './components/Toast'
import { useWide } from './hooks/useMedia'
import { reloadBadges, resetBadges, useBadges } from './hooks/useBadges'
import { projectChangedGroups, resetGroups } from './hooks/useGroups'
import { resetProjects } from './hooks/useProjects'
import { MeContext, ReloadMeContext } from './hooks/useMe'
import { softSpring } from './lib/motion'
import { sendDeviceHints } from './lib/deviceHints'
import { Accounts } from './screens/Accounts'
import { Devices } from './screens/Devices'
import { ApiScreen } from './screens/ApiScreen'
import { ConnectScreen, McpScreen } from './screens/McpScreen'
import { ChannelSetup } from './screens/ChannelSetup'
import { FavoritesScreen } from './screens/FavoritesScreen'
import { ChannelDetail } from './screens/ChannelDetail'
import { ErrorsScreen } from './screens/ErrorsScreen'
import { Channels } from './screens/Channels'
import { Feed } from './screens/Feed'
import { Proxies } from './screens/Proxies'
import { Login } from './screens/Login'
import { PlanScreen } from './screens/PlanScreen'
import { MemberSettings } from './screens/MemberSettings'
import { MyGroups } from './screens/MyGroups'
import { Performance } from './screens/Performance'
import { AiSettings } from './screens/AiSettings'
import { AiUsageScreen } from './screens/AiUsage'
import { AiScreen } from './screens/AiScreen'
import { TopicScreen } from './screens/TopicScreen'
import { CommentsScreen } from './screens/CommentsScreen'
import { SourcesScreen } from './screens/SourcesScreen'
import { ChatNotice } from './components/ChatNotice'
import { ChatScreen } from './screens/ChatScreen'
import { NewChatScreen } from './screens/NewChatScreen'
import { TriggersScreen } from './screens/TriggersScreen'
import { ChannelStatsScreen } from './screens/ChannelStatsScreen'
import { PromoCodes } from './screens/PromoCodes'
import { Settings } from './screens/Settings'
import { UserSettings } from './screens/UserSettings'
import { Team } from './screens/Team'
import { Users } from './screens/Users'
import { Top } from './screens/Top'
import { PlansPage } from './screens/PlansPage'
import { BalanceScreen } from './screens/BalanceScreen'
import { ServiceAiScreen } from './screens/ServiceAiScreen'
import { initIntros } from './lib/intros'
import { IntroHost } from './components/Intro'

/** A tab, or a pushed detail screen inside a tab (channel page, accounts, proxies). */
const SUB_SCREENS = [
  'accounts',
  'proxies',
  'api',
  'mcp',
  'errors',
  'users',
  'promo',
  'groups',
  'team',
  'performance',
  'plan',
  'openrouter',
  'openrouter-usage',
  'devices',
  'balance',
  'ai-usage',
  'service-ai',
] as const
/** Management screens: the admin's only. */
const ADMIN_SCREENS: readonly string[] = ['accounts', 'proxies', 'errors', 'users', 'promo', 'performance', 'service-ai']
/** A project owner's: their groups, team, plan and AI. */
const OWNER_SCREENS: readonly string[] = ['groups', 'team', 'plan', 'openrouter', 'openrouter-usage', 'balance', 'ai-usage']
type Route = {
  tab: Tab
  channelId?: number
  /** the channel's «Статистика» */
  stats?: boolean
  sub?: (typeof SUB_SCREENS)[number]
  userId?: number
  topicId?: number
  chatId?: number
  /** #/ai/chat/new?topic=<id>: a chat about to be made (a post on a topic) */
  newChat?: boolean
  /** #/triggers: «Триггер-слова», from the feed */
  triggers?: boolean
  /** #/favorites: «Избранное», from the feed */
  favorites?: boolean
  /** #/comments/<post>/<section>: a post's comments, in the section it was opened from */
  commentsPostId?: number
  /** #/ai/sources/<chat post>: the posts a post the AI wrote comes from («N источников») */
  sourcesOf?: number
  /** #/connect/<request>: an AI app asks to be let in through the MCP connector (backend mcp_oauth) */
  connectRequest?: string
}

const TAB_IDS: readonly string[] = ['feed', 'top', 'ai', 'channels', 'settings']

function parseHash(hash: string): Route {
  // "#/openrouter?connected=1": the screen reads its query itself
  const [, first, second, third] = hash.replace(/^#/, '').split('?')[0].split('/')
  if (first === 'channel' && second && /^\d+$/.test(second))
    return { tab: 'channels', channelId: Number(second), stats: third === 'stats' }
  // #/ai/topic/<id>: a topic, opened from the list or from the bot's message
  if (first === 'ai' && second === 'topic' && third && /^\d+$/.test(third)) return { tab: 'ai', topicId: Number(third) }
  if (first === 'ai' && second === 'chat' && third && /^\d+$/.test(third)) return { tab: 'ai', chatId: Number(third) }
  if (first === 'ai' && second === 'chat' && third === 'new') return { tab: 'ai', newChat: true }
  if (first === 'ai' && second === 'sources' && third && /^\d+$/.test(third))
    return { tab: 'ai', sourcesOf: Number(third) }
  if (first === 'triggers') return { tab: 'feed', triggers: true }
  if (first === 'favorites') return { tab: 'feed', favorites: true }
  if (first === 'comments' && second && /^\d+$/.test(second))
    return { tab: (TAB_IDS.includes(third) ? third : 'feed') as Tab, commentsPostId: Number(second) }
  // #/qr/<code>: a computer's QR code opened by the phone's camera — «Устройства» asks to confirm it
  if (first === 'qr') return { tab: 'settings', sub: 'devices' }
  if (first === 'connect' && second) return { tab: 'settings', connectRequest: second }
  const sub = SUB_SCREENS.find((s) => s === first)
  // #/users/<id>: a person's card, opened from the list of who tracks a channel
  if (sub === 'users' && second && /^\d+$/.test(second)) return { tab: 'settings', sub, userId: Number(second) }
  if (sub) return { tab: 'settings', sub }
  if (first === 'top' || first === 'ai' || first === 'channels' || first === 'settings') return { tab: first }
  return { tab: 'feed' }
}

function routeKey(r: Route) {
  if (r.connectRequest) return 'connect'
  return r.sourcesOf
    ? `sources/${r.sourcesOf}`
    : r.commentsPostId
      ? `comments/${r.commentsPostId}`
      : r.channelId
        ? `channel/${r.channelId}${r.stats ? '/stats' : ''}`
        : r.topicId
          ? `topic/${r.topicId}`
          : r.chatId
            ? `chat/${r.chatId}`
            : r.newChat
              ? 'chat/new'
              : r.triggers
                ? 'triggers'
                : r.favorites
                  ? 'favorites'
                  : (r.sub ?? r.tab)
}

function isDetail(r: Route) {
  return Boolean(
    r.channelId ||
      r.sub ||
      r.topicId ||
      r.chatId ||
      r.newChat ||
      r.triggers ||
      r.favorites ||
      r.commentsPostId ||
      r.sourcesOf ||
      r.connectRequest,
  )
}

function useRoute() {
  const [route, setRoute] = useState<Route>(() => parseHash(location.hash))
  useEffect(() => {
    const onHash = () => setRoute(parseHash(location.hash))
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  const go = useCallback((path: string) => {
    location.hash = `/${path}`
  }, [])
  return [route, go] as const
}

// push = slide in from the right, pop = back to the left, tab = crossfade
const variants = {
  enter: (dir: string) => ({
    opacity: 0,
    x: dir === 'push' ? 60 : dir === 'pop' ? -40 : 0,
    scale: dir === 'tab' ? 0.99 : 1,
  }),
  center: { opacity: 1, x: 0, scale: 1 },
  exit: (dir: string) => ({
    opacity: 0,
    x: dir === 'push' ? -40 : dir === 'pop' ? 60 : 0,
    transition: { duration: 0.12 },
  }),
}

export default function App() {
  const [auth, setAuth] = useState<'loading' | 'out' | Me>('loading')

  const loadMe = useCallback(
    () =>
      api.me().then(
        (me) => {
          // before the screens mount: a section's welcome cards open on its first visit only
          initIntros(me.intros_seen, me.role !== 'admin')
          setAuth(me)
        },
        () => setAuth('out'),
      ),
    [],
  )

  useEffect(() => {
    loadMe()
    return onUnauthorized(() => setAuth('out'))
  }, [loadMe])

  const logout = async () => {
    await api.logout().catch(() => {})
    resetGroups()
    resetBadges()
    resetProjects()
    location.hash = '/'
    setAuth('out')
  }

  return (
    <ToastProvider>
      {auth === 'loading' && (
        <div className="loader" style={{ minHeight: '100dvh', alignItems: 'center' }}>
          <Loader2 size={28} className="spinner" />
        </div>
      )}
      {auth === 'out' && <Outside onDone={loadMe} />}
      {typeof auth === 'object' && (
        <MeContext.Provider value={auth}>
          <ReloadMeContext.Provider value={loadMe}>
            {auth.needs_channel && auth.project ? (
              <ChannelSetup projectId={auth.project.id} onLogout={logout} />
            ) : (
              <Shell me={auth} onLogout={logout} />
            )}
          </ReloadMeContext.Provider>
        </MeContext.Provider>
      )}
    </ToastProvider>
  )
}

/** Before signing in: the login page, or the plans («Посмотреть тарифы», #/plans). */
function Outside({ onDone }: { onDone: () => void }) {
  const [plans, setPlans] = useState(() => /^#\/plans\b/.test(location.hash))
  useEffect(() => {
    const onHash = () => setPlans(/^#\/plans\b/.test(location.hash))
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  useEffect(() => {
    window.scrollTo(0, 0) // braces: a browser's scrollTo may give back a promise, and an effect must not
  }, [plans])
  return plans ? <PlansPage onBack={() => (location.hash = '/')} /> : <Login onDone={onDone} />
}

function Shell({ me, onLogout }: { me: Me; onLogout: () => void }) {
  const [hashRoute, go] = useRoute()
  const wide = useWide()
  const admin = me.role === 'admin'
  const tabs = tabsFor(me)
  // someone who opens another role's address (or a tab they have not got) lands on their own settings
  const foreign =
    hashRoute.sub &&
    ((!admin && ADMIN_SCREENS.includes(hashRoute.sub)) ||
      (me.role !== 'user' && OWNER_SCREENS.includes(hashRoute.sub)) ||
      (admin && hashRoute.sub === 'devices'))
  const route: Route = foreign || !tabs.some((t) => t.id === hashRoute.tab) ? { tab: 'settings' } : hashRoute
  // another project: every screen loads again for it
  const key = `${me.project?.id ?? 'all'}:${routeKey(route)}`
  const prev = useRef(route)
  const scrolls = useRef<Record<string, number>>({})

  useEffect(() => {
    if (!admin) sendDeviceHints()
  }, [admin])

  const direction =
    isDetail(route) && !isDetail(prev.current) ? 'push' : !isDetail(route) && isDetail(prev.current) ? 'pop' : 'tab'
  const back = (fallback: Tab) => (history.length > 1 ? history.back() : go(fallback))

  // remember where each screen was scrolled, restore it on return
  useLayoutEffect(() => {
    return () => {
      scrolls.current[key] = window.scrollY
    }
  }, [key])

  useEffect(() => {
    prev.current = route
  })

  // a channel page of the project left behind would show nothing: back to the channel list
  const project = me.project?.id
  const shownProject = useRef(project)
  useEffect(() => {
    if (shownProject.current !== project && route.channelId) go('channels')
    shownProject.current = project
  }, [project, route.channelId, go])

  // the blue badges: new posts on «Лента»; new topics, the AI's new posts and unread answers together on «AI»
  const badges = useBadges(!admin)
  const tabBadges = { feed: badges.feed, ai: badges.topics + badges.posts + badges.chats }
  useEffect(() => {
    if (!admin) reloadBadges()
  }, [project, admin])
  // «Группы проекта» are each project's own
  const groupsOf = useRef(project)
  useEffect(() => {
    if (groupsOf.current !== project) projectChangedGroups()
    groupsOf.current = project
  }, [project])

  const selectTab = (tab: Tab) => {
    // the owner without their own OpenRouter linked (dev:service:plan): «AI» shows where to link it
    if (tab === 'ai' && me.role === 'user' && me.ai_own && !me.ai_linked) {
      location.hash = '/settings?flash=ai'
      return
    }
    if (tab === route.tab && !isDetail(route)) {
      window.scrollTo({ top: 0, behavior: 'smooth' })
      return
    }
    go(tab)
  }
  const selectRef = useRef(selectTab)
  selectRef.current = selectTab

  const tabsRef = useRef(tabs)
  tabsRef.current = tabs

  // keyboard: digits switch sections, Esc goes back from a detail screen
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      // typing: a field, a list to choose from, the post editor (contenteditable) — the keys are the text's
      const typing = target.isContentEditable || target.closest('input, textarea, select, [role="dialog"]')
      if (e.metaKey || e.ctrlKey || e.altKey || typing) return
      const tab = tabsRef.current[Number(e.key) - 1]
      if (tab) selectRef.current(tab.id)
      else if (e.key === 'Escape' && isDetail(parseHash(location.hash))) history.back()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className={`app${wide ? ' wide' : ''}`}>
      {wide && <Sidebar tabs={tabs} active={route.tab} badges={tabBadges} onSelect={selectTab} />}
      <AnimatePresence
        mode="wait"
        custom={direction}
        initial={false}
        onExitComplete={() => window.scrollTo(0, direction === 'pop' ? (scrolls.current[key] ?? 0) : 0)}
      >
        <motion.main
          key={key}
          custom={direction}
          variants={variants}
          initial="enter"
          animate="center"
          exit="exit"
          transition={softSpring}
        >
          {route.connectRequest ? (
            <ConnectScreen request={route.connectRequest} onDone={() => go('mcp')} />
          ) : route.sourcesOf ? (
            <SourcesScreen id={route.sourcesOf} onBack={() => back('ai')} />
          ) : route.commentsPostId ? (
            <CommentsScreen id={route.commentsPostId} tab={route.tab} onBack={() => back(route.tab)} />
          ) : route.channelId && route.stats ? (
            <ChannelStatsScreen id={route.channelId} onBack={() => back('channels')} />
          ) : route.channelId ? (
            <ChannelDetail
              id={route.channelId}
              onBack={() => back('channels')}
              onOpenStats={() => go(`channel/${route.channelId}/stats`)}
            />
          ) : route.sub === 'accounts' ? (
            <Accounts onBack={() => back('settings')} />
          ) : route.sub === 'proxies' ? (
            <Proxies onBack={() => back('settings')} />
          ) : route.sub === 'api' ? (
            <ApiScreen onBack={() => back('settings')} />
          ) : route.sub === 'mcp' ? (
            <McpScreen onBack={() => back('settings')} />
          ) : route.sub === 'errors' ? (
            <ErrorsScreen onBack={() => back('settings')} />
          ) : route.sub === 'users' ? (
            <Users onBack={() => back('settings')} openUserId={route.userId} />
          ) : route.sub === 'promo' ? (
            <PromoCodes onBack={() => back('settings')} />
          ) : route.sub === 'performance' ? (
            <Performance onBack={() => back('settings')} />
          ) : route.sub === 'groups' ? (
            <MyGroups onBack={() => back('settings')} />
          ) : route.sub === 'team' ? (
            <Team onBack={() => back('settings')} />
          ) : route.sub === 'plan' ? (
            <PlanScreen
              onBack={() => back('settings')}
              onOpenBalance={() => go('balance')}
              onOpenUsage={() => go('ai-usage')}
            />
          ) : route.sub === 'balance' ? (
            <BalanceScreen onBack={() => back('settings')} />
          ) : route.sub === 'ai-usage' ? (
            <AiUsageScreen onBack={() => back('settings')} backLabel="Назад" />
          ) : route.sub === 'service-ai' ? (
            <ServiceAiScreen onBack={() => back('settings')} />
          ) : route.sub === 'openrouter' ? (
            <AiSettings onBack={() => go('settings')} onOpenUsage={() => go('openrouter-usage')} />
          ) : route.sub === 'devices' ? (
            <Devices onBack={() => back('settings')} />
          ) : route.sub === 'openrouter-usage' ? (
            <AiUsageScreen onBack={() => go('openrouter')} />
          ) : route.chatId ? (
            <ChatScreen id={route.chatId} onBack={() => go('ai')} />
          ) : route.newChat ? (
            <NewChatScreen onBack={() => go('ai')} />
          ) : route.triggers ? (
            <TriggersScreen onBack={() => back('feed')} />
          ) : route.favorites ? (
            <FavoritesScreen onBack={() => back('feed')} />
          ) : route.topicId ? (
            <TopicScreen id={route.topicId} onBack={() => back('ai')} />
          ) : route.tab === 'top' ? (
            <Top />
          ) : route.tab === 'ai' ? (
            <AiScreen onOpenTopic={(id) => go(`ai/topic/${id}`)} onOpenChat={(id) => go(`ai/chat/${id}`)} />
          ) : route.tab === 'channels' ? (
            <Channels onOpen={(id) => go(`channel/${id}`)} />
          ) : route.tab === 'settings' && me.role === 'member' ? (
            <MemberSettings
              onLogout={onLogout}
              onOpenApi={() => go('api')}
              onOpenMcp={() => go('mcp')}
              onOpenDevices={() => go('devices')}
            />
          ) : route.tab === 'settings' && !admin ? (
            <UserSettings
              onLogout={onLogout}
              onOpenApi={() => go('api')}
              onOpenMcp={() => go('mcp')}
              onOpenGroups={() => go('groups')}
              onOpenTeam={() => go('team')}
              onOpenPlan={() => go('plan')}
              onOpenBalance={() => go('balance')}
              onOpenAi={() => go(me.ai_own ? 'openrouter' : 'ai-usage')}
              onOpenDevices={() => go('devices')}
            />
          ) : route.tab === 'settings' ? (
            <Settings
              onLogout={onLogout}
              onOpenAccounts={() => go('accounts')}
              onOpenProxies={() => go('proxies')}
              onOpenApi={() => go('api')}
              onOpenMcp={() => go('mcp')}
              onOpenErrors={() => go('errors')}
              onOpenUsers={() => go('users')}
              onOpenPromo={() => go('promo')}
              onOpenPerformance={() => go('performance')}
              onOpenServiceAi={() => go('service-ai')}
            />
          ) : (
            <Feed />
          )}
        </motion.main>
      </AnimatePresence>
      {!wide && <TabBar tabs={tabs} active={route.tab} badges={tabBadges} onSelect={selectTab} />}
      {(me.role === 'user' || me.member?.ai_chat) && <ChatNotice />}
      <IntroHost />
    </div>
  )
}
