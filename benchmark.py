"""Real endpoint benchmark. Stdlib only; never substitutes a fake model.

Use an isolated preview for repeatable before/after runs. This deliberately
respects quotas, records rejections, and does not retry or bypass challenges.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import http.cookiejar
import json
import math
import statistics
import subprocess
import threading
import time
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CASES = [
    ('faq', 'What does Tom specialize in?'),
    ('generated', 'How does the SQL agent explorer pattern resolve ambiguous schema?'),
    ('followup', 'What safeguards does it use before executing SQL?'),
    ('unsupported', 'What is Tom\'s private bank account number?'),
    ('faq', 'How can I contact Tom?'),
]


def opener():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def ask(client, url, question):
    started = time.perf_counter()
    result = {'question': question, 'http_status': None, 'first_answer_seconds': None, 'success': False}
    request = urllib.request.Request(url.rstrip('/') + '/api/chat/stream',
                                     data=json.dumps({'question': question}).encode(),
                                     headers={'Content-Type': 'application/json'})
    try:
        with client.open(request, timeout=180) as response:
            result['http_status'] = response.status
            event = None
            done = False
            for raw in response:
                line = raw.decode('utf-8').strip()
                if line.startswith('event:'):
                    event = line[6:].strip()
                elif line.startswith('data:'):
                    data = json.loads(line[5:])
                    if event == 'delta' and data.get('text') and result['first_answer_seconds'] is None:
                        result['first_answer_seconds'] = time.perf_counter() - started
                    if event == 'final':
                        result.update(answer=data.get('answer'), sources=data.get('sources'), response_mode=data.get('response_mode'), metrics=data.get('metrics'))
                        result['success'] = bool(data.get('answer'))
                    elif event == 'error':
                        result.update(error=data.get('error'), success=False)
                    elif event == 'done':
                        done = True
            if not done:
                result.update(success=False, error=result.get('error', 'Incomplete SSE response'))
    except urllib.error.HTTPError as error:
        result.update(http_status=error.code, retry_after=error.headers.get('Retry-After'), error='HTTP rejection')
    except (OSError, ValueError) as error:
        result['error'] = type(error).__name__
    result['seconds'] = time.perf_counter() - started
    return result


def summarize(rows):
    summaries = {}
    # Do not pool instant FAQ with generated latency or hide failed requests.
    def category(row):
        return row.get('response_mode', 'legacy') if row['success'] else 'rejected'
    for mode in sorted({category(r) for r in rows}):
        group = [r for r in rows if category(r) == mode]
        times = sorted(r['seconds'] for r in group if r['success'])
        summaries[mode] = {'count': len(group), 'successful': len(times),
                           'p50_seconds': statistics.median(times) if times else None,
                           'p95_seconds': times[max(0, math.ceil(.95 * len(times)) - 1)] if times else None}
    return summaries


def sample_cpu(container, stop, samples):
    while not stop.is_set():
        try:
            value = subprocess.run(['docker', 'stats', '--no-stream', '--format', '{{json .}}', container],
                                   capture_output=True, text=True, timeout=5, check=True)
            sample = json.loads(value.stdout)
            samples.append({'elapsed_monotonic': time.monotonic(), 'cpu_percent': float(sample['CPUPerc'].rstrip('%')),
                            'memory': sample['MemUsage']})
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            samples.append({'error': 'CPU sampling unavailable'})
            return
        stop.wait(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', help='Isolated preview endpoint, e.g. http://127.0.0.1:8081')
    parser.add_argument('--faq-only-local', action='store_true', help='Measure only local FAQ lookup + SQLite persistence, without HTTP, retrieval, or a model')
    parser.add_argument('--output', default='benchmark-results.json')
    parser.add_argument('--label', required=True, help='Release SHA/mode/CPU cap, recorded verbatim')
    parser.add_argument('--delay', type=float, default=22, help='Seconds between sequential requests; respects default minute quota')
    parser.add_argument('--overlap', action='store_true', help='After a 61-second cooldown, submit three generated requests together')
    parser.add_argument('--container', help='Optional exact Docker model container name for CPU sampling')
    args = parser.parse_args()
    if args.delay < 0:
        parser.error('--delay must be nonnegative')
    if args.faq_only_local:
        if args.url or args.overlap or args.container:
            parser.error('--faq-only-local cannot be mixed with endpoint options')
        import agent
        from memory import ConversationMemory
        previous = agent.MEMORY
        rows = []
        with tempfile.TemporaryDirectory() as temp:
            agent.MEMORY = ConversationMemory(Path(temp) / 'benchmark.sqlite3')
            cpu_start = time.process_time()
            try:
                for i in range(50):
                    for faq in agent.FAQ:
                        question = sorted(faq['questions'])[0]
                        start = time.perf_counter()
                        result = agent.curated_answer(question)
                        agent.save_exchange(question, result['answer'], f'benchmark-{i}-{question}')
                        rows.append({'seconds': time.perf_counter() - start, 'response_mode': result['response_mode'], 'success': True})
                cpu_seconds = time.process_time() - cpu_start
            finally:
                agent.MEMORY = previous
        report = {'label': args.label, 'created_at': datetime.now(timezone.utc).isoformat(),
                  'kind': 'local-faq-only', 'summary': summarize(rows), 'samples': rows,
                  'process_cpu_seconds': cpu_seconds,
                  'notes': ['Real local FAQ lookup and SQLite writes in a temporary database.',
                            'Excludes HTTP, quotas, challenge verification, network, embeddings, and generation.',
                            'Not a VPS inference benchmark or evidence of generated-answer latency.']}
        Path(args.output).write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report['summary'], indent=2))
        return 0
    if not args.url:
        parser.error('--url is required unless --faq-only-local is selected')
    report = {'label': args.label, 'created_at': datetime.now(timezone.utc).isoformat(), 'url': args.url,
              'kind': 'real-endpoint', 'cpu_samples': [], 'sequential': [], 'overlap': []}
    stop = threading.Event()
    sampler = None
    if args.container:
        sampler = threading.Thread(target=sample_cpu, args=(args.container, stop, report['cpu_samples']), daemon=True)
        sampler.start()
    try:
        client = opener()  # Cookies persist through the follow-up case.
        for i, (case, question) in enumerate(CASES):
            if i:
                time.sleep(args.delay)
            row = ask(client, args.url, question)
            row['case'] = case
            report['sequential'].append(row)
            print(json.dumps(row), flush=True)
        if args.overlap:
            time.sleep(61)
            barrier = threading.Barrier(3)
            def simultaneous(i):
                barrier.wait()
                return ask(opener(), args.url, CASES[1][1])
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                report['overlap'] = list(pool.map(simultaneous, range(3)))
    finally:
        stop.set()
        if sampler:
            sampler.join(6)
        report['summary'] = summarize(report['sequential'])
        values = [s['cpu_percent'] for s in report['cpu_samples'] if 'cpu_percent' in s]
        report['model_container_cpu_peak_percent'] = max(values) if values else None
        report['notes'] = ['Docker CPU percent is per logical CPU; 100% means approximately one CPU.',
                           'Small-sample p95 is descriptive, not a production latency estimate.',
                           'Source IDs and answer text require human factual review.',
                           'No first status event is counted as first answer text.']
        Path(args.output).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report['summary'], indent=2))
    return 0 if all(r['success'] for r in report['sequential']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
