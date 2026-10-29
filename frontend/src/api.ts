export type Period = '20m' | '1h' | '6h' | '24h' | '7d' | '30d' | 'all'
export type Sort = 'new' | 'er' | 'fr' | 'fr_plus' | 'forwards' | 'reactions' | 'comments' | 'views'

export interface ChannelRef {
  id: number
  title: string
  username: string | null
  avatar_url: string | null
}

export interface Post {
  /** «Избранное»: the person starred it (absent for the admin) */
  favorite?: boolean
  /** in the smart feed: how well the post matches its prompt, and why (a few words) */
  smart?: {
    score: number
    reason: string | null
    match: boolean
    /** the person overruled the AI on it (shown, or hidden), and why */
    mine: 'show' | 'hide' | null
    mine_reason: string | null
    /** who did it: «владелец» or a staff member's login */
    mine_by: string | null
  }
  id: number
  channel: ChannelRef
  msg_id: number
  /** the copy in our aggregator group once reposted, otherwise the original */
  url: string | null
  source_url: string | null
  reposted: boolean
  /** deleted in its channel: the numbers are the last ones seen */
  deleted: boolean
  /** a user can have the bot send them the post ("Посмотреть оригинал поста") */
  sendable: boolean
  posted_at: string
  text: string
  html: string
  media_type: string | null
  media_count: number
  preview_url: string | null
  /** a picture of every album item that has one */
  media: PostMedia[]
  views: number
  forwards: number
  reactions: number
  comments: number
  er: number
  fr: number
  /** post FR / the channel's average FR over its last 100 settled posts; 0 while unknown */
  fr_plus: number
  /** views reached 1% of the channel's subscribers — takes a place in ER / FR / FR+ rankings */
  rated: boolean
  reactions_top: { e: string; c: number }[]
  repost_status: 'pending' | 'done' | 'skipped' | 'failed'
  repost_error: string | null
  /** «Триггер-слова»: the words the post has, as it has them (highlighted) */
  trigger_found?: string[]
  /** the search found it by the text on its pictures only (that text is not shown) */
  found_on_picture?: boolean
}

export interface PostMedia {
  url: string
  kind: 'photo' | 'video' | 'other'
  /** the full-size photo, fetched from Telegram when opened; null for a video */
  full_url: string | null
  /** a video: where to ask for its link on Telegram's servers (api.video); null for a photo */
  video_url: string | null
}

/** What a comment has besides its text. Pictures, stickers and voice messages come when shown or tapped
 * (api.commentMedia); a video or a GIF plays from Telegram's servers, as a post's (api.commentVideo). */
export type CommentMedia =
  | { kind: 'photo'; w: number | null; h: number | null; blur: string | null }
  | {
      kind: 'video' | 'round' | 'gif'
      w: number | null
      h: number | null
      /** seconds; none for a GIF */
      duration: number | null
      blur: string | null
      /** Telegram has a preview picture of it */
      thumb: boolean
    }
  | { kind: 'voice'; duration: number; waveform: number[] }
  | { kind: 'sticker'; format: 'webp' | 'tgs' | 'webm'; emoji: string | null }
  | { kind: 'other'; label: string }

/** A comment of a post, as Telegram has it in the channel's discussion group — read when the page opens, never
 * stored. */
export type PublicationsFilter = 'all' | 'scheduled' | 'published' | 'suggested'

/** «Публикации» → settings, the owner's alone. */
export interface PublicationSettings {
  /** «Создавать тематические посты»: after a topics run, a post for each of its largest new topics */
  topic_posts: boolean
  posts_limit: number
  prompt: string
  /** «Автопостинг» */
  autopost: 'off' | 'now' | 'delay'
  autopost_hours: number
  /** «Время между публикациями», minutes */
  autopost_gap_min: number
  /** «Минимальная важность» (1-10, 1: any): of the topics posts are written for, and of those autoposted */
  min_importance_posts: number
  /** autoposts a day at most (the owner's local day); 0: any */
  daily_limit: number
  /** autoposting only topics this many channels told (1: any) */
  autopost_min_sources: number
  limits: number[]
  hours: number[]
  gaps: number[]
  daily_limits: number[]
  sources_options: number[]
}

/** «Обложки»: the project's cover style (backend covers.py). */
export interface CoverSettings {
  /** autoposts and rubrics' issues: off | always | no_photo */
  mode: 'off' | 'always' | 'no_photo'
  template: string
  size: string
  accent: string
  font: string
  position: 'top' | 'center' | 'bottom'
  show_channel: boolean
  has_logo: boolean
  /** changes with every save: the previews are asked anew */
  version: number
  templates: { key: string; name: string }[]
  fonts: { key: string; name: string }[]
  sizes: string[]
  accents: string[]
  modes: ('off' | 'always' | 'no_photo')[]
}

/** «Календарь»: a post that is to go, went out or failed to, in a few words. */
export interface CalendarItem {
  id: number
  at: string
  state: 'scheduled' | 'published' | 'failed'
  title: string
}

/** «Что такое важность»: a band of the scale, with the project's recent topics in it. */
export interface ImportanceBand {
  from: number
  to: number
  examples: { title: string; importance: number }[]
}

export interface PostComment {
  id: number
  date: string
  edited: boolean
  html: string
  author: { name: string; username: string | null; seed: number }
  /** what it answers: the comment's author and a few words of it (or the part quoted) */
  reply: { id: number; name: string | null; text: string } | null
  media: CommentMedia | null
  reactions: { e: string; c: number }[]
}

export type CommentsPage =
  | {
      total: number
      items: PostComment[]
      has_more: boolean
      /** the discussion group's @username; none when it is closed */
      group: string | null
      unavailable?: undefined
    }
  | { unavailable: 'none' | 'private' | 'gone' | 'busy'; message: string }

/** A video to play straight from Telegram's servers — or why Telegram does not give it (`error`, `message`). */
export type VideoSource =
  | {
      url: string
      /** gif: plays muted in a loop; round: a «кружок» */
      kind: 'video' | 'gif' | 'round'
      /** width / height, when Telegram tells */
      ratio: number | null
      duration: string | null
      error?: undefined
    }
  | { error: 'too_big' | 'protected' | 'gone' | 'private' | 'missing' | 'telegram'; message: string; url?: undefined }

export type ChannelStatus = 'pending' | 'active' | 'paused' | 'error' | 'deleting' | 'restricted'

export interface Channel {
  id: number
  link: string
  title: string | null
  username: string | null
  /** every public @username it has now, the main one first; empty when it is private */
  usernames: string[]
  subscribers: number | null
  status: ChannelStatus
  status_detail: string | null
  account_id: number | null
  avatar_url: string | null
  last_post_at: string | null
  created_at: string
  posts_per_day: number
  avg_er: number
  avg_fr: number
  avg_views: number
  /** user only: the project's own channel (tracked for free, cannot be removed from the list) */
  own?: boolean
  /** user only: over the plan's limit, not read till a plan with room is bought (or another one is paused) */
  paused?: boolean
  /** admin only: how many users track the channel */
  users?: number
  /** admin only: added by the admin, kept even when no user tracks it */
  held?: boolean
  /** admin only: the account that reads the channel */
  account_name?: string | null
  /** admin only: backup accounts inside a private channel */
  backups?: Backup[]
}

export interface Backup {
  name: string
  status: 'planned' | 'requested' | 'member' | 'failed'
  /** when it joins (planned) or is tried again (failed) */
  due_at: string | null
}

