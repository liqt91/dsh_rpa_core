from playwright.sync_api import sync_playwright
import sys, re, os, urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = "https://www.yingdao.com/yddoc/rpa/zh-CN/711591442174164992"
OUT = r"D:\Users\Administrator\Documents\代码\rpa_core\docs\yingdao-cmds-desktop"
os.makedirs(OUT, exist_ok=True)

# 用户清单: 页面真实h1 -> 文件名别名(如有差异)
CMDS = [
    ("获取窗口对象", None),
    ("获取窗口对象列表", None),
    ("点击元素(win)", None),
    ("鼠标悬停在元素上(win)", None),
    ("填写输入框(win)", None),
    ("运行或打开", None),
    ("拖拽元素(win)", None),
    ("等待元素(win)", None),
    ("填写密码框(win)", None),
    ("设置下拉框(win)", None),
    ("设置复选框(win)", None),
    ("设置元素值(win)", None),
    ("获取元素对象(win)", None),
    ("获取关联元素(win)", None),
    ("获取相似元素列表(win)", None),
    ("获取窗口对象", None),
    ("激活软件窗口", None),
    ("设置窗口状态", None),
    ("设置窗口是否显示", None),
    ("移动窗口位置", None),
    ("调整窗口大小", None),
    ("关闭软件窗口", None),
    ("常用元素属性", None),
    ("元素截图(win)", None),
    ("获取元素信息(win)", None),
    ("获取元素位置(win)", None),
    ("获取下拉框选项(win)", None),
    ("获取窗口信息", None),
    ("获取选中文本", None),
]

SUBDIRS = ["元素操作", "窗口操作", "数据提取"]


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


def expand_dir(pg, name, rounds=5):
    for _ in range(rounds):
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

    # 展开桌面软件自动化 + 子目录
    expand_dir(pg, "桌面软件自动化", rounds=5)
    pg.wait_for_timeout(1000)
    for d in SUBDIRS:
        expand_dir(pg, d, rounds=5)
    pg.wait_for_timeout(2000)

    # dump 确认
    all_nodes = pg.evaluate("""
        () => [...document.querySelectorAll('.ant-tree-treenode')]
            .map(n => {
              const t=n.querySelector('.ant-tree-title');
              const sw=n.querySelector('.ant-tree-switcher');
              const cls=sw?(sw.getAttribute('class')||''):'';
              return {text:t?t.textContent.trim():'', isleaf: cls.includes('noop')};
            })
    """)
    leaf_names = [n["text"] for n in all_nodes if n["isleaf"] and n["text"]]
    print("total leaves:", len(leaf_names))

    # check which CMDS are in tree
    for title, alias in CMDS:
        in_tree = title in leaf_names
        print(f"  {'OK' if in_tree else 'MISS'}: {title}")

    saved = 0
    for title, alias in CMDS:
        ok = click_leaf(pg, title)
        if not ok:
            print("MISS(clk):", title)
            continue
        # wait h1 match (allow alias)
        h1target = alias or title
        for _ in range(8):
            pg.wait_for_timeout(700)
            cur = h1(pg).strip()
            if cur == h1target or cur == title:
                break
        if h1(pg).strip() not in (title, h1target):
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
