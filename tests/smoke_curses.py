"""Drives the real curses interface inside a pty (see test_smoke_curses.py).

Exits 0 and writes "OK" to $NCEU_SMOKE_RESULT when every screen rendered and
the queue drained without raising; writes the traceback and exits 1 otherwise.
"""
import curses
import os
import sys
import threading
import traceback

sys.path.insert(0, os.getcwd())

from nceu import main

RESULT = os.environ['NCEU_SMOKE_RESULT']


class FakeService:
    def __init__(self):
        self.lock = threading.Lock()
        self.archived = []

    def users(self):
        return self

    def messages(self):
        return self

    def modify(self, userId=None, id=None, body=None):
        service = self

        class Exec:
            def execute(self, num_retries=0):
                with service.lock:
                    service.archived.append(id)
                return {'id': id}

        return Exec()


def build_emails():
    emails = []
    for i in range(40):
        sender = f"Person {i % 7} <p{i % 7}@example.com>"
        emails.append({
            'id': f'msg{i}',
            'threadId': f'thread{i % 5}',
            'subject': f'Subject number {i}',
            'sender': sender,
            'size': 1000 + i,
            'date': f'Mon, {1 + i % 28:02d} Jan 2024 10:00:00 +0000',
            'state': None,
            'task': None,
        })
    return emails


def run(stdscr):
    queue = main.ArchiveQueue(creds=object())
    queue.start()
    try:
        interface = main.NCDULikeInterface(stdscr, build_emails(), None, queue)
        interface.run()
    finally:
        queue.shutdown(timeout=10)
    return queue


def entry():
    main.build = lambda *args, **kwargs: FakeService()
    main.ARCHIVE_DELAY = 1
    queue = curses.wrapper(run)
    with open(RESULT, 'w') as handle:
        handle.write(f"OK archived={queue.archived_count} errors={len(queue.errors)}\n")


if __name__ == '__main__':
    try:
        entry()
    except BaseException:
        with open(RESULT, 'w') as handle:
            handle.write(traceback.format_exc())
        sys.exit(1)
