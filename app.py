import asyncio, base64, hashlib, json, os, re, secrets, time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
SESSION = os.environ["TG_SESSION"]
USER = os.getenv("APP_USER", "hrd")
PASSWORD = os.environ["APP_PASSWORD"]
ADMIN_USER = os.getenv("ADMIN_USER", USER)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", PASSWORD)
SEED = [c.strip() for c in os.getenv("TG_CHANNEL", "").split(",") if c.strip()]  # optional first channels
DATA = Path(os.getenv("DATA_DIR", "data"))
COVERS = DATA / "covers"
COVERS.mkdir(parents=True, exist_ok=True)
STATE, CHFILE = DATA / "state.json", DATA / "channels.json"
STATIC = Path(__file__).parent / "static"
CH = 512 * 1024      # Telegram download chunk size
CAP = 16 * CH        # max bytes per 206 response (8 MB); Chrome asks for the next range itself
BIG = 10 ** 10       # lecture id = channel id * BIG + message id

client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
meta = {"next": 0, "channels": []}   # saved list: [{"id", "ref"}]
ents = {}                            # channel id -> Telegram entity
lib = {"data": None, "t": 0.0}
docs = {}
lock = asyncio.Lock()


def peer(c):
    if c.lstrip("-").isdigit():
        return int(c) if c.startswith("-") else int("-100" + c)
    return c


def write_json(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj))
    tmp.replace(path)


async def resolve(ref):
    p = peer(ref)
    if isinstance(p, int):
        await client.get_dialogs()  # fills the entity cache for private channels
    return await client.get_entity(p)


@asynccontextmanager
async def lifespan(_):
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("TG_SESSION is not logged in. Run login.py again.")
    if CHFILE.exists():
        meta.update(json.loads(CHFILE.read_text()))
    else:
        meta.update(next=len(SEED), channels=[{"id": i, "ref": r} for i, r in enumerate(SEED)])
        write_json(CHFILE, meta)
    for c in meta["channels"]:
        try:
            ents[c["id"]] = await resolve(c["ref"])
        except Exception as e:
            print("Could not open", c["ref"], e)
    yield
    await client.disconnect()


basic = HTTPBasic(realm="HRD HUB")


def match(c, u, p):
    return secrets.compare_digest(c.username.encode(), u.encode()) and secrets.compare_digest(c.password.encode(), p.encode())


def deny():
    return HTTPException(401, headers={"WWW-Authenticate": 'Basic realm="HRD HUB"'})


def check(c: HTTPBasicCredentials = Depends(basic)):
    if not (match(c, USER, PASSWORD) or match(c, ADMIN_USER, ADMIN_PASSWORD)):
        raise deny()


def check_admin(c: HTTPBasicCredentials = Depends(basic)):
    if not match(c, ADMIN_USER, ADMIN_PASSWORD):
        raise deny()


pub = APIRouter(dependencies=[Depends(check)])
adm = APIRouter(dependencies=[Depends(check_admin)])

SEC = re.compile(r"^\[(\d+)\]\s*Section\s*[»›>:]+\s*(.+)$", re.I)
CRS = re.compile(r"^Course\s*[»›>:]+\s*(.+)$", re.I)
LEC = re.compile(r"^\[(\d+)\]\s*[»›>:]+\s*(.+)$")


def parse(text):
    num = title = sec = course = secno = None
    for raw in (text or "").splitlines():
        l = re.sub(r"^[^\w\[]+", "", raw).strip()
        if m := CRS.match(l):
            course = m[1].strip()
        elif m := SEC.match(l):
            secno, sec = int(m[1]), m[2].strip()
        elif m := LEC.match(l):
            num, title = int(m[1]), m[2].strip()
    return (num, title, sec, secno, course) if title else None


async def build():
    courses = {}
    for cid, ent in ents.items():
        default = getattr(ent, "title", "") or "Lectures"
        async for m in client.iter_messages(ent):
            is_video = m.video or (m.document and (m.document.mime_type or "").startswith("video/"))
            p = parse(m.message) if is_video else None
            if not p:
                continue
            num, title, sec, secno, course = p
            name = course or default
            c = courses.setdefault(name, {"name": name, "items": []})
            c["items"].append({"id": cid * BIG + m.id, "num": num, "title": title, "sec": sec or "Other",
                               "secNo": 9999 if secno is None else secno, "date": m.date.date().isoformat()})
    return {"courses": courses}


def cpath(name):
    return COVERS / (hashlib.sha1(name.encode()).hexdigest()[:16] + ".jpg")


