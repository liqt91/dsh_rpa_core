"""M109 取证：取消层级后，该层属性即便还勾着也不进选择器、属性区只读。

两条路都走真代码：
- 纯函数层：`selector_tree.compose`（网页层级）与 `desktop_editing.compose_selector`；
- 控件层：真建隐藏窗口，读 BM_GETCHECK / IsWindowEnabled / 预览文本。
"""

from __future__ import annotations

import json
import pathlib
import time

import win32con
import win32gui

from rpa_core.capture_kit import desktop_editing, element_editing, selector_tree
from rpa_core.capture_kit.edit_dialog import ElementEditDialog

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _hide_first_show():
    orig_show = win32gui.ShowWindow

    state = {"first": True}

    def patched(hwnd, cmd):
        if state["first"] and cmd == win32con.SW_SHOW:
            state["first"] = False
            return None
        return orig_show(hwnd, cmd)

    return orig_show, patched


def web_probe() -> None:
    doc = json.loads(
        (ROOT / "workflows/xiaohongshu/elements/小红书输入框1.json").read_text(
            encoding="utf-8"
        )
    )
    model = element_editing.build_edit_model(doc, name=doc.get("name", ""))
    levels = model["levels"]
    print("[web] levels", len(levels), "before:", selector_tree.compose(levels))

    orig_show, patched = _hide_first_show()
    win32gui.ShowWindow = patched
    try:
        dlg = ElementEditDialog()
        dlg._place = lambda *a: None
        dlg.show(title="probe", name="probe", levels=levels, selector=model["selector"])
        for _ in range(300):
            if dlg._hwnd and len(dlg._level_boxes) == len(levels):
                break
            time.sleep(0.01)
        time.sleep(0.15)
        hwnd = dlg._hwnd

        # 选一个当前勾着、且不是目标级的层
        index = next(
            i for i, level in enumerate(levels[:-1]) if level["included"]
        )
        # 点它的层级行（选中看属性）：cid 从 _control_map 反查，别拿 index 当 cid
        level_cid = next(
            cid
            for cid, meta in dlg._control_map.items()
            if meta[0] == "level" and meta[1] == index
        )
        win32gui.SendMessage(
            hwnd, win32con.WM_COMMAND, level_cid, dlg._level_boxes[index]
        )
        time.sleep(0.15)
        attr_ids = [
            cid for cid, meta in dlg._control_map.items() if meta[0] == "check"
        ]
        before_enabled = [win32gui.IsWindowEnabled(win32gui.GetDlgItem(hwnd, cid)) for cid in attr_ids]
        attr_checked = [
            bool(win32gui.SendMessage(win32gui.GetDlgItem(hwnd, cid), win32con.BM_GETCHECK, 0, 0))
            for cid in attr_ids
        ]
        print(f"[web] 层 {index} 勾选前：属性框可编辑={before_enabled} 属性勾选={attr_checked}")

        # 取消这层的勾选（点左端小方框）：SETCHECK 只改控件态，
        # 真正的状态迁移在窗口过程的 BN_CLICKED 分支里，所以要补一条 WM_COMMAND。
        check_hwnd = dlg._level_check_boxes[index]
        check_cid = next(
            cid
            for cid, meta in dlg._control_map.items()
            if meta[0] == "level_check" and meta[1] == index
        )
        win32gui.SendMessage(check_hwnd, win32con.BM_SETCHECK, 0, 0)
        win32gui.SendMessage(hwnd, win32con.WM_COMMAND, check_cid, check_hwnd)
        time.sleep(0.15)

        after_enabled = [
            win32gui.IsWindowEnabled(win32gui.GetDlgItem(hwnd, cid)) for cid in attr_ids
        ]
        after_checked = [
            bool(win32gui.SendMessage(win32gui.GetDlgItem(hwnd, cid), win32con.BM_GETCHECK, 0, 0))
            for cid in attr_ids
        ]
        preview = win32gui.GetWindowText(dlg._preview_edit)
        composed = selector_tree.compose(dlg._levels)
        print(f"[web] 层 {index} 取消后：属性框可编辑={after_enabled} 属性勾选={after_checked}")
        print("[web] 预览:", preview.replace("\r\n", " "))
        print("[web] compose:", composed)
        assert dlg._levels[index]["included"] is False
        assert not any(after_enabled), "未参与层的属性框还能编辑"
        assert after_checked == attr_checked, "取消层级时属性勾选被清掉了（应保留）"
        assert composed == preview, "预览与内核重组不一致"
        assert preview == model["selector"] or preview, "预览为空"
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        time.sleep(0.2)
    finally:
        win32gui.ShowWindow = orig_show


