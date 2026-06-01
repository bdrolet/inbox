import re

import psycopg
from sentence_transformers import SentenceTransformer

from models.message import Message
from repo import embeddings

# "On Mon, Jan 1 2024, Alice <alice@example.com> wrote:"
_REPLY_CHAIN_RE = re.compile(
    r"\n\s*On .+wrote:\s*$",
    re.DOTALL | re.MULTILINE,
)
_ORIGINAL_MSG_RE = re.compile(
    r"\n[-_]{4,}\s*Original Message\s*[-_]{4,}",
    re.IGNORECASE,
)
_SIGNATURE_RE = re.compile(
    r"\n--\s*\n.*$|Sent from my .+$|Get Outlook for .+$",
    re.DOTALL | re.IGNORECASE,
)


def strip_reply_chain(text: str) -> str:
    text = _ORIGINAL_MSG_RE.split(text)[0]
    m = _REPLY_CHAIN_RE.search(text)
    if m:
        text = text[: m.start()]
    return text.strip()


def strip_signature(text: str) -> str:
    m = _SIGNATURE_RE.search(text)
    if m:
        text = text[: m.start()]
    return text.strip()


def text_for_embedding(msg: Message) -> str:
    body = strip_reply_chain(msg.get("body") or "")
    body = strip_signature(body)
    return f"From: {msg['sender']}\nSubject: {msg['subject']}\n\n{body[:1500]}"


def embed_and_store(
    conn: psycopg.Connection,
    message_id: str,
    text: str,
    model: SentenceTransformer,
) -> list[float]:
    vec = model.encode(text, normalize_embeddings=True).tolist()
    embeddings.store(conn, message_id, vec)
    return vec
