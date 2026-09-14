#!/usr/bin/env python3
"""Fire concurrent requests at a running ticketing backend and report what came back.

The Django test suite proves the invariants against a test database. This script asks the
question the suite cannot: how does the *deployed* process behave -- gunicorn's worker
model, Neon's cold start, Render's proxy -- when many buyers arrive at once.

Standard library only, so it runs anywhere Python does:

    uv run python scripts/loadtest.py                       # reads against localhost
    uv run python scripts/loadtest.py --write               # also books and confirms
    uv run python scripts/loadtest.py --base-url https://tickets-6cko.onrender.com

Reads (health, availability) are always safe. Writes create real bookings, so --write is
refused for any host other than localhost unless --allow-remote-writes is also passed.
Never point that at production: every booking it makes holds a real seat for the TTL.

Exit status is non-zero if any request returned 5xx, if a response was not JSON, or if
the event ended up over capacity -- so this can gate a deploy.
"""

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

LOCAL_HOSTS = {'localhost', '127.0.0.1', '::1'}


class Result:
    __slots__ = ('status', 'seconds', 'body', 'error')

    def __init__(self, status, seconds, body=None, error=None):
        self.status = status
        self.seconds = seconds
        self.body = body
        self.error = error


def call(base_url, path, method='GET', body=None, headers=None, timeout=90):
    """One HTTP request, timed. Never raises: transport failures become status 0."""
    request = urllib.request.Request(
        base_url + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type': 'application/json', **(headers or {})},
        method=method,
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return Result(0, time.perf_counter() - started, error=str(exc))
    seconds = time.perf_counter() - started
    try:
        return Result(status, seconds, body=json.loads(raw))
    except ValueError:
        return Result(status, seconds, error=f'non-JSON body: {raw[:120]!r}')


def run_parallel(fn, items, concurrency):
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        return list(pool.map(fn, items))


def percentile(values, pct):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))
    return ordered[index]


def report(title, results):
    statuses = Counter(r.status for r in results)
    latencies = [r.seconds for r in results if r.status]
    print(f'\n{title}')
    print(f'  requests : {len(results)}')
    print('  statuses : ' + ', '.join(
        f'{status or "transport error"}={count}' for status, count in sorted(statuses.items())
    ))
    if latencies:
        print(
            f'  latency  : p50 {percentile(latencies, 50):.3f}s  '
            f'p95 {percentile(latencies, 95):.3f}s  '
            f'p99 {percentile(latencies, 99):.3f}s  '
            f'max {max(latencies):.3f}s  '
            f'mean {statistics.fmean(latencies):.3f}s'
        )
    errors = [r.error for r in results if r.error]
    for error in errors[:5]:
        print(f'  error    : {error}')
    if len(errors) > 5:
        print(f'  ... and {len(errors) - 5} more')


def failures(results):
    """Requests that count as a defect: server errors, unreachable, or unparseable."""
    return [r for r in results if r.status == 0 or r.status >= 500 or r.error]


def read_phase(base_url, requests, concurrency):
    # Wake the stack first and time it on its own: a cold Render dyno plus a cold Neon
    # is the single worst latency a buyer will ever see, and it should not be averaged
    # away inside the burst numbers.
    wake = call(base_url, '/api/health/')
    print(f'first /api/health/ (cold start if asleep): {wake.status} in {wake.seconds:.2f}s')

    paths = ['/api/availability/' if i % 4 else '/api/health/' for i in range(requests)]
    results = run_parallel(lambda p: call(base_url, p), paths, concurrency)
    report(f'READ  {concurrency} concurrent x {requests}', results)
    return results


def write_phase(base_url, buyers, concurrency, run_id):
    def book(index):
        return call(
            base_url, '/api/bookings/', method='POST',
            body={
                'buyer_name': f'Loadtest {run_id} {index}',
                'buyer_whatsapp': f'+549119{index:06d}',
                'sender_account_name': 'loadtest',
                'guests': [{'full_name': f'Loadtest {run_id} guest {index}-{g}'}
                           for g in range((index % 3) + 1)],
            },
            # DRF keys the throttle on X-Forwarded-For when present. Distinct values
            # simulate a crowd of buyers; without them this script is the runaway client
            # the throttle exists to stop, and everything past request 120 is a 429.
            headers={'X-Forwarded-For': f'10.{(index >> 8) & 255}.{index & 255}.1'},
        )

    created = run_parallel(book, range(buyers), concurrency)
    report(f'BOOK  {concurrency} concurrent x {buyers}', created)

    references = [r.body['reference'] for r in created if r.status == 201]
    sold_out = sum(1 for r in created if r.status == 409)
    print(f'  outcome  : {len(references)} booked, {sold_out} told sold out')

    if not references:
        return created

    confirmed = run_parallel(
        lambda ref: call(base_url, f'/api/bookings/{ref}/confirm/', method='POST'),
        references, concurrency,
    )
    report(f'CONFIRM {concurrency} concurrent x {len(references)}', confirmed)
    return created + confirmed


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--concurrency', type=int, default=20)
    parser.add_argument('--requests', type=int, default=200,
                        help='read requests to fire (default 200)')
    parser.add_argument('--buyers', type=int, default=40,
                        help='bookings to attempt with --write (default 40)')
    parser.add_argument('--write', action='store_true',
                        help='also POST bookings and confirm them (localhost only)')
    parser.add_argument('--allow-remote-writes', action='store_true',
                        help='let --write target a non-local host. Creates real bookings.')
    args = parser.parse_args()

    base_url = args.base_url.rstrip('/')
    host = urllib.parse.urlparse(base_url).hostname
    if args.write and host not in LOCAL_HOSTS and not args.allow_remote_writes:
        sys.exit(
            f'refusing --write against {host}: it would create real bookings. '
            f'Pass --allow-remote-writes if that is truly what you want.'
        )

    print(f'target: {base_url}')
    all_results = read_phase(base_url, args.requests, args.concurrency)

    if args.write:
        run_id = time.strftime('%H%M%S')
        all_results += write_phase(base_url, args.buyers, args.concurrency, run_id)

    final = call(base_url, '/api/availability/')
    over_capacity = False
    if final.body:
        taken, capacity = final.body.get('taken'), final.body.get('capacity')
        over_capacity = taken is not None and capacity is not None and taken > capacity
        print(f'\nfinal availability: taken {taken} / capacity {capacity}'
              + ('  ** OVER CAPACITY **' if over_capacity else ''))

    bad = failures(all_results)
    print(f'\n{"FAIL" if bad or over_capacity else "PASS"}: '
          f'{len(bad)} server errors / transport failures out of {len(all_results)} requests')
    sys.exit(1 if bad or over_capacity else 0)


if __name__ == '__main__':
    main()
