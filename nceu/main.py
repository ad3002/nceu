import os
import curses
from curses import wrapper
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
import socket
import threading
import time
from collections import defaultdict
from datetime import datetime
import dateutil.parser
import re
from google_auth_oauthlib.flow import InstalledAppFlow
import sys

API_TIMEOUT = 120
API_RETRIES = 3

# How long a task sits in the queue before it is actually sent to Gmail.
# This window is what makes an accidental 'a' recoverable: open the queue,
# drop the task, nothing ever leaves the inbox.
ARCHIVE_DELAY = 5

STATE_QUEUED = 'queued'
STATE_RUNNING = 'running'
STATE_ARCHIVED = 'archived'
STATE_ERROR = 'error'

STATE_MARKERS = {
    STATE_QUEUED: 'Q',
    STATE_RUNNING: '>',
    STATE_ARCHIVED: '-',
    STATE_ERROR: '!',
}

SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

def authenticate_gmail():
    creds = None
    if os.path.exists('token.json'):
        try:
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        except Exception as error:
            print(f"An error occurred while reading token: {error}")
            print("Removing invalid token and re-authenticating...")
            os.remove('token.json')
            creds = None
    if not os.path.exists('credentials.json'):
        print("Please download credentials.json from the Google Cloud Console and save it to the current directory.")
        print("Instructions: https://developers.google.com/gmail/api/quickstart/python")
        print("See our github page for more information: https://github.com/ad3002/nceu")
        sys.exit(1)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as error:
                print(f"Token refresh failed: {error}")
                print("Re-authenticating...")
                if os.path.exists('token.json'):
                    os.remove('token.json')
                creds = None
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return creds

def parse_date(date_string):
    try:
        return dateutil.parser.parse(date_string)
    except ValueError:
        return datetime.min
    
def get_total_emails(service, query):
    for attempt in range(API_RETRIES):
        try:
            result = service.users().labels().get(userId='me', id='INBOX').execute(num_retries=API_RETRIES)
            return result['messagesTotal']
        except Exception as error:
            if attempt < API_RETRIES - 1:
                print(f"Attempt {attempt + 1} failed: {error}. Retrying...")
                time.sleep(2 ** attempt)
            else:
                print(f"Failed to get total emails after {API_RETRIES} attempts: {error}")
                return 0
    
def extract_email(sender):
    match = re.search(r'<(.+?)>', sender)
    if match:
        return match.group(1)
    return sender

def clean_subject(subject):
    return re.sub(r'^(Re|Fwd|Fw)\s*:\s*', '', subject, flags=re.IGNORECASE).strip()


def download_emails(stdscr, service):
    height, width = stdscr.getmaxyx()
    downloaded_emails = 0
    total_size = 0
    current_item = ""
    start_time = time.time()
    emails_data = []

    total_emails = get_total_emails(service, 'in:inbox')

    def update_screen():
        stdscr.clear()
        y_offset = (height - 7) // 2
        x_offset = (width - 50) // 2
        
        stdscr.addstr(y_offset, x_offset, "Downloading inbox emails...", curses.A_BOLD)
        stdscr.addstr(y_offset + 2, x_offset, f"Progress: {downloaded_emails}/{total_emails}")
        stdscr.addstr(y_offset + 2, x_offset + 30, f"Size: {total_size / (1024*1024):.1f} MiB")
        stdscr.addstr(y_offset + 3, x_offset, f"Current: {current_item[:40]}")
        
        elapsed_time = time.time() - start_time
        if elapsed_time > 0:
            speed = downloaded_emails / elapsed_time
            eta = (total_emails - downloaded_emails) / speed if speed > 0 else 0
            stdscr.addstr(y_offset + 5, x_offset, f"Speed: {speed:.2f} emails/sec | ETA: {eta:.0f} sec")
        
        progress = int((downloaded_emails / total_emails) * 40) if total_emails > 0 else 0
        stdscr.addstr(y_offset + 6, x_offset, f"[{'=' * progress}{' ' * (40-progress)}]")
        
        stdscr.addstr(height-1, 0, "Press q to abort", curses.A_REVERSE)
        stdscr.refresh()

    try:
        next_page_token = None
        while True:
            results = service.users().messages().list(userId='me', pageToken=next_page_token, maxResults=100, q='in:inbox').execute(num_retries=API_RETRIES)
            messages = results.get('messages', [])

            for message in messages:
                msg = service.users().messages().get(userId='me', id=message['id'], format='metadata').execute(num_retries=API_RETRIES)
                
                headers = msg['payload']['headers']
                subject = next((header['value'] for header in headers if header['name'] == 'Subject'), 'No Subject')
                sender = next((header['value'] for header in headers if header['name'] == 'From'), 'Unknown')
                date = next((header['value'] for header in headers if header['name'] == 'Date'), 'Unknown')
                current_item = subject

                size = msg['sizeEstimate']
                total_size += size
                downloaded_emails += 1

                emails_data.append({
                    'id': message['id'],
                    'threadId': msg.get('threadId', message['id']),
                    'subject': subject,
                    'sender': sender,
                    'size': size,
                    'date': date,
                    'state': None,
                    'task': None
                })

                update_screen()

                stdscr.nodelay(True)
                if stdscr.getch() == ord('q'):
                    return emails_data

            next_page_token = results.get('nextPageToken')
            if not next_page_token:
                break

    except Exception as error:
        stdscr.addstr(height-2, 0, f"An error occurred: {str(error)}", curses.A_REVERSE)
        stdscr.getch()

    stdscr.nodelay(False)
    return emails_data


