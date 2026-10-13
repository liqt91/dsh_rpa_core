"""OCR worker：承载 ddddocr 推理的**常驻**子进程。

为什么必须独立进程（而不是在编排进程内直接调 ddddocr）：

- ddddocr 是 C 扩展（onnxruntime）+ 全局单例模型，官方明说不要反复初始化；
- 实测一次性开销：``import ddddocr`` 1.11s、首次构造 ``DdddOcr`` 0.13s、首次推理 0.44s；
- 放主编排进程会阻塞事件循环，且单例状态跨 run 污染。

为什么是「常驻」而不是像 ``python_worker`` 那样「一命令一进程」：

- 每次 spawn 都要重付 import + 构造 + 首次推理（实测合计约 1.7s）；
- 流程里连续识别多张图时，常驻把后续每次压到约 0.01s。

协议（stdin/stdout，**一行一 JSON**，便于同一进程多轮往返）：

    请求  {"id": <int>, "commandId": "ocr.recognize", "inputs": {...}}
    响应  {"id": <int>, "ok": true,  "outputs": {...}}
          {"id": <int>, "ok": false, "code": "INVALID_INPUT", "message": "..."}

stdin 关闭即退出。

★ 分派为什么是**显式 if 链**而不是 handler 注册表（见 `handle` 的 docstring）：
两条静态门禁按 `if command_id == "<id>"` 的字面量切片判「参数读了没」「错误码声明了没」，
注册表分派会让新命令**看着被检查、其实零覆盖**。
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

from rpa_core.model.errors import ErrorCode

# ddddocr 实例缓存：键 = (模式, 自定义模型路径)。
# 同一进程内复用，落实「不要反复初始化」；跨模式/跨模型各自一份。
_ENGINES: dict[tuple[str, str], Any] = {}


class _OcrInputError(Exception):
    """输入不合法（互斥、缺失、文件不存在、base64 非法）→ ``INVALID_INPUT``。"""


def _engine(mode: str, model_path: str) -> Any:
    key = (mode, model_path)
    engine = _ENGINES.get(key)
    if engine is not None:
        return engine
    import ddddocr

    if mode == "recognize":
        engine = ddddocr.DdddOcr(show_ad=False, import_onnx_path=model_path)
    else:  # pragma: no cover - 其余模式由后续切片接入
        raise _OcrInputError(f"unsupported ocr mode: {mode}")
    _ENGINES[key] = engine
    return engine


def _decode_image(path: Any, encoded: Any) -> bytes:
    """图片输入解码：``imagePath`` 与 ``imageBase64`` **恰好给一个**。

    两者都给或都不给都判 ``INVALID_INPUT``（两条路径分开判，不合并成一条）。
    取值（``inputs.get("imagePath")``）留在调用处，是为了让参数门禁看得见字面量键。
    """
    if bool(path) == bool(encoded):
        raise _OcrInputError("exactly one of imagePath / imageBase64 must be provided")
    if path:
        candidate = Path(str(path))
        if not candidate.is_file():
            raise _OcrInputError(f"image file not found: {path}")
        return candidate.read_bytes()
    try:
        return base64.b64decode(str(encoded), validate=True)
    except Exception as exc:  # noqa: BLE001 - binascii.Error 等多种解码异常统一归类
        raise _OcrInputError(f"imageBase64 is not valid base64: {exc}") from exc


def _as_float(value: Any) -> float | None:
    """把 numpy 标量等转成 JSON 可序列化的 float；不可转则 None。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _failure(request_id: Any, code: ErrorCode, message: str) -> dict[str, Any]:
    return {"id": request_id, "ok": False, "code": code.value, "message": message}


def recognize(image: bytes, *, model_path: str, with_probability: bool) -> dict[str, Any]:
    """``ocr.recognize`` 的实现：图片字节 → 文本（可选置信度）。"""
    engine = _engine("recognize", model_path)
    if not with_probability:
        return {"text": str(engine.classification(image))}
    raw = engine.classification(image, probability=True)
    # ★ probability=True 同时返回 `probabilities`（按整个字符集的概率矩阵，
    #   实测单张即可达数 MB）与 `charset`（数千项的表）——**绝不透传**，
    #   只取 text 与 confidence，否则会撑爆 result.json。
    return {
        "text": str(raw.get("text", "")),
        "confidence": _as_float(raw.get("confidence")),
    }


def handle(request: dict[str, Any]) -> dict[str, Any]:
    """单条请求 → 单条响应。

    ★ 分派写成**显式 if 链**，而不是 ``_HANDLERS[command_id]`` 注册表：

    两条静态门禁（``check_param_consumption`` / ``check_error_contract``）按
    `if command_id == "<id>"` 的字面量切片判「这条命令读了哪些参数」「能返回哪些错误码」。
    注册表分派两者都读不到——命令会被算作「已检查」而实际零覆盖（假绿）。
    新增 ``ocr.*`` 命令时**照抄一个分支**，把该命令**读取的参数**与**能返回的错误码**
    都写进分支体；错误码一律走 ``ErrorCode.X``（不要写字面量字符串）。
    """
    request_id = request.get("id")
    command_id = str(request.get("commandId") or "")
    inputs = request.get("inputs") or {}
    if command_id == "ocr.recognize":
        try:
            outputs = recognize(
                _decode_image(inputs.get("imagePath"), inputs.get("imageBase64")),
                model_path=str(inputs.get("modelPath") or ""),
                with_probability=bool(inputs.get("withProbability")),
            )
        except _OcrInputError as exc:
            return _failure(request_id, ErrorCode.INVALID_INPUT, str(exc))
        except Exception as exc:  # noqa: BLE001 - ddddocr 对坏图抛 ImageProcessError 等
            return _failure(request_id, ErrorCode.EXECUTOR_FAILED, f"{type(exc).__name__}: {exc}")
        return {"id": request_id, "ok": True, "outputs": outputs}
    # 注意：这条兜底 return 留在 If 之外（别顺手改成 `else:`）。
    # 门禁判「本期未实现」的依据是**分支体**里出现 COMMAND_NOT_FOUND；
    # `if cmd == "ocr.recognize": ... else: return COMMAND_NOT_FOUND` 这种最自然的分派
    # 写法曾让它被整条静默豁免（口径缺陷已修：只看 `node.body`，负向验证见
    # `.harness/spike/probe_m60_ocr_gates_negative.py` 第 ③ 向）。
    return _failure(
        request_id,
        ErrorCode.COMMAND_NOT_FOUND,
        f"ocr worker does not handle {command_id!r}",
    )


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 - 非交互场景 reconfig 不可用时保持默认
        pass
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response: dict[str, Any] = {
                "id": None,
                "ok": False,
                "code": ErrorCode.INVALID_INPUT.value,
                "message": f"malformed request line: {exc}",
            }
        else:
            response = handle(request)
        print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
