/**
 * Reference of the external API shown in Settings → API. Two parts: the data at /api/v1, read only — written here,
 * keep in sync with backend/app/web/public_api.py; and managing everything as on the site — the site's own methods,
 * whose reference the server gives (backend/app/web/api_routes.py), only those the key's holder may call.
 */
import type { ApiField, ApiRefMethod, ApiReference } from '../api'

export interface ApiMethod {
  path: string
  title: string
  description: string
  params?: [string, string][]
  /** query part appended to the path in the curl example */
  example?: string
  response?: string
}

export const POST_FIELDS: [string, string][] = [
  ['id', 'номер поста в мониторинге (растёт по мере сбора)'],
  ['channel', 'id, title, username, url канала'],
  ['msg_id, url', 'номер сообщения в канале и ссылка на оригинал в t.me'],
  ['repost_url', 'ссылка на пересланную копию в группе-агрегаторе или null, если пост ещё не переслан'],
  ['deleted_at', 'когда стало известно, что пост удалён в канале (цифры после этого не обновляются), или null'],
  ['posted_at', 'время публикации, UTC (ISO 8601)'],
  ['text', 'текст поста без форматирования'],
  [
    'extracted_text_from_media',
    'текст, распознанный на картинках поста (OCR по всем превью, строки по порядку): "" — текста на них нет; null — пост собран до появления распознавания или ещё в очереди (обычно это секунды после публикации)',
  ],
  ['media_type, media_count', 'photo, video, gif, document, poll, voice, audio, webpage или null; число медиа в альбоме'],
  ['preview_url', 'путь к превью-картинке (тот же ключ) или null'],
  ['previews', 'превью каждого элемента альбома: url и kind (photo, video или other)'],
  ['metrics.views / reactions / comments / forwards', 'просмотры, реакции всего, комментарии, репосты'],
  ['metrics.er', '(реакции + комментарии + репосты) / просмотры, %'],
  ['metrics.fr', 'репосты / просмотры, %: какая доля увидевших пост поделилась им (наш репост в группу-агрегатор не считается)'],
  ['metrics.fr_plus', 'FR поста относительно среднего FR последних 100 постов этого канала старше суток: 1 — как обычно, 3 — в три раза лучше; 0, пока истории мало'],
  ['reactions', 'топ реакций: emoji и count (custom — свои эмодзи, paid — звёзды)'],
  ['rated', 'true, когда просмотры достигли 1% подписчиков канала — с этого момента пост участвует в рейтингах er, fr и fr_plus'],
  ['metrics_updated_at', 'когда цифры обновлялись: до 2 мин после публикации — каждые 10 с, до 5 мин — 20 с, до 10 мин — 30 с, до 20 мин — раз в минуту, до часа — раз в 2 мин, до 6 ч — раз в 5 мин, до суток — раз в 15 мин, до 7 дней — раз в час, потом не обновляются'],
]

export const ERRORS: [string, string][] = [
  ['401 missing_key / invalid_key', 'нет ключа или он неверный (ключ могли сбросить)'],
  ['403 blocked', 'доступ к аккаунту закрыт администратором'],
  ['403 no_api', 'нет действующего тарифа с доступом к API'],
  ['404 not_found / unknown_channel / no_preview', 'нет такого поста, канала, метода или превью'],
  ['422 invalid_params', 'неверный параметр — в message сказано какой'],
  ['429 rate_limited', 'больше 120 запросов в минуту (на все методы вместе); заголовок Retry-After — через сколько секунд можно снова'],
]

