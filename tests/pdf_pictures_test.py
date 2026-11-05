"""A PDF with pages that are only pictures (scans, photos) is read, and the person is told which pages weren't: the
rest of the PDF is searched, and without a note they would not know pages are missing. Blank pages don't count, even
when the PDF shares one list of pictures between all its pages. Needs pypdf (no Ollama, no library).

Usage: .venv/bin/python tests/pdf_pictures_test.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from library import myfiles  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILS.append(name)


TEXT = "Run the generator outdoors, at least 20 feet from any window, door or vent, and never in a garage."


def make_pdf(pages):
    """A PDF whose pages are "text", "picture" or "blank". Every page shares one resource list that holds the picture,
    as many real PDFs do."""
    objs = {1: b"<< /Type /Catalog /Pages 2 0 R >>",
            3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            4: b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 "
               b"/Length 1 >>\nstream\n\x80\nendstream"}
    kids = []
    for i, kind in enumerate(pages):
        page, content = 5 + 2 * i, 6 + 2 * i
        stream = {"text": f"BT /F1 12 Tf 72 720 Td ({TEXT}) Tj ET".encode(),
                  "picture": b"q 400 0 0 400 100 200 cm /Im1 Do Q", "blank": b""}[kind]
        objs[page] = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R /Resources << /Font "
                      b"<< /F1 3 0 R >> /XObject << /Im1 4 0 R >> >> >>" % content)
        objs[content] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
        kids.append(b"%d 0 R" % page)
    objs[2] = b"<< /Type /Pages /Count %d /Kids [%s] >>" % (len(pages), b" ".join(kids))
    out, offsets = bytearray(b"%PDF-1.4\n"), {}
    for number in sorted(objs):
        offsets[number] = len(out)
        out += b"%d 0 obj\n" % number + objs[number] + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (max(objs) + 1)
    for number in range(1, max(objs) + 1):
        out += b"%010d 00000 n \n" % offsets[number]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (max(objs) + 1, xref)
    return bytes(out)


folder = tempfile.mkdtemp()


def write(name, pages):
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(make_pdf(pages))
    return path


def extract(pages, name="t.pdf"):
    return myfiles.extract(write(name, pages))


title, text, note = extract(["text", "picture", "text"])
check("text pages are read", "generator outdoors" in text, text)
check("one picture page: named, with how many pages there are",
      note == "Page 2 of 3 is only a picture (a scan or a photo), so the AI can't read it. It searches the rest.", note)
check("a blank page is not a picture", extract(["text", "blank", "text"])[2] == "")
check("a PDF with only text has no note", extract(["text", "text"])[2] == "")
note = extract(["text", "picture", "blank", "picture", "text"])[2]
check("several: counted and listed, blank pages left out",
      note == "2 of 5 pages (2, 4) are only pictures (scans or photos), so the AI can't read them. It searches the rest.", note)
note = extract(["text"] + ["picture"] * 10)[2]
check("a long list is cut short", note.startswith("10 of 11 pages (2, 3, 4, 5, 6, 7, 8, 9 and 2 more)"), note)

try:
    extract(["picture", "picture"])
    check("a PDF that is all pictures is refused, as before", False, "it was read")
except myfiles.Unreadable as exc:
    check("a PDF that is all pictures is refused, as before", "pictures of pages" in str(exc), str(exc))

# ---- what the app's list gets: the file is ready, with the note ----
write("mixed.pdf", ["text", "picture", "text"])
write("plain.pdf", ["text", "text"])
with open(os.path.join(folder, "notes.txt"), "w") as f:
    f.write(TEXT)
for name in ("t.pdf",):
    os.remove(os.path.join(folder, name))
db = os.path.join(tempfile.mkdtemp(), "mine.sqlite")
myfiles.sync(folder, db)
items = {it["name"]: it for it in myfiles.items(db)}
check("the mixed PDF is ready and carries the note", items["mixed.pdf"]["status"] == "ready"
      and "Page 2 of 3 is only a picture" in (items["mixed.pdf"]["error"] or ""), items.get("mixed.pdf"))
check("a PDF with only text has no note", items["plain.pdf"]["status"] == "ready" and not items["plain.pdf"]["error"],
      items.get("plain.pdf"))
check("a text file has no note", items["notes.txt"]["status"] == "ready" and not items["notes.txt"]["error"],
      items.get("notes.txt"))
check("the mixed PDF's text is searchable", bool(myfiles.MyFiles.open(db).search("generator outdoors")))

print("all passed" if not FAILS else f"{len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
