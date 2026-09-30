import asyncio
import tempfile
import unittest
from pathlib import Path

from econductor.config import Settings
from econductor.security import PathGuard
from econductor.ui import EconductorApp


class ApprovalUITests(unittest.IsolatedAsyncioTestCase):
    async def test_session_button_approves_and_resets_on_new_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            app = EconductorApp(PathGuard(Path(directory)), Settings(model="test"))
            async with app.run_test() as pilot:
                pending = asyncio.create_task(asyncio.to_thread(app.approve, "execute", "print(1)"))
                await pilot.pause()
                await pilot.click("#session")
                self.assertTrue(await pending)
                self.assertTrue(app.agent.auto_approve_session)
                self.assertTrue(await asyncio.to_thread(app.approve, "execute", "print(2)"))

                app.command("/new")
                self.assertFalse(app.agent.auto_approve_session)
                session_id = app.agent.session.id
                app.agent.save()
                app.agent.auto_approve_session = True
                app.resume(session_id)
                self.assertFalse(app.agent.auto_approve_session)
                await pilot.pause()


if __name__ == "__main__":
    unittest.main()
