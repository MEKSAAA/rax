"""Token accounting from Claude Code's own transcripts, in the Codex usage shape."""
import json
from pathlib import Path

KEYS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens', 'output_tokens', 'total_tokens')


class UsageReader:
    """Totals over the model responses in a Claude Code home, read incrementally (the dashboard
    polls growing transcripts). Each response counts once: Claude Code writes one line per
    content block, each with the response's usage so far, so its largest counts are kept."""

    def __init__(self):
        self.offsets, self.seen, self.totals = {}, {}, dict.fromkeys(KEYS, 0)

    def read(self, home):
        paths = sorted(Path(home, 'projects').rglob('*.jsonl'))
        if any(path.stat().st_size < self.offsets.get(path, 0) for path in paths):
            self.__init__()  # A transcript was rewritten (compaction keeps them append-only, but be safe).
        for path in paths:
            offset = self.offsets.get(path, 0)
            try:
                with path.open('rb') as stream:
                    stream.seek(offset)
                    for line in stream:
                        if not line.endswith(b'\n'):
                            break  # A concurrent writer has not finished this record.
                        offset += len(line)
                        self.add(line)
            except OSError:
                continue
            self.offsets[path] = offset
        totals = {**self.totals, 'total_tokens': self.totals['input_tokens'] + self.totals['output_tokens']}
        return {'token_usage': totals, 'total_session_usage': totals, 'model_calls': len(self.seen)}

    def add(self, line):
        boundary = b'"compact_boundary"' in line
        if not boundary and (b'"usage"' not in line or b'"assistant"' not in line):
            return
        try:
            row = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(row, dict):
            return
        if boundary:
            self.compaction(row)
            return
        message = row.get('message')
        if row.get('type') != 'assistant' or not isinstance(message, dict) or row.get('isApiErrorMessage'):
            return
        usage, key = message.get('usage'), message.get('id') or row.get('requestId')
        if not isinstance(usage, dict) or message.get('model') == '<synthetic>' or not key:
            return
        count = lambda name: usage.get(name) if isinstance(usage.get(name), int) else 0
        counts = {'input_tokens': count('input_tokens') + count('cache_read_input_tokens')
                  + count('cache_creation_input_tokens'),
                  'cached_input_tokens': count('cache_read_input_tokens'),
                  'cache_write_input_tokens': count('cache_creation_input_tokens'),
                  'output_tokens': count('output_tokens')}
        previous = self.seen.setdefault(key, dict.fromkeys(counts, 0))
        for name, value in counts.items():
            if value > previous[name]:
                self.totals[name] += value - previous[name]
                previous[name] = value


    def compaction(self, row):
        """A compaction's own model request, which the transcript records only as its boundary: it
        reads the conversation (preTokens, from the prompt cache) and writes the summary
        (postTokens). Measured against the relay's per-request log of the same session, these
        match the requests the transcript lacks within 2%."""
        metadata = row.get('compactMetadata')
        if row.get('subtype') != 'compact_boundary' or not isinstance(metadata, dict) or not row.get('uuid'):
            return
        if row['uuid'] in self.seen:
            return
        size = lambda name: metadata.get(name) if isinstance(metadata.get(name), int) else 0
        counts = {'input_tokens': size('preTokens'), 'cached_input_tokens': size('preTokens'),
                  'cache_write_input_tokens': 0, 'output_tokens': size('postTokens')}
        self.seen[row['uuid']] = counts
        for name, value in counts.items():
            self.totals[name] += value


def home_usage(home):
    """Every conversation in a Claude Code home, subagents included."""
    return UsageReader().read(home)
