#!/usr/bin/env python3
"""
使用 Camoufox 绕过 Cloudflare 验证执行 Linux.do 签到
"""

import json
import os
from urllib.parse import urlparse, parse_qs
from camoufox.async_api import AsyncCamoufox
from playwright_captcha import CaptchaType, ClickSolver, FrameworkType
from utils.browser_utils import filter_cookies, take_screenshot, save_page_content_to_file
from utils.config import ProviderConfig
from utils.get_headers import get_browser_headers, print_browser_headers
from utils.storage_state import ensure_storage_state_from_env

STORAGE_STATE_ENV_NAME = "STORATE_STATES_LINUXDO"


class LinuxDoSignIn:
    """使用 Linux.do 登录授权类"""

    def __init__(
        self,
        account_name: str,
        provider_config: ProviderConfig,
        username: str,
        password: str,
    ):
        """初始化

        Args:
            account_name: 账号名称
            provider_config: 提供商配置
            username: Linux.do 用户名
            password: Linux.do 密码
        """
        self.account_name = account_name
        self.provider_config = provider_config
        self.username = username
        self.password = password

    async def signin(
        self,
        client_id: str,
        auth_state: str,
        auth_cookies: list,
        cache_file_path: str = "",
    ) -> tuple[bool, dict, dict | None]:
        """使用 Linux.do 账号执行登录授权

        Args:
            client_id: OAuth 客户端 ID
            auth_state: OAuth 认证状态
            auth_cookies: OAuth 认证 cookies
            cache_file_path: 缓存文件

        Returns:
            (成功标志, 用户信息字典, 浏览器指纹头部信息或None)
            - 浏览器指纹头部信息仅在检测到 Cloudflare 验证页面时返回
        """
        print(f"ℹ️ {self.account_name}: Executing sign-in with Linux.do")
        print(
            f"ℹ️ {self.account_name}: Using client_id: {client_id}, auth_state: {auth_state}, cache_file: {cache_file_path}"
        )

        # 使用 Camoufox 启动浏览器
        async with AsyncCamoufox(
            # persistent_context=True,
            # user_data_dir=tmp_dir,
            headless=False,
            humanize=True,
            locale="en-US",
            os="macos",  # 强制使用 macOS 指纹，避免跨平台指纹不一致问题
            config={
                "forceScopeAccess": True,
            },
        ) as browser:
            ensure_storage_state_from_env(
                    cache_file_path,
                    self.account_name,
                    self.username,
                    env_name=STORAGE_STATE_ENV_NAME,
            )
            
            # 只有在缓存文件存在时才加载 storage_state
            storage_state = cache_file_path if os.path.exists(cache_file_path) else None
            if storage_state:
                print(f"ℹ️ {self.account_name}: Found cache file, restore storage state")
            else:
                print(f"ℹ️ {self.account_name}: No cache file found, starting fresh")

            context = await browser.new_context(storage_state=storage_state)

            # 设置从参数获取的 auth cookies 到页面上下文
            if auth_cookies:
                await context.add_cookies(auth_cookies)
                print(f"ℹ️ {self.account_name}: Set {len(auth_cookies)} auth cookies from provider")
            else:
                print(f"ℹ️ {self.account_name}: No auth cookies to set")

            page = await context.new_page()

            # 监听新版 new-api 回调页前端调用的 OAuth 兑换接口响应：
            # 响应体包含 access_token 与用户信息（data.user.id），直接捕获即可，
            # 无需脚本自行重放 code（新版 flow token 一次性有效）
            oauth_bundles = []

            async def _on_response(response):
                try:
                    url = response.url
                    if "/api/oauth/" not in url or "/api/oauth/state" in url:
                        return
                    if response.status != 200:
                        return
                    body = await response.json()
                    oauth_bundles.append({"url": url, "body": body})
                except Exception:
                    pass

            page.on("response", _on_response)

            async with ClickSolver(
                framework=FrameworkType.CAMOUFOX, page=page, max_attempts=5, attempt_delay=3
            ) as solver:

                try:
                    # 检查是否已经登录（通过缓存恢复）
                    is_logged_in = False
                    oauth_url = (
                        f"https://connect.linux.do/oauth2/authorize?"
                        f"response_type=code&client_id={client_id}&state={auth_state}"
                    )

                    if os.path.exists(cache_file_path):
                        try:
                            print(f"ℹ️ {self.account_name}: Checking login status at {oauth_url}")
                            # 直接访问授权页面检查是否已登录
                            response = await page.goto(oauth_url, wait_until="domcontentloaded")
                            print(
                                f"ℹ️ {self.account_name}: redirected to app page {response.url if response else 'N/A'}"
                            )
                            await save_page_content_to_file(page, "sign_in_check", self.account_name, prefix="linuxdo")

                            # 登录后可能直接跳转回应用页面
                            if response and response.url.startswith(self.provider_config.origin):
                                is_logged_in = True
                                print(
                                    f"✅ {self.account_name}: Already logged in via cache, proceeding to authorization"
                                )
                            else:
                                # 检查是否出现授权按钮（表示已登录）
                                allow_btn = await page.query_selector('a[href^="/oauth2/approve"]')
                                if allow_btn:
                                    is_logged_in = True
                                    print(
                                        f"✅ {self.account_name}: Already logged in via cache, proceeding to authorization"
                                    )
                                else:
                                    print(f"ℹ️ {self.account_name}: Cache session expired, need to login again")
                        except Exception as e:
                            print(
                                f"⚠️ {self.account_name}: Failed to check login status: {e}\n"
                                f"Current page is: {page.url}"
                            )

                    # 如果未登录，则执行登录流程
                    if not is_logged_in:
                        run_login_manual = os.getenv('RUN_LINUXDO_LOGIN_MANUAL')
                        print(f"ℹ️ {self.account_name}: Run log-in manual env is {run_login_manual}")
                        if run_login_manual != 'true':
                            print(
                                f"❌ {self.account_name}: Log-in faild\n"
                                f"Current page is: {page.url}"
                            )
                            await take_screenshot(page, "logged_in_failed", self.account_name)
                            return False, {"error": "Linux.do log-in failed"}, None
                        
                        try:
                            print(f"ℹ️ {self.account_name}: Starting to sign in linux.do")

                            await page.goto("https://linux.do/login", wait_until="domcontentloaded")

                            # 检查是否在 Cloudflare 验证页面
                            page_title = await page.title()
                            page_content = await page.content()

                            if "Just a moment" in page_title or "Checking your browser" in page_content:
                                print(f"ℹ️ {self.account_name}: Cloudflare challenge detected, auto-solving...")
                                try:
                                    await solver.solve_captcha(
                                        captcha_container=page, captcha_type=CaptchaType.CLOUDFLARE_INTERSTITIAL
                                    )
                                    print(f"✅ {self.account_name}: Cloudflare challenge auto-solved")
                                    await page.wait_for_timeout(10000)
                                except Exception as solve_err:
                                    print(f"⚠️ {self.account_name}: Auto-solve failed: {solve_err}")

                            await page.fill("#login-account-name", self.username)
                            await page.wait_for_timeout(2000)
                            await page.fill("#login-account-password", self.password)
                            await page.wait_for_timeout(2000)
                            await page.click("#login-button")
                            await page.wait_for_timeout(10000)

                            await save_page_content_to_file(page, "sign_in_result", self.account_name, prefix="linuxdo")

                            try:
                                current_url = page.url
                                print(f"ℹ️ {self.account_name}: Current page url is {current_url}")
                                if "linux.do/challenge" in current_url:
                                    print(
                                        f"⚠️ {self.account_name}: Cloudflare challenge detected, "
                                        "Camoufox should bypass it automatically. Waiting..."
                                    )
                                    # 等待 Cloudflare 验证完成
                                    await page.wait_for_selector('a[href^="/oauth2/approve"]', timeout=60000)
                                    print(f"✅ {self.account_name}: Cloudflare challenge bypassed successfully")

                            except Exception as e:
                                print(f"⚠️ {self.account_name}: Possible Cloudflare challenge: {e}")
                                # 即使超时，也尝试继续
                                pass

                            # 保存新的会话状态
                            await context.storage_state(path=cache_file_path)
                            print(f"✅ {self.account_name}: Storage state saved to cache file")

                        except Exception as e:
                            print(f"❌ {self.account_name}: Error occurred while signing in linux.do: {e}")
                            await take_screenshot(page, "signin_bypass_error", self.account_name)
                            return False, {"error": "Linux.do sign-in error"}, None

                        # 登录后访问授权页面
                        try:
                            print(f"ℹ️ {self.account_name}: Navigating to authorization page: {oauth_url}")
                            await page.goto(oauth_url, wait_until="domcontentloaded")
                        except Exception as e:
                            print(f"❌ {self.account_name}: Failed to navigate to authorization page: {e}")
                            await take_screenshot(page, "auth_page_navigation_failed_bypass", self.account_name)
                            return False, {"error": "Linux.do authorization page navigation failed"}, None

                    try:
                        # 等待授权按钮出现，最多等待30秒
                        print(f"ℹ️ {self.account_name}: Waiting for authorization button...")
                        await page.wait_for_selector('a[href^="/oauth2/approve"]', timeout=30000)
                        allow_btn_ele = await page.query_selector('a[href^="/oauth2/approve"]')

                        if allow_btn_ele:
                            print(f"✅ {self.account_name}: Approve button found, proceeding to authorization")
                            await allow_btn_ele.click()

                            # 在等待重定向之前，先检查是否遇到 Cloudflare 挑战
                            try:
                                print(f"ℹ️ {self.account_name}: Checking for Cloudflare challenge after authorization...")
                                await page.wait_for_timeout(3000)  # 等待页面响应

                                page_title = await page.title()
                                page_content = await page.content()
                                current_url = page.url

                                # 检查 URL 中是否包含 Cloudflare 挑战参数或页面内容
                                if "__cf_chl_rt_tk" in current_url or "Just a moment" in page_title or "Checking your browser" in page_content:
                                    cloudflare_challenge_detected = True
                                    print(f"ℹ️ {self.account_name}: Cloudflare challenge detected before redirect, auto-solving...")
                                    try:
                                        await solver.solve_captcha(
                                            captcha_container=page, captcha_type=CaptchaType.CLOUDFLARE_INTERSTITIAL
                                        )
                                        print(f"✅ {self.account_name}: Cloudflare challenge auto-solved")
                                        await page.wait_for_timeout(5000)
                                    except Exception as solve_err:
                                        print(f"⚠️ {self.account_name}: Auto-solve failed: {solve_err}")
                                else:
                                    print(f"ℹ️ {self.account_name}: No Cloudflare challenge detected, proceeding to redirect")
                                    
                            except Exception as e:
                                print(f"⚠️ {self.account_name}: Error checking Cloudflare challenge: {e}")
                        else:
                            print(f"❌ {self.account_name}: Approve button not found")
                            await take_screenshot(page, "approve_button_not_found_bypass", self.account_name)
                            return False, {"error": "Linux.do allow button not found"}, None
                    except Exception as e:
                        print(
                            f"❌ {self.account_name}: Error occurred during authorization: {e}\n"
                            f"Current page is: {page.url}"
                        )
                        await take_screenshot(page, "authorization_failed_bypass", self.account_name)
                        return False, {"error": "Linux.do authorization failed"}, None

                    # 统一处理授权逻辑（无论是否通过缓存登录）
                    # 标记是否检测到 Cloudflare 验证页面
                    cloudflare_challenge_detected = False

                    try:                  
                        # 先检查是否已跳转到 /console/token（Cloudflare 挑战等待期间可能已完成跳转）
                        console_token_pattern = f"**{self.provider_config.origin}/console/token**"
                        if oauth_bundles:
                            # 兑换响应已在回调页加载时捕获（缓存会话直接完成授权）
                            print(f"ℹ️ {self.account_name}: OAuth callback already processed during sign-in check")
                        else:
                            try:
                                await page.wait_for_url(console_token_pattern, timeout=3000)
                                print(f"ℹ️ {self.account_name}: Already redirected to /console/token, skipping redirect_pattern wait")
                            except Exception:
                                # 未跳转到 /console/token，使用配置的 redirect_pattern 等待
                                redirect_pattern = self.provider_config.get_linuxdo_auth_redirect_pattern()
                                print(f"ℹ️ {self.account_name}: Waiting for redirect to: {redirect_pattern}")
                                await page.wait_for_url(redirect_pattern, timeout=30000)
                                await page.wait_for_timeout(5000)

                        # 检查是否在 Cloudflare 验证页面
                        page_title = await page.title()
                        page_content = await page.content()

                        if "Just a moment" in page_title or "Checking your browser" in page_content:
                            cloudflare_challenge_detected = True
                            print(f"ℹ️ {self.account_name}: Cloudflare challenge detected, auto-solving...")
                            try:
                                await solver.solve_captcha(
                                    captcha_container=page, captcha_type=CaptchaType.CLOUDFLARE_INTERSTITIAL
                                )
                                print(f"✅ {self.account_name}: Cloudflare challenge auto-solved")
                                await page.wait_for_timeout(10000)
                            except Exception as solve_err:
                                print(f"⚠️ {self.account_name}: Auto-solve failed: {solve_err}")
                    except Exception as e:
                        # 检查 URL 中是否包含 code 参数，如果包含则视为正常（OAuth 回调成功）
                        if "code=" in page.url:
                            print(f"ℹ️ {self.account_name}: Redirect timeout but OAuth code found in URL, continuing...")
                        else:
                            print(
                                f"❌ {self.account_name}: Error occurred during redirecting: {e}\n"
                                f"Current page is: {page.url}"
                            )
                            await take_screenshot(page, "linuxdo_authorization_failed", self.account_name)

                    # 从 localStorage 获取 user 对象并提取 id
                    api_user = None
                    current_url = page.url

                    # 新版 new-api：回调页前端会调用 /api/oauth/:provider 兑换 code，
                    # 从监听到的响应中提取 access_token 与用户 id
                    access_token = None
                    for _ in range(6):
                        for item in oauth_bundles:
                            body = item.get("body") or {}
                            if not body.get("success"):
                                continue
                            data = body.get("data") or {}
                            token = data.get("access_token")
                            if not token:
                                continue
                            oauth_user = data.get("user") if isinstance(data.get("user"), dict) else {}
                            api_user = data.get("id") or oauth_user.get("id")
                            access_token = token
                            break
                        if access_token and api_user:
                            break
                        if not oauth_bundles:
                            await page.wait_for_timeout(2000)

                    if access_token and api_user:
                        print(
                            f"✅ {self.account_name}: Captured OAuth bundle from callback "
                            f"(api_user: {api_user})"
                        )
                        user_cookies = filter_cookies(await context.cookies(), self.provider_config.origin)
                        # 原始 cookie（含 domain 等字段），供站点 UI 兜底流程恢复会话
                        domain_suffix = urlparse(self.provider_config.origin).netloc.lstrip(".")
                        raw_site_cookies = [
                            c for c in await context.cookies()
                            if (c.get("domain") or "").lstrip(".") == domain_suffix
                            or (c.get("domain") or "").lstrip(".").endswith("." + domain_suffix)
                        ]
                        return True, {
                            "access_token": access_token,
                            "api_user": api_user,
                            "cookies": user_cookies,
                            "raw_cookies": raw_site_cookies,
                        }, None

                    if oauth_bundles:
                        print(
                            f"⚠️ {self.account_name}: OAuth callback API responded but no valid bundle: "
                            f"{json.dumps(oauth_bundles[-1].get('body', {}), ensure_ascii=False)[:200]}"
                        )

                    try:
                        try:
                            await page.wait_for_function('localStorage.getItem("user") !== null', timeout=10000)
                        except Exception:
                            await page.wait_for_timeout(5000)

                        user_data = await page.evaluate("() => localStorage.getItem('user')")
                        if user_data:
                            user_obj = json.loads(user_data)
                            api_user = user_obj.get("id")
                            if api_user:
                                print(f"✅ {self.account_name}: Got api user: {api_user}")
                            else:
                                print(f"⚠️ {self.account_name}: User id not found in localStorage")
                        else:
                            print(f"⚠️ {self.account_name}: User data not found in localStorage")
                    except Exception as e:
                        print(f"⚠️ {self.account_name}: Error reading user from localStorage: {e}")

                    if api_user:
                        print(f"✅ {self.account_name}: OAuth authorization successful")

                        # 提取 session cookie，只保留与 provider domain 匹配的
                        restore_cookies = await page.context.cookies()
                        user_cookies = filter_cookies(restore_cookies, self.provider_config.origin)

                        result = {"cookies": user_cookies, "api_user": api_user}

                        # 只有当检测到 Cloudflare 验证页面时，才获取并返回浏览器指纹头部信息
                        browser_headers = None
                        if cloudflare_challenge_detected:
                            browser_headers = await get_browser_headers(page)
                            print_browser_headers(self.account_name, browser_headers)
                            print(
                                f"ℹ️ {self.account_name}: Browser headers returned (Cloudflare challenge was detected)"
                            )
                        else:
                            print(
                                f"ℹ️ {self.account_name}: Browser headers not returned (no Cloudflare challenge detected)"
                            )

                        return True, result, browser_headers
                    else:
                        print(f"⚠️ {self.account_name}: OAuth callback received but no user ID found")
                        await take_screenshot(page, "oauth_failed_no_user_id_bypass", self.account_name)
                        parsed_url = urlparse(current_url)
                        query_params = parse_qs(parsed_url.query)

                        # 如果 query 中包含 code，说明 OAuth 回调成功
                        if "code" in query_params:
                            print(f"✅ {self.account_name}: OAuth code received: {query_params.get('code')}")
                            # 只有当检测到 Cloudflare 验证页面时，才获取并返回浏览器指纹头部信息
                            browser_headers = None
                            if cloudflare_challenge_detected:
                                browser_headers = await get_browser_headers(page)
                                print_browser_headers(self.account_name, browser_headers)
                                print(
                                    f"ℹ️ {self.account_name}: Browser headers returned (Cloudflare challenge was detected)"
                                )
                            else:
                                print(
                                    f"ℹ️ {self.account_name}: Browser headers not returned (no Cloudflare challenge detected)"
                                )
                            return True, query_params, browser_headers
                        else:
                            print(
                                f"❌ {self.account_name}: OAuth failed, no code in callback\n"
                                f"Parsed url is: {current_url}"
                            )
                            return (
                                False,
                                {
                                    "error": "Linux.do OAuth failed - no code in callback",
                                },
                                None,
                            )

                except Exception as e:
                    print(f"❌ {self.account_name}: Error occurred while processing linux.do page: {e}")
                    await take_screenshot(page, "page_navigation_error_bypass", self.account_name)
                    return False, {"error": "Linux.do page navigation error"}, None
                finally:
                    await page.close()
                    await context.close()
