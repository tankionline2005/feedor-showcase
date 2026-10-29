"""The site's own methods by an API key: which of them a key may call, and what each does, for people.

A key acts as its holder does on the site — the project's owner, a staff member with the rights the owner gave them,
or the admin — through the very same methods and checks (deps.require_viewer). BY_KEY is the list of them: a method
not in it answers a key with 403. Each one says, in Russian, what it does — the reference in Settings → API is built
from it and the methods' own parameters (public_api.reference). SITE_ONLY are the methods that stay the site's, and
why; a test keeps every method in one of the two.

`who` — who may call it, as the site lets them: "anyone" (the admin too), "project" (the owner and every staff
member), "owner", "own_ai" (an owner on their own OpenRouter: dev:service:plan), "admin", "member" (staff only), or
a staff right (the owner always has it), several by commas:
any of them will do.
"""

from typing import NamedTuple

from ..plans import plan_of


class Method(NamedTuple):
    group: str
    who: str
    text: str


SCHEDULE = (
    "schedule — расписание: {\"rules\": [...], \"tz\": \"Europe/Moscow\"}; правило {\"kind\": \"times\", \"days\": [0..6], "
    "\"times\": [\"09:00\", \"21:00\"]} — в эти часы, или {\"kind\": \"every\", \"every_min\": 60, \"days\": [0..6], "
    "\"from\": \"10:00\" | null, \"to\": \"22:00\" | null} — через равные промежутки; дни — 0 = понедельник."
)