/** A project key only: the channel the project is about, and why that matters. */
export const MY_CHANNEL = {
  intro: [
    'Каждый ключ принадлежит проекту, а у проекта есть свой Telegram-канал — тот, который вы развиваете. Его описывает поле my_channel (в /project, /channels и /stats). Пока канал не выбран в настройках проекта, my_channel равно null.',
    'Все данные мониторинга нужны для развития этого канала: остальные каналы — конкуренты, источники и ориентиры. Поэтому у каждого поста и канала есть флаг is_my_channel, а посты своего канала можно получить через channel=my.',
  ],
  fields: [
    ['title, username, url', 'канал проекта'],
    ['telegram_id', 'id канала в Telegram'],
    ['channel_id', 'id канала в мониторинге — для /posts?channel= и /channels/{id}'],
    ['collected', 'собираются ли уже его посты: только что выбранный канал сначала подключается'],
    ['subscribers', 'подписчики'],
    ['stats_7d', 'средние за 7 дней: posts_per_day, avg_views, avg_er, avg_fr — ориентир, с которым удобно сравнивать посты'],
  ] as [string, string][],
  scenarioTitle: 'Пример: новостной пост в стиле своего канала',
  scenario: [
    'GET /project — узнать свой канал (my_channel) и его средние цифры.',
    'GET /posts?channel=my&limit=100 — прочитать последние посты своего канала и понять его стиль: длину, тон, структуру, оформление, эмодзи, подписи.',
    'GET /topics?limit=10 — взять готовые темы: ИИ уже собрал посты разных каналов в новости с пересказом и важностью. Если ИИ не подключён — GET /posts?period=24h&sort=fr_plus&limit=30: то, что за сутки лучше всего зашло в других каналах (is_my_channel = false).',
    'GET /topics/{id} — хронология и посты-источники выбранной темы.',
    'Написать по ней новостной пост в стиле своего канала, а не в стиле источника, и сверить ожидания с my_channel.stats_7d или GET /project/stats.',
  ],
  forLlm:
    'Для LLM: считайте my_channel целью. Что бы вы ни анализировали — топы, реакции, темы конкурентов, — вывод должен помогать развитию этого канала и звучать его голосом.',
}

const MY_CHANNEL_SAMPLE = `"my_channel": {
    "title": "Технологии", "username": "tech_daily", "url": "https://t.me/tech_daily",
    "telegram_id": -1001234567890, "channel_id": 7, "collected": true, "subscribers": 184000,
    "stats_7d": { "posts_per_day": 3.4, "avg_views": 41200, "avg_er": 2.95, "avg_fr": 0.35 }
  }`

const POST_SAMPLE = `{
  "id": 1532,
  "channel": { "id": 7, "title": "Технологии", "username": "tech_daily", "url": "https://t.me/tech_daily" },
  "msg_id": 4810,
  "url": "https://t.me/tech_daily/4810",
  "repost_url": "https://t.me/my_aggregator/9120",
  "deleted_at": null,
  "posted_at": "2026-10-07T09:14:02Z",
  "text": "Компания представила новую линейку…",
  "extracted_text_from_media": "НОВЫЕ НОУТБУКИ\\nдо 22 часов без подзарядки",
  "media_type": "photo",
  "media_count": 1,
  "preview_url": "/api/v1/posts/1532/preview",
  "metrics": { "views": 48210, "reactions": 1320, "comments": 86, "forwards": 410,
               "er": 3.766, "fr": 0.85, "fr_plus": 2.4 },
  "reactions": [{ "emoji": "👍", "count": 900 }, { "emoji": "🔥", "count": 420 }],
  "rated": true,
  "metrics_updated_at": "2026-10-07T10:30:11Z"
}`

/** A parameter or field in a few words: its type, its options or default, whether it must be given. */
export function fieldText(f: ApiField): string {
  // a field left out is left as it is: «или null» matters only where it must be given
  const parts = [f.nullable && f.required ? `${f.type} или null` : f.type]
  if (f.options) parts.push(f.options.join(', '))
  if (f.default !== undefined && f.default !== '' && !(Array.isArray(f.default) && !f.default.length))
    parts.push(`по умолчанию ${JSON.stringify(f.default)}`)
  if (f.required) parts.push('обязательно')
  return parts.join('; ')
}

const sampleOf = (f: ApiField): unknown =>
  f.options ? f.options[0] : f.type === 'boolean' ? true : f.type === 'integer' || f.type === 'number' ? 1
    : f.type.startsWith('array') ? [] : f.type === 'object' ? {} : '…'

/** A curl call of one of the site's methods: its path with sample ids, its required query, a sample body. */
export function curlFor(origin: string, key: string, m: ApiRefMethod): string {
  let path = m.path.replace(/\{\w+\}/g, '123')
  const query = m.params.filter((p) => p.in === 'query' && p.required)
  if (query.length) path += '?' + query.map((p) => `${p.name}=${encodeURIComponent(String(sampleOf(p)))}`).join('&')
  const parts = ['curl']
  if (m.method !== 'GET') parts.push(`-X ${m.method}`)
  parts.push(`-H "X-API-Key: ${key}"`)
  if (m.body_kind === 'form-data') {
    parts.push(...(m.body ?? []).map((f) => `-F "${f.name}=@файл"`))
  } else if (m.body) {
    const shown = m.body.filter((f) => f.required)
    const fields = shown.length ? shown : m.body.slice(0, 1)
    const body = Object.fromEntries(fields.map((f) => [f.name, sampleOf(f)]))
    parts.push('-H "Content-Type: application/json"', `-d '${JSON.stringify(body)}'`)
  }
  parts.push(`"${origin}${path}"`)
  return parts.join(' ')
}

