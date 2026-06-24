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

class RandomStartRequest(BaseModel):
    openid: str
    goals: list[str]

class FocusBreakdownRequest(BaseModel):
    openid: str
    tasks: list[str]

class AnalyzeRolesRequest(BaseModel):
    longterm_goal: str

class DailyTasksRequest(BaseModel):
    longterm_goal: str
    main_quest: str = ""
    role: str = ""

class CreativeFishboneRequest(BaseModel):
    task: str
    thoughts: list[str]
    work_title: str = ""
    outline: str = ""
    updates: list[str] = []

class WorkClarifyRequest(BaseModel):
    work_title: str = ""
    outline: str = ""
    updates: list[str] = []
    new_material: str
    current_task: str = ""

class LongStoryAnalyzeRequest(BaseModel):
    work_title: str = ""
    text: str
    current_task: str = ""

class CharacterFishboneRequest(BaseModel):
    work_title: str = ""
    text: str
    focus_character: str = ""
    current_task: str = ""

class WorkContextSaveRequest(BaseModel):
    openid: str
    work_title: str = ""
    outline: str = ""
    new_material: str = ""
    clarity_result: dict | None = None
    long_story_text: str = ""
    long_story_result: dict | None = None
    character_result: dict | None = None
    my_characters: list[dict] = []
    updates: list[dict] = []
    story_refs: list[dict] = []
    current_task: str = ""

class WorkContextLoadRequest(BaseModel):
    openid: str


async def ask_claude_json(prompt: str, max_tokens: int = 1200):
    import json, re
    async with httpx.AsyncClient(timeout=45) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}]
            }
        )
    data = resp.json()
    text_block = next((b for b in data.get("content", []) if b.get("type") == "text"), None)
    if not text_block:
        raise Exception(f"Unexpected response: {data}")
    raw = text_block["text"].strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        if start == -1:
            raise
        try:
            parsed, _ = json.JSONDecoder().raw_decode(raw[start:])
            return parsed
        except json.JSONDecodeError:
            repair_prompt = f"""把下面内容修复成合法 JSON。不要解释，不要 markdown，只返回合法 JSON。

原内容：
{raw}"""
            async with httpx.AsyncClient(timeout=30) as client:
                repair_resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    },
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": max_tokens,
                        "messages": [{"role": "user", "content": repair_prompt}]
                    }
                )
            repair_data = repair_resp.json()
            repaired = next((b for b in repair_data.get("content", []) if b.get("type") == "text"), None)
            if not repaired:
                raise
            repaired_raw = repaired["text"].strip()
            repaired_raw = re.sub(r"^```[a-z]*\n?", "", repaired_raw)
            repaired_raw = re.sub(r"\n?```$", "", repaired_raw)
            return json.loads(repaired_raw)


def chunk_text(text: str, size: int = 6000):
    cleaned = text.strip()
    return [cleaned[i:i + size] for i in range(0, len(cleaned), size)]


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


async def web_search(query: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}
            )
        data = resp.json()
        abstract = data.get("AbstractText", "")
        related = [r.get("Text", "") for r in data.get("RelatedTopics", [])[:3]
                   if isinstance(r, dict) and r.get("Text")]
        result = abstract if abstract else "。".join(related)
        return result or f"未找到关于「{query}」的具体信息"
    except Exception:
        return f"搜索「{query}」时出错"


SEARCH_TOOL = [{
    "name": "web_search",
    "description": "当对任务内容不清楚时（如书名、电影名、项目名、专业术语等），用此工具搜索背景信息，帮助给出更准确的第一步",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"}
        },
        "required": ["query"]
    }
}]


