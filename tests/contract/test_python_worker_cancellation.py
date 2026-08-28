import asyncio

from rpa_core.executors import PythonWorkerExecutor
from rpa_core.model.command import CommandInvocation


class FakeProcess:
    def __init__(self):
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.communicate_cancelled = False

    async def communicate(self, payload):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.communicate_cancelled = True
            raise

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.returncode = 1

    async def wait(self):
        if not self.killed:
            await asyncio.Event().wait()
        return self.returncode


def test_python_worker_terminate_then_kill_and_reap(monkeypatch):
    process = FakeProcess()

    async def create_process(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    executor = PythonWorkerExecutor(terminate_grace_seconds=0.001)
    invocation = CommandInvocation(
        command_id="data.writeJson",
        command_version="1.0.0",
        run_id="run",
        step_id="step",
        inputs={"workspace": ".", "path": "out.json", "data": {}},
    )

    async def run():
        cancellation = asyncio.Event()
        task = asyncio.create_task(executor.execute(invocation, cancellation))
        await asyncio.sleep(0)
        cancellation.set()
        result = await task
        return result

    result = asyncio.run(run())
    assert result.status == "cancelled"
    assert process.terminated
    assert process.killed
    assert process.communicate_cancelled
    assert executor.active_process_count == 0
