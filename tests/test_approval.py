import json
import tempfile
import unittest
from pathlib import Path

from econductor.agent import Agent
from econductor.config import Settings
from econductor.security import PathGuard
from econductor.types import Completion, ToolCall, ToolResult


class FakeInference:
    def __init__(self):
        self.completions = 0

    def count(self, messages, tools):
        return 100

    def complete(self, messages, tools, on_text):
        self.completions += 1
        if self.completions == 1:
            return Completion(
                text="",
                calls=[
                    ToolCall("execute", {"language": "python", "code": "print(1)"}, id="1"),
                    ToolCall("execute", {"language": "python", "code": "print(2)"}, id="2"),
                ],
            )
        return Completion(text="Done")

    def unload(self):
        pass


class ApprovalTests(unittest.TestCase):
    def test_session_auto_approval_applies_to_later_actions_without_persisting(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = PathGuard(Path(directory))
            guard.initialize()
            agent = Agent(guard, Settings(), FakeInference())
            executed = []
            approvals = []

            def execute(arguments):
                executed.append(arguments["code"])
                return ToolResult(True, "ok")

            def approve(name, preview):
                approvals.append(name)
                agent.auto_approve_session = True
                return True

            agent.executor.execute = execute
            agent.turn("Run two calculations", lambda kind, value: None, approve)

            self.assertEqual(approvals, ["execute"])
            self.assertEqual(executed, ["print(1)", "print(2)"])
            saved = json.loads((guard.state / "sessions" / f"{agent.session.id}.json").read_text())
            self.assertNotIn("auto_approve_session", saved)


if __name__ == "__main__":
    unittest.main()