@app.post("/analyze-roles")
async def analyze_roles(req: AnalyzeRolesRequest):
    import json, re
    goal = req.longterm_goal.strip()
    if not goal:
        return {"error": "长期目标为空"}
    try:
        prompt = f"""用户的长期目标是：
{goal}

请分析这个长期目标背后适合长期扮演的具体角色。角色必须是具体身份、职业、创作身份或专业实践身份，例如：摄影师、写作者、导演、独立研究者、产品设计师、策展人。

要求：
1. 返回 3-5 个角色
2. 每个角色名字不超过 8 个字
3. 每个角色说明不超过 32 个字
4. 说明要强调这个角色如何帮助用户推进长期目标
5. 不要使用抽象人格词或泛管理身份，例如“推进者”“探索员”“执行官”“执行者”“项目管理者”“项目执行者”“项目制片主任”

只返回 JSON 数组，不要任何其他文字：
[{{"name": "角色名", "desc": "角色说明"}}]"""

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 800,
                    "messages": [{"role": "user", "content": prompt}]
                }
            )
        data = resp.json()
        text_block = next((b for b in data.get("content", []) if b.get("type") == "text"), None)
        if not text_block:
            raise Exception(f"Unexpected response: {data}")

        raw = text_block["text"].strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        banned_names = {"推进者", "探索员", "执行官", "执行者", "项目执行者", "项目管理者", "项目制片主任", "管理者"}
        roles = [
            role for role in json.loads(raw)
            if role.get("name") and role.get("desc") and role["name"] not in banned_names
        ]
        return {"roles": roles}
    except Exception as e:
        return {"error": str(e)}


@app.post("/daily-tasks")
async def daily_tasks(req: DailyTasksRequest):
    import json, re
    goal = req.longterm_goal.strip()
    main_quest = req.main_quest.strip()
    role = req.role.strip()
    if not goal and not main_quest:
        return {"error": "长期目标为空"}
    try:
        has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in f"{goal}{main_quest}")
        language_rule = "请用中文返回任务。" if has_cjk else "Return every task in English only."
        prompt = f"""用户的长期目标：
{goal or main_quest}

今日主线：
{main_quest or goal}

今日角色：
{role or "未设定"}

请把这个长期目标拆成今天可以完成的小任务。

要求：
1. 返回 3 个任务
2. 每个任务 15-45 分钟内能完成
3. 每个任务必须是具体动作，不要抽象建议
4. 任务要能推进长期目标，而不是泛泛自我管理
5. 不要包含“制定计划”这种空泛任务，除非任务具体到产出物
6. 每个任务不超过 28 个字
7. {language_rule}

只返回 JSON 数组，不要任何其他文字：
["小任务1", "小任务2", "小任务3"]"""

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 700,
                    "messages": [{"role": "user", "content": prompt}]
                }
            )
        data = resp.json()
        text_block = next((b for b in data.get("content", []) if b.get("type") == "text"), None)
        if not text_block:
            raise Exception(f"Unexpected response: {data}")

        raw = text_block["text"].strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        tasks = [task for task in json.loads(raw) if isinstance(task, str) and task.strip()]
        return {"tasks": tasks[:3]}
    except Exception as e:
        return {"error": str(e)}