BY_KEY: dict[str, Method] = {
    # --- the key's holder ---
    "GET /api/auth/me": Method("Профиль и ключ", "anyone",
                               "Кто владелец ключа: role — user (владелец проекта), member (сотрудник) или admin; "
                               "project.id, тариф, can_edit_channels, ai_linked — подключён ли OpenRouter.ai; у "
                               "сотрудника в member — его права."),
    "GET /api/api-key": Method("Профиль и ключ", "anyone", "Ваш ключ API и лимит запросов в минуту."),
    "POST /api/api-key/reset": Method("Профиль и ключ", "anyone",
                                      "Новый ключ вместо этого: старый сразу перестаёт работать."),
    "GET /api/api-key/reference": Method("Профиль и ключ", "anyone",
                                         "Эта справка: методы, доступные этому ключу, с их параметрами."),
    "GET /api/badges": Method("Профиль и ключ", "project",
                              "Счётчики нового с прошлого просмотра: лента, темы, публикации, непрочитанные чаты и "
                              "триггер-слова."),
    "POST /api/badges/seen": Method("Профиль и ключ", "project",
                                    "Отметить раздел просмотренным: kind — feed, topics или posts; его счётчик "
                                    "обнулится."),
    "GET /api/live": Method("Профиль и ключ", "anyone",
                            "Поток server-sent events: событие приходит, когда посты каналов появились, удалились или "
                            "обновили цифры, — повод запросить заново то, что вы показываете."),

    # --- the feed ---
    "GET /api/posts": Method("Лента и посты", "anyone",
                             "Лента и топ, как на сайте. period — 20m, 1h, 6h, 24h, 7d, 30d или all; sort — new, er, "
                             "fr, fr_plus, forwards, reactions, comments или views; channel_id — один канал; q — поиск "
                             "по тексту и тексту на картинках; smart — true (что оставила умная лента) или rejected "
                             "(что она скрыла); trigger — посты одного триггер-слова; favorites=true — «Избранное». "
                             "offset и limit (до 100) — страницы."),
    "GET /api/posts/{post_id}": Method("Лента и посты", "anyone", "Один пост целиком."),
    "GET /api/media/{post_id}": Method("Лента и посты", "anyone",
                                       "Превью картинки поста (JPEG); i — номер элемента альбома, с 0."),
    "GET /api/media/{post_id}/full": Method("Лента и посты", "anyone",
                                            "Фото поста в полном размере (JPEG), прямо из Telegram; i — номер "
                                            "элемента альбома."),
    "GET /api/media/{post_id}/video": Method("Лента и посты", "anyone",
                                             "Ссылка на видео поста: url, kind, ratio, duration — или error и "
                                             "message, почему его нет; fresh=true — новая ссылка, если старая "
                                             "перестала открываться."),
    "POST /api/posts/{post_id}/original": Method("Лента и посты", "project",
                                                 "Бот пришлёт оригинал поста вам в Telegram или, с chat_id, в группу "
                                                 "проекта."),
    "PUT /api/posts/{post_id}/favorite": Method("Лента и посты", "project",
                                                "В «Избранное»: такой пост хранится дольше срока хранения постов."),
    "DELETE /api/posts/{post_id}/favorite": Method("Лента и посты", "project", "Убрать пост из «Избранного»."),
    "GET /api/posts/{post_id}/comments": Method("Лента и посты", "anyone",
                                                "Комментарии к посту; after — id последнего полученного, чтобы "
                                                "получить только новые."),
    "GET /api/posts/{post_id}/comments/{comment_id}/media": Method(
        "Лента и посты", "anyone", "Картинка комментария: what — thumb (превью), full или file."),
    "GET /api/posts/{post_id}/comments/{comment_id}/video": Method(
        "Лента и посты", "anyone", "Ссылка на видео комментария, как у видео поста."),

    # --- triggers ---
    "GET /api/triggers": Method("Триггер-слова", "project",
                                "Ваши триггер-слова: new — новых совпадений, total — всего."),
    "POST /api/triggers": Method("Триггер-слова", "project",
                                 "Новое триггер-слово: words — слова; mode — forms (разные формы слова) или exact "
                                 "(точно так); channels_mode — all (все каналы) или only (только channel_ids); notify — "
                                 "присылать посты: notify_dm — вам в Telegram, notify_chat_id — в группу проекта."),
    "PUT /api/triggers/{trigger_id}": Method("Триггер-слова", "project",
                                             "Изменить триггер-слово: те же поля, что при создании."),
    "DELETE /api/triggers/{trigger_id}": Method("Триггер-слова", "project", "Удалить триггер-слово."),
    "POST /api/triggers/{trigger_id}/seen": Method("Триггер-слова", "project",
                                                   "Отметить совпадения триггер-слова просмотренными."),
    "GET /api/triggers/channels": Method("Триггер-слова", "project",
                                         "Каналы, которые можно выбрать для триггер-слова."),

    # --- channels ---
    "GET /api/channels": Method("Каналы", "anyone",
                                "Каналы проекта: статус и статистика за 7 дней; own — канал самого проекта (у "
                                "администратора — все каналы)."),
    "POST /api/channels": Method("Каналы", "can_edit_channels",
                                 "Добавить каналы: links — ссылки, @username или ссылки-приглашения, через пробел, "
                                 "запятую или с новой строки."),
    "POST /api/channels/remove": Method("Каналы", "can_edit_channels",
                                        "Убрать каналы из проекта по ссылкам (links); dry=true — только проверить, "
                                        "какие найдутся."),
    "PUT /api/channels/{channel_id}/paused": Method("Каналы", "can_edit_channels",
                                                    "Поставить канал на паузу или вернуть его (paused): сверх "
                                                    "лимита тарифа каналы на паузе не читаются."),
    "DELETE /api/channels/{channel_id}": Method("Каналы", "can_edit_channels",
                                                "Убрать канал из проекта (у администратора — из мониторинга)."),
    "POST /api/channels/list-file": Method("Каналы", "project",
                                           "Бот пришлёт вам в Telegram файл со списком каналов проекта."),
    "GET /api/channels/{channel_id}/stats": Method("Каналы", "anyone",
                                                   "Статистика канала проекта (у администратора — любого): period — "
                                                   "7d, 30d или 90d; tz — сдвиг часового пояса от UTC в минутах."),
    "GET /api/channels/{channel_id}/avatar": Method("Каналы", "anyone", "Аватар канала."),
    "PATCH /api/channels/{channel_id}": Method("Каналы", "admin",
                                               "action — pause (пауза), resume (снова собирать) или retry (ещё раз "
                                               "после ошибки)."),
    "GET /api/channels/{channel_id}/followers": Method("Каналы", "admin", "Какие проекты следят за каналом."),
    "GET /api/projects/channels": Method("Каналы", "owner",
                                         "Каналы, куда добавлен бот, — из них выбирается канал проекта."),
    "PUT /api/projects/{project_id}/channel": Method("Каналы", "owner",
                                                     "Выбрать канал проекта: chat_id — из списка выше, или link — "
                                                     "ссылка на любой канал; project_id — проект этого ключа "
                                                     "(project.id в /api/auth/me)."),
    "GET /api/projects/{project_id}/avatar": Method("Каналы", "owner", "Аватар канала проекта."),

    # --- groups ---
    "GET /api/groups": Method("Группы проекта", "project",
                              "Группы проекта, куда бот отправляет посты; у владельца в found — группы с ботом, которые "
                              "можно добавить."),
    "POST /api/groups": Method("Группы проекта", "owner",
                               "Добавить группу: link — публичную по ссылке, или chat_id — из found."),
    "DELETE /api/groups/{chat_id}": Method("Группы проекта", "owner", "Убрать группу из проекта."),

    # --- the smart feed ---
    "GET /api/smart": Method("Умная лента", "project",
                             "Умная лента: включена ли, её промпт, сколько постов за сутки ИИ прочёл и сколько "
                             "оставил; editable и can_correct — что можно этому ключу."),
    "PUT /api/smart": Method("Умная лента", "ai_configure",
                             "enabled — включить или выключить; prompt — какие посты показывать; reset_prompt=true — "
                             "вернуть стандартный промпт."),
    "POST /api/smart/posts/{post_id}/correct": Method("Умная лента", "ai_teach,ai_configure",
                                                      "Поправить ИИ: show=false — скрыть пост, который он оставил, "
                                                      "show=true — показать скрытый; reason — почему: ИИ учится на "
                                                      "этом."),
    "DELETE /api/smart/posts/{post_id}/correct": Method("Умная лента", "ai_teach,ai_configure",
                                                        "Отменить поправку: снова решает ИИ."),

    # --- topics ---
    "GET /api/topics": Method("Темы", "project",
                              "Темы — новости, которые ИИ собрал из постов разных каналов, свежие и важные наверху; "
                              "hidden=true — скрытые темы; offset и limit (до 200) — страницы."),
    "GET /api/topics/{topic_id}": Method("Темы", "project", "Тема целиком: пересказ, хронология и посты-источники."),
    "GET /api/topics/settings": Method("Темы", "project",
                                       "Настройки тем: включены ли, расписание и ближайшие сборы, промпт (тем, кто "
                                       "может настраивать); editable, can_run, can_teach — что можно этому ключу."),
    "PUT /api/topics/settings": Method("Темы", "ai_configure",
                                       "enabled — включить или выключить темы; prompt — что для вас тема "
                                       f"(reset_prompt=true — стандартный); {SCHEDULE}"),
    "PUT /api/topics/notify": Method("Темы", "project", "on — присылать ли вам новые темы в Telegram."),
    "GET /api/topics/run": Method("Темы", "project", "Идёт ли сбор тем сейчас и чем закончился последний."),
    "POST /api/topics/run": Method("Темы", "owner", "«Собрать сейчас»: собрать темы, не дожидаясь расписания."),
    "POST /api/topics/{topic_id}/hide": Method("Темы", "ai_teach",
                                               "Скрыть тему для всего проекта; reason — почему: ИИ учится, какие темы "
                                               "проекту не нужны."),
    "POST /api/topics/{topic_id}/unhide": Method("Темы", "ai_teach", "Вернуть скрытую тему."),
    "POST /api/topics/{topic_id}/covered": Method("Темы", "ai_teach",
                                                  "Отметить «Есть в вашем канале»: канал проекта об этом уже писал."),
    "POST /api/topics/{topic_id}/not-covered": Method("Темы", "ai_teach",
                                                      "«Это не так»: канал проекта об этом не писал."),

    # --- publications ---
    "GET /api/publications": Method("Публикации", "project",
                                    "Публикации: filter — all, scheduled (запланированные), published (вышедшие) или "
                                    "suggested (предложения ИИ); start и end — посты одного дня календаря; offset и "
                                    "limit (до 50). У каждого поста editable — может ли этот ключ его менять; у "
                                    "владельца в settings — настройки публикаций."),
    "GET /api/publications/calendar": Method("Публикации", "project",
                                             "Календарь: что выходило и выйдет между start и end (ISO 8601 с часовым "
                                             "поясом), коротко — время, состояние и первая строка."),
    "GET /api/publications/importance": Method("Публикации", "project",
                                               "Шкала важности тем с примерами тем проекта за неделю."),
    "PUT /api/publications/settings": Method("Публикации", "owner",
                                             "topic_posts — писать посты по темам; posts_limit — сколько за один сбор; "
                                             "min_importance_posts — от какой важности; prompt — пожелания к постам. "
                                             "Автопостинг: autopost — off, now (сразу) или delay (через "
                                             "autopost_hours часов); autopost_gap_min — минут между публикациями; "
                                             "daily_limit — максимум в сутки; autopost_min_sources — минимум "
                                             "источников."),

    # --- the AI's posts ---
    "POST /api/chat-posts": Method("Посты", "ai_publish",
                                   "Создать пост из своего текста: text — HTML Telegram (<b>, <i>, <u>, <s>, <a href>, "
                                   "<blockquote>…), до 4096 символов, а с картинками — до 1024; source_post_ids — "
                                   "посты проекта, из которых взять фото (до 30; в ответе — candidates и media); "
                                   "cover — с обложкой, cover_title — её заголовок. Пост появится в «Публикациях»: "
                                   "его можно поправить, опубликовать или запланировать, как любой другой."),
    "PATCH /api/chat-posts/{post_id}": Method("Посты", "project",
                                              "Изменить пост до публикации: text — HTML Telegram (<b>, <i>, <u>, <s>, "
                                              "<a href>, <blockquote>…); media — картинки из candidates поста, по "
                                              "порядку. Можно автору поста из своего чата, а посты в «Публикациях» — "
                                              "тем, кто может публиковать."),
    "GET /api/chat-posts/{post_id}/sources": Method("Посты", "project",
                                                    "Посты-источники, из которых написан пост."),
    "POST /api/chat-posts/{post_id}/send": Method("Посты", "project",
                                                  "Бот пришлёт пост вам в Telegram или, с chat_id, в группу проекта."),
    "GET /api/chat-posts/{post_id}/cover.jpg": Method("Посты", "project", "Обложка поста, как она выйдет."),
    "POST /api/chat-posts/{post_id}/publish": Method("Посты", "ai_publish",
                                                     "Опубликовать в канале проекта сейчас. Если часть картинок "
                                                     "отправить нельзя, ничего не выходит: published=false и skipped; "
                                                     "с partial=true — без них."),
    "PUT /api/chat-posts/{post_id}/schedule": Method("Посты", "ai_publish",
                                                     "Запланировать публикацию: at — время с часовым поясом (ISO "
                                                     "8601), не раньше чем через 2 минуты и не позже чем через год; "
                                                     "partial — как у публикации. Новое время заменяет прежнее."),
    "DELETE /api/chat-posts/{post_id}/schedule": Method("Посты", "ai_publish",
                                                        "Отменить запланированную публикацию."),
    "PUT /api/chat-posts/{post_id}/cover": Method("Посты", "ai_chat,ai_publish",
                                                  "Обложка: on — с ней или без; title — её заголовок (пусто — первая "
                                                  "строка поста). Как и правка: автору своего поста, а постам в "
                                                  "«Публикациях» — тем, кто может публиковать."),
    "PUT /api/chat-posts/{post_id}/channel": Method("Посты", "ai_publish",
                                                    "Изменить текст вышедшего поста прямо в канале (text); картинки "
                                                    "остаются."),
    "DELETE /api/chat-posts/{post_id}/channel": Method("Посты", "ai_publish",
                                                       "Удалить вышедший пост из канала (Telegram даёт боту на это 48 "
                                                       "часов); здесь пост останется."),
    "DELETE /api/chat-posts/{post_id}": Method("Посты", "ai_publish",
                                               "Удалить предложение ИИ или свой пост, которые ещё не публиковали."),
    "POST /api/chat-posts/from-message/{message_id}": Method("Посты", "ai_chat",
                                                             "Оформить ответ ИИ в чате (message_id) как пост."),

    # --- rubrics ---
    "GET /api/rubrics": Method("Рубрики", "owner", "Рубрики проекта и заготовки (presets) для новых."),
    "POST /api/rubrics": Method("Рубрики", "owner",
                                "Новая рубрика из заготовки: preset — key из presets. Создаётся выключенной."),
    "PUT /api/rubrics/{rubric_id}": Method("Рубрики", "owner",
                                           "name; enabled — включить; source — topics (темы) или top_posts (самое "
                                           "популярное); period_hours — 0 (с прошлого выпуска), 3, 6, 12, 24, 72 или "
                                           "168; max_items — 3–10; min_importance — 1–10; channel_ids и words — только "
                                           "эти каналы и слова; prompt — как писать; header и footer — первая и "
                                           "последняя строки; picture — none или source (фото источников); mode — "
                                           f"suggest (в предложения) или auto (сразу в канал); {SCHEDULE}"),
    "DELETE /api/rubrics/{rubric_id}": Method("Рубрики", "owner",
                                              "Удалить рубрику; вышедшие и запланированные выпуски останутся."),
    "POST /api/rubrics/{rubric_id}/example": Method("Рубрики", "owner",
                                                    "Написать пример выпуска сейчас — только посмотреть: ничего не "
                                                    "сохраняется и не публикуется."),

    # --- covers ---
    "GET /api/covers": Method("Обложки", "owner",
                              "Стиль обложек проекта и все варианты: templates, sizes, accents, fonts, positions, "
                              "modes."),
    "PUT /api/covers": Method("Обложки", "owner",
                              "template, size, accent, font, position (top, center или bottom), show_channel; mode — "
                              "обложки к автопостам и рубрикам: off, always или no_photo (если нет фото)."),
    "POST /api/covers/logo": Method("Обложки", "owner", "Загрузить логотип: form-data, поле file — PNG или JPEG."),
    "DELETE /api/covers/logo": Method("Обложки", "owner", "Убрать логотип."),
    "GET /api/covers/preview.jpg": Method("Обложки", "owner",
                                          "Пример обложки (JPEG) со стилем из параметров и заголовком title; width — "
                                          "ширина картинки."),

    # --- the chat ---
    "GET /api/chats": Method("Чат с ИИ", "ai_chat", "Ваши чаты с ИИ."),
    "POST /api/chats": Method("Чат с ИИ", "ai_chat", "Новый чат; topic_id — начать его с темы."),
    "GET /api/chats/{thread_id}": Method("Чат с ИИ", "ai_chat", "Чат: сообщения и посты, которые написал ИИ."),
    "PATCH /api/chats/{thread_id}": Method("Чат с ИИ", "ai_chat", "Переименовать чат (title)."),
    "DELETE /api/chats/{thread_id}": Method("Чат с ИИ", "ai_chat",
                                            "Удалить чат; вышедшие и запланированные посты из него останутся в "
                                            "«Публикациях»."),
    "POST /api/chats/{thread_id}/messages": Method("Чат с ИИ", "ai_chat",
                                                   "Написать ИИ: text; tz — сдвиг часового пояса от UTC в минутах. "
                                                   "Ответ идёт потоком строк JSON (NDJSON), пока ИИ пишет; можно не "
                                                   "читать поток, а запросить чат позже."),
    "POST /api/chats/{thread_id}/retry": Method("Чат с ИИ", "ai_chat",
                                                "Отправить последний вопрос заново — после ответа с ошибкой."),
    "GET /api/chats/status": Method("Чат с ИИ", "ai_chat",
                                    "Отвечает ли ещё ИИ в чатах ids (через запятую) и начало ответа."),
    "GET /api/chats/post-prompt": Method("Чат с ИИ", "project",
                                         "Ваш шаблон просьбы «Создать об этом пост для моего канала»."),
    "PUT /api/chats/post-prompt": Method("Чат с ИИ", "project",
                                         "Изменить шаблон (template); пусто — стандартный."),

    # --- OpenRouter ---
    "GET /api/ai": Method("ИИ и расходы", "own_ai",
                          "Подключение OpenRouter.ai: модели по операциям, месячный лимит, баланс."),
    "PUT /api/ai": Method("ИИ и расходы", "own_ai",
                          "models — {операция: id модели}; monthly_limit_usd — месячный лимит, clear_monthly_limit=true "
                          "— снять его."),
    "DELETE /api/ai": Method("ИИ и расходы", "own_ai", "Отключить OpenRouter.ai."),
    "POST /api/ai/accept-warning": Method("ИИ и расходы", "own_ai",
                                          "Подтвердить, что токены оплачивает владелец и модели — его выбор."),
    "GET /api/ai/models": Method("ИИ и расходы", "own_ai,admin",
                                 "Модели OpenRouter, подходящие для операции (operation), от дешёвых к дорогим."),
    "GET /api/ai/usage": Method("ИИ и расходы", "owner",
                                "Расходы на ИИ: period — 24h, 7d или 30d; tz — сдвиг часового пояса в минутах; по "
                                "часам или дням, операциям и людям (со своим OpenRouter — и по моделям)."),

    # --- the money ---
    "GET /api/billing": Method("Баланс и тариф", "owner",
                               "Баланс, тариф проекта и его срок, автопродление, ИИ тарифа в этом месяце (included, "
                               "used, extra — докупать ли с баланса, extra_cap — лимит докупки), тарифы. Суммы — "
                               "в долларах."),
    "PATCH /api/billing": Method("Баланс и тариф", "owner",
                                 "auto_renew — автопродление; ai_extra — докупать ИИ с баланса; ai_extra_cap — "
                                 "лимит докупки в месяц, clear_ai_extra_cap=true — снять его."),
    "GET /api/billing/history": Method("Баланс и тариф", "owner",
                                       "История операций с балансом, новые первыми; before — id последней показанной."),
    "GET /api/plans": Method("Баланс и тариф", "anyone", "Все тарифы: цена, каналы, сотрудники, ИИ."),

    # --- the team ---
    "GET /api/members": Method("Команда", "owner", "Сотрудники проекта и их права."),
    "POST /api/members": Method("Команда", "owner",
                                "Добавить сотрудника: login и права — can_edit_channels, ai_chat, ai_chat_limit_usd "
                                "(лимит на чат в месяц, $), ai_teach, ai_configure, ai_publish. Пароль приходит в "
                                "ответе один раз."),
    "PATCH /api/members/{member_id}": Method("Команда", "owner",
                                             "Изменить права сотрудника; они действуют с его следующего запроса."),
    "POST /api/members/{member_id}/password": Method("Команда", "owner",
                                                     "Новый пароль сотрудника (в ответе); он выйдет на всех "
                                                     "устройствах."),
    "DELETE /api/members/{member_id}": Method("Команда", "owner", "Удалить сотрудника."),
    "POST /api/members/me/telegram": Method("Команда", "member",
                                            "Ссылка на бота: откройте её, чтобы привязать свой Telegram."),
    "DELETE /api/members/me/telegram": Method("Команда", "member", "Отвязать свой Telegram."),
    "POST /api/promo/redeem": Method("Команда", "owner", "Ввести промокод (code): тариф проекта начнётся или продлится."),
    "GET /api/mcp/connections": Method("Профиль и ключ", "project",
                                       "ИИ-приложения, подключённые через MCP-коннектор от вашего имени."),
    "DELETE /api/mcp/connections/{connection_id}": Method("Профиль и ключ", "project",
                                                          "Отключить ИИ-приложение: его доступ сразу пропадёт."),
    "GET /api/mcp/tools": Method("Профиль и ключ", "anyone",
                                 "Инструменты MCP-коннектора, доступные вам: name и description."),

    # --- the admin's ---
    "GET /api/accounts": Method("Администрирование", "admin", "Аккаунты Telegram, которые собирают посты."),
    "POST /api/accounts/login/start": Method("Администрирование", "admin",
                                             "Начать вход в аккаунт: phone (api_id и api_hash — свои, необязательно); "
                                             "Telegram пришлёт код."),
    "POST /api/accounts/login/code": Method("Администрирование", "admin", "Код из Telegram (login_id, code)."),
    "POST /api/accounts/login/password": Method("Администрирование", "admin",
                                                "Пароль двухэтапной проверки (login_id, password)."),
    "POST /api/accounts/login/cancel": Method("Администрирование", "admin", "Отменить вход (login_id)."),
    "POST /api/accounts/import": Method("Администрирование", "admin", "Добавить аккаунт строкой сессии (session)."),
    "POST /api/accounts/import-files": Method("Администрирование", "admin",
                                              "Добавить аккаунты файлами сессий: form-data, поле files."),
    "PATCH /api/accounts/{account_id}": Method("Администрирование", "admin",
                                               "enabled — включить или выключить; max_channels — сколько каналов на "
                                               "аккаунт."),
    "DELETE /api/accounts/{account_id}": Method("Администрирование", "admin", "Удалить аккаунт."),
    "POST /api/accounts/{account_id}/change-proxy": Method("Администрирование", "admin",
                                                           "Дать аккаунту другой прокси."),
    "GET /api/proxies": Method("Администрирование", "admin", "Прокси и их состояние."),
    "POST /api/proxies": Method("Администрирование", "admin", "Добавить прокси: text — по одному в строке."),
    "POST /api/proxies/{proxy_id}/check": Method("Администрирование", "admin", "Проверить прокси сейчас."),
    "PATCH /api/proxies/{proxy_id}": Method("Администрирование", "admin", "enabled — включить или выключить прокси."),
    "DELETE /api/proxies/{proxy_id}": Method("Администрирование", "admin", "Удалить прокси."),
    "GET /api/notify": Method("Администрирование", "admin", "Бот уведомлений: подключён ли, чаты, куда он пишет."),
    "PUT /api/notify/token": Method("Администрирование", "admin", "Подключить бота: token от @BotFather."),
    "DELETE /api/notify/chats/{chat_id}": Method("Администрирование", "admin", "Убрать чат уведомлений."),
    "POST /api/notify/test": Method("Администрирование", "admin", "Отправить пробное уведомление."),
    "GET /api/settings": Method("Администрирование", "admin", "Настройки сервиса."),
    "PUT /api/settings": Method("Администрирование", "admin", "Изменить настройки: {ключ: значение}."),
    "GET /api/status": Method("Администрирование", "admin", "Состояние сбора: аккаунты, каналы, очереди, ошибки."),
    "GET /api/storage": Method("Администрирование", "admin", "Место на диске и что его занимает."),
    "GET /api/perf": Method("Администрирование", "admin", "Нагрузка на сервер: period — 1h, 24h или 7d."),
    "GET /api/reposts/failed": Method("Администрирование", "admin", "Посты, которые не удалось переслать в группу."),
    "POST /api/status/dismiss-repost-error": Method("Администрирование", "admin", "Скрыть ошибку пересылки."),
    "GET /api/users": Method("Администрирование", "admin", "Пользователи и их проекты."),
    "PATCH /api/users/{user_id}": Method("Администрирование", "admin", "blocked — закрыть или открыть доступ."),
    "PATCH /api/users/{user_id}/projects/{project_id}": Method("Администрирование", "admin",
                                                               "Тариф проекта: plan и expires_at."),
    "GET /api/users/{user_id}/projects/{project_id}/channels": Method("Администрирование", "admin",
                                                                      "Каналы проекта пользователя."),
    "GET /api/billing/admin": Method("Администрирование", "admin",
                                     "ИИ сервиса: подключён ли OpenRouter, его баланс, модели, наценка, расход и "
                                     "выручка за месяц."),
    "PATCH /api/billing/admin": Method("Администрирование", "admin",
                                       "markup_pct — наценка в процентах (200 — вдвое); models — {операция: модель}."),
    "POST /api/billing/admin/users/{user_id}/balance": Method("Администрирование", "admin",
                                                              "Начислить (amount > 0) или списать деньги с баланса "
                                                              "владельца, с комментарием note."),
    "GET /api/billing/admin/plans": Method("Администрирование", "admin", "Все тарифы, dev:service:plan тоже."),
    "GET /api/promo": Method("Администрирование", "admin", "Промокоды."),
    "POST /api/promo": Method("Администрирование", "admin", "Создать промокоды: plan, days, count."),
    "DELETE /api/promo/{code}": Method("Администрирование", "admin", "Удалить промокод."),
}

