"""An OpenAI-style API, so chat programs on other computers (Open WebUI, say) can use Survival LLM: the same model,
library search, instructions and Sources check as the app's own window, from a computer with a graphics card.

Started by app/server.py --api (README "Use it from other computers"); off unless asked for, since the app itself
listens on 127.0.0.1 only. Every request needs the key from api-key.txt, with the app's settings, sent as
"Authorization: Bearer KEY". Left out: the AI's memory notes about the person, which anyone with the key could read
or add to, and the person's own files unless the server was started with --api-my-files.
"""
import base64
import json
import re
import secrets
import select
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

import core
from core import ID_REMARK, chat_events, trim

# OpenAI's reasoning_effort, as the app's Reasoning setting (no reasoning_effort: Off, the app's own default).
REASONING = {"none": "off", "minimal": "off", "low": "short", "medium": "medium", "high": "long"}
TASK = "### Task:"  # how Open WebUI starts requests of its own: a chat's title, its tags, follow-up questions
TASK_LIMIT = 1024  # tokens: those are short
MAX_IMAGES = 6  # pictures kept in a request, the most recent
DATA_URL = re.compile(r"^data:image/[\w.+-]+;base64,([A-Za-z0-9+/=\s]+)$")
# Said to the model when the person attached a picture. A photo can't diagnose anything or show that a wild plant,
# mushroom or berry is safe to eat, and a small model reads photos far less well than text.
IMAGE_NOTE = ("The person attached a photo. Say briefly what you can see and how sure you are, then search the library "
              "for what it shows. A photo can't give a diagnosis, and can't show that a plant, mushroom or berry is "
              "safe to eat: say so, and never tell them it is safe.")
MAX_BODY = 32 * 1024 ** 2  # a long chat, with pictures the model can't see
CORRECTED = "\n\n---\n\nCorrected answer:\n\n"
# Text before a lookup isn't the answer ("Let me check the library."), and the client can't take text back, so the
# start of each answer waits until it is this long or the model finishes; text that turns out to come before a
# lookup goes with its reasoning instead.
COMMIT_AT = 200
# "...the door closed. Sources: ..." at the very end, which the app moves to a line of its own.
INLINE_START = re.compile(r"(?<=[.!?])[ \t]+(?:\*\*)?(?:Sources?(?:\*\*)?:|(?:S|So|Sou|Sour|Sourc|Source|Sources)?\Z)")


