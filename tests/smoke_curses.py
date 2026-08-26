"""Drives the real curses interface inside a pty (see test_smoke_curses.py).

Exits 0 and writes "OK" to $NCEU_SMOKE_RESULT when every screen rendered and
the queue drained without raising; writes the traceback and exits 1 otherwise.
"""
import base64
import curses
import os
import sys
import threading
import traceback

sys.path.insert(0, os.getcwd())

from nceu import main

RESULT = os.environ['NCEU_SMOKE_RESULT']


def encode(text):
    return base64.urlsafe_b64encode(text.encode('utf-8')).decode('ascii')


def plain_part(text):
    return {'mimeType': 'text/plain',
            'headers': [{'name': 'Content-Type', 'value': 'text/plain; charset=UTF-8'}],
            'body': {'data': encode(text)}}


def message_payload(message_id, quoted=False):
    paragraph = ' '.join(f"word{n}" for n in range(120))
    body = f"Body of {message_id}\n\n{paragraph}\n\nBye."
    if quoted:
        body += "\n\nOn Mon, 1 Jan 2024, Someone wrote:\n> quoted one\n> quoted two\n> quoted three"
    return {
        'mimeType': 'multipart/mixed',
        'headers': [
            {'name': 'From', 'value': 'Someone <someone@example.com>'},
            {'name': 'To', 'value': 'me@example.com'},
            {'name': 'Date', 'value': 'Mon, 1 Jan 2024 10:00:00 +0000'},
            {'name': 'Subject', 'value': f'Subject of {message_id}'},
        ],
        'parts': [
            plain_part(body),
            {'mimeType': 'application/pdf', 'filename': 'report.pdf',
             'body': {'size': 2048, 'attachmentId': 'att1'}},
        ],
    }


class FakeService:
    def __init__(self):
        self.lock = threading.Lock()
        self.archived = []
        self.body_fetches = []
        self.thread_fetches = []

    def users(self):
        return self

    def messages(self):
        return self

    def threads(self):
        return _Threads(self)

    def modify(self, userId=None, id=None, body=None):
        service = self

        class Exec:
            def execute(self, num_retries=0):
                with service.lock:
                    service.archived.append(id)
                return {'id': id}

        return Exec()

    def get(self, userId=None, id=None, format=None):
        service = self

        class Exec:
            def execute(self, num_retries=0):
                with service.lock:
                    service.body_fetches.append(id)
                return {'id': id, 'snippet': 'snippet text', 'payload': message_payload(id)}

        return Exec()


class _Threads:
    def __init__(self, service):
        self.service = service

    def get(self, userId=None, id=None, format=None):
        service = self.service

        class Exec:
            def execute(self, num_retries=0):
                with service.lock:
                    service.thread_fetches.append(id)
                return {'id': id, 'messages': [
                    {'id': f'{id}-msg1', 'labelIds': ['INBOX'], 'snippet': 's1',
                     'payload': message_payload(f'{id}-msg1')},
                    {'id': f'{id}-msg2', 'labelIds': ['SENT'], 'snippet': 's2',
                     'payload': message_payload(f'{id}-msg2', quoted=True)},
                ]}

        return Exec()


SERVICE = FakeService()


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


class TracingInterface(main.NCDULikeInterface):
    """Records which screens were actually reached, so the smoke test can
    assert the key sequence exercised them instead of silently missing."""

    trace = []

    def show_email_details(self):
        TracingInterface.trace.append(f'reader:{self.view_mode}')
        super().show_email_details()

    def show_queue(self):
        TracingInterface.trace.append('queue-view')
        super().show_queue()

    def show_thread(self, thread_id, focus_id=None):
        TracingInterface.trace.append(f'thread:{self.view_mode}')
        super().show_thread(thread_id, focus_id=focus_id)

    def enqueue_current(self):
        TracingInterface.trace.append(f'enqueue:{self.view_mode}')
        super().enqueue_current()


def run(stdscr):
    queue = main.ArchiveQueue(creds=object())
    queue.start()
    try:
        interface = TracingInterface(stdscr, build_emails(), SERVICE, queue)
        interface.run()
    finally:
        queue.shutdown(timeout=10)
    return queue


def entry():
    main.build = lambda *args, **kwargs: SERVICE
    main.ARCHIVE_DELAY = 1
    queue = curses.wrapper(run)
    with open(RESULT, 'w') as handle:
        handle.write(f"OK archived={queue.archived_count} errors={len(queue.errors)} "
                     f"bodies={len(SERVICE.body_fetches)} threads={len(SERVICE.thread_fetches)}\n")
        handle.write("trace=" + ",".join(TracingInterface.trace) + "\n")


if __name__ == '__main__':
    try:
        entry()
    except BaseException:
        with open(RESULT, 'w') as handle:
            handle.write(traceback.format_exc())
        sys.exit(1)
