"""CLI 入口：`uv run python -m ai_signuper grok --count 1 --sink txt`"""

from __future__ import annotations

import argparse
import os
import time

from dotenv import load_dotenv

from .providers.base import Provider
from .providers.grok import GrokProvider
from .runtime import (
    DrissionBrowserSession,
    build_chromium_options,
    ensure_stable_python_runtime,
    warn_runtime_compatibility,
)
from .sinks.base import Sink
from .sinks.sub2api import Sub2ApiSink
from .sinks.txt_file import TxtFileSink

PROVIDERS: dict[str, type[Provider]] = {
    "grok": GrokProvider,
}


def _make_sink(args: argparse.Namespace) -> Sink:
    if args.sink == "txt":
        return TxtFileSink(args.output)

    if args.sink == "sub2api":
        base_url = os.environ.get("SUB2API_BASE_URL", "")
        api_key = os.environ.get("SUB2API_ADMIN_API_KEY", "")
        groups_raw = os.environ.get("SUB2API_DEFAULT_GROUP_IDS", "")
        group_ids = [int(x) for x in groups_raw.split(",") if x.strip()]
        return Sub2ApiSink(
            base_url=base_url,
            api_key=api_key,
            default_group_ids=group_ids,
            batch_size=args.batch_size,
        )

    raise ValueError(f"unknown sink: {args.sink}")


def main() -> None:
    ensure_stable_python_runtime()
    warn_runtime_compatibility()
    load_dotenv()

    parser = argparse.ArgumentParser(description="AI 服务自动注册机")
    parser.add_argument("provider", choices=list(PROVIDERS), help="目标 AI 服务（如 grok）")
    parser.add_argument("--count", type=int, default=0, help="执行轮数，0 表示无限循环")
    parser.add_argument("--sink", choices=["txt", "sub2api"], default="txt", help="产物下游")
    parser.add_argument("--output", default="output/sso.txt", help="txt sink 输出文件")
    parser.add_argument("--batch-size", type=int, default=1, help="sub2api sink 批次大小")
    args = parser.parse_args()

    provider_cls = PROVIDERS[args.provider]
    provider = provider_cls()
    sink = _make_sink(args)

    session = DrissionBrowserSession(build_chromium_options(provider.chrome_lang))
    session.start()

    rounds_done = 0
    try:
        while True:
            if args.count > 0 and rounds_done >= args.count:
                break
            rounds_done += 1
            print(f"\n[*] 开始第 {rounds_done} 轮注册（provider={provider.name}）")
            try:
                result = provider.run_round(session)
                sink.push(provider.name, result)
                print(f"[*] 本轮注册完成，邮箱: {result['email']}")
            except KeyboardInterrupt:
                print("\n[Info] 收到中断信号，停止后续轮次。")
                break
            except Exception as error:
                print(f"[Error] 第 {rounds_done} 轮失败: {error}")
            finally:
                session.restart()

            if args.count == 0 or rounds_done < args.count:
                time.sleep(2)

        sink.flush()
    finally:
        session.stop()


if __name__ == "__main__":
    main()