@app.post("/creative-fishbone")
async def creative_fishbone(req: CreativeFishboneRequest):
    import json, re
    task = req.task.strip()
    thoughts = [thought.strip() for thought in req.thoughts if thought.strip()]
    if not task:
        return {"error": "创作任务为空"}
    try:
        thoughts_text = "\n".join(f"- {thought}" for thought in thoughts) or "- 暂无散乱想法"
        updates_text = "\n".join(f"- {item.strip()}" for item in req.updates if item.strip()) or "- 暂无更新"
        prompt = f"""你是一个故事编辑和故事结构可视化助手。用户不是在做普通任务管理，也不是在整理无关干扰；用户的 scattered mind 基本都围绕同一个创作任务，所有想法默认都服务于这个故事/作品。

作品名：
{req.work_title.strip() or "未命名作品"}

已有大纲：
{req.outline.strip() or "暂无大纲"}

历史 work update：
{updates_text}

创作任务：
{task}

用户倒出来的想法：
{thoughts_text}

请把这些想法整理成“故事地图”，帮助用户看见故事结构，而不是做任务分类。必须使用以下 7 个分支：
1. 故事核心：一句话故事、主题、角色欲望、作品为什么存在
2. 人物关系：角色、动机、关系、秘密、人物弧线
3. 世界/意象：地点、物件、画面、声音、氛围、符号
4. 事件顺序：开头、转折、场景、章节、因果链
5. 冲突张力：矛盾、危险、隐瞒、代价、对抗
6. 待决定：空白、疑问、尚未选择的方向
7. 下一场景：现在立刻能写的一幕或一个具体片段

要求：
- 不要泛泛鼓励
- 不要把想法改写得太官方，要保留用户原本的创作质感
- 每条 items 尽量短
- 如果某个分支没有明显材料，可以返回空数组
- next_action 必须是一个 2-10 分钟内能开始的具体写作/视觉化动作
- 尽量指出故事的“因果关系”和“张力位置”

只返回 JSON，不要任何其他文字：
{{
  "categories": [
    {{"id": "premise", "title": "故事核心", "hint": "一句话、主题、欲望", "items": []}},
    {{"id": "character", "title": "人物关系", "hint": "角色、动机、关系", "items": []}},
    {{"id": "world", "title": "世界/意象", "hint": "地点、物件、氛围", "items": []}},
    {{"id": "timeline", "title": "事件顺序", "hint": "开头、转折、场景", "items": []}},
    {{"id": "tension", "title": "冲突张力", "hint": "秘密、矛盾、危险", "items": []}},
    {{"id": "unknown", "title": "待决定", "hint": "问题、空白、选择", "items": []}},
    {{"id": "next", "title": "下一场景", "hint": "马上能写的一幕", "items": []}}
  ],
  "next_action": "一个具体下一步"
}}"""

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 1200,
                    "messages": [{"role": "user", "content": prompt}]
                }
            )
        data = resp.json()
        text_block = next((b for b in data.get("content", []) if b.get("type") == "text"), None)
        if not text_block:
            raise Exception(f"Unexpected response: {data}")

        raw = text_block["text"].strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        result = json.loads(raw)
        return result
    except Exception as e:
        return {"error": str(e)}


@app.post("/work-clarify")
async def work_clarify(req: WorkClarifyRequest):
    import json, re
    new_material = req.new_material.strip()
    if not new_material:
        return {"error": "新增想法为空"}
    try:
        updates_text = "\n".join(f"- {item.strip()}" for item in req.updates if item.strip()) or "- 暂无更新"
        prompt = f"""你是一个创作编辑和结构助手。用户正在做一个具体作品，所有新增想法都默认服务于这个作品。你的任务不是评价好坏，而是帮用户把新增想法放进已有作品结构里，理清它应该影响哪里。

作品名：
{req.work_title.strip() or "未命名作品"}

当前创作任务：
{req.current_task.strip() or "未指定"}

已有大纲：
{req.outline.strip() or "暂无大纲"}

历史 work update：
{updates_text}

这次新增想法：
{new_material}

请基于已有大纲和更新，整理这条新增想法。要求：
1. placement: 说明它最适合放在作品的哪里，例如人物、场景、章节、主题、某一幕、某条线索
2. impact: 说明它会改变/加强作品的什么
3. conflicts: 如果它和已有大纲有冲突或需要警惕，列出来；没有就空数组
4. outline_patch: 给出 2-5 条可以追加到大纲里的短句
5. next_action: 给一个 2-10 分钟内能做的创作动作
6. questions: 给 1-3 个真正有助于继续写的追问

只返回 JSON，不要任何其他文字：
{{
  "placement": "它应该放在哪里",
  "impact": "它影响什么",
  "conflicts": [],
  "outline_patch": [],
  "next_action": "具体下一步",
  "questions": []
}}"""

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 1200,
                    "messages": [{"role": "user", "content": prompt}]
                }
            )
        data = resp.json()
        text_block = next((b for b in data.get("content", []) if b.get("type") == "text"), None)
        if not text_block:
            raise Exception(f"Unexpected response: {data}")

        raw = text_block["text"].strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        return json.loads(raw)
    except Exception as e:
        return {"error": str(e)}


