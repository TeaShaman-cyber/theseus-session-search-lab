import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    '.github/workflows/smoke-session-search-arcade-inspector.yml',
    '.github/workflows/promote-session-search-ready.yml',
    '.github/workflows/activate-session-search-generation.yml',
)


class ArcadeWorkflowIdentityTest(unittest.TestCase):
    def test_protected_arcade_workflows_use_stable_repository_identity(self):
        for rel in WORKFLOWS:
            text = (ROOT / rel).read_text(encoding='utf-8')
            self.assertIn('ARCADE_USER_ID: ${{ vars.ARCADE_USER_ID }}', text, rel)
            self.assertNotIn('ARCADE_USER_ID: ${{ github.actor }}', text, rel)
            self.assertNotIn('ARCADE_USER_ID: ${{ secrets.ARCADE_USER_ID }}', text, rel)


if __name__ == '__main__':
    unittest.main()
