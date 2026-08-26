"""End-to-end smoke test: the real curses UI driven by real keystrokes in a pty."""
import fcntl
import os
import pty
import struct
import sys
import termios
import time

import pytest


KEYS = [
    ('p', 0.2),        # stop the queue so the tasks pile up deterministically
    ('a', 0.2),        # queue a sender (the cursor moves down on its own)
    ('a', 0.2),        # queue the next one
    ('a', 0.2),        # and the next one
    ('v', 0.4),        # open the queue view
    ('d', 0.3),        # drop the selected task
    ('p', 0.2),        # start the queue again from inside the view
    ('p', 0.2),        # and stop it again
    ('q', 0.3),        # back to the list
    ('t', 0.3),        # group by thread
    ('c', 0.8),        # read the whole conversation of the current thread
    ('h', 0.3),        # unfold the quoted history
    ('h', 0.3),        # fold it back
    (' ', 0.2),        # page down through the conversation
    ('b', 0.2),        # page back up
    ('q', 0.3),        # leave the conversation
    ('t', 0.3),        # back to grouping by sender
    ('\n', 0.4),       # open a sender
    ('a', 0.2),        # queue a single email
    ('u', 0.2),        # take it back out of the queue
    ('\n', 0.8),       # open the message reader (loads the body)
    (' ', 0.2),        # page down through the body
    ('b', 0.2),        # page back up
    ('r', 0.5),        # reload the body
    ('c', 0.8),        # jump from the message to its whole thread
    ('q', 0.3),        # back to the message
    ('q', 0.3),        # back to the email list
    ('q', 0.3),        # back to the senders
    ('s', 0.2),        # change sort order
    ('p', 3.0),        # start the queue and let it drain
    ('q', 0.8),        # quit
    ('w', 3.0),        # if the queue is still busy: wait for it
]


@pytest.mark.skipif(sys.platform == 'win32', reason='pty is POSIX only')
def test_curses_ui_survives_a_full_key_run(tmp_path):
    result_path = tmp_path / 'smoke.txt'
    env_result = str(result_path)
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(repo_root)
        os.environ['TERM'] = 'xterm'
        os.environ['NCEU_SMOKE_RESULT'] = env_result
        os.execv(sys.executable, [sys.executable, os.path.join('tests', 'smoke_curses.py')])

    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 100, 0, 0))
    time.sleep(1.5)  # let curses initialise and paint the first frame

    try:
        for keys, pause in KEYS:
            os.write(fd, keys.encode())
            time.sleep(pause)
        deadline = time.time() + 20
        status = None
        while time.time() < deadline:
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
            try:
                os.read(fd, 4096)
            except OSError:
                pass
            time.sleep(0.1)
        else:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
            pytest.fail("curses UI did not exit; it is stuck on some screen")
    finally:
        os.close(fd)

    assert result_path.exists(), "the UI never reported a result"
    report = result_path.read_text()
    assert report.startswith('OK'), f"curses UI crashed:\n{report}"
    assert os.WEXITSTATUS(status) == 0
    assert 'errors=0' in report
    archived = int(report.split('archived=')[1].split()[0])
    assert archived > 0, "the queue exited without archiving anything"
    bodies = int(report.split('bodies=')[1].split()[0])
    assert bodies >= 2, "opening a message must fetch its body, and 'r' must fetch it again"
    threads = int(report.split('threads=')[1].split()[0])
    assert threads >= 2, "the conversation must be fetched from the thread list and from a message"

    trace = report.split('trace=')[1].strip().split(',')
    assert 'queue-view' in trace, "the queue view was never reached"
    assert 'reader:emails' in trace, "the message reader was never reached"
    assert 'thread:senders' in trace, "'c' on a thread row did not open the conversation"
    assert 'thread:emails' in trace, "'c' inside a message did not open the conversation"
    assert trace.count('enqueue:senders') >= 3
