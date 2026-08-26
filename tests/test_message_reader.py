"""Tests for reading a message body and a whole conversation."""
import base64

import pytest

from nceu import main


def encode(text, charset='utf-8'):
    return base64.urlsafe_b64encode(text.encode(charset)).decode('ascii')


def plain_part(text, charset='utf-8'):
    return {
        'mimeType': 'text/plain',
        'headers': [{'name': 'Content-Type', 'value': f'text/plain; charset={charset}'}],
        'body': {'data': encode(text, charset)},
    }


def html_part(markup):
    return {'mimeType': 'text/html', 'body': {'data': encode(markup)}}


def attachment_part(name, size=2048):
    return {'mimeType': 'application/pdf', 'filename': name,
            'body': {'size': size, 'attachmentId': 'att-1'}}


class FakeScreen:
    def getmaxyx(self):
        return 24, 100

    def erase(self):
        pass

    def addstr(self, *args, **kwargs):
        pass

    def refresh(self):
        pass


class ServiceStub:
    def __init__(self, message=None, thread=None, error=None):
        self.message = message
        self.thread = thread
        self.error = error
        self.message_calls = []
        self.thread_calls = []

    def users(self):
        return self

    def messages(self):
        return _Endpoint(self, 'message')

    def threads(self):
        return _Endpoint(self, 'thread')


class _Endpoint:
    def __init__(self, service, kind):
        self.service = service
        self.kind = kind

    def get(self, userId=None, id=None, format=None):
        service, kind = self.service, self.kind

        class Exec:
            def execute(self, num_retries=0):
                if service.error:
                    raise RuntimeError(service.error)
                if kind == 'message':
                    service.message_calls.append((id, format))
                    return service.message
                service.thread_calls.append((id, format))
                return service.thread

        return Exec()


def make_email(msg_id='m1', thread_id='t1'):
    return {'id': msg_id, 'threadId': thread_id, 'subject': 'Quarterly report',
            'sender': 'Alice <alice@example.com>', 'size': 100,
            'date': 'Mon, 1 Jan 2024 10:00:00 +0000', 'state': None, 'task': None}


# --- body extraction ---------------------------------------------------

def test_plain_text_wins_over_html():
    payload = {'mimeType': 'multipart/alternative',
               'parts': [plain_part('The plain body'), html_part('<p>The html body</p>')]}
    assert main.extract_message_body(payload) == ('The plain body', 'plain')


def test_html_is_converted_when_there_is_no_plain_part():
    payload = {'mimeType': 'multipart/alternative',
               'parts': [html_part('<style>p{color:red}</style>'
                                   '<p>Hello&nbsp;&amp; welcome</p><br><p>Second line</p>')]}
    text, kind = main.extract_message_body(payload)
    assert kind == 'html'
    assert 'color:red' not in text, "style blocks must not leak into the text"
    assert 'Hello & welcome' in text
    assert 'Second line' in text
    assert '<' not in text


def test_empty_plain_part_falls_through_to_html():
    payload = {'parts': [plain_part('   \n  '), html_part('<p>Real content</p>')]}
    text, kind = main.extract_message_body(payload)
    assert kind == 'html'
    assert text == 'Real content'


def test_nested_multipart_and_attachments():
    payload = {
        'mimeType': 'multipart/mixed',
        'parts': [
            {'mimeType': 'multipart/alternative',
             'parts': [plain_part('Nested body'), html_part('<p>nested html</p>')]},
            attachment_part('report.pdf', 4096),
        ],
    }
    assert main.extract_message_body(payload) == ('Nested body', 'plain')
    assert main.list_attachments(payload) == [('report.pdf', 4096)]


def test_non_utf8_charset_is_decoded():
    payload = {'parts': [plain_part('Привет, мир', charset='windows-1251')]}
    text, kind = main.extract_message_body(payload)
    assert kind == 'plain'
    assert text == 'Привет, мир'


def test_unknown_charset_does_not_crash():
    part = plain_part('body text')
    part['headers'] = [{'name': 'Content-Type', 'value': 'text/plain; charset=x-unknown-42'}]
    text, kind = main.extract_message_body({'parts': [part]})
    assert (text, kind) == ('body text', 'plain')


def test_base64_without_padding_is_decoded():
    data = base64.urlsafe_b64encode(b'abcde').decode('ascii').rstrip('=')
    assert main.decode_base64url(data) == b'abcde'


# --- fetching ----------------------------------------------------------

def test_fetch_email_body_fills_the_email():
    service = ServiceStub(message={
        'id': 'm1', 'snippet': 'ignored snippet',
        'payload': {
            'headers': [{'name': 'To', 'value': 'me@example.com'},
                        {'name': 'Cc', 'value': 'boss@example.com'}],
            'parts': [plain_part('Line one\nLine two'), attachment_part('a.pdf', 1024)],
        }})
    email = make_email()

    main.fetch_email_body(service, email)

    assert service.message_calls == [('m1', 'full')]
    assert email['body'] == 'Line one\nLine two'
    assert email['body_kind'] == 'plain'
    assert email['body_error'] is None
    assert email['to'] == 'me@example.com'
    assert email['cc'] == 'boss@example.com'
    assert email['attachments'] == [('a.pdf', 1024)]


