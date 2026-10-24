"""Claude Code's own transcripts give each model request's tokens once, compactions included."""
import json

from services.claude_usage import UsageReader, home_usage


def assistant(ident, output, *, cached=100, written=10, model='claude-opus-5-5', **extra):
    return {'type': 'assistant', 'requestId': 'r-' + ident, **extra,
            'message': {'id': ident, 'model': model, 'usage': {
                'input_tokens': 2, 'cache_read_input_tokens': cached, 'cache_creation_input_tokens': written,
                'output_tokens': output}}}


def boundary(uuid, pre, post):
    return {'type': 'system', 'subtype': 'compact_boundary', 'uuid': uuid,
            'compactMetadata': {'trigger': 'auto', 'preTokens': pre, 'postTokens': post}}


def write(path, rows, tail=''):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows) + tail)


def test_each_request_counts_once_at_its_largest_usage(tmp_path):
    # Claude Code writes one line per content block, each with the response's usage so far.
    write(tmp_path/'projects/-ws/s1.jsonl', [
        {'type': 'user', 'message': {'content': 'go'}},
        assistant('m1', 5), assistant('m1', 40),  # Thinking, then text, of one response.
        assistant('m2', 7, cached=112, written=3),
        assistant('m3', 9, model='<synthetic>'),  # Written by Claude Code, not the model.
        assistant('m4', 9, isApiErrorMessage=True)])
    usage = home_usage(tmp_path)
    assert usage['model_calls'] == 2
    assert usage['token_usage'] == {'input_tokens': 112 + 117, 'cached_input_tokens': 212,
                                    'cache_write_input_tokens': 13, 'output_tokens': 47,
                                    'total_tokens': 229 + 47}


def test_compactions_count_as_requests_and_subagents_are_included(tmp_path):
    # A compaction's request is in the transcript only as its boundary; a subagent has its own file.
    write(tmp_path/'projects/-ws/s1.jsonl', [assistant('m1', 40), boundary('b1', 200000, 9000)])
    write(tmp_path/'projects/-ws/s1/subagents/agent-a.jsonl', [assistant('m2', 5), boundary('b2', 1000, 50)])
    usage = home_usage(tmp_path)
    assert usage['model_calls'] == 4
    totals = usage['token_usage']
    assert totals['input_tokens'] == 112 * 2 + 201000 and totals['cached_input_tokens'] == 200 + 201000
    assert totals['output_tokens'] == 45 + 9050 and totals['cache_write_input_tokens'] == 20


def test_growing_transcripts_are_read_incrementally(tmp_path):
    path = tmp_path/'projects/-ws/s1.jsonl'
    partial = json.dumps(assistant('m2', 8))
    write(path, [assistant('m1', 4)], tail=partial[:20])  # A writer is mid-line.
    reader = UsageReader()
    assert reader.read(tmp_path)['model_calls'] == 1
    with path.open('a') as stream:
        stream.write(partial[20:] + '\n' + json.dumps(boundary('b1', 300, 30)) + '\n'
                     + json.dumps(boundary('b1', 300, 30)) + '\n')  # A boundary read twice counts once.
    usage = reader.read(tmp_path)
    assert usage['model_calls'] == 3 and usage['token_usage']['output_tokens'] == 4 + 8 + 30
    assert reader.read(tmp_path) == usage
