"""Behaviour tests for the background archive queue.

They exercise the real ArchiveQueue worker thread against a fake Gmail
service, so they assert what actually happened to each message - not just
that a call returned without raising.
"""
import threading
import time

import pytest

from nceu import main


DELAY = 0.3


@pytest.fixture(autouse=True)
def fast_delay(monkeypatch):
    monkeypatch.setattr(main, 'ARCHIVE_DELAY', DELAY)


class FakeExecutable:
    def __init__(self, service, message_id, body):
        self.service = service
        self.message_id = message_id
        self.body = body

    def execute(self, num_retries=0):
        with self.service.lock:
            self.service.calls.append((self.message_id, self.body))
            if self.message_id in self.service.fail_ids:
                raise RuntimeError(f"boom for {self.message_id}")
            self.service.archived.append(self.message_id)
        return {'id': self.message_id}


class FakeMessages:
    def __init__(self, service):
        self.service = service

    def modify(self, userId=None, id=None, body=None):
        return FakeExecutable(self.service, id, body)


class FakeUsers:
    def __init__(self, service):
        self.service = service

    def messages(self):
        return FakeMessages(self.service)


class FakeService:
    def __init__(self, fail_ids=()):
        self.lock = threading.Lock()
        self.calls = []
        self.archived = []
        self.fail_ids = set(fail_ids)

    def users(self):
        return FakeUsers(self)


@pytest.fixture
def gmail(monkeypatch):
    service = FakeService()
    monkeypatch.setattr(main, 'build', lambda *a, **kw: service)
    return service


def make_email(msg_id, subject='Subject'):
    return {
        'id': msg_id,
        'threadId': f'thread-{msg_id}',
        'subject': subject,
        'sender': f'Someone <{msg_id}@example.com>',
        'size': 100,
        'date': 'Mon, 1 Jan 2024 10:00:00 +0000',
        'state': None,
        'task': None,
    }


@pytest.fixture
def queue():
    q = main.ArchiveQueue(creds=object())
    q.start()
    yield q
    q.shutdown(timeout=5)


def wait_until(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def enqueue(queue, emails, label='task'):
    task = main.ArchiveTask(label, emails)
    for email in emails:
        email['task'] = task
        email['state'] = main.STATE_QUEUED
    queue.enqueue(task)
    return task


def test_nothing_is_archived_during_the_undo_window(gmail, queue):
    email = make_email('m1')
    enqueue(queue, [email])

    time.sleep(DELAY / 2)

    assert gmail.calls == [], "message left the inbox before the undo window expired"
    assert email['state'] == main.STATE_QUEUED
    assert queue.pending_count() == 1


def test_cancelled_task_never_reaches_gmail(gmail, queue):
    email = make_email('m1')
    task = enqueue(queue, [email])

    assert queue.cancel(task) is True
    main.clear_task_marks(task)

    time.sleep(DELAY * 2)

    assert gmail.calls == [], "cancelled task was still sent to Gmail"
    assert gmail.archived == []
    assert email['state'] is None, "cancelled email must lose its queued marker"
    assert email['task'] is None
    assert queue.pending_count() == 0
    assert queue.archived_count == 0


def test_task_is_archived_after_the_delay(gmail, queue):
    emails = [make_email('m1'), make_email('m2')]
    task = enqueue(queue, emails, label='Sender A')

    assert wait_until(lambda: task.status == main.STATE_ARCHIVED)

    assert gmail.archived == ['m1', 'm2']
    assert [body for _, body in gmail.calls] == [{'removeLabelIds': ['INBOX']}] * 2
    assert all(email['state'] == main.STATE_ARCHIVED for email in emails)
    assert task.archived == 2
    assert task.failed == 0
    assert queue.archived_count == 2
    assert queue.errors == []
    assert queue.pending_count() == 0


def test_tasks_are_processed_in_fifo_order(gmail, queue):
    first = enqueue(queue, [make_email('a1')], label='first')
    second = enqueue(queue, [make_email('b1')], label='second')

    assert wait_until(lambda: second.status == main.STATE_ARCHIVED)
    assert first.status == main.STATE_ARCHIVED
    assert gmail.archived == ['a1', 'b1']


def test_stopped_queue_holds_tasks_and_resume_flushes_them(gmail, queue):
    email = make_email('m1')
    task = enqueue(queue, [email])

    assert queue.toggle_pause() is True
    time.sleep(DELAY * 3)

    assert gmail.calls == [], "stopped queue kept archiving"
    assert queue.pending_count() == 1
    assert email['state'] == main.STATE_QUEUED

    assert queue.toggle_pause() is False
    assert wait_until(lambda: task.status == main.STATE_ARCHIVED)
    assert gmail.archived == ['m1']


def test_failure_is_surfaced_not_swallowed(monkeypatch, queue):
    service = FakeService(fail_ids={'m2'})
    monkeypatch.setattr(main, 'build', lambda *a, **kw: service)

    emails = [make_email('m1'), make_email('m2')]
    task = enqueue(queue, emails, label='Sender A')

    assert wait_until(lambda: task.status == main.STATE_ERROR)

    assert service.archived == ['m1'], "the healthy message should still be archived"
    assert task.archived == 1
    assert task.failed == 1
    assert 'boom for m2' in task.error
    assert '1/2 failed' in task.error
    assert emails[0]['state'] == main.STATE_ARCHIVED
    assert emails[1]['state'] == main.STATE_ERROR
    assert queue.errors == [('Sender A', task.error)], "failure must be visible in the queue, not only in a log"


def test_connection_failure_is_surfaced(monkeypatch, queue):
    def explode(*args, **kwargs):
        raise RuntimeError("no network")

    monkeypatch.setattr(main, 'build', explode)

    email = make_email('m1')
    task = enqueue(queue, [email])

    assert wait_until(lambda: task.status == main.STATE_ERROR)
    assert 'no network' in task.error
    assert email['state'] == main.STATE_ERROR
    assert len(queue.errors) == 1


def test_discard_pending_clears_every_marker(gmail, queue):
    queue.toggle_pause()
    emails_a = [make_email('a1'), make_email('a2')]
    emails_b = [make_email('b1')]
    enqueue(queue, emails_a, label='A')
    enqueue(queue, emails_b, label='B')

    dropped = queue.discard_pending()
    for task in dropped:
        main.clear_task_marks(task)

    assert len(dropped) == 2
    assert queue.pending_count() == 0
    assert all(email['state'] is None for email in emails_a + emails_b)
    assert gmail.calls == []


def test_cancel_returns_false_for_unknown_task(gmail, queue):
    task = main.ArchiveTask('ghost', [make_email('m1')])
    assert queue.cancel(task) is False


def test_seconds_left_counts_down_to_zero():
    task = main.ArchiveTask('x', [make_email('m1')])
    assert 0 < task.seconds_left() <= DELAY
    time.sleep(DELAY + 0.05)
    assert task.seconds_left() == 0.0


def test_owner_group_marker_is_cleared_on_cancel(gmail, queue):
    emails = [make_email('m1')]
    group = {'sender_email': 'x@example.com', 'sender_full': 'X', 'count': 1,
             'emails': emails, 'task': None}
    task = main.ArchiveTask('X', emails, owner=group)
    group['task'] = task
    for email in emails:
        email['task'] = task
        email['state'] = main.STATE_QUEUED
    queue.enqueue(task)

    assert queue.cancel(task) is True
    main.clear_task_marks(task)

    assert group['task'] is None
    assert emails[0]['state'] is None
