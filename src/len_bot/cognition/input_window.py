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
    view.metadata['_text_range'] = {'start':0, 'end':low, 'total':len(text)}
    return view
