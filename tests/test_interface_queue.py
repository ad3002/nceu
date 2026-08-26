"""Behaviour tests for the queue-driven parts of the ncurses interface.

The screen is stubbed: only geometry is needed, since these tests assert
state transitions (cursor movement, row markers, undo) rather than pixels.
"""
import time

import pytest

from nceu import main


DELAY = 0.3


@pytest.fixture(autouse=True)
def fast_delay(monkeypatch):
    monkeypatch.setattr(main, 'ARCHIVE_DELAY', DELAY)


class FakeScreen:
    def __init__(self, height=24, width=100):
        self.height = height
        self.width = width

    def getmaxyx(self):
        return self.height, self.width


class RecordingQueue:
    """Stands in for ArchiveQueue: records enqueues, never touches Gmail."""

    def __init__(self):
        self.tasks = []
        self.paused = False
        self.done_count = 0
        self.archived_count = 0
        self.errors = []

    def enqueue(self, task):
        self.tasks.append(task)

    def cancel(self, task):
        for i, pending in enumerate(self.tasks):
            if pending is task:
                self.tasks.pop(i)
                return True
        return False

    def pending(self):
        return list(self.tasks)

    def pending_count(self):
        return len(self.tasks)

    def running_task(self):
        return None

    def is_busy(self):
        return bool(self.tasks)

    def discard_pending(self):
        dropped, self.tasks = self.tasks, []
        return dropped

    def toggle_pause(self):
        self.paused = not self.paused
        return self.paused


def make_email(msg_id, sender, subject='Subject'):
    return {
        'id': msg_id,
        'threadId': f'thread-{msg_id}',
        'subject': subject,
        'sender': sender,
        'size': 100,
        'date': 'Mon, 1 Jan 2024 10:00:00 +0000',
        'state': None,
        'task': None,
    }


@pytest.fixture
def ui():
    emails = [
        make_email('a1', 'Alice <alice@example.com>', 'Hi'),
        make_email('a2', 'Alice <alice@example.com>', 'Re: Hi'),
        make_email('b1', 'Bob <bob@example.com>', 'Report'),
        make_email('c1', 'Carol <carol@example.com>', 'Invoice'),
    ]
    interface = main.NCDULikeInterface(FakeScreen(), emails, service=None, queue=RecordingQueue())
    # deterministic order: Alice (2), then Bob and Carol (1 each)
    interface.grouped_emails.sort(key=lambda g: (-g['count'], g['sender_email']))
    interface.current_row = 0
    return interface


def test_archiving_a_sender_queues_it_and_moves_the_cursor_down(ui):
    ui.enqueue_current()

    assert len(ui.queue.tasks) == 1, "pressing 'a' must enqueue instead of archiving inline"
    task = ui.queue.tasks[0]
    assert task.label == 'Alice <alice@example.com>'
    assert [email['id'] for email in task.emails] == ['a1', 'a2']
    assert all(email['state'] == main.STATE_QUEUED for email in task.emails)
    assert ui.current_row == 1, "cursor must run down so the next row can be queued straight away"


def test_rapid_archiving_queues_every_row_without_blocking(ui):
    started = time.time()
    for _ in range(3):
        ui.enqueue_current()
    elapsed = time.time() - started

    assert elapsed < DELAY, "queueing must not wait for the archive delay"
    assert [task.label for task in ui.queue.tasks] == [
        'Alice <alice@example.com>',
        'Bob <bob@example.com>',
        'Carol <carol@example.com>',
    ]
    assert ui.current_row == 2, "cursor stops at the last row"
    assert all(email['state'] == main.STATE_QUEUED for email in ui.emails)


def test_queued_row_is_marked_and_counted(ui):
    ui.enqueue_current()
    alice = ui.grouped_emails[0]

    assert ui.group_state(alice) == main.STATE_QUEUED
    assert main.STATE_MARKERS[main.STATE_QUEUED] == 'Q'
    assert ui.remaining_count(alice) == 2, "queued emails are still in the inbox"

    for email in alice['emails']:
        email['state'] = main.STATE_ARCHIVED
    assert ui.group_state(alice) == main.STATE_ARCHIVED
    assert ui.remaining_count(alice) == 0


def test_undo_on_a_row_removes_it_from_the_queue(ui):
    ui.enqueue_current()
    alice = ui.grouped_emails[0]
    ui.current_row = 0

    ui.unqueue_current()

    assert ui.queue.tasks == [], "task must be gone from the queue"
    assert all(email['state'] is None for email in alice['emails'])
    assert all(email['task'] is None for email in alice['emails'])
    assert alice['task'] is None
    assert 'Removed 1' in ui.message


def test_undo_on_a_clean_row_says_so_and_changes_nothing(ui):
    ui.unqueue_current()

    assert ui.queue.tasks == []
    assert ui.message == "Nothing pending on this row"


def test_double_archive_does_not_queue_twice(ui):
    ui.enqueue_current()
    ui.current_row = 0
    ui.enqueue_current()

    assert len(ui.queue.tasks) == 1
    assert ui.message == "Already queued or archived"