export type AccountStatus = 'new' | 'active' | 'flood' | 'waiting' | 'error' | 'dead'
export type ProxyStatus = 'unknown' | 'ok' | 'dead'

export interface Account {
  id: number
  phone: string | null
  name: string | null
  username: string | null
  enabled: boolean
  status: AccountStatus
  status_detail: string | null
  flood_until: string | null
  proxy: { id: number; label: string; kind: Proxy['kind']; status: ProxyStatus } | null
  custom_api: boolean
  max_channels: number
  channels: number
}

export interface Proxy {
  id: number
  kind: 'socks5' | 'mtproto'
  host: string
  port: number
  label: string
  username: string | null
  enabled: boolean
  status: ProxyStatus
  latency_ms: number | null
  checked_at: string | null
  last_error: string | null
  accounts: number
  /** where an automatically added proxy came from, e.g. "@ProxyMTProto"; null = added by hand */
  source: string | null
}

export interface Settings {
  aggregator_channel: string
  retention_days: number
  /** free disk space (MB) below which the oldest posts are deleted early */
  cleanup_free_mb: number
  /** a new channel brings its posts of the last this many days */
  backfill_days: number
  poll_interval_sec: number
  join_interval_sec: number
  repost_interval_sec: number
  repost_max_age_hours: number
  accounts_per_proxy: number
  /** accounts inside every private channel: the main one plus backups */
  /** pick up new proxies from the public channel @ProxyMTProto */
  proxy_feed_enabled: boolean
}

export interface NotifyState {
  configured: boolean
  bot_username: string | null
  link: string | null
  chats: { chat_id: number; title: string | null }[]
}

export interface Storage {
  total: number
  used: number
  free: number
  database: number
  media: number
  level: 'ok' | 'low' | 'critical'
}

export type PerfMetric = 'cpu' | 'mem_used' | 'disk_read' | 'disk_write' | 'net_in' | 'net_out'
export type PerfPeriod = '1h' | '24h' | '7d'

/** Settings → System Performance: the server's load, averaged per point (step seconds). */
export interface Perf {
  period: PerfPeriod
  step: number
  since: string
  until: string
  mem_total: number | null
  points: ({ t: string } & Record<PerfMetric, number>)[]
  summary: Record<PerfMetric, { now: number | null; avg: number | null; max: number | null }>
}

export interface FailedRepost {
  post_id: number
  channel: { id: number; title: string; username: string | null }
  url: string | null
  posted_at: string
  failed_at: string | null
  error: string
  text: string
  media_type: string | null
}

export interface ImportResult {
  added: number
  duplicates: string[]
  errors: { file: string; error: string }[]
}

export interface Status {
  channels: Record<string, number>
  accounts: Record<string, number>
  reposts: Record<string, number>
  proxies: Record<string, number>
  posts: number
  last_repost_error: string | null
}

export type PlanId = 'trial' | 'starter' | 'pro' | 'studio' | 'agency' | 'dev'

export interface Plan {
  id: PlanId
  title: string
  /** USD for 30 days; null: not sold (the trial, dev:service:plan) */
  price: number | null
  /** how many channels a project may track; null = no limit */
  channels: number | null
  /** the API and the MCP connector */
  api: boolean
  /** staff accounts of a project; null = no limit */
  members: number | null
  /** «Триггер-слова» of a project (the owner's and the staff's together); null = no limit */
  triggers: number | null
  /** «Группы проекта»; null = no limit */
  groups: number | null
  /** USD of AI a month at the service's prices (the trial's: once); null: the owner's own OpenRouter */
  ai: number | null
  badge: string | null
  buyable: boolean
  /** null: the plan never ends */
  expires_at: string | null
}

/** «Тариф» and «Баланс» of the current project's owner (backend billing.py). Money in USD. */
export interface BillingState {
  balance: number
  /** how to top up once the payments are open; null: «Пополнение временно недоступно» */
  topup: null
  payments_live: boolean
  plan: Plan | null
  /** a plan that ran out: the project is frozen till one is bought */
  expired_plan: Plan | null
  /** when a frozen project's data goes (once the payments are live) */
  delete_at: string | null
  started_at: string | null
  auto_renew: boolean
  /** a cheaper plan chosen: it comes at the end of the period */
  next_plan: Plan | null
  renew_price: number | null
  /** the plan's AI this period; null: the owner's own OpenRouter */
  ai: {
    included: number
    used: number
    extra_used: number
    extra: boolean
    extra_cap: number | null
    renews_at: string | null
  } | null
  paused: { channels: number; members: number; triggers: number }
  plans: Plan[]
  project: string
}

export interface BillingOp {
  id: number
  kind: 'topup' | 'plan' | 'refund' | 'ai' | 'admin'
  title: string
  note: string | null
  amount: number
  project: string | null
  at: string
  day: string | null
}

/** The admin's side of the money: the service's OpenRouter, its models and markup, the month's figures. */
export interface ServiceAi {
  linked: boolean
  paused: string | null
  paused_reason: string | null
  balance: AiUsage['balance']
  models: { operation: AiOperation; title: string; model: string; default: string }[]
  markup_pct: number
  payments_live: boolean
  trials_waiting: number
  month: { cost: number; charged: number; plans: number }
  owners: { user_id: number; name: string | null; username: string | null; cost: number; charged: number; requests: number }[]
}

/** The owner's banner on every screen: the plan ends, the project is frozen, the AI stands. */
export interface Notice {
  tone: 'warn' | 'danger'
  text: string
  action: 'plan' | 'balance'
}

/** A project's own group the bot sends posts to («Группы проекта»). */
export interface MyGroup {
  chat_id: number
  title: string | null
  username: string | null
  /** the bot was removed from the group */
  bot_removed: boolean
  created_at: string
}

export interface Project {
  id: number
  /** the channel's title, or "Проект 2" until one is chosen */
  title: string
  channel: {
    /** the channel in the monitoring (it is tracked like any other, outside the limit) */
    channel_id: number
    title: string
    username: string | null
    link: string
    /** active: its posts are collected; pending: an account is joining it; error: see status_detail */
    status: 'active' | 'pending' | 'error'
    status_detail: string | null
  } | null
  avatar_url: string | null
  /** the plan in force; null when none or expired */
  plan: Plan | null
  /** the plan ran out */
  expired: boolean
  /** the project this browser works in */
  current: boolean
}

export interface ProjectsState {
  items: Project[]
  /** a new project only once every project has a plan */
  can_create: boolean
  create_hint: string | null
}

/** A channel the bot was added to that may become the project's channel. */
export interface CandidateChannel {
  chat_id: number
  title: string | null
  username: string | null
  /** already the channel of this or another of the user's projects */
  project: { id: number; title: string } | null
}

export interface GroupsState {
  items: MyGroup[]
  /** groups the owner added the bot to, not added to this project yet */
  found: { chat_id: number; title: string | null; username: string | null }[]
  /** groups a project may have, whatever its plan */
  limit: number
  bot_username: string | null
}

export interface PromoCode {
  code: string
  plan: Plan
  /** null: forever */
  days: number | null
  created_at: string
  used_at: string | null
  used_by: { id: number; name: string | null; username: string | null } | null
}

/** How «Создать об этом пост для моего канала» asks the AI: the person's own template, or the default. */
export interface PostPrompt {
  template: string
  default: string
  /** the person changed it */
  custom: boolean
}

