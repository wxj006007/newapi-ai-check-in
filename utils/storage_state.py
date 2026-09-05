#!/usr/bin/env python3
"""
storage state 相关工具
"""

import json
import os


def ensure_storage_state_from_env(
    cache_file_path: str,
    account_name: str,
    username: str,
    env_name: str = "STORATE_STATES",
) -> bool:
    """当环境变量（secret）中存在该用户的 storage state 时，写入/覆盖本地缓存文件。

    优先级说明：secret 中由用户手工生成的状态总是优先于本地缓存文件——
    缓存可能保存过失败会话（例如登录被 GitHub 拦截），不能反过来覆盖 secret。
    仅当 secret 未提供该用户的状态时，才保留已有的缓存文件。

    返回 True 表示 cache_file_path 已包含可用的 storage state。
    """
    if not cache_file_path:
        print(f"⚠️ {account_name}: Skip restoring storage state because cache_file_path is empty")
        return False

    storage_states_str = os.getenv(env_name, "")
    if storage_states_str:
        try:
            storage_states = json.loads(storage_states_str)
        except json.JSONDecodeError as exc:
            print(f"⚠️ {account_name}: Failed to parse {env_name}: {exc}")
            storage_states = None

        if isinstance(storage_states, dict) and storage_states.get(username) is not None:
            storage_state_data = storage_states[username]

            if isinstance(storage_state_data, str):
                try:
                    storage_state_data = json.loads(storage_state_data)
                except json.JSONDecodeError as exc:
                    print(f"⚠️ {account_name}: Storage state '{username}' is not valid JSON: {exc}")
                    return False

            if not isinstance(storage_state_data, dict):
                print(f"⚠️ {account_name}: Storage state '{username}' must be a JSON object")
                return False

            cache_dir = os.path.dirname(cache_file_path)
            if cache_dir:
                os.makedirs(cache_dir, exist_ok=True)

            with open(cache_file_path, "w", encoding="utf-8") as file:
                json.dump(storage_state_data, file, ensure_ascii=False, indent=2)

            print(f"ℹ️ {account_name}: Restored storage state from {env_name} -> {username}")
            return True

    if os.path.exists(cache_file_path):
        print(f"⚠️ {account_name}: Keep existing cache file: {cache_file_path}")
        return False

    if not storage_states_str:
        print(f"⚠️ {account_name}: Skip restoring storage state because {env_name} is empty or not set")
        return False

    # env 有内容但没有该用户的状态
    print(f"⚠️ {account_name}: Skip restoring storage state because '{username}' was not found in {env_name}")
    return False