/** How the site's methods are called: the same for every key. */
export const MANAGE_INTRO = [
  'Всё, что вы делаете на сайте, можно делать и по ключу: это те же методы, которыми пользуется сам сайт, с теми же проверками прав. Ключ владельца проекта может всё, что может владелец; ключ сотрудника — только то, что ему разрешил владелец. Ниже — методы, доступные вашему ключу.',
  'Адреса начинаются с /api/ (не /api/v1). Тело запроса — JSON (для файлов — form-data), ответы — JSON, время — ISO 8601. Ошибка — код 4xx и {"detail": "…"} с объяснением по-русски: 401 — неверный ключ, 403 — нет прав на это действие или метод есть только на сайте, 404 — нет такого объекта, 409 — сейчас нельзя (сказано почему), 422 — неверный параметр, 429 — слишком много запросов (заголовок Retry-After).',
]

/**
 * The whole reference as plain text (no markup), e.g. to paste into an LLM. The key is left
 * as a placeholder so it does not end up in someone else's chat history.
 */
export function docsPlainText(origin: string, rateLimit: number, admin: boolean, manage: ApiReference | null): string {
  const key = '<API_KEY>'
  const base = `${origin}/api/v1`
  const { fields: postFields, methods, errors } = docsFor(admin)
  const fields = (rows: [string, string][]) => rows.map(([name, text]) => `- ${name}: ${text}`).join('\n')
  const parts = [
    'API мониторинга Telegram-каналов — документация',
    '',
    `Сервер: ${origin}`,
    `Авторизация: заголовок "X-API-Key: ${key}" (или "Authorization: Bearer ${key}"). ${key} — ключ из настроек мониторинга.`,
    `Лимит: не больше ${rateLimit} запросов в минуту на все методы вместе.`,
    'Две части: УПРАВЛЕНИЕ (/api/…) — всё, что можно делать на сайте; ДАННЫЕ (/api/v1/…) — посты, каналы и темы в удобном для аналитики виде, только чтение.',
    ...(admin
      ? []
      : [
          '',
          'КАНАЛ ПРОЕКТА (my_channel)',
          ...MY_CHANNEL.intro,
          'Поля my_channel:',
          fields(MY_CHANNEL.fields),
          `${MY_CHANNEL.scenarioTitle}:`,
          ...MY_CHANNEL.scenario.map((step, i) => `${i + 1}. ${step}`),
          MY_CHANNEL.forLlm,
        ]),
  ]
  if (manage) {
    parts.push('', 'УПРАВЛЕНИЕ (/api/…)', ...MANAGE_INTRO)
    for (const g of manage.groups) {
      parts.push('', g.title.toUpperCase())
      for (const m of g.methods) {
        parts.push('', `${m.method} ${m.path} — ${m.text}`)
        const params = m.params.map((p) => `- ${p.name} (${p.in === 'path' ? 'в пути' : 'в запросе'}): ${fieldText(p)}`)
        if (params.length) parts.push('Параметры:', ...params)
        if (m.body?.length)
          parts.push(m.body_kind === 'form-data' ? 'Форма (form-data):' : 'Тело (JSON):', ...m.body.map((f) => `- ${f.name}: ${fieldText(f)}`))
      }
    }
  }
  parts.push(
    '',
    'ДАННЫЕ (/api/v1/…)',
    `Базовый адрес: ${base}`,
    'Только чтение. Все методы — GET, ответы в JSON, время — UTC в формате ISO 8601.',
    'Посты хранятся ограниченный срок, потом удаляются и из API.',
    '',
    'ОШИБКИ /api/v1',
    'Ответ с кодом 4xx и телом {"error": {"code": "...", "message": "..."}}.',
    fields(errors),
    '',
    'ПОЛЯ ПОСТА',
    fields(postFields),
  )
  for (const m of methods) {
    parts.push('', `GET ${m.path} — ${m.title}`, m.description)
    if (m.params) parts.push('Параметры:', fields(m.params))
    if (m.example) parts.push('Пример:', `curl -H "X-API-Key: ${key}" "${base}${m.example}"`)
    if (m.response) parts.push('Ответ:', m.response)
  }
  return parts.join('\n')
}