def test_single_email_view_queues_only_that_email(ui):
    ui.selected_sender = ui.grouped_emails[0]
    ui.view_mode = 'emails'
    ui.current_row = 0

    ui.enqueue_current()

    assert len(ui.queue.tasks) == 1
    task = ui.queue.tasks[0]
    assert [email['id'] for email in task.emails] == ['a1']
    assert ui.selected_sender['emails'][1]['state'] is None, "sibling email must stay untouched"
    assert ui.current_row == 1


def test_partly_queued_sender_queues_only_the_rest(ui):
    ui.selected_sender = ui.grouped_emails[0]
    ui.view_mode = 'emails'
    ui.current_row = 0
    ui.enqueue_current()

    ui.view_mode = 'senders'
    ui.current_row = 0
    ui.enqueue_current()

    assert len(ui.queue.tasks) == 2
    assert [email['id'] for email in ui.queue.tasks[1].emails] == ['a2']


def test_undo_from_sender_row_cancels_per_email_tasks(ui):
    ui.selected_sender = ui.grouped_emails[0]
    ui.view_mode = 'emails'
    ui.current_row = 0
    ui.enqueue_current()
    ui.current_row = 1
    ui.enqueue_current()
    assert len(ui.queue.tasks) == 2

    ui.view_mode = 'senders'
    ui.current_row = 0
    ui.unqueue_current()

    assert ui.queue.tasks == []
    assert all(email['state'] is None for email in ui.grouped_emails[0]['emails'])
    assert 'Removed 2' in ui.message


def test_running_task_cannot_be_undone(ui):
    ui.enqueue_current()
    task = ui.queue.tasks[0]
    task.status = main.STATE_RUNNING
    ui.current_row = 0

    ui.unqueue_current()

    assert ui.queue.tasks == [task], "a task already sent to Gmail must stay"
    assert ui.message == "Nothing pending on this row"


def test_status_line_reports_pending_countdown_and_errors(ui):
    ui.enqueue_current()
    line = ui.queue_status_line()

    assert 'RUNNING' in line
    assert '1 queued (2 emails)' in line
    assert 'next in' in line

    ui.queue.paused = True
    ui.queue.errors.append(('Bob', '1/1 failed: boom'))
    line = ui.queue_status_line()
    assert 'STOPPED' in line
    assert 'ERRORS 1' in line, "failed tasks must be visible on the main screen"


def test_regrouping_keeps_the_queue_link(ui):
    ui.enqueue_current()
    task = ui.queue.tasks[0]

    ui.toggle_group_mode()
    ui.toggle_group_mode()

    alice = next(g for g in ui.grouped_emails if g['sender_email'] == 'alice@example.com')
    assert alice['task'] is task, "the group must still know its pending task after regrouping"
    ui.current_row = ui.grouped_emails.index(alice)
    ui.unqueue_current()
    assert ui.queue.tasks == []


class RecordingScreen(FakeScreen):
    """Captures what draw() actually paints, line by line."""

    def __init__(self, height=24, width=100):
        super().__init__(height, width)
        self.lines = {}

    def erase(self):
        self.lines = {}

    clear = erase

    def move(self, y, x):
        self._cursor = (y, x)

    def clrtoeol(self):
        pass

    def refresh(self):
        pass

    def addstr(self, y, x, text, mode=0):
        row = self.lines.setdefault(y, [' '] * self.width)
        for offset, char in enumerate(text):
            if x + offset < self.width:
                row[x + offset] = char
        self.lines[y] = row

    def text(self, y):
        return ''.join(self.lines.get(y, [])).rstrip()

    def all_text(self):
        return [self.text(y) for y in sorted(self.lines)]


def test_draw_marks_queued_rows_and_shows_queue_progress(ui):
    screen = RecordingScreen()
    ui.stdscr = screen
    ui.enqueue_current()
    ui.draw()

    status = screen.text(2)
    assert status.startswith('Queue [RUNNING]:')
    assert '1 queued (2 emails)' in status

    rows = [line for line in screen.all_text() if 'alice@example.com' in line]
    assert rows, "the queued sender must still be listed"
    assert rows[0].startswith('Q '), f"queued row must carry the Q marker, got {rows[0]!r}"
    assert '2 Alice' in rows[0], "count column still shows 2 emails waiting in the inbox"


def test_draw_marks_archived_rows_and_drops_their_count(ui):
    screen = RecordingScreen()
    ui.stdscr = screen
    alice = ui.grouped_emails[0]
    for email in alice['emails']:
        email['state'] = main.STATE_ARCHIVED
    ui.current_row = 1  # keep the highlight off the archived row
    ui.queue.archived_count = 2
    ui.draw()

    rows = [line for line in screen.all_text() if 'alice@example.com' in line]
    assert rows[0].startswith('- '), f"archived row must carry the - marker, got {rows[0]!r}"
    assert '0 Alice' in rows[0], "archived emails no longer count towards the inbox"
    assert 'archived 2' in screen.text(2)


def test_draw_footer_advertises_the_queue_keys(ui):
    screen = RecordingScreen()
    ui.stdscr = screen
    ui.draw()

    footer = screen.text(screen.height - 1)
    for key in ('a: Queue', 'u: Unqueue', 'v: Queue view', 'p: Stop/Start'):
        assert key in footer
