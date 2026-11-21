#!/usr/bin/env python3
"""Read-only market and promotion watchers. No trades, no claims, no login."""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import os
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent
REPORTS = ROOT / 'reports'
DATA = ROOT / 'data'
STATE_PATH = DATA / 'seen.json'
MARKET_URL = ('https://api.coingecko.com/api/v3/coins/markets?'
              'vs_currency=usd&ids=bitcoin%2Cethereum%2Csolana&'
              'order=market_cap_desc&per_page=3&page=1&sparkline=false&'
              'price_change_percentage=24h')
SOURCES = {
    'AirdropAlert (каталог, НЕ подтверждение)': 'https://airdropalert.com/feed/rssfeed',
    'Kraken (официальный блог)': 'https://blog.kraken.com/feed/',
    'Coinbase (официальный блог)': 'https://www.coinbase.com/blog/rss.xml',
}
TRIGGERS = re.compile(r'\b(airdrop|giveaway|learn.?and.?earn|learn\s*&\s*earn|quiz|rewards?|bonus|bonuses|campaign|quest|promotions?|free token|earn btc|claim|incentive|distribution|launchpool)\b', re.I)
RISK_WORDS = re.compile(r'\b(deposit|staking|stake|purchase|buy|trading volume|trading competition|trade to|leverage|futures|referral|invite friends|pay gas|gas fee|swap|mint|fee required)\b', re.I)
LOW_RISK_WORDS = re.compile(r'\b(quiz|learn.?and.?earn|learn\s*&\s*earn|no deposit|free quiz|free course)\b', re.I)
USER_AGENT = 'CryptoAutoAgents/1.0 (read-only public RSS and market data)'


def fetch(url: str, limit: int = 2_000_000) -> bytes:
    if not url.startswith('https://'):
        raise ValueError('Only HTTPS sources are allowed')
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json, application/rss+xml, application/atom+xml, application/xml, */*'})
    with urllib.request.urlopen(req, timeout=17) as response:
        raw = response.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('Response too large')
        return raw


def localname(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


def find_text(node: ET.Element, tagname: str) -> str:
    for child in node:
        if localname(child.tag) == tagname:
            return ''.join(child.itertext()).strip()
    return ''


def parse_feed(raw: bytes, source: str) -> list[dict[str, str]]:
    root = ET.fromstring(raw)
    entries: list[dict[str, str]] = []
    for item in root.iter():
        if localname(item.tag) not in {'item', 'entry'}:
            continue
        title = find_text(item, 'title')[:240]
        link = find_text(item, 'link')
        if not link:
            for child in item:
                if localname(child.tag) == 'link' and (child.get('rel', 'alternate') == 'alternate'):
                    link = child.get('href', '')
                    break
        published = find_text(item, 'pubDate') or find_text(item, 'published') or find_text(item, 'updated')
        summary = find_text(item, 'description') or find_text(item, 'summary')
        summary = re.sub(r'<[^>]*>', ' ', html.unescape(summary))
        summary = re.sub(r'\s+', ' ', summary).strip()[:450]
        if not title or not link.startswith('https://'):
            continue
        if not TRIGGERS.search(title + ' ' + summary):
            continue
        content = title + ' ' + summary
        if RISK_WORDS.search(content):
            flag = 'Похоже, есть платные условия/транзакции: ПРОВЕРИТЬ'
        elif LOW_RISK_WORDS.search(content):
            flag = 'Потенциально без депозита: УСЛОВИЯ НЕ ПРОВЕРЕНЫ'
        else:
            flag = 'Неизвестно, можно ли получить деньги без вложений'
        key = hashlib.sha256((source + '\n' + link).encode('utf-8')).hexdigest()[:18]
        entries.append({'id': key, 'source': source, 'title': title, 'url': link,
                        'published': published[:70], 'summary': summary, 'status': flag})
        if len(entries) >= 30:
            break
    return entries


def get_market_data() -> list[dict[str, Any]]:
    raw = fetch(MARKET_URL)
    value = json.loads(raw.decode('utf-8'))
    if not isinstance(value, list):
        raise ValueError('Unexpected market response')
    out: list[dict[str, Any]] = []
    for coin in value:
        if coin.get('id') not in {'bitcoin', 'ethereum', 'solana'}:
            continue
        price = coin.get('current_price')
        change = coin.get('price_change_percentage_24h')
        out.append({'name': str(coin.get('name', coin['id'])), 'symbol': str(coin.get('symbol','')).upper(),
                    'usd': round(float(price), 6) if price is not None else None,
                    'change24h': round(float(change), 2) if change is not None else None})
    return out


def old_ids() -> set[str]:
    if not STATE_PATH.exists():
        return set()
    try:
        obj = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        return {str(x) for x in obj.get('seen', [])}
    except (ValueError, OSError):
        return set()


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def build_markdown(payload: dict[str, Any]) -> str:
    lines = [
        '# Crypto Auto Agents — последний отчёт', '',
        f"Дата проверки (UTC): **{payload['time_utc']}**", '',
        '**Реально заработано агентами: $0 (автоматическое получение наград не выполняется).**', '',
        '## Агент рынка: наблюдения, не торговые сигналы', '',
        '| Монета | Цена USD | 24ч |', '|---|---:|---:|',
    ]
    for c in payload['market']:
        price = f"${c['usd']:,.4f}" if c['usd'] is not None else 'н/д'
        change = f"{c['change24h']:+.2f}%" if c['change24h'] is not None else 'н/д'
        lines.append(f"| {c['name']} ({c['symbol']}) | {price} | {change} |")
    if not payload['market']:
        lines.append('| Данные временно недоступны | — | — |')
    lines += ['', '## Агент наград: новые находки с прошлой проверки', '']
    if not payload['new']:
        lines.append('Новых подходящих публикаций не найдено; это не означает отсутствия акций.')
    for item in payload['new']:
        lines += [f"- **[{item['title']}]({item['url']})** — {item['source']}",
                  f"  - {item['status']}",
                  f"  - Дата публикации: {item['published'] or 'не указана'}"]
    lines += ['', '## Последние отобранные публикации', '']
    if not payload['latest']:
        lines.append('Данные по публикациям временно отсутствуют.')
    for item in payload['latest'][:15]:
        lines.append(f"- [{item['title']}]({item['url']}) — {item['source']} — {item['status']}")
    lines += ['', '## Проверка источников', '']
    for key, value in payload['health'].items():
        lines.append(f"- {key}: {value}")
    lines += ['', '## Правила безопасности', '',
              '- Ссылки из агрегаторов не являются проверенными claim-сайтами. Сначала открывай первоисточник проекта.',
              '- Не вводи seed phrase, приватный ключ, пароль, CVV или код подтверждения.',
              '- Не считай XP, баллы или виртуальные ваучеры деньгами, пока не проверен реальный вывод.',
              '- Не подключай основной кошелёк к неизвестным сайтам и не подтверждай слепые транзакции.',
              '- Никаких сделок, депозитов и фарминга аккаунтов скрипт не выполняет.', '',
              'Источники: [CoinGecko](https://www.coingecko.com/en/api), '
              '[AirdropAlert](https://airdropalert.com/rss-feed/), '
              '[Kraken](https://blog.kraken.com/), '
              '[Coinbase](https://www.coinbase.com/blog).', '']
    return '\n'.join(lines)


def build_html(payload: dict[str, Any]) -> str:
    def items_table(items: list[dict[str, str]]) -> str:
        if not items:
            return '<p class="muted">Новых публикаций нет или источники недоступны.</p>'
        pieces=[]
        for x in items[:20]:
            pieces.append('<article><a rel="noopener noreferrer" target="_blank" href="'+esc(x['url'])+'">'+esc(x['title'])+'</a><p class="muted">'+esc(x['source'])+' · '+esc(x['published'] or 'дата неизвестна')+'</p><p>'+esc(x['status'])+'</p></article>')
        return '\n'.join(pieces)
    mkt=''.join(f"<tr><td>{esc(x['name'])} ({esc(x['symbol'])})</td><td>${esc(x['usd'])}</td><td>{esc(x['change24h'])}%</td></tr>" for x in payload['market'])
    health=''.join(f'<li>{esc(k)}: {esc(v)}</li>' for k,v in payload['health'].items())
    return '''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Crypto Auto Agents</title>
<style>body{background:#10131a;color:#edf0f6;font:16px/1.55 system-ui,Segoe UI,sans-serif;margin:0}main{max-width:950px;margin:auto;padding:30px 16px}h1{font-size:30px}h2{font-size:21px;margin-top:34px}.muted{color:#aab0bf;font-size:13px}section,.banner{background:#1b202b;border:1px solid #313848;border-radius:14px;padding:18px;margin:16px 0}article{border-bottom:1px solid #373e4d;padding:14px 0}article:last-child{border:0}a{color:#96bcff}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:12px;border-bottom:1px solid #353c4b}.banner{background:#26221d;color:#ffdfb1}ul{padding-left:24px}</style></head><body><main><h1>Crypto Auto Agents</h1><p class="muted">Проверено: ''' + esc(payload['time_utc']) + ''' UTC · Только публичные данные</p><div class="banner"><strong>Заработано автоматически: $0.</strong> Это мониторинг возможностей, не бот для получения токенов и не финансовый совет.</div><section><h2>📈 Наблюдение рынка</h2><table><tr><th>Монета</th><th>USD</th><th>Изменение 24ч</th></tr>''' + mkt + '''</table></section><section><h2>🎁 Новые публикации</h2>''' + items_table(payload['new']) + '''</section><section><h2>🗞️ Отобранные публикации</h2>''' + items_table(payload['latest']) + '''</section><section><h2>🛠️ Доступность источников</h2><ul>''' + health + '''</ul></section><section><h2>🔒 Безопасность</h2><p>Агрегаторы могут содержать мошеннические ссылки. Проверяй официальные условия, страну, депозит, комиссии, сроки, KYC и возможность вывода. Никому не сообщай seed phrase. Скрипт не подключает кошельки и не торгует.</p></section><p class="muted">Источники: CoinGecko, AirdropAlert, Kraken, Coinbase.</p></main></body></html>'''


def send_telegram(payload: dict[str, Any]) -> str:
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    chat = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat:
        return 'Не настроен (необязательно)'
    msg = ('Crypto Auto Agents: ' + payload['time_utc'] + ' UTC\n' +
           f"Новых публикаций: {len(payload['new'])}; реальный заработок: $0\n")
    for c in payload['market']:
        msg += f"{c['symbol']}: ${c['usd']} ({c['change24h']}% за 24ч)\n"
    for x in payload['new'][:5]:
        msg += '\n' + x['title'][:90] + '\n' + x['url']
    data = urllib.parse.urlencode({'chat_id': chat, 'text': msg[:3950], 'disable_web_page_preview': 'true'}).encode()
    url = 'https://api.telegram.org/bot' + token + '/sendMessage'
    req = urllib.request.Request(url, data=data, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=15) as rsp:
            rsp.read(300)
        return 'Отправлено'
    except Exception as exc:
        return f'Ошибка Telegram ({type(exc).__name__}), проверь secrets'


def run() -> dict[str, Any]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M')
    health: dict[str, str] = {}
    market: list[dict[str, Any]] = []
    try:
        market = get_market_data()
        health['CoinGecko (цены)'] = 'OK' if market else 'Нет записей'
    except Exception as exc:
        health['CoinGecko (цены)'] = 'Недоступен: ' + type(exc).__name__
    all_items: list[dict[str, str]] = []
    for name, url in SOURCES.items():
        try:
            entries = parse_feed(fetch(url), name)
            all_items += entries
            health[name] = 'OK, кандидатов: ' + str(len(entries))
        except Exception as exc:
            health[name] = 'Недоступен или формат изменился: ' + type(exc).__name__
    seen = old_ids()
    unique = list({x['id']: x for x in all_items}.values())
    new = [x for x in unique if x['id'] not in seen][:20]
    # Keep the most recent feed items first; no reliance on inconsistent source dates.
    payload = {'time_utc': timestamp, 'market': market, 'new': new, 'latest': unique[:35], 'health': health, 'real_earnings_usd': 0}
    (REPORTS / 'LATEST.md').write_text(build_markdown(payload), encoding='utf-8')
    (REPORTS / 'LATEST.html').write_text(build_html(payload), encoding='utf-8')
    (REPORTS / 'latest.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    STATE_PATH.write_text(json.dumps({'seen': sorted(seen | {x['id'] for x in unique})[-1500:]}, indent=2), encoding='utf-8')
    health['Telegram'] = send_telegram(payload)
    # Rewrite after telegram result is known.
    (REPORTS / 'LATEST.md').write_text(build_markdown(payload), encoding='utf-8')
    (REPORTS / 'LATEST.html').write_text(build_html(payload), encoding='utf-8')
    (REPORTS / 'latest.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"{timestamp} UTC: prices {len(market)}, candidates {len(unique)}, new {len(new)}")
    return payload


if __name__ == '__main__':
    run()