def desktop_probe() -> None:
    # 工作区里的桌面元素都只有「窗口 + 目标」两层，造一条三层链来验中间层。
    doc = {
        "kind": "desktop",
        "metadata": {"windowTitle": "微信", "className": "WeChatMainWndForPC"},
        "selector": {
            "locator": {
                "controlType": "Button",
                "name": "发送",
                "className": "Button",
                "path": [
                    {"controlType": "Pane", "name": "会话区", "className": "MiddlePane"},
                    {"controlType": "Pane", "name": "输入栏", "className": "InnerPane"},
                ],
            }
        },
    }
    nodes = desktop_editing.nodes_from_descriptor(doc)
    print("[desk] nodes", len(nodes))
    idents = [f"{n.get('type')}/{n.get('name')}" for n in nodes]
    print("[desk] 链:", " > ".join(idents))
    base = desktop_editing.compose_selector(doc, nodes)
    base_path = list(base.get("locator", {}).get("path") or [])
    print("[desk] before path:", base_path)

    # 取一个非目标、非只读、且当前参与的中间层，取消它
    index = None
    for i, node in enumerate(nodes[:-1]):
        if node.get("selected") and not node.get("readonly"):
            index = i
    assert index is not None, "没有可取消的中间层"
    nodes[index]["selected"] = False
    # 该层的属性保持 required=True（用户场景：属性还勾着）
    still_on = [row["field"] for row in nodes[index].get("attributes") or [] if row.get("required")]
    after = desktop_editing.compose_selector(doc, nodes)
    path = list(after.get("locator", {}).get("path") or [])
    print(f"[desk] 取消第 {index} 层（属性仍勾：{still_on}）→ path {len(path)} 步：{path}")
    assert len(path) == len(base_path) - 1, f"取消层后 path 步数没少：{len(path)} vs {len(base_path)}"
    name = str(nodes[index].get("name") or "")
    if name:
        assert all(name not in json.dumps(step, ensure_ascii=False) for step in path), (
            f"取消的层 {name} 还留在 path 里"
        )

    orig_show, patched = _hide_first_show()
    win32gui.ShowWindow = patched
    try:
        dlg = ElementEditDialog()
        dlg._place = lambda *a: None
        dlg.show(title="probe", name="probe", descriptor=doc, nodes=nodes, selector="")
        for _ in range(300):
            if dlg._hwnd and len(dlg._node_boxes) == len(nodes):
                break
            time.sleep(0.01)
        time.sleep(0.15)
        hwnd = dlg._hwnd
        # 选中该层（桌面节点的映射在 _desktop_control_map，不是 _control_map）
        node_cid = next(
            cid
            for cid, meta in dlg._desktop_control_map.items()
            if meta[0] == "node" and meta[1] == index
        )
        win32gui.SendMessage(
            hwnd, win32con.WM_COMMAND, node_cid, win32gui.GetDlgItem(hwnd, node_cid)
        )
        time.sleep(0.25)
        ids = [cid for cid, meta in dlg._desktop_control_map.items() if meta[0] == "attr_check"]
        enabled = [win32gui.IsWindowEnabled(win32gui.GetDlgItem(hwnd, cid)) for cid in ids]
        checked = [
            bool(win32gui.SendMessage(win32gui.GetDlgItem(hwnd, cid), win32con.BM_GETCHECK, 0, 0))
            for cid in ids
        ]
        print(f"[desk] 属性框可编辑={enabled} 勾选={checked}")
        assert checked and not any(enabled), "未参与节点的属性框还能编辑"
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        time.sleep(0.2)
    finally:
        win32gui.ShowWindow = orig_show


if __name__ == "__main__":
    web_probe()
    desktop_probe()
    print("M109 PROBE OK")