# the read API (/api/v1, public_api), for the MCP connector's tools — its reference for people is the site's own
# (frontend lib/apiDocs.ts); a key calls it with no list of its own
DATA: dict[str, Method] = {
    "GET /api/v1/project": Method("Данные", "project",
                                  "Проект и его канал (my_channel): подписчики и средние цифры за 7 дней, тариф. "
                                  "Начните с него: весь мониторинг — для развития этого канала."),
    "GET /api/v1/project/stats": Method("Данные", "project",
                                        "Статистика канала проекта: period — 7d, 30d или 90d; tz — сдвиг часового "
                                        "пояса в минутах; подписчики, посты и показатели по дням, лучшие часы и "
                                        "средние отслеживаемых каналов для сравнения."),
    "GET /api/v1/posts": Method("Данные", "anyone",
                                "Посты в удобном для анализа виде: period (20m, 1h, 6h, 24h, 7d, 30d, all) или "
                                "since и until; sort — new, er, fr, fr_plus, forwards, reactions, comments или views; "
                                "channel — id, @username или my (канал проекта); q — поиск; smart=true — только умная "
                                "лента; limit (до 100) и offset."),
    "GET /api/v1/posts/new": Method("Данные", "anyone",
                                    "Посты, собранные после after_id, по порядку — чтобы ничего не пропустить; "
                                    "smart=true — только умная лента."),
    "GET /api/v1/posts/{post_id}": Method("Данные", "anyone", "Один пост целиком, с форматированием (html)."),
    "GET /api/v1/posts/{post_id}/preview": Method("Данные", "anyone", "Превью картинки поста; i — номер в альбоме."),
    "GET /api/v1/channels": Method("Данные", "anyone",
                                   "Каналы со статистикой за 7 дней; status — active, pending или error; my_channel "
                                   "— канал проекта."),
    "GET /api/v1/channels/{channel_id}": Method("Данные", "anyone", "Один канал со статистикой."),
    "GET /api/v1/stats": Method("Данные", "anyone",
                                "Сводка: каналы по статусам, посты всего и за сутки, топ-5 постов за сутки."),
    "GET /api/v1/topics": Method("Данные", "project",
                                 "Темы — новости, которые ИИ собрал из постов разных каналов: важность 1–10, сколько "
                                 "каналов писали, covered — канал проекта уже писал; updated_after — только новые и "
                                 "обновлённые."),
    "GET /api/v1/topics/{topic_id}": Method("Данные", "project", "Тема целиком: хронология и посты-источники."),
}

