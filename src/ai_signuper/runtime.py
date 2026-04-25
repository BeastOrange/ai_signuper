"""运行期工具：Python 解释器守卫 + Chromium 浏览器会话封装。"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from DrissionPage import Chromium, ChromiumOptions
from DrissionPage.errors import PageDisconnectedError

# 项目根目录（两级上：src/ai_signuper/runtime.py → 项目根）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TURNSTILE_EXTENSION_PATH = str(PROJECT_ROOT / "turnstilePatch")


def ensure_stable_python_runtime():
    """优先自动切到更稳定的 3.12 / 3.13，避免 3.14 下 Mail.tm 偶发 TLS/兼容问题。"""
    if sys.version_info < (3, 14) or os.environ.get("AI_SIGNUPER_REEXEC_DONE") == "1":
        return

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(local_app_data, "Programs", "Python", "Python312", "python.exe"),
        os.path.join(local_app_data, "Programs", "Python", "Python313", "python.exe"),
    ]

    current_python = os.path.normcase(os.path.abspath(sys.executable))
    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        if os.path.normcase(os.path.abspath(candidate)) == current_python:
            return

        print(f"[*] 检测到 Python {sys.version.split()[0]}，自动切换到更稳定的解释器: {candidate}")
        env = os.environ.copy()
        env["AI_SIGNUPER_REEXEC_DONE"] = "1"
        os.execve(candidate, [candidate, *sys.argv], env)


def warn_runtime_compatibility():
    if sys.version_info >= (3, 14):
        print("[提示] 当前 Python 为 3.14+；若出现 Mail.tm TLS 异常，建议改用 Python 3.12 或 3.13。")


def build_chromium_options(lang: str = "zh-CN") -> ChromiumOptions:
    """构造启动参数：自动端口、turnstilePatch 扩展、强制语言。

    `lang` 决定 navigator.language / Accept-Language。x.ai 按浏览器语言渲染按钮文案，
    Provider 通过 chrome_lang 字段传它需要的区域（避免按钮匹配字符串错位）。
    """
    co = ChromiumOptions()
    co.auto_port()
    co.set_timeouts(base=1)
    co.set_argument(f"--lang={lang}")
    # Accept-Language 给一份带后备的列表，避免某些页面对 navigator.language 单值过敏
    accept_languages = f"{lang},{lang.split('-')[0]};q=0.9,en;q=0.8"
    co.set_pref("intl.accept_languages", accept_languages)
    co.add_extension(TURNSTILE_EXTENSION_PATH)
    return co


class DrissionBrowserSession:
    """封装 DrissionPage 的 Chromium 实例 + 当前活动 tab。

    Provider 拿到 session 后通过 `.page` 操作页面，通过 `.refresh_page()` 在跳转后
    重新获取活动 tab（旧句柄 PageDisconnectedError 时使用）。
    """

    def __init__(self, options: ChromiumOptions):
        self._options = options
        self._browser: Chromium | None = None
        self._page = None

    def start(self):
        self._browser = Chromium(self._options)
        tabs = self._browser.get_tabs()
        self._page = tabs[-1] if tabs else self._browser.new_tab()
        return self

    def stop(self):
        if self._browser is not None:
            try:
                self._browser.quit()
            except Exception:
                pass
        self._browser = None
        self._page = None

    def restart(self):
        """每轮结束都重启整个浏览器实例，避免长时间复用造成的页面/Cookie 污染。"""
        self.stop()
        self.start()

    @property
    def page(self):
        if self._page is None:
            raise RuntimeError("BrowserSession 未启动；先调用 .start()")
        return self._page

    @property
    def browser(self):
        return self._browser

    def refresh_page(self):
        """验证码确认后页面会跳转，旧 page 句柄可能断开，统一重新获取当前活动 tab。"""
        if self._browser is None:
            self.start()
            return self._page
        try:
            tabs = self._browser.get_tabs()
            self._page = tabs[-1] if tabs else self._browser.new_tab()
        except Exception:
            self.restart()
        return self._page

    def open_url(self, url: str):
        self.refresh_page()
        try:
            self._page.get(url)
        except Exception:
            self.refresh_page()
            self._page = self._browser.new_tab(url)
        return self._page


def wait_for_cookie(session: DrissionBrowserSession, cookie_name: str, timeout: int = 120) -> str:
    """注册完成后等待指定 cookie 出现并返回其值。"""
    deadline = time.time() + timeout
    last_seen_names: set[str] = set()

    while time.time() < deadline:
        try:
            session.refresh_page()
            page = session.page
            if page is None:
                time.sleep(1)
                continue

            cookies = page.cookies(all_domains=True, all_info=True) or []
            for item in cookies:
                if isinstance(item, dict):
                    name = str(item.get("name", "")).strip()
                    value = str(item.get("value", "")).strip()
                else:
                    name = str(getattr(item, "name", "")).strip()
                    value = str(getattr(item, "value", "")).strip()

                if name:
                    last_seen_names.add(name)

                if name == cookie_name and value:
                    print(f"[*] 注册完成后已获取到 {cookie_name} cookie。")
                    return value
        except PageDisconnectedError:
            session.refresh_page()
        except Exception:
            pass

        time.sleep(1)

    raise Exception(f"注册完成后未获取到 {cookie_name} cookie，当前已见 cookie: {sorted(last_seen_names)}")
