"""AES-CTR through OpenSSL for Telethon's MTProto-proxy and obfuscated connections.

Telethon 1.x does this layer with pyaes, in pure Python: every byte an account sends or receives
through an MTProto proxy goes through it, and with a couple of dozen accounts that alone kept the
server's only core busy. The same stream cipher from `cryptography` (OpenSSL) gives the very same
bytes many times faster.
"""

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class FastAESModeCTR:
    """Drop-in for telethon.crypto.AESModeCTR: one keystream, which encrypt and decrypt both
    continue (CTR is symmetric), across calls of any length."""

    def __init__(self, key: bytes, iv: bytes):
        assert isinstance(key, bytes) and isinstance(iv, bytes) and len(iv) == 16
        self._stream = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()

    def encrypt(self, data: bytes) -> bytes:
        return self._stream.update(data)

    def decrypt(self, data: bytes) -> bytes:
        return self._stream.update(data)


def install() -> None:
    """Puts it everywhere Telethon took AESModeCTR by name."""
    from telethon import crypto
    from telethon.crypto import aesctr, cdndecrypter
    from telethon.network.connection import tcpmtproxy, tcpobfuscated

    for module in (crypto, aesctr, cdndecrypter, tcpmtproxy, tcpobfuscated):
        module.AESModeCTR = FastAESModeCTR
