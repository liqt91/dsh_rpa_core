import json

from rpa_core.importer import LegacyElementImporter


def test_legacy_element_importer_handles_web_and_desktop(tmp_path):
    data = {
        "version": 1,
        "source": "legacy-elements.json",
        "elements": [
            {
                "id": "web-search",
                "element_type": "web",
                "candidates": [{"kind": "css", "selector": "#query"}],
                "extra_field": "ignored",
            },
            {
                "id": "desk-name",
                "element_type": "uia",
                "extra_uia": "ignored",
                "uia_path": [
                    {"automation_id": "root", "control_type": "WindowControl", "name": "Root"},
                    {"automation_id": "nameInput", "control_type": "Edit", "name": ""},
                ],
            },
            {
                "id": "desk-open",
                "element_type": "win32",
                "win32_path": [
                    {"class_name": "#32770", "title": "打开", "control_id": 0},
                    {"class_name": "Edit", "title": "", "control_id": 1148, "index": 0},
                ],
                "unexpected": True,
            },
        ],
    }
    source = tmp_path / "elements.json"
    source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    catalog = LegacyElementImporter(source).import_data(data)
    assert catalog.source_file == str(source)
    assert len(catalog.elements) == 3
    assert catalog.elements[0].locator.backend == "uia"
    assert catalog.elements[1].locator.automation_id == "nameInput"
    assert catalog.elements[2].locator.backend == "win32"
    assert any(item.code == "CATALOG_EXTRA_KEYS" for item in catalog.diagnostics)
    assert any(item.code == "WEB_IGNORED_FIELDS" for item in catalog.diagnostics)
    assert any(item.code == "UIA_IGNORED_FIELDS" for item in catalog.diagnostics)


def test_legacy_element_importer_rejects_missing_desktop_path(tmp_path):
    data = {"version": 1, "elements": [{"id": "bad", "element_type": "win32"}]}
    source = tmp_path / "elements.json"
    source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    catalog = LegacyElementImporter(source).import_data(data)
    assert catalog.elements == []
    assert catalog.diagnostics[0].code == "DESKTOP_PATH_MISSING"