@app.post("/analyze-long-story")
async def analyze_long_story(req: LongStoryAnalyzeRequest):
    text = req.text.strip()
    if not text:
        return {"error": "文本为空"}
    if len(text) > 120000:
        return {"error": "文本过长，请先上传 12 万字以内的版本或分卷分析"}
    try:
        chunks = chunk_text(text, 6000)
        chunk_summaries = []
        for index, chunk in enumerate(chunks[:20]):
            prompt = f"""你是故事结构分析助手。下面是长篇作品《{req.work_title.strip() or "未命名作品"}》的第 {index + 1}/{len(chunks)} 段文本。

请只基于这一段提取故事结构信息，不要复述原文。

文本：
{chunk}

返回 JSON：
{{
  "events": ["这一段发生的关键事件，最多5条"],
  "characters": ["出现的人物及关系/动机变化，最多5条"],
  "world_images": ["地点、物件、意象、氛围，最多5条"],
  "tensions": ["冲突、秘密、危险、悬念，最多5条"],
  "questions": ["这一段留下的问题或伏笔，最多5条"]
}}"""
            chunk_summaries.append(await ask_claude_json(prompt, 1100))

        summaries_text = "\n".join(
            f"段落 {i + 1}: {summary}"
            for i, summary in enumerate(chunk_summaries)
        )
        final_prompt = f"""你是故事编辑。下面是长篇作品《{req.work_title.strip() or "未命名作品"}》分段提取出的结构信息。

当前分析目的：
{req.current_task.strip() or "拆解整体故事线"}

分段信息：
{summaries_text}

请汇总成一张“故事地图”，重点是帮助作者看清故事线，而不是复述全文。

返回 JSON：
{{
  "overview": "一句话概括故事主线",
  "storylines": [
    {{"name": "故事线名称", "beats": ["关键推进点"]}}
  ],
  "characters": [
    {{"name": "人物名", "role": "叙事功能/关系/欲望"}}
  ],
  "timeline": ["按顺序列出关键事件"],
  "tensions": ["主要冲突、秘密、悬念"],
  "motifs": ["反复出现的意象/主题"],
  "open_questions": ["仍未解决的问题"],
  "next_action": "作者接下来 2-10 分钟能做的具体动作"
}}"""
        result = await ask_claude_json(final_prompt, 1800)
        result["chunk_count"] = len(chunks)
        result["analyzed_chunk_count"] = len(chunk_summaries)
        return result
    except Exception as e:
        return {"error": str(e)}


@app.post("/character-fishbone")
async def character_fishbone(req: CharacterFishboneRequest):
    text = req.text.strip()
    if not text:
        return {"error": "文本为空"}
    if len(text) > 120000:
        return {"error": "文本过长，请先上传 12 万字以内的版本或分卷分析"}

    try:
        chunks = chunk_text(text, 6000)
        chunk_notes = []
        for index, chunk in enumerate(chunks[:20]):
            prompt = f"""你是故事编辑。下面是作品《{req.work_title.strip() or "未命名作品"}》的第 {index + 1}/{len(chunks)} 段文本。

目标：只提取人物信息，不要复述原文，不要长篇引用。

如果用户指定主角，请优先关注：
{req.focus_character.strip() or "未指定，自动判断最核心主角"}

文本：
{chunk}

返回 JSON：
{{
  "characters": [
    {{"name": "人物名", "role": "叙事功能", "traits": ["特点"], "experiences": ["经历/压力"], "desire": "想要什么"}}
  ],
  "main_character_clues": ["主角相关线索"],
  "writing_intent_clues": ["作者为什么这样写这个人物的线索"]
}}"""
            chunk_notes.append(await ask_claude_json(prompt, 1200))

        notes_text = "\n".join(
            f"段落 {i + 1}: {note}"
            for i, note in enumerate(chunk_notes)
        )
        final_prompt = f"""你是一个故事编辑和人物弧线分析助手。下面是作品《{req.work_title.strip() or "未命名作品"}》分段提取出的人物信息。

用户当前创作目的：
{req.current_task.strip() or "把正在阅读的作品变成创作参照"}

用户指定主角：
{req.focus_character.strip() or "未指定，请自动选择最核心主角"}

分段人物信息：
{notes_text}

请生成“人物鱼骨图”。重点不是剧情复述，而是让作者看清：主要人物是谁、主角是什么样的人、经历了什么、作者为什么这样写，以及这对自己的创作有什么提醒。

要求：
- 保持分析短、清楚、有创作价值
- 不要引用长段原文
- “写作意图”要从叙事功能推断：为什么让这个人物这样欲望、受伤、失败、转变
- “对我创作的提醒”必须能帮助用户回到自己的作品
- fishbone 必须是数组，每个分支 title + items

只返回 JSON：
{{
  "main_character": "主角名",
  "thesis": "一句话概括这个主角的核心",
  "major_characters": [
    {{"name": "人物名", "function": "这个人物在故事中做什么"}}
  ],
  "fishbone": [
    {{"title": "主角特点", "items": []}},
    {{"title": "核心欲望", "items": []}},
    {{"title": "经历/创伤", "items": []}},
    {{"title": "关系压力", "items": []}},
    {{"title": "转变轨迹", "items": []}},
    {{"title": "写作意图", "items": []}},
    {{"title": "对我创作的提醒", "items": []}}
  ],
  "return_move": "用户接下来 2-10 分钟能做的一个人物写作动作",
  "chunk_count": {len(chunks)},
  "analyzed_chunk_count": {len(chunk_notes)}
}}"""
        return await ask_claude_json(final_prompt, 1800)
    except Exception as e:
        return {"error": str(e)}