def test_fetch_falls_back_to_the_snippet_when_there_is_no_text_part():
    service = ServiceStub(message={'id': 'm1', 'snippet': 'Short &amp; sweet',
                                   'payload': {'parts': [attachment_part('a.pdf')]}})
    email = make_email()

    main.fetch_email_body(service, email)

    assert email['body'] == 'Short & sweet'
    assert email['body_kind'] == 'snippet'


def test_fetch_failure_is_stored_and_rendered_not_swallowed():
    service = ServiceStub(error='quota exceeded')
    email = make_email()

    main.fetch_email_body(service, email)

    assert email['body'] is None
    assert email['body_error'] == 'quota exceeded'

    lines = [text for text, _ in main.build_reader_lines(email, 80)]
    assert any('quota exceeded' in line for line in lines), \
        "a message that failed to load must say so instead of looking empty"
    assert any('Press r to try again.' in line for line in lines)


# --- reader rendering --------------------------------------------------

def test_reader_shows_headers_and_wraps_the_body():
    email = make_email()
    email.update({'to': 'me@example.com', 'body': 'word ' * 60, 'body_kind': 'plain',
                  'attachments': [('report.pdf', 2048)]})

    lines = main.build_reader_lines(email, 40)
    texts = [text for text, _ in lines]

    assert texts[0] == 'Subject: Quarterly report'
    assert 'From: Alice <alice@example.com>' in texts
    assert 'To: me@example.com' in texts
    assert 'Date: Mon, 1 Jan 2024 10:00:00 +0000' in texts
    assert 'Attachment: report.pdf (2.0 KiB)' in texts
    assert all(len(text) <= 40 for text in texts), "no line may overflow the terminal width"
    assert len([text for text in texts if text.startswith('word')]) > 1, "long body must wrap"
    assert [text for text, is_header in lines if is_header], "headers must be highlighted"


def test_reader_marks_converted_html_and_empty_bodies():
    email = make_email()
    email.update({'body': 'converted text', 'body_kind': 'html'})
    assert main.BODY_KIND_NOTES['html'] in [text for text, _ in main.build_reader_lines(email, 80)]

    email.update({'body': '   ', 'body_kind': None})
    texts = [text for text, _ in main.build_reader_lines(email, 80)]
    assert '(this message has no readable text)' in texts


def test_reader_says_loading_before_the_body_arrives():
    email = make_email()
    assert 'Loading...' in [text for text, _ in main.build_reader_lines(email, 80)]


# --- quoted tails ------------------------------------------------------

def test_quoted_history_is_folded_away():
    body = ("Sounds good, thanks!\n"
            "\n"
            "On Mon, 1 Jan 2024, Alice wrote:\n"
            "> the original question\n"
            "> second quoted line\n"
            ">\n"
            "> regards\n")
    visible, hidden = main.split_quoted_tail(body)

    assert visible == 'Sounds good, thanks!'
    assert hidden == 5, "the attribution line and the quote belong to the folded block"


def test_short_quotes_are_kept():
    body = "Answer above\n> one quoted line\n"
    visible, hidden = main.split_quoted_tail(body)
    assert hidden == 0
    assert visible == body


def test_message_without_quotes_is_untouched():
    body = "Just a plain reply\nwith two lines"
    assert main.split_quoted_tail(body) == (body, 0)


# --- thread ------------------------------------------------------------

@pytest.fixture
def thread_service():
    return ServiceStub(thread={'id': 't1', 'messages': [
        {'id': 'm1', 'labelIds': ['INBOX'], 'snippet': 's1',
         'payload': {'headers': [{'name': 'From', 'value': 'Alice <alice@example.com>'},
                                 {'name': 'Date', 'value': 'Mon, 1 Jan 2024 10:00:00 +0000'},
                                 {'name': 'Subject', 'value': 'Quarterly report'}],
                     'parts': [plain_part('Can you send the report?')]}},
        {'id': 'm2', 'labelIds': ['SENT'], 'snippet': 's2',
         'payload': {'headers': [{'name': 'From', 'value': 'Me <me@example.com>'},
                                 {'name': 'Date', 'value': 'Mon, 1 Jan 2024 11:00:00 +0000'},
                                 {'name': 'Subject', 'value': 'Re: Quarterly report'}],
                     'parts': [plain_part("Here it is.\n\nOn Mon, Alice wrote:\n"
                                          "> Can you send the report?\n> thanks\n> A.")]}},
    ]})


def test_fetch_thread_returns_every_message_in_order(thread_service):
    messages, error = main.fetch_thread(thread_service, 't1')

    assert error is None
    assert thread_service.thread_calls == [('t1', 'full')]
    assert [message['id'] for message in messages] == ['m1', 'm2']
    assert messages[0]['sender'] == 'Alice <alice@example.com>'
    assert messages[0]['body'] == 'Can you send the report?'
    assert messages[1]['labels'] == ['SENT']


