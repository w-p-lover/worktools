import unittest

from github_trending_daily import assess_repository, parse_trending


SAMPLE_HTML = """
<article class="Box-row">
  <h2 class="h3 lh-condensed"><a href="/example/tool"> example / tool </a></h2>
  <p class="col-9 color-fg-muted my-1 pr-4">A useful AI developer tool.</p>
  <span itemprop="programmingLanguage">Python</span>
  <a href="/example/tool/stargazers">12,345</a>
  <a href="/example/tool/forks">678</a>
  <span class="d-inline-block float-sm-right">321 stars today</span>
</article>
"""


class TrendingParserTest(unittest.TestCase):
    def test_parses_repository_card(self):
        repositories = parse_trending(SAMPLE_HTML)

        self.assertEqual(
            repositories,
            [
                {
                    "name": "example/tool",
                    "url": "https://github.com/example/tool",
                    "description": "A useful AI developer tool.",
                    "language": "Python",
                    "stars": 12345,
                    "forks": 678,
                    "stars_today": 321,
                }
            ],
        )

    def test_assessment_contains_required_decisions(self):
        assessment = assess_repository(
            {
                "name": "example/tool",
                "description": "A useful AI developer tool.",
                "language": "Python",
                "stars": 12345,
                "forks": 678,
                "stars_today": 321,
            }
        )

        self.assertIn("AI", assessment["scenario"])
        self.assertIn("较成熟", assessment["usability"])
        self.assertTrue(assessment["learnable"])
        self.assertIn("Python", assessment["learning_value"])

    def test_assessment_identifies_specific_usage_scenarios(self):
        cases = [
            ("moeru-ai/airi", "Self-hosted companion with realtime voice chat", "AI"),
            ("OpenCut-app/OpenCut", "The open-source CapCut alternative", "音视频"),
            ("Raphire/Win11Debloat", "PowerShell script to customize Windows", "Windows"),
        ]

        for name, description, expected in cases:
            with self.subTest(name=name):
                assessment = assess_repository(
                    {
                        "name": name,
                        "description": description,
                        "language": "Python",
                        "stars": 100,
                        "forks": 10,
                        "stars_today": 5,
                    }
                )
                self.assertIn(expected, assessment["scenario"])


if __name__ == "__main__":
    unittest.main()
