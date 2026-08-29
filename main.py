# -*- coding: utf-8 -*-
"""AstrBot 插件：群聊词云

监听群聊文本消息并存入本地 SQLite，按需生成词云图与水群排行榜。
消息只保存在本机（data/plugin_data/astrbot_plugin_wordcloud/），不会上传任何第三方。
"""

import asyncio
import logging
import random
import re
import sqlite3
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

try:
    import jieba
    from wordcloud import WordCloud

    DEPS_READY = True
except ImportError:
    # 依赖缺失时插件仍可加载，指令会给出安装提示，不影响 AstrBot 其他功能
    DEPS_READY = False

PLUGIN_NAME = "astrbot_plugin_wordcloud"

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_RE = re.compile(r"[a-zA-Z]{2,}")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_CQ_RE = re.compile(r"\[CQ:[^\]]*\]")
# 消息链占位符：[表情:x]、[图片]、[At:123] 等，统一剔除
_PLACEHOLDER_RE = re.compile(
    r"\[(?:CQ:[^\]]*|At[^\]]*|表情[^\]]*|图片[^\]]*|动画表情[^\]]*"
    r"|文件[^\]]*|语音[^\]]*|视频[^\]]*|转发消息[^\]]*|引用消息[^\]]*)\]"
)
_AT_PREFIX_RE = re.compile(r"^\s*@\S+\s+")
# 「哈哈哈哈哈哈」这类刷屏叠字压缩成两连字，避免词云被单字重复刷屏
_REPEAT_CJK_RE = re.compile(r"([\u4e00-\u9fff])\1{2,}")
_UNSAFE_FILENAME_RE = re.compile(r"[^\w-]+")

# 常见中文字体候选路径（按顺序探测，Windows -> macOS -> Linux）
FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/wenquanyi/wqy-microhei/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
]

# 内置停用词：高频无实义词 + 群聊灌水常用词
BUILTIN_STOPWORDS = {
    "的", "了", "是", "我", "你", "他", "她", "它", "我们", "你们", "他们", "您",
    "吗", "呢", "吧", "啊", "呀", "哦", "噢", "嗯", "哈", "哈哈", "嘿嘿", "嘻嘻",
    "呵", "嘛", "么", "什么", "怎么", "怎样", "哪", "哪个", "哪些", "哪里", "谁",
    "这", "那", "这个", "那个", "这些", "那些", "这样", "那样", "这里", "那里",
    "和", "与", "或", "及", "跟", "就", "都", "也", "很", "还", "有", "没", "不",
    "在", "又", "再", "才", "只", "被", "把", "让", "给", "对", "向", "往", "从",
    "到", "于", "用", "上", "下", "里", "外", "中", "前", "后", "左", "右",
    "一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "个", "些",
    "为", "因为", "所以", "但是", "可是", "然后", "接着", "于是", "如果", "虽然",
    "还是", "或者", "而且", "并且", "不过", "只是", "就是", "真是", "真是的",
    "现在", "今天", "明天", "昨天", "时候", "时间", "地方", "东西", "事情", "问题",
    "觉得", "感觉", "知道", "以为", "认为", "应该", "可能", "也许", "大概", "居然",
    "已经", "还要", "需要", "可以", "能够", "不会", "不能", "没有", "不是", "自己",
    "真的", "真的吗",
    "大家", "各位", "兄弟", "姐妹", "老哥", "老铁", "朋友", "哥们", "大佬", "萌新",
    "起来", "出来", "过来", "一下", "有点", "一些", "一点", "这么", "那么",
    "直接", "反正", "突然", "其实", "确实", "根本", "完全", "终于", "赶紧", "马上",
    "emmm", "emm", "aaa", "hhh", "qq", "at", "jpg", "png", "http", "com", "cn",
}


class MissingFontError(Exception):
    """未找到可渲染中文的字体。"""


