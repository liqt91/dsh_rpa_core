from playwright.sync_api import sync_playwright
import sys, re, os, urllib.request, json

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = "https://www.yingdao.com/yddoc/rpa/zh-CN/711591442174164992"
OUT = r"D:\Users\Administrator\Documents\代码\rpa_core\docs\yingdao-cmds-desktop"
os.makedirs(OUT, exist_ok=True)

# Target commands to scrape
TARGETS = [
    "获取窗口对象", "获取窗口对象列表", "点击元素(win)", "鼠标悬停在元素上(win)",
    "填写输入框(win)", "运行或打开", "拖拽元素(win)", "等待元素(win)",
    "填写密码框(win)", "设置下拉框(win)", "设置复选框(win)", "设置元素值(win)",
    "获取元素对象(win)", "获取关联元素(win)", "获取相似元素列表(win)",
    "激活软件窗口", "设置窗口状态", "设置窗口是否显示", "移动窗口位置",
    "调整窗口大小", "关闭软件窗口", "常用元素属性", "元素截图(win)",
    "获取元素信息(win)", "获取元素位置(win)", "获取下拉框选项(win)",
    "获取窗口信息", "获取选中文本",
]


def safe(name):
    return re.sub(r"[^\w\u4e00-\u9fff-]+", "_", name).strip("_") or "cmd"


def download(src, dest):
    try:
        req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0", "Referer": BASE})
        data = urllib.request.urlopen(req, timeout=40).read()
        open(dest, "wb").write(data)
        return len(data)
    except Exception as e:
        print("  dl fail", repr(src)[:60], "err", e)
        return 0


with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1680, "height": 1080})
    pg.goto(BASE, wait_until="domcontentloaded", timeout=90000)
    pg.wait_for_timeout(6000)

    # Step 1: Extract full tree from React fiber
    tree = pg.evaluate("""
      () => {
        const treeEl = document.querySelector('.ant-tree-list-holder-inner');
        let fiber = null;
        for (const key of Object.keys(treeEl)) {
          if (key.startsWith('__reactFiber$') || key.startsWith('__reactInternalInstance$')) {
            fiber = treeEl[key];
            break;
          }
        }
        let node = fiber;
        for (let i = 0; i < 50; i++) {
          const props = node.memoizedProps || node.pendingProps || {};
          if (props.treeData && Array.isArray(props.treeData) && props.treeData.length > 0) {
            return props.treeData;
          }
          node = node.return;
        }
        return null;
      }
    """)

    # Step 2: Build name -> docUniqueId map
    def build_map(items, result={}):
        for item in items:
            name = item.get('name', '')
            doc_id = item.get('docUniqueId', '')
            if name and doc_id:
                result[name] = doc_id
            children = item.get('menuTrees', [])
            if children:
                build_map(children, result)
        return result

    name_to_id = build_map(tree)
    print(f"total nodes in tree: {len(name_to_id)}")

    # Step 3: Navigate to each target's URL directly
    saved = 0
    for cmd in TARGETS:
        doc_id = name_to_id.get(cmd)
        if not doc_id:
            print(f"MISS(id): {cmd}")
            continue

        url = f"https://www.yingdao.com/yddoc/rpa/zh-CN/{doc_id}"
        pg.goto(url, wait_until="domcontentloaded", timeout=90000)
        pg.wait_for_timeout(3000)

        # Verify page loaded
        h1_text = pg.evaluate("() => (document.querySelector('h1')||{}).textContent||''")
        if not h1_text.strip():
            print(f"MISS(h1): {cmd} -> empty")
            continue

        ed = pg.locator(".editor.editor--readonly").first
        text = ed.inner_text().strip() if ed.count() else ""
        if not text:
            print(f"MISS(text): {cmd}")
            continue

        imgs = pg.eval_on_selector_all(
            ".editor.editor--readonly img",
            "els => els.map(e => e.getAttribute('src'))"
        )

        name = safe(cmd)
        asset_dir = os.path.join(OUT, "assets", name)
        os.makedirs(asset_dir, exist_ok=True)
        refs = []
        for i, src in enumerate(imgs):
            dest = os.path.join(asset_dir, f"img{i}.png")
            download(src, dest)
            refs.append(f"assets/{name}/img{i}.png")

        parts = [f"# {cmd}", "", text, "", "## 参数截图"]
        for rel in refs:
            parts.append(f"![{rel}]({rel})")
        open(os.path.join(OUT, name + ".md"), "w", encoding="utf-8").write("\n".join(parts))
        saved += 1
        print(f"OK {cmd} | text {len(text)} imgs {len(refs)}")

    print(f"DONE saved {saved}/{len(TARGETS)}")
    b.close()