@app.post("/work-context/save")
async def save_work_context(req: WorkContextSaveRequest):
    openid = req.openid.strip()
    if not openid:
        return {"error": "openid 为空"}

    updated_at = datetime.now().isoformat()
    conn = sqlite3.connect("tasks.db")
    conn.execute(
        """
        INSERT INTO work_contexts (
            openid, work_title, outline, new_material, clarity_result,
            long_story_text, long_story_result, character_result, my_characters, updates, story_refs, current_task, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(openid) DO UPDATE SET
            work_title = excluded.work_title,
            outline = excluded.outline,
            new_material = excluded.new_material,
            clarity_result = excluded.clarity_result,
            long_story_text = excluded.long_story_text,
            long_story_result = excluded.long_story_result,
            character_result = excluded.character_result,
            my_characters = excluded.my_characters,
            updates = excluded.updates,
            story_refs = excluded.story_refs,
            current_task = excluded.current_task,
            updated_at = excluded.updated_at
        """,
        (
            openid,
            req.work_title,
            req.outline,
            req.new_material,
            json.dumps(req.clarity_result or {}, ensure_ascii=False),
            req.long_story_text,
            json.dumps(req.long_story_result or {}, ensure_ascii=False),
            json.dumps(req.character_result or {}, ensure_ascii=False),
            json.dumps(req.my_characters or [], ensure_ascii=False),
            json.dumps(req.updates or [], ensure_ascii=False),
            json.dumps(req.story_refs or [], ensure_ascii=False),
            req.current_task,
            updated_at,
        )
    )
    conn.commit()
    conn.close()
    return {"message": "作品档案已保存", "updated_at": updated_at}


