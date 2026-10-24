"""Complete public errors: the full message, with credentials, URLs and host directories removed."""
import json
import re

MAX_REASON = 8000


def redact(reason):
    """Remove URLs, host directories and credentials; keep file names and the message."""
    reason = re.sub(r'https?://\S+', '[redacted URL]', reason)
    # Keep file names (tracebacks name the failing module) but not the host's directories.
    reason = re.sub(r'(?<![\w.])/(?:[^\s,;\]\)\"\'/]+/)+', '', reason)
    reason = re.sub(r'(?i)(bearer\s+|(?:api[_-]?key|authorization|token)\s*[=:]\s*)[^\s,;]+', r'\1[redacted]', reason)
    reason = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[redacted key]', reason)
    if len(reason) > MAX_REASON:  # Keep the start and the innermost traceback frames.
        reason = reason[:MAX_REASON // 4] + '\n...\n' + reason[-(MAX_REASON * 3 // 4):]
    return reason


def describe(exc):
    """One error as a line for a record: its type and its whole (redacted) message."""
    text = redact(str(exc))
    return f'{type(exc).__name__}: {text}' if text else type(exc).__name__


def provider_body(exc):
    """An HTTP error's response body, read once and kept on the exception."""
    if not hasattr(exc, 'provider_body'):
        body = b''
        try:
            if getattr(exc, 'fp', None) is not None:
                body = exc.read(65536) or b''
        except Exception:
            pass
        exc.provider_body = body
    return exc.provider_body


def public_failure(exc, *, operation=None, stage='request'):
    """The whole error the agent sees. Never replace it with a summary or a generic label."""
    if stage == 'provider_request':
        # The provider's own messages and bodies can carry arbitrary request data: the agent gets
        # the status and the billing outcome, the operator's provider log keeps the whole answer.
        status = getattr(exc, 'code', None)
        if isinstance(status, int):
            phrase = redact(str(getattr(exc, 'reason', '') or ''))
            reason = (f'Provider returned HTTP {status}' + (f' ({phrase})' if phrase else '')
                      + '; it produced no completion'
                      + (', so its billing reservation was released.' if getattr(exc, 'reservation_released', False)
                         else '; its billing reservation is retained.'))
        elif isinstance(exc, OSError):
            # Timeout or lost connection after sending: the provider may still have billed the call.
            reason = (f'Provider request failed ({type(exc).__name__}); the provider may have billed it, '
                      'so its billing reservation is retained.')
        else:  # The provider's answer failed our own checks (model, usage, choices).
            reason = redact(str(exc) or type(exc).__name__)
    else:
        reason = redact(str(exc) or type(exc).__name__)
    failure = {'type': type(exc).__name__, 'operation': operation, 'stage': stage, 'reason': reason}
    state = getattr(exc, 'state', None)
    if state:
        failure['simulator_state'] = state
    return failure


def provider_record(exc, **fields):
    """Operator-only record of a failed provider call: the exception and the raw response body."""
    import time
    return {'time': time.time(), **fields, 'type': type(exc).__name__, 'status': getattr(exc, 'code', None),
            'message': str(exc), 'body': provider_body(exc).decode('utf-8', 'replace'),
            'notes': getattr(exc, '__notes__', [])}
