from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import requests

HOST = '0.0.0.0'
PORT = int(os.environ.get('PORT', '3000'))
BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / 'public'
REQUIRED_UI_FILES = ('index.html', 'app.js', 'styles.css')
PAGE_SIZE = 25
# Parallel page fetches reduce full-scan time without the burst size of the old 64-worker pool.
BATCH_SIZE = 32
SSE_HEARTBEAT_INTERVAL = 15
MAX_PAGES = 5000
# Keep a complete ~1,250-page contest in memory so later searches reuse fetched pages.
CACHE_LIMIT = 2500

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Origin': 'https://leetcode.com',
    'Referer': 'https://leetcode.com/',
    'sec-fetch-site': 'same-origin',
    'sec-fetch-mode': 'cors',
    'sec-fetch-dest': 'empty',
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)
CACHE_LOCK = threading.Lock()
PAGE_CACHE: dict[str, dict] = {}
INFO_CACHE: dict[str, dict | None] = {}
HISTORY_CACHE: dict[str, list] = {}


def remember(cache: dict, key: str, value):
    with CACHE_LOCK:
        if key in cache:
            del cache[key]
        cache[key] = value
        while len(cache) > CACHE_LIMIT:
            cache.pop(next(iter(cache)))


def normalize(value) -> str:
    return str(value or '').strip().casefold()


