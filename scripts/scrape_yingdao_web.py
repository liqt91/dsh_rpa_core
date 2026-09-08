from playwright.sync_api import sync_playwright
import sys, re, os, urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = "https://www.yingdao.com/yddoc/rpa/zh-CN/711591442174164992"
OUT = r"D:\Users\Administrator\Documents\代码\rpa_core\docs\yingdao-cmds"
os.makedirs(OUT, exist_ok=True)

CMDS = [
    "打开网页", "选择浏览器用户", "获取已打开的网页对象", "点击元素(web)", "鼠标悬停在元素上(web)",
    "填写输入框(web)", "关闭网页", "自动处理弹框(web)", "跳转至新网址", "等待网页加载完成",
    "停止网页加载", "鼠标滚动网页", "执行JS脚本", "获取网页对象列表", "设置Cookie", "移除指定Cookie",
    "等待元素(web)", "拖拽元素(web)", "填写密码框(web)", "设置下拉框(web)", "设置复选框(web)",
    "设置元素值(web)", "设置元素属性(web)", "获取元素对象(web)", "获取相似元素列表(web)", "获取关联元素(web)",
    "获取元素位置(web)", "批量数据抓取", "网页截图", "获取元素信息(web)", "获取下拉框选项(web)",
    "获取网页信息", "获取滚动条位置", "提取网页对话框内容", "获取筛选所有Cookie", "获取指定Cookie信息",
    "开始监听网页请求", "停止监听网页请求", "获取网页监听结果", "上传文件", "下载文件",
    "处理下载对话框", "处理上传对话框", "处理网页对话框",
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


def expand_all(pg):
    """反复点所有 close 的 switcher,直到没有 close 节点(最多 8 轮)。"""
    for _ in range(8):
        closed = pg.evaluate("""
          () => {
            const nodes=[...document.querySelectorAll('.ant-tree-list-holder .ant-tree-treenode')];
            let hit=null;
            for(const n of nodes){
              const t=n.querySelector('.ant-tree-title');
              const sw=n.querySelector('.ant-tree-switcher');
              if(t && sw && (sw.getAttribute('class')||'').includes('switcher_close')){ hit={n1:n, t:t.textContent.trim()}; break; }
            }
            if(!hit) return 0;
            hit.n1.querySelector('.ant-tree-switcher').click();
            return hit.t;
          }
        """)
        if not closed:
            break
        pg.wait_for_timeout(700)
    print("  [tree expanded]")


def grab(pg, title):
    node = pg.evaluate("""
        (t) => {
          const nodes=[...document.querySelectorAll('.ant-tree-list-holder .ant-tree-treenode')];
          const hit=nodes.find(n => (n.querySelector('.ant-tree-title')||{}).textContent?.trim()===t);
          if(!hit) return false;
          hit.scrollIntoView({block:'center'}); hit.click(); return true;
        }""", title)
    if not node:
        return None
    pg.wait_for_timeout(1400)
    ed = pg.locator(".editor.editor--readonly").first
    if ed.count() == 0:
        return None
    text = ed.inner_text()
    if not text.strip():
        return None
    imgs = pg.eval_on_selector_all(
        ".editor.editor--readonly img",
        "els => els.map(e => e.getAttribute('src'))",
    )
    return {"text": text, "imgs": imgs}


with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1680, "height": 1080})
    pg.goto(BASE, wait_until="domcontentloaded", timeout=90000)
    pg.wait_for_timeout(6000)

    expand_all(pg)
    pg.wait_for_timeout(1500)
    # 再次 dump,确认叶子已渲染
    leaf = pg.evaluate("""
        () => [...document.querySelectorAll('.ant-tree-list-holder .ant-tree-treenode')]
                 .map(n=>(n.querySelector('.ant-tree-title')||{}).textContent?.trim())
                 .filter(t=>t)
    """)
    print("leaves count:", len(leaf))
    print("has 跳转至新网址:", "跳转至新网址" in leaf)
    print("has 批量数据抓取:", "批量数据抓取" in leaf)

    saved = 0
    for title in CMDS:
        data = grab(pg, title)
        if data is None:
            print("MISS:", title)
            continue
        name = safe(title)
        asset_dir = os.path.join(OUT, "assets", name)
        os.makedirs(asset_dir, exist_ok=True)
        refs = []
        imgs = data["imgs"]
        for i, src in enumerate(imgs):
            dest = os.path.join(asset_dir, f"img{i}.png")
            download(src, dest)
            refs.append(f"assets/{name}/img{i}.png")
        parts = [f"# {title}", "", data["text"], "", "## 参数截图"]
        for rel in refs:
            parts.append(f"![{rel}]({rel})")
        open(os.path.join(OUT, name + ".md"), "w", encoding="utf-8").write("\n".join(parts))
        saved += 1
        print(f"OK {title} | text {len(data['text'])} imgs {len(refs)}")
    print("DONE saved", saved, "/", len(CMDS))
    b.close()
