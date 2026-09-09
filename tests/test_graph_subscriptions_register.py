import clients.graph_subscriptions as gs


class C:
    def get_headers(self, immutable=False):
        return {"h": "1"}


def test_register_default_is_inbox(monkeypatch):
    seen = {}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "s"}

    monkeypatch.setattr(gs.requests, "post", lambda url, json, headers: seen.update(json) or R())
    monkeypatch.setenv("WEBHOOK_CLIENT_STATE", "inbox-webhook")
    gs.register(C(), "https://w")
    assert (
        seen["resource"] == "me/mailFolders/inbox/messages"
        and seen["clientState"] == "inbox-webhook"
    )


def test_register_sent(monkeypatch):
    seen = {}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "s"}

    monkeypatch.setattr(gs.requests, "post", lambda url, json, headers: seen.update(json) or R())
    gs.register(
        C(),
        "https://w",
        resource="me/mailFolders/sentitems/messages",
        client_state="inbox-webhook-sent",
    )
    assert (
        seen["resource"] == "me/mailFolders/sentitems/messages"
        and seen["clientState"] == "inbox-webhook-sent"
    )