@pub.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@pub.get("/api/library")
async def library(refresh: int = 0):
    async with lock:
        if refresh or not lib["data"] or time.time() - lib["t"] > 600:
            lib["data"], lib["t"] = await build(), time.time()
    for n, c in lib["data"]["courses"].items():
        p = cpath(n)
        c["cover"] = f"/covers/{p.name}?v={int(p.stat().st_mtime)}" if p.exists() else ""
    return lib["data"]


@pub.get("/covers/{fname}")
async def cover(fname: str):
    if not re.fullmatch(r"[0-9a-f]{16}\.jpg", fname) or not (COVERS / fname).exists():
        return Response("Not found", 404)
    return FileResponse(COVERS / fname, headers={"Cache-Control": "private, max-age=3600"})


@pub.get("/api/state")
async def get_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


@pub.put("/api/state")
async def put_state(request: Request):
    write_json(STATE, await request.json())
    return {"ok": True}


async def get_doc(cid):
    hit = docs.get(cid)
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    ch, mid = divmod(cid, BIG)
    if ch not in ents:
        return None
    m = await client.get_messages(ents[ch], ids=mid)
    d = m.document if m else None
    if d:
        docs[cid] = (time.time(), d)
    return d


@pub.get("/stream/{cid}")
async def stream(cid: int, request: Request):
    d = await get_doc(cid)
    if not d:
        return Response("Not found", 404)
    size, mime = d.size, d.mime_type or "video/mp4"
    start, end, code = 0, size - 1, 200
    if m := re.match(r"bytes=(\d*)-(\d*)", request.headers.get("range", "")):
        a, b = m.groups()
        if a == "" and b != "":
            start = max(0, size - int(b))
        else:
            start = int(a or 0)
            if b:
                end = min(int(b), size - 1)
        if start > end or start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        end, code = min(end, start + CAP - 1), 206
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1), "Cache-Control": "private, max-age=3600"}
    if code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    async def body():
        off = start - start % CH
        skip, left = start - off, end - start + 1
        async for chunk in client.iter_download(d, offset=off, limit=(end - off) // CH + 1, request_size=CH):
            if skip:
                chunk, skip = chunk[skip:], 0
            chunk = chunk[:left]
            left -= len(chunk)
            yield bytes(chunk)
            if left <= 0:
                break

    return StreamingResponse(body(), status_code=code, media_type=mime, headers=headers)


# ---------- admin ----------
@adm.get("/admin")
async def admin_page():
    return FileResponse(STATIC / "admin.html")


@adm.get("/api/admin/channels")
async def list_channels():
    return [{"id": c["id"], "ref": c["ref"], "ok": c["id"] in ents,
             "title": getattr(ents.get(c["id"]), "title", "")} for c in meta["channels"]]


@adm.post("/api/admin/channels")
async def add_channel(request: Request):
    ref = ((await request.json()).get("ref") or "").strip()
    if not ref:
        raise HTTPException(400, "Enter a channel username, link or ID.")
    try:
        ent = await resolve(ref)
    except Exception:
        raise HTTPException(400, "Could not open that channel. Your Telegram account must be a member of it.")
    if any(e.id == ent.id for e in ents.values()):
        raise HTTPException(409, "That channel is already added.")
    cid = meta["next"]
    meta["channels"].append({"id": cid, "ref": ref})
    meta["next"] = cid + 1
    ents[cid] = ent
    write_json(CHFILE, meta)
    lib["data"] = None
    return {"ok": True}


@adm.delete("/api/admin/channels/{cid}")
async def del_channel(cid: int):
    meta["channels"] = [c for c in meta["channels"] if c["id"] != cid]
    ents.pop(cid, None)
    write_json(CHFILE, meta)
    lib["data"] = None
    return {"ok": True}


@adm.get("/api/admin/dialogs")
async def dialogs():
    have = {e.id for e in ents.values()}
    out = []
    async for d in client.iter_dialogs():
        if d.is_channel:
            e = d.entity
            ref = "@" + e.username if getattr(e, "username", None) else str(d.id)
            out.append({"title": d.name, "ref": ref, "added": e.id in have,
                        "kind": "group" if getattr(e, "megagroup", False) else "channel"})
    return out


@adm.post("/api/admin/cover")
async def set_cover(request: Request):
    j = await request.json()
    name, img = j.get("course", ""), j.get("image", "")
    if not name or not img.startswith("data:image/jpeg;base64,"):
        raise HTTPException(400, "Send a JPEG image for an existing course.")
    raw = base64.b64decode(img.split(",", 1)[1])
    if len(raw) > 3_000_000:
        raise HTTPException(413, "Image is too large.")
    cpath(name).write_bytes(raw)
    return {"ok": True}


@adm.delete("/api/admin/cover")
async def del_cover(course: str):
    cpath(course).unlink(missing_ok=True)
    return {"ok": True}


app = FastAPI(lifespan=lifespan)
app.include_router(pub)
app.include_router(adm)
