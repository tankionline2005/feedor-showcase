import os
import random

import pyaes  # Telethon's own, the reference
from telethon.network.connection import tcpmtproxy

import app.tg.client  # noqa: F401  (installs the fast cipher)
from app.tg.fastctr import FastAESModeCTR


def reference(key: bytes, iv: bytes):
    aes = pyaes.AESModeOfOperationCTR(key)
    aes._counter._counter = list(iv)  # what telethon.crypto.AESModeCTR does
    return aes


def test_the_fast_cipher_gives_the_same_bytes_as_telethons():
    rnd = random.Random(7)
    for _ in range(20):
        key, iv = os.urandom(32), os.urandom(16)
        slow, fast = reference(key, iv), FastAESModeCTR(key, iv)
        # a stream in pieces of any length, like reads and writes on a socket
        for _ in range(30):
            chunk = os.urandom(rnd.choice([1, 3, 15, 16, 17, 64, 1000, 4097]))
            assert fast.encrypt(chunk) == slow.encrypt(chunk)
        assert fast.decrypt(b"\x00" * 33) == slow.decrypt(b"\x00" * 33)


def test_telethon_uses_it_for_mtproto_proxies():
    assert tcpmtproxy.AESModeCTR is FastAESModeCTR