def content_text(content):
    """A message's text: OpenAI clients send a string, or a list of parts of which only the text parts count."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text") or "" for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def content_images(content):
    """(the base64 pictures in a message's parts, how many it couldn't use). Only pictures sent inside the request
    (data: URLs, which is how Open WebUI sends what the person attaches): a web address is never fetched."""
    found, skipped = [], 0
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict) or part.get("type") != "image_url":
            continue
        url = part.get("image_url")
        url = url.get("url") if isinstance(url, dict) else url
        match = DATA_URL.match(url) if isinstance(url, str) else None
        try:
            if not match:
                raise ValueError("not a data URL")
            base64.b64decode(match.group(1), validate=True)
            found.append(match.group(1))
        except ValueError:  # binascii.Error is one
            skipped += 1
    return found, skipped


def parse_messages(raw):
    """OpenAI chat messages as (the client's system messages, Ollama user and assistant messages). A message with
    pictures carries them as "images"; one it couldn't load says so in its text, so the model doesn't answer as if it
    had seen it."""
    system, chat = [], []
    for m in raw if isinstance(raw, list) else []:
        if not isinstance(m, dict):
            continue
        text = content_text(m.get("content")).strip()
        images, skipped = content_images(m.get("content"))
        if skipped:
            text = f"{text}\n\n[{skipped} attached image(s) could not be loaded.]".strip()
        if m.get("role") in ("system", "developer") and text:
            system.append(text)
        elif m.get("role") in ("user", "assistant") and (text or images):
            chat.append({"role": m["role"], "content": text, **({"images": images} if images else {})})
    return system, chat


def limit_images(messages, can_see):
    """Keep the pictures of the most recent MAX_IMAGES, as each takes room in the model's context, and none when the
    model can't see (an earlier picture in a chat that moved to another model). What is left out is said in the
    message's text. Returns how many pictures are kept."""
    kept = 0
    for m in reversed(messages):
        images = m.pop("images", None) or []
        room = MAX_IMAGES - kept if can_see else 0
        if images[:room]:
            m["images"] = images[:room]
            kept += len(m["images"])
        if len(images) > room:
            m["content"] = f"{m['content']}\n\n[{len(images) - room} attached image(s) not shown to you.]".strip()
    return kept


def settled(text):
    """How much of the answer so far can't change any more: chat_events' done event rewrites its Sources line and
    takes out passage ids, so text from where either may start waits for the end, and so does whitespace at the end
    (taken off before the Sources line)."""
    pos = 0
    for line in text.splitlines(keepends=True):
        head = line.lstrip(" \t*_#").lower()
        if head.startswith("source") or (not line.endswith("\n") and "source".startswith(head)):
            break
        pos += len(line)
    for pattern in (INLINE_START, ID_REMARK):
        found = pattern.search(text, 0, pos)
        if found:
            pos = found.start()
    return len(text[:pos].rstrip())


class Answer:
    """The answer as a client sees it: streamed as the model writes it, less what may still change (settled(),
    COMMIT_AT). When the app changes what already went out (it asked again, or the model looked something up after
    all), the client gets the whole corrected answer after it, as the protocol has no way to take text back."""

    def __init__(self):
        self.text, self.sent, self.wrong = "", 0, False

    def add(self, text):
        self.text = self.text + text if self.sent else (self.text + text).lstrip("\n")
        if self.wrong or (not self.sent and len(self.text) < COMMIT_AT):
            return ""
        ready = max(self.sent, settled(self.text))
        out, self.sent = self.text[self.sent:ready], ready
        return out

    def lookup(self):
        """It looks something up, or is asked again: what it wrote so far isn't the answer. Returns that text when
        none of it went out yet (it goes with the reasoning)."""
        if self.sent:
            self.wrong = True
            return ""
        before, self.text = self.text, ""
        return before

    def finish(self, done):
        """The rest of the answer, from chat_events' done event."""
        final = done.get("answer") or ""
        if done.get("unfinished") and not final.strip():
            read = "; ".join(f"{s['title']} ({s['source']})" for s in done.get("sources") or [])
            final = "It didn't finish an answer. Try asking again." + (f"\n\nArticles it read: {read}" if read else "")
        sent = self.text[:self.sent]
        if not self.wrong and final.startswith(sent):
            return final[len(sent):]
        return (CORRECTED if sent else "") + final


def pieces(events):
    """chat_events' events as what an OpenAI client shows: ("reasoning", text) for its reasoning and lookups,
    ("content", text) for the answer, then ("finish", {"reason", "tokens"}); ("tick", "") when there is nothing to send."""
    answer = Answer()
    for ev in events:
        kind = ev["type"]
        if kind == "thinking":
            yield "reasoning", ev["text"]
        elif kind in ("search", "read", "retry"):
            before = answer.lookup().strip()
            if before and kind != "retry":  # a retry's text was an answer it is asked to redo
                yield "reasoning", f"\n\n{before}"
            if kind == "search":
                found = len(ev.get("results") or [])
                yield "reasoning", f'\n\nSearched the library for "{ev["query"]}": {found} results.\n\n'
            elif kind == "read":
                yield "reasoning", f"\n\nRead {ev['title']} ({ev['source']}).\n\n"
            else:
                yield "tick", ""
        elif kind == "think_limit":
            yield "reasoning", "\n\nReached the reasoning limit, so it answers from what it has.\n\n"
        elif kind == "token":
            text = answer.add(ev["text"])
            yield ("content", text) if text else ("tick", "")
        elif kind == "done":
            rest = answer.finish(ev)
            if rest:
                yield "content", rest
            yield "finish", {"reason": "length" if ev.get("cut_off") else "stop", "tokens": ev.get("tokens") or 0}
            return
        else:
            yield "tick", ""


def plain(model, caps, messages):
    """A request of the chat program's own (a chat's title, say): straight to the model, without the library or the
    app's instructions, as chat_events' events."""
    text, final = [], {}
    # The answers' num_ctx, so Ollama doesn't load the model again for it. A title or a few tags need no reasoning:
    # Qwen reasoned for over ten minutes about a 3-word title on a computer without a graphics card.
    options = {"num_ctx": 32768, "num_predict": TASK_LIMIT, "temperature": 0.3}
    for chunk in core._stream(model, messages, None, False if "thinking" in caps else None, options):
        msg = chunk.get("message", {})
        if msg.get("thinking"):
            yield {"type": "thinking", "text": msg["thinking"]}
        if msg.get("content"):
            text.append(msg["content"])
            yield {"type": "token", "text": msg["content"]}
        if chunk.get("done"):
            final = chunk
    yield {"type": "done", "answer": "".join(text), "cut_off": final.get("done_reason") == "length",
           "tokens": final.get("eval_count", 0)}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    app = key = None  # set by serve(): app/server.py, and the key requests must carry

    def log_message(self, *args):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, code, message, kind="invalid_request_error"):
        return self._send(code, {"error": {"message": message, "type": kind}})

    def _path(self):
        """The path without its /v1, so clients set up with either base URL work."""
        path = urlparse(self.path).path.rstrip("/")
        return path[3:] if path.startswith("/v1/") else path

    def _authorized(self):
        got = self.headers.get("Authorization", "")
        return got[:7].lower() == "bearer " and secrets.compare_digest(got[7:].strip().encode(), self.key.encode())

    def do_GET(self):
        if not self._authorized():
            return self._error(401, "A valid API key is required (Authorization: Bearer KEY).", "authentication_error")
        if self._path() == "/models":
            return self._send(200, {"object": "list", "data": [
                {"id": m, "object": "model", "created": 0, "owned_by": "survival-llm"} for m in self.app.chat_models()]})
        return self._error(404, "Not found.")

    def do_POST(self):
        if not self._authorized():
            self.close_connection = True  # its body stays unread
            return self._error(401, "A valid API key is required (Authorization: Bearer KEY).", "authentication_error")
        if self._path() != "/chat/completions":
            self.close_connection = True
            return self._error(404, "Not found.")
        size = int(self.headers.get("Content-Length") or 0)
        if size > MAX_BODY:
            self.close_connection = True
            return self._error(413, "This request is too big.")
        try:
            body = json.loads(self.rfile.read(size) or b"{}")
        except ValueError:
            return self._error(400, "The request isn't valid JSON.")
        if not isinstance(body, dict):
            return self._error(400, "The request isn't valid JSON.")
        return self._completion(body)

    def _completion(self, body):
        system, messages = parse_messages(body.get("messages"))
        if not messages or messages[-1]["role"] != "user":
            return self._error(400, "messages must end with the person's message.")
        model = str(body.get("model") or "").removesuffix(":latest")
        if model not in self.app.chat_models():
            return self._error(404, f"No model called {body.get('model')!r} is installed on this computer.")
        caps = self.app.model_caps(model)
        if messages[-1].get("images") and "vision" not in caps:
            seeing = [m for m in self.app.chat_models() if "vision" in self.app.model_caps(m)]
            return self._error(400, f"{model} can't read images. Pick a model that can"
                                    + (f": {', '.join(seeing)}." if seeing else " (none installed on this computer can)."))
        seen = limit_images(messages, "vision" in caps)
        if messages[-1]["content"].startswith(TASK):
            events = plain(model, caps, [{"role": "system", "content": s} for s in system] + messages)
        else:
            prompt = self.app.api_prompt(caps) + (f"\n\n{IMAGE_NOTE}" if seen else "") + "".join(
                f"\n\nInstructions from the program the person is chatting in:\n{s}" for s in system)
            messages = [{"role": "system", "content": prompt}] + messages
            trim(messages)
            reasoning = REASONING.get(str(body.get("reasoning_effort") or "").lower(), "off")
            events = chat_events(model, self.app.api_library(), messages, reasoning=reasoning, caps=caps, screen=False)
        reply = {"id": "chatcmpl-" + uuid.uuid4().hex, "created": int(time.time()), "model": model}
        try:
            if body.get("stream"):
                return self._stream(pieces(events), reply)
            return self._whole(pieces(events), reply)
        finally:
            events.close()  # closes the request to Ollama, which stops generating

    def _gone(self):
        """Whether the client closed the connection (it gave up waiting): then the model can stop."""
        try:
            readable = select.select([self.connection], [], [], 0)[0]
            return bool(readable) and not self.connection.recv(1, socket.MSG_PEEK)
        except OSError:
            return True

    def _whole(self, parts, reply):
        out, finish, checked = {"reasoning": [], "content": []}, {}, time.monotonic()
        try:
            for kind, value in parts:
                if kind == "finish":
                    finish = value
                elif kind != "tick":
                    out[kind].append(value)
                if time.monotonic() - checked > 1:
                    checked = time.monotonic()
                    if self._gone():
                        self.close_connection = True
                        return None
        except Exception as exc:  # noqa: BLE001 - Ollama stopped, ran out of memory, ...
            return self._error(502, self.app.friendly_error(exc), "server_error")
        message = {"role": "assistant", "content": "".join(out["content"])}
        if out["reasoning"]:
            message["reasoning_content"] = "".join(out["reasoning"])
        tokens = finish.get("tokens", 0)
        return self._send(200, {**reply, "object": "chat.completion", "choices": [
            {"index": 0, "message": message, "finish_reason": finish.get("reason", "stop")}],
            "usage": {"prompt_tokens": 0, "completion_tokens": tokens, "total_tokens": tokens}})

    def _stream(self, parts, reply):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def chunk(delta, finish=None):
            data = {**reply, "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(b"data: " + json.dumps(data).encode() + b"\n\n")
            self.wfile.flush()

        last_tick = time.monotonic()
        try:
            chunk({"role": "assistant", "content": ""})
            while True:
                try:
                    kind, value = next(parts)
                except StopIteration:
                    break
                except Exception as exc:  # noqa: BLE001 - Ollama stopped, ...: the answer ends with why
                    chunk({"content": f"\n\n{self.app.friendly_error(exc)}"}, "stop")
                    break
                if kind == "finish":
                    chunk({}, value["reason"])
                elif kind == "tick":  # writing something is how a closed connection gets noticed
                    if time.monotonic() - last_tick > 1:
                        last_tick = time.monotonic()
                        self.wfile.write(b": tick\n\n")
                        self.wfile.flush()
                else:
                    chunk({"reasoning_content" if kind == "reasoning" else "content": value})
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except OSError:  # BrokenPipeError, ConnectionResetError, ...: the client stopped or went away
            pass


def serve(app, address, key):
    """Answer API requests on address (host, port) in a background thread; returns the server. app: app/server.py."""
    handler = type("ApiHandler", (Handler,), {"app": app, "key": key})
    server = app.QuietServer(address, handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
