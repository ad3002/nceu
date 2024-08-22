import os
import curses
from curses import wrapper
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
import time
from typing import List, Dict
from collections import defaultdict
from datetime import datetime
import dateutil.parser
import re
from google_auth_oauthlib.flow import InstalledAppFlow
import sys

SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

def authenticate_gmail():
    creds = None
    if os.path.exists('token.json'):
        try:
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        except Exception as error:
            print(f"An error occurred while reading token: {error}")
            os.remove('token.json')
            print("Token file removed. Please restart the program.")
            sys.exit(1)
    if not os.path.exists('credentials.json'):
        print("Please download credentials.json from the Google Cloud Console and save it to the current directory.")
        print("Instructions: https://developers.google.com/gmail/api/quickstart/python")
        print("See our github page for more information: https://github.com/ad3002/nceu")
        sys.exit(1)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
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
    try:
        result = service.users().labels().get(userId='me', id='INBOX').execute()
        return result['messagesTotal']
    except Exception as error:
        print(f"An error occurred while getting total emails: {error}")
        return 0
    
def extract_email(sender):
    match = re.search(r'<(.+?)>', sender)
    if match:
        return match.group(1)
    return sender


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
            results = service.users().messages().list(userId='me', pageToken=next_page_token, maxResults=100, q='in:inbox').execute()
            messages = results.get('messages', [])

            for message in messages:
                msg = service.users().messages().get(userId='me', id=message['id'], format='metadata').execute()
                
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
                    'subject': subject,
                    'sender': sender,
                    'size': size,
                    'date': date
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

