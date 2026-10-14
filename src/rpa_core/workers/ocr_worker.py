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

# ddddocr 实例缓存：键 = (模式, 自定义模型路径, 字符集)。
# 同一进程内复用，落实「不要反复初始化」；跨模式/跨模型/跨字符集各自一份。
_ENGINES: dict[tuple[str, str, str], Any] = {}


class _OcrInputError(Exception):
    """输入不合法（互斥、缺失、文件不存在、base64 非法）→ ``INVALID_INPUT``。"""


class _OcrNoMatch(Exception):
    """滑块匹配低于用户给的 ``minConfidence`` → ``EXECUTOR_FAILED``。

    为什么不并进 ``_OcrInputError``：**输入本身是合法的**，是这一对图匹配得不够好。
    也不新造错误码：M60 定过「能用既有码就不扩枚举」，留待 S4 统一裁决。
    """


def _engine(mode: str, model_path: str, charset_range: str) -> Any:
    """按 ``(模式, 自定义模型路径, 字符集)`` 取/建 ddddocr 实例。

    ★ ``charset_range`` **必须进缓存键**：`set_ranges` 改的是**实例自身的状态**，
    不是构造参数。共用一份实例的话，A 请求设过 `"0123456789"` 之后，B 请求（没填该项）
    会**悄悄沿用** A 的字符集——串味且极难查。进键的代价是「每个不同字符集一份实例」
    （各付一次构造 ~0.13s 与一份模型内存），换请求之间互不影响。

    ★ det 与 slide 两个模式都**不接** `import_onnx_path`，两条都是实测结论：

    - `det=True` 时构造函数走第一个分支、**根本不透传** `import_onnx_path`
      （传了也不报错，就是被忽略）⇒ `ocr.detect` 声明 `modelPath` 会是一个**什么都不做的参数**，
      所以 manifest 里**不声明**它；
    - `det=False, ocr=False` 但 `import_onnx_path` 非空时，构造函数会落进
      `elif ocr or import_onnx_path` 去建 **OCR** 引擎（实测抛 `ModelLoadError`），
      滑块模式就被顶掉了 ⇒ 这里必须传空串。
    """
    key = (mode, model_path, charset_range)
    engine = _ENGINES.get(key)
    if engine is not None:
        return engine
    import ddddocr

    if mode == "recognize":
        engine = ddddocr.DdddOcr(show_ad=False, import_onnx_path=model_path)
        if charset_range:
            # 实测：真名是 compat/v1 的 ``set_ranges``——**不是** ``set_charset_range``，
            # 那个方法压根不存在（M60 S1 曾据此误判「字符集收敛不可用」，见
            # `.harness/tasks/M60-ocr-captcha.md` §8.3 ① 的订正）。
            # 另注：它只在「模型认得的字符」里生效，不认得的字符被**静默丢弃**。
            engine.set_ranges(charset_range)
    elif mode == "detect":
        engine = ddddocr.DdddOcr(det=True, show_ad=False)
    elif mode == "slide":
        engine = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)
    else:  # pragma: no cover - 其余模式未接入
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


def recognize(
    image: bytes, *, model_path: str, charset_range: str, with_probability: bool
) -> dict[str, Any]:
    """``ocr.recognize`` 的实现：图片字节 → 文本（可选置信度）。

    ``charset_range`` 非空时收敛字符集（ddddocr 的 `set_ranges`）。★ 它**不是**白名单过滤：
    只有「模型字符集里认得的字符」才生效，不认得的字符被**静默丢弃**；若一个都不认得，
    有效索引为空、识别结果退化成**空串**（实测 `set_ranges("abc")` 对数字图 → `''`）。
    这是库的行为，不是本层的降级，故如实透传、由用户在流程里判。
    """
    engine = _engine("recognize", model_path, charset_range)
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


def detect(image: bytes) -> dict[str, Any]:
    """``ocr.detect`` 的实现：图片字节 → 目标框列表。

    ddddocr 回的是 ``[x1, y1, x2, y2]`` 四元列表；这里转成与 ``browser.getPosition``
    **同形状**的 ``{x, y, width, height}``，免得两条命令的坐标口径各说各话。

    ★ 已知边界（库的行为，如实登记）：**不保证找全**。实测四个分开字符的图只回了 3 个框
    （漏掉最左那个），所以下游不能假设「框数 == 字符数」。
    """
    engine = _engine("detect", "", "")
    items: list[dict[str, int]] = []
    for box in engine.detection(image):
        left, top, right, bottom = (int(value) for value in box)
        items.append({"x": left, "y": top, "width": right - left, "height": bottom - top})
    return {"boxes": items, "count": len(items)}