export const METHODS: ApiMethod[] = [
  {
    path: '/posts',
    title: 'Посты — лента и топы',
    description: 'Список постов с фильтрами и сортировкой. Для топа за период укажите period и sort.',
    params: [
      ['period', '20m, 1h, 6h, 24h, 7d, 30d или all (по умолчанию)'],
      ['since, until', 'вместо period: границы по времени публикации, ISO 8601 (2026-10-07T00:00:00Z)'],
      ['sort', 'new (по умолчанию), er, fr, fr_plus, forwards, reactions, comments, views — по убыванию; в er, fr и fr_plus посты, у которых просмотров меньше 1% подписчиков канала, идут после остальных'],
      ['channel', 'id или @username; можно несколько: channel=a&channel=b или через запятую'],
      ['q', 'поиск по тексту поста и по тексту на его картинках'],
      ['limit, offset', 'до 100 за раз (по умолчанию 50); следующая страница — offset из next_offset'],
    ],
    example: '/posts?period=24h&sort=fr_plus&limit=10',
    response: `{
  "items": [ ${'{ …пост… }'} ],
  "has_more": true,
  "next_offset": 10
}

Пост:
${POST_SAMPLE}`,
  },
  {
    path: '/posts/new',
    title: 'Новые посты — для синхронизации',
    description:
      'Посты, собранные после указанного, по порядку. Сохраните last_id и передавайте его в after_id при следующем запросе — так ничего не потеряется и не повторится. Если has_more = true, запросите сразу ещё раз.',
    params: [
      ['after_id', 'id последнего полученного поста (0 — с самого начала)'],
      ['limit', 'до 100 (по умолчанию 100)'],
    ],
    example: '/posts/new?after_id=1500',
    response: `{ "items": [ …посты… ], "last_id": 1532, "has_more": false }`,
  },
  {
    path: '/posts/{id}',
    title: 'Один пост',
    description: 'Пост целиком: те же поля плюс html (текст с форматированием), album_ids и repost_status.',
    example: '/posts/1532',
  },
  {
    path: '/posts/{id}/preview',
    title: 'Превью поста',
    description: 'Картинка JPEG, если у поста есть фото или превью видео. Для альбома — ?i=1, ?i=2… (адреса есть в previews). Нужен тот же ключ.',
    example: '/posts/1532/preview',
  },
  {
    path: '/channels',
    title: 'Каналы',
    description: 'Все каналы мониторинга со статистикой за последние 7 дней.',
    params: [
      [
        'status',
        'active, pending, paused, error или restricted (запрещён в странах всех аккаунтов); можно несколько через запятую',
      ],
    ],
    example: '/channels?status=active',
    response: `{
  "items": [{
    "id": 7, "title": "Технологии", "username": "tech_daily", "url": "https://t.me/tech_daily",
    "subscribers": 184000, "status": "active",
    "posts_per_day": 3.4, "avg_views": 41200, "avg_er": 2.95, "avg_fr": 0.35,
    "last_post_at": "2026-10-07T09:14:02Z", "added_at": "2026-10-01T12:00:00Z"
  }]
}`,
  },
  {
    path: '/channels/{id}',
    title: 'Один канал',
    description: 'Те же поля, что в списке каналов.',
    example: '/channels/7',
  },
  {
    path: '/stats',
    title: 'Сводка',
    description: 'Каналы по статусам, посты всего и за сутки, репосты за сутки и топ-5 постов за сутки по просмотрам.',
    example: '/stats',
    response: `{
  "channels": { "active": 120, "pending": 2, "paused": 1, "error": 3, "restricted": 1, "total": 127 },
  "posts_total": 18420, "posts_24h": 640, "reposted_24h": 610,
  "top_24h": [ …посты… ],
  "generated_at": "2026-10-07T10:31:00Z"
}`,
  },
]

/** A project's key: the project and its own channel. */
const PROJECT_METHOD: ApiMethod = {
  path: '/project',
  title: 'Проект и его канал',
  description:
    'Проект, которому принадлежит ключ: его канал (my_channel), тариф и сколько каналов он отслеживает кроме своего. Начните с этого метода — он говорит, для какого канала работает весь мониторинг.',
  example: '/project',
  response: `{
  "title": "Технологии",
  ${MY_CHANNEL_SAMPLE},
  "plan": { "id": "pro", "title": "Pro", "expires_at": "2026-11-26T23:59:59Z" },
  "channels": { "tracked": 42, "limit": 200 },
  "generated_at": "2026-10-07T10:31:00Z"
}`,
}

