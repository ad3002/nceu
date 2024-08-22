# NCeu (NCurses Email Usage)

NCeu is a command-line interface tool for managing and analyzing your Gmail inbox, inspired by the ncdu (NCurses Disk Usage) utility. It provides an interactive, text-based user interface for exploring your emails, grouping them by sender, and performing actions like archiving.

## Features

- Authenticate with Gmail using OAuth2
- Download and analyze emails from your inbox
- Group emails by sender
- Sort emails by count or date
- View email details
- Archive emails directly from the interface
- ncurses-based UI for smooth navigation

## Prerequisites

- Python 3.6+
- pip (Python package manager)

## Installation

1. Clone this repository:
   ```
   git clone https://github.com/ad3002/nceu.git
   cd nceu
   ```

2. Install the required packages:
   ```
   pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client python-dateutil
   ```

3. Set up Google Cloud Project and enable Gmail API:
   - Go to the [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project
   - Enable the Gmail API for your project
   - Create credentials (OAuth client ID) for a desktop application
   - Download the client configuration and save it as `credentials.json` in the project directory

## Setting Up a Test User

For security reasons, it's recommended to set up a test Gmail account instead of using your primary account during development and testing:

1. Create a new Gmail account for testing purposes.
2. In your Google Cloud Console project:
   - Go to the OAuth consent screen settings
   - Add your test Gmail address to the "Test users" section
3. Use this test account when authorizing the application during development and testing.

This approach allows you to safely test all features without risking your primary email account.

## Usage

Run the script:

```
python nceu.py
```

On first run, you'll be prompted to authorize the application. Follow the provided URL to grant necessary permissions. Use your test account credentials for this step.

### Navigation

- Use arrow keys to move up and down the list
- Press 'Enter' to view emails from a sender or email details
- Press 's' to change sort order (in sender view)
- Press 'a' to archive an email (in email view)
- Press 'q' to go back or quit the application

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Inspired by the ncdu utility
- Uses Google's Gmail API

## Disclaimer

This tool requires access to your Gmail account. Please review the code and use it at your own risk. Always be cautious when granting access to your email account, and preferably use a test account as described in the "Setting Up a Test User" section.