@register(PLUGIN_NAME, "dsh", "统计群聊消息，生成词云图与水群排行榜", "1.0.0")
class WordCloudStar(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        try:
            self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        except Exception:
            # 兜底：直接使用 AstrBot 数据根目录下的规范路径
            self.data_dir = Path("data") / "plugin_data" / PLUGIN_NAME
            self.data_dir.mkdir(parents=True, exist_ok=True)
        self.fonts_dir = self.data_dir / "fonts"

        self._load_config()
        self.stopwords = set(BUILTIN_STOPWORDS)
        self._collect_extra_stopwords()
        self.conn: sqlite3.Connection | None = None
        self._init_db()

        if DEPS_READY:
            try:
                jieba.setLogLevel(logging.WARNING)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 初始化与数据库
    # ------------------------------------------------------------------

    async def initialize(self):
        """插件加载完成后的异步初始化：清理过期数据与旧图片。"""
        await asyncio.to_thread(self._cleanup)

    def _load_config(self):
        self.days_default = self._as_int("days_default", 7, 1, 365)
        self.days_max = self._as_int("days_max", 90, 1, 3650)
        self.keep_days = self._as_int("keep_days", 90, 0, 36500)
        self.top_n = self._as_int("top_n", 100, 10, 500)
        self.min_word_len = self._as_int("min_word_len", 2, 1, 10)
        self.min_messages = self._as_int("min_messages", 10, 1, 100000)
        self.width = self._as_int("width", 1000, 200, 4096)
        self.height = self._as_int("height", 600, 200, 4096)
        self.background_color = str(self.config.get("background_color", "white") or "white")
        self.colormap = str(self.config.get("colormap", "viridis") or "viridis")
        self.font_path = str(self.config.get("font_path", "") or "").strip()

    def _as_int(self, key: str, default: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(hi, int(self.config.get(key, default))))
        except (TypeError, ValueError):
            return default

    def _collect_extra_stopwords(self):
        extra = self.config.get("extra_stopwords", []) or []
        if isinstance(extra, str):
            parts = re.split(r"[,，\s]+", extra)
        else:
            parts = extra
        for w in parts:
            w = str(w).strip()
            if w:
                self.stopwords.add(w)

    def _init_db(self):
        try:
            self.conn = sqlite3.connect(
                str(self.data_dir / "messages.db"), check_same_thread=False
            )
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    nickname TEXT,
                    content TEXT NOT NULL,
                    ts INTEGER NOT NULL
                )
                """
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_group_ts ON messages(group_id, ts)"
            )
            self.conn.commit()
        except Exception as e:
            self.conn = None
            logger.error(f"[词云] 初始化数据库失败: {e}")

    def _cleanup(self):
        """按 keep_days 清理过期消息，并删除 24 小时前的临时词云图片。"""
        if self.conn is None:
            return
        try:
            if self.keep_days > 0:
                cutoff = int(time.time()) - self.keep_days * 86400
                self.conn.execute("DELETE FROM messages WHERE ts < ?", (cutoff,))
                self.conn.commit()
            now = time.time()
            for f in self.data_dir.glob("wordcloud_*.png"):
                try:
                    if now - f.stat().st_mtime > 86400:
                        f.unlink()
                except OSError:
                    pass
        except Exception as e:
            logger.error(f"[词云] 清理过期数据失败: {e}")

    # ------------------------------------------------------------------
    # 消息记录（被动监听）
    # ------------------------------------------------------------------

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """(内部) 监听并记录群聊文本消息，用于词云统计"""
        if self.conn is None:
            return
        try:
            if not self.config.get("enable_record", True):
                return
            sender_id = event.get_sender_id()
            if not sender_id or sender_id == event.get_self_id():
                return
            raw = event.message_str or ""
            if raw.strip().startswith("/"):
                return
            content = self._clean_text(raw)
            if not content:
                return
            group_id = event.get_group_id()
            if not group_id:
                return
            nickname = (event.get_sender_name() or "")[:64]
            self.conn.execute(
                "INSERT INTO messages (group_id, user_id, nickname, content, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (group_id, sender_id, nickname, content[:500], int(time.time())),
            )
            self.conn.commit()
        except Exception as e:
            logger.error(f"[词云] 记录消息失败: {e}")

    @staticmethod
    def _clean_text(text: str) -> str:
        text = _CQ_RE.sub(" ", text)
        text = _PLACEHOLDER_RE.sub(" ", text)
        text = _URL_RE.sub(" ", text)
        text = _AT_PREFIX_RE.sub(" ", text)
        text = _REPEAT_CJK_RE.sub(r"\1\1", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    # ------------------------------------------------------------------
    # 指令：/词云
    # ------------------------------------------------------------------

    @filter.command("词云", alias={"wordcloud"})
    async def wordcloud_cmd(self, event: AstrMessageEvent, days: str = ""):
        """生成群聊词云图，可指定统计天数，如：/词云 7、/词云 30、/词云 今日"""
        if not DEPS_READY:
            yield event.plain_result(
                "词云插件缺少依赖库，请在 AstrBot 的 Python 环境中执行：pip install jieba wordcloud"
            )
            return
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("词云统计仅支持在群聊中使用～")
            return
        n, err = self._parse_days(days)
        if err:
            yield event.plain_result(err)
            return

        yield event.plain_result(f"正在统计本群最近 {n} 天的消息，请稍候…")
        try:
            freq, total, users = await asyncio.to_thread(
                self._collect_words, group_id, n
            )
        except Exception as e:
            logger.error(f"[词云] 统计消息失败: {e}")
            yield event.plain_result("统计失败啦，详情请查看后台日志。")
            return

        if total < self.min_messages:
            yield event.plain_result(
                f"最近 {n} 天只记录到 {total} 条消息（至少需要 {self.min_messages} 条），样本太少啦～"
            )
            return
        if not freq:
            yield event.plain_result(
                "没有统计到有效词汇，可能都被停用词过滤掉了，可在插件配置中调整。"
            )
            return

        try:
            path, summary = await asyncio.to_thread(
                self._render, freq, group_id, n, total, users
            )
        except MissingFontError as e:
            yield event.plain_result(str(e))
            return
        except Exception as e:
            logger.error(f"[词云] 生成图片失败: {e}")
            yield event.plain_result("生成图片失败啦，详情请查看后台日志。")
            return

        from astrbot.api.message_components import Image, Plain

        yield event.chain_result([Image.fromFileSystem(str(path)), Plain(summary)])

    # ------------------------------------------------------------------
    # 指令：/水群排行
    # ------------------------------------------------------------------

    @filter.command("水群排行", alias={"水群排名", "wordcloud_rank"})
    async def rank_cmd(self, event: AstrMessageEvent, days: str = "", top: str = ""):
        """查看本群水群排行榜，如：/水群排行 7 10（天数 名额）"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("水群排行仅支持在群聊中使用～")
            return
        n, err = self._parse_days(days)
        if err:
            yield event.plain_result(err)
            return
        try:
            top_n = max(1, min(20, int(top))) if top.strip() else 10
        except ValueError:
            top_n = 10

        try:
            rows, total = await asyncio.to_thread(
                self._query_rank, group_id, n, top_n
            )
        except Exception as e:
            logger.error(f"[词云] 查询排行失败: {e}")
            yield event.plain_result("查询失败啦，详情请查看后台日志。")
            return

        if not rows or total == 0:
            yield event.plain_result(f"最近 {n} 天还没有记录到消息哦～")
            return

        medals = ["🥇", "🥈", "🥉"]
        lines = [f"🏆 本群最近 {n} 天水群排行 Top {len(rows)}（共 {total} 条消息）"]
        for i, (user_id, nickname, cnt) in enumerate(rows, start=1):
            name = nickname or user_id
            pct = cnt / total * 100
            prefix = medals[i - 1] if i <= 3 else f"{i}."
            lines.append(f"{prefix} {name}：{cnt} 条（{pct:.1f}%）")
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------
    # 指令：/词云状态
    # ------------------------------------------------------------------

    @filter.command("词云状态", alias={"wordcloud_status"})
    async def status_cmd(self, event: AstrMessageEvent):
        """查看本群词云消息的记录状态"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("该指令仅支持在群聊中使用～")
            return
        if self.conn is None:
            yield event.plain_result("数据库不可用，请查看后台日志。")
            return
        try:
            row = await asyncio.to_thread(
                self._query_status, group_id
            )
        except Exception as e:
            logger.error(f"[词云] 查询状态失败: {e}")
            yield event.plain_result("查询失败啦，详情请查看后台日志。")
            return
        total, users, first_ts, last_ts = row
        enabled = bool(self.config.get("enable_record", True))
        lines = [
            "📊 词云数据状态",
            f"记录开关：{'开启' if enabled else '关闭'}",
            f"本群已记录：{total} 条消息，{users} 位成员",
        ]
        if first_ts and last_ts:
            span_days = max(1, (last_ts - first_ts + 86399) // 86400)
            lines.append(
                f"记录区间：{datetime.fromtimestamp(first_ts):%Y-%m-%d} ~ "
                f"{datetime.fromtimestamp(last_ts):%Y-%m-%d}（约 {span_days} 天）"
            )
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------
    # 指令：/词云清除（管理员）
    # ------------------------------------------------------------------

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("词云清除", alias={"wordcloud_clear"})
    async def clear_cmd(self, event: AstrMessageEvent):
        """（仅 AstrBot 管理员）清除本群已记录的词云数据"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("该指令仅支持在群聊中使用～")
            return
        if self.conn is None:
            yield event.plain_result("数据库不可用，请查看后台日志。")
            return
        try:
            deleted = await asyncio.to_thread(self._delete_group, group_id)
        except Exception as e:
            logger.error(f"[词云] 清除数据失败: {e}")
            yield event.plain_result("清除失败啦，详情请查看后台日志。")
            return
        yield event.plain_result(f"已清除本群 {deleted} 条消息记录，之后将重新开始统计。")

    # ------------------------------------------------------------------
    # 指令：/词云帮助
    # ------------------------------------------------------------------

    @filter.command("词云帮助", alias={"wordcloud_help"})
    async def help_cmd(self, event: AstrMessageEvent):
        """查看词云插件的使用帮助"""
        text = (
            "📖 群聊词云插件\n"
            "/词云 [天数] —— 生成词云图（默认 7 天，支持「今日」）\n"
            "/水群排行 [天数] [名额] —— 查看水群排行榜\n"
            "/词云状态 —— 查看本群数据记录情况\n"
            "/词云清除 —— 管理员清除本群数据（隐私保护）\n"
            f"提示：消息将保存在机器人本地数据库，保留 {self.keep_days if self.keep_days > 0 else '永久'} 天。"
        )
        yield event.plain_result(text)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _parse_days(self, days: str) -> tuple[int, str]:
        """解析天数参数，返回 (天数, 错误信息)。"""
        days = (days or "").strip()
        if days in ("今日", "今天", "today"):
            return 1, ""
        if not days:
            return self.days_default, ""
        try:
            n = int(days)
        except ValueError:
            return 0, "参数格式：/词云 天数，例如 /词云 30"
        if n < 1:
            return 0, "天数至少为 1 哦"
        return min(n, self.days_max), ""

    def _collect_words(self, group_id: str, days: int):
        """读取时间窗内的消息并统计词频，返回 (词频表, 消息数, 人数)。"""
        since = int(time.time()) - days * 86400
        total, users = self.conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT user_id) FROM messages "
            "WHERE group_id = ? AND ts >= ?",
            (group_id, since),
        ).fetchone()
        if total == 0:
            return Counter(), 0, 0
        freq: Counter = Counter()
        for (content,) in self.conn.execute(
            "SELECT content FROM messages WHERE group_id = ? AND ts >= ?",
            (group_id, since),
        ):
            freq.update(self._extract_words(content))
        return freq, total, users

    def _extract_words(self, text: str) -> list[str]:
        """分词：中文走 jieba，英文单词走正则，均做停用词与长度过滤。"""
        words: list[str] = []
        for tok in jieba.lcut(text):
            tok = tok.strip()
            if not tok:
                continue
            if _CJK_RE.fullmatch(tok):
                if len(tok) >= self.min_word_len and tok not in self.stopwords:
                    words.append(tok)
            elif _LATIN_RE.fullmatch(tok):
                w = tok.lower()
                if w not in self.stopwords:
                    words.append(w)
        return words

    def _resolve_font(self) -> Path | None:
        """按 配置路径 -> 数据目录 fonts/ -> 系统常见路径 的顺序查找字体。"""
        if self.font_path and Path(self.font_path).is_file():
            return Path(self.font_path)
        if self.fonts_dir.is_dir():
            for pattern in ("*.ttf", "*.ttc", "*.otf"):
                for f in self.fonts_dir.glob(pattern):
                    return f
        for p in FONT_CANDIDATES:
            if Path(p).is_file():
                return Path(p)
        return None

    def _render(self, freq: Counter, group_id: str, days: int, total: int, users: int):
        """渲染词云图，返回 (图片路径, 摘要文本)。"""
        font = self._resolve_font()
        if font is None:
            raise MissingFontError(
                "未找到可用的中文字体，请二选一：\n"
                f"1. 在插件配置 font_path 中填入 .ttf/.ttc 字体文件的完整路径；\n"
                f"2. 把任意中文字体文件放入：{self.fonts_dir}"
            )

        wc = WordCloud(
            font_path=str(font),
            width=self.width,
            height=self.height,
            background_color=self.background_color,
            max_words=self.top_n,
            colormap=self.colormap,
            prefer_horizontal=0.9,
            collocations=False,  # 词频已由 jieba 统计，避免 wordcloud 二次合并
            relative_scaling=0.35,
            margin=2,
            min_font_size=8,
            random_state=random.Random(),
        )
        wc.generate_from_frequencies(dict(freq))

        safe_group = _UNSAFE_FILENAME_RE.sub("_", group_id) or "unknown"
        out_path = self.data_dir / f"wordcloud_{safe_group}_{int(time.time())}.png"
        wc.to_file(str(out_path))

        top10 = "、".join(w for w, _ in list(freq.items())[:10])
        summary = (
            f"📊 最近 {days} 天 | {total} 条消息 | {users} 位成员参与\n"
            f"高频词：{top10}"
        )
        return out_path, summary

    def _query_rank(self, group_id: str, days: int, top_n: int):
        since = int(time.time()) - days * 86400
        rows = self.conn.execute(
            "SELECT user_id, MAX(nickname), COUNT(*) AS cnt FROM messages "
            "WHERE group_id = ? AND ts >= ? "
            "GROUP BY user_id ORDER BY cnt DESC LIMIT ?",
            (group_id, since, top_n),
        ).fetchall()
        total = self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE group_id = ? AND ts >= ?",
            (group_id, since),
        ).fetchone()[0]
        return rows, total

    def _query_status(self, group_id: str):
        row = self.conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT user_id), MIN(ts), MAX(ts) "
            "FROM messages WHERE group_id = ?",
            (group_id,),
        ).fetchone()
        total, users, first_ts, last_ts = row
        return total, users, first_ts or 0, last_ts or 0

    def _delete_group(self, group_id: str) -> int:
        cur = self.conn.execute("DELETE FROM messages WHERE group_id = ?", (group_id,))
        self.conn.commit()
        return cur.rowcount or 0

    async def terminate(self):
        """插件卸载/停用时关闭数据库连接。"""
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None
