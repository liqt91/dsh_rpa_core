"""流程生成相关错误：任何让流程无法被完整参数化 / 校验不通过的情况都归到这里。"""


class FlowGenerationError(ValueError):
    """流程生成失败：输入无法被映射为合法、可直接运行的流程。

    与项目「避免产出无法运行的流程」原则一致——只要某一步无法被完整参数化
    （缺选择器、网址推断不出、命令不存在、参数不满足 input_schema 等），
    就抛出本错误并给出可操作的修正提示，绝不吐出半成品。该错误会被 CLI 捕获并
    以结构化 JSON 输出（code=GENERATE_FAILED），不会甩出 traceback。
    """

    def __init__(
        self,
        message: str,
        *,
        details: list[str] | None = None,
        step_index: int | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.details = details or []
        self.step_index = step_index

    def __str__(self) -> str:  # pragma: no cover - 仅用于可读性
        if self.step_index is not None:
            return f"[步骤 {self.step_index}] {self.message}"
        return self.message
