"""M60 S1 契约：``ocr.recognize``（catalog + 执行器 + worker 三层）。

按 ``invariants.md`` #12（stable 命令需要 catalog / contract / runtime integration 三件套）：

- **catalog 层**：manifest 与实现的 executor / kind / risk / effect / errors 一致；
- **contract 层**：``OcrExecutor`` 的输入互斥守卫与输出形状；
- **runtime integration**：真起 worker 子进程跑 ddddocr，断言识别结果。

★ 两条判据值得单独说明：

1. **体积守卫**：ddddocr 的 ``probability=True`` 会额外返回按整个字符集的概率矩阵
   （实测单张即达数 MB）与 ``charset`` 表。它们**绝不能进 CommandResult**
   （会撑爆 ``result.json``）——这里既断言键不存在，也对整个结果的序列化体积设上限。
2. **常驻守卫**：同一个 executor 的多次调用必须复用**同一个**子进程
   （ddddocr import + 首次构造 + 首次推理有一次性开销，见 ``workers/ocr_worker.py``）。

图片用**内联 base64**（64x26 PNG，内容 ``58``）而不是磁盘 fixture：判据不依赖字体文件，
且与 ``tests/commands/cases/ocr.json`` 用的是同一张图，两端可对照。
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

from rpa_core.catalog import load_catalog
from rpa_core.executors import OcrExecutor
from rpa_core.model.command import CommandInvocation, CommandKind
from rpa_core.model.errors import ErrorCode
from rpa_core.workers import ocr_worker

ROOT = Path(__file__).resolve().parents[2]
CAPTCHA_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAAAaCAIAAACIHs0YAAACVklEQVR42u2Xu2vqUBzHTzS+glIj+FrUQUFB6gOj"
    "URAl2E2hs5N0EVzcC479JwSLguILHaTQQenUQV1EBJ0UHBXRGi2oseQOKaL3drtDLOQ75ZcvgXw43+85CUTTNPjN"
    "4oFfLg6AA+AALmW326FL3d/fn9z393eCIBQKhUqlCofDg8GAdQD4fKAoajQaBYPBQCBwumk2m5mLbrdLEITT6Uyn"
    "0xRFPT094Tje6XSsViubBPSZ+v0+AKBQKNA/KRaLSSSS9XrNjNPpFACQSCRoVnURIQbA4XD8iLpcLoVCoVQqZUaV"
    "SgXD8GazuaIO9Pt9BEG63a7JZBKLxRiGvby8nNyHhweSJJPJ5GKxmM1m8XicWRaWS3C+HKFQiM/nB4PBer2ezWZv"
    "b28hCDpPVC6Xg+Hv2ggEglqtRrOtCwAcxz0ez+FwYMbPz0+dTqdUKvf7PU3TxWIRhmGCIMrlcj6fxzAMQZC3t7cr"
    "AvhXj4+PAIB2u73dblEUxTDs6+uLsXa7ncVi0ev1x+PxWkr88fHxVynVajUAgCTJ4XC4Wq2i0SiP9/2ISCSKRqPT"
    "6XQ8Hl9FiSeTCYqiqVTq3GZezmAwMF/dfD7/4hCBYQDA8Xi8lhIbjUatVkuSJDPO53O5XO5yuZjAoCiK4/gpQhRF"
    "2Ww2jUbDboQuAJrNJgRBbre7UqlkMhmj0SiTyXq9HuM+Pz8DAO7u7mq1WqlU8vv9PB6vWq1eV4lfX1+9Xq9YLL65"
    "uYlEIoPB4NxtNBo+n08ikSAIEggEWq0W69soxP1ScgAcAAfAAfyP/gBjnwScjBKmqQAAAABJRU5ErkJggg=="
)
MANIFEST_PATH = ROOT / "commands" / "ocr" / "recognize.json"


def _invocation(**inputs: object) -> CommandInvocation:
    return CommandInvocation(
        command_id="ocr.recognize",
        command_version="1.0.0",
        run_id="run",
        step_id="ocr",
        inputs=dict(inputs),
    )


def test_manifest_declares_the_contract_the_executor_implements():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["id"] == "ocr.recognize"
    assert manifest["executor"] == "ocr.ddddocr"
    assert manifest["kind"] == "query"
    assert manifest["risk"] == "read"
    assert manifest["stability"] == "stable"
    assert manifest["effect"] == {"kind": "pure", "replay": "safe", "idempotency": "none"}
    assert set(manifest["errors"]) >= {"INVALID_INPUT", "EXECUTOR_FAILED"}
    assert set(manifest["input_schema"]["properties"]) == {
        "imagePath",
        "imageBase64",
        "withProbability",
        "modelPath",
    }


def test_catalog_loads_the_ocr_command_with_its_own_executor():
    catalog = load_catalog(ROOT / "commands")
    spec = catalog["ocr.recognize"]
    assert spec.executor == "ocr.ddddocr"
    assert spec.kind == CommandKind.QUERY


def test_executor_name_is_registered_where_the_cli_builds_the_registry():
    """manifest 的 `executor` 名必须真的在 CLI 的 registry 里注册。

    名字写错不会在 catalog 加载时暴露，而是运行期 `ExecutorRegistry.get()` 抛 KeyError
    ——这条判据把「改名忘了同步」挡在测试里。
    """
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    cli_source = (ROOT / "src" / "rpa_core" / "cli.py").read_text(encoding="utf-8")
    assert f'"{manifest["executor"]}":' in cli_source


def test_recognize_reads_path_and_base64_and_keeps_one_resident_process(tmp_path):
    """runtime integration：真跑 ddddocr，并验证常驻复用。"""
    png = tmp_path / "captcha.png"
    png.write_bytes(base64.b64decode(CAPTCHA_PNG_B64))

    async def main():
        executor = OcrExecutor()
        try:
            results, pids = [], []
            scenarios = (
                {"imagePath": str(png)},
                {"imageBase64": CAPTCHA_PNG_B64},
                {"imageBase64": CAPTCHA_PNG_B64, "modelPath": ""},
            )
            for inputs in scenarios:
                results.append(await executor.execute(_invocation(**inputs), asyncio.Event()))
                assert executor._process is not None
                pids.append(executor._process.pid)
            return results, pids
        finally:
            await executor.close()

    results, pids = asyncio.run(main())
    for result in results:
        assert result.status == "success", result.error
        assert result.outputs["text"] == "58"
    assert len(set(pids)) == 1, f"常驻 worker 被重复拉起：{pids}"


def test_probability_reports_confidence_without_dumping_the_probability_matrix():
    """★ 体积守卫：`probability=True` 的巨大数组不得进入结果。"""

    async def main():
        executor = OcrExecutor()
        try:
            return await executor.execute(
                _invocation(imageBase64=CAPTCHA_PNG_B64, withProbability=True), asyncio.Event()
            )
        finally:
            await executor.close()

    result = asyncio.run(main())
    assert result.status == "success", result.error
    assert result.outputs["text"] == "58"
    assert set(result.outputs) == {"text", "confidence"}
    assert 0.0 <= result.outputs["confidence"] <= 1.0
    serialized = json.dumps(result.model_dump(), ensure_ascii=False)
    assert len(serialized) < 4096, f"OCR 结果异常膨胀（{len(serialized)} 字节）"


def test_image_input_guard_rejects_missing_and_duplicate_inputs(tmp_path):
    png = tmp_path / "captcha.png"
    png.write_bytes(base64.b64decode(CAPTCHA_PNG_B64))

    async def main():
        executor = OcrExecutor()
        try:
            neither = await executor.execute(_invocation(), asyncio.Event())
            both = await executor.execute(
                _invocation(imagePath=str(png), imageBase64=CAPTCHA_PNG_B64), asyncio.Event()
            )
            missing = await executor.execute(
                _invocation(imagePath=str(tmp_path / "no-such.png")), asyncio.Event()
            )
            return neither, both, missing
        finally:
            await executor.close()

    neither, both, missing = asyncio.run(main())
    for result in (neither, both, missing):
        assert result.status == "error"
        assert result.error is not None
        assert result.error.code == ErrorCode.INVALID_INPUT


def test_close_releases_the_resident_process():
    async def main():
        executor = OcrExecutor()
        result = await executor.execute(_invocation(imageBase64=CAPTCHA_PNG_B64), asyncio.Event())
        assert result.status == "success", result.error
        assert executor.active_process_count == 1
        await executor.close()
        return executor.active_process_count

    assert asyncio.run(main()) == 0


def test_cancel_during_startup_is_reported_and_leaves_no_child():
    """取消必须在「等待 worker 响应」期间生效，且不泄漏子进程（M60 验收口径 5）。"""

    async def main():
        executor = OcrExecutor()
        cancellation = asyncio.Event()
        task = asyncio.create_task(
            executor.execute(_invocation(imageBase64=CAPTCHA_PNG_B64), cancellation)
        )
        await asyncio.sleep(0.05)  # 此刻仍在 spawn / 首次推理，远未完成
        cancellation.set()
        result = await task
        count = executor.active_process_count
        await executor.close()
        return result.status, count

    status, count = asyncio.run(main())
    assert status == "cancelled"
    assert count == 0


def test_worker_handle_is_a_pure_function_for_protocol_errors():
    unknown = ocr_worker.handle({"id": 1, "commandId": "ocr.nope", "inputs": {}})
    assert unknown == {
        "id": 1,
        "ok": False,
        "code": "COMMAND_NOT_FOUND",
        "message": "ocr worker does not handle 'ocr.nope'",
    }
    empty = ocr_worker.handle({"id": 2, "commandId": "ocr.recognize", "inputs": {}})
    assert empty["id"] == 2
    assert empty["ok"] is False
    assert empty["code"] == "INVALID_INPUT"
