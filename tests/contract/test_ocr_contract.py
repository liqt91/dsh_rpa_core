"""M60 S1/S2/S3 契约：``ocr`` 指令族（catalog + 执行器 + worker 三层）。

按 ``invariants.md`` #12（stable 命令需要 catalog / contract / runtime integration 三件套）：

- **catalog 层**：manifest 与实现的 executor / kind / risk / effect / errors 一致；
- **contract 层**：``OcrExecutor`` 的输入互斥守卫与输出形状；
- **runtime integration**：真起 worker 子进程跑 ddddocr，断言识别结果。

★ 三条判据值得单独说明：

1. **体积守卫**（S1）：ddddocr 的 ``probability=True`` 会额外返回按整个字符集的概率矩阵
   （实测单张即达数 MB）与 ``charset`` 表。它们**绝不能进 CommandResult**
   （会撑爆 ``result.json``）——这里既断言键不存在，也对整个结果的序列化体积设上限。
2. **常驻守卫**（S1）：同一个 executor 的多次调用必须复用**同一个**子进程
   （ddddocr import + 首次构造 + 首次推理有一次性开销，见 ``workers/ocr_worker.py``）。
   S2 把这条从「同图重复调用」升级为**跨字符集**调用：``charsetRange`` 必须进实例缓存键，
   否则先设过范围的那次会**串味**到后面没填该项的请求。
3. **滑块夹具只证形状，证不了坐标**（S2，实测）：
   * ``simple_target=False`` 走 ``_edge_based_match``（``core/slide_engine.py``）——
     对**光滑合成图**，Canny 后块图与背景都是全零，``matchTemplate`` 退化成
     ``max_loc == (0, 0)``，于是恒回「块图自身中心」且 ``confidence == 1.0``；
     **匹配对与错配这一对看起来一模一样**。
   * ``simple_target=True`` 走 ``_simple_template_match``；我用的条纹背景是**周期纹理**，
     同一块图在多个位置都是完美匹配，``minMaxLoc`` 取到**最左的别名**——
     实测已知裁剪框 ``(140, 30, 175, 65)``（真值中心 157/47）却返回 ``x=37``/``97``。
   ⇒ 两条路都**不能**用合成图反推坐标真值。故本文件只钉「形状、键、类型、在框内、
   阈值因果关系」，坐标精度留给真实滑块图（M60 任务单 §40.3）。

图片用**内联 base64**（64x26 PNG，内容 ``58``）而不是磁盘 fixture：判据不依赖字体文件，
且与 ``tests/commands/cases/ocr.json`` 用的是同一张图，两端可对照。
``ocr.detect`` 另需一张**真渲染过文字**的图（检测模型认的是字），这里用 Pillow
**自带的可缩放字体**现画，不依赖系统字体；背景/块图则用 PIL 画几何纹理（不需要字体）。
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import random
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

# 块图裁剪框：**只用于生成夹具**，不要拿它反推期望坐标（理由见模块 docstring §3）。
SLIDE_CROP = (140, 30, 175, 65)


def _invocation(command_id: str = "ocr.recognize", **inputs: object) -> CommandInvocation:
    return CommandInvocation(
        command_id=command_id,
        command_version="1.0.0",
        run_id="run",
        step_id="ocr",
        inputs=dict(inputs),
    )


def _run_with(calls):
    """在一个常驻 executor 上顺序跑若干 ``(command_id, inputs)``，返回 (结果, 子进程 pid)。"""

    async def main():
        executor = OcrExecutor()
        try:
            results, pids = [], []
            for command_id, inputs in calls:
                results.append(
                    await executor.execute(_invocation(command_id, **inputs), asyncio.Event())
                )
                assert executor._process is not None
                pids.append(executor._process.pid)
            return results, pids
        finally:
            await executor.close()

    return asyncio.run(main())


def _png_bytes(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _b64(blob: bytes) -> str:
    return base64.b64encode(blob).decode("ascii")


def _digit_image(text: str = "5834", font_px: int = 44):
    """渲染一行数字（**检测模型认的是字**，所以这张图必须真画文字）。

    ★ 用 Pillow **自带**的可缩放字体（``load_default(size)``，Pillow ≥ 10.1；
    ``uv.lock`` 锁的是 11.3），不依赖 ``arial.ttf`` 之类的系统字体——
    实测本机 ``arial.ttf`` 可用、但 CI/别的机器上不一定，那样 fixture 会随机器变。
    ⚠️ **不要**退回无参 ``load_default()``：那是 6x11 的位图字体，画出来的数字小到
    检测模型认不出（返回 0 个框），``count >= 1`` 那条判据就废了。
    """
    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.load_default(font_px)
    except TypeError as exc:  # pragma: no cover - 只有 Pillow < 10.1 才会走到
        raise AssertionError(f"本判据需要 Pillow >= 10.1（uv.lock 锁 11.3）：{exc}") from exc
    image = Image.new("RGB", (240, 70), "white")
    draw = ImageDraw.Draw(image)
    cursor = 12
    for char in text:
        draw.text((cursor, 8), char, fill="black", font=font)
        cursor += 52
    return image


def _textured(width: int = 200, height: int = 100):
    """条纹纹理背景（PIL 画几何图形，不需要字体）。"""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (width, height), (205, 215, 225))
    draw = ImageDraw.Draw(image)
    for i in range(0, width, 6):
        draw.line([(i, 0), (i, height)], fill=(180 + (i * 7) % 60, 195, 210), width=2)
    for j in range(0, height, 8):
        draw.line([(0, j), (width, j)], fill=(195, 190 + (j * 9) % 50, 205), width=2)
    return image


def _slide_fixtures() -> tuple[bytes, bytes]:
    """返回 (块图字节, 带缺口背景字节)：块图是背景原位裁剪，随后把该处涂成深色当缺口。"""
    from PIL import ImageDraw

    plain = _textured()
    template = plain.crop(SLIDE_CROP)
    holed = _textured()
    ImageDraw.Draw(holed).rectangle(SLIDE_CROP, fill=(35, 35, 40))
    return _png_bytes(template), _png_bytes(holed)


def _noise_block(seed: int = 7, side: int = 20) -> bytes:
    """与背景**毫无关系**的噪声块：相关系数实测约 0.20（见 §3），用来当错配负样本。"""
    from PIL import Image

    random.seed(seed)
    image = Image.new("RGB", (side, side))
    pixels = image.load()
    for y in range(side):
        for x in range(side):
            pixels[x, y] = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
    return _png_bytes(image)


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
        "charsetRange",
    }


_OCR_HANDLERS = {
    "ocr.recognize": "ocr.ddddocr:recognize",
    "ocr.detect": "ocr.ddddocr:detect",
    "ocr.slideMatch": "ocr.ddddocr:slideMatch",
}


def _ocr_manifest(command_id: str) -> dict:
    path = ROOT / "commands" / "ocr" / f"{command_id.split('.')[-1]}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_whole_ocr_family_shares_one_manifest_shape_and_one_executor():
    cli_source = (ROOT / "src" / "rpa_core" / "cli.py").read_text(encoding="utf-8")
    for command_id, handler in _OCR_HANDLERS.items():
        manifest = _ocr_manifest(command_id)
        assert manifest["id"] == command_id
        assert manifest["executor"] == "ocr.ddddocr"
        assert manifest["kind"] == "query"
        assert manifest["risk"] == "read"
        assert manifest["stability"] == "stable"
        assert manifest["effect"] == {"kind": "pure", "replay": "safe", "idempotency": "none"}
        assert set(manifest["errors"]) >= {"INVALID_INPUT", "EXECUTOR_FAILED"}
        assert manifest["implementation"]["handler"] == handler
        # 三条共用同一个 executor 名 ⇒ 注册一次即可；把「改名忘了同步」挡在测试里。
        assert f'"{manifest["executor"]}":' in cli_source


def test_detect_declares_no_model_path_because_det_mode_ignores_it():
    """★ det 模式**不透传** ``import_onnx_path``（``ddddocr`` 构造函数第一分支直接吞掉）。

    实测：``DdddOcr(det=True, import_onnx_path="不存在")`` 不报错、也不加载它。
    ⇒ 若 ``ocr.detect`` 声明 ``modelPath``，那就是一个**什么都不做的参数**；
    manifest 里不声明它才是对的（这条判据防的是「好心补上一个没用的参数」）。
    """
    properties = _ocr_manifest("ocr.detect")["input_schema"]["properties"]
    assert set(properties) == {"imagePath", "imageBase64"}


def test_catalog_loads_the_whole_ocr_family_on_one_executor():
    catalog = load_catalog(ROOT / "commands")
    ocr_ids = sorted(command for command in catalog if command.startswith("ocr."))
    assert ocr_ids == ["ocr.detect", "ocr.recognize", "ocr.slideMatch"]
    assert {catalog[command].executor for command in ocr_ids} == {"ocr.ddddocr"}
    assert {catalog[command].kind for command in ocr_ids} == {CommandKind.QUERY}


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


def test_charset_range_narrows_the_charset_and_never_leaks_into_other_requests():
    """★ S2 的核心判据：``charsetRange`` 只影响**自己那一次**调用。

    ``set_ranges`` 改的是**实例自身的状态**（不是构造参数），所以 ``charsetRange``
    必须进引擎缓存键；否则「先设过范围的请求」会把范围**串味**给后面所有没填该项的请求。
    这条判据把三种字符集在**同一个常驻进程**里顺序跑一遍：
    ① 收敛到数字 → 还是 ``58``；② 收敛到模型不认得的字母 → **静默退化成空串**（不报错）；
    ③ 不填该项 → 必须回到完整字符集（``58``，而不是沿用 ① 或 ② 的设置）。
    """
    scenarios = (
        ("0123456789", "58"),
        ("abc", ""),  # 模型字符集里没有字母 ⇒ 静默丢弃 ⇒ 空串（库的行为，不是本层降级）
        (None, "58"),  # 不填 ⇒ 完整字符集；串味的话这里会变成 "" 或别的
    )
    calls = [
        ("ocr.recognize", {"imageBase64": CAPTCHA_PNG_B64, **({"charsetRange": c} if c else {})})
        for c, _ in scenarios
    ]
    results, pids = _run_with(calls)
    for (charset, expected), result in zip(scenarios, results, strict=True):
        assert result.status == "success", (charset, result.error)
        assert result.outputs["text"] == expected, (charset, result.outputs)
    assert len(set(pids)) == 1, f"常驻 worker 被重复拉起：{pids}"


def test_detect_normalises_boxes_and_both_input_channels_agree(tmp_path):
    """``ocr.detect``：``[x1,y1,x2,y2]`` → ``{x,y,width,height}``，且两条输入通道一致。

    ★ 「两条产出路径必须给出同一个答案」是这里的主判据：同一张图走 ``imagePath`` 与走
    ``imageBase64`` 的结果必须**逐字节相同**——「两条路各自绿」抓不到「各自漂移」。
    ★ 框数只判 ``>= 1``：库**不保证找全**（实测四个字符时而 4 个框、时而 3 个），
    钉死数量会在库/模型更新时假红。
    """
    image = _digit_image("5834")
    width, height = image.size
    blob = _png_bytes(image)
    png = tmp_path / "captcha.png"
    png.write_bytes(blob)

    results, pids = _run_with(
        [
            ("ocr.detect", {"imagePath": str(png)}),
            ("ocr.detect", {"imageBase64": _b64(blob)}),
        ]
    )
    by_path, by_base64 = results
    for result in (by_path, by_base64):
        assert result.status == "success", result.error
    assert by_path.outputs == by_base64.outputs, "同图两条输入通道结果不一致"
    assert len(set(pids)) == 1

    boxes, count = by_path.outputs["boxes"], by_path.outputs["count"]
    assert count == len(boxes) >= 1, by_path.outputs
    for box in boxes:
        assert set(box) == {"x", "y", "width", "height"}
        assert all(isinstance(value, int) for value in box.values()), box
        assert box["width"] > 0 and box["height"] > 0, box
        assert 0 <= box["x"] and 0 <= box["y"], box
        assert box["x"] + box["width"] <= width, box
        assert box["y"] + box["height"] <= height, box


def test_slide_match_min_confidence_is_the_only_thing_that_can_reject_a_bad_match():
    """``ocr.slideMatch``：库**没有失配分支**，所以 ``minConfidence`` 是唯一的闸门。

    判据的因果链：**同一对图**（噪声块 + 带缺口背景）——
    - ``minConfidence=0``（默认）⇒ 成功，返回 ``{x,y,confidence}``，坐标落在背景内，
      且 ``confidence`` 必须**真的低于**下面的阈值（否则「被拦住」那条会退化成恒真——
      等于没钉住库的「失配也报最佳位置」这个行为）；
    - ``minConfidence=0.5`` ⇒ ``EXECUTOR_FAILED``。两次只差阈值 ⇒ 拦住它的确实是阈值。
    ★ 不断言坐标的具体值：合成图上边缘模式会退化、模板模式会命中周期纹理的别名，
    坐标真值只能由真实滑块图钉（见模块 docstring §3）。但**中心语义**仍可钉一角：
    源码是 ``x = max_loc + 模板尺寸 // 2`` 且 ``max_loc >= 0`` ⇒ ``x >= 模板宽 // 2``
    **必然成立**；若哪天退化成「返回左边界」，``x`` 就会小于这个下界。这条下界是
    公式的**必要条件**（不是夹具巧合），所以拿它当判据是稳的。
    """
    template, background = _slide_fixtures()
    noise = _noise_block()
    from PIL import Image as _Image

    template_size = _Image.open(io.BytesIO(template)).size
    noise_size = _Image.open(io.BytesIO(noise)).size
    background_size = _Image.open(io.BytesIO(background)).size
    background_b64 = _b64(background)
    thr = 0.5

    def _assert_centre_semantics(outputs: dict, tpl_size: tuple[int, int]) -> None:
        tpl_w, tpl_h = tpl_size
        bg_w, bg_h = background_size
        assert tpl_w // 2 <= outputs["x"] <= bg_w, outputs
        assert tpl_h // 2 <= outputs["y"] <= bg_h, outputs

    results, pids = _run_with(
        [
            (
                "ocr.slideMatch",
                {
                    "templateImageBase64": _b64(noise),
                    "backgroundImageBase64": background_b64,
                    "minConfidence": 0,
                },
            ),
            (
                "ocr.slideMatch",
                {
                    "templateImageBase64": _b64(noise),
                    "backgroundImageBase64": background_b64,
                    "minConfidence": thr,
                },
            ),
            (
                "ocr.slideMatch",
                {
                    "templateImageBase64": _b64(template),
                    "backgroundImageBase64": background_b64,
                },
            ),
        ]
    )
    loose, strict, strong = results
    assert len(set(pids)) == 1

    assert loose.status == "success", loose.error
    assert set(loose.outputs) == {"x", "y", "confidence"}
    assert isinstance(loose.outputs["x"], int) and isinstance(loose.outputs["y"], int)
    _assert_centre_semantics(loose.outputs, noise_size)
    assert loose.outputs["confidence"] < thr, f"夹具前提失效（错配却报高相关度）：{loose.outputs}"

    assert strict.status == "error", strict.outputs
    assert strict.error is not None
    assert strict.error.code == ErrorCode.EXECUTOR_FAILED
    assert strict.error.message

    # 同一背景上的**原位块图**仍是成功路径（阈值缺省 0 时不拦）。
    assert strong.status == "success", strong.error
    assert set(strong.outputs) == {"x", "y", "confidence"}
    _assert_centre_semantics(strong.outputs, template_size)


def test_slide_match_guards_each_of_its_four_input_paths(tmp_path):
    """两组「文件路径 / base64 二选一」各自独立判——四类非法组合都要显式报错。"""
    template, background = _slide_fixtures()
    missing = tmp_path / "no-such.png"
    common = {
        "templateImageBase64": _b64(template),
        "backgroundImageBase64": _b64(background),
    }
    scenarios = (
        # 块图两给
        {**common, "templateImagePath": str(missing)},
        # 背景两给
        {**common, "backgroundImagePath": str(missing)},
        # 块图都不给（背景合法）
        {"backgroundImageBase64": common["backgroundImageBase64"]},
        # 路径不存在
        {"templateImageBase64": common["templateImageBase64"], "backgroundImagePath": str(missing)},
    )
    results, _ = _run_with([("ocr.slideMatch", inputs) for inputs in scenarios])
    for inputs, result in zip(scenarios, results, strict=True):
        assert result.status == "error", (inputs, result.outputs)
        assert result.error is not None and result.error.code == ErrorCode.INVALID_INPUT, inputs


def test_worker_dispatches_every_ocr_command_before_the_not_found_fallback():
    """★ 分派是**显式 if 链**（缺参数门禁的字面量切片要求，见 worker docstring）。

    漏写一条分支不会报错，只会表现为 ``COMMAND_NOT_FOUND``——这条把三条都钉住：
    合法命令 + 空输入必须落到各自分支的 ``INVALID_INPUT``，而不是兜底的「不认识这条命令」。
    """
    for command_id in _OCR_HANDLERS:
        response = ocr_worker.handle({"id": 7, "commandId": command_id, "inputs": {}})
        assert response["id"] == 7, command_id
        assert response["ok"] is False, command_id
        assert response["code"] == ErrorCode.INVALID_INPUT.value, response


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
