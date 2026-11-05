"""Checks for the OpenAI-style API other programs (Open WebUI) chat through (app/openai_api.py): the key, the model
list, answers whole and streamed with the library's lookups as reasoning, the Sources line the app rewrites, text
written before a lookup, the client's own requests (a chat's title), and no show_article.
No Ollama: a fake model (core._stream), a fake library and a stand-in for app/server.py.

Usage: .venv/bin/python tests/openai_api_test.py"""
import json
import os
import sys
import types
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "app")]
import core  # noqa: E402
import openai_api  # noqa: E402
from server import QuietServer  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILS.append(name)


# ---- OLLAMA_HOST set for sharing Ollama on the network ("0.0.0.0:11434") still connects ----
for value, want in [("0.0.0.0:11434", "http://127.0.0.1:11434"), ("0.0.0.0", "http://127.0.0.1:11434"),
                    ("127.0.0.1:11434", "http://127.0.0.1:11434"), ("http://192.168.1.5:11434/", "http://192.168.1.5:11434"),
                    ("[::]:11434", "http://127.0.0.1:11434"), ("::1", "http://[::1]:11434"), ("localhost", "http://localhost:11434")]:
    check(f"OLLAMA_HOST={value}", core._ollama_address(value) == want, core._ollama_address(value))
check("the app's window keeps show_article",
      "show_article" in core.system_prompt(types.SimpleNamespace(labels=lambda: ["Ready.gov"]), True))

# ---- what streams, and what waits for the end ----
long = ("Keep the fridge closed. " * 10).strip()  # past COMMIT_AT
a = openai_api.Answer()
out = a.add(long + "\n\nSour")
check("the start waits until it is long enough", openai_api.Answer().add("Short.") == "")
check("a line that may be the Sources line waits", out == long, repr(out))
rest = a.finish({"answer": long + "\n\nSources: Food Safety (Ready.gov)"})
check("the rewritten Sources line follows", rest == "\n\nSources: Food Safety (Ready.gov)", repr(rest))
check("'Some' at a line's start goes out", openai_api.settled("A.\nSome") == len("A.\nSome"))
check("'Sou' at a line's start waits", openai_api.settled("A.\nSou") == len("A."))
check("'**Sources:**' waits", openai_api.settled("A.\n**Sources:** x\n") == len("A."))
check("'. Sources:' at the end of a line waits", openai_api.settled("Done. Sources: X") == len("Done."))
check("'. S' at the end waits", openai_api.settled("Done. S") == len("Done."))
check("a passage id remark waits", openai_api.settled("Boil it (id 1403312) for a minute.") == len("Boil it"))
a = openai_api.Answer()
a.add(long)
check("text that went out before a lookup: nothing to give the reasoning", a.lookup() == "")
rest = a.finish({"answer": "Use bleach."})
check("then the whole corrected answer follows", rest == openai_api.CORRECTED + "Use bleach.", repr(rest))
a = openai_api.Answer()
a.add("Let me check the library.")
check("text before a lookup goes with the reasoning", a.lookup() == "Let me check the library.")
check("unfinished: says so, and what it read",
      "Articles it read: Water (CDC)" in openai_api.Answer().finish(
          {"answer": "", "unfinished": True, "sources": [{"title": "Water", "source": "CDC"}]}))

# ---- the server ----
rounds, calls = [], []


def fake_stream(model, messages, tools, think, options):
    calls.append({"messages": [dict(m) for m in messages], "tools": tools})
    for chunk in rounds.pop(0):
        yield chunk
    yield {"done": True, "eval_count": 7, "eval_duration": 1}


core._stream = fake_stream


class Lib:
    has_personal = False

    def labels(self):
        return ["Ready.gov"]

    def search(self, query, topic="any", limit=8):
        return [{"id": 1403312, "title": "Food Safety", "source": "Ready.gov", "official": True, "snippet": "4 hours"}]

    def read(self, pid, max_chars=5000):
        return {"title": "Food Safety", "source": "Ready.gov", "official": True, "url": "", "next_id": None,
                "text": "A closed fridge keeps food safe for about 4 hours."} if pid == 1403312 else None


app = types.SimpleNamespace(
    QuietServer=QuietServer, chat_models=lambda: ["nemotron-3.5-lightning:30b-a3b", "gemma4"],
    model_caps=lambda m: {"tools", "completion"}, api_library=Lib,
    api_prompt=lambda caps: core.system_prompt(Lib(), "tools" in caps, screen=False),
    friendly_error=lambda exc: f"Something went wrong: {exc}")