/** Who is signed in: the admin (password at #/admin) or a user (through the bot). */
export type Role = 'admin' | 'user' | 'member'

export interface Me {
  /** the admin, a project owner (signed in through the bot), or a staff member of a project */
  role: Role
  /** a staff member: their login */
  user: string | null
  username: string | null
  /** the project the user (or the staff member) works in; null for the admin */
  project: { id: number } | null
  /** the project's plan; null when none or expired */
  plan: Plan | null
  /** adding and removing channels: the owner always, a staff member if allowed */
  can_edit_channels: boolean
  /** a staff member only */
  member: {
    login: string
    project_title: string
    project_username: string | null
    telegram: { username: string | null; name: string | null } | null
    ai_chat: boolean
    ai_chat_limit_usd: number | null
    ai_teach: boolean
    ai_configure: boolean
    ai_publish: boolean
  } | null
  /** the project's AI has its OpenRouter: the service's, or the owner's own (dev:service:plan) */
  ai_linked: boolean
  /** the project's AI is the owner's own OpenRouter (dev:service:plan): its settings, models and spending */
  ai_own: boolean
  /** the owner's banner on every screen */
  notice: Notice | null
  /** an owner none of whose projects has its channel: the site asks for it before anything else (ChannelSetup) */
  needs_channel: boolean
  /** the sections whose welcome cards this person has seen (lib/intros.ts) */
  intros_seen: string[]
}

/** A staff account of the current project ("Совместный мониторинг"). */
export interface TeamMember {
  id: number
  login: string
  can_edit_channels: boolean
  ai_chat: boolean
  /** what their chats may cost a month (USD); null: no limit of their own */
  ai_chat_limit_usd: number | null
  /** teaching the project's AI: hiding topics, «Это не так», correcting the owner's smart feed */
  ai_teach: boolean
  /** the settings of the smart feed and topics */
  ai_configure: boolean
  /** publishing the posts the AI wrote in the project's channel */
  ai_publish: boolean
  /** what their chats cost this month */
  chat_spent: number
  telegram: { username: string | null; name: string | null } | null
  created_at: string
  last_seen_at: string | null
  /** over the plan's limit: cannot sign in till a plan with room for them is bought */
  paused: boolean
}

export type AiOperation = 'filter' | 'vision' | 'topics' | 'advice' | 'chat'

/** Settings → «Настройки OpenRouter.ai» of a project owner. */
export interface AiState {
  linked: boolean
  connected_at: string | null
  /** the owner accepted that tokens are paid by them and models are their choice (asked after each link) */
  warning_accepted: boolean
  /** why the AI stands still (no credits, a refused key, the monthly cap), for people */
  paused: string | null
  models: { operation: AiOperation; title: string; model: string; default: string }[]
  monthly_limit_usd: number | null
  spent_month: number
}

export interface AiModel {
  id: string
  name: string
  context: number | null
  /** USD per million tokens */
  prompt_price: number
  completion_price: number
  caps: string[]
}

export type AiUsagePeriod = '24h' | '7d' | '30d'

export interface AiUsageTotal {
  key: string | number | null
  title: string
  cost: number
  requests: number
}

/** «Расход и остаток»: spending over a period and OpenRouter's balance. */
export interface AiUsage {
  period: AiUsagePeriod
  step: number
  bars: { t: string; cost: number; by: Partial<Record<AiOperation, number>> }[]
  cost: number
  requests: number
  /** their own OpenRouter (dev:service:plan): the tokens, models, its balance and the monthly cap are told */
  own: boolean
  tokens: { prompt: number; completion: number; cached: number } | null
  totals: Partial<Record<'operation' | 'project' | 'person' | 'model', AiUsageTotal[]>>
  spent_month: number | null
  monthly_limit_usd: number | null
  balance: {
    credits_total: number | null
    credits_used: number | null
    credits_left: number | null
    key_limit: number | null
    key_limit_left: number | null
    key_usage_daily: number | null
    key_usage_monthly: number | null
  } | null
  first_at: string | null
}

/** What the owner lets a staff member do. */
export type MemberRights = Pick<
  TeamMember,
  'can_edit_channels' | 'ai_chat' | 'ai_chat_limit_usd' | 'ai_teach' | 'ai_configure' | 'ai_publish'
>

/** The smart feed the viewer sees («AI» in the feed). */
export interface SmartState {
  linked: boolean
  paused: string | null
  /** the viewer may set it up: the owner, or a staff member allowed to set up the project's AI */
  editable: boolean
  /** hiding and showing its posts with why (also staff allowed to teach the AI, on the owner's feed) */
  can_correct: boolean
  enabled: boolean
  prompt: string
  default_prompt: string
  /** the model reading it: the owner's only */
  model: string | null
  day: { cost: number | null; judged: number; matched: number }
  balance_left: number | null
}

/** AI → «Темы»: what several posts tell about one event. */
export interface Topic {
  id: number
  title: string
  summary: string
  created_at: string
  updated_at: string
  /** something new was added lately and this person has not opened it since */
  updated: boolean
  /** the project's own channel already told it */
  covered: boolean
  /** the mark was put by hand, by whom («владелец» or a login); null: by the AI */
  covered_by: string | null
  /** «Есть продолжение»: big news came after the channel told it */
  followup: boolean
  /** the channels that wrote about it, each once */
  sources: number
  /** its posts, all of them (a channel may have written several) */
  posts_count: number
  /** hidden in the project, with why: off the list, and the AI learns from the reason */
  hidden: boolean
  hidden_at: string | null
  hide_reason: string | null
  /** «владелец» or the staff member's login */
  hidden_by: string | null
  channels: { id: number; title: string; avatar_url: string | null }[]
  views: number
  first: { channel: string; posted_at: string } | null
  /** the pictures of its posts, each once */
  media: { url: string; full_url: string | null; video_url: string | null; kind: string; post_id: number }[]
}

export interface TopicDetail extends Topic {
  timeline: { at: string; text: string }[]
  posts: Post[]
}

/** One rule of a schedule, on its days (0 is Monday). */
export interface ScheduleRule {
  /** every: every so often within hours (or all day); times: at the times listed */
  kind: 'every' | 'times'
  days: number[]
  /** «Регулярно»: the hours (null: all day); a span past midnight belongs to the day it begins */
  from?: string | null
  to?: string | null
  every_min?: number
  /** «В точное время» */
  times?: string[]
}

/** When topics run: the project's own schedule (backend schedule.py) — the rules together, and an extra run once
 * that many new posts wait. */
export interface AiSchedule {
  rules: ScheduleRule[]
  posts: { count: number; gap_min: number } | null
  tz: string
}

export interface ScheduleInfo {
  schedule: AiSchedule
  /** the schedule in a few words: «каждый час с 8:00 до 23:00» */
  schedule_summary: string
  /** each rule in a few words */
  rule_summaries: string[]
  schedule_options: {
    kinds: ScheduleRule['kind'][]
    every_min: number[]
    posts: number[]
    gap_min: number[]
    max_rules: number
    max_times: number
  }
  next_run_at: string | null
  /** the next runs by the rules (an extra one by posts may come sooner) */
  upcoming: string[]
  /** about how many runs a day; null when it depends on posts we cannot foresee */
  per_day: number | null
}

