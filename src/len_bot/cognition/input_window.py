"""Bounded raw-text views retain exact source positions, never summary claims."""
from len_bot.cognition.projection import estimate_tokens


def prefix_end(text, token_limit):
    if estimate_tokens(text) <= token_limit:
        return len(text)
    low, high = 1, len(text)
    while low < high:
        midpoint = (low + high + 1) // 2
        if estimate_tokens(text[:midpoint]) <= token_limit:
            low = midpoint
        else:
            high = midpoint - 1
    return low


def original_prefix(event, token_limit):
    text = event.raw_text
    low = prefix_end(text, token_limit)
    if low == len(text):
        return event
    view = event.model_copy(deep=True)
    view.payload['raw_text'] = text[:low]
    span = event.metadata.get('_text_range') or {'start': 0, 'end': len(text), 'total': len(text)}
    view.metadata['_text_range'] = {'start':span['start'], 'end':span['start']+low, 'total':span['total']}
    return view


def original_remainder(event, coverage):
    """Schedule the first uncovered range without granting previous-episode evidence."""
    if coverage is None or coverage.complete or not coverage.next_offset:
        return event
    if coverage.total != len(event.raw_text):
        raise ValueError('Observation coverage no longer matches the original')
    start = coverage.next_offset
    end = next((left for left, _ in coverage.ranges if left > start), coverage.total)
    view = event.model_copy(deep=True)
    view.payload['raw_text'] = event.raw_text[start:end]
    view.metadata['_text_range'] = {'start': start, 'end': end, 'total': coverage.total}
    return view
