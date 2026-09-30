import json
import unittest

from econductor.sessions import compact


def token_count(messages):
    # Deterministic stand-in for the model's exact tokenizer.
    return len(json.dumps(messages, ensure_ascii=False)) // 3


class CompactionTests(unittest.TestCase):
    def test_one_turn_with_large_tool_logs_can_continue(self):
        request = {"role": "user", "content": "Analyze the data and explain the results."}
        messages = [request]
        for index in range(4):
            call_id = str(index)
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": "execute",
                                "arguments": {"language": "python", "code": "x" * 1000},
                            },
                        }
                    ],
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "name": "execute",
                    "tool_call_id": call_id,
                    "content": json.dumps(
                        {
                            "ok": True,
                            "summary": "repeated warning\n" * 500,
                            "artifacts": [f"/project/.econductor/artifacts/{index}/execution.log"],
                        }
                    ),
                }
            )
        original = json.dumps(messages)
        system = {"role": "system", "content": "Use tools safely."}
        self.assertGreater(token_count([system, *messages]), 2500)

        prompt, tokens = compact(messages, token_count, 2500, system)

        self.assertLessEqual(tokens, 2500)
        self.assertIn(request, prompt)
        self.assertEqual(json.dumps(messages), original)
        for index, message in enumerate(prompt):
            if message["role"] == "tool":
                self.assertGreater(index, 0)
                self.assertEqual(prompt[index - 1]["role"], "assistant")
        self.assertTrue(any("artifacts" in str(message) for message in prompt))

    def test_resume_after_full_turn_retains_latest_request_and_prior_goal(self):
        messages = [
            {"role": "user", "content": "Estimate the treatment effect."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {"name": "execute", "arguments": {"code": "x" * 10000}},
                    }
                ],
            },
            {
                "role": "tool",
                "name": "execute",
                "tool_call_id": "1",
                "content": json.dumps(
                    {
                        "ok": True,
                        "summary": "output" * 3000,
                        "artifacts": ["/project/result.csv"],
                    }
                ),
            },
            {"role": "user", "content": "Please continue."},
        ]
        prompt, tokens = compact(
            messages, token_count, 1200, {"role": "system", "content": "System"}
        )

        self.assertLessEqual(tokens, 1200)
        self.assertTrue(
            any("Estimate the treatment effect." in message.get("content", "") for message in prompt)
        )
        self.assertTrue(any(message.get("content") == "Please continue." for message in prompt))

    def test_latest_request_alone_can_still_exceed_context(self):
        messages = [{"role": "user", "content": "x" * 10000}]
        with self.assertRaisesRegex(ValueError, "latest request alone"):
            compact(messages, token_count, 500, {"role": "system", "content": "System"})


if __name__ == "__main__":
    unittest.main()