/** «Рубрики»: a post the AI writes by itself on a schedule (backend rubrics.py); its schedule as the topics'. */
export interface Rubric extends ScheduleInfo {
  id: number
  name: string
  enabled: boolean
  source: 'topics' | 'top_posts'
  /** what it takes: this many hours back; 0: since the last issue */
  period_hours: number
  max_items: number
  min_importance: number
  /** only these channels; empty: all */
  channel_ids: number[]
  words: string[]
  prompt: string
  header: string
  footer: string
  picture: 'none' | 'source'
  /** suggest: to «Предложения ИИ»; auto: straight to the channel */
  mode: 'suggest' | 'auto'
  last_run_at: string | null
  last_error: string | null
}

export interface RubricsState {
  items: Rubric[]
  periods: number[]
  item_counts: number[]
  most: number
  presets: { key: string; name: string }[]
}

export interface TopicSettings extends ScheduleInfo {
  linked: boolean
  paused: string | null
  editable: boolean
  /** «Собрать сейчас» */
  can_run: boolean
  /** hiding topics, «Это не так» */
  can_teach: boolean
  enabled: boolean
  /** the owner's only */
  prompt: string | null
  default_prompt: string | null
  last_run_at: string | null
  run: AiRun | null
  /** «По постам»: new posts waiting for the next run */
  waiting_posts: number | null
  notify: boolean
  telegram_linked: boolean
}

/** A topics run: going (on the schedule or «сейчас»), or how the last one ended. */
export type AiRun =
  | { going: true; percent: number; started_at: string }
  | {
      going: false
      finished_at: string
      result: { new: number; updated?: number } | null
      error: { text: string; detail: string | null } | null
    }

export type StatsPeriod = '7d' | '30d' | '90d'

/** «Статистика» of a project channel. Percentages are null where there is nothing to divide by. */
export interface ChannelStats {
  period: StatsPeriod
  subscribers: { t: string; v: number }[]
  days: {
    day: string
    posts: number
    avg_views: number | null
    er: number | null
    fr: number | null
    reach: number | null
    subs_delta: number | null
  }[]
  hours: { hour: number; posts: number; avg_views: number | null }[]
  summary: {
    subscribers: number | null
    subs_delta: number | null
    posts: number
    posts_per_day: number
    avg_views: number | null
    er: number | null
    fr: number | null
    reach: number | null
  }
  collecting_since: string | null
}

/** The blue badges: new posts, topics and the AI's posts by topics since the person last looked; chats with an
 * unread answer. */
export interface Badges {
  feed: number
  topics: number
  /** «Публикации»: the AI's new posts by topics */
  posts: number
  chats: number
  /** «Триггер-слова»: posts with the person's words not looked at yet */
  triggers: number
  /** counted up to this: «99+» past it */
  most: number
}

/** AI → «Чат»: a person's chats with the AI in the current project. */
export interface ChatList {
  linked: boolean
  paused: string | null
  owner: boolean
  /** the model new chats start with: the owner's only */
  model: string | null
  /** unread: an answer came after the person last had the chat open (a blue dot) */
  items: { id: number; title: string; updated_at: string; last: string; unread: boolean }[]
}

/** A picture or video a chat post may have: from the posts its facts come from. */
export interface ChatPostMedia {
  post_id: number
  slot: number
  kind: 'photo' | 'video' | 'gif'
  url: string
  full_url: string | null
  video_url: string | null
  /** why the bot cannot attach it (a channel that forbids copying…); null: it can */
  why: string | null
}

/** A post the AI wrote for the person's channel: shown as a post, edited, sent to their Telegram. */
export interface ChatPostOut {
  id: number
  /** Telegram's HTML: b, i, u, s, code, pre, tg-spoiler, a, blockquote; new lines as they are */
  text: string
  /** as the AI wrote it («Вернуть текст ИИ») */
  ai_text: string
  media: ChatPostMedia[]
  /** all the pictures and videos of its sources, the same picture once: what to choose from */
  candidates: ChatPostMedia[]
  channel: { title: string; avatar_url: string | null; seed: number }
  edited: boolean
  sent_at: string | null
  /** how many posts it was written from («N источников», api.chatPostSources) */
  sources: number
  /** last published in the project's channel, and the link to it there */
  published_at: string | null
  published_url: string | null
  /** «Отложенная публикация»: when the bot publishes it */
  scheduled_at: string | null
  /** «Публикации»: the topic the AI wrote it for by itself; who published and who scheduled it («владелец», a
   * staff member's login, «автопостинг»); may this person edit and publish it */
  topic: {
    id: number
    title: string
    importance: number | null
  } | null
  /** why «Автопостинг» left it a suggestion, while it is one */
  autopost_note: string | null
  /** an issue of a rubric («Рубрики») */
  rubric: { id: number; name: string } | null
  /** written by its author themselves through the API or the MCP connector, not the AI: who */
  written_by: string | null
  /** «Обложка»: on, its picture, its title (a person's, or the post's first line) */
  cover: boolean
  cover_url: string | null
  cover_title: string
  /** a published one: «Изменить в канале» / «Удалить из канала» — may each be done, and why not; the limit of its
   * text there (a caption 1024, a message 4096) */
  channel_actions: {
    edit: boolean
    why_edit: string | null
    delete: boolean
    why_delete: string | null
    limit: number
  } | null
  /** «изменён»: its text changed in the channel */
  published_edited_at: string | null
  /** «Удалён из канала»: taken back, when and by whom */
  withdrawn_at: string | null
  withdrawn_by: string | null
  published_by: string | null
  scheduled_by: string | null
  editable: boolean
  /** a scheduled publishing that did not happen: why, and when it was to be */
  publish_error: string | null
  publish_error_at: string | null
  /** Telegram's limit for its text (1024 under pictures, 4096 without) and its length as Telegram counts it */
  limit: number
  length: number
  max_media: number
}

export interface ChatMessageOut {
  id: number
  /** error: the answer did not come (with what OpenRouter said in detail); post: a post the AI wrote */
  role: 'user' | 'assistant' | 'error' | 'post'
  text: string
  post?: ChatPostOut
  detail?: string | null
  /** an answer: when it was ready */
  at: string
  /** an answer: how long it took, from the question to the last letter (older answers do not know) */
  took_ms?: number | null
}

export interface ChatThreadOut {
  id: number
  title: string
  created_at: string
  /** the model this chat keeps: the owner's only */
  model: string | null
  busy: boolean
  /** the answer being written right now (the AI goes on when the page is closed) */
  pending: { text: string; status: string | null; elapsed: number } | null
  messages: ChatMessageOut[]
}

/** What comes while the AI answers. */
export type ChatEvent =
  | { type: 'status'; text: string }
  | { type: 'delta'; text: string }
  | { type: 'done'; message_id: number }
  | { type: 'error'; text: string; detail?: string | null }

export interface TeamState {
  items: TeamMember[]
  /** null = no limit */
  limit: number | null
  has_plan: boolean
}

export interface WebUser {
  id: number
  tg_user_id: number
  name: string | null
  username: string | null
  projects: WebProject[]
  blocked: boolean
  /** the account's money, USD */
  balance: number
  created_at: string
  last_seen_at: string | null
}

/** Admin: someone's project that tracks a channel. */
export interface ChannelFollower {
  user: { id: number; name: string | null; username: string | null }
  project: { id: number; title: string }
  /** the project's own channel */
  own: boolean
  added_at: string
}

