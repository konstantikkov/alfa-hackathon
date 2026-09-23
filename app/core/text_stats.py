from __future__ import annotations


def approx_token_count(text: str) -> int:
    """Cheap estimate (~chars/4) -- good enough for metrics and logging. Not a
    substitute for a real tokenizer; see tools/dataset_generator for the
    tiktoken-based option used when generating benchmark fixtures.

    Deliberately O(1): the previous words-based blend called text.split() on
    every request, allocating a list of every word of an up-to-400KB payload
    for a log field -- and for Russian text the max() picked the chars/4 branch
    anyway (avg word+space > 5.2 chars makes words*1.3 < len/4).
    """
    if not text:
        return 0
    return max(len(text) // 4, 1)
