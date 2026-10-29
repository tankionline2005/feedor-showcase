"""Fills the local dev database with fake channels and posts: python -m app.devseed"""

import asyncio
import random
from datetime import timedelta

from sqlalchemy import delete

from .db import SessionLocal, init_db
from .models import Account, Channel, Post, Proxy, utcnow
from .proxies import ProxyConfig
from .tg.parse import compute_er, compute_fr
from .fr_plus import refresh_fr_bases
from .worker.collector import RESTRICTED_EVERYWHERE

CHANNELS = [
    ("Технологии и стартапы", "tech_daily", 184_000),
    ("Экономика сегодня", "econ_today", 92_500),
    ("Городские новости", "city_news", 341_000),
    ("Дизайн и типографика", "type_design", 27_300),
    ("Наука простыми словами", "science_easy", 512_000),
    ("Кино без спойлеров", "cinema_ns", 63_800),
]

TEXTS = [
    "<strong>Главное за день</strong>\n\nКомпания представила новую линейку ноутбуков: прирост производительности до 40%, автономность — до 22 часов. Продажи стартуют в ноябре.",
    "Курс рубля на открытии торгов: доллар — 92,4 ₽, евро — 100,1 ₽. Аналитики ждут умеренной волатильности до конца недели.",
    "В центре города с понедельника перекроют движение на трёх улицах — <a href=\"https://example.com\">схема объездов</a>. Работы продлятся две недели.",
    "Почему шрифты с гуманистическими пропорциями лучше читаются на экране? Разбираем на примерах — длинный пост, сохраняйте.\n\nПервое: открытые апертуры. Буквы вроде «а», «с», «е» не схлопываются при мелком кегле.\n\nВторое: умеренный контраст штрихов.\n\nТретье: достаточная высота строчных.\n\nЧетвёртое: ритм и межбуквенные пробелы. Хороший экранный шрифт держит ровный серый цвет текста, без пятен и дыр.\n\nПятое: хинтинг и рендеринг. На экранах с низкой плотностью пикселей это до сих пор важно.",
    "Учёные впервые сняли на видео процесс деления клетки в реальном времени с разрешением 20 нм.",
    "Вышел трейлер нового сезона — премьера 14 ноября. Смотрели первый? Делитесь в комментариях.",
    "<em>Цитата дня</em>\n\n<blockquote>Простота — необходимое условие красоты.</blockquote>",
    "Опрос: каким сервисом для заметок вы пользуетесь?",
    "Короткая новость без картинки, но с хорошими цифрами: выручка выросла на 18% год к году.",
    "Собрали подборку из 12 бесплатных инструментов для работы с изображениями. Всё работает в браузере.",
]

DEMO_ERRORS = [
    "Аккаунт «Тестовый аккаунт» не может писать в группу-агрегатор — проверьте, что участникам разрешено "
    "отправлять сообщения и медиа и что аккаунт не ограничен",
    "Пост удалён в исходном канале",
    "Группа принимает только по заявкам — нужна открытая публичная группа",
]

EMOJI = ["👍", "🔥", "❤", "😁", "🤔", "👏", "😢", "🎉"]
MEDIA = [None, None, None, "photo", "video", "document", "poll", "voice"]


async def main() -> None:
    await init_db()
    random.seed(7)
    async with SessionLocal() as s:
        await s.execute(delete(Post))
        await s.execute(delete(Channel))
        await s.execute(delete(Account))
        await s.execute(delete(Proxy))
        demo = [
            (ProxyConfig("socks5", "185.12.34.56", 1080, "user1", "pass"), {"status": "ok", "latency_ms": 84}),
            (ProxyConfig("socks5", "185.12.34.57", 1080, "user1", "pass"), {"status": "ok", "latency_ms": 132}),
            (ProxyConfig("mtproto", "91.200.1.10", 443, secret="dd" + "0" * 32),
             {"status": "dead", "fails": 2, "last_error": "Connection refused"}),
        ]
        proxies = [
            Proxy(kind=c.kind, host=c.host, port=c.port, username=c.username, password=c.password, secret=c.secret,
                  key=c.key, checked_at=utcnow(), **extra)
            for c, extra in demo
        ]
        s.add_all(proxies)
        await s.flush()
        acc = Account(phone="+79990000000", name="Тестовый аккаунт", session="", status="active", max_channels=300,
                      proxy_id=proxies[0].id)
        s.add(acc)
        s.add(Account(phone="+79990000001", name="Запасной", session="", status="flood",
                      status_detail="Лимит Telegram, пауза 340 с", proxy_id=proxies[1].id))
        s.add(Account(phone="+79990000002", name="Третий", session="", status="waiting",
                      status_detail="Нет свободного рабочего прокси — добавьте прокси в пул", proxy_id=proxies[2].id))
        await s.flush()

        now = utcnow()
        channels = []
        for i, (title, username, subs) in enumerate(CHANNELS):
            ch = Channel(
                link=f"@{username}",
                username=username,
                tg_id=1_000_000 + i,
                title=title,
                subscribers=subs,
                status="active",
                account_id=acc.id,
                info_updated_at=now,
                last_post_at=now,
            )
            s.add(ch)
            channels.append(ch)
        s.add(Channel(link="@waiting_one", username="waiting_one", status="pending"))
        s.add(Channel(link="@no_such_channel", username="no_such_channel", status="error", status_detail="Канал не найден"))
        s.add(Channel(link="@sleepy", username="sleepy", title="Спящий канал", tg_id=999, status="paused", account_id=acc.id))
        s.add(Channel(link="@geo_blocked", username="geo_blocked", title="РИА Новости", tg_id=998, status="restricted",
                      status_detail=RESTRICTED_EVERYWHERE, restricted_for=[acc.id]))
        await s.flush()

        msg_id = 100
        for n in range(140):
            ch = random.choice(channels)
            age = timedelta(minutes=random.randint(2, 60 * 24 * 6))
            subs = ch.subscribers or 10_000
            views = int(subs * random.uniform(0.05, 0.6) * min(1.0, age.total_seconds() / 7200 + 0.2))
            reactions_top = sorted(
                ({"e": e, "c": int(views * random.uniform(0.0005, 0.02))} for e in random.sample(EMOJI, random.randint(0, 4))),
                key=lambda r: -r["c"],
            )
            reactions = sum(r["c"] for r in reactions_top)
            comments = int(views * random.uniform(0, 0.004))
            forwards = int(views * random.uniform(0, 0.01))
            text = random.choice(TEXTS)
            msg_id += 1
            s.add(
                Post(
                    channel_id=ch.id,
                    msg_id=msg_id,
                    posted_at=now - age,
                    text=text,
                    html=text,
                    media_type=random.choice(MEDIA),
                    media_count=random.choice([1, 1, 1, 3]),
                    views=views,
                    forwards=forwards,
                    reactions=reactions,
                    comments=comments,
                    er=compute_er(views, reactions, comments, forwards),
                    fr=compute_fr(views, forwards),
                    reactions_top=reactions_top or None,
                    repost_status=(status := random.choice(["done"] * 8 + ["failed", "skipped"])),
                    repost_error=random.choice(DEMO_ERRORS) if status == "failed" else None,
                    reposted_at=now - age + timedelta(seconds=random.randint(5, 90)),
                )
            )
        await s.commit()
        await refresh_fr_bases(s)
    print("seeded")


if __name__ == "__main__":
    asyncio.run(main())