/** A user's project as the admin sees it. */
export interface WebProject {
  id: number
  title: string
  /** the plan in force; null when none or expired */
  plan: Plan | null
  /** a plan that ran out */
  expired_plan: Plan | null
  /** channels the project tracks */
  channels: number
  /** its staff accounts */
  members: number
  created_at: string
}

export type TgLoginStatus = 'pending' | 'ok' | 'expired' | 'declined'

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    /** what the server told besides the message (a detail given as {message, ...}) */
    public info: Record<string, unknown> = {},
  ) {
    super(message)
  }
}

type Listener = () => void
const unauthorizedListeners = new Set<Listener>()
export function onUnauthorized(fn: Listener) {
  unauthorizedListeners.add(fn)
  return () => {
    unauthorizedListeners.delete(fn)
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const isForm = body instanceof FormData
  const res = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: body === undefined || isForm ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
  })
  if (res.status === 401 && !path.startsWith('/api/auth/login')) {
    unauthorizedListeners.forEach((fn) => fn())
  }
  if (!res.ok) {
    let message = `Ошибка ${res.status}`
    let info: Record<string, unknown> = {}
    try {
      const data = await res.json()
      if (typeof data.detail === 'string') message = data.detail
      else if (Array.isArray(data.detail) && data.detail[0]?.msg) message = data.detail[0].msg
      else if (typeof data.detail?.message === 'string') {
        message = data.detail.message
        info = data.detail
      }
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, message, info)
  }
  return res.json() as Promise<T>
}

export interface PostQuery {
  period?: Period
  sort?: Sort
  channel_id?: number
  q?: string
  offset?: number
  limit?: number
  /** only what the smart feed kept ("true"), or what it turned down ("rejected") */
  smart?: 'true' | 'rejected'
  /** «Триггер-слова»: the posts of one of the person's triggers */
  trigger?: number
  /** «Избранное»: the posts the person starred */
  favorites?: boolean
}

/** «Триггер-слова»: words to watch for in the project's channels, without the AI. */
export interface Trigger {
  id: number
  name: string
  words: string[]
  /** forms: the word in any of its forms; exact: as written */
  mode: 'forms' | 'exact'
  /** all: every channel the project tracks (now and later) but channel_ids; only: just channel_ids */
  channels_mode: 'all' | 'only'
  channel_ids: number[]
  notify: boolean
  notify_dm: boolean
  /** one of the project's groups («Группы проекта») the bot tells too */
  notify_chat_id: number | null
  /** posts after the person last looked */
  new: number
  total: number
  created_at: string
}

export type TriggerIn = Omit<Trigger, 'id' | 'new' | 'total' | 'created_at'>

export interface TriggersState {
  items: Trigger[]
  /** the bot can write to the person (a staff member links their Telegram first) */
  telegram_linked: boolean
  can_groups: boolean
  /** the project's triggers (everyone's), and what its plan allows: null = no limit */
  used: number
  limit: number | null
}

export type QrLoginStatus = 'pending' | 'scanned' | 'declined' | 'expired' | 'ok'

/** What a device is, as a person knows it: «iPhone», «Safari 18.1». */
export interface DeviceKind {
  name: string
  app: string
  platform: 'ios' | 'android' | 'windows' | 'mac' | 'linux' | 'other'
  form: 'phone' | 'tablet' | 'computer'
}

export interface Device extends DeviceKind {
  id: string
  /** «Belgrade, Serbia», by the IP; null when unknown */
  place: string | null
  ip: string | null
  created_at: string
  last_seen_at: string
  online: boolean
}

export interface QrAsking extends DeviceKind {
  place: string | null
  ip: string | null
}

