# Резервные копии

Дважды в сутки сервер собирает всё, без чего проект не восстановить, в один tar, шифрует его [age](https://age-encryption.org)
открытым ключом и кладёт в Google Диск владельца, в папку **`feedor-backup`**. Хранятся 14 последних копий (неделя).

Исходники скриптов и юнитов — [`deploy/backup/`](../deploy/backup/).

## Что и когда

**Когда:** в 03:00 и 15:00 МСК — `feedor-backup.timer`, `OnCalendar=*-*-* 00,12:00:00 UTC`, `Persistent=true` (если
сервер был выключен в это время, копия делается сразу после включения).

**Что в копии** (`feedor-ГГГГММДД-ЧЧММССZ.tar.age`, время UTC, ~50 МБ):

| Файл в архиве | Что это |
|---|---|
| `db.dump` | вся база PostgreSQL `monitoring`, `pg_dump -Fc`: пользователи и их балансы, история операций (`balance_ops`), проекты с тарифами, каналы, посты, сессии Telegram-аккаунтов, настройки (токен бота, ключ OpenRouter сервиса, наценка), ключи OpenRouter владельцев dev:service:plan |
| `env` | `/opt/kindergarten-monitoring/.env`: пароль базы, `SECRET_KEY`, `TG_API_ID/HASH`, пароль администратора |
| `src/` | выложенный код, вместе с `docker-compose.yml` и `deploy/Caddyfile` |
| `caddy_data/` | сертификаты Let's Encrypt и аккаунт Caddy |
| `server/…` | настройки сервера по их путям: правила ufw (с адресами Cloudflare), `jail.local` fail2ban, `daemon.json` Docker, `fstab`, `authorized_keys`, юниты и скрипты бэкапа, `recipient.txt` |
| `INFO`, `SHA256SUMS` | когда и где сделана копия; контрольные суммы всех файлов |

**Не копируется:** картинки постов (`media`, ~1,8 ГБ — копии из Telegram; без них у старых постов просто не будет
превью), геобаза `media/_geo` (скачивается сама), `rclone.conf` и `notify.env` (секреты, их делают заново).

**Как идёт копия** (`/usr/local/sbin/feedor-backup`): дамп → проверка дампа (`pg_restore -l` читается, не меньше
1 МБ, основные таблицы на месте) → tar → `age -R recipient.txt` → `rclone copyto` → сверка размера в облаке с локальным →
только после этого удаление копий сверх 14, насовсем (`--drive-use-trash=false`) → итоговая строка в журнал. Временные
файлы — в `/var/tmp/feedor-backup.*`, удаляются в любом случае.

**Если что-то не так:** на любом шаге — сообщение от бота проекта в чаты системных оповещений (таблица `notify_chats`):
«Резервная копия … не удалась: шаг «…», код …». Токен и чаты скрипт берёт из базы; на случай, если сломалась сама база,
их копия лежит в `/etc/feedor-backup/notify.env` (обновляется после каждой удачной копии).

## Где ключи

| Что | Где |
|---|---|
| **Закрытый ключ age** — без него копии не открыть | `C:\path\to\feedor-backup-key.txt` у владельца; должен быть ещё в менеджере паролей и в одном месте. **На сервере его нет.** |
| Открытый ключ age | `/etc/feedor-backup/recipient.txt` (сервер) и в первой строке-комментарии файла с закрытым ключом |
| Доступ к Google Диску | `/etc/feedor-backup/rclone.conf` (сервер, 600), права только `drive.file`: rclone видит лишь созданные им файлы |
| Бот для оповещений | `/etc/feedor-backup/notify.env` (сервер, 600) |

## Вручную и журнал

```bash
ssh -i ~/.ssh/kindergarten_vps root@SERVER_IP
systemctl start feedor-backup               # копия сейчас (ждёт, пока закончится, ~1 мин)
journalctl -u feedor-backup -n 30           # журнал; последняя строка удачной копии: «feedor-backup: OK …»
systemctl list-timers feedor-backup.timer   # когда следующая
rclone --config /etc/feedor-backup/rclone.conf lsl gdrive:feedor-backup   # копии в облаке
DRY_RUN=1 FAIL_AT=upload feedor-backup      # проверить оповещение: ломает шаг, ничего не отправляет
```

Шаги для `FAIL_AT`: `lock dump check-dump collect encrypt upload check-upload prune finish`.

## Проверить копию

Скачивает последнюю копию (или названную), расшифровывает закрытым ключом из stdin — на диск сервера ключ не пишется,
разворачивает базу во временную `feedor_restore_check`, сверяет число строк каждой таблицы с рабочей, `.env`, код и
настройки — с рабочими, потом всё временное удаляет. Рабочую базу не меняет.

```bash
# Git Bash
ssh -i ~/.ssh/kindergarten_vps root@SERVER_IP feedor-backup-verify < /path/to/feedor-backup-key.txt
# PowerShell
Get-Content C:\path\to\feedor-backup-key.txt | ssh -i ~/.ssh/kindergarten_vps root@SERVER_IP feedor-backup-verify
```

Таблицы, в которые пишут постоянно (`posts`, `perf_samples`, `channel_snapshots`), со временем расходятся с копией — это
нормально. Проблема — только таблица с одной стороны, несошедшиеся контрольные суммы или ошибка восстановления: тогда в
конце «ИТОГ: проблем: N».

## Восстановление на новом сервере с нуля

Пути и имена ниже — как сейчас (`/opt/kindergarten-monitoring`, проект compose `kindergarten`).

1. **Сервер.** Ubuntu 24.04, ваш SSH-ключ (`~/.ssh/kindergarten_vps.pub`) в `/root/.ssh/authorized_keys`. Поставить:
   ```bash
   apt-get update && apt-get install -y docker.io docker-compose-v2 age ufw fail2ban python3-systemd
   ```
   rclone — официальный `.deb` с downloads.rclone.org со сверкой `SHA256SUMS` (в apt он старый).

2. **Копия.** Скачать нужный `feedor-….tar.age` из папки `feedor-backup` в Google Диске и залить на сервер:
   ```bash
   scp -i ~/.ssh/kindergarten_vps feedor-….tar.age root@НОВЫЙ_IP:/root/
   ```

3. **Расшифровать** (ключ идёт через stdin, на диск сервера не попадает) и распаковать:
   ```bash
   ssh -i ~/.ssh/kindergarten_vps root@НОВЫЙ_IP "age -d -i - -o /root/backup.tar /root/feedor-….tar.age" < /path/to/feedor-backup-key.txt
   ssh -i ~/.ssh/kindergarten_vps root@НОВЫЙ_IP
   mkdir /root/restore && tar -C /root/restore -xf /root/backup.tar && cd /root/restore && sha256sum -c --quiet SHA256SUMS
   ```

4. **Старый сервер остановить, если он ещё жив** — до запуска нового: иначе Telegram-аккаунты окажутся онлайн с двух IP
   сразу (`cd /opt/kindergarten-monitoring/src && docker compose -p kindergarten stop`).

5. **Секреты и код:**
   ```bash
   mkdir -p /opt/kindergarten-monitoring && cd /opt/kindergarten-monitoring
   install -m 600 /root/restore/env .env
   cp -a /root/restore/src src && ln -s ../.env src/.env
   ```

6. **База** — в пустую, до первого запуска приложения:
   ```bash
   cd /opt/kindergarten-monitoring/src
   docker compose -p kindergarten up -d db       # ждать «healthy»: docker compose -p kindergarten ps
   docker exec -i kindergarten-db-1 pg_restore -U monitoring -d monitoring --no-owner --exit-on-error < /root/restore/db.dump
   ```

7. **Сертификаты** (необязательно — Caddy получит новые сам) и запуск:
   ```bash
   docker compose -p kindergarten create
   cp -a /root/restore/caddy_data/. /var/lib/docker/volumes/kindergarten_caddy_data/_data/
   docker compose -p kindergarten up -d --build
   ```

8. **Настройки сервера** из `/root/restore/server/` — по тем же путям: `etc/docker/daemon.json` (потом
   `systemctl restart docker`), `etc/fail2ban/jail.local` (`systemctl restart fail2ban`), swap по `etc/fstab`
   (`fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile` + строка в `/etc/fstab`).
   ufw: скопировать `etc/ufw/*.rules`, **проверить в `after.rules` имя сетевого интерфейса** (`ens3`; на новом сервере —
   `ip -br link`) и что в `user.rules` разрешён порт 22, затем `ufw --force enable`. Адреса Cloudflare сверить с
   https://www.cloudflare.com/ips-v4.

9. **DNS.** В Cloudflare A-запись `feedor.pro` (и `www`) → новый IP, проксирование включено. Проверить сайт.

10. **Выкладка.** В `scripts/deploy.sh` поменять `HOST` на новый IP.

11. **Бэкапы на новом сервере:**
    ```bash
    install -d -m 700 /etc/feedor-backup
    install -m 600 /root/restore/server/etc/feedor-backup/recipient.txt /etc/feedor-backup/
    ```
    `rclone.conf` — заново, как в «Доступ к Google Диску» ниже. Затем
    `bash /opt/kindergarten-monitoring/src/deploy/backup/install.sh`, `systemctl start feedor-backup` и проверка копии.
    Ключ age остаётся прежним — старые копии открываются тем же файлом.

12. Удалить `/root/restore`, `/root/backup.tar` и `/root/feedor-….tar.age`.

## Доступ к Google Диску (rclone.conf)

Нужен при переезде, если доступ отозван или токен перестал работать (в журнале ошибки `invalid_grant` / `401`).

1. На своём компьютере: скачать официальный rclone для Windows с downloads.rclone.org во временную папку, сверить
   `SHA256SUMS`, запустить:
   ```
   rclone authorize "drive" "eyJzY29wZSI6ImRyaXZlLmZpbGUifQ"
   ```
   (base64 от `{"scope":"drive.file"}`, без «=» в конце). В браузере войти и разрешить.
2. Между `--->` и `<---End paste` — base64 с JSON `{"token": …}`. Положить на сервер в `/etc/feedor-backup/rclone.conf`
   (права 600), не показывая токен:
   ```
   [gdrive]
   type = drive
   scope = drive.file
   token = <JSON токена>
   ```
3. Проверить: `rclone --config /etc/feedor-backup/rclone.conf about gdrive:`; удалить локальные файлы с токеном.

Папку `feedor-backup` создаёт сам rclone. Папку, созданную вручную, он с правами `drive.file` не видит и сделает рядом
вторую с тем же именем.

**Свой client_id.** rclone ходит в Google через собственное приложение владельца, а не через общий client_id rclone (его
Google отключает в 2026 году): Google Cloud Console, проект `mybackupproject-512523`, приложение «MyBackupProject»,
OAuth client «Desktop client 1», опубликовано в Production (в режиме Testing токен живёт 7 дней). Поэтому в шаге 1 блок —
base64 от `{"scope":"drive.file","client_id":"…","client_secret":"…"}` без «=» (client_id и секрет — из
`client_secret_….json`, который скачивается в Credentials), а в `rclone.conf` между `scope` и `token` — ещё строки
`client_id = …` и `client_secret = …`.

Как его сделать заново: проект → включить Google Drive API → Google Auth Platform: Branding (название, почта, внизу
Developer contact information; **без логотипа** — с ним нужна проверка Google; домашняя страница и политика — `https://feedor.pro`,
домен `feedor.pro`) → Audience: External, Publish app → Clients: Create client, тип «Desktop app». Файлы, созданные одним
client_id, другой с правами `drive.file` не видит: rclone заведёт новую папку `feedor-backup`, старую удалить вручную.