class NCDULikeInterface:
    def __init__(self, stdscr, emails, service):
        self.stdscr = stdscr
        self.emails = emails
        self.service = service 
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

    def group_emails(self):
        grouped = defaultdict(list)
        for email in self.emails:
            sender_email = extract_email(email['sender'])
            grouped[sender_email].append(email)
        return [{'sender_email': sender_email, 'sender_full': emails[0]['sender'], 'count': len(emails), 'emails': emails} 
                for sender_email, emails in grouped.items()]

    def sort_emails(self):
        if self.view_mode == 'senders':
            self.grouped_emails.sort(key=lambda x: x[self.sort_by], reverse=self.sort_reverse)
        elif self.view_mode == 'emails':
            self.selected_sender['emails'].sort(key=lambda x: parse_date(x['date']), reverse=True)

    def show_message(self, message):
        height, width = self.stdscr.getmaxyx()
        self.stdscr.addstr(height-2, 0, message.center(width), curses.A_BOLD)
        self.stdscr.refresh()
        time.sleep(1)


    def draw(self):
        height, width = self.stdscr.getmaxyx()
        
        if height != self.last_height or width != self.last_width:
            self.stdscr.clear()
            self.last_height, self.last_width = height, width
        else:
            self.stdscr.erase()

        header = "Inbox Email Manager (grouped by sender email)"
        self.stdscr.addstr(0, (width - len(header)) // 2, header, curses.A_REVERSE)

        sort_info = f"Sorted by {self.sort_by} ({'desc' if self.sort_reverse else 'asc'})"
        self.stdscr.addstr(1, (width - len(sort_info)) // 2, sort_info)

        if self.view_mode == 'senders':
            self.stdscr.addstr(3, 0, f"{'Count':>10} {'Sender':<60}")
        else:
            self.stdscr.addstr(3, 0, f"{'Date':<20} {'Subject':<50}")
        self.stdscr.addstr(4, 0, "-" * (width - 1))

        for i in range(5, height - 1):
            self.stdscr.move(i, 0)
            self.stdscr.clrtoeol() 
            if self.view_mode == 'senders':
                if i - 5 + self.top_row < len(self.grouped_emails):
                    sender_data = self.grouped_emails[i - 5 + self.top_row]
                    if i - 5 + self.top_row == self.current_row:
                        mode = curses.A_REVERSE
                    else:
                        mode = curses.A_NORMAL
                    self.stdscr.addstr(i, 0, f"{sender_data['count']:>10} {sender_data['sender_full'][:60]:<60}", mode)
            else:
                if i - 5 + self.top_row < len(self.selected_sender['emails']):
                    email = self.selected_sender['emails'][i - 5 + self.top_row]
                    if i - 5 + self.top_row == self.current_row:
                        mode = curses.A_REVERSE
                    else:
                        mode = curses.A_NORMAL
                    date = parse_date(email['date']).strftime('%Y-%m-%d %H:%M')
                    self.stdscr.addstr(i, 0, f"{date:<20} {email['subject'][:50]:<50}", mode)

        if self.view_mode == 'senders':
            footer = "q: Quit | s: Change sort | Enter: View emails"
        else:
            footer = "q: Back to senders | a: Archive | Enter: View email details"
        self.stdscr.addstr(height - 1, 0, footer, curses.A_REVERSE)

        self.stdscr.refresh()

    def run(self):
        curses.curs_set(0) 
        while True:
            self.draw()
            key = self.stdscr.getch()

            if key == ord('q'):
                if self.view_mode == 'emails':
                    self.view_mode = 'senders'
                    self.current_row = self.sender_position
                    self.top_row = max(0, self.current_row - curses.LINES + 6)
                else:
                    break
            elif key == curses.KEY_UP and self.current_row > 0:
                self.current_row -= 1
                if self.current_row < self.top_row:
                    self.top_row = self.current_row
            elif key == curses.KEY_DOWN:
                if self.view_mode == 'senders' and self.current_row < len(self.grouped_emails) - 1:
                    self.current_row += 1
                elif self.view_mode == 'emails' and self.current_row < len(self.selected_sender['emails']) - 1:
                    self.current_row += 1
                if self.current_row >= self.top_row + curses.LINES - 6:
                    self.top_row = self.current_row - curses.LINES + 7
            elif key == ord('s') and self.view_mode == 'senders':
                self.change_sort()
            elif key == ord('a') and self.view_mode == 'emails':
                self.archive_email()
            elif key == 10:
                if self.view_mode == 'senders':
                    self.selected_sender = self.grouped_emails[self.current_row]
                    self.sender_position = self.current_row 
                    self.view_mode = 'emails'
                    self.current_row = 0
                    self.top_row = 0
                    self.sort_emails()
                else:
                    self.show_email_details()

    def change_sort(self):
        if self.sort_by == 'count':
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_by = 'count'
            self.sort_reverse = True
        self.sort_emails()

    def archive_email(self):
        if self.view_mode == 'emails':
            email = self.selected_sender['emails'][self.current_row]
            try:
                self.service.users().messages().modify(
                    userId='me',
                    id=email['id'],
                    body={'removeLabelIds': ['INBOX']}
                ).execute()
                
                del self.selected_sender['emails'][self.current_row]
                self.selected_sender['count'] -= 1
                
                if self.selected_sender['count'] == 0:
                    self.grouped_emails = [group for group in self.grouped_emails if group['sender_email'] != self.selected_sender['sender_email']]
                    self.view_mode = 'senders'
                    self.current_row = min(self.sender_position, len(self.grouped_emails) - 1)
                    self.top_row = max(0, self.current_row - curses.LINES + 6)
                elif self.current_row >= len(self.selected_sender['emails']):
                    self.current_row = len(self.selected_sender['emails']) - 1
                
                self.show_message("Email archived successfully")
            except Exception as e:
                self.show_message(f"Error archiving email: {str(e)}")

    def show_email_details(self):
        email = self.selected_sender['emails'][self.current_row]
        height, width = self.stdscr.getmaxyx()
        
        self.stdscr.clear()
        self.stdscr.addstr(0, 0, f"Subject: {email['subject']}", curses.A_BOLD)
        self.stdscr.addstr(2, 0, f"From: {email['sender']}")
        self.stdscr.addstr(3, 0, f"Date: {email['date']}")
        self.stdscr.addstr(5, 0, "Press any key to return")
        self.stdscr.refresh()
        self.stdscr.getch()

def inner_main(stdscr, creds):
    curses.use_default_colors()
    curses.curs_set(0)
    stdscr.timeout(100) 
    stdscr.clear()

    curses.start_color()
    curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)
    stdscr.bkgd(' ', curses.color_pair(1))

    
    service = build('gmail', 'v1', credentials=creds)

    emails = download_emails(stdscr, service)
    
    interface = NCDULikeInterface(stdscr, emails, service)
    interface.run()

def main():
    creds = authenticate_gmail()
    wrapper(inner_main, creds)

if __name__ == '__main__':
    main()