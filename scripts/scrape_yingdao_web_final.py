from playwright.sync_api import sync_playwright
import sys, re, os, urllib.request, json

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
DIRS = ["网页操作", "元素操作", "数据提取", "对话框处理"]


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


def expand_dir(pg, name):
    for _ in range(4):
        st = pg.evaluate("""
          (t) => {
            const root=document.querySelector('.ant-tree-list-holder-inner');
            const nodes=root?root.querySelectorAll('.ant-tree-treenode'):document.querySelectorAll('.ant-tree-treenode');
            for(const n of nodes){
              const ti=n.querySelector('.ant-tree-title');
              const sw=n.querySelector('.ant-tree-switcher');
              if(ti && ti.textContent.trim()===t){
                const cls=sw?(sw.getAttribute('class')||''):'';
                if(cls.includes('switcher_close')){ sw.click(); return 'clicked'; }
                if(cls.includes('switcher_open')){ return 'open'; }
              }
            }
            return 'missing';
          }
        """, name)
        if st != "clicked":
            break
        pg.wait_for_timeout(700)


def click_leaf(pg, title):
    return pg.evaluate("""
      (t) => {
        const root=document.querySelector('.ant-tree-list-holder-inner');
        const nodes=root?root.querySelectorAll('.ant-tree-treenode'):document.querySelectorAll('.ant-tree-treenode');
        for(const n of nodes){
          const w=n.querySelector('.ant-tree-node-content-wrapper');
          const ti=n.querySelector('.ant-tree-title');
          if(w && ti && ti.textContent.trim()===t){
            w.scrollIntoView({block:'center'});
            w.dispatchEvent(new MouseEvent('click',{bubbles:true}));
            return true;
          }
        }
        return false;
      }
    """, title)


def h1(pg):
    return pg.evaluate("() => (document.querySelector('h1')||{}).textContent||''")


with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1680, "height": 1080})
    pg.goto(BASE, wait_until="domcontentloaded", timeout=90000)
    pg.wait_for_timeout(6000)

    for d in DIRS:
        expand_dir(pg, d)
    pg.wait_for_timeout(2000)
    print("dirs expanded")

    saved = 0
    for title in CMDS:
        ok = click_leaf(pg, title)
        if not ok:
            print("MISS(clk):", title)
            continue
        # 等 h1 匹配指令名(最多 8 次)
        for _ in range(8):
            pg.wait_for_timeout(600)
            if h1(pg).strip() == title:
                break
        if h1(pg).strip() != title:
            print("MISS(h1):", title, "->", repr(h1(pg)))
            continue
        ed = pg.locator(".editor.editor--readonly").first
        text = ed.inner_text().strip() if ed.count() else ""
        if not text:
            print("MISS(text):", title)
            continue
        imgs = pg.eval_on_selector_all(".editor.editor--readonly img",
                                       "els => els.map(e => e.getAttribute('src'))")
        name = safe(title)
        asset_dir = os.path.join(OUT, "assets", name)
        os.makedirs(asset_dir, exist_ok=True)
        refs = []
        for i, src in enumerate(imgs):
            dest = os.path.join(asset_dir, f"img{i}.png")
            download(src, dest)
            refs.append(f"assets/{name}/img{i}.png")
        parts = [f"# {title}", "", text, "", "## 参数截图"]
        for rel in refs:
            parts.append(f"![{rel}]({rel})")
        open(os.path.join(OUT, name + ".md"), "w", encoding="utf-8").write("\n".join(parts))
        saved += 1
        print(f"OK {title} | text {len(text)} imgs {len(refs)}")
    print("DONE saved", saved, "/", len(CMDS))
    b.close()