class ArchiveTask:
    """A single unit of work in the archive queue: one sender group or one email."""

    def __init__(self, label, emails, owner=None):
        self.label = label
        self.emails = emails
        self.owner = owner
        self.enqueued_at = time.time()
        self.status = STATE_QUEUED
        self.archived = 0
        self.failed = 0
        self.error = None

    def seconds_left(self):
        return max(0.0, ARCHIVE_DELAY - (time.time() - self.enqueued_at))


class ArchiveQueue:
    """Background archiver. The UI thread only enqueues and reads state."""

    def __init__(self, creds):
        self._creds = creds
        self._cond = threading.Condition()
        self._tasks = []
        self._running = None
        self._service = None
        self._stop = threading.Event()
        self._thread = None
        self.paused = False
        self.done_count = 0
        self.archived_count = 0
        self.errors = []

    def start(self):
        self._thread = threading.Thread(target=self._worker, name='archive-queue', daemon=True)
        self._thread.start()

    def enqueue(self, task):
        with self._cond:
            self._tasks.append(task)
            self._cond.notify_all()

    def cancel(self, task):
        """Drop a still-pending task. Returns False if it is already running or gone."""
        with self._cond:
            for i, pending in enumerate(self._tasks):
                if pending is task:
                    self._tasks.pop(i)
                    self._cond.notify_all()
                    return True
            return False

    def discard_pending(self):
        with self._cond:
            dropped = self._tasks
            self._tasks = []
            self._cond.notify_all()
            return dropped

    def pending(self):
        with self._cond:
            return list(self._tasks)

    def pending_count(self):
        with self._cond:
            return len(self._tasks)

    def running_task(self):
        with self._cond:
            return self._running

    def is_busy(self):
        with self._cond:
            return bool(self._tasks) or self._running is not None

    def toggle_pause(self):
        with self._cond:
            self.paused = not self.paused
            self._cond.notify_all()
            return self.paused

    def resume(self):
        with self._cond:
            self.paused = False
            self._cond.notify_all()

    def shutdown(self, timeout=API_TIMEOUT):
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _worker(self):
        while not self._stop.is_set():
            with self._cond:
                if self.paused or not self._tasks:
                    self._cond.wait(0.2)
                    continue
                wait = self._tasks[0].seconds_left()
                if wait > 0:
                    self._cond.wait(min(wait, 0.2))
                    continue
                task = self._tasks.pop(0)
                self._running = task
            task.status = STATE_RUNNING
            for email in task.emails:
                email['state'] = STATE_RUNNING
            self._execute(task)
            with self._cond:
                self._running = None
                self.done_count += 1
                if task.error:
                    self.errors.append((task.label, task.error))

    def _worker_service(self):
        # httplib2 is not thread safe, so the worker owns its own service object.
        if self._service is None:
            self._service = build('gmail', 'v1', credentials=self._creds)
        return self._service

    def _execute(self, task):
        try:
            service = self._worker_service()
        except Exception as error:
            task.status = STATE_ERROR
            task.error = f"Gmail connection failed: {error}"
            for email in task.emails:
                email['state'] = STATE_ERROR
            return

        last_error = None
        for email in task.emails:
            if self._stop.is_set():
                break
            try:
                service.users().messages().modify(
                    userId='me',
                    id=email['id'],
                    body={'removeLabelIds': ['INBOX']}
                ).execute(num_retries=API_RETRIES)
            except Exception as error:
                task.failed += 1
                last_error = str(error)
                email['state'] = STATE_ERROR
                continue
            task.archived += 1
            self.archived_count += 1
            email['state'] = STATE_ARCHIVED

        if task.failed:
            task.status = STATE_ERROR
            task.error = f"{task.failed}/{len(task.emails)} failed: {last_error}"
        else:
            task.status = STATE_ARCHIVED