server = openai_api.serve(app, ("127.0.0.1", 0), "secret-key")
BASE = f"http://127.0.0.1:{server.server_address[1]}/v1"


def request(path, body=None, key="secret-key"):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


code, _ = request("/models", key="wrong")
check("a wrong key is refused", code == 401, code)
code, text = request("/models")
ids = [m["id"] for m in json.loads(text)["data"]] if code == 200 else []
check("models: the installed chat models", ids == ["nemotron-3.5-lightning:30b-a3b", "gemma4"], text)

lookup = {"message": {"content": "", "tool_calls": [
    {"function": {"name": "search_library", "arguments": {"query": "fridge blackout"}}}]}}
read = {"message": {"content": "", "tool_calls": [{"function": {"name": "read_article", "arguments": {"id": 1403312}}}]}}
rounds[:] = [[{"message": {"content": "Let me check."}}, lookup], [read],
             [{"message": {"content": "About 4 hours if you keep the door closed.\n\nSources: Mayo Clinic"}}]]
calls.clear()
code, text = request("/chat/completions", {"model": "gemma4:latest", "messages": [
    {"role": "system", "content": "Answer in Spanish."},
    {"role": "user", "content": [{"type": "text", "text": "How long is fridge food safe in a blackout?"}]}]})
reply = json.loads(text) if code == 200 else {}
msg = (reply.get("choices") or [{}])[0].get("message", {})
check("whole answer: 200", code == 200, text)
check("whole answer: the made-up source is replaced by what it read",
      msg.get("content") == "About 4 hours if you keep the door closed.\n\nSources: Food Safety (Ready.gov)", msg)
check("whole answer: lookups and the text before them are reasoning",
      "Let me check." in msg.get("reasoning_content", "") and 'Searched the library for "fridge blackout"'
      in msg.get("reasoning_content", "") and "Read Food Safety (Ready.gov)" in msg.get("reasoning_content", ""), msg)
tool_names = [t["function"]["name"] for t in calls[0]["tools"] or []] if calls else []
check("no show_article tool", tool_names == ["search_library", "read_article"], tool_names)
system = calls[0]["messages"][0]["content"] if calls else ""
check("the app's instructions, then the client's", system.startswith(core.BASE_PROMPT)
      and system.endswith("Instructions from the program the person is chatting in:\nAnswer in Spanish."), system[-200:])
check("no show_article in the instructions", "show_article" not in system)

rounds[:] = [[lookup], [read],
             [{"message": {"content": long}}, {"message": {"content": "\n\nSources: Food Safety (Ready.gov)"}}]]
code, text = request("/chat/completions", {"model": "gemma4", "stream": True,
                                           "messages": [{"role": "user", "content": "Fridge in a blackout?"}]})
events = [json.loads(line[6:]) for line in text.split("\n") if line.startswith("data: {")]
content = "".join(e["choices"][0]["delta"].get("content") or "" for e in events)
reasoning = "".join(e["choices"][0]["delta"].get("reasoning_content") or "" for e in events)
check("streamed: ends with [DONE]", text.rstrip().endswith("data: [DONE]"), text[-80:])
check("streamed: the answer adds up", content == long + "\n\nSources: Food Safety (Ready.gov)", repr(content))
check("streamed: lookups as reasoning", "Searched the library" in reasoning, reasoning)
check("streamed: finish_reason stop", events and events[-1]["choices"][0]["finish_reason"] == "stop", events[-1:])

rounds[:] = [[{"message": {"content": "Blackout fridge safety"}}]]
calls.clear()
code, text = request("/chat/completions", {"model": "gemma4", "messages": [
    {"role": "user", "content": "### Task:\nGenerate a concise title for this chat."}]})
msg = json.loads(text)["choices"][0]["message"] if code == 200 else {}
check("the client's own request: straight to the model", msg.get("content") == "Blackout fridge safety", text)
check("the client's own request: no tools or app instructions",
      calls and calls[0]["tools"] is None and calls[0]["messages"][0]["role"] == "user", calls)

code, text = request("/chat/completions", {"model": "llama9", "messages": [{"role": "user", "content": "Hi"}]})
check("a model that isn't installed: 404", code == 404, code)
code, text = request("/chat/completions", {"model": "gemma4", "messages": [{"role": "assistant", "content": "Hi"}]})
check("no question from the person: 400", code == 400, code)

server.shutdown()
print("all passed" if not FAILS else f"{len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
