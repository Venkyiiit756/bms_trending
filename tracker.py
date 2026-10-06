import sys
import json
import os
import csv
from datetime import datetime

try:
    from curl_cffi import requests as b_requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "curl_cffi"])
    from curl_cffi import requests as b_requests

CSV_FILE = 'bms_booking_log.csv'

def init_csv():
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp', 'movie_name', 'metric_type', 'metric_value', 'raw_text'])

def log_to_csv(movie_name, metric_type, metric_value, raw_text):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(CSV_FILE, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([timestamp, movie_name, metric_type, metric_value, raw_text])
    print(f"[{timestamp}] Saved: {movie_name} | {metric_type}: {metric_value}")

def fetch_and_log():
    session = b_requests.Session()
    base = 'https://in.bookmyshow.com'
    page = base + '/movies/hyderabad/drishyam-the-conclusion/ET00477911'
    
    app_headers = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': page,
        'x-platform': 'AND',
        'x-platform-code': 'ANDROID',
        'x-app-code': 'MOBAND2',
        'x-region-code': 'HYD',
        'x-region-slug': 'hyderabad',
        'x-app-version': '18.2.8',
        'x-app-version-code': '18281',
        'Origin': base
    }
    
    try:
        api_response = session.get(
            base + '/api/movies/v1/synopsis/secondary?eventcode=ET00477911&channel=mobile',
            headers=app_headers,
            impersonate="chrome",
            timeout=25
        )
        status = api_response.status_code
        body = api_response.text
    except Exception as e:
        print('API request error:', e)
        return

    if status != 200:
        print(f'API failed with status {status}')
        return

    try:
        data = json.loads(body)
    except ValueError:
        print('Response is not JSON.')
        return

    movie_name = 'Drishyam 3'
    meta = data.get('meta', {})
    if isinstance(meta, dict) and 'event' in meta:
         movie_name = meta['event'].get('eventName', movie_name)

    if not isinstance(data.get('widgets'), dict):
        print('Unexpected response: widgets missing.')
        return

    found = False
    for widget in data['widgets'].values():
        obj = widget.get('objectData') or {}
        if (obj.get('action') or {}).get('label') == 'Trending':
            text = ''.join(c.get('text', '') for c in
                           (obj.get('text') or {}).get('components', []))
            if text:
                found = True
                metric_type = 'unknown'
                metric_value = text
                if 'last 1 hour' in text.lower():
                    metric_type = '1_hour'
                    metric_value = text.split(' ')[0]
                elif 'last 24 hours' in text.lower():
                    metric_type = '24_hours'
                    metric_value = text.split(' ')[0]
                
                log_to_csv(movie_name, metric_type, metric_value, text)
                break
                
    if not found:
        print('No trending widget or stats returned.')

if __name__ == '__main__':
    init_csv()
    fetch_and_log()