export const api = {
  me: () => request<Me>('GET', '/api/auth/me'),
  login: (username: string, password: string) =>
    request<{ user: string }>('POST', '/api/auth/login', { username, password }),
  logout: () => request('POST', '/api/auth/logout'),
  memberLogin: (login: string, password: string) => request('POST', '/api/members/login', { login, password }),
  team: () => request<TeamState>('GET', '/api/members'),
  channelStats: (id: number, period: StatsPeriod) =>
    request<ChannelStats>('GET', `/api/channels/${id}/stats?period=${period}&tz=${-new Date().getTimezoneOffset()}`),
  /** «Публикации»: what went out or is to, and the AI's posts by topics; the settings for the owner only */
  publications: (filter: PublicationsFilter, offset = 0) =>
    request<{ items: ChatPostOut[]; has_more: boolean; settings: PublicationSettings | null }>(
      'GET',
      `/api/publications?filter=${filter}&offset=${offset}`,
    ),
  savePublicationSettings: (
    values: Partial<Omit<PublicationSettings, 'limits' | 'hours' | 'gaps' | 'daily_limits' | 'sources_options'>>,
  ) => request<PublicationSettings>('PUT', '/api/publications/settings', values),
  introSeen: (key: string) => request<{ intros_seen: string[] }>('POST', '/api/auth/intros', { key }),
  publicationsCalendar: (start: string, end: string) =>
    request<{ items: CalendarItem[] }>(
      'GET',
      `/api/publications/calendar?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
    ),
  /** a day of the calendar: its posts, by the clock */
  publicationsDay: (start: string, end: string) =>
    request<{ items: ChatPostOut[]; has_more: boolean }>(
      'GET',
      `/api/publications?limit=50&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
    ),
  covers: () => request<CoverSettings>('GET', '/api/covers'),
  saveCovers: (values: Partial<CoverSettings>) => request<CoverSettings>('PUT', '/api/covers', values),
  uploadCoverLogo: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<CoverSettings>('POST', '/api/covers/logo', form)
  },
  deleteCoverLogo: () => request<CoverSettings>('DELETE', '/api/covers/logo'),
  /** a post's «Обложка» on or off, and its title */
  setCover: (id: number, on: boolean, title?: string) =>
    request<ChatPostOut>('PUT', `/api/chat-posts/${id}/cover`, { on, title }),
  rubrics: () => request<RubricsState>('GET', '/api/rubrics'),
  createRubric: (preset: string) => request<Rubric>('POST', '/api/rubrics', { preset }),
  saveRubric: (
    id: number,
    values: Partial<
      Pick<
        Rubric,
        | 'name'
        | 'enabled'
        | 'source'
        | 'period_hours'
        | 'max_items'
        | 'min_importance'
        | 'channel_ids'
        | 'prompt'
        | 'header'
        | 'footer'
        | 'picture'
        | 'mode'
      > & { schedule: AiSchedule; words: string | string[] }
    >,
  ) => request<Rubric>('PUT', `/api/rubrics/${id}`, values),
  deleteRubric: (id: number) => request<{ ok: boolean }>('DELETE', `/api/rubrics/${id}`),
  /** «Сделать пример сейчас»: an issue written at once, only to look at */
  rubricExample: (id: number) => request<{ text: string }>('POST', `/api/rubrics/${id}/example`),
  importanceExamples: () => request<{ bands: ImportanceBand[] }>('GET', '/api/publications/importance'),
  /** an AI's post by a topic nobody wants */
  deleteChatPost: (id: number) => request<{ ok: boolean }>('DELETE', `/api/chat-posts/${id}`),
  /** «Изменить в канале»: the published post's text changed there by the bot */
  editInChannel: (id: number, text: string) => request<ChatPostOut>('PUT', `/api/chat-posts/${id}/channel`, { text }),
  /** «Удалить из канала»: the bot deletes the published post from the channel */
  withdrawChatPost: (id: number) => request<ChatPostOut>('DELETE', `/api/chat-posts/${id}/channel`),
  chats: () => request<ChatList>('GET', '/api/chats'),
  /** `topicId`: «Создать об этом пост для моего канала» — the chat's first answer is a post on it */
  newChat: (topicId?: number) =>
    request<{ id: number }>('POST', '/api/chats', topicId ? { topic_id: topicId } : undefined),
  /** «Оформить как пост»: an answer of the AI becomes a post */
  chatPostFromMessage: (messageId: number) => request<ChatPostOut>('POST', `/api/chat-posts/from-message/${messageId}`),
  /** the person's edit: the text (Telegram's HTML) and the pictures chosen, in order */
  editChatPost: (id: number, body: { text?: string; media?: { post: number; slot: number }[] }) =>
    request<ChatPostOut>('PATCH', `/api/chat-posts/${id}`, body),
  /** «N источников»: the posts it was written from, as the feed shows them */
  chatPostSources: (id: number) => request<{ items: Post[] }>('GET', `/api/chat-posts/${id}/sources`),
  /** the bot publishes it in the project's channel; when some pictures cannot go, nothing is published until asked
   * again with `partial` */
  publishChatPost: (id: number, partial = false) =>
    request<{ published: boolean; url?: string; skipped: string[] }>('POST', `/api/chat-posts/${id}/publish`, {
      partial,
    }),
  /** «Отложенная публикация»: the bot publishes it then; checked now, what of the pictures cannot go is asked about
   * as for publishing */
  scheduleChatPost: (id: number, at: string, partial = false) =>
    request<{ scheduled: boolean; scheduled_at?: string; skipped?: string[] }>(
      'PUT',
      `/api/chat-posts/${id}/schedule`,
      { at, partial },
    ),
  unscheduleChatPost: (id: number) => request<{ ok: boolean }>('DELETE', `/api/chat-posts/${id}/schedule`),
  /** the bot sends the post to the person's own Telegram or (chatId) one of the owner's groups; what of its
   * pictures could not go, and the group's name */
  sendChatPost: (id: number, chatId?: number) =>
    request<{ ok: boolean; skipped: string[]; to: string | null }>(
      'POST',
      `/api/chat-posts/${id}/send`,
      chatId ? { chat_id: chatId } : undefined,
    ),
  triggers: () => request<TriggersState>('GET', '/api/triggers'),
  addTrigger: (t: TriggerIn) => request<TriggersState & { id: number }>('POST', '/api/triggers', t),
  saveTrigger: (id: number, t: TriggerIn) => request<TriggersState>('PUT', `/api/triggers/${id}`, t),
  deleteTrigger: (id: number) => request<TriggersState>('DELETE', `/api/triggers/${id}`),
  triggerSeen: (id: number) => request('POST', `/api/triggers/${id}/seen`),
  triggerChannels: () =>
    request<{ items: { id: number; title: string; username: string | null; avatar_url: string | null }[] }>(
      'GET',
      '/api/triggers/channels',
    ),
  /** the blue badges: what came since the person last looked */
  badges: () => request<Badges>('GET', '/api/badges'),
  badgeSeen: (kind: 'feed' | 'topics' | 'posts') => request('POST', '/api/badges/seen', { kind }),
  /** how the chats the page waits on stand (ChatNotice) */
  chatsStatus: (ids: number[]) =>
    request<{ items: { id: number; title: string; busy: boolean; failed: boolean; last: string }[] }>(
      'GET',
      `/api/chats/status?ids=${ids.join(',')}`,
    ),
  chat: (id: number) => request<ChatThreadOut>('GET', `/api/chats/${id}`),
  deleteChat: (id: number) => request('DELETE', `/api/chats/${id}`),
  /** «Создать об этом пост для моего канала»: the person's template ({тема}, {номер}) */
  postPrompt: () => request<PostPrompt>('GET', '/api/chats/post-prompt'),
  /** null: back to the default */
  setPostPrompt: (template: string | null) => request<PostPrompt>('PUT', '/api/chats/post-prompt', { template }),
  renameChat: (id: number, title: string) => request<{ title: string }>('PATCH', `/api/chats/${id}`, { title }),
  /** Sends a message (or, with text null, the last one again); the answer comes piece by piece to onEvent. */
  sendChat: async (id: number, text: string | null, onEvent: (e: ChatEvent) => void, signal?: AbortSignal) => {
    const tz = -new Date().getTimezoneOffset()
    const res = await fetch(text === null ? `/api/chats/${id}/retry` : `/api/chats/${id}/messages`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(text === null ? { tz } : { text, tz }),
      signal,
    })
    if (!res.ok || !res.body) {
      let message = `Ошибка ${res.status}`
      try {
        const data = await res.json()
        if (typeof data.detail === 'string') message = data.detail
      } catch {
        /* not json */
      }
      throw new ApiError(res.status, message)
    }
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      for (const line of lines) if (line.trim()) onEvent(JSON.parse(line) as ChatEvent)
    }
    if (buffer.trim()) onEvent(JSON.parse(buffer) as ChatEvent)
  },
  topics: (offset = 0, limit = 20, hidden = false) =>
    request<{ items: Topic[]; has_more: boolean }>(
      'GET',
      `/api/topics?offset=${offset}&limit=${limit}${hidden ? '&hidden=true' : ''}`,
    ),
  hideTopic: (id: number, reason: string) => request('POST', `/api/topics/${id}/hide`, { reason }),
  unhideTopic: (id: number) => request('POST', `/api/topics/${id}/unhide`),
  topic: (id: number) => request<TopicDetail>('GET', `/api/topics/${id}`),
  topicNotCovered: (id: number) => request('POST', `/api/topics/${id}/not-covered`),
  topicCovered: (id: number) => request('POST', `/api/topics/${id}/covered`),
  topicSettings: () => request<TopicSettings>('GET', '/api/topics/settings'),
  saveTopicSettings: (values: { enabled?: boolean; prompt?: string; reset_prompt?: boolean; schedule?: AiSchedule }) =>
    request<TopicSettings>('PUT', '/api/topics/settings', values),
  topicNotify: (on: boolean) => request<TopicSettings>('PUT', '/api/topics/notify', { on }),
  runTopics: () => request<AiRun | null>('POST', '/api/topics/run'),
  topicsRun: () => request<AiRun | null>('GET', '/api/topics/run'),
  smart: () => request<SmartState>('GET', '/api/smart'),
  saveSmart: (values: { enabled?: boolean; prompt?: string; reset_prompt?: boolean }) =>
    request<SmartState>('PUT', '/api/smart', values),
  /** «Скрыть» a post the smart feed kept, or «Показать» one it turned down, with why */
  smartCorrect: (postId: number, show: boolean, reason: string) =>
    request('POST', `/api/smart/posts/${postId}/correct`, { show, reason }),
  smartUncorrect: (postId: number) => request('DELETE', `/api/smart/posts/${postId}/correct`),
  ai: () => request<AiState>('GET', '/api/ai'),
  aiAcceptWarning: () => request<AiState>('POST', '/api/ai/accept-warning'),
  aiConnect: () => request<{ url: string }>('POST', '/api/ai/connect', { origin: location.origin }),
  aiUnlink: () => request<AiState>('DELETE', '/api/ai'),
  saveAi: (values: {
    models?: Partial<Record<AiOperation, string | null>>
    monthly_limit_usd?: number
    clear_monthly_limit?: boolean
  }) => request<AiState>('PUT', '/api/ai', values),
  aiModels: (operation: AiOperation) =>
    request<{ default: string; items: AiModel[] }>('GET', `/api/ai/models?operation=${operation}`),
  aiUsage: (period: AiUsagePeriod) =>
    request<AiUsage>('GET', `/api/ai/usage?period=${period}&tz=${-new Date().getTimezoneOffset()}`),
  addMember: (login: string, rights: MemberRights) =>
    request<{ member: TeamMember; password: string }>('POST', '/api/members', { login, ...rights }),
  patchMember: (id: number, rights: Partial<MemberRights>) =>
    request<TeamMember>('PATCH', `/api/members/${id}`, rights),
  resetMemberPassword: (id: number) =>
    request<{ member: TeamMember; password: string }>('POST', `/api/members/${id}/password`),
  deleteMember: (id: number) => request('DELETE', `/api/members/${id}`),
  /** «Файл-список отслеживаемых каналов»: the bot sends channel-list.md to the person's Telegram */
  channelListFile: () => request<{ ok: boolean; count: number }>('POST', '/api/channels/list-file'),
  linkTelegram: () => request<{ link: string; expires_in: number }>('POST', '/api/members/me/telegram'),
  unlinkTelegram: () => request('DELETE', '/api/members/me/telegram'),
  tgLoginStart: () => request<{ link: string; expires_in: number }>('POST', '/api/auth/tg/start'),
  tgLoginStatus: () => request<{ status: TgLoginStatus }>('GET', '/api/auth/tg/status'),
  /** a QR code for the login page; a phone signed in scans it (Settings → «Устройства») */
  qrLoginStart: () => request<{ token: string; expires_in: number }>('POST', '/api/auth/qr/start'),
  qrLoginStatus: () => request<{ status: QrLoginStatus }>('GET', '/api/auth/qr/status'),
  /** Settings → «Устройства»: this device and the person's others */
  devices: () => request<{ current: Device | null; others: Device[] }>('GET', '/api/devices'),
  /** what the browser tells about itself beyond its user agent, so the list names it better */
  deviceHints: (hints: { model?: string | null; os_version?: string | null; standalone: boolean }) =>
    request('PUT', '/api/devices/current', hints),
  terminateDevice: (id: string) => request('DELETE', `/api/devices/${encodeURIComponent(id)}`),
  terminateOtherDevices: () => request<{ ok: boolean; count: number }>('POST', '/api/devices/terminate-others'),
  /** the phone scanned a login page's QR code: which browser asks to sign in */
  qrScanned: (token: string) => request<QrAsking>('GET', `/api/devices/qr/${encodeURIComponent(token)}`),
  qrConfirm: (token: string) => request('POST', `/api/devices/qr/${encodeURIComponent(token)}`),
  qrDecline: (token: string) => request('POST', `/api/devices/qr/${encodeURIComponent(token)}/decline`),

  users: () => request<{ items: WebUser[] }>('GET', '/api/users'),
  patchUser: (id: number, body: { blocked: boolean }) => request<WebUser>('PATCH', `/api/users/${id}`, body),
  channelFollowers: (channelId: number) =>
    request<{ items: ChannelFollower[] }>('GET', `/api/channels/${channelId}/followers`),
  userProjectChannels: (userId: number, projectId: number) =>
    request<{ items: (Channel & { own: boolean; added_at: string })[] }>(
      'GET',
      `/api/users/${userId}/projects/${projectId}/channels`,
    ),
  patchProject: (userId: number, projectId: number, body: { plan?: string; expires_at?: string | null }) =>
    request<WebUser>('PATCH', `/api/users/${userId}/projects/${projectId}`, body),
  promoCodes: () => request<{ items: PromoCode[]; plans: Plan[] }>('GET', '/api/promo'),
  generatePromo: (body: { plan: string; days: number | null; count: number }) =>
    request<{ items: PromoCode[] }>('POST', '/api/promo', body),
  deletePromo: (code: string) => request('DELETE', `/api/promo/${encodeURIComponent(code)}`),
  redeemPromo: (code: string) => request<{ plan: Plan }>('POST', '/api/promo/redeem', { code }),

  /** the plans page: before signing in too */
  plans: () => request<{ items: Plan[]; trial_days: number }>('GET', '/api/plans'),
  billing: () => request<BillingState>('GET', '/api/billing'),
  buyPlan: (plan: PlanId) =>
    request<BillingState & { done: { started?: PlanId; next?: PlanId; kept?: PlanId } }>('POST', '/api/billing/buy', {
      plan,
    }),
  patchBilling: (values: { auto_renew?: boolean; ai_extra?: boolean; ai_extra_cap?: number; clear_ai_extra_cap?: boolean }) =>
    request<BillingState>('PATCH', '/api/billing', values),
  topup: () => request('POST', '/api/billing/topup'),
  billingHistory: (before?: number) =>
    request<{ items: BillingOp[]; more: boolean }>('GET', `/api/billing/history${before ? `?before=${before}` : ''}`),
  serviceAi: () => request<ServiceAi>('GET', '/api/billing/admin'),
  patchServiceAi: (values: { markup_pct?: number; models?: Record<string, string | null> }) =>
    request<ServiceAi>('PATCH', '/api/billing/admin', values),
  connectServiceAi: () =>
    request<{ url: string }>('POST', '/api/billing/admin/connect', { origin: location.origin }),
  unlinkServiceAi: () => request<ServiceAi>('DELETE', '/api/billing/admin/key'),
  goLive: () => request<ServiceAi & { trials_started: number }>('POST', '/api/billing/admin/live'),
  adminMoney: (userId: number, amount: number, note: string) =>
    request<{ balance: number }>('POST', `/api/billing/admin/users/${userId}/balance`, { amount, note }),
  adminPlans: () => request<{ items: (Plan & { forever: boolean; by_promo: boolean })[] }>('GET', '/api/billing/admin/plans'),

  posts: (query: PostQuery) => {
    const params = new URLSearchParams()
    Object.entries(query).forEach(([k, v]) => {
      if (v !== undefined && v !== '') params.set(k, String(v))
    })
    return request<{ items: Post[]; has_more: boolean; smart?: boolean }>('GET', `/api/posts?${params}`)
  },

  /** «Избранное»: star a post, or take the star off */
  star: (postId: number, on: boolean) =>
    request<{ favorite: boolean }>(on ? 'PUT' : 'DELETE', `/api/posts/${postId}/favorite`),

  channels: () => request<{ items: Channel[]; limit?: number | null }>('GET', '/api/channels'),
  setChannelPaused: (id: number, paused: boolean) =>
    request<{ paused: boolean }>('PUT', `/api/channels/${id}/paused`, { paused }),
  addChannels: (links: string) =>
    request<{ added: Channel[]; duplicates: string[]; invalid: string[]; over_limit: string[]; limit: number | null }>(
      'POST',
      '/api/channels',
      { links },
    ),
  /** «Удалить списком»: the project stops tracking the channels of the links; `dry`: only how many would go */
  removeChannels: (links: string, dry = false) =>
    request<{ removed: number; not_found: string[]; invalid: string[]; own: string[] }>(
      'POST',
      '/api/channels/remove',
      {
        links,
        dry,
      },
    ),
  /** `fresh`: the link given before stopped playing (it lives about three hours) */
  post: (id: number) => request<Post>('GET', `/api/posts/${id}`),
  /** the post's comments after the one with this id (0: from the first), oldest first */
  comments: (postId: number, after = 0) => request<CommentsPage>('GET', `/api/posts/${postId}/comments?after=${after}`),
  commentMedia: (postId: number, commentId: number, what: 'thumb' | 'full' | 'file') =>
    `/api/posts/${postId}/comments/${commentId}/media?what=${what}`,
  /** for VideoViewer: where to ask for the link of a comment's video or GIF */
  commentVideo: (postId: number, commentId: number) => `/api/posts/${postId}/comments/${commentId}/video?v=1`,
  video: (videoUrl: string, fresh = false) => request<VideoSource>('GET', fresh ? `${videoUrl}&fresh=true` : videoUrl),
  sendOriginal: (postId: number, chatId?: number) =>
    request<{ ok: boolean; to: string | null }>(
      'POST',
      `/api/posts/${postId}/original`,
      chatId ? { chat_id: chatId } : undefined,
    ),
  groups: () => request<GroupsState>('GET', '/api/groups'),
  projects: () => request<ProjectsState>('GET', '/api/projects'),
  createProject: () => request<{ id: number }>('POST', '/api/projects'),
  openProject: (id: number) => request('POST', `/api/projects/${id}/open`),
  deleteProject: (id: number) => request('DELETE', `/api/projects/${id}`),
  projectChannels: () =>
    request<{ items: CandidateChannel[]; bot_username: string | null }>('GET', '/api/projects/channels'),
  /** any channel by its link or @username, or one of the channels with the bot */
  setProjectChannel: (projectId: number, target: { link: string } | { chat_id: number }) =>
    request<ProjectsState>('PUT', `/api/projects/${projectId}/channel`, target),
  addGroup: (body: { link?: string; chat_id?: number }) => request<GroupsState>('POST', '/api/groups', body),
  deleteGroup: (chatId: number) => request<GroupsState>('DELETE', `/api/groups/${chatId}`),
  channelAction: (id: number, action: 'pause' | 'resume' | 'retry') =>
    request<Channel>('PATCH', `/api/channels/${id}`, { action }),
  deleteChannel: (id: number) => request('DELETE', `/api/channels/${id}`),

  accounts: () => request<{ items: Account[] }>('GET', '/api/accounts'),
  loginStart: (body: { phone: string; api_id?: number; api_hash?: string }) =>
    request<{ login_id: string }>('POST', '/api/accounts/login/start', body),
  loginCode: (login_id: string, code: string) =>
    request<{ status: 'ok' | 'password_needed' }>('POST', '/api/accounts/login/code', { login_id, code }),
  loginPassword: (login_id: string, password: string) =>
    request<{ status: 'ok' }>('POST', '/api/accounts/login/password', { login_id, password }),
  loginCancel: (login_id: string) => request('POST', '/api/accounts/login/cancel', { login_id }),
  importSession: (body: { session: string; api_id?: number; api_hash?: string }) =>
    request('POST', '/api/accounts/import', body),
  patchAccount: (id: number, body: Partial<Pick<Account, 'enabled' | 'max_channels'>>) =>
    request<Account>('PATCH', `/api/accounts/${id}`, body),
  changeAccountProxy: (id: number) => request<Account>('POST', `/api/accounts/${id}/change-proxy`),
  importAccountFiles: (files: File[]) => {
    const form = new FormData()
    files.forEach((f) =>
      form.append('files', f, (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name),
    )
    return request<ImportResult>('POST', '/api/accounts/import-files', form)
  },

  apiKey: () => request<{ key: string; rate_limit: number }>('GET', '/api/api-key'),
  resetApiKey: () => request<{ key: string; rate_limit: number }>('POST', '/api/api-key/reset'),
  apiReference: () => request<ApiReference>('GET', '/api/api-key/reference'),
  mcpTools: () => request<{ items: McpTool[] }>('GET', '/api/mcp/tools'),
  mcpConnections: () => request<{ items: McpConnection[] }>('GET', '/api/mcp/connections'),
  mcpDisconnect: (id: number) => request<{ ok: true }>('DELETE', `/api/mcp/connections/${id}`),
  mcpRequest: (requestToken: string) => request<McpRequest>('POST', '/api/mcp/request', { request: requestToken }),
  mcpApprove: (requestToken: string, allow: boolean) =>
    request<{ redirect: string }>('POST', '/api/mcp/approve', { request: requestToken, allow }),

  notify: () => request<NotifyState>('GET', '/api/notify'),
  removeNotifyChat: (chatId: number) => request<NotifyState>('DELETE', `/api/notify/chats/${chatId}`),
  setNotifyToken: (token: string) => request<NotifyState>('PUT', '/api/notify/token', { token }),
  testNotify: () => request<{ sent: number }>('POST', '/api/notify/test'),
  deleteAccount: (id: number) => request('DELETE', `/api/accounts/${id}`),

  proxies: () => request<{ items: Proxy[] }>('GET', '/api/proxies'),
  addProxies: (text: string) =>
    request<{ added: Proxy[]; working: number; duplicates: string[]; invalid: { line: string; error: string }[] }>(
      'POST',
      '/api/proxies',
      { text },
    ),
  checkProxy: (id: number) => request<Proxy>('POST', `/api/proxies/${id}/check`),
  patchProxy: (id: number, enabled: boolean) => request<Proxy>('PATCH', `/api/proxies/${id}`, { enabled }),
  deleteProxy: (id: number) => request('DELETE', `/api/proxies/${id}`),

  settings: () => request<Settings>('GET', '/api/settings'),
  saveSettings: (values: Partial<Settings>) => request<Settings>('PUT', '/api/settings', values),
  status: () => request<Status>('GET', '/api/status'),
  storage: () => request<Storage>('GET', '/api/storage'),
  perf: (period: PerfPeriod) => request<Perf>('GET', `/api/perf?period=${period}`),
  dismissRepostError: () => request('POST', '/api/status/dismiss-repost-error'),
  failedReposts: (offset = 0, limit = 50) =>
    request<{ items: FailedRepost[]; total: number; has_more: boolean }>(
      'GET',
      `/api/reposts/failed?offset=${offset}&limit=${limit}`,
    ),
}

