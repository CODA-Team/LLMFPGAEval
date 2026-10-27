"""Shared cable-0 provenance. Callers hold the workspace execution lease."""
import uuid

from .config import WORK
from .agent_tools import read_json, write_json, timestamp

STATE_FILE = WORK / 'board-cable0.json'


def begin_download(owner, bit_sha256):
    head = {'attempt': uuid.uuid4().hex, 'owner': str(owner), 'bit_sha256': bit_sha256,
            'ok': False, 'time': timestamp(), 'cable': 0}
    write_json(STATE_FILE, head)
    return head['attempt']


def finish_download(attempt, ok):
    head = read_json(STATE_FILE, {})
    if head.get('attempt') == attempt:
        head.update(ok=bool(ok), time=timestamp())
        write_json(STATE_FILE, head)


def matches_download(attempt, owner, bit_sha256):
    head = read_json(STATE_FILE, {})
    return bool(attempt and head.get('ok') and head.get('attempt') == attempt
                and head.get('owner') == str(owner) and head.get('bit_sha256') == bit_sha256)