/** A project's key: what the AI made of the channels, and the project channel's numbers over time. */
const PROJECT_STATS_METHOD: ApiMethod = {
  path: '/project/stats',
  title: 'Статистика канала проекта',
  description:
    'То же, что экран «Статистика» канала проекта на сайте: подписчики по времени, посты и их показатели по дням, лучшие часы публикации и средние отслеживаемых каналов для сравнения. Показатели поста учитываются, когда ему исполнится сутки. Нужен выбранный канал проекта.',
  params: [
    ['period', '7d, 30d (по умолчанию) или 90d'],
    ['tz', 'сдвиг вашего часового пояса от UTC в минутах (Москва — 180), чтобы дни и часы считались по-вашему; по умолчанию 0'],
  ],
  example: '/project/stats?period=30d&tz=180',
  response: `{
  ${MY_CHANNEL_SAMPLE},
  "period": "30d",
  "subscribers": [{ "t": "2026-10-01T00:00:00Z", "v": 183200 }, …],
  "days": [{ "day": "2026-10-06", "posts": 4, "avg_views": 40100, "er": 3.1, "fr": 0.4, "reach": 21.8, "subs_delta": 120 }, …],
  "hours": [{ "hour": 9, "posts": 12, "avg_views": 45300 }, …],
  "summary": { "subscribers": 184000, "subs_delta": 800, "posts": 98, "posts_per_day": 3.3,
               "avg_views": 41200, "er": 2.95, "fr": 0.35, "reach": 22.4 },
  "benchmark": { "avg_views": 18900, "er": 2.1, "fr": 0.28, "reach": 15.2, "channels": 42 },
  "collecting_since": "2026-09-20T12:00:00Z",
  "generated_at": "2026-10-07T10:31:00Z"
}

er, fr и reach — в %; reach — средние просмотры поста к подписчикам; subs_delta — прирост подписчиков (null — нет данных).`,
}

const TOPIC_SAMPLE = `{
  "id": 318,
  "title": "Вышла новая модель ИИ",
  "summary": "Компания представила модель, которая…",
  "importance": 8,
  "covered": false,
  "followup": false,
  "created_at": "2026-10-07T08:40:00Z", "updated_at": "2026-10-07T10:05:00Z", "last_post_at": "2026-10-07T09:58:00Z",
  "sources": 5,
  "channels": [{ "id": 7, "title": "Технологии", "username": "tech_daily", "url": "https://t.me/tech_daily", "is_my_channel": false }],
  "posts_count": 9,
  "post_ids": [1490, 1502, 1532],
  "views": 214000
}`

const TOPIC_METHODS: ApiMethod[] = [
  {
    path: '/topics',
    title: 'Темы',
    description:
      'Новости, которые ИИ собрал из постов отслеживаемых каналов, — как в разделе «Темы» на сайте: свежие наверху, внутри одного запуска — самые важные. Скрытые темы не отдаются. Темы появляются, когда в проекте включены «Темы».',
    params: [
      ['updated_after', 'ISO 8601: только темы, созданные или обновлённые позже, — по порядку обновления, от старых к новым; сохраните updated_at последней и передайте в следующий раз'],
      ['limit, offset', 'до 100 за раз (по умолчанию 20); следующая страница — offset из next_offset'],
    ],
    example: '/topics?limit=10',
    response: `{
  "items": [ ${'{ …тема… }'} ],
  "has_more": true,
  "next_offset": 10
}

Тема:
${TOPIC_SAMPLE}

importance — важность от 1 до 10 по мнению ИИ; covered — ваш канал уже написал об этом; followup — после вашего поста появилось важное продолжение (повод для ещё одного поста); sources — сколько разных каналов писали; post_ids — посты-источники по времени публикации.`,
  },
  {
    path: '/topics/{id}',
    title: 'Одна тема',
    description: 'Тема целиком: те же поля плюс timeline — хронология (at, text) и posts — посты-источники полностью, новые первыми.',
    example: '/topics/318',
  },
]