def send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict):
    body = json.dumps(payload).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('Content-Length', str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def get_json(url: str, referer: str | None = None, retries: int = 3):
    headers = dict(HEADERS)
    if referer:
        headers['Referer'] = referer
    retryable_statuses = {408, 425, 429, 500, 502, 503, 504}
    last_error = None
    for attempt in range(retries):
        try:
            response = SESSION.get(url, headers=headers, timeout=(5, 12))
            if response.status_code == 200:
                return response.json()
            last_error = RuntimeError(f'upstream returned {response.status_code}')
            # A 403 can be a transient Cloudflare response, but retry it only once
            # before allowing the caller to try the .cn mirror.
            may_retry_403 = response.status_code == 403 and attempt < 2
            if response.status_code not in retryable_statuses and not may_retry_403:
                break
        except Exception as error:  # pragma: no cover - network dependent
            last_error = error
        if attempt + 1 < retries:
            time.sleep(0.25 * (attempt + 1))
    raise last_error or RuntimeError('upstream request failed')


CONTEST_LABELS = {
    'weekly': 'Weekly',
    'biweekly': 'Biweekly',
}


def contest_slug(contest_type: str, contest: int) -> str:
    return f'{contest_type}-contest-{contest}'


def contest_title(contest_type: str, contest: int) -> str:
    return f'{CONTEST_LABELS[contest_type]} Contest {contest}'


def ranking_url(contest_type: str, contest: int, page: int) -> str:
    return f'https://leetcode.com/contest/api/ranking/{contest_slug(contest_type, contest)}/?pagination={page}&region=global_v2'


def cn_ranking_url(contest_type: str, contest: int, page: int) -> str:
    return f'https://leetcode.cn/contest/api/ranking/{contest_slug(contest_type, contest)}/?pagination={page}&region=global_v2'


def fetch_page(contest_type: str, contest: int, page: int) -> dict:
    key = f'{contest_type}:{contest}:{page}'
    with CACHE_LOCK:
        if key in PAGE_CACHE:
            return PAGE_CACHE[key]
    try:
        slug = contest_slug(contest_type, contest)
        data = get_json(ranking_url(contest_type, contest, page), f'https://leetcode.com/contest/{slug}/ranking/{page}/?region=global_v2')
        data['__rankfinder_source'] = 'live'
    except Exception:
        # LeetCode's main API occasionally rate-limits server-side requests with a
        # Cloudflare 403. The official CN mirror exposes the same public leaderboard
        # data and keeps the app usable while the primary endpoint recovers.
        data = get_json(cn_ranking_url(contest_type, contest, page), 'https://leetcode.cn/')
        data['__rankfinder_source'] = 'mirror'
    remember(PAGE_CACHE, key, data)
    return data


def fetch_info(contest_type: str, contest: int) -> dict | None:
    key = f'{contest_type}:{contest}'
    with CACHE_LOCK:
        if key in INFO_CACHE:
            return INFO_CACHE[key]
    try:
        data = get_json(f'https://leetcode.cn/contest/api/info/{contest_slug(contest_type, contest)}/', 'https://leetcode.cn/')
        info = data.get('contest')
    except Exception:
        info = None
    remember(INFO_CACHE, key, info)
    return info


def fetch_history(username: str) -> list:
    key = normalize(username)
    with CACHE_LOCK:
        if key in HISTORY_CACHE:
            return HISTORY_CACHE[key]
    query = '''query ContestHistory($username: String!) {
      userContestRankingHistory(username: $username) {
        attended ranking problemsSolved totalProblems finishTimeInSeconds rating
        contest { title titleSlug startTime }
      }
    }'''
    response = SESSION.post(
        'https://leetcode.com/graphql',
        headers={**HEADERS, 'Content-Type': 'application/json'},
        json={'operationName': 'ContestHistory', 'variables': {'username': username}, 'query': query},
        timeout=(5, 12),
    )
    response.raise_for_status()
    history = response.json().get('data', {}).get('userContestRankingHistory') or []
    remember(HISTORY_CACHE, key, history)
    return history


def match_row(row: dict, username: str) -> bool:
    target = normalize(username)
    return any(normalize(row.get(key)) == target for key in ('username', 'user_slug', 'userSlug'))


def find_row(data: dict | None, username: str):
    if not data:
        return None
    return next((row for row in data.get('total_rank', []) if match_row(row, username)), None)


def page_for_rank(rank: int) -> int:
    return max(1, math.ceil(int(rank) / PAGE_SIZE))


def result_for(row: dict, contest_type: str, contest: int, page: int, info: dict | None, source: str, **extra):
    start_time = int((info or {}).get('start_time') or (info or {}).get('origin_start_time') or 0)
    finish_timestamp = int(row.get('finish_time') or 0)
    finish_time = max(0, finish_timestamp - start_time) if finish_timestamp and start_time else None
    user_slug = row.get('user_slug') or row.get('userSlug') or row.get('username')
    return {
        'contestType': contest_type,
        'contest': contest,
        'contestTitle': contest_title(contest_type, contest),
        'username': user_slug,
        'displayName': row.get('username') or user_slug,
        'rank': int(row.get('rank') or row.get('rank_v2') or 0),
        'score': row.get('score'),
        'finishTime': finish_time,
        'finishTimestamp': finish_timestamp or None,
        'country': row.get('country_name'),
        'countryCode': row.get('country_code'),
        'profileUrl': f'https://leetcode.com/u/{quote(str(user_slug), safe="")}/',
        'rankingUrl': f'https://leetcode.com/contest/{contest_slug(contest_type, contest)}/ranking/{page}/?region=global_v2',
        'page': page,
        'source': source,
        'problemsSolved': extra.get('problemsSolved'),
        'totalProblems': extra.get('totalProblems'),
        'rating': extra.get('rating'),
        'totalParticipants': extra.get('totalParticipants'),
    }


def write_event(handler: BaseHTTPRequestHandler, event: str, payload: dict):
    message = f'event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n'.encode('utf-8')
    lock = getattr(handler, '_rankfinder_stream_lock', None)
    try:
        if lock:
            with lock:
                handler.wfile.write(message)
                handler.wfile.flush()
        else:
            handler.wfile.write(message)
            handler.wfile.flush()
    except OSError as error:
        raise ConnectionError('client disconnected') from error


def search_stream(handler: BaseHTTPRequestHandler, contest_type: str, contest: int, username: str):
    handler.send_response(HTTPStatus.OK)
    handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
    handler.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
    handler.send_header('Connection', 'close')
    handler.send_header('X-Accel-Buffering', 'no')
    handler.close_connection = True
    handler.end_headers()
    stream_lock = threading.Lock()
    handler._rankfinder_stream_lock = stream_lock
    with stream_lock:
        handler.wfile.write(b': connected\n\n')
        handler.wfile.flush()

    progress_state = {'scanned': 0, 'totalPages': 0}
    state_lock = threading.Lock()
    heartbeat_stop = threading.Event()

    def heartbeat():
        while not heartbeat_stop.wait(SSE_HEARTBEAT_INTERVAL):
            with state_lock:
                snapshot = dict(progress_state)
            try:
                write_event(handler, 'heartbeat', snapshot)
            except ConnectionError:
                return

    heartbeat_thread = threading.Thread(target=heartbeat, name='rankfinder-sse-heartbeat', daemon=True)
    heartbeat_thread.start()

    def status(message: str, detail: str = ''):
        write_event(handler, 'status', {'message': message, 'detail': detail})

    def progress(scanned: int, total_pages: int):
        percent = min(100, round(scanned / total_pages * 100, 1)) if total_pages else 0
        with state_lock:
            progress_state.update({'scanned': scanned, 'totalPages': total_pages})
        write_event(handler, 'progress', {
            'scanned': scanned,
            'totalPages': total_pages,
            'percent': percent,
        })

    try:
        status(f'Checking {contest_title(contest_type, contest)}', 'Looking for a fast match before scanning the leaderboard.')
        try:
            history = fetch_history(username)
        except Exception:
            history = []
        desired_title = normalize(contest_title(contest_type, contest))
        history_entry = next((entry for entry in history if entry.get('attended') and entry.get('ranking', 0) > 0 and normalize((entry.get('contest') or {}).get('title')) == desired_title), None)
        history_page = page_for_rank(history_entry['ranking']) if history_entry else None
        info = fetch_info(contest_type, contest)

        if history_entry:
            status('Match found in contest history', f"Opening the exact leaderboard row for rank {int(history_entry['ranking']):,}.")
            page = history_page
            try:
                data = fetch_page(contest_type, contest, page)
                row = find_row(data, username)
            except Exception:
                row = None
                data = None
            if row:
                total_pages = max(page, math.ceil((data.get('user_num') or history_entry['ranking']) / PAGE_SIZE))
                progress(1, total_pages)
                write_event(handler, 'result', result_for(
                    row, contest_type, contest, page, info, 'contest history + leaderboard' if data.get('__rankfinder_source') == 'live' else 'leaderboard mirror',
                    problemsSolved=history_entry.get('problemsSolved'),
                    totalProblems=history_entry.get('totalProblems'),
                    rating=history_entry.get('rating'),
                    totalParticipants=data.get('user_num'),
                ))
                write_event(handler, 'done', {})
                return

            # A user's historical rank is still useful when LeetCode has since
            # reprocessed the leaderboard and the old row moved to another page.
            # Return it immediately rather than making the user wait for a full scan.
            if info and history_entry.get('finishTimeInSeconds') is not None:
                history_row = {
                    'username': username,
                    'user_slug': username,
                    'rank': history_entry.get('ranking'),
                    'score': None,
                    'finish_time': int(info.get('start_time') or info.get('origin_start_time') or 0) + int(history_entry.get('finishTimeInSeconds') or 0),
                }
                write_event(handler, 'result', result_for(
                    history_row, contest_type, contest, history_page, info, 'contest history',
                    problemsSolved=history_entry.get('problemsSolved'),
                    totalProblems=history_entry.get('totalProblems'),
                    rating=history_entry.get('rating'),
                ))
                write_event(handler, 'done', {})
                return

        status(f'Scanning {contest_title(contest_type, contest)} leaderboard', 'This contest is not in the user history yet, so pages are being searched directly.')
        first_page = fetch_page(contest_type, contest, 1)
        total_users = int(first_page.get('user_num') or first_page.get('total_users') or 0)
        total_pages = min(MAX_PAGES, max(1, math.ceil(total_users / PAGE_SIZE)))
        status(
            f'Scanning {contest_title(contest_type, contest)} leaderboard',
            f'Checking {total_users:,} participants across {total_pages:,} pages; progress updates as pages return.',
        )
        progress(0, total_pages)

        if history_page:
            # Rankings can change after a contest (for example, after a correction or
            # disqualification). Search around the historical rank first so older
            # biweekly contests do not require a full 40k-user scan.
            nearby_start = max(1, history_page - 128)
            nearby_end = min(total_pages, history_page + 128)
            nearby_pages = list(range(nearby_start, nearby_end + 1))
            nearby_set = set(nearby_pages)
            scan_order = nearby_pages + [page for page in range(1, total_pages + 1) if page not in nearby_set]
            status('Checking the likely ranking range', 'The leaderboard changed after the contest, so the historical rank is being used as a shortcut.')
        else:
            scan_order = list(range(1, total_pages + 1))

        completed = 0
        from concurrent.futures import ThreadPoolExecutor, as_completed
        for start in range(0, len(scan_order), BATCH_SIZE):
            pages = scan_order[start:start + BATCH_SIZE]
            found = None
            found_page = None
            found_source = 'live'
            # Keep concurrency moderate and report each completed request so the UI
            # doesn't sit at 0% while waiting for an entire batch to finish.
            executor = ThreadPoolExecutor(max_workers=min(BATCH_SIZE, len(pages)))
            futures = {executor.submit(fetch_page, contest_type, contest, page): page for page in pages}
            try:
                for future in as_completed(futures):
                    page = futures[future]
                    completed += 1
                    try:
                        data = future.result()
                    except Exception:
                        progress(completed, total_pages)
                        continue
                    row = find_row(data, username)
                    if row and found is None:
                        found, found_page = row, page
                        found_source = 'live' if data.get('__rankfinder_source') == 'live' else 'mirror'
                    progress(completed, total_pages)
                    if found:
                        for pending in futures:
                            if pending is not future:
                                pending.cancel()
                        break
            finally:
                executor.shutdown(wait=found is None, cancel_futures=found is not None)
            if found:
                source_label = 'global leaderboard' if found_source == 'live' else 'leaderboard mirror'
                write_event(handler, 'result', result_for(found, contest_type, contest, found_page, info, source_label, totalParticipants=total_users))
                write_event(handler, 'done', {})
                return

        write_event(handler, 'not_found', {'contest': contest, 'username': username, 'message': f'No exact match for “{username}” was found in {contest_title(contest_type, contest)}.'})
        write_event(handler, 'done', {})
    except (BrokenPipeError, ConnectionResetError, ConnectionError):
        return
    except Exception as error:
        try:
            write_event(handler, 'error', {'message': 'The leaderboard could not be reached right now.', 'detail': str(error)})
            write_event(handler, 'done', {})
        except Exception:
            pass
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        # Keep the preview log readable; upstream status is surfaced in the app.
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/health':
            missing = [name for name in REQUIRED_UI_FILES if not (PUBLIC_DIR / name).is_file()]
            if missing:
                return send_json(self, 503, {'ok': False, 'error': 'Required UI files are missing from public/.', 'missing': missing})
            return send_json(self, 200, {'ok': True})
        if parsed.path == '/api/search':
            params = parse_qs(parsed.query)
            contest_type = (params.get('contestType') or params.get('type') or ['weekly'])[0].lower()
            contest_value = (params.get('contest') or [''])[0]
            username = (params.get('username') or [''])[0].strip()
            if contest_type not in CONTEST_LABELS:
                return send_json(self, 400, {'error': 'Choose weekly or biweekly contest.'})
            if not re.fullmatch(r'\d{1,5}', contest_value or '') or not 1 <= int(contest_value) <= 9999:
                return send_json(self, 400, {'error': 'Enter a valid contest number.'})
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,40}', username):
                return send_json(self, 400, {'error': 'Enter a valid LeetCode username.'})
            return search_stream(self, contest_type, int(contest_value), username)
        return self.serve_static(parsed.path)

    def serve_static(self, request_path: str):
        relative = 'index.html' if request_path in ('', '/') else request_path.lstrip('/')
        target = (PUBLIC_DIR / relative).resolve()
        if PUBLIC_DIR.resolve() not in target.parents and target != PUBLIC_DIR.resolve():
            return send_json(self, 403, {'error': 'Forbidden'})
        if not target.exists() or not target.is_file():
            return send_json(self, 404, {'error': 'File not found'})
        content_types = {
            '.html': 'text/html; charset=utf-8',
            '.css': 'text/css; charset=utf-8',
            '.js': 'text/javascript; charset=utf-8',
            '.json': 'application/json; charset=utf-8',
            '.svg': 'image/svg+xml',
        }
        body = target.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', content_types.get(target.suffix, 'application/octet-stream'))
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    # Warm a generic public GraphQL request so LeetCode can set its CSRF cookie.
    # Avoid querying or hardcoding any account during app startup.
    try:
        SESSION.get('https://leetcode.com/graphql?query=query%20%7B%20__typename%20%7D', timeout=8)
    except Exception:
        pass
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f'RankFinder running at http://{HOST}:{PORT}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
