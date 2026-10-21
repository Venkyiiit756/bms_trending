"""Publish a post with images to X (Twitter) using the account's OAuth 1.0a user keys.

Needs four repository secrets: X_API_KEY, X_API_SECRET (the app's consumer keys) and
X_ACCESS_TOKEN, X_ACCESS_SECRET (the account's access token, with Read and Write permission).
Images are uploaded with the v2 media endpoint, falling back to v1.1; alt text is best effort.
"""
import os

import requests
from requests_oauthlib import OAuth1

API = 'https://api.x.com/2'
UPLOAD_V1 = 'https://upload.twitter.com/1.1/media'


def _auth():
    return OAuth1(os.environ['X_API_KEY'], os.environ['X_API_SECRET'],
                  os.environ['X_ACCESS_TOKEN'], os.environ['X_ACCESS_SECRET'])


def upload(auth, path, alt):
    with open(path, 'rb') as f:
        r = requests.post(f'{API}/media/upload', auth=auth, files={'media': f},
                          data={'media_category': 'tweet_image'}, timeout=60)
    if r.ok:
        media_id = r.json()['data']['id']
        if alt:
            requests.post(f'{API}/media/metadata', auth=auth, timeout=30,
                          json={'id': media_id, 'metadata': {'alt_text': {'text': alt}}})
        return media_id
    with open(path, 'rb') as f:
        r1 = requests.post(f'{UPLOAD_V1}/upload.json', auth=auth, files={'media': f}, timeout=60)
    if not r1.ok:
        raise RuntimeError(f'media upload failed: v2 HTTP {r.status_code} {r.text[:150]}; '
                           f'v1.1 HTTP {r1.status_code} {r1.text[:150]}')
    media_id = r1.json()['media_id_string']
    if alt:
        requests.post(f'{UPLOAD_V1}/metadata/create.json', auth=auth, timeout=30,
                      json={'media_id': media_id, 'alt_text': {'text': alt}})
    return media_id


def post(text, images, alts):
    auth = _auth()
    ids = [upload(auth, p, a) for p, a in zip(images, alts)]
    r = requests.post(f'{API}/tweets', auth=auth, timeout=30,
                      json={'text': text, **({'media': {'media_ids': ids}} if ids else {})})
    if not r.ok:
        raise RuntimeError(f'post failed: HTTP {r.status_code} {r.text[:200]}')
    return r.json()['data']['id']
