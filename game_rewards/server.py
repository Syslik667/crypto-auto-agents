"""A loopback-only dashboard. Run with python -m game_rewards.server --open."""
import argparse
import contextlib
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import sqlite3
import webbrowser

from local_telegram.scanner import private_directory
from . import model, sources, store, telegram

STATIC = Path(__file__).with_name("static")
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8")}
BODY_LIMIT = 4_300_000


class Dashboard(ThreadingHTTPServer):
    def __init__(self, port, directory):
        super().__init__(("127.0.0.1", port), Handler)
        self.authority = f"127.0.0.1:{self.server_port}"
        self.origin = "http://" + self.authority
        self.csrf = secrets.token_hex(32)
        self.directory = Path(directory)
        self.database = self.directory / "game-rewards.sqlite3"
        with contextlib.closing(store.open_db(self.database)):
            pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def respond(self, status, kind, data):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob:; "
                         "frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(data)

    def json(self, status, value):
        self.respond(status, "application/json; charset=utf-8",
                     json.dumps(value, ensure_ascii=False, allow_nan=False).encode())

    def allowed_host(self):
        if self.headers.get_all("Host") != [self.server.authority]:
            self.json(403, {"error": "Откройте интерфейс по адресу 127.0.0.1."})
            return False
        return True

    def do_GET(self):
        if not self.allowed_host():
            return
        if self.path in ASSETS:
            filename, kind = ASSETS[self.path]
            data = (STATIC / filename).read_bytes()
            if self.path == "/":
                data = data.replace(b"__LOCAL_TOKEN__", self.server.csrf.encode())
            self.respond(200, kind, data)
            return
        match = re.fullmatch(r"/api/picture/([1-9][0-9]{0,15})", self.path)
        with contextlib.closing(store.open_db(self.server.database)) as db:
            if match:
                picture = store.screenshot(db, int(match[1]))
                if picture:
                    self.respond(200, *picture)
                    return
            elif self.path == "/api/state":
                today = dt.date.today()
                db.execute("BEGIN")
                offers = store.list_offers(db, today)
                self.json(200, {"offers": offers, "summary": store.summary(db, today, offers),
                                "journal": store.journal(db), "platforms": sources.PLATFORMS,
                                "today": today.isoformat()})
                return
        self.json(404, {"error": "Страница не найдена."})

    def do_POST(self):
        if not self.allowed_host():
            return
        supplied = self.headers.get("X-Game-Token", "")
        if (self.headers.get_all("Origin") != [self.server.origin] or not supplied.isascii()
                or not secrets.compare_digest(supplied, self.server.csrf)):
            self.json(403, {"error": "Проверка локального запроса не пройдена. Обновите страницу."})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= BODY_LIMIT or self.headers.get_content_type() != "application/json":
                raise ValueError("Требуется JSON. Размер запроса ограничен 4,3 МБ.")
            raw = json.loads(self.rfile.read(length))
            if not isinstance(raw, dict):
                raise ValueError("Требуется объект JSON.")
            with contextlib.closing(store.open_db(self.server.database)) as db:
                status, body = self.action(db, raw, dt.date.today())
            self.json(status, body)
        except (UnicodeError, json.JSONDecodeError):
            self.json(400, {"error": "Не удалось прочитать JSON."})
        except ValueError as exc:
            self.json(400, {"error": str(exc)})
        except (OverflowError, RecursionError, sqlite3.Error, TimeoutError):
            self.json(400, {"error": "Запрос не обработан. Проверьте формат и попробуйте снова."})

    def action(self, db, raw, today):
        if self.path == "/api/offer":
            offer, created = store.save_offer(db, raw, today)
            return 200, {"id": offer, "created": created}
        if self.path == "/api/import":
            offers = raw.get("offers")
            if not isinstance(offers, list) or not 1 <= len(offers) <= 25:
                raise ValueError("Импортируйте от 1 до 25 карточек за раз.")
            results = []
            for index, row in enumerate(offers, 1):
                try:
                    offer, created = store.save_offer(db, row, today)
                    results.append({"row": index, "id": offer, "created": created})
                except ValueError as exc:
                    results.append({"row": index, "error": str(exc)})
            return 200, {"results": results}
        if self.path == "/api/sources":
            return 200, {"checks": sources.public_status()}
        if self.path == "/api/telegram":
            events = telegram.reminders(store.list_offers(db, today), today)
            return 200, {"message": telegram.notify(db, self.server.directory.parent, events)}
        if self.path in ("/api/start", "/api/progress"):
            offer_id = raw.get("id")
            if type(offer_id) is not int or not 1 <= offer_id < 10 ** 16:
                raise ValueError("Укажите номер предложения.")
            if self.path == "/api/start":
                started = model.date(raw.get("started"))
                if raw.get("attested") is not True or not started or started > today:
                    raise ValueError("Укажите фактическую дату первой установки.")
                store.start(db, offer_id, started)
            else:
                position = raw.get("position")
                if type(position) is not int or not 0 <= position < 30:
                    raise ValueError("Укажите номер этапа.")
                store.advance(db, offer_id, position, raw.get("state"), raw.get("attested"),
                              raw.get("amount"), raw.get("currency"))
            return 200, {"saved": True}
        return 404, {"error": "Действие не найдено."}


def main():
    parser = argparse.ArgumentParser(description="Local Game Reward Agent dashboard")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Use a port between 1024 and 65535")
    directory = private_directory() / "game-rewards"
    directory.mkdir(exist_ok=True, mode=0o700)
    with Dashboard(args.port, directory) as server:
        print(f"Game Reward Agent: {server.origin} (Ctrl+C to stop)", flush=True)
        if args.open:
            webbrowser.open_new_tab(server.origin)
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()


if __name__ == "__main__":
    main()