# the site's own, and why a key does not get them
SITE_ONLY: dict[str, str] = {
    "POST /api/auth/login": "вход администратора на сайт",
    "POST /api/auth/logout": "выход с сайта",
    "POST /api/auth/tg/start": "вход на сайт через Telegram",
    "GET /api/auth/tg/status": "вход на сайт через Telegram",
    "POST /api/auth/qr/start": "вход на сайт по QR-коду",
    "GET /api/auth/qr/status": "вход на сайт по QR-коду",
    "POST /api/members/login": "вход сотрудника на сайт",
    "POST /api/auth/intros": "приветственные карточки сайта",
    "POST /api/client-log": "отчёты страниц сайта о сбоях",
    "GET /api/devices": "устройства — это входы на сайт",
    "PUT /api/devices/current": "устройства — это входы на сайт",
    "DELETE /api/devices/{device_id}": "устройства — это входы на сайт",
    "POST /api/devices/terminate-others": "устройства — это входы на сайт",
    "GET /api/devices/qr/{token}": "вход на сайт по QR-коду: ключ не открывает сайт",
    "POST /api/devices/qr/{token}": "вход на сайт по QR-коду: ключ не открывает сайт",
    "POST /api/devices/qr/{token}/decline": "вход на сайт по QR-коду",
    "GET /api/projects": "ключ принадлежит одному проекту",
    "POST /api/projects": "ключ принадлежит одному проекту",
    "POST /api/projects/{project_id}/open": "ключ принадлежит одному проекту",
    "DELETE /api/projects/{project_id}": "ключ принадлежит одному проекту",
    "POST /api/ai/connect": "OpenRouter.ai подключается в браузере",
    "GET /api/ai/callback": "OpenRouter.ai подключается в браузере",
    "GET /api/health": "проверка, что сервер работает, — без ключа",
    "POST /api/mcp/request": "разрешить ИИ-приложение может только человек, на сайте",
    "POST /api/billing/buy": "покупает тариф только человек, на сайте",
    "POST /api/billing/topup": "пополняет баланс только человек, на сайте",
    "POST /api/billing/admin/connect": "OpenRouter сервиса подключается в браузере",
    "GET /api/billing/admin/callback": "OpenRouter сервиса подключается в браузере",
    "DELETE /api/billing/admin/key": "отключить ИИ сервиса может только администратор, на сайте",
    "POST /api/billing/admin/live": "запустить оплату может только администратор, на сайте",
    "POST /api/mcp/approve": "разрешить ИИ-приложение может только человек, на сайте",
}


def may(who: str, viewer) -> bool:
    """Whether the viewer may call a method of `who` — as the site lets them (the reference shows these only)."""
    for one in who.split(","):
        if one == "anyone":
            return True
        if one == "admin" and viewer.is_admin:
            return True
        if one == "project" and viewer.project_id is not None:
            return True
        if one == "owner" and viewer.is_owner:
            return True
        if one == "member" and viewer.role == "member":
            return True
        if one == "can_edit_channels" and viewer.can_edit_channels and (viewer.is_admin or viewer.project_id):
            return True
        if one == "own_ai" and viewer.is_owner and (plan := plan_of(viewer.plan)) is not None and plan.own_ai:
            return True
        if one not in ("anyone", "admin", "project", "owner", "member", "can_edit_channels", "own_ai")                 and viewer.may(one):
            return True
    return False
