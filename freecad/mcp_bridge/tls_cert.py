"""Self-signed RSA-2048 X.509 v3 certificate and private key generator.

Generates a 2048-bit RSA key (`e = 65537`) and an 825-day X.509 v3
certificate (`CA:FALSE`, `digitalSignature` and `keyEncipherment`,
`serverAuth`) covering the given addresses beyond loopback (`iPAddress`
and `dNSName` Subject Alternative Names), using Python's standard library
only (`secrets`, `hashlib`, `ipaddress`, `math`, `datetime`). Writes
`tls-cert.pem` and `tls-key.pem` in `token_file.token_dir()`, with
`tls-key.pem` readable by the user only.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import ipaddress
import math
import os
import secrets
from pathlib import Path

from freecad.mcp_bridge import config, token_file

CERT_FILE_NAME = "tls-cert.pem"
KEY_FILE_NAME = "tls-key.pem"
RSA_BITS = 2048
PRIME_BITS = RSA_BITS // 2
RSA_E = 65537
MILLER_RABIN_ROUNDS = 40
VALIDITY_DAYS = 825

# DER prefix for DigestInfo(AlgorithmIdentifier(sha256, NULL),
# OCTET STRING(32))
SHA256_DIGEST_INFO_PREFIX = bytes.fromhex(
    "3031300d060960864801650304020105000420"
)

_OID_RSA_ENCRYPTION = (1, 2, 840, 113549, 1, 1, 1)
_OID_SHA256_WITH_RSA = (1, 2, 840, 113549, 1, 1, 11)
_OID_COMMON_NAME = (2, 5, 4, 3)
_OID_KEY_USAGE = (2, 5, 29, 15)
_OID_SUBJECT_ALT_NAME = (2, 5, 29, 17)
_OID_BASIC_CONSTRAINTS = (2, 5, 29, 19)
_OID_EXT_KEY_USAGE = (2, 5, 29, 37)
_OID_KP_SERVER_AUTH = (1, 3, 6, 1, 5, 5, 7, 3, 1)


def _small_primes(limit: int = 2048) -> tuple[int, tuple[int, ...]]:
    sieve = bytearray([1]) * (limit + 1)
    primes: list[int] = []
    product = 1
    for i in range(3, limit + 1, 2):
        if sieve[i]:
            primes.append(i)
            product *= i
            for j in range(i * i, limit + 1, 2 * i):
                sieve[j] = 0
    return product, tuple(primes)


_SMALL_PRIME_PRODUCT, _SMALL_PRIMES = _small_primes()


def _is_probable_prime(n: int, rounds: int = MILLER_RABIN_ROUNDS) -> bool:
    """Miller-Rabin probable-prime test using `secrets` for every base."""
    for p in _SMALL_PRIMES:
        if n % p == 0:
            return n == p
    d = n - 1
    s = (d & -d).bit_length() - 1
    d >>= s
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _generate_prime(bits: int = PRIME_BITS) -> int:
    """Generate a random probable prime of `bits` bits with the top two bits
    set and `gcd(p - 1, RSA_E) == 1`."""
    top_two_bits = (1 << (bits - 1)) | (1 << (bits - 2))
    while True:
        candidate = secrets.randbits(bits) | top_two_bits | 1
        if math.gcd(candidate, _SMALL_PRIME_PRODUCT) != 1:
            continue
        if math.gcd(candidate - 1, RSA_E) != 1:
            continue
        if _is_probable_prime(candidate):
            return candidate


def generate_rsa_keypair() -> dict[str, int]:
    """Generate a 2048-bit RSA keypair (`e = 65537`)."""
    min_diff = 1 << (PRIME_BITS // 2 + 64)
    while True:
        p = _generate_prime(PRIME_BITS)
        q = _generate_prime(PRIME_BITS)
        if p == q or abs(p - q) <= min_diff:
            continue
        if p < q:
            p, q = q, p
        n = p * q
        if n.bit_length() != RSA_BITS:
            continue
        phi = (p - 1) * (q - 1)
        if math.gcd(RSA_E, phi) != 1:
            continue
        d = pow(RSA_E, -1, phi)
        dp = d % (p - 1)
        dq = d % (q - 1)
        qinv = pow(q, -1, p)
        return {
            "n": n,
            "e": RSA_E,
            "d": d,
            "p": p,
            "q": q,
            "dp": dp,
            "dq": dq,
            "qinv": qinv,
        }


def _der_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    encoded = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(encoded)]) + encoded


def _der_tlv(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + _der_length(len(body)) + body


def _der_integer(value: int) -> bytes:
    if value < 0:
        raise ValueError("DER integer must be non-negative")
    if value == 0:
        return _der_tlv(0x02, b"\x00")
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    if raw[0] & 0x80:
        raw = b"\x00" + raw
    return _der_tlv(0x02, raw)


def _der_sequence(items: list[bytes] | tuple[bytes, ...]) -> bytes:
    return _der_tlv(0x30, b"".join(items))


def _der_set(items: list[bytes] | tuple[bytes, ...]) -> bytes:
    return _der_tlv(0x31, b"".join(items))


def _der_octet_string(data: bytes) -> bytes:
    return _der_tlv(0x04, data)


def _der_bit_string(data: bytes, unused_bits: int = 0) -> bytes:
    return _der_tlv(0x03, bytes([unused_bits]) + data)


def _der_boolean(value: bool) -> bytes:
    return _der_tlv(0x01, b"\xff" if value else b"\x00")


def _der_null() -> bytes:
    return b"\x05\x00"


def _der_utf8_string(text: str) -> bytes:
    return _der_tlv(0x0C, text.encode("utf-8"))


def _der_oid(oid: tuple[int, ...]) -> bytes:
    if len(oid) < 2:
        raise ValueError("OID requires at least two arcs")
    out = bytearray([40 * oid[0] + oid[1]])
    for arc in oid[2:]:
        if arc == 0:
            out.append(0)
            continue
        chunks: list[int] = []
        val = arc
        while val > 0:
            chunks.append(val & 0x7F)
            val >>= 7
        for chunk in reversed(chunks[1:]):
            out.append(0x80 | chunk)
        out.append(chunks[0])
    return _der_tlv(0x06, bytes(out))


def _der_time(dt: datetime.datetime) -> bytes:
    utc = dt.astimezone(datetime.timezone.utc)
    if 1950 <= utc.year <= 2049:
        return _der_tlv(0x17, utc.strftime("%y%m%d%H%M%SZ").encode("ascii"))
    return _der_tlv(0x18, utc.strftime("%Y%m%d%H%M%SZ").encode("ascii"))


def _pem_wrap(label: str, der_bytes: bytes) -> str:
    b64 = base64.b64encode(der_bytes).decode("ascii")
    lines = [b64[i : i + 64] for i in range(0, len(b64), 64)]
    body = "\n".join(lines)
    return f"-----BEGIN {label}-----\n{body}\n-----END {label}-----\n"


def encode_private_key_pem(key: dict[str, int]) -> str:
    """Encode `key` as a PKCS#1 `RSA PRIVATE KEY` PEM string."""
    der = _der_sequence(
        [
            _der_integer(0),
            _der_integer(key["n"]),
            _der_integer(key["e"]),
            _der_integer(key["d"]),
            _der_integer(key["p"]),
            _der_integer(key["q"]),
            _der_integer(key["dp"]),
            _der_integer(key["dq"]),
            _der_integer(key["qinv"]),
        ]
    )
    return _pem_wrap("RSA PRIVATE KEY", der)