def slide_match(
    template: bytes, background: bytes, *, simple_target: bool, min_confidence: float
) -> dict[str, Any]:
    """``ocr.slideMatch`` 的实现：滑块块图 + 背景图 → 缺口中心坐标（+ 相关系数）。

    ★ 三条**必须写进文档**的一手事实（`core/slide_engine.py` 实测）：

    1. ``target_x`` / ``target_y`` 是匹配区域的**中心**坐标（``max_loc + 尺寸 // 2``），
       **不是左边界**——算拖动距离时要减掉滑块自身的一半宽高；
    2. ``confidence`` 是 ``cv2.matchTemplate(TM_CCOEFF_NORMED)`` 的**归一化相关系数**
       （取值 [-1, 1]），**不是概率**；
    3. 它**没有「找不到」分支**：恒返回最佳位置；底图退化（如纯色）时照样回
       ``confidence == 1.0``。⇒「匹配失败」在返回体里**根本看不出来**，
       只能靠用户显式开启的 ``minConfidence`` 拦（默认 0 = 不拦、原样透传）。
    """
    engine = _engine("slide", "", "")
    raw = engine.slide_match(template, background, simple_target=simple_target)
    confidence = _as_float(raw.get("confidence"))
    if min_confidence > 0 and (confidence is None or confidence < min_confidence):
        raise _OcrNoMatch(
            f"slide match confidence {confidence!r} is below minConfidence {min_confidence}"
        )
    return {
        "x": int(raw.get("target_x", 0)),
        "y": int(raw.get("target_y", 0)),
        "confidence": confidence,
    }


def handle(request: dict[str, Any]) -> dict[str, Any]:
    """单条请求 → 单条响应。

    ★ 分派写成**显式 if 链**，而不是 ``_HANDLERS[command_id]`` 注册表：

    两条静态门禁（``check_param_consumption`` / ``check_error_contract``）按
    `if command_id == "<id>"` 的字面量切片判「这条命令读了哪些参数」「能返回哪些错误码」。
    注册表分派两者都读不到——命令会被算作「已检查」而实际零覆盖（假绿）。
    新增 ``ocr.*`` 命令时**照抄一个分支**，把该命令**读取的参数**与**能返回的错误码**
    都写进分支体；错误码一律走 ``ErrorCode.X``（不要写字面量字符串）。

    已接入三条：``ocr.recognize``（图片 → 文本，可选字符集收敛与置信度）、
    ``ocr.detect``（图片 → 目标框）、``ocr.slideMatch``（滑块块图 + 背景图 → 缺口坐标）。
    """
    request_id = request.get("id")
    command_id = str(request.get("commandId") or "")
    inputs = request.get("inputs") or {}
    if command_id == "ocr.recognize":
        try:
            outputs = recognize(
                _decode_image(inputs.get("imagePath"), inputs.get("imageBase64")),
                model_path=str(inputs.get("modelPath") or ""),
                charset_range=str(inputs.get("charsetRange") or ""),
                with_probability=bool(inputs.get("withProbability")),
            )
        except _OcrInputError as exc:
            return _failure(request_id, ErrorCode.INVALID_INPUT, str(exc))
        except Exception as exc:  # noqa: BLE001 - ddddocr 对坏图抛 ImageProcessError 等
            return _failure(request_id, ErrorCode.EXECUTOR_FAILED, f"{type(exc).__name__}: {exc}")
        return {"id": request_id, "ok": True, "outputs": outputs}
    if command_id == "ocr.detect":
        try:
            outputs = detect(_decode_image(inputs.get("imagePath"), inputs.get("imageBase64")))
        except _OcrInputError as exc:
            return _failure(request_id, ErrorCode.INVALID_INPUT, str(exc))
        except Exception as exc:  # noqa: BLE001 - 坏图抛 ImageProcessError
            return _failure(request_id, ErrorCode.EXECUTOR_FAILED, f"{type(exc).__name__}: {exc}")
        return {"id": request_id, "ok": True, "outputs": outputs}
    if command_id == "ocr.slideMatch":
        try:
            outputs = slide_match(
                _decode_image(
                    inputs.get("templateImagePath"), inputs.get("templateImageBase64")
                ),
                _decode_image(
                    inputs.get("backgroundImagePath"), inputs.get("backgroundImageBase64")
                ),
                simple_target=bool(inputs.get("simpleTarget")),
                min_confidence=float(inputs.get("minConfidence") or 0.0),
            )
        except _OcrInputError as exc:
            return _failure(request_id, ErrorCode.INVALID_INPUT, str(exc))
        except _OcrNoMatch as exc:
            # 坐标是「最佳猜测」而不是「找不到」（库没有该分支）⇒ 用户开了阈值却不达标时
            # 必须**报错**，否则错坐标会被当成功一路拖下去。
            return _failure(request_id, ErrorCode.EXECUTOR_FAILED, str(exc))
        except Exception as exc:  # noqa: BLE001 - 坏图抛 ImageProcessError
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
