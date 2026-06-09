import clients.claude as claude
from models.message import Message

_SIGNATURE = (
    "\n\nBest,\n"
    "Ben Drolet\n"
    "AI Infrastructure Engineering for Healthcare\n"
    "drolet.ai · linkedin.com/in/benjamindrolet"
)


def generate(msg: Message) -> str:
    """Return a plain-text draft reply to the given email."""
    prompt = (
        "Draft a concise, professional reply to the following email.\n"
        "Write only the reply body — no subject line, no 'Dear X' opener unless natural.\n"
        f"End with this exact sign-off:{_SIGNATURE}\n\n"
        f"From: {msg.get('sender_display') or msg['sender']} <{msg['sender']}>\n"
        f"Subject: {msg['subject']}\n\n"
        f"{(msg['body'] or '')[:2000]}"
    )
    return claude.draft(prompt)
