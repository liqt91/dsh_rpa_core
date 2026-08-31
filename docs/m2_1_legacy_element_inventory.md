# M2.1 旧元素库静态盘点

## 来源

- `tests/fixtures/legacy-elements/elements.json`

## 盘点范围

- `web` 元素：仅接受静态 selector 候选，当前支持 `css` 和 `xpath`。
- `uia` 元素：仅接受 `uia_path`，导入 leaf node 的 `automation_id`、`control_type`、`name`。
- `win32` 元素：仅接受 `win32_path`，导入 leaf node 的 `title`、`class_name`、`control_id`、`foundIndex`。

## 保留信息

- `sourceFile`
- `sourceIndex`
- `elementId`
- `legacyIdentity`

## 诊断约定

- `WEB_NO_SELECTOR`
- `WEB_IGNORED_FIELDS`
- `UIA_IGNORED_FIELDS`
- `DESKTOP_PATH_MISSING`
- `CATALOG_EXTRA_KEYS`

## 说明

导入器仅处理静态数据，不导入或执行 `rpa_script` runtime 代码。