/** A parameter or a body field of a method, as the server's reference gives it (Settings → API). */
export interface ApiField {
  name: string
  type: string
  required: boolean
  nullable: boolean
  options: (string | number)[] | null
  default?: unknown
  in?: 'path' | 'query'
}

/** One of the site's methods a key may call: what it does, its parameters and body. */
export interface ApiRefMethod {
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  path: string
  text: string
  params: ApiField[]
  body: ApiField[] | null
  body_kind: 'json' | 'form-data' | null
}

/** The methods this person's key may call, by section — only those their rights allow. */
export interface ApiReference {
  rate_limit: number
  groups: { title: string; methods: ApiRefMethod[] }[]
}

/** A tool of the MCP connector this person's AI app would get. */
export interface McpTool {
  name: string
  group: string
  description: string
  read_only: boolean
}

/** «Подключённые приложения»: an AI app this person let in through the MCP connector. */
export interface McpConnection {
  id: number
  client_name: string
  redirect_host: string
  created_at: string
  last_used_at: string | null
}

/** The page that asks to allow an AI app (#/connect/<request>): which app, and as whom it will act. */
export interface McpRequest {
  client_name: string
  redirect_host: string
  project_title: string
  role: 'user' | 'member'
  login: string | null
  has_api: boolean
}