/** A user's key sees only their own channels, and nothing of our aggregator group. */
const USER_METHODS: Record<string, Partial<ApiMethod>> = {
  '/posts': {
    params: [
      ['period', '20m, 1h, 6h, 24h, 7d, 30d или all (по умолчанию)'],
      ['since, until', 'вместо period: границы по времени публикации, ISO 8601 (2026-10-07T00:00:00Z)'],
      ['sort', 'new (по умолчанию), er, fr, fr_plus, forwards, reactions, comments, views — по убыванию; в er, fr и fr_plus посты, у которых просмотров меньше 1% подписчиков канала, идут после остальных'],
      ['channel', 'id, @username или my — канал проекта; можно несколько: channel=a&channel=b или через запятую'],
      ['q', 'поиск по тексту поста и по тексту на его картинках'],
      [
        'smart',
        'true — только то, что показывает умная лента (как на сайте у владельца ключа). У каждого поста будет smart: decided_by — ai (решил ИИ, с reason и score 0–100), person (решил человек, с reason) или null (пост из времени до включения ленты); 409 smart_feed_off — лента выключена',
      ],
      ['limit, offset', 'до 100 за раз (по умолчанию 50); следующая страница — offset из next_offset'],
    ],
  },
  '/posts/new': {
    description:
      'Посты, собранные после указанного, по порядку. Сохраните last_id и передавайте его в after_id при следующем запросе — так ничего не потеряется и не повторится. Если has_more = true, запросите сразу ещё раз. С smart=true — только то, что оставила умная лента, и только уже проверенные ИИ посты: свежий пост придёт, когда ИИ его прочтёт (обычно в пределах минуты), а last_id может быть больше id последнего поста — отброшенные посты повторно не проверяются.',
    params: [
      ['after_id', 'id последнего полученного поста (0 — с самого начала)'],
      ['smart', 'true — только посты умной ленты, с полем smart (см. /posts)'],
      ['limit', 'до 100 (по умолчанию 100)'],
    ],
  },
  '/posts/{id}': {
    description: 'Пост целиком: те же поля плюс html (текст с форматированием) и album_ids.',
  },
  '/channels': {
    description: 'Каналы проекта со статистикой за последние 7 дней и my_channel — канал самого проекта (он тоже в списке, с is_my_channel = true).',
    params: [['status', 'active (работает), pending (подключается) или error (ошибка канала); можно несколько через запятую']],
    response: `{
  "items": [{
    "id": 7, "title": "Технологии", "username": "tech_daily", "url": "https://t.me/tech_daily",
    "is_my_channel": true, "subscribers": 184000, "status": "active",
    "posts_per_day": 3.4, "avg_views": 41200, "avg_er": 2.95, "avg_fr": 0.35,
    "last_post_at": "2026-10-07T09:14:02Z", "added_at": "2026-10-01T12:00:00Z"
  }],
  ${MY_CHANNEL_SAMPLE}
}`,
  },
  '/stats': {
    description: 'Каналы проекта по статусам, посты всего и за сутки, топ-5 постов за сутки по просмотрам и my_channel.',
    response: `{
  "channels": { "active": 120, "pending": 2, "error": 1, "total": 123 },
  "posts_total": 18420, "posts_24h": 640,
  "top_24h": [ …посты… ],
  ${MY_CHANNEL_SAMPLE},
  "generated_at": "2026-10-07T10:31:00Z"
}`,
  },
}

/** The reference as the admin or a project (its owner or staff) sees it. */
export function docsFor(admin: boolean): {
  fields: [string, string][]
  methods: ApiMethod[]
  errors: [string, string][]
} {
  if (admin) return { fields: POST_FIELDS, methods: METHODS, errors: ERRORS }
  return {
    fields: POST_FIELDS.filter(([name]) => name !== 'repost_url').map(([name, text]) =>
      name === 'channel' ? [name, 'id, title, username, url канала и is_my_channel — канал ли это самого проекта'] : [name, text],
    ),
    methods: [PROJECT_METHOD, PROJECT_STATS_METHOD, ...METHODS, ...TOPIC_METHODS].map((m) => {
      const own = { ...m, ...USER_METHODS[m.path] }
      const response = own.response
        ?.replace(/\n\s*"repost_url": [^\n]*/, '')
        .replace('"url": "https://t.me/tech_daily" },', '"url": "https://t.me/tech_daily",\n               "is_my_channel": false },')
      return response ? { ...own, response } : own
    }),
    errors: [
      ...ERRORS.slice(0, 3),
      ['404 no_my_channel', 'channel=my или /project/stats, а канал проекта не выбран'],
      ...ERRORS.slice(3, 5),
      ['409 smart_feed_off', 'smart=true, а умная лента выключена или ИИ проекта сейчас недоступен'],
      ...ERRORS.slice(5),
    ],
  }
}
