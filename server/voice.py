"""강토끼 목소리: 문장을 Typecast로 한 번 만들어 저장해 두고 다시 씁니다.

- 같은 문장(목소리·모델·감정·빠르기까지 같으면)은 다시 만들지 않습니다.
- 이번 달 만든 글자 수가 한도를 넘으면 더 만들지 않습니다. 앱은 기기 목소리로 대신 말합니다.
- API 키는 출력하거나 기록하지 않습니다. 키는 .env 또는 TYPECAST_* 환경 변수에서 읽습니다.
"""
import hashlib
import json
import os
import re
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

API = 'https://api.typecast.ai/v1/text-to-speech'
TEMPOS = {'0.85': 0.85, '1': 1.0, '1.15': 1.15}          # 앱의 말 빠르기: 천천히 · 보통 · 빠르게
TEXT = re.compile(r"[가-힣0-9A-Za-z .,!?~'\"·()\-]{1,120}")  # 앱이 하는 말에 쓰는 글자만


class BadText(ValueError):
    pass


class VoiceBudget(Exception):
    """이번 달 한도를 다 씀 (또는 Typecast 크레딧 부족)."""


class VoiceUnavailable(Exception):
    """설정이 없거나 Typecast에 연결할 수 없음."""


def read_env(path):
    values = {}
    try:
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('"\'')
    except OSError:
        pass
    return values


def clean_text(text):
    text = ' '.join(str(text).split())
    if not TEXT.fullmatch(text) or not re.search(r'[가-힣0-9]', text):
        raise BadText('Unsupported text')
    return text


def transcode(mp3):
    """320kbps 스테레오를 48kbps 모노로 줄임 (한 문장 약 130KB → 20KB). ffmpeg가 없거나 실패하면 원본."""
    try:
        out = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', 'pipe:0',
                              '-ac', '1', '-ar', '24000', '-b:a', '48k', '-f', 'mp3', 'pipe:1'],
                             input=mp3, capture_output=True, timeout=20, check=True).stdout
        return out if len(out) > 1000 else mp3
    except (OSError, subprocess.SubprocessError):
        return mp3


class Voice:
    def __init__(self, folder, api_key, voice_id, model='ssfm-v31', emotion='happy', monthly_limit=14000,
                 label='', fetch=None):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.api_key, self.voice_id, self.model, self.emotion = api_key, voice_id, model, emotion
        self.limit, self.label = monthly_limit, label
        self.usage_path = self.folder / 'usage.json'
        self.lock = threading.Lock()
        self.gate = threading.Semaphore(2)   # Typecast 무료 플랜 동시 호출 한도
        self.pending = {}
        self.fetch = fetch or self.request

    @property
    def enabled(self):
        return bool(self.api_key and self.voice_id)

    def path(self, text, tempo):
        key = hashlib.sha256(f'{self.voice_id}|{self.model}|{self.emotion}|{tempo}|{text}'.encode()).hexdigest()[:40]
        return self.folder / key[:2] / f'{key}.mp3'

    def usage(self):
        month = datetime.now().strftime('%Y-%m')
        try:
            data = json.loads(self.usage_path.read_text())
        except (OSError, ValueError):
            data = {}
        return data if data.get('month') == month else dict(month=month, chars=0, clips=0)

    def status(self):
        with self.lock:
            used = self.usage()
        return dict(enabled=self.enabled, voice=self.label, month=used['month'], used=used['chars'], limit=self.limit)

    def clip(self, text, tempo='1'):
        text = clean_text(text)
        if tempo not in TEMPOS:
            raise BadText('Unsupported tempo')
        path = self.path(text, tempo)
        if path.exists():
            return path.read_bytes()
        if not self.enabled:
            raise VoiceUnavailable('Voice is not configured')
        with self.lock:
            same = self.pending.setdefault(path.name, threading.Lock())
        with same:   # 같은 문장을 동시에 요청해도 한 번만 만듦
            try:
                if path.exists():
                    return path.read_bytes()
                with self.lock:
                    if self.usage()['chars'] + len(text) > self.limit:
                        raise VoiceBudget('Monthly limit reached')
                with self.gate:
                    audio = self.fetch(text, TEMPOS[tempo])
                with self.lock:   # 만들었으면 저장에 실패해도 사용량은 셈
                    used = self.usage(); used['chars'] += len(text); used['clips'] += 1
                    tmp = self.usage_path.with_suffix('.tmp'); tmp.write_text(json.dumps(used)); os.replace(tmp, self.usage_path)
                audio = transcode(audio)
                path.parent.mkdir(exist_ok=True, mode=0o700)
                tmp = path.with_suffix('.tmp'); tmp.write_bytes(audio); os.replace(tmp, path)
                return audio
            finally:
                with self.lock:
                    self.pending.pop(path.name, None)

    def request(self, text, tempo):
        body = dict(voice_id=self.voice_id, text=text, model=self.model, language='kor',
                    prompt=dict(emotion_type='preset', emotion_preset=self.emotion, emotion_intensity=1.0),
                    output=dict(audio_format='mp3', audio_tempo=tempo))
        req = urllib.request.Request(API, data=json.dumps(body).encode(), method='POST', headers={
            'X-API-KEY': self.api_key, 'Content-Type': 'application/json', 'User-Agent': 'kkangchong-math'})
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                data = response.read()
                if not response.headers.get('Content-Type', '').startswith('audio/') or len(data) < 500:
                    raise VoiceUnavailable('Unexpected response')
                return data
        except urllib.error.HTTPError as error:
            if error.code == 402:
                raise VoiceBudget('Typecast credits') from None
            raise VoiceUnavailable(f'Typecast {error.code}') from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise VoiceUnavailable('Typecast unreachable') from None


def from_env(folder, env_file):
    env = {**read_env(env_file), **{k: v for k, v in os.environ.items() if k.startswith('TYPECAST_')}}
    try:
        limit = int(env.get('TYPECAST_MONTHLY_LIMIT') or 14000)
    except ValueError:
        limit = 14000
    return Voice(folder, env.get('TYPECAST_API_KEY', ''), env.get('TYPECAST_VOICE_ID', 'tc_663c689ada7bbd7f8f1a788a'),
                 model=env.get('TYPECAST_MODEL', 'ssfm-v31'), emotion=env.get('TYPECAST_EMOTION', 'happy'),
                 monthly_limit=limit, label=env.get('TYPECAST_VOICE_LABEL', '로로'))
