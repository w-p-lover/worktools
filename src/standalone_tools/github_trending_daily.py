"""抓取 GitHub 每日 Trending，并生成带初步使用建议的 Markdown 日报。"""

from __future__ import annotations

import argparse
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


TRENDING_URL = "https://github.com/trending?since=daily"
USER_AGENT = "Mozilla/5.0 Codex-GitHub-Trending-Daily/1.0"


def _number(text: str) -> int:
    match = re.search(r"[\d,]+", text)
    return int(match.group(0).replace(",", "")) if match else 0


class TrendingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.repositories: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None
        self.depth = 0
        self.heading_depth: int | None = None
        self.capture_field: str | None = None
        self.capture_depth: int | None = None
        self.capture_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()

        if tag == "article" and "Box-row" in classes:
            self.current = {
                "name": "",
                "url": "",
                "description": "",
                "language": "未知",
                "stars": 0,
                "forks": 0,
                "stars_today": 0,
            }
            self.depth = 1
            return

        if self.current is None:
            return

        self.depth += 1
        if tag == "h2":
            self.heading_depth = self.depth

        href = attributes.get("href") or ""
        if tag == "a" and self.heading_depth is not None and re.fullmatch(r"/[^/]+/[^/]+", href):
            self.current["name"] = href.strip("/")
            self.current["url"] = f"https://github.com{href}"
        elif tag == "p" and "color-fg-muted" in classes:
            self._start_capture("description")
        elif tag == "span" and attributes.get("itemprop") == "programmingLanguage":
            self._start_capture("language")
        elif tag == "a" and href.endswith("/stargazers"):
            self._start_capture("stars")
        elif tag == "a" and href.endswith("/forks"):
            self._start_capture("forks")
        elif tag == "span" and "float-sm-right" in classes:
            self._start_capture("stars_today")

    def handle_data(self, data: str) -> None:
        if self.capture_field is not None:
            self.capture_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return

        if self.capture_field is not None and self.capture_depth == self.depth:
            value = " ".join("".join(self.capture_text).split())
            if self.capture_field in {"stars", "forks", "stars_today"}:
                self.current[self.capture_field] = _number(value)
            elif value:
                self.current[self.capture_field] = value
            self.capture_field = None
            self.capture_depth = None
            self.capture_text = []

        if tag == "h2" and self.heading_depth == self.depth:
            self.heading_depth = None

        if tag == "article" and self.depth == 1:
            if self.current["name"]:
                self.repositories.append(self.current)
            self.current = None
            self.depth = 0
            return

        self.depth -= 1

    def _start_capture(self, field: str) -> None:
        self.capture_field = field
        self.capture_depth = self.depth
        self.capture_text = []


def parse_trending(html: str) -> list[dict[str, Any]]:
    parser = TrendingParser()
    parser.feed(html)
    return parser.repositories


def _scenario(repository: dict[str, Any]) -> str:
    text = f"{repository['name']} {repository['description']}".lower()
    if re.search(r"\bai\b", text):
        return "AI 应用、智能体或模型工具链实践"
    categories = [
        (("agent", "llm", "model", "rag", "prompt"), "AI 应用、智能体或模型工具链实践"),
        (("capcut", "video", "audio", "media editor"), "音视频编辑、内容创作工具或桌面应用开发"),
        (("windows", "win11", "powershell"), "Windows 系统精简、运维脚本或终端环境定制"),
        (("dataset", "data layer"), "数据集构建、数据产品原型或训练素材准备"),
        (("marketing", "seo", "copywriting", "growth"), "营销自动化、内容增长或 SEO 工作流"),
        (("security", "vulnerability", "pentest", "malware"), "安全检测、漏洞研究或防护工具建设"),
        (("database", "sql", "data pipeline", "analytics"), "数据存储、数据工程或分析平台建设"),
        (("kubernetes", "cloud", "devops", "deploy", "observability"), "云原生、DevOps、部署或可观测性建设"),
        (("frontend", "react", "vue", "web ui", "browser"), "Web 前端、交互界面或浏览器应用开发"),
        (("cli", "compiler", "sdk", "developer tool", "code", "spec-driven", "toolkit"), "开发者工具、工程效率或代码基础设施"),
        (("mobile", "android", "ios"), "移动端应用或跨平台客户端开发"),
    ]
    for keywords, scenario in categories:
        if any(keyword in text for keyword in keywords):
            return scenario
    return "通用开源工具评估、原型验证或同类方案调研"


