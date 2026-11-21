# Crypto Auto Agents — read-only market + airdrop discovery

**Стоимость:** €0 на публичном репозитории GitHub Actions с обычными hosted runners, при действующих бесплатных условиях. Python без внешних библиотек и API-ключей.

**Честно:** система НЕ зарабатывает деньги, НЕ проходит опросы, НЕ выполняет квесты на платформах, НЕ получает токены, НЕ логинится и НЕ торгует. Только мониторит публичные источники и сохраняет отчёты. Все найденные предложения требуют ручной проверки условий.

## Запуск в облаке — ПК можно выключить

1. Зарегистрируйся в GitHub: https://github.com/signup (если уже есть аккаунт, используй его).
2. Создай новый **Public** репозиторий, например `crypto-auto-agents`, через https://github.com/new . Отметь `Add a README` либо создай пустой репозиторий.
3. Распакуй ZIP. В новом репозитории выбери **Add file → Upload files** и **перетащи всю папку с содержимым**, чтобы сохранилась вложенная директория `.github/workflows/monitor.yml`. Убедись, что GitHub сохранил именно `/.github/workflows/monitor.yml` в корне (НЕ `crypto_auto_agents/.github/...`). Сохрани изменения кнопкой **Commit changes**. Если веб-интерфейс не принимает скрытую папку `.github`, создай файл `.github/workflows/monitor.yml` отдельно через **Add file → Create new file** и вставь содержимое из архива.
4. На вкладке **Actions** разреши workflows, если GitHub попросит, выбери `Crypto Auto Agents — read-only monitoring`, нажми **Run workflow** один раз.
5. Когда появится зелёная галочка, открой `reports/LATEST.md` прямо в репозитории. Если JSON-источник недоступен, его ошибка видна в разделе «Проверка источников».
6. Далее расписание запускается в **00:17, 06:17, 12:17, 18:17 UTC** (в Германии зимой это 01:17, 07:17, 13:17 и 19:17). Возможны задержки. Автообновление не гарантировано при отключении workflows или после 60 дней неактивности публичного репозитория.

Работает без аккаунтов бирж и без кошельков. Не загружай приватные ключи и seed phrase даже в секреты.

### Дополнительно: Telegram-уведомления (НЕобязательно)

В Telegram создай бота через официального @BotFather, получи `TELEGRAM_BOT_TOKEN`, узнай свой `TELEGRAM_CHAT_ID`. В репозитории открой `Settings → Secrets and variables → Actions → New repository secret` и добавь два секрета: `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID`.

Не записывай токены в файлы проекта. Скрипт отправляет краткий отчёт после каждого запуска (даже если новых наград нет). Отправка Telegram может дать ошибку при некорректной настройке, но отчёт будет сохранён.

## Запуск на Windows (только при желании)

Нужен Python 3.11 или новее. Дважды нажми `RUN_WINDOWS.bat`; браузер откроет `reports/LATEST.html`. Если Python не установлен, используй облачный вариант.

## Схема

- `agent.py` — проверяет CoinGecko (BTC/ETH/SOL), RSS AirdropAlert, Kraken, Coinbase.
- `reports/LATEST.md` — человекочитаемый отчёт в GitHub.
- `reports/LATEST.html` — локальный красивый отчёт.
- `reports/latest.json` — структурированные данные для будущего Codex/Claude.
- `data/seen.json` — история увиденных публикаций, чтобы отличать новые от старых.
- `.github/workflows/monitor.yml` — 4 автоматических запуска в сутки.

**Проверяй:** страна Германия, обязательность депозита/торговли, комиссию сети, KYC, лимиты, дату окончания, возможность продажи/вывода. Слова «free», «earn», «reward» ничего не гарантируют.

## Codex

Загрузи этот проект в Codex, если захочешь добавить новые ИСТОЧНИКИ через официальные API/RSS, улучшить скоринг или Telegram-формат. Не добавляй автокликеры, капчу-обход, массовые аккаунты, сделки, доступ к кошелькам, паролям или платежам.

## Источники и документы

- https://www.coingecko.com/en/api/pricing
- https://airdropalert.com/rss-feed/
- https://blog.kraken.com/feed/
- https://www.coinbase.com/blog/rss.xml
- https://docs.github.com/en/actions/reference/events-that-trigger-workflows
- https://docs.github.com/en/billing/concepts/product-billing/github-actions