def clear_task_marks(task):
    for email in task.emails:
        if email.get('task') is task:
            email['task'] = None
            email['state'] = None
    if task.owner is not None and task.owner.get('task') is task:
        task.owner['task'] = None


class NCDULikeInterface:
    def __init__(self, stdscr, emails, service, queue):
        self.stdscr = stdscr
        self.emails = emails
        self.service = service
        self.queue = queue
        self.group_mode = 'sender'
        self.grouped_emails = self.group_emails()
        self.current_row = 0
        self.top_row = 0
        self.sort_by = 'count'
        self.sort_reverse = True
        self.view_mode = 'senders'
        self.selected_sender = None
        self.sender_position = 0
        self.sort_emails()
        self.last_height = 0
        self.last_width = 0
        self.message = ""
        self.message_until = 0.0

    def group_emails(self):
        if self.group_mode == 'thread':
            return self._group_by_thread()
        return self._group_by_sender()

    def _group_by_sender(self):
        grouped = defaultdict(list)
        for email in self.emails:
            sender_email = extract_email(email['sender'])
            grouped[sender_email].append(email)
        return [{'sender_email': sender_email, 'sender_full': emails[0]['sender'], 'count': len(emails), 'emails': emails, 'task': None}
                for sender_email, emails in grouped.items()]

    def _group_by_thread(self):
        grouped = defaultdict(list)
        for email in self.emails:
            grouped[email['threadId']].append(email)
        result = []
        for thread_id, emails in grouped.items():
            sorted_emails = sorted(emails, key=lambda x: parse_date(x['date']), reverse=True)
            subject = clean_subject(sorted_emails[0]['subject'])
            senders = list({extract_email(e['sender']) for e in sorted_emails})
            result.append({
                'sender_email': thread_id,
                'sender_full': subject,
                'count': len(sorted_emails),
                'emails': sorted_emails,
                'senders': senders,
                'task': None,
            })
        return result

    def reattach_tasks(self):
        """After regrouping, restore the group -> task link from the email marks."""
        for group in self.grouped_emails:
            tasks = {id(email.get('task')): email.get('task') for email in group['emails']}
            group['task'] = next(iter(tasks.values())) if len(tasks) == 1 else None

    def sort_emails(self):
        if self.view_mode == 'senders':
            self.grouped_emails.sort(key=lambda x: x[self.sort_by], reverse=self.sort_reverse)
        elif self.view_mode == 'emails':
            self.selected_sender['emails'].sort(key=lambda x: parse_date(x['date']), reverse=True)

    def show_message(self, message, seconds=2.0):
        """Non-blocking status message: the queue must never wait on the UI."""
        self.message = message
        self.message_until = time.time() + seconds

    def visible_rows(self):
        height, _ = self.stdscr.getmaxyx()
        return max(1, height - 6)

    def row_count(self):
        if self.view_mode == 'senders':
            return len(self.grouped_emails)
        return len(self.selected_sender['emails'])

    def ensure_visible(self):
        visible = self.visible_rows()
        if self.current_row < self.top_row:
            self.top_row = self.current_row
        elif self.current_row >= self.top_row + visible:
            self.top_row = self.current_row - visible + 1
        self.top_row = max(0, self.top_row)

    def move_down(self):
        if self.current_row < self.row_count() - 1:
            self.current_row += 1
        self.ensure_visible()

    def move_up(self):
        if self.current_row > 0:
            self.current_row -= 1
        self.ensure_visible()

    def group_state(self, group):
        states = {email.get('state') for email in group['emails']}
        if STATE_ERROR in states:
            return STATE_ERROR
        if STATE_RUNNING in states:
            return STATE_RUNNING
        if STATE_QUEUED in states:
            return STATE_QUEUED
        if states == {STATE_ARCHIVED}:
            return STATE_ARCHIVED
        return None

    def remaining_count(self, group):
        return sum(1 for email in group['emails'] if email.get('state') != STATE_ARCHIVED)

    def queue_status_line(self):
        pending = self.queue.pending()
        running = self.queue.running_task()
        parts = []
        if running is not None:
            parts.append(f"archiving {running.label[:22]} {running.archived}/{len(running.emails)}")
        if pending:
            waiting = sum(len(task.emails) for task in pending)
            parts.append(f"{len(pending)} queued ({waiting} emails)")
            parts.append(f"next in {pending[0].seconds_left():.0f}s")
        if not parts:
            parts.append("idle")
        parts.append(f"archived {self.queue.archived_count}")
        if self.queue.errors:
            parts.append(f"ERRORS {len(self.queue.errors)}")
        state = "STOPPED" if self.queue.paused else "RUNNING"
        return f"Queue [{state}]: " + " | ".join(parts)

    def addstr_safe(self, y, x, text, mode=curses.A_NORMAL):
        height, width = self.stdscr.getmaxyx()
        if y < 0 or y >= height or x >= width:
            return
        text = text[:max(0, width - x - 1)]
        if text:
            self.stdscr.addstr(y, x, text, mode)

    def draw(self):
        height, width = self.stdscr.getmaxyx()
        
        if height != self.last_height or width != self.last_width:
            self.stdscr.clear()
            self.last_height, self.last_width = height, width
        else:
            self.stdscr.erase()

        if self.group_mode == 'thread':
            header = "Inbox Email Manager (grouped by thread)"
        else:
            header = "Inbox Email Manager (grouped by sender email)"
        self.addstr_safe(0, max(0, (width - len(header)) // 2), header, curses.A_REVERSE)

        sort_info = f"Sorted by {self.sort_by} ({'desc' if self.sort_reverse else 'asc'})"
        self.addstr_safe(1, max(0, (width - len(sort_info)) // 2), sort_info)

        status = self.queue_status_line()
        status_mode = curses.A_BOLD if (self.queue.errors or self.queue.paused) else curses.A_NORMAL
        self.addstr_safe(2, 0, status, status_mode)

        if self.view_mode == 'senders':
            if self.group_mode == 'thread':
                self.addstr_safe(3, 0, f"{'':2}{'Count':>10} {'Subject':<60}")
            else:
                self.addstr_safe(3, 0, f"{'':2}{'Count':>10} {'Sender':<60}")
        else:
            self.addstr_safe(3, 0, f"{'':2}{'Date':<20} {'From':<30} {'Subject':<40}")
        self.addstr_safe(4, 0, "-" * (width - 1))

        for i in range(5, height - 1):
            self.stdscr.move(i, 0)
            self.stdscr.clrtoeol()
            index = i - 5 + self.top_row
            if self.view_mode == 'senders':
                if index >= len(self.grouped_emails):
                    continue
                sender_data = self.grouped_emails[index]
                state = self.group_state(sender_data)
                mode = curses.A_REVERSE if index == self.current_row else curses.A_NORMAL
                if state == STATE_ARCHIVED and index != self.current_row:
                    mode = curses.A_DIM
                marker = STATE_MARKERS.get(state, ' ')
                remaining = self.remaining_count(sender_data)
                self.addstr_safe(i, 0, f"{marker:<2}{remaining:>10} {sender_data['sender_full'][:60]:<60}", mode)
            else:
                if index >= len(self.selected_sender['emails']):
                    continue
                email = self.selected_sender['emails'][index]
                state = email.get('state')
                mode = curses.A_REVERSE if index == self.current_row else curses.A_NORMAL
                if state == STATE_ARCHIVED and index != self.current_row:
                    mode = curses.A_DIM
                marker = STATE_MARKERS.get(state, ' ')
                date = parse_date(email['date']).strftime('%Y-%m-%d %H:%M')
                sender_short = extract_email(email['sender'])[:30]
                self.addstr_safe(i, 0, f"{marker:<2}{date:<20} {sender_short:<30} {email['subject'][:40]:<40}", mode)

        if self.message and time.time() < self.message_until:
            self.addstr_safe(height - 2, 0, self.message.center(width - 1), curses.A_BOLD)
        elif self.message:
            self.message = ""

        if self.view_mode == 'senders':
            mode_label = "threads" if self.group_mode == 'sender' else "senders"
            footer = f"q: Quit | a: Queue | u: Unqueue | v: Queue view | p: Stop/Start | s: Sort | t: By {mode_label} | Enter: Open"
        else:
            footer = "q: Back | a: Queue | u: Unqueue | v: Queue view | p: Stop/Start | Enter: Details"
        self.addstr_safe(height - 1, 0, footer.ljust(width - 1), curses.A_REVERSE)

        self.stdscr.refresh()

    def run(self):
        curses.curs_set(0)
        self.stdscr.timeout(200)
        while True:
            self.draw()
            key = self.stdscr.getch()

            if key == -1:
                continue
            if key == ord('q'):
                if self.view_mode == 'emails':
                    self.view_mode = 'senders'
                    self.current_row = self.sender_position
                    self.ensure_visible()
                elif self.confirm_quit():
                    break
            elif key == curses.KEY_UP:
                self.move_up()
            elif key == curses.KEY_DOWN:
                self.move_down()
            elif key == curses.KEY_NPAGE:
                self.current_row = min(self.row_count() - 1, self.current_row + self.visible_rows())
                self.ensure_visible()
            elif key == curses.KEY_PPAGE:
                self.current_row = max(0, self.current_row - self.visible_rows())
                self.ensure_visible()
            elif key == ord('t') and self.view_mode == 'senders':
                self.toggle_group_mode()
            elif key == ord('s') and self.view_mode == 'senders':
                self.change_sort()
            elif key == ord('a'):
                self.enqueue_current()
            elif key == ord('u'):
                self.unqueue_current()
            elif key == ord('v'):
                self.show_queue()
            elif key == ord('p'):
                paused = self.queue.toggle_pause()
                self.show_message("Queue stopped" if paused else "Queue running")
            elif key == 10:
                if self.view_mode == 'senders':
                    if not self.grouped_emails:
                        continue
                    self.selected_sender = self.grouped_emails[self.current_row]
                    self.sender_position = self.current_row 
                    self.view_mode = 'emails'
                    self.current_row = 0
                    self.top_row = 0
                    self.sort_emails()
                else:
                    self.show_email_details()

    def toggle_group_mode(self):
        self.group_mode = 'thread' if self.group_mode == 'sender' else 'sender'
        self.grouped_emails = self.group_emails()
        self.reattach_tasks()
        self.current_row = 0
        self.top_row = 0
        self.sort_emails()

    def change_sort(self):
        if self.sort_by == 'count':
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_by = 'count'
            self.sort_reverse = True
        self.sort_emails()

    def enqueue_current(self):
        """Add the current row to the background queue and move on immediately."""
        if self.row_count() == 0:
            return
        if self.view_mode == 'senders':
            group = self.grouped_emails[self.current_row]
            targets = [email for email in group['emails']
                       if email.get('state') not in (STATE_QUEUED, STATE_RUNNING, STATE_ARCHIVED)]
            if not targets:
                self.show_message("Already queued or archived")
                self.move_down()
                return
            task = ArchiveTask(group['sender_full'], targets, owner=group)
            group['task'] = task
        else:
            email = self.selected_sender['emails'][self.current_row]
            if email.get('state') in (STATE_QUEUED, STATE_RUNNING, STATE_ARCHIVED):
                self.show_message("Already queued or archived")
                self.move_down()
                return
            targets = [email]
            task = ArchiveTask(email['subject'][:40] or 'No Subject', targets)

        for email in targets:
            email['task'] = task
            email['state'] = STATE_QUEUED
        self.queue.enqueue(task)
        self.show_message(f"Queued {len(targets)} email(s) - {ARCHIVE_DELAY}s to undo with 'u'", 1.5)
        self.move_down()

    def current_tasks(self):
        if self.row_count() == 0:
            return []
        if self.view_mode == 'senders':
            group = self.grouped_emails[self.current_row]
            emails = group['emails']
        else:
            emails = [self.selected_sender['emails'][self.current_row]]
        tasks = []
        for email in emails:
            task = email.get('task')
            if task is not None and task.status == STATE_QUEUED and not any(task is t for t in tasks):
                tasks.append(task)
        return tasks

    def unqueue_current(self):
        tasks = self.current_tasks()
        if not tasks:
            self.show_message("Nothing pending on this row")
            return
        removed = 0
        for task in tasks:
            if self.queue.cancel(task):
                clear_task_marks(task)
                removed += 1
        if removed:
            self.show_message(f"Removed {removed} task(s) from the queue")
        else:
            self.show_message("Too late - already being archived")

    def show_queue(self):
        selected = 0
        top = 0
        message = ""
        message_until = 0.0
        while True:
            pending = self.queue.pending()
            running = self.queue.running_task()
            height, width = self.stdscr.getmaxyx()
            self.stdscr.erase()

            header = "Archive queue"
            self.addstr_safe(0, max(0, (width - len(header)) // 2), header, curses.A_REVERSE)
            state = "STOPPED" if self.queue.paused else "RUNNING"
            summary = (f"[{state}] pending {len(pending)} | archived {self.queue.archived_count} | "
                       f"errors {len(self.queue.errors)} | delay {ARCHIVE_DELAY}s")
            self.addstr_safe(1, 0, summary, curses.A_BOLD if self.queue.errors else curses.A_NORMAL)

            if running is not None:
                self.addstr_safe(2, 0, f">  now: {running.label[:50]} ({running.archived}/{len(running.emails)})")
            else:
                self.addstr_safe(2, 0, "   now: nothing" if not self.queue.paused else "   now: stopped")

            self.addstr_safe(3, 0, f"{'In':>5} {'Emails':>7}  Target")
            self.addstr_safe(4, 0, "-" * (width - 1))

            error_lines = min(len(self.queue.errors), 3)
            list_bottom = height - 2 - (error_lines + 2 if error_lines else 0)
            visible = max(1, list_bottom - 5)
            if selected >= len(pending):
                selected = max(0, len(pending) - 1)
            if selected < top:
                top = selected
            elif selected >= top + visible:
                top = selected - visible + 1

            if not pending:
                self.addstr_safe(5, 0, "   queue is empty")
            for row in range(visible):
                index = top + row
                if index >= len(pending):
                    break
                task = pending[index]
                mode = curses.A_REVERSE if index == selected else curses.A_NORMAL
                self.addstr_safe(5 + row, 0,
                                 f"{task.seconds_left():>4.0f}s {len(task.emails):>7}  {task.label[:width - 20]}",
                                 mode)

            if error_lines:
                base = list_bottom
                self.addstr_safe(base, 0, "-" * (width - 1))
                self.addstr_safe(base + 1, 0, f"Failed tasks ({len(self.queue.errors)}):", curses.A_BOLD)
                for offset, (label, error) in enumerate(self.queue.errors[-error_lines:]):
                    self.addstr_safe(base + 2 + offset, 0, f"! {label[:30]}: {error}", curses.A_BOLD)

            if message and time.time() < message_until:
                self.addstr_safe(height - 2, 0, message.center(width - 1), curses.A_BOLD)

            footer = "q: Back | d: Remove task | c: Clear queue | p: Stop/Start"
            self.addstr_safe(height - 1, 0, footer.ljust(width - 1), curses.A_REVERSE)
            self.stdscr.refresh()

            key = self.stdscr.getch()
            if key == -1:
                continue
            if key in (ord('q'), 27):
                break
            elif key == curses.KEY_UP and selected > 0:
                selected -= 1
            elif key == curses.KEY_DOWN and selected < len(pending) - 1:
                selected += 1
            elif key in (ord('d'), ord('x'), curses.KEY_DC):
                if not pending:
                    continue
                task = pending[selected]
                if self.queue.cancel(task):
                    clear_task_marks(task)
                    message = f"Removed: {task.label[:40]}"
                else:
                    message = "Too late - already being archived"
                message_until = time.time() + 2
            elif key == ord('c'):
                dropped = self.queue.discard_pending()
                for task in dropped:
                    clear_task_marks(task)
                message = f"Cleared {len(dropped)} task(s)"
                message_until = time.time() + 2
            elif key == ord('p'):
                paused = self.queue.toggle_pause()
                message = "Queue stopped" if paused else "Queue running"
                message_until = time.time() + 2

        self.last_height = 0  # force a full redraw of the list view

    def confirm_quit(self):
        if not self.queue.is_busy():
            return True
        while True:
            height, width = self.stdscr.getmaxyx()
            pending = self.queue.pending_count()
            self.stdscr.erase()
            prompt = f"Queue still has {pending} pending task(s)"
            self.addstr_safe(height // 2 - 1, max(0, (width - len(prompt)) // 2), prompt, curses.A_BOLD)
            options = "w: wait and finish   d: discard queue and quit   c: cancel"
            self.addstr_safe(height // 2 + 1, max(0, (width - len(options)) // 2), options)
            self.stdscr.refresh()
            key = self.stdscr.getch()
            if key == -1:
                continue
            if key == ord('w'):
                return self.wait_for_queue()
            if key == ord('d'):
                for task in self.queue.discard_pending():
                    clear_task_marks(task)
                return True
            if key in (ord('c'), ord('q'), 27):
                self.last_height = 0
                return False

    def wait_for_queue(self):
        self.queue.resume()
        while self.queue.is_busy():
            height, width = self.stdscr.getmaxyx()
            self.stdscr.erase()
            running = self.queue.running_task()
            label = running.label[:40] if running is not None else "..."
            line = f"Finishing queue: {self.queue.pending_count()} left | {label}"
            self.addstr_safe(height // 2, max(0, (width - len(line)) // 2), line, curses.A_BOLD)
            hint = "c: stop waiting"
            self.addstr_safe(height // 2 + 2, max(0, (width - len(hint)) // 2), hint)
            self.stdscr.refresh()
            key = self.stdscr.getch()
            if key in (ord('c'), 27):
                self.last_height = 0
                return False
        if self.queue.errors:
            height, width = self.stdscr.getmaxyx()
            self.stdscr.erase()
            line = f"{len(self.queue.errors)} task(s) failed - press any key"
            self.addstr_safe(height // 2, max(0, (width - len(line)) // 2), line, curses.A_BOLD)
            for offset, (label, error) in enumerate(self.queue.errors[-5:]):
                self.addstr_safe(height // 2 + 2 + offset, 0, f"! {label[:30]}: {error}")
            self.stdscr.refresh()
            self.stdscr.timeout(-1)
            self.stdscr.getch()
            self.stdscr.timeout(200)
        return True

    def show_email_details(self):
        email = self.selected_sender['emails'][self.current_row]
        height, width = self.stdscr.getmaxyx()
        
        self.stdscr.clear()
        self.addstr_safe(0, 0, f"Subject: {email['subject']}", curses.A_BOLD)
        self.addstr_safe(2, 0, f"From: {email['sender']}")
        self.addstr_safe(3, 0, f"Date: {email['date']}")
        if email.get('state'):
            self.addstr_safe(4, 0, f"Queue state: {email['state']}")
        self.addstr_safe(6, 0, "Press any key to return")
        self.stdscr.refresh()
        self.stdscr.timeout(-1)
        self.stdscr.getch()
        self.stdscr.timeout(200)
        self.last_height = 0

def inner_main(stdscr, creds):
    curses.use_default_colors()
    curses.curs_set(0)
    stdscr.timeout(100) 
    stdscr.clear()

    curses.start_color()
    curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)
    stdscr.bkgd(' ', curses.color_pair(1))

    
    socket.setdefaulttimeout(API_TIMEOUT)
    service = build('gmail', 'v1', credentials=creds)

    emails = download_emails(stdscr, service)

    queue = ArchiveQueue(creds)
    queue.start()
    try:
        interface = NCDULikeInterface(stdscr, emails, service, queue)
        interface.run()
    finally:
        queue.shutdown()
    return queue.errors

def main():
    creds = authenticate_gmail()
    errors = wrapper(inner_main, creds)
    if errors:
        print(f"{len(errors)} archive task(s) failed:")
        for label, error in errors:
            print(f"  ! {label}: {error}")
        sys.exit(1)

if __name__ == '__main__':
    main()
