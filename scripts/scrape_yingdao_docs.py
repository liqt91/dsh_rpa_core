#!/usr/bin/env python
"""抓取影刀官方帮助中心「指令文档」正文与参数面板截图，刷新本地对标镜像。

背景
----
`docs/yingdao-cmds/`（网页自动化 44 条）与 `docs/yingdao-cmds-desktop/`（桌面软件自动化）
是影刀指令的**本地镜像**，用于对标（见 `docs/yingdao-web-cmds-benchmark.md`）。
原抓取脚本 `scripts/scrape_yingdao_*.py` 依赖 Playwright，已随 playwright 一并按 ADR-0013
移除 ⇒ 本脚本**用帮助中心自己的 HTTP 接口重写，纯标准库、无需浏览器、无需登录**。

接口（2026-10-12 实测，均在 `brandCode=rpa&languageCode=zh-CN` 下）
-----------------------------------------------------------------
1. 菜单树   GET https://api.yingdao.com/api/noauth/v1/yddoc/menus/getMenuTreeByBrandCode
            ?brandCode=rpa&languageCode=zh-CN            → 约 1 MB 全站目录树
2. 文档登记 GET .../menus/{menuId}/contents              → data.content 为 OSS 相对路径
3. OSS 域名 GET .../yddoc/oss/domain                     → https://xybot-oss-cdn.yingdao.com
4. 正文     GET {oss}{data.content}                      → Tiptap/ProseMirror JSON

产出
----
- `<out>/<指令名>.md`              正文块逐行，**截图留在原位**（见 `render()` 注释）
- `<out>/assets/<指令名>/imgN.png` 参数面板截图，N 按正文出现顺序
- `<out>/_tree_meta.json`          该分类的完整叶子清单（旧版只抓到 9 个 leaf，不要再拿它判完整性）
- `<out>/_manifest.json`           每条 docId / 正文 sha256 / 图片 sha256
                                   → `--check` 用它回答「影刀改了什么」

用法
----
    python scripts/scrape_yingdao_docs.py                 # 刷新两个分类
    python scripts/scrape_yingdao_docs.py --only 网页自动化
    python scripts/scrape_yingdao_docs.py --check         # 只报变化，不写盘
    python scripts/scrape_yingdao_docs.py --list          # 打印目录树
    python scripts/scrape_yingdao_docs.py --prune         # 顺手删上游已下线的旧截图

注意
----
写盘一律走 `write_text()`（显式 `newline="\\n"`）。Windows 上 `Path.write_text()` 会把
`\\n` 翻成 `\\r\\n`，一改就是**整个文件**变 CRLF ⇒ diff 退化成「全文件重写」，
把影刀真正的改动淹没在行尾噪声里（本脚本第一版就踩了，见 `.workbuddy/memory`）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

API = "https://api.yingdao.com/api/noauth/v1/yddoc"
PAGE = "https://www.yingdao.com/yddoc/rpa/zh-CN/help/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://www.yingdao.com/",
    "Accept": "application/json, text/plain, */*",
}

# 分类名 → 输出目录（相对仓库根）
GROUPS = {
    "网页自动化": "docs/yingdao-cmds",
    "桌面软件自动化": "docs/yingdao-cmds-desktop",
}

WORD = re.compile(r"[^\w\u4e00-\u9fff-]+")


def safe_name(name: str) -> str:
    """指令名 → 文件名。与旧脚本同一套规则，保证文件名不churn。"""
    return WORD.sub("_", name).strip("_") or "cmd"


def fetch(url: str, *, retries: int = 3, timeout: int = 60) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:  # noqa: PERF203
            last = exc
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"fetch failed: {url} ({last!r})")


def fetch_json(url: str) -> dict:
    payload = json.loads(fetch(url).decode("utf-8"))
    if not isinstance(payload, dict) or not payload.get("success", True):
        raise RuntimeError(f"api error: {url} -> {str(payload)[:200]}")
    return payload


def menu_tree() -> list[dict]:
    url = f"{API}/menus/getMenuTreeByBrandCode?brandCode=rpa&languageCode=zh-CN"
    return fetch_json(url)["data"]


def oss_domain() -> str:
    return str(fetch_json(f"{API}/oss/domain")["data"]).rstrip("/")


def walk(nodes: list[dict], path: tuple[str, ...] = ()):
    for node in nodes:
        here = path + (node["name"],)
        yield here, node
        yield from walk(node.get("menuTrees") or [], here)


def collect_leaves(tree: list[dict], group: str) -> list[tuple[str, str, str]]:
    """该分类下的全部叶子 → [(指令名, menuId, 所属子目录)]，保持目录顺序。

    子目录要带上：影刀**真的存在同名指令**（「桌面软件自动化 > 获取窗口对象」与
    「… > 窗口操作 > 获取窗口对象」），只按指令名落盘会互相覆盖（一个静默丢失）。
    """
    prefix = ("指令文档", group)
    leaves: list[tuple[str, str, str]] = []
    for path, node in walk(tree):
        if len(path) > len(prefix) and path[: len(prefix)] == prefix and node.get("type") == "doc":
            parent = path[-2] if len(path) > len(prefix) + 1 else ""
            leaves.append((node["name"], str(node["id"]), parent))
    return leaves


def assign_stems(leaves: list[tuple[str, str, str]]) -> list[str]:
    """指令名 → 文件名；重名时补所属子目录，仍冲突再退到 docId 后缀。"""
    base = [safe_name(name) for name, _, _ in leaves]
    counts = Counter(base)
    out: list[str] = []
    seen: Counter[str] = Counter()
    for (name, menu_id, parent), stem in zip(leaves, base, strict=True):
        if counts[stem] > 1 and parent:
            stem = safe_name(f"{parent}_{name}")
        seen[stem] += 1
        if seen[stem] > 1:
            stem = f"{stem}_{menu_id}"
        out.append(stem)
    return out


def doc_body(menu_id: str) -> dict:
    registered = fetch_json(f"{API}/menus/{menu_id}/contents")["data"]
    content = registered.get("content") or ""
    if registered.get("contentType") != "oss" or not content.startswith("/"):
        raise RuntimeError(f"unexpected content shape for {menu_id}: {registered}")
    return json.loads(fetch(f"{oss_domain()}{content}").decode("utf-8"))


def node_text(node: dict) -> str:
    """把一个节点之下的全部 text 拼成一行（不含任何分隔符）。"""
    if node.get("type") == "text":  # 直接被当成块传入的 text 节点（它的文本在 text 键上）
        return str(node.get("text") or "").strip()

    parts: list[str] = []

    def visit(item: object) -> None:
        if isinstance(item, dict):
            if item.get("type") == "hardBreak":
                # 段内换行（Shift+Enter）。不还原就会把两段拼成一行
                # （「…只有标签为<select>，…才可生效」+「如下所示，图①…」曾粘成一句）。
                parts.append("\n")
                return
            if item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
                return
            for value in item.values():
                visit(value)
        elif isinstance(item, list):
            for value in item:
                visit(value)

    visit(node.get("content"))
    return "".join(parts).strip()


# 这些类型只做「往下走一层」，自身不出文本。
# `description`（指令简介）与 `listItem` 都必须在内，否则 `node_text` 会把它们
# **内部的多段** 拼成一行（旧产物是分成两行的）。
# 曾经把 `description` 当容器会**整条简介丢失**——因为当时 `node_text` 不认
# 「直接被当成块传入的 text 节点」，对它取 `content` 恒为空；现在 `node_text`
# 首行就拦了这种情况，两个诉求可以同时满足。
BLOCK_WRAPPERS = {
    "doc",
    "bulletList",
    "orderedList",
    "listItem",
    "blockquote",
    "description",
}


def render(document: dict) -> list[tuple[str, str]]:
    """ProseMirror 文档 → `[("text", 行) | ("image", src)]`，保持正文顺序。

    与 Playwright 时代的旧产物有两处**有意**偏离（旧脚本用页面 `inner_text()`，
    表格被摊成一堆带制表符的碎行、"参数名" 与 "参数说明" 混在一起）：

    - 图片**留在原位**。旧产物把全部截图堆在末尾的「## 参数截图」，
      读参数时得来回翻页；原位引用让「参数名 → 它的面板截图」挨着。
    - 表格**逐行展开**、单元格用 ` | ` 连接，不再丢结构。

    正文文本本身是逐字保留的（含 💡 之类装饰性图标之外的一切内容）。
    """
    stream: list[tuple[str, str]] = []

    def emit(node: dict) -> None:
        kind = node.get("type")
        if kind == "image":
            src = (node.get("attrs") or {}).get("src") or node.get("src")
            if src:
                stream.append(("image", str(src)))
            return
        if kind in BLOCK_WRAPPERS:
            for child in node.get("content") or []:
                emit(child)
            return
        if kind == "table":
            for row in node.get("content") or []:
                emit(row)
            return
        if kind == "tableRow":
            cells = [node_text(cell).replace("\n", " ") for cell in node.get("content") or []]
            if any(cells):
                stream.append(("text", " | ".join(cells)))
            return
        text = node_text(node)
        for line in text.split("\n"):  # 段内 hardBreak 还原成的多行
            line = line.strip()
            if line:
                stream.append(("text", line))

    for child in document.get("content") or []:
        emit(child)
    return stream


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_text(path: Path, text: str) -> None:
    """全仓 LF 约定：显式 newline="\\n"。

    Windows 上 `Path.write_text()` 会把 `\\n` 翻成 `\\r\\n`，一改就是**整个文件**变 CRLF
    （不是只改新加的行）⇒ git diff 会退化成「全文件重写」，把影刀真正的改动淹没。
    """
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def load_manifest(path: Path) -> dict:
    """返回 {stem: 条目}。注意条目在 `docs` 键下——直接返回整个 manifest
    会让 `stem not in previous` **恒真**（幂等判据变成假绿灯：每轮都报「新增全量」）。
    """
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("docs") or {}


def scrape(group: str, out_dir: Path, *, check_only: bool, prune: bool = False, echo=print) -> dict:
    tree = menu_tree()
    leaves = collect_leaves(tree, group)
    stems = assign_stems(leaves)
    manifest_path = out_dir / "_manifest.json"
    previous = load_manifest(manifest_path)
    entries: dict[str, dict] = {}
    changed: list[str] = []
    added: list[str] = []
    failed: list[str] = []

    echo(f"[{group}] 叶子 {len(leaves)} 条 → {out_dir.relative_to(ROOT)}")

    for index, ((name, menu_id, _parent), stem) in enumerate(zip(leaves, stems, strict=True), 1):
        try:
            document = doc_body(menu_id)
        except Exception as exc:  # 单条失败不该毁掉整轮
            failed.append(f"{name}({menu_id}): {exc}")
            echo(f"  [{index}/{len(leaves)}] FAIL {name} <- {exc}")
            keep = previous.get(stem)
            if keep:
                entries[stem] = keep
            continue

        stream = render(document)
        images = [payload for kind, payload in stream if kind == "image"]
        # 变更判据只看**正文文本**：截图换 CDN 域名不该被判成「影刀改了内容」
        body = "\n".join(payload for kind, payload in stream if kind == "text")
        body_hash = sha256(body.encode("utf-8"))
        asset_dir = out_dir / "assets" / stem

        image_hashes: list[str] = []
        slots: dict[int, str] = {}
        blobs: list[tuple[str, bytes]] = []
        for position, src in enumerate(images):
            blob_name = f"img{position}.png"
            try:
                data = fetch(src)
            except Exception as exc:  # 上游删过的老图会 404，跳过而不是伪造引用
                failed.append(f"{name} img{position}: {exc}")
                echo(f"  [{index}/{len(leaves)}] img FAIL {name} img{position}")
                continue
            blobs.append((blob_name, data))
            slots[position] = blob_name

        prior = previous.get(stem) or {}
        prior_hash = prior.get("body")
        status = "same"
        if stem not in previous:
            status = "new"
            added.append(stem)
        elif prior_hash != body_hash or prior.get("docId") != menu_id:
            status = "changed"
            changed.append(stem)

        if not check_only:
            parts = [f"# {name}", ""]
            position = 0
            for kind, payload in stream:
                if kind == "text":
                    parts += [payload, ""]
                    continue
                blob_name = slots.get(position)
                position += 1
                if blob_name:
                    rel = f"assets/{stem}/{blob_name}"
                    parts += [f"![{rel}]({rel})", ""]
            write_text(out_dir / f"{stem}.md", "\n".join(parts).rstrip("\n") + "\n")

            asset_dir.mkdir(parents=True, exist_ok=True)
            present = {p.name for p in asset_dir.glob("img*.png")}
            for blob_name, data in blobs:
                target = asset_dir / blob_name
                if not target.exists() or sha256(target.read_bytes()) != sha256(data):
                    target.write_bytes(data)
                image_hashes.append(sha256(data))
            leftovers = sorted(present - {blob_name for blob_name, _ in blobs})
            if prune and leftovers:  # 影刀删掉的旧图，明确要求才清理
                for orphan in leftovers:
                    (asset_dir / orphan).unlink()
                echo(f"      prune: {', '.join(leftovers)}")

        entries[stem] = {
            "name": name,
            "docId": menu_id,
            "url": f"{PAGE}{menu_id}",
            "body": body_hash,
            "images": image_hashes or (prior.get("images") or []),
        }
        echo(f"  [{index}/{len(leaves)}] {status:7s} {name} | {len(body)} 字 / 图 {len(blobs)}")

    if not check_only:
        out_dir.mkdir(parents=True, exist_ok=True)
        leaves_meta = [
            {"name": name, "id": menu_id, "stem": stem, "parent": parent}
            for (name, menu_id, parent), stem in zip(leaves, stems, strict=True)
        ]
        write_text(
            out_dir / "_tree_meta.json",
            json.dumps(leaves_meta, ensure_ascii=False, indent=2) + "\n",
        )
        write_text(
            out_dir / "_manifest.json",
            json.dumps(
                {
                    "source": "yingdao help center /api/noauth/v1/yddoc",
                    "brandCode": "rpa",
                    "languageCode": "zh-CN",
                    "group": group,
                    "scrapedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "count": len(leaves),
                    "docs": entries,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )

    echo(
        f"[{group}] 完成：新增 {len(added)} / 变更 {len(changed)} / 失败 {len(failed)}"
        f"（总 {len(leaves)}）"
    )
    for item in failed:
        echo(f"  ! {item}")
    return {"group": group, "total": len(leaves), "added": added, "changed": changed}


def show_tree(group: str) -> None:
    tree = menu_tree()
    prefix = ("指令文档", group)
    for path, node in walk(tree):
        if path[: len(prefix)] == prefix and len(path) > len(prefix):
            depth = len(path) - len(prefix)
            mark = "doc " if node.get("type") == "doc" else "dir "
            print("  " * depth + f"{mark}{node['name']}  {node['id']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="刷新影刀指令文档本地镜像")
    parser.add_argument("--only", choices=sorted(GROUPS), help="只处理某个分类")
    parser.add_argument("--check", action="store_true", help="只报告变化，不写盘")
    parser.add_argument("--prune", action="store_true", help="顺手删除上游已下线的旧截图")
    parser.add_argument("--list", action="store_true", help="打印目录树后退出")
    args = parser.parse_args(argv)

    groups = [args.only] if args.only else list(GROUPS)
    if args.list:
        for group in groups:
            print(f"=== {group} ===")
            show_tree(group)
        return 0

    summary = []
    for group in groups:
        summary.append(
            scrape(
                group,
                ROOT / GROUPS[group],
                check_only=args.check,
                prune=args.prune,
                echo=print,
            )
        )

    if args.check:
        for item in summary:
            print(
                f"[{item['group']}] 新增 {item['added'] or '无'} / 变更 {item['changed'] or '无'}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
