# 群聊词云 astrbot_plugin_wordcloud

<p align="center">
  <img src="logo.png" alt="群聊词云" width="256"/>
</p>

> ⚠️ **本插件仍在开发中**，功能与配置可能变动，可能存在 Bug。遇到问题欢迎在 [Issues](https://github.com/xiaowan138/astrbot_plugin_wordcloud/issues) 中反馈。

一个 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件：持续记录群聊文本消息，一条指令把最近的群聊内容渲染成词云图，附带水群排行榜。

所有数据仅保存在机器人本地的 SQLite 数据库（`data/plugin_data/astrbot_plugin_wordcloud/`），不会上传到任何第三方服务。

## 功能

- 📊 **词云图**：统计最近 N 天的群聊消息，jieba 分词 + 停用词过滤后生成词云图片，附带消息量、参与人数和高频词摘要
- 🏆 **水群排行榜**：统计群成员发言条数与占比，前三名带奖牌
- 📈 **记录状态**：查看本群已记录的消息数、成员数、记录区间
- 🧹 **隐私保护**：管理员可一键清除本群数据；支持按天数自动过期清理
- ⚙️ **可配置**：字体、画布尺寸、背景色、配色方案、词数上限、额外停用词等

## 指令

| 指令 | 说明 |
| --- | --- |
| `/词云 [天数]` | 生成词云图，默认 7 天，支持 `/词云 今日`、`/词云 30` |
| `/水群排行 [天数] [名额]` | 水群排行榜，如 `/水群排行 7 10` |
| `/词云状态` | 查看本群消息记录情况 |
| `/词云清除` | （仅 AstrBot 管理员）清除本群已记录的数据 |
| `/词云帮助` | 查看帮助 |

别名：`wordcloud`、`水群排名`、`wordcloud_rank` 等。

## 安装

1. 将本文件夹（`astrbot_plugin_wordcloud`）放入 AstrBot 的 `data/plugins/` 目录（或通过 WebUI「插件管理 → 从目录安装 / 上传安装」）。
2. 安装 Python 依赖（在 AstrBot 所用的 Python 环境中执行）：

   ```bash
   pip install -r data/plugins/astrbot_plugin_wordcloud/requirements.txt
   ```

   依赖为 `jieba`（中文分词）与 `wordcloud`（词云渲染，自带 numpy/Pillow）。
3. 在 WebUI 插件管理中载入/重载插件。

## 配置

插件加载后可在 WebUI 插件配置页修改，常用项：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enable_record` | `true` | 是否记录群聊消息 |
| `keep_days` | `90` | 消息保留天数，0 为永久 |
| `days_default` | `7` | `/词云` 默认统计天数 |
| `top_n` | `100` | 词云最多显示词数 |
| `min_word_len` | `2` | 中文词最短长度 |
| `extra_stopwords` | `[]` | 额外停用词 |
| `font_path` | 空 | 中文字体路径，留空自动探测 |
| `background_color` | `white` | 背景色，如 `#1a1a2e` |
| `colormap` | `viridis` | matplotlib 配色方案 |

## 字体说明

词云需要中文字体。插件按以下顺序自动查找：

1. 配置项 `font_path` 指定的字体文件；
2. 数据目录 `data/plugin_data/astrbot_plugin_wordcloud/fonts/` 下的任意 `.ttf/.ttc/.otf`；
3. 系统常见字体（Windows 微软雅黑/黑体、macOS 苹方、Linux 文泉驿/Noto CJK）。

如果三个位置都找不到，`/词云` 会回复提示，此时请手动指定 `font_path` 或往 `fonts/` 文件夹丢一个中文字体。

## 常见问题

- **Linux 服务器生成失败提示找不到字体**：安装 `fonts-wqy-microhei`（Debian/Ubuntu：`apt install fonts-wqy-microhei`）或把任意中文 `.ttf` 放入 `fonts/` 目录。
- **不想让某些词出现在词云里**：把词加进配置的 `extra_stopwords`。
- **换肤**：深色群名片风格可设置 `background_color` 为 `#111827`、`colormap` 为 `rainbow`。

## 数据与隐私

- 消息原文（发送者 ID、昵称、文本内容、时间）存储于本地 SQLite：`data/plugin_data/astrbot_plugin_wordcloud/messages.db`，仅用于本插件统计。
- 默认保留 90 天，可在配置中调整或设为 0（永久）。
- 群成员可用 `/词云清除`（管理员）清空本群数据；关闭 `enable_record` 后停止记录。

## 目录结构

```
astrbot_plugin_wordcloud/
├── main.py              # 插件主体
├── metadata.yaml        # 插件元数据
├── _conf_schema.json    # WebUI 配置模板
├── requirements.txt     # Python 依赖
├── logo.png             # 插件封面
└── README.md
```