def _der_name(common_name: str) -> bytes:
    atv = _der_sequence(
        [_der_oid(_OID_COMMON_NAME), _der_utf8_string(common_name)]
    )
    return _der_sequence([_der_set([atv])])


def _der_extension(
    oid: tuple[int, ...], value_der: bytes, critical: bool = False
) -> bytes:
    items = [_der_oid(oid)]
    if critical:
        items.append(_der_boolean(True))
    items.append(_der_octet_string(value_der))
    return _der_sequence(items)


def _der_san_extension(hosts: list[str]) -> bytes:
    names: list[bytes] = []
    for raw in hosts:
        host = raw.strip()
        if not host:
            continue
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        try:
            ip_obj = ipaddress.ip_address(host)
            names.append(_der_tlv(0x87, ip_obj.packed))
        except ValueError:
            names.append(_der_tlv(0x82, host.encode("ascii")))
    return _der_extension(
        _OID_SUBJECT_ALT_NAME, _der_sequence(names), critical=False
    )


def pkcs1_v15_sha256_pad(
    message: bytes, modulus_bytes: int = RSA_BITS // 8
) -> bytes:
    """Return the PKCS#1 v1.5 EMSA-PKCS1-v1_5 SHA-256 encoded block for
    `message`."""
    digest = hashlib.sha256(message).digest()
    digest_info = SHA256_DIGEST_INFO_PREFIX + digest
    pad_len = modulus_bytes - 3 - len(digest_info)
    if pad_len < 8:
        raise ValueError("RSA modulus too short for PKCS#1 v1.5 SHA-256")
    return b"\x00\x01" + (b"\xff" * pad_len) + b"\x00" + digest_info


def _sign_pkcs1_v15_sha256(tbs_der: bytes, key: dict[str, int]) -> bytes:
    modulus_bytes = RSA_BITS // 8
    em = pkcs1_v15_sha256_pad(tbs_der, modulus_bytes)
    sig_int = pow(int.from_bytes(em, "big"), key["d"], key["n"])
    return sig_int.to_bytes(modulus_bytes, "big")


def beyond_loopback_hosts(entries: list[str] | tuple[str, ...]) -> list[str]:
    """Return the distinct hosts beyond loopback from `entries`, in order."""
    hosts: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        host, _ = config.parse_listen_entry(entry)
        if config.is_loopback_address(host):
            continue
        try:
            norm = str(ipaddress.ip_address(host))
        except ValueError:
            norm = host.lower()
        if norm not in seen:
            seen.add(norm)
            hosts.append(norm if ":" not in norm else norm)
    return hosts


