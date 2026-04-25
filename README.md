# ai_signuper

AI 服务自动注册机框架。当前实现 Grok（xAI），并把产生的 sso JWT 灌入 [Sub2API](https://github.com/Wei-Shaw/sub2api) 当作可分发的上游账号。

## 快速开始

```bash
# 1. 装依赖（uv 管理；不要用 pip）
uv sync

# 2. 单轮试跑（产物默认落到 ./output/sso.txt）
uv run python -m ai_signuper grok --count 1

# 3. 长跑
uv run python -m ai_signuper grok --count 0          # 无限循环，Ctrl-C 停
uv run python -m ai_signuper grok --count 10         # 跑 10 轮
```

注册流程会打开一个**可见的** Chromium 窗口。Turnstile 需要真人化的鼠标轨迹，请把窗口留在前台、不要最小化。

## 灌入 Sub2API

部署 Sub2API（参见 `sub2api/README.md`），在管理后台 `/admin/settings` 生成 Admin API Key，复制到项目根 `.env`：

```bash
cp .env.example .env
# 填 SUB2API_BASE_URL 和 SUB2API_ADMIN_API_KEY
```

然后：

```bash
uv run python -m ai_signuper grok --count 1 --sink sub2api
```

注册成功的账号会以 `platform=grok, type=apikey, credentials.api_key=<sso jwt>` 的形态写入 Sub2API。批量入库失败会兜底落 `output/sso-failed.txt`，避免丢账号。

## 目录结构

```
src/ai_signuper/
  __main__.py        # CLI 入口
  runtime.py         # Chromium 启停 + Python 守卫
  mail_otp.py        # Mail.tm + 验证码（provider 共用）
  providers/
    base.py          # Provider 协议（实现新 provider 时实现它）
    grok.py          # Grok 注册流程
  sinks/
    base.py          # Sink 协议
    txt_file.py      # 兜底：append 到 sso.txt
    sub2api.py       # 灌入 Sub2API 管理 API
turnstilePatch/      # Cloudflare Turnstile 鼠标坐标 spoof 扩展
sub2api/             # vendored 的 Sub2API 网关（独立 .git，不要在里面 git push）
output/              # 运行产物
```

## 加一个新 provider

1. 在 `src/ai_signuper/providers/` 新建 `<name>.py`，写一个类实现 `Provider` 协议（见 `providers/base.py`）：`name / signup_url / chrome_lang / success_cookie_name` + `run_round(session)`。
2. 在 `__main__.py` 的 `PROVIDERS` 字典里注册它。
3. `chrome_lang` 决定页面渲染语言；如果你的 `run_round` 里写死了某种语言的按钮文本，就要用对应的 lang，否则按钮匹配会落空。
4. 凭证类型不一样时直接复用 sinks——sub2api sink 用 `provider.name` 当 platform，credentials 字段 hack 走 `apikey`。
5. 在 README 这一节加一条记录该 provider 的 sink 行为。

## 已知陷阱

详见 `CLAUDE.md`。摘要：

- **Python 必须 3.12 / 3.13**，3.14 上 Mail.tm TLS 偶发挂掉。`requires-python` 已经卡住。
- **Chrome 必须 zh-CN locale**：所有按钮匹配字符串是中文。`runtime.build_chromium_options` 已强制 `--lang=zh-CN`。
- **页面交互必须 JS 注入**：x.ai 是 React 受控表单，Python `.input()` 会让 React 内部状态不同步、按钮永远 disabled。providers/grok.py 里的 JS 块不要"简化"。
- **每轮重启浏览器**，不要复用 cookie / session（反检测）。
