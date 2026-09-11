from fastapi import FastAPI
from pydantic import BaseModel
import httpx
import sqlite3
import asyncio
import os
import random
import json
from datetime import datetime, timedelta

app = FastAPI()

APPID = os.environ.get("APPID", "wxd185d88371e9916a")
APPSECRET = os.environ.get("APPSECRET", "d09c682a57a63790c1fae0f20978a17d")
TEMPLATE_ID = os.environ.get("TEMPLATE_ID", "wnPOFUCqyZgTiMY7pdHoNgyG65k3VBC38JXLuOfXdZw")
REMIND_HOURS = float(os.environ.get("REMIND_HOURS", "3"))
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

def init_db():
    conn = sqlite3.connect("tasks.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            openid TEXT NOT NULL,
            tasks TEXT NOT NULL,
            remind_at TEXT NOT NULL,
            sent INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS work_contexts (
            openid TEXT PRIMARY KEY,
            work_title TEXT DEFAULT '',
            outline TEXT DEFAULT '',
            new_material TEXT DEFAULT '',
            clarity_result TEXT DEFAULT '',
            long_story_text TEXT DEFAULT '',
            long_story_result TEXT DEFAULT '',
            character_result TEXT DEFAULT '',
            my_characters TEXT DEFAULT '',
            updates TEXT DEFAULT '',
            story_refs TEXT DEFAULT '',
            current_task TEXT DEFAULT '',
            updated_at TEXT NOT NULL
        )
    """)
    try:
        conn.execute("ALTER TABLE work_contexts ADD COLUMN story_refs TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE work_contexts ADD COLUMN character_result TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE work_contexts ADD COLUMN my_characters TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

init_db()


class LoginRequest(BaseModel):
    code: str

class TaskRequest(BaseModel):
    openid: str
    tasks: list[str]
    remind_hours: float = REMIND_HOURS


@app.post("/login")
async def login(req: LoginRequest):
    url = (
        f"https://api.weixin.qq.com/sns/jscode2session"
        f"?appid={APPID}&secret={APPSECRET}"
        f"&js_code={req.code}&grant_type=authorization_code"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
    data = resp.json()
    openid = data.get("openid", "")
    if not openid:
        return {"error": "获取openid失败", "detail": data}
    return {"openid": openid}


@app.post("/submit-tasks")
async def submit_tasks(req: TaskRequest):
    remind_at = (datetime.now() + timedelta(hours=req.remind_hours)).isoformat()
    tasks_text = "\n".join(req.tasks)
    conn = sqlite3.connect("tasks.db")
    conn.execute(
        "INSERT INTO reminders (openid, tasks, remind_at) VALUES (?, ?, ?)",
        (req.openid, tasks_text, remind_at)
    )
    conn.commit()
    conn.close()
    return {"message": f"任务已保存，将在{req.remind_hours}小时后提醒"}


async def get_access_token() -> str:
    url = (
        f"https://api.weixin.qq.com/cgi-bin/token"
        f"?grant_type=client_credential&appid={APPID}&secret={APPSECRET}"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
    return resp.json().get("access_token", "")


async def send_reminder(openid: str, tasks: str):
    token = await get_access_token()
    url = f"https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token={token}"
    task_summary = tasks.replace("\n", "，")[:20]
    payload = {
        "touser": openid,
        "template_id": TEMPLATE_ID,
        "page": "pages/progress/progress",
        "data": {
            "phrase8": {"value": "请更新进度"},
            "thing4": {"value": task_summary}
        }
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload)
    print(f"[{datetime.now()}] 已发送提醒给 {openid}")


async def reminder_loop():
    while True:
        conn = sqlite3.connect("tasks.db")
        now = datetime.now().isoformat()
        rows = conn.execute(
            "SELECT id, openid, tasks FROM reminders WHERE remind_at <= ? AND sent = 0",
            (now,)
        ).fetchall()
        for row_id, openid, tasks in rows:
            await send_reminder(openid, tasks)
            conn.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (row_id,))
        conn.commit()
        conn.close()
        await asyncio.sleep(60)


@app.on_event("startup")
async def startup():
    asyncio.create_task(reminder_loop())


# ==================== 写作进度（writing-progress 小程序，多用户） ====================
import re
import time as time_mod
from fastapi.responses import HTMLResponse, PlainTextResponse
import base64
import hashlib
import secrets
from typing import Optional
from writing_logic import aggregate_file_docs, build_daily, count_text

WRITING_APPID = os.environ.get("WRITING_APPID", "wxff2f10ce15321b4a")
WRITING_APPSECRET = os.environ.get("WRITING_APPSECRET", "af1333432c29946412e52b37c805d836")
WRITING_ENV = os.environ.get("WRITING_ENV", "cloud1-d8gpsjp7i273e1044")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_writing_token = {"value": "", "expires_at": 0.0}


async def writing_access_token() -> str:
    if _writing_token["value"] and time_mod.time() < _writing_token["expires_at"]:
        return _writing_token["value"]
    async with httpx.AsyncClient() as client:
        resp = await client.post("https://api.weixin.qq.com/cgi-bin/stable_token", json={
            "grant_type": "client_credential",
            "appid": WRITING_APPID,
            "secret": WRITING_APPSECRET,
        })
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"获取 access_token 失败: {data}")
    _writing_token["value"] = data["access_token"]
    _writing_token["expires_at"] = time_mod.time() + data.get("expires_in", 7200) - 300
    return _writing_token["value"]


async def writing_db(action: str, query: str) -> dict:
    data = {}
    for attempt in (1, 2):
        token = await writing_access_token()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.weixin.qq.com/tcb/{action}?access_token={token}",
                json={"env": WRITING_ENV, "query": query},
            )
        data = resp.json()
        if data.get("errcode") == 40001 and attempt == 1:
            _writing_token["value"] = ""  # token 失效，换新重试一次
            continue
        break
    if data.get("errcode") != 0:
        raise RuntimeError(f"{action} 失败: {data.get('errmsg')}")
    return data


async def writing_query_doc(collection: str, doc_id: str):
    q = f'db.collection("{collection}").where({{_id:{json.dumps(doc_id)}}}).get()'
    rows = (await writing_db("databasequery", q)).get("data", [])
    return json.loads(rows[0]) if rows else None


async def writing_upsert(collection: str, doc_id: str, doc: dict):
    q = (f'db.collection("{collection}").where({{_id:{json.dumps(doc_id)}}})'
         f'.update({{data:{json.dumps(doc, ensure_ascii=False)}}})')
    data = await writing_db("databaseupdate", q)
    if data.get("matched", 0) == 0:
        add_q = (f'db.collection("{collection}")'
                 f'.add({{data:{json.dumps({"_id": doc_id, **doc}, ensure_ascii=False)}}})')
        await writing_db("databaseadd", add_q)


# ---------- 内容级同步：网页与电脑编辑同一批文件 ----------
# 允许一层子目录作为「书」：书名/章节名.md；禁止路径穿越与隐藏文件
# 书名/文件.md 或 书名/分类/文件.md（最多两层目录）。
# 每段首字符禁止 "." ，路径穿越与隐藏文件仍被挡住。
DOC_NAME_RE = re.compile(r"^(?:[^/\\.][^/\\]{0,60}/){0,2}[^/\\.][^/\\]{0,119}$")
DOC_MAX_CHARS = 200_000
EDITORS = ("web", "computer")


def content_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


# tcb 的 HTTP API 查询串无法承载换行等控制字符，内容一律 base64 存储
def content_encode(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def content_decode(b64: str) -> str:
    return base64.b64decode(b64.encode("ascii")).decode("utf-8")


WRITING_TOKEN_RE = re.compile(r"^[A-Za-z0-9\-]{4,64}$")


async def writing_uid_from_token(token: str) -> str:
    """令牌 → openid。畸形令牌或云端异常一律返回空串，绝不抛异常。"""
    if not WRITING_TOKEN_RE.match(token or ""):
        return ""
    try:
        q = f'db.collection("devices").where({{token:{json.dumps(token)}}}).limit(1).get()'
        rows = (await writing_db("databasequery", q)).get("data", [])
        return json.loads(rows[0]).get("_openid", "") if rows else ""
    except RuntimeError as e:
        print(f"[writing] 令牌校验失败: {e}")
        return ""


ACTIVE_MS_MAX_PER_REPORT = 30 * 60 * 1000  # 单次上报的写作时长上限，防异常值


async def writing_prev_daily(uid: str, date_str: str):
    """取该用户 date_str 之前最近一个写作日的 daily（当天首报时继承其收笔总数为基线）。"""
    q = (f'db.collection("daily").where({{uid:{json.dumps(uid)}}})'
         f'.orderBy("date","desc").limit(5).get()')
    for r in (await writing_db("databasequery", q)).get("data", []):
        doc = json.loads(r)
        if doc.get("date", "") < date_str:  # ISO 日期字符串可直接比较
            return doc
    return None


async def writing_update_progress(uid: str, date_str: str, now_ms: int, active_ms_add: int = 0) -> dict:
    """按用户聚合全部 files 记录，更新当日进度。"""
    all_q = f'db.collection("files").where({{uid:{json.dumps(uid)}}}).limit(1000).get()'
    all_docs = [json.loads(r) for r in (await writing_db("databasequery", all_q)).get("data", [])]
    merged = aggregate_file_docs(all_docs)
    daily_id = f"{uid}:{date_str}"
    existing = await writing_query_doc("daily", daily_id)
    prev = None if existing is not None else await writing_prev_daily(uid, date_str)
    daily = build_daily(uid, date_str, merged, existing, now_ms,
                        active_ms_add=max(0, min(int(active_ms_add), ACTIVE_MS_MAX_PER_REPORT)),
                        prev_daily=prev)
    await writing_upsert("daily", daily_id, daily)
    return daily


async def writing_doc_metas(uid: str):
    """列出该用户全部文档的元信息（不含正文）。正文一律走 docs/get 单取。"""
    field = '.field({name:true,updatedAt:true,editor:true,hash:true,cjk:true,en:true,readonly:true,marks:true})'
    q = f'db.collection("docs").where({{uid:{json.dumps(uid)}}}).limit(1000){field}.get()'
    return [json.loads(r) for r in (await writing_db("databasequery", q)).get("data", [])]


class DocsListRequest(BaseModel):
    token: str
    date: str = ""  # 传入当天日期则一并返回当日进度（今日新增/写作时长）


class DocsGetRequest(BaseModel):
    token: str
    name: str


class DocsPutRequest(BaseModel):
    token: str
    name: str
    content: str
    editor: str
    date: str
    readonly: bool = False
    baseUpdatedAt: Optional[int] = None
    activeMs: int = 0  # 本次上报新增的实际写作时长（毫秒）


class DocsChangesRequest(BaseModel):
    token: str
    since: int
    names: list  # 本地磁盘当前存在的文件名，用于清理已删除文件
    deletedNames: list = []  # 客户端确认「曾同步到磁盘、现已删除」的文件
    date: str


@app.post("/writing/docs/list")
async def writing_docs_list(req: DocsListRequest):
    uid = await writing_uid_from_token(req.token)
    if not uid:
        return {"ok": False, "error": "无效令牌"}
    try:
        docs = await writing_doc_metas(uid)
        docs.sort(key=lambda d: d.get("name", ""))
        today = None
        if req.date and DATE_RE.match(req.date):
            daily = await writing_query_doc("daily", f"{uid}:{req.date}")
            if daily:
                today = {"deltaCjk": daily.get("deltaCjk", 0), "activeMs": daily.get("activeMs", 0)}
        return {"ok": True, "docs": docs, "today": today}
    except RuntimeError as e:
        print(f"[writing] docs/list 失败: {e}")
        return {"ok": False, "error": "服务器内部错误"}


@app.post("/writing/docs/get")
async def writing_docs_get(req: DocsGetRequest):
    uid = await writing_uid_from_token(req.token)
    if not uid:
        return {"ok": False, "error": "无效令牌"}
    try:
        doc = await writing_query_doc("docs", f"{uid}:{req.name}")
        if doc is None:
            return {"ok": False, "error": "文件不存在"}
        hb = doc.get("highlightsB64")
        highlights = json.loads(content_decode(hb)) if hb else []
        return {"ok": True, "content": content_decode(doc.get("contentB64", "")),
                "updatedAt": doc.get("updatedAt", 0), "readonly": doc.get("readonly", False),
                "marks": doc.get("marks", []), "highlights": highlights}
    except RuntimeError as e:
        print(f"[writing] docs/get 失败: {e}")
        return {"ok": False, "error": "服务器内部错误"}


@app.post("/writing/docs/put")
async def writing_docs_put(req: DocsPutRequest):
    if not DOC_NAME_RE.match(req.name) or req.name.startswith("."):
        return {"ok": False, "error": "非法文件名"}
    if req.editor not in EDITORS:
        return {"ok": False, "error": "非法编辑来源"}
    if not DATE_RE.match(req.date):
        return {"ok": False, "error": "日期格式应为 YYYY-MM-DD"}
    if len(req.content) > DOC_MAX_CHARS:
        return {"ok": False, "error": f"单文件最长 {DOC_MAX_CHARS} 字符"}
    uid = await writing_uid_from_token(req.token)
    if not uid:
        return {"ok": False, "error": "无效令牌"}
    try:
        doc_id = f"{uid}:{req.name}"
        existing = await writing_query_doc("docs", doc_id)
        if existing and existing.get("readonly") and req.editor == "web":
            return {"ok": False, "error": "该文件为只读（Word 文档请在电脑上编辑）"}
        # 网页保存时校验版本，避免覆盖电脑刚写的内容；电脑保存以磁盘为准
        if (req.editor == "web" and req.baseUpdatedAt is not None and existing
                and existing.get("updatedAt") != req.baseUpdatedAt):
            return {"ok": False, "conflict": True, "error": "文件已在电脑上更新"}

        now_ms = int(time_mod.time() * 1000)
        cjk, en = count_text(req.content)
        await writing_upsert("docs", doc_id, {
            "uid": uid, "name": req.name, "contentB64": content_encode(req.content),
            "hash": content_hash(req.content), "editor": req.editor,
            "readonly": req.readonly, "cjk": cjk, "en": en, "updatedAt": now_ms,
        })
        # 配置类文档（_advisor.md / _conflicts.md 等）内容照存——顾问要读，
        # 但字数记 0，不污染写作统计。写 0 而不是跳过，是为了让此前
        # 已被计入的旧数据也归零。
        counted = is_countable(req.name)
        await writing_upsert("files", f"{uid}:sync:{req.name}", {
            "uid": uid, "source": "sync", "name": req.name,
            "cjk": cjk if counted else 0, "en": en if counted else 0,
            "updatedAt": now_ms,
        })
        daily = await writing_update_progress(uid, req.date, now_ms, active_ms_add=req.activeMs)
        return {"ok": True, "updatedAt": now_ms, "cjk": cjk, "en": en,
                "deltaCjk": daily["deltaCjk"], "activeMs": daily["activeMs"]}
    except RuntimeError as e:
        print(f"[writing] docs/put 失败: {e}")
        return {"ok": False, "error": "服务器内部错误"}


class DocsMarksRequest(BaseModel):
    token: str
    name: str
    marks: list  # 被标记段落的原文列表


class DocsHighlightsRequest(BaseModel):
    token: str
    name: str
    highlights: list  # [{para, start, len, color}]，整体 base64 存储


@app.post("/writing/docs/highlights")
async def writing_docs_highlights(req: DocsHighlightsRequest):
    """保存彩色高亮标注（不写进正文文件，单独存、跨设备同步）。"""
    if not isinstance(req.highlights, list) or len(req.highlights) > 1000:
        return {"ok": False, "error": "高亮过多"}
    uid = await writing_uid_from_token(req.token)
    if not uid:
        return {"ok": False, "error": "无效令牌"}
    try:
        doc_id = f"{uid}:{req.name}"
        if await writing_query_doc("docs", doc_id) is None:
            return {"ok": False, "error": "文件不存在"}
        blob = content_encode(json.dumps(req.highlights, ensure_ascii=False))
        q = (f'db.collection("docs").where({{_id:{json.dumps(doc_id)}}})'
             f'.update({{data:{{highlightsB64:{json.dumps(blob)}}}}})')
        await writing_db("databaseupdate", q)
        return {"ok": True, "count": len(req.highlights)}
    except RuntimeError as e:
        print(f"[writing] docs/highlights 失败: {e}")
        return {"ok": False, "error": "服务器内部错误"}


@app.post("/writing/docs/marks")
async def writing_docs_marks(req: DocsMarksRequest):
    """保存段落书签（以段落文字为键，跨设备同步）。"""
    if not isinstance(req.marks, list) or len(req.marks) > 100:
        return {"ok": False, "error": "书签最多 100 个"}
    marks = [m for m in req.marks if isinstance(m, str) and 0 < len(m) <= 500]
    uid = await writing_uid_from_token(req.token)
    if not uid:
        return {"ok": False, "error": "无效令牌"}
    try:
        doc_id = f"{uid}:{req.name}"
        if await writing_query_doc("docs", doc_id) is None:
            return {"ok": False, "error": "文件不存在"}
        q = (f'db.collection("docs").where({{_id:{json.dumps(doc_id)}}})'
             f'.update({{data:{{marks:{json.dumps(marks, ensure_ascii=False)}}}}})')
        await writing_db("databaseupdate", q)
        return {"ok": True, "count": len(marks)}
    except RuntimeError as e:
        print(f"[writing] docs/marks 失败: {e}")
        return {"ok": False, "error": "服务器内部错误"}


@app.post("/writing/docs/changes")
async def writing_docs_changes(req: DocsChangesRequest):
    """watcher 轮询：取回网页端的修改；顺带清理本地已删除的文件。"""
    uid = await writing_uid_from_token(req.token)
    if not uid:
        return {"ok": False, "error": "无效令牌"}
    if not DATE_RE.match(req.date):
        return {"ok": False, "error": "日期格式应为 YYYY-MM-DD"}
    try:
        metas = await writing_doc_metas(uid)
        changed = []
        removed = []
        local_names = set(n for n in req.names if isinstance(n, str))
        # 客户端确认删除的文件：无论最后编辑者是谁都清理（曾落盘、用户主动删的）
        confirmed_deleted = set(n for n in req.deletedNames if isinstance(n, str)) - local_names
        for name in confirmed_deleted:
            if any(m.get("name") == name for m in metas):
                await writing_db("databasedelete",
                                 f'db.collection("docs").where({{_id:{json.dumps(f"{uid}:{name}")}}}).remove()')
                await writing_db("databasedelete",
                                 f'db.collection("files").where({{_id:{json.dumps(f"{uid}:sync:{name}")}}}).remove()')
                removed.append(name)
        for m in metas:
            if m.get("name") in confirmed_deleted:
                continue
            name = m.get("name", "")
            if m.get("editor") == "web" and m.get("updatedAt", 0) > req.since:
                full = await writing_query_doc("docs", f"{uid}:{name}")
                if full:
                    changed.append({"name": name, "content": content_decode(full.get("contentB64", "")),
                                    "updatedAt": full.get("updatedAt", 0)})
            # 只清理「最后一次由电脑编辑」且本地已不存在的文件——网页新建未落盘的文件绝不动
            elif m.get("editor") == "computer" and local_names and name not in local_names:
                await writing_db("databasedelete",
                                 f'db.collection("docs").where({{_id:{json.dumps(f"{uid}:{name}")}}}).remove()')
                await writing_db("databasedelete",
                                 f'db.collection("files").where({{_id:{json.dumps(f"{uid}:sync:{name}")}}}).remove()')
                removed.append(name)
        if removed:
            await writing_update_progress(uid, req.date, int(time_mod.time() * 1000))
        return {"ok": True, "changed": changed, "removed": removed}
    except RuntimeError as e:
        print(f"[writing] docs/changes 失败: {e}")
        return {"ok": False, "error": "服务器内部错误"}


SHARE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{6,40}$")
COMMENT_MAX = 2000


def book_of_name(name: str) -> str:
    return name.split("/")[0] if "/" in name else ""


class ShareCreateRequest(BaseModel):
    token: str
    book: str = ""  # 空串 = 主书架（THE ROOM）


class ShareGetRequest(BaseModel):
    share: str


class ShareChapterRequest(BaseModel):
    share: str
    name: str


class CommentAddRequest(BaseModel):
    share: str
    name: str
    para: str = ""
    author: str = ""
    text: str


class CommentByTokenRequest(BaseModel):
    token: str
    name: str


class CommentDeleteRequest(BaseModel):
    token: str
    id: str


async def writing_book_chapters(uid: str, book: str):
    """返回该用户某本书下所有文件名（升序）。"""
    metas = await writing_doc_metas(uid)
    names = [m["name"] for m in metas if book_of_name(m.get("name", "")) == book]
    return sorted(names)


@app.post("/writing/share/create")
async def writing_share_create(req: ShareCreateRequest):
    """为整本书生成只读分享码（同一本书复用同一个码）。"""
    uid = await writing_uid_from_token(req.token)
    if not uid:
        return {"ok": False, "error": "无效令牌"}
    try:
        q = (f'db.collection("shares").where({{uid:{json.dumps(uid)},book:{json.dumps(req.book)}}})'
             f'.limit(1).get()')
        rows = (await writing_db("databasequery", q)).get("data", [])
        if rows:
            share_id = json.loads(rows[0])["_id"]
        else:
            share_id = secrets.token_urlsafe(9)
            await writing_upsert("shares", share_id, {
                "uid": uid, "book": req.book, "createdAt": int(time_mod.time() * 1000)})
        return {"ok": True, "shareId": share_id}
    except RuntimeError as e:
        print(f"[writing] share/create 失败: {e}")
        return {"ok": False, "error": "服务器内部错误"}


@app.post("/writing/share/book")
async def writing_share_book(req: ShareGetRequest):
    """公开：凭分享码返回这本书的章节目录（仅书名+章节标题，不含正文）。"""
    if not SHARE_ID_RE.match(req.share or ""):
        return {"ok": False, "error": "无效分享码"}
    try:
        share = await writing_query_doc("shares", req.share)
        if share is None:
            return {"ok": False, "error": "分享不存在或已被取消"}
        book = share.get("book", "")
        names = await writing_book_chapters(share["uid"], book)
        chapters = [{"name": n, "title": n.split("/")[-1].rsplit(".", 1)[0]} for n in names]
        return {"ok": True, "book": book or "THE ROOM", "chapters": chapters}
    except RuntimeError as e:
        print(f"[writing] share/book 失败: {e}")
        return {"ok": False, "error": "服务器内部错误"}


@app.post("/writing/share/chapter")
async def writing_share_chapter(req: ShareChapterRequest):
    """公开：凭分享码返回本书内某一章的正文（校验该章确属这本书）。"""
    if not SHARE_ID_RE.match(req.share or ""):
        return {"ok": False, "error": "无效分享码"}
    try:
        share = await writing_query_doc("shares", req.share)
        if share is None:
            return {"ok": False, "error": "分享不存在或已被取消"}
        if book_of_name(req.name) != share.get("book", ""):
            return {"ok": False, "error": "无权访问"}
        doc = await writing_query_doc("docs", f'{share["uid"]}:{req.name}')
        if doc is None:
            return {"ok": False, "error": "该文件已被删除"}
        return {"ok": True,
                "title": req.name.split("/")[-1].rsplit(".", 1)[0],
                "content": content_decode(doc.get("contentB64", ""))}
    except RuntimeError as e:
        print(f"[writing] share/chapter 失败: {e}")
        return {"ok": False, "error": "服务器内部错误"}


def _comment_field(d, key):
    """留言字段 base64 存储；兼容极早期的明文旧数据。"""
    b = d.get(key + "B64")
    if b is not None:
        return content_decode(b)
    return d.get(key, "")


async def _comments_for(uid: str, name: str):
    q = (f'db.collection("comments").where({{uid:{json.dumps(uid)},name:{json.dumps(name)}}})'
         f'.orderBy("createdAt","asc").limit(500).get()')
    rows = (await writing_db("databasequery", q)).get("data", [])
    return [{"id": d["_id"], "para": _comment_field(d, "para"),
             "author": _comment_field(d, "author"), "text": _comment_field(d, "text"),
             "createdAt": d.get("createdAt", 0)}
            for d in map(json.loads, rows)]


@app.post("/writing/comments/list")
async def writing_comments_list(req: ShareChapterRequest):
    """公开：某一章的全部留言。"""
    if not SHARE_ID_RE.match(req.share or ""):
        return {"ok": False, "error": "无效分享码"}
    try:
        share = await writing_query_doc("shares", req.share)
        if share is None:
            return {"ok": False, "error": "分享不存在或已被取消"}
        if book_of_name(req.name) != share.get("book", ""):
            return {"ok": False, "error": "无权访问"}
        return {"ok": True, "comments": await _comments_for(share["uid"], req.name)}
    except RuntimeError as e:
        print(f"[writing] comments/list 失败: {e}")
        return {"ok": False, "error": "服务器内部错误"}


@app.post("/writing/comments/add")
async def writing_comments_add(req: CommentAddRequest):
    """公开：读者对某段落留言。"""
    if not SHARE_ID_RE.match(req.share or ""):
        return {"ok": False, "error": "无效分享码"}
    text = (req.text or "").strip()
    author = (req.author or "").strip()[:24] or "读者"
    if not text or len(text) > COMMENT_MAX:
        return {"ok": False, "error": "留言为空或过长"}
    try:
        share = await writing_query_doc("shares", req.share)
        if share is None:
            return {"ok": False, "error": "分享不存在或已被取消"}
        if book_of_name(req.name) != share.get("book", ""):
            return {"ok": False, "error": "无权评论"}
        cid = secrets.token_urlsafe(9)
        await writing_upsert("comments", cid, {
            "uid": share["uid"], "name": req.name,
            "paraB64": content_encode((req.para or "")[:500]),
            "authorB64": content_encode(author),
            "textB64": content_encode(text[:COMMENT_MAX]),
            "createdAt": int(time_mod.time() * 1000)})
        return {"ok": True, "id": cid, "author": author}
    except RuntimeError as e:
        print(f"[writing] comments/add 失败: {e}")
        return {"ok": False, "error": "服务器内部错误"}


@app.post("/writing/comments/mine")
async def writing_comments_mine(req: CommentByTokenRequest):
    """作者端：凭令牌读取某一章收到的留言。"""
    uid = await writing_uid_from_token(req.token)
    if not uid:
        return {"ok": False, "error": "无效令牌"}
    try:
        return {"ok": True, "comments": await _comments_for(uid, req.name)}
    except RuntimeError as e:
        print(f"[writing] comments/mine 失败: {e}")
        return {"ok": False, "error": "服务器内部错误"}


@app.post("/writing/comments/delete")
async def writing_comments_delete(req: CommentDeleteRequest):
    """作者端：删除自己收到的一条留言。"""
    uid = await writing_uid_from_token(req.token)
    if not uid:
        return {"ok": False, "error": "无效令牌"}
    try:
        c = await writing_query_doc("comments", req.id)
        if c is not None and c.get("uid") == uid:
            await writing_db("databasedelete",
                             f'db.collection("comments").where({{_id:{json.dumps(req.id)}}}).remove()')
        return {"ok": True}
    except RuntimeError as e:
        print(f"[writing] comments/delete 失败: {e}")
        return {"ok": False, "error": "服务器内部错误"}


# 两个页面每次部署都在变，文件名不带版本号。不给缓存指令的话浏览器会按
# 启发式规则自行缓存，用户刷新后拿到的仍是旧 HTML——表现为「新功能看不见」
# 「快捷键不工作」，排查时极易误判成代码 bug。
NO_CACHE = {"Cache-Control": "no-cache, must-revalidate", "Pragma": "no-cache"}


@app.get("/read")
async def writing_read_page():
    page = os.path.join(os.path.dirname(os.path.abspath(__file__)), "read_page.html")
    with open(page, encoding="utf-8") as f:
        return HTMLResponse(f.read(), headers=NO_CACHE)


@app.get("/write")
async def writing_web_page():
    page = os.path.join(os.path.dirname(os.path.abspath(__file__)), "write_page.html")
    with open(page, encoding="utf-8") as f:
        return HTMLResponse(f.read(), headers=NO_CACHE)


@app.get("/writing/shelf-bg.jpg")
async def writing_shelf_bg():
    from fastapi.responses import FileResponse
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shelf_bg.jpg")
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=604800"})


@app.get("/writing/watcher.py")
async def writing_watcher_script():
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watcher_client.py")
    with open(script, encoding="utf-8") as f:
        return PlainTextResponse(f.read(), media_type="text/x-python")


# ==================== 科幻顾问（Kimi / Moonshot） ====================
# 按书隔离：从当前文档名推出所在的书，只在那本书里找 00_ 设定 / 07_ 冲突清单 /
# _advisor.md 专属人格。根书架（THE ROOM）用 scifi_advisor_prompt.md，其它书用通用人格。

MOONSHOT_API_KEY = os.environ.get("MOONSHOT_API_KEY", "").strip()
MOONSHOT_MODEL = os.environ.get("MOONSHOT_MODEL", "kimi-k3")
# 用中转站/代理时改这个（OpenAI 兼容格式，填到 /v1 为止）
MOONSHOT_BASE_URL = os.environ.get("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1").rstrip("/")
MOONSHOT_URL = f"{MOONSHOT_BASE_URL}/chat/completions"

def is_countable(name: str) -> bool:
    """文件名以 _ 开头的是配置/AI 产物，存内容但不计写作字数。"""
    return not name.rsplit("/", 1)[-1].startswith("_")


CANON_PREFIX = "00_"        # 每本书的世界观权威：书内第一个 00_ 开头的文档
CONFLICT_PREFIX = "_conflicts"  # 每本书的冲突清单：书内 _conflicts.md
PERSONA_DOC = "_advisor.md" # 可选：书内专属顾问人格，存在则覆盖默认

class ScifiAdvisorRequest(BaseModel):
    token: str
    mode: str = "ask"        # ask=提设定问顾问 / check=查与 canon 的冲突
    question: str = ""       # ask 模式的问题
    selection: str = ""      # 编辑器里选中的段落（两种模式都可选）
    name: str = ""           # 当前打开的文档名，用于判断在哪本书里（必填）


def advisor_prompt_file(filename: str) -> str:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    with open(path, encoding="utf-8") as f:
        return f.read()


def advisor_book_prefix(name: str) -> str:
    """'写作系统带我飞/第一章.md' -> '写作系统带我飞/'；根书架文档 -> ''。"""
    i = name.find("/")
    return name[: i + 1] if i > 0 else ""


async def advisor_find_doc(uid: str, prefix: str, starts: str) -> str:
    """在某本书里找第一个文件名以 starts 开头的文档，返回完整 name；没有返回 ''。"""
    docs = await writing_doc_metas(uid)
    for n in sorted(d.get("name", "") for d in docs):
        if not n.startswith(prefix):
            continue
        rel = n[len(prefix):]
        if "/" in rel:              # 更深一层的子目录不算这本书的
            continue
        if rel.startswith(starts):
            return n
    return ""


async def advisor_doc_text(uid: str, name: str) -> str:
    if not name:
        return ""
    doc = await writing_query_doc("docs", f"{uid}:{name}")
    if not doc:
        return ""
    return content_decode(doc.get("contentB64", ""))


async def advisor_persona(uid: str, prefix: str):
    """返回 (人格文本, 来源说明)。"""
    base = advisor_prompt_file("scifi_advisor_prompt.md")   # 层1+2 通用底座
    custom = await advisor_doc_text(uid, f"{prefix}{PERSONA_DOC}")  # 层3 本项目
    if custom.strip():
        return f"{base}\n\n---\n\n{custom}", f"通用底座 + {prefix}{PERSONA_DOC}"
    return base, "通用底座（本书无 _advisor.md，缺项目坐标）"


async def moonshot_chat(system: str, user: str, max_tokens: int = 2000) -> str:
    """调用 Kimi。失败时抛 RuntimeError，由端点统一转成 {ok:false}。"""
    if not MOONSHOT_API_KEY:
        raise RuntimeError("未配置 MOONSHOT_API_KEY")
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                MOONSHOT_URL,
                headers={
                    "Authorization": f"Bearer {MOONSHOT_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": MOONSHOT_MODEL,
                    "temperature": 0.3,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
    except httpx.HTTPError as e:
        raise RuntimeError(f"网络错误: {e}")

    if resp.status_code != 200:
        raise RuntimeError(f"Kimi 返回 {resp.status_code}: {resp.text[:200]}")

    payload = resp.json()
    # 中转站有时把 OpenAI 标准响应多包一层 data，两种都认。
    if isinstance(payload.get("data"), dict) and "choices" in payload["data"]:
        payload = payload["data"]
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError(f"Kimi 响应异常: {str(payload)[:200]}")
    return choices[0].get("message", {}).get("content", "").strip()


ASK_TEMPLATE = """下面是《{book}》的世界观权威设定（{canon_name}）：

<canon>
{canon}
</canon>
{selection_block}
作者的问题：
{question}

按你的「工作模式」判断这是哪一类问法，只出那一档的内容——**不要一律出长报告**。
问撞车就只谈撞车，问怎么交代就只给载体与改写示范。

无论哪一档都遵守：
- 不写开场白，直接进结论。
- 引 canon 时引**原话**，不要编行号。
- 涉及作品知识时遵守你的「校准」规则：表外作品标【表外·凭记忆】，宁可少举一部，不要编一部。
- 你现在处于**只读环境**（canon 是上面贴给你的）。只给建议，不要声称已经写回任何文件；
  该改 canon 时，写成"这条若采纳，请在 canon 补一行：……"。
"""

CHECK_TEMPLATE = """你在做设定一致性检查。下面是《{book}》的世界观权威设定（{canon_name}），以及一份已知冲突清单。

<canon>
{canon}
</canon>

<冲突清单>
{conflicts}
</冲突清单>

待检查的文本（来自《{name}》）：
<待检查>
{text}
</待检查>

任务：把待检查文本**逐条**对照上面的冲突清单和 canon，只报告真正命中的问题。

规则：
- 逐条比对，不要通读了事。清单里每一条都要过一遍。
- 只报实质冲突（设定矛盾、事实打架、违反硬规则），不报措辞差异、不报文笔问题。
- 命中清单里已登记的冲突时，写出编号（如 C-02）。
- 发现清单外的新冲突，标注【新】。
- 确实没有问题就只回一句"未发现与 canon 的冲突"，不要凑数。
- 引 canon 时引原话，不要编行号。你处于只读环境，只报告不修改，也不要声称已修改。

输出格式，每条一段：

**[编号或【新】] 一句话说明冲突**
- 待检查文本里的说法：……
- canon / 清单里的说法：……
- 建议：……
"""


@app.post("/scifi-advisor")
async def scifi_advisor(req: ScifiAdvisorRequest):
    uid = await writing_uid_from_token(req.token)
    if not uid:
        return {"ok": False, "error": "无效令牌"}

    mode = (req.mode or "ask").strip()
    if mode not in ("ask", "check"):
        return {"ok": False, "error": "mode 只能是 ask 或 check"}

    name = (req.name or "").strip()
    if not name:
        return {"ok": False, "error": "请先打开一篇文档——顾问需要知道你在哪本书里"}
    prefix = advisor_book_prefix(name)
    book = prefix.rstrip("/") or "根书架"

    try:
        canon_name = await advisor_find_doc(uid, prefix, CANON_PREFIX)
        canon = await advisor_doc_text(uid, canon_name)
        persona, persona_src = await advisor_persona(uid, prefix)
    except RuntimeError as e:
        print(f"[advisor] 读取《{book}》设定失败: {e}")
        return {"ok": False, "error": "服务器内部错误"}
    except OSError as e:
        print(f"[advisor] 读取顾问 prompt 失败: {e}")
        return {"ok": False, "error": "服务器内部错误"}

    if not canon_name or not canon.strip():
        return {"ok": False,
                "error": f"《{book}》里没有 {CANON_PREFIX} 开头的设定文件。"
                         f"在这本书的文件夹里建一个（如 {CANON_PREFIX}世界观设定.md）就行"}

    if mode == "ask":
        question = req.question.strip()
        if not question:
            return {"ok": False, "error": "问题为空"}
        selection_block = ""
        if req.selection.strip():
            selection_block = f"\n作者选中的段落：\n<选中>\n{req.selection.strip()}\n</选中>\n"
        user_msg = ASK_TEMPLATE.format(
            book=book, canon_name=canon_name, canon=canon,
            question=question, selection_block=selection_block,
        )
        max_tokens = 2000
    else:
        text = req.selection.strip()
        if not text:
            return {"ok": False, "error": "请先选中要检查的文本"}
        try:
            conflicts_name = await advisor_find_doc(uid, prefix, CONFLICT_PREFIX)
            conflicts = await advisor_doc_text(uid, conflicts_name)
        except RuntimeError as e:
            print(f"[advisor] 读取《{book}》冲突清单失败: {e}")
            return {"ok": False, "error": "服务器内部错误"}
        if not conflicts.strip():
            conflicts = "（这本书暂无冲突清单，只依据 canon 检查）"
        user_msg = CHECK_TEMPLATE.format(
            book=book, canon_name=canon_name, canon=canon,
            conflicts=conflicts, name=name, text=text,
        )
        max_tokens = 2500

    try:
        answer = await moonshot_chat(persona, user_msg, max_tokens)
    except RuntimeError as e:
        print(f"[advisor] Kimi 调用失败: {e}")
        return {"ok": False, "error": str(e)}

    if not answer:
        return {"ok": False, "error": "Kimi 返回了空内容"}

    return {"ok": True, "mode": mode, "answer": answer,
            "book": book, "canon": canon_name, "persona": persona_src}
