# NCeu (NCurses Email Usage)

NCeu is a command-line interface tool for managing and analyzing your Gmail inbox, inspired by the ncdu (NCurses Disk Usage) utility. It provides an interactive, text-based user interface for exploring your emails, grouping them by sender, and performing actions like archiving.

## Features

- Authenticate with Gmail using OAuth2
- Download and analyze emails from your inbox
- Group emails by sender
- Sort emails by count or date
- View email details
- Archive individual emails or all emails from a sender directly from the interface
- Background archive queue: pressing 'a' queues the row and the cursor moves on immediately, so you can
  flag many senders in a row without ever waiting for the Gmail API
- 5 second undo window: a queued task is only sent to Gmail after a short delay, so a mistaken 'a' can
  still be taken back
- Queue inspector with live countdown, per-task removal, and a stop/start switch
- Read a message right in the terminal: full body (plain text, or HTML converted to text),
  headers, attachment list, scrolling
- Read a whole conversation: every message of the thread in order, including the ones you sent,
  with the quoted history folded away
- Failed tasks are shown in the interface (status line, row marker, queue view) and repeated on exit -
  they are never silently dropped
- ncurses-based UI for smooth navigation

## Prerequisites

- Python 3.6+
- pip (Python package manager)

## Installation

You can install NCeu using pip:

```
pip install nceu
```

This will install NCeu as a system-wide application.

If you want to install it from source:

1. Clone this repository:
   ```
   git clone https://github.com/ad3002/nceu.git
   cd nceu
   ```

2. Install the package:
   ```
   pip install .
   ```

3. Set up Google Cloud Project and enable Gmail API:
   - Go to the [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project
   - Enable the Gmail API for your project
   - Create credentials (OAuth client ID) for a desktop application
   - Download the client configuration and save it as `credentials.json` in your working directory

## Setting Up a Test User

For security reasons, it's recommended to set up a test Gmail account instead of using your primary account during development and testing:

1. Create a new Gmail account for testing purposes.
2. In your Google Cloud Console project:
   - Go to the OAuth consent screen settings
   - Add your test Gmail address to the "Test users" section
3. Use this test account when authorizing the application during development and testing.

This approach allows you to safely test all features without risking your primary email account.

## Usage

After installation, you can run NCeu from anywhere in your system by simply typing:

```
nceu
```

On first run, you'll be prompted to authorize the application. Follow the provided URL to grant necessary permissions. Use your test account credentials for this step.

### Navigation

- Use arrow keys (and PgUp/PgDn) to move up and down the list
- Press 'Enter' to view emails from a sender or email details
- Press 's' to change sort order (in sender view)
- Press 't' to switch between grouping by sender and by thread (in sender view)
- Press 'a' to put the current row into the archive queue - a whole sender in sender view, a single
  email in email view. The cursor moves down straight away, so 'a a a a' queues four rows.
- Press 'u' to take the current row back out of the queue
- Press 'v' to open the queue, 'p' to stop or restart it
- Press 'c' to read the whole conversation of the current row
- Press 'q' to go back or quit the application

### Reading messages

'Enter' on an email opens it: subject, from/to, date, attachments and the full body, fetched from
Gmail on demand (the inbox scan itself only downloads metadata, so it stays fast). A message with no
plain text part is converted from HTML; if there is no text at all, the Gmail snippet is shown, and
both cases are labelled. If the body cannot be loaded, the reader says why instead of looking empty -
'r' tries again.

Scrolling: arrows, PgUp/PgDn, space and 'b', Home/End. 'a' queues the message for archiving and
returns to the list, 'c' opens the whole conversation, 'q' goes back.

### Reading conversations

'c' opens the entire thread: every message in order, numbered `1/4`, `2/4`, ..., each with its own
headers and body. Messages you sent are marked `[sent]`, messages that already left the inbox are
marked `[archived]`, and the message you came from is marked with `>>`.

The quoted history at the end of a reply is folded to a `[N quoted lines hidden]` line so a long
chain stays readable - 'h' unfolds it. 'a' queues every inbox message of the conversation for
archiving, 'r' reloads it, 'q' goes back.

Threads are also a grouping mode: 't' in the list view groups the inbox by conversation instead of by
sender, so 'c' on a row reads that conversation and 'a' archives it as a whole.

### Archive queue

Archiving never blocks the interface. Pressing 'a' only appends a task to a queue that a background
worker drains one task at a time.

- Each task waits `ARCHIVE_DELAY` (5 seconds by default) before it is sent to Gmail. Until then it can
  be cancelled and nothing leaves the inbox.
- The second line of the screen shows the queue state: whether it runs or is stopped, how many tasks
  and emails are waiting, the countdown for the next one, how many emails were archived, and how many
  tasks failed.
- Rows are marked with their queue state: `Q` queued, `>` being archived, `-` archived, `!` failed.
  The count column shows how many emails of that sender are still in the inbox.
- In the queue view ('v'): arrow keys to move, 'd' to drop the selected task, 'c' to clear the queue,
  'p' to stop or restart the worker, 'q' to go back. Failed tasks are listed at the bottom.
- Quitting with a non-empty queue asks first: 'w' waits for the queue to drain, 'd' throws the pending
  tasks away, 'c' returns to the list.

## Tests

```
python3 -m pytest tests/
```

The suite drives the real queue worker against a fake Gmail service (delay, cancellation, stop/start,
partial failures), checks body and thread parsing (multipart, HTML, charsets, quoted tails, load
errors), and runs the real ncurses interface inside a pty, asserting that the queue view, the message
reader and the conversation reader were actually reached.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Inspired by the ncdu utility
- Uses Google's Gmail API

## Disclaimer

This tool requires access to your Gmail account. Please review the code and use it at your own risk. Always be cautious when granting access to your email account, and preferably use a test account as described in the "Setting Up a Test User" section.