def assess_repository(repository: dict[str, Any]) -> dict[str, Any]:
    stars = repository["stars"]
    forks = repository["forks"]
    language = repository["language"]

    if stars >= 10_000 and forks >= 500:
        usability = "较成熟：社区采用度高，可进入选型验证，但生产使用前仍需检查文档、许可证和维护状态"
    elif stars >= 5_000 and forks >= 300:
        usability = "中等偏高：已有较多使用者，适合试用和原型，生产接入前需要专项验证"
    elif stars >= 1_000:
        usability = "中等：热度已形成，适合小范围试用，暂不应仅凭热榜直接用于生产"
    else:
        usability = "早期观察：近期关注度较高，更适合体验和跟踪，不建议直接用于关键生产场景"

    learnable = language != "未知"
    if learnable:
        learning_value = f"可以学习：可重点阅读 {language} 实现、项目结构和核心功能设计，并结合 Issues 判断真实质量"
    else:
        learning_value = "谨慎判断：榜单未标出主要语言，先检查源码占比、文档和提交记录再决定是否深入"

    return {
        "scenario": _scenario(repository),
        "usability": usability,
        "learnable": learnable,
        "learning_value": learning_value,
    }


def fetch_trending(timeout: int = 20) -> str:
    request = Request(TRENDING_URL, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def render_report(repositories: list[dict[str, Any]], generated_at: datetime) -> str:
    lines = [
        f"# GitHub 每日热榜（{generated_at:%Y-%m-%d}）",
        "",
        f"> 抓取时间：{generated_at:%Y-%m-%d %H:%M:%S}；来源：[GitHub Trending]({TRENDING_URL})。",
        "> 可用程度和学习建议仅基于榜单公开信息与社区指标作初步判断，不替代许可证、安全性和实际运行验证。",
        "",
        f"共收录 {len(repositories)} 个项目。",
        "",
    ]
    for index, repository in enumerate(repositories, 1):
        assessment = assess_repository(repository)
        lines.extend(
            [
                f"## {index}. [{repository['name']}]({repository['url']})",
                "",
                f"- 简介：{repository['description'] or 'GitHub Trending 页面未提供简介'}",
                f"- 主要语言：{repository['language']}",
                f"- 社区指标：{repository['stars']:,} Stars / {repository['forks']:,} Forks / 今日新增 {repository['stars_today']:,} Stars",
                f"- 可用场景：{assessment['scenario']}",
                f"- 可用程度：{assessment['usability']}",
                f"- 是否可以学习：{'是' if assessment['learnable'] else '需先评估'}。{assessment['learning_value']}",
                "",
            ]
        )
    return "\n".join(lines)


def default_output_path(generated_at: datetime) -> Path:
    project_root = Path(__file__).resolve().parents[2]
    return project_root / "reports" / "github-trending" / f"{generated_at:%Y-%m-%d}.md"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10, help="输出前 N 个项目，默认 10")
    parser.add_argument("--output", type=Path, help="自定义 Markdown 输出路径")
    args = parser.parse_args()

    repositories = parse_trending(fetch_trending())[: max(args.limit, 1)]
    if not repositories:
        raise RuntimeError("未从 GitHub Trending 页面解析到项目，页面结构可能已变化")

    generated_at = datetime.now().astimezone()
    output_path = args.output or default_output_path(generated_at)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_report(repositories, generated_at), encoding="utf-8")
    print(f"已生成 {len(repositories)} 个项目的日报：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
