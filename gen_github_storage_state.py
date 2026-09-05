#!/usr/bin/env python3
"""
本地交互式生成 GitHub storage state

从 secret/ACCOUNTS.secret.json 读取账号配置，逐个用 Camoufox 登录 GitHub：
- 若登录成功（无需验证或验证后成功），保存 Playwright storage state
- 全部输出汇总为 STORATE_STATES_GITHUB 环境变量所需的 JSON 格式：
    { "<github用户名>": <storage state JSON>, ... }

用法:
    uv run python gen_github_storage_state.py            # 处理所有账号
    uv run python gen_github_storage_state.py rfceu259   # 只处理指定账号

登录过程中如遇 2FA / 设备验证，请在弹出的浏览器窗口中手动完成（脚本会等待）。
"""

import asyncio
import json
import os
import sys

from camoufox.async_api import AsyncCamoufox

ACCOUNTS_FILE = "secret/ACCOUNTS.secret.json"
OUTPUT_FILE = "STORATE_STATES_GITHUB.local.json"
CACHE_DIR = "storage-states"


def load_github_accounts():
    with open(ACCOUNTS_FILE, encoding="utf-8") as f:
        accounts = json.load(f)
    seen = {}
    for acc in accounts:
        gh = acc.get("github")
        if not gh:
            continue
        username = gh.get("username")
        if username and username not in seen:
            seen[username] = gh
    return seen


def detect_state(page) -> str:
    url = page.url
    if "/sessions/verified-device" in url:
        return "device-verification"
    if "/two-factor" in url or "/sessions/two-factor" in url:
        return "2fa"
    if "/sessions/" in url:
        return "verification-other"
    if url.startswith("https://github.com/login"):
        return "login-page"
    if url.rstrip("/") == "https://github.com" or url.startswith("https://github.com/"):
        # github.com 页面：无法仅凭 URL 区分已登录/登录失败（POST /session 失败页
        # URL 也以 github.com/ 开头），调用方需用 is_logged_in 正面确认
        return "github-page"
    return "unknown"


async def is_logged_in(page) -> bool:
    """通过 meta[name=user-login] 正面确认当前 GitHub 页面处于登录状态"""
    try:
        v = await page.evaluate(
            "() => { const m = document.querySelector('meta[name=\"user-login\"]');"
            " return m ? m.getAttribute('content') : null; }"
        )
        return bool(v)
    except Exception:
        return False


async def login_account(username: str, password: str) -> tuple[bool, dict | None, str]:
    """登录单个账号，返回 (是否成功, storage state, 状态描述)"""
    cache_file = os.path.join(CACHE_DIR, f"local_{username}_storage_state.json")
    if os.path.exists(cache_file):
        print(f"✅ {username}: 已有本地缓存 {cache_file}，跳过登录")
        with open(cache_file, encoding="utf-8") as f:
            return True, json.load(f), "cached"

    print(f"\n{'=' * 50}\n🌐 正在登录 GitHub 账号: {username}")
    tmp_marker = os.path.join(CACHE_DIR, ".camoufox_profile_tmp")
    os.makedirs(tmp_marker, exist_ok=True)
    import tempfile

    with tempfile.TemporaryDirectory(prefix="camoufox_gen_") as tmp_dir:
        async with AsyncCamoufox(
            persistent_context=True,
            user_data_dir=tmp_dir,
            headless=False,
            humanize=True,
            locale="en-US",
            config={"forceScopeAccess": True},
        ) as context:
            page = await context.new_page()
            state_desc = "unknown"
            try:
                await page.goto("https://github.com/login", wait_until="domcontentloaded")
                await page.fill("#login_field", username)
                await page.fill("#password", password)
                await page.click('input[type="submit"][value="Sign in"]')

                # 等待登录结果，最多 180 秒（给用户时间手动完成 2FA/设备验证）
                print(f"⏳ {username}: 等待登录结果（如遇 2FA / 设备验证请在浏览器窗口中手动完成，最多等待 180 秒）...")
                logged = False
                for _ in range(90):
                    await page.wait_for_timeout(2000)
                    state_desc = detect_state(page)
                    if state_desc == "github-page":
                        logged = await is_logged_in(page)
                        if logged:
                            state_desc = "logged-in"
                            break
                        state_desc = "login-failed"
                        break
                    if state_desc in ("logged-in", "login-page", "verification-other"):
                        break

                if state_desc == "logged-in" and await is_logged_in(page):
                    storage = await context.storage_state()
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(storage, f, ensure_ascii=False, indent=2)
                    print(f"✅ {username}: 登录成功，storage state 已保存到 {cache_file}")
                    return True, storage, "logged-in"
                else:
                    print(f"❌ {username}: 登录未完成，当前状态: {state_desc} (url: {page.url[:100]})")
                    return False, None, state_desc
            except Exception as e:
                print(f"❌ {username}: 登录异常: {e}")
                return False, None, f"error: {e}"
            finally:
                await page.close()


async def main():
    targets = set(sys.argv[1:])
    accounts = load_github_accounts()
    if targets:
        accounts = {k: v for k, v in accounts.items() if k in targets}
    if not accounts:
        print("没有找到可处理的 GitHub 账号")
        return

    results = {}
    states = {}
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, encoding="utf-8") as f:
            try:
                states = json.load(f)
            except Exception:
                states = {}

    for username, gh in accounts.items():
        ok, storage, desc = await login_account(username, gh.get("password", ""))
        results[username] = desc
        if ok and storage:
            states[username] = storage

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(states, f, ensure_ascii=False)

    print(f"\n{'=' * 50}\n📋 汇总:")
    for username, desc in results.items():
        print(f"  {username}: {desc}")
    print(f"\n💾 storage states ({len(states)} 个) 已写入: {OUTPUT_FILE}")
    print("将该文件内容完整复制到 GitHub 仓库的 Actions secret: STORATE_STATES_GITHUB")


if __name__ == "__main__":
    asyncio.run(main())
