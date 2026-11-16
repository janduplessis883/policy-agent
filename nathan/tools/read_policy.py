"""Read policy and profile bodies using Notion-flavored Markdown v2."""

import re
from urllib.parse import urlsplit

from nathan.domain import Document, SourceError

UNKNOWN = re.compile(r"<unknown\b[^>]*?/?>", re.IGNORECASE)
# Markdown links to attachments or child pages are not their underlying content.
UNREAD_CONTENT = re.compile(
    r"<(?:pdf|file|image|video|audio|embed|bookmark|page|database|custom-block)\b|!\[",
    re.IGNORECASE,
)


def read_policy(client, policy) -> Document:
    """Recover unloaded subtrees; never classify unread content as complete evidence."""
    active, cache = set(), {}
    requests = 0

    def canonical(block_id):
        return block_id.replace("-", "").casefold()

    def load(block_id, depth=0):
        nonlocal requests
        key = canonical(block_id)
        if key in active or depth > 30 or requests >= 200:
            return "[Content not read: recursive or oversized subtree]", False
        if key in cache:
            return cache[key]
        requests += 1
        active.add(key)
        try:
            data = client.markdown(block_id)
            text = data.get("markdown")
            unknown_ids = data.get("unknown_block_ids")
            truncated = data.get("truncated")
            if (
                not isinstance(text, str)
                or not isinstance(truncated, bool)
                or not isinstance(unknown_ids, list)
                or any(not isinstance(value, str) or not value for value in unknown_ids)
            ):
                raise SourceError("Notion returned incomplete Markdown response metadata.")
            recovered, complete = {}, True
            for unknown_id in dict.fromkeys(unknown_ids):
                try:
                    subtree, subtree_complete = load(unknown_id, depth + 1)
                except SourceError:
                    complete = False
                    continue
                recovered[canonical(unknown_id)] = (subtree, subtree_complete)
            replaced = set()

            def replace_unknown(match):
                nonlocal complete
                tag = match.group(0)
                url = re.search(r"""\burl=["']([^"']+)["']""", tag, re.IGNORECASE)
                if url:
                    parts = urlsplit(url.group(1))
                    reference = canonical(parts.fragment or parts.path.rsplit("/", 1)[-1])
                    if reference in recovered:
                        subtree, ok = recovered[reference]
                        replaced.add(reference)
                        complete &= ok
                        return subtree
                complete = False
                return tag

            text = UNKNOWN.sub(replace_unknown, text)
            # Truncation without recoverable IDs is still incomplete, even with no visible tag.
            if truncated and not unknown_ids:
                complete = False
            for reference, (subtree, ok) in recovered.items():
                if reference not in replaced:
                    text += "\n\n[Additional recovered subtree]\n" + subtree
                    complete = False  # Its original position could not be verified.
            if len(replaced) != len({canonical(value) for value in unknown_ids}):
                complete = False
            if UNKNOWN.search(text) or UNREAD_CONTENT.search(text) or "Transcript omitted." in text:
                complete = False
            if len(text) > 180_000:
                raise SourceError(f"{policy.title}: document is too large to read completely.")
            cache[key] = (text, complete)
            return text, complete
        finally:
            active.remove(key)

    text, complete = load(policy.id)
    return Document(policy.id, policy.title, text, complete, policy.public_url)
