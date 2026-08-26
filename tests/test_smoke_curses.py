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
    ('\x1b[B', 0.1),   # down
    ('a', 0.1),        # queue sender
    ('a', 0.1),        # queue next sender (cursor moved on its own)
    ('a', 0.1),        # and the next one
    ('v', 0.3),        # open the queue view
    ('\x1b[B', 0.1),   # move inside the queue
    ('d', 0.2),        # drop that task
    ('p', 0.2),        # stop the queue
    ('p', 0.2),        # start it again
    ('q', 0.3),        # back to the list
    ('t', 0.2),        # regroup by thread
    ('t', 0.2),        # and back
    ('u', 0.2),        # undo on the current row
    ('\n', 0.3),       # open a sender
    ('a', 0.2),        # queue a single email
    ('u', 0.2),        # undo it
    ('\n', 0.3),       # email details
    (' ', 0.3),        # leave details
    ('q', 0.3),        # back to senders
    ('s', 0.2),        # change sort
    ('q', 0.5),        # quit -> confirmation while the queue is busy
    ('w', 3.0),        # wait for the queue to drain
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