def test_fetch_thread_failure_is_surfaced():
    messages, error = main.fetch_thread(ServiceStub(error='thread not found'), 't1')
    assert messages is None
    assert error == 'thread not found'

    lines = [text for text, _ in main.build_thread_lines(None, 80, error=error)]
    assert any('thread not found' in line for line in lines)


def test_thread_lines_number_messages_and_fold_quotes(thread_service):
    messages, _ = main.fetch_thread(thread_service, 't1')

    lines = main.build_thread_lines(messages, 70, focus_id='m2')
    texts = [text for text, _ in lines]

    assert any(text.startswith('-- 1/2') for text in texts)
    assert any(text.startswith('>> 2/2') for text in texts), "the opened message is marked"
    assert any('[sent]' in text for text in texts), "your own reply is labelled as sent"
    assert 'From: Alice <alice@example.com>' in texts
    assert 'Can you send the report?' in texts
    assert 'Here it is.' in texts
    assert any('quoted lines hidden' in text for text in texts)
    assert '> Can you send the report?' not in texts, "quoted history is folded by default"


def test_thread_lines_label_a_message_that_left_the_inbox(thread_service):
    messages, _ = main.fetch_thread(thread_service, 't1')
    messages[1]['labels'] = ['CATEGORY_PERSONAL']

    texts = [text for text, _ in main.build_thread_lines(messages, 70)]
    assert any('[archived]' in text for text in texts)


def test_thread_lines_can_show_the_quotes(thread_service):
    messages, _ = main.fetch_thread(thread_service, 't1')
    texts = [text for text, _ in main.build_thread_lines(messages, 70, show_quotes=True)]

    assert '> Can you send the report?' in texts
    assert not any('quoted lines hidden' in text for text in texts)


def test_thread_lines_never_overflow_the_width(thread_service):
    messages, _ = main.fetch_thread(thread_service, 't1')
    for width in (30, 45, 100):
        texts = [text for text, _ in main.build_thread_lines(messages, width)]
        assert all(len(text) <= width for text in texts), f"overflow at width {width}"


# --- interface wiring --------------------------------------------------

def make_interface(emails, service):
    interface = main.NCDULikeInterface.__new__(main.NCDULikeInterface)
    interface.stdscr = FakeScreen()
    interface.emails = emails
    interface.service = service
    interface.queue = None
    interface.group_mode = 'sender'
    interface.view_mode = 'senders'
    interface.current_row = 0
    interface.top_row = 0
    interface.selected_sender = None
    interface.message = ""
    interface.message_until = 0.0
    interface.thread_cache = {}
    interface.grouped_emails = interface.group_emails()
    return interface


def test_thread_id_of_the_current_row(thread_service):
    emails = [make_email('m1', 't1'), make_email('m2', 't1'), make_email('m3', 't2')]
    emails[2]['sender'] = 'Alice <alice@example.com>'
    interface = make_interface(emails, thread_service)

    # one sender, two different threads -> ambiguous, the user is told why
    thread_id, problem = interface.current_thread_id()
    assert thread_id is None
    assert 'several threads' in problem

    interface.group_mode = 'thread'
    interface.grouped_emails = interface.group_emails()
    interface.current_row = 0
    thread_id, problem = interface.current_thread_id()
    assert problem is None
    assert thread_id in ('t1', 't2')

    interface.group_mode = 'sender'
    interface.grouped_emails = interface.group_emails()
    interface.view_mode = 'emails'
    interface.selected_sender = interface.grouped_emails[0]
    interface.current_row = 0
    assert interface.current_thread_id() == (interface.selected_sender['emails'][0]['threadId'], None)


def test_loading_a_thread_caches_it(thread_service):
    interface = make_interface([make_email('m1', 't1')], thread_service)

    messages, error = interface.load_thread('t1')

    assert error is None
    assert [message['id'] for message in messages] == ['m1', 'm2']
    assert interface.thread_cache['t1'] == (messages, None)

    interface.load_thread('t1')
    assert len(thread_service.thread_calls) == 2, "an explicit reload refetches"


def test_reader_lines_never_overflow_the_width():
    email = make_email()
    email.update({
        'to': 'someone.with.a.very.long.address@a-long-domain-name.example.com',
        'body': 'A ' + 'very ' * 40 + 'long paragraph.\n\nSecond paragraph.',
        'body_kind': 'html',
        'attachments': [('a-report-with-a-really-long-file-name.pdf', 2048)],
    })
    for width in (24, 40, 100):
        texts = [text for text, _ in main.build_reader_lines(email, width)]
        assert all(len(text) <= width for text in texts), f"overflow at width {width}"

    email['body'] = None
    email['body_error'] = 'a rather long error message ' * 4
    for width in (24, 40, 100):
        texts = [text for text, _ in main.build_reader_lines(email, width)]
        assert all(len(text) <= width for text in texts), f"error overflow at width {width}"
