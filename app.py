from flask import Flask, render_template, send_file, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import requests
import re
import pdfplumber
from io import BytesIO
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import os
from concurrent.futures import ThreadPoolExecutor
import time

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# Directory to store extracted email files
extracted_folder = os.path.join(os.getcwd(), 'extracted_emails')
if not os.path.exists(extracted_folder):
    os.makedirs(extracted_folder)

# Initialize ThreadPoolExecutor with 10 workers (10 parallel tasks)
executor = ThreadPoolExecutor(max_workers=10)

# Function to extract emails using regex
def extract_emails(text):
    """Extract email addresses from text."""
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    return re.findall(email_pattern, text)

# Function to extract PDF links from a webpage
def extract_pdfs_from_page(page_url):
    """Extract PDF links from a web page."""
    pdf_links = []
    try:
        response = requests.get(page_url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        for link in soup.find_all('a', href=True):
            href = link['href']
            if href.endswith('.pdf'):
                full_url = urljoin(page_url, href)
                pdf_links.append(full_url)
    except Exception as e:
        print(f"Error extracting PDFs from {page_url}: {e}")
    return pdf_links

# Function to extract emails from a PDF
def extract_emails_from_pdf(pdf_url):
    """Extract emails from a PDF."""
    emails = set()
    try:
        response = requests.get(pdf_url, timeout=10)
        response.raise_for_status()
        with pdfplumber.open(BytesIO(response.content)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                if text:
                    extracted_emails = extract_emails(text)
                    if extracted_emails:
                        emails.update(extracted_emails)
    except Exception as e:
        print(f"Error processing PDF {pdf_url}: {e}")
    return list(emails)

# Function to handle email extraction for a given URL (including extracting PDFs and emails)
def process_url(url, user_id):
    """Process a single URL, extract PDFs, and emails."""
    socketio.emit('status_update', {'data': f'Processing URL: {url}'}, room=user_id)
    pdf_links = extract_pdfs_from_page(url)
    if pdf_links:
        socketio.emit('status_update', {'data': f'Found {len(pdf_links)} PDFs in {url}'}, room=user_id)
    else:
        socketio.emit('status_update', {'data': f'No PDFs found at {url}'}, room=user_id)

    all_emails = set()
    for pdf_url in pdf_links:
        emails = extract_emails_from_pdf(pdf_url)
        all_emails.update(emails)
        socketio.emit('status_update', {'data': f'Extracted {len(emails)} emails from {pdf_url}'}, room=user_id)

    return all_emails

@socketio.on('start_extraction')
def handle_extraction(urls):
    """Handle extraction process with concurrency."""
    # Generate a unique identifier for each user
    user_id = request.sid  # Use the session ID of the user

    # Join the user to a specific room based on their session ID
    join_room(user_id)

    all_emails = set()
    start_time = time.time()

    # Submit tasks to process each URL concurrently
    future_to_url = {executor.submit(process_url, url, user_id): url for url in urls}

    # Wait for all tasks to complete and gather results
    for future in future_to_url:
        emails = future.result()
        all_emails.update(emails)

    email_list = list(all_emails)
    email_count = len(email_list)

    file_name = f'{email_count}-extracted_emails.txt'
    file_path = os.path.join(extracted_folder, file_name)

    try:
        with open(file_path, 'w') as txt_file:
            for email in email_list:
                txt_file.write(email + '\n')
        print(f"File successfully created at: {file_path}")
    except Exception as e:
        print(f"Error writing file: {e}")

    socketio.emit('extraction_complete', {'emails': email_list, 'count': email_count, 'file_name': file_name}, room=user_id)
    end_time = time.time()
    socketio.emit('status_update', {'data': f"Extraction completed in {end_time - start_time:.2f} seconds."}, room=user_id)

    # Leave the room when the task is done (optional cleanup)
    leave_room(user_id)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download/<filename>')
def download_file(filename):
    """Serve the extracted emails as a downloadable file."""
    file_path = os.path.join(extracted_folder, filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    else:
        return "File not found", 404

if __name__ == "__main__":
    socketio.run(app, debug=True)