@app.post("/work-context/load")
async def load_work_context(req: WorkContextLoadRequest):
    openid = req.openid.strip()
    if not openid:
        return {"error": "openid 为空"}

    conn = sqlite3.connect("tasks.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT work_title, outline, new_material, clarity_result,
               long_story_text, long_story_result, character_result, my_characters, updates, story_refs, current_task, updated_at
        FROM work_contexts
        WHERE openid = ?
        """,
        (openid,)
    ).fetchone()
    conn.close()

    if not row:
        return {"context": None}

    def parse_json_field(value, fallback):
        try:
            return json.loads(value) if value else fallback
        except json.JSONDecodeError:
            return fallback

    return {
        "context": {
            "work_title": row["work_title"] or "",
            "outline": row["outline"] or "",
            "new_material": row["new_material"] or "",
            "clarity_result": parse_json_field(row["clarity_result"], {}),
            "long_story_text": row["long_story_text"] or "",
            "long_story_result": parse_json_field(row["long_story_result"], {}),
            "character_result": parse_json_field(row["character_result"], {}),
            "my_characters": parse_json_field(row["my_characters"], []),
            "updates": parse_json_field(row["updates"], []),
            "story_refs": parse_json_field(row["story_refs"], []),
            "current_task": row["current_task"] or "",
            "updated_at": row["updated_at"] or "",
        }
    }


@app.post("/random-start")
async def random_start(req: RandomStartRequest):
    import json, re
    try:
        goals_text = "\n".join(f"- {g}" for g in req.goals)
        prompt = f"""我有以下几件想做的事：
{goals_text}

请为每件事给出一个具体的第一步，要求：
1. 只需要10-20分钟就能完成
2. 简单到不需要准备，立刻就能开始
3. 一句话说清楚，不超过20个字
4. 不要拆成多步，就一个动作

如果对某个任务不了解（例如是书名、电影、专业词汇），请先用 web_search 工具查询。

只返回一个JSON数组，不要任何其他文字：
[{{"goal": "原始目标", "first_step": "具体第一步"}}]"""

        messages = [{"role": "user", "content": prompt}]

        for _ in range(4):
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    },
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": 1000,
                        "tools": SEARCH_TOOL,
                        "messages": messages
                    }
                )
            data = resp.json()

            if data.get("stop_reason") == "tool_use":
                tool_block = next((b for b in data["content"] if b.get("type") == "tool_use"), None)
                if tool_block and tool_block["name"] == "web_search":
                    search_result = await web_search(tool_block["input"]["query"])
                    messages.append({"role": "assistant", "content": data["content"]})
                    messages.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": tool_block["id"],
                            "content": search_result
                        }]
                    })
                    continue

            text_block = next((b for b in data["content"] if b.get("type") == "text"), None)
            if not text_block:
                raise Exception(f"Unexpected response: {data}")

            raw = text_block["text"].strip()
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            steps = json.loads(raw)
            picked = random.choice(steps)

            remind_at = (datetime.now() + timedelta(minutes=30)).isoformat()
            conn = sqlite3.connect("tasks.db")
            conn.execute(
                "INSERT INTO reminders (openid, tasks, remind_at) VALUES (?, ?, ?)",
                (req.openid, picked["first_step"], remind_at)
            )
            conn.commit()
            conn.close()

            return {"goal": picked["goal"], "first_step": picked["first_step"]}

        raise Exception("查询次数超限，请重试")
    except Exception as e:
        return {"error": str(e)}


@app.post("/focus-breakdown")
async def focus_breakdown(req: FocusBreakdownRequest):
    import json, re
    if not req.tasks:
        return {"error": "任务列表为空"}
    try:
        goals_text = "\n".join(f"- {t}" for t in req.tasks)
        prompt = f"""我今天计划专注完成以下任务：
{goals_text}

请为每个任务给出一个具体的第一步，要求：
1. 只需要10-20分钟就能完成
2. 简单到立刻就能开始，不需要任何准备
3. 一句话说清楚，不超过20个字
4. 就一个动作，不要拆成多步

如果对某个任务不了解（书名、电影、专业词汇等），请先用 web_search 工具查询。

只返回一个JSON数组，不要任何其他文字：
[{{"task": "原始任务名", "first_step": "具体第一步"}}]"""

        messages = [{"role": "user", "content": prompt}]

        for _ in range(4):
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    },
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": 1000,
                        "tools": SEARCH_TOOL,
                        "messages": messages
                    }
                )
            data = resp.json()

            if data.get("stop_reason") == "tool_use":
                tool_block = next((b for b in data["content"] if b.get("type") == "tool_use"), None)
                if tool_block and tool_block["name"] == "web_search":
                    search_result = await web_search(tool_block["input"]["query"])
                    messages.append({"role": "assistant", "content": data["content"]})
                    messages.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": tool_block["id"],
                            "content": search_result
                        }]
                    })
                    continue

            text_block = next((b for b in data["content"] if b.get("type") == "text"), None)
            if not text_block:
                raise Exception(f"Unexpected response: {data}")

            raw = text_block["text"].strip()
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            steps = json.loads(raw)
            return {"steps": steps}

        raise Exception("查询次数超限，请重试")
    except Exception as e:
        return {"error": str(e)}


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