def generate_self_signed_cert(
    hosts: list[str] | tuple[str, ...],
    key: dict[str, int] | None = None,
    now: datetime.datetime | None = None,
) -> tuple[str, str, dict[str, object]]:
    """Generate a self-signed RSA-2048 X.509 v3 certificate and private key
    covering `hosts`.

    Returns `(cert_pem, key_pem, metadata)` where `metadata` carries `key`,
    `tbs_der`, `signature`, `not_before`, and `not_after`.
    """
    cleaned = [h.strip() for h in hosts if h and h.strip()]
    if not cleaned:
        raise ValueError(
            "At least one address beyond loopback is required to generate a "
            "certificate."
        )
    if key is None:
        key = generate_rsa_keypair()

    now_utc = (
        now.astimezone(datetime.timezone.utc)
        if now is not None
        else datetime.datetime.now(datetime.timezone.utc)
    ).replace(microsecond=0)
    not_before = now_utc - datetime.timedelta(days=1)
    not_after = not_before + datetime.timedelta(days=VALIDITY_DAYS)

    serial = secrets.randbits(127) | (1 << 126)
    sig_alg = _der_sequence([_der_oid(_OID_SHA256_WITH_RSA), _der_null()])
    name = _der_name(cleaned[0])
    validity = _der_sequence([_der_time(not_before), _der_time(not_after)])

    rsa_pub_der = _der_sequence(
        [_der_integer(key["n"]), _der_integer(key["e"])]
    )
    spki = _der_sequence(
        [
            _der_sequence([_der_oid(_OID_RSA_ENCRYPTION), _der_null()]),
            _der_bit_string(rsa_pub_der),
        ]
    )

    basic_constraints = _der_extension(
        _OID_BASIC_CONSTRAINTS, _der_sequence([]), critical=True
    )
    # digitalSignature (bit 0 = 0x80) | keyEncipherment (bit 2 = 0x20) -> 0xA0,
    # 5 trailing unused bits.
    key_usage = _der_extension(
        _OID_KEY_USAGE, _der_bit_string(b"\xa0", unused_bits=5), critical=True
    )
    ext_key_usage = _der_extension(
        _OID_EXT_KEY_USAGE,
        _der_sequence([_der_oid(_OID_KP_SERVER_AUTH)]),
        critical=False,
    )
    san = _der_san_extension(cleaned)
    extensions = _der_tlv(
        0xA3,
        _der_sequence([basic_constraints, key_usage, ext_key_usage, san]),
    )

    tbs_der = _der_sequence(
        [
            _der_tlv(0xA0, _der_integer(2)),  # v3
            _der_integer(serial),
            sig_alg,
            name,
            validity,
            name,
            spki,
            extensions,
        ]
    )
    signature = _sign_pkcs1_v15_sha256(tbs_der, key)
    cert_der = _der_sequence(
        [tbs_der, sig_alg, _der_bit_string(signature)]
    )
    cert_pem = _pem_wrap("CERTIFICATE", cert_der)
    key_pem = encode_private_key_pem(key)
    return (
        cert_pem,
        key_pem,
        {
            "key": key,
            "tbs_der": tbs_der,
            "signature": signature,
            "not_before": not_before,
            "not_after": not_after,
            "serial": serial,
        },
    )


def cert_path(directory: Path | None = None) -> Path:
    """Path to `tls-cert.pem` in the bridge's folder."""
    base = directory if directory is not None else token_file.token_dir()
    return base / CERT_FILE_NAME


def key_path(directory: Path | None = None) -> Path:
    """Path to `tls-key.pem` in the bridge's folder."""
    base = directory if directory is not None else token_file.token_dir()
    return base / KEY_FILE_NAME


def _write_atomic(path: Path, content: str, user_only: bool) -> None:
    folder = path.parent
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if user_only:
        token_file._user_only(folder, is_dir=True)
    rand_suffix = secrets.token_hex(4)
    temporary = folder / f".{path.name}.{os.getpid()}.{rand_suffix}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    mode = 0o600 if user_only else 0o644
    fd = os.open(temporary, flags, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        if user_only:
            token_file._user_only(temporary, is_dir=False)
        token_file._replace_file(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def generate_and_store_certificate(
    hosts: list[str] | tuple[str, ...],
    directory: Path | None = None,
) -> tuple[Path, Path]:
    """Generate `tls-cert.pem` and `tls-key.pem` covering `hosts` in
    `directory` (`token_file.token_dir()` by default), with `tls-key.pem`
    readable by the user only."""
    cert_pem, key_pem, _ = generate_self_signed_cert(hosts)
    c_path = cert_path(directory)
    k_path = key_path(directory)
    _write_atomic(k_path, key_pem, user_only=True)
    _write_atomic(c_path, cert_pem, user_only=False)
    return c_path, k_path
