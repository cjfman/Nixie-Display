"""RSA signing/verification for the raspi_run one-shot ``runonce.sh`` hook.

Scripts are signed off-device with an RSA private key; the Pi verifies the
signature against trusted public keys before running. Verification shells out to
``openssl`` (already present on the Pi) — no extra Python dependency.

Rollout is staged by *key presence*: while the keys dir is empty the runner is
**permissive** (unsigned/headerless scripts still run); once any public key is
installed it is **strict** (only validly signed scripts run). A cryptographically
*invalid* signature is always a hard skip.

Header block (shell comments, written just after the shebang)::

    # nixie-runonce-version: 1
    # nixie-runonce-signer: charles-laptop   (informational only)
    # nixie-runonce-sig: <base64 RSA-SHA256 signature>

What is signed (``_canonical_text``): the file with every ``# nixie-runonce-``
header line removed, normalized to ``\\n`` line endings with a trailing newline.
Signer and verifier reduce a file to that identical form before hashing, so
header lines never affect the signature and line-ending differences can't break
verification.
"""

import base64
import glob
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

HEADER_VERSION = '1'
DEFAULT_KEYS_DIR = '~/.nixie/runonce_keys'

## A nixie-runonce header line (stripped before hashing). Tolerates leading
## whitespace and an optional space after '#'.
_HEADER_LINE_RE = re.compile(rb'^\s*#\s*nixie-runonce-')
## Parse a header field: name (lowercase word) and value.
_HEADER_FIELD_RE = re.compile(r'^\s*#\s*nixie-runonce-([a-z]+):\s*(.*)$')


@dataclass
class VerifyResult:
    status: str                      # ok | unsigned | unverifiable | invalid | error
    key_name: Optional[str] = None   # filename stem of the key that validated (status=ok)
    message: str = ''


def _canonical_text(text) -> str:
    """Reduce a script to the exact text that is signed/verified: every
    ``# nixie-runonce-`` header line dropped, ``\\n`` endings, trailing newline."""
    lines = [ln for ln in text.splitlines()
             if not _HEADER_LINE_RE.match(ln.encode('utf-8', 'replace'))]
    return '\n'.join(lines) + '\n'


def canonical_body(path) -> bytes:
    """The signed bytes for the script at ``path`` (see ``_canonical_text``)."""
    with open(path, 'r', errors='replace') as f:
        return _canonical_text(f.read()).encode('utf-8')


def parse_headers(text) -> Dict[str, str]:
    """Return the nixie-runonce header fields present in ``text``."""
    headers = {}
    for line in text.splitlines():
        match = _HEADER_FIELD_RE.match(line)
        if match:
            headers[match.group(1)] = match.group(2).strip()
    return headers


def list_public_keys(keys_dir) -> List[Tuple[str, str]]:
    """Trusted public keys as ``(name, path)``; name is the filename stem."""
    base = os.path.expanduser(keys_dir)
    keys = []
    for pattern in ('*.pem', '*.pub'):
        for path in sorted(glob.glob(os.path.join(base, pattern))):
            keys.append((os.path.splitext(os.path.basename(path))[0], path))
    return keys


def _openssl_sign(body, priv_pem) -> str:
    """RSA-SHA256 sign ``body`` (bytes) with a private key PEM; base64 result."""
    proc = subprocess.run(
        ['openssl', 'dgst', '-sha256', '-sign', priv_pem],
        input=body, capture_output=True, check=True,
    )
    return base64.b64encode(proc.stdout).decode('ascii')


def _openssl_verify(body, sig_b64, pub_pem) -> bool:
    """True if ``sig_b64`` is a valid RSA-SHA256 signature of ``body`` for the
    public key PEM at ``pub_pem``. Raises FileNotFoundError if openssl is absent."""
    try:
        sig = base64.b64decode(sig_b64, validate=True)
    except Exception:
        return False
    tmp = tempfile.NamedTemporaryFile(delete=False)
    try:
        tmp.write(sig)
        tmp.close()
        proc = subprocess.run(
            ['openssl', 'dgst', '-sha256', '-verify', pub_pem, '-signature', tmp.name],
            input=body, capture_output=True, check=False,
        )
        return proc.returncode == 0
    finally:
        os.unlink(tmp.name)


def verify(path, keys_dir) -> VerifyResult:
    """Classify the signature state of the script at ``path`` against the trusted
    keys in ``keys_dir``. Does not decide whether to run — see ``decide``."""
    try:
        with open(path, 'r', errors='replace') as f:
            text = f.read()
    except OSError as e:
        return VerifyResult('error', message=str(e))

    sig = parse_headers(text).get('sig')
    if not sig or sig.lower() == 'none':
        return VerifyResult('unsigned')

    keys = list_public_keys(keys_dir)
    if not keys:
        return VerifyResult('unverifiable')

    body = _canonical_text(text).encode('utf-8')
    try:
        for name, key_path in keys:
            if _openssl_verify(body, sig, key_path):
                return VerifyResult('ok', key_name=name)
    except FileNotFoundError:
        return VerifyResult('error', message='openssl not found')
    return VerifyResult('invalid')


def decide(result, enforce) -> Tuple[bool, str]:
    """Map a VerifyResult to (run?, single log line). ``enforce`` is True when any
    trusted public key is installed. An invalid signature is always a hard skip."""
    status = result.status
    if status == 'ok':
        return True, f"runonce-verify: OK - signed by '{result.key_name}' - running"
    if status == 'invalid':
        return False, "runonce-verify: INVALID signature (no trusted key matched) - NOT running"
    if status == 'unverifiable':
        return True, "runonce-verify: signed but no public keys installed - running (permissive)"
    if status == 'unsigned':
        if enforce:
            return False, "runonce-verify: unsigned script - NOT running (enforced; public keys installed)"
        return True, "runonce-verify: unsigned script - running (permissive)"
    if status == 'error':
        if enforce:
            return False, f"runonce-verify: verify error ({result.message}) - NOT running"
        return True, f"runonce-verify: verify error ({result.message}) - running (permissive)"
    return False, f"runonce-verify: unknown status '{status}' - NOT running"


def _insert_headers(canonical, signer_name, sig_b64) -> str:
    """Insert the header block into canonical text (after the shebang if any)."""
    lines = canonical.split('\n')
    if lines and lines[-1] == '':
        lines = lines[:-1]   ## trailing newline -> empty final element; re-added below
    header = [
        f"# nixie-runonce-version: {HEADER_VERSION}",
        f"# nixie-runonce-signer: {signer_name}",
        f"# nixie-runonce-sig: {sig_b64}",
    ]
    if lines and lines[0].startswith('#!'):
        out = [lines[0]] + header + lines[1:]
    else:
        out = header + lines
    return '\n'.join(out) + '\n'


def sign(path, priv_pem, signer_name, out_path=None):
    """Sign the script at ``path`` and write it back (or to ``out_path``).

    Strips any existing nixie-runonce headers, signs the canonical body, then
    writes the shebang, a fresh header block, and the body."""
    with open(path, 'r', errors='replace') as f:
        canonical = _canonical_text(f.read())
    sig_b64 = _openssl_sign(canonical.encode('utf-8'), priv_pem)
    final = _insert_headers(canonical, signer_name, sig_b64)
    with open(out_path or path, 'w') as f:
        f.write(final)
