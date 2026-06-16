from openai import OpenAI
from dotenv import load_dotenv
import os
import base64
from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv(override=True)

api_key = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=api_key)

MODEL = "gpt-5-nano"

CONTEXT_SIZE = 8192

TEMPERATURE = 0.1

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"] # permissions
import time

def processParts(parts):
    body_message = ""
    body_html = ""
    for part in parts:
        mimeType = part.get("mimeType")
        if mimeType == 'multipart/alternative':
            subparts = part.get('parts')
            [new_message, new_html] = processParts(subparts)
            body_message += new_message
            body_html += new_html
        elif mimeType == 'text/plain':
            body = part.get("body")
            data = body.get("data")
            new_message = base64.urlsafe_b64decode(data)
            body_message += str(new_message, 'utf-8')
        elif mimeType == 'text/html':
            body = part.get("body")
            data = body.get("data")
            new_html = base64.urlsafe_b64decode(data)
            body_html += str(new_html, 'utf-8')
    return [body_message, body_html]


def createGmailRequest():
    """List the user's gmail labels"""
    emails_output = []
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", GMAIL_SCOPES)
    # If there are no valid user credentials available, let the user log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
        # save credentials for next run
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    try:
        # call the gmail API
        service = build("gmail", "v1", credentials=creds)
        messages = service.users().messages().list(userId="me", q="newer_than:1d category:primary").execute()
        i = 0
        print("Collecting gmails...")
        for message in messages["messages"]:
            i += 1
            msg = service.users().messages().get(userId="me", id=message["id"]).execute()
            subject = [header["value"] for header in msg["payload"]["headers"] if header["name"] == "Subject"]
            from_email = [header["value"] for header in msg["payload"]["headers"] if header["name"] == "From"]
            [body_message, body_html] = processParts([msg["payload"]])
            if (body_message and body_html != ""):
                content = body_message
            else:
                content = BeautifulSoup(body_html, "html.parser").text
            emails_output.append([subject, from_email, content])
            print("Email #" + str(i) + " collected")
        return emails_output
    
    except HttpError as error:
        # TO-DO: handle errors from gmail API
        print(f"An error occurred: {error}")

def summarizeEmails(emails):
    i = 0
    emails_summaries = []
    for email in emails:
        if not email[2] or email[2] == "":
            continue
        i += 1
        print("summarization " + str(i) + " of " + str(len(emails)))
        # add a timeout such that if the response takes too long, we try generating the response again

        try:
            print("Input length: " + str(len(email[2].split(" "))))
            summary = client.chat.completions.create(
                model=MODEL,
                messages=[{
                        "role": "system",
                        "content": (
                            'You are a powerful email-handling personal assistant that is excellent at saving me (your boss) time. '
                            'Your task is to summarize the following email into a single ehader, with any action items listed as bullet points (action items only for IMPORTANT emails). '
                            'Also, categorize the email into one of two categories IMPORTANT (/personal/work/admin) and UNIMPORTANT (marketing/spam/other). '
                            'Also, if the email seems to be a template, I only care about the content that is unique to this email, not that it is a template.'
                        )
                    },
                    {
                        "role": "user",
                        "content": email[2],
                    }
                ]
            )
            emails_summaries.append(summary.choices[0].message.content)

        except Exception as e:
            print("Model Timeout: Not able to summarize the following email: " + email[0][0])
            print(e)

    emails_output = '\n'.join(emails_summaries)
    return emails_output

SUMMARY_FORMAT = """
You are a powerful email-handling personal assistant that is excellent at saving your boss time. 
Create a to-do list from the following email summaries which are classfied as IMPORTANT and UNIMPORTANT. 
Then provide a short written summary of highlights from all of the emails (they are from the past 24hrs). 
Provide ONLY this summary. I don't want any additional text such as 'Here are the items formatted as requested.'
The output MUST be in this format given from the following example. There is a short header of the task to be completed
and underneath are bullet points of a summary and action items from the email.

Expected output format example:

Highlights (past 24 hours):
IMPORTANT:
    Task: Joe requests password for google account
        - Provide Joe with expected password
    Task: Daniel asks for slides from past presentation
        - If you still have the slides, send the slides to Daniel
    Task: Respond to George
        - George wishes you well
        - Wonders how you are doing
    
UNIMPORTANT:
    - Recent sign-in on youtube account from different device
    - HackerRank has a new challenge posted
"""

def createDailySummary(emails_output, client):
    summary = client.chat.completions.create(
        model=MODEL,
        messages=[{
            "role": "system",
            "content": (
                SUMMARY_FORMAT
            )
        },
        {
            "role": "user",
            "content": emails_output
        }]
    )
    return summary

if __name__ == "__main__":

    emails = createGmailRequest()
    print("\n All emails collected. Summarizing... \n")
    emails_output = summarizeEmails(emails)
    print("Approx Total # of Summary Tokens " + str(len(str(emails)) // 4) + ". (A number over 7000 may result in an incomplete summary.) \n")
    print("Summarizing all emails into a daily summary... \n")
    daily_summary = createDailySummary(emails_output, client)
    daily_summary = daily_summary.choices[0].message.content
    print(daily_summary)
    with open("daily_summary.txt", "w") as file:
        file.write(daily_summary)
