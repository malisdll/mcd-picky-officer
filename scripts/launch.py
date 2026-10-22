#!/usr/bin/env python3
"""把仓库准备到「只差 git push」的程度。

它做四件事，每一步都可以重复执行（幂等）：
1. 把 README 里的 YOUR_GITHUB_NAME 替换成真实用户名；
2. git init + 设置仓库级作者信息（不动全局配置）；
3. 按逻辑分几次提交，让提交历史可读，而不是一个 "init" 糊上去；
4. 配好 remote 并打印接下来要手动做的事。

它**不会**做的事：推送、创建远程仓库、打开 Pages、提交报名 Issue。
这些都需要你的 GitHub 凭证，脚本不该也不该有能力碰。

用法：
    python scripts/launch.py <你的GitHub用户名> [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER = "YOUR_GITHUB_NAME"
REPO_NAME = "mcd-picky-officer"

USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")

# 敏感信息：Token 泄露比不报名严重得多，所以推之前先自己扫一遍
SECRET_RE = re.compile(
    r"(Bearer\s+[A-Za-z0-9._-]{20,}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|(?:MCD_MCP_TOKEN\s*[=:]\s*[\"']?)(?!\$\{)[A-Za-z0-9]{16,})"
)

TEXT_GLOBS = ("*.md", "*.json", "*.html", "*.py", "*.yml")


def sh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=check)


def git_available() -> bool:
    return shutil.which("git") is not None


def is_repo() -> bool:
    r = sh("git", "rev-parse", "--is-inside-work-tree", check=False)
    return r.returncode == 0 and r.stdout.strip() == "true"


def scan_secrets() -> list[str]:
    hits: list[str] = []
    for pattern in TEXT_GLOBS:
        for path in ROOT.rglob(pattern):
            if any(part in {".git", "node_modules", "__pycache__"} for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if SECRET_RE.search(line):
                    rel = path.relative_to(ROOT).as_posix()
                    hits.append(f"{rel}:{lineno}: {line.strip()[:90]}")
    return hits


def replace_placeholder(username: str, dry: bool) -> list[str]:
    changed: list[str] = []
    for path in ROOT.rglob("*.md"):
        if ".git" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if PLACEHOLDER not in text:
            continue
        new = text.replace(PLACEHOLDER, username)
        if not dry:
            path.write_text(new, encoding="utf-8", newline="\n")
        changed.append(path.relative_to(ROOT).as_posix())
    return changed


# 分几次提交，让历史说得清这个项目是怎么长出来的
COMMITS = [
    ("参赛声明与项目骨架",
     ["CONTEST_DECLARATION.md", "LICENSE", ".gitignore", "data"]),
    ("核心判定逻辑：四态判定、配餐、算价核验",
     ["mcd/models.py", "mcd/profile.py", "mcd/matcher.py", "mcd/planner.py",
      "mcd/pricing.py", "mcd/verify.py", "mcd/store.py", "mcd/__init__.py"]),
    ("MCP 客户端与真实数据接入",
     ["mcd/mcp_client.py", "mcd/live.py", "mcd/infer.py", "mcd/nutrition.py"]),
    ("能力体检器：用零副作用探测回答「产品能不能成立」",
     ["mcd/doctor.py"]),
    ("把「查不到」编译成能念给柜台的问句",
     ["mcd/counter.py", "mcd/render.py"]),
    ("CLI、离线测试与 CI",
     ["run.py", "tests", ".github/workflows/ci.yml"]),
    ("单文件交互演示与 Pages 部署",
     ["demo", ".github/workflows/pages.yml", "assets"]),
    ("文档：实测结论、集成说明、上线清单",
     ["README.md", "MCP_INTEGRATION.md", "SKILL.md", "workbuddy.md",
      "SUBMISSION.md", "mcp-config.example.json", "docs", "scripts"]),
]


def commit_all(username: str, dry: bool) -> int:
    if not is_repo():
        if not dry:
            sh("git", "init", "-q")
            sh("git", "branch", "-M", "main")

    # 只设仓库级配置，不动用户的全局 git 身份
    if not dry:
        sh("git", "config", "user.name", username)
        sh("git", "config", "user.email", f"{username}@users.noreply.github.com")

    made = 0
    for subject, paths in COMMITS:
        existing = [p for p in paths if (ROOT / p).exists()]
        if not existing:
            continue
        if not dry:
            sh("git", "add", "--", *existing)
            has = sh("git", "diff", "--cached", "--quiet", check=False)
            if has.returncode == 0:
                continue                     # 没有变更，不产生空提交
            sh("git", "commit", "-q", "-m", subject)
        print(f"  · {subject}")
        made += 1

    # 兜底：把还没进版本库的东西（比如后来补的文件）收进最后一个提交
    if not dry:
        sh("git", "add", "-A")
        pending = sh("git", "diff", "--cached", "--quiet", check=False)
        if pending.returncode != 0:
            sh("git", "commit", "-q", "-m", "补齐首次上线前的收尾改动")
            print("  · 补齐收尾改动")
            made += 1
    return made


def set_remote(username: str, dry: bool) -> str:
    url = f"https://github.com/{username}/{REPO_NAME}.git"
    if dry:
        return url
    cur = sh("git", "remote", "get-url", "origin", check=False)
    if cur.returncode == 0:
        if cur.stdout.strip() != url:
            sh("git", "remote", "set-url", "origin", url)
            print(f"  · remote 已更正为 {url}")
    else:
        sh("git", "remote", "add", "origin", url)
        print(f"  · remote 已设为 {url}")
    return url


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="准备仓库到「只差 push」的状态")
    p.add_argument("username", help="你的 GitHub 用户名（用于替换 README 占位符与作者信息）")
    p.add_argument("--dry-run", action="store_true", help="只打印将要做什么，不落盘")
    args = p.parse_args(argv)

    username = args.username.strip().lstrip("@")
    if not USERNAME_RE.match(username):
        print(f"用户名不合法：{username!r}")
        print("GitHub 用户名只能是字母、数字和单个连字符，且不能以连字符开头或结尾。")
        return 2
    if username == PLACEHOLDER:
        print("这还是占位符本身，请换成真实用户名。")
        return 2

    dry = args.dry_run
    print(f"\n准备好把 mcd-picky-officer 上线到 github.com/{username}"
          f"{'（演练模式，不写入）' if dry else ''}\n")

    print("[1/4] 敏感信息扫描")
    hits = scan_secrets()
    if hits:
        print("  ✗ 发现疑似密钥，先处理再推：")
        for h in hits:
            print(f"      {h}")
        print("\n  Token 等同于你的麦当劳账号凭证。它只应存在于环境变量里。")
        return 1
    print("  ✓ 未发现 Token / 密钥 / 手机号")

    if not git_available():
        print("\n✗ 找不到 git，请先安装：https://git-scm.com/downloads")
        return 1

    print("\n[2/4] 替换 README 占位符")
    changed = replace_placeholder(username, dry)
    for c in changed:
        print(f"  · {c}")
    if not changed:
        print("  · 没有需要替换的占位符（可能已经替换过了）")

    print("\n[3/4] 建立提交历史")
    if not is_repo():
        print("  · 初始化仓库（main 分支）")
    commit_all(username, dry)

    print("\n[4/4] 配置远程仓库")
    url = set_remote(username, dry)

    print("\n" + "─" * 62)
    if dry:
        print("演练结束。去掉 --dry-run 再跑一次才会真正写文件。")
        return 0

    print("本地已就绪。接下来三步都要你手动做（需要你的 GitHub 凭证）：\n")
    print("  1. 在 GitHub 网页新建空仓库 mcd-picky-officer（公开，不要勾任何初始化文件）")
    print(f"     https://github.com/new?name={REPO_NAME}&visibility=public")
    print()
    print("  2. 推送")
    print("     git push -u origin main")
    print()
    print("  3. 开启 Pages：仓库 Settings → Pages → Source 选 \"GitHub Actions\"")
    print(f"     然后等 Actions 跑完，访问 https://{username}.github.io/{REPO_NAME}/?p=peanut")
    print()
    print("  4. 提交报名（正文模板见 SUBMISSION.md 第 3 节）")
    print("     https://github.com/M-China/mcd-developer-innovation-challenge/issues/new")
    print()
    print(f"  截止：2026-10-25 23:59（北京时间）　Star 统计截止：2026-10-26 00:00")
    print("─" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
