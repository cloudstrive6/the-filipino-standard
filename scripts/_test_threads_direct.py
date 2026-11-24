"""Offline tests for the direct Threads path (no network, nothing posted). Run: py -I scripts/_test_threads_direct.py"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import threads_api  # noqa: E402
import refresh_threads_token as rt  # noqa: E402


class R:
    def __init__(self, d):
        self.d = d

    def json(self):
        return self.d


def test_post_text_flow():
    calls = []

    def req(method, url, timeout=None, data=None, params=None):
        p = data or params
        calls.append((method, url.split("v1.0/")[1], {k: v for k, v in p.items() if k != "access_token"}))
        assert p["access_token"] == "tok"
        if url.endswith("/me/threads"):
            return R({"id": "c1"})
        if url.endswith("/c1") and method == "GET":
            return R({"status": "FINISHED"})
        if url.endswith("/me/threads_publish"):
            return R({"id": "m1"})
        if url.endswith("/m1"):
            return R({"permalink": "https://www.threads.com/@thefilipinostandard/post/x"})
        raise AssertionError(url)

    os.environ["THREADS_ACCESS_TOKEN"] = "tok"
    threads_api.requests.request = req
    out = threads_api.post_text("Isang test.", sleep=lambda s: None)
    assert out == {"post_id": "m1", "post_url": "https://www.threads.com/@thefilipinostandard/post/x"}
    assert calls[0] == ("POST", "me/threads", {"media_type": "TEXT", "text": "Isang test."})
    assert calls[2] == ("POST", "me/threads_publish", {"creation_id": "c1"})


def test_too_long_rejected():
    os.environ["THREADS_ACCESS_TOKEN"] = "tok"
    try:
        threads_api.post_text("x" * 501)
    except ValueError:
        return
    raise AssertionError("501 chars should be rejected")


def test_refresh_due():
    today = dt.date(2026, 11, 24)
    assert rt.due("", today) and rt.due("garbage", today) and rt.due("2026-11-18", today)
    assert not rt.due("2026-11-20", today)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
