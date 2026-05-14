import os
import re
import time
import json
import unicodedata
from datetime import date, datetime, timezone
from html import unescape
from typing import Iterable
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.job import Currency, Job, JobLevel, JobStatus
from app.models.job_skill import JobSkill
from app.models.skill import Skill
from app.services.skill_normalization import expand_skill_labels, is_non_skill_role_label


class JobCrawler:
    _SECTION_PATTERNS: dict[str, tuple[str, ...]] = {
        "role_responsibilities": (
            "your role & responsibilities",
            "your role and responsibilities",
            "our role & responsibilities",
            "our role and responsibilities",
            "role & responsibilities",
            "role and responsibilities",
            "responsibilities",
            "job responsibilities",
            "job description",
            "key responsibilities",
            "main responsibilities",
            "what you will do",
            "what you'll do",
            "mo ta cong viec",
            "trach nhiem",
            "nhiem vu",
        ),
        "skills_qualifications": (
            "your skills & qualifications",
            "your skills and qualifications",
            "skills & qualifications",
            "skills and qualifications",
            "qualifications",
            "requirements",
            "job requirements",
            "required qualifications",
            "experience and qualifications",
            "required domain knowledge",
            "technical and ba related competencies",
            "your profile",
            "what we're looking for",
            "what we are looking for",
            "yeu cau",
            "ky nang va yeu cau",
        ),
        "benefits": (
            "benefits",
            "benefits and perks",
            "what we offer",
            "what we can offer",
            "why you'll love working here",
            "why you will love working here",
            "salary and benefits",
            "perks",
            "working time and location",
            "quyen loi",
            "phuc loi",
        ),
    }

    def __init__(self) -> None:
        self._user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/129.0.0.0 Safari/537.36"
        )
        self._body_wait_seconds = int(os.getenv("CRAWLER_BODY_WAIT_SECONDS", "12"))
        self._post_load_sleep_seconds = float(os.getenv("CRAWLER_POST_LOAD_SLEEP_SECONDS", "1.2"))
        self._page_load_timeout_seconds = int(os.getenv("CRAWLER_PAGELOAD_TIMEOUT_SECONDS", "20"))
        self._use_webdriver_manager = os.getenv("CRAWLER_USE_WEBDRIVER_MANAGER", "0").strip().lower() in {"1", "true", "yes"}

    def _create_driver(self) -> webdriver.Chrome:
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--blink-settings=imagesEnabled=false")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-web-resources")
        options.add_argument("--disable-sync")
        options.page_load_strategy = "eager"
        options.add_argument(f"user-agent={self._user_agent}")
        options.add_experimental_option(
            "prefs",
            {
                "profile.managed_default_content_settings.images": 2,
                "profile.default_content_setting_values.notifications": 2,
            },
        )

        chromedriver_path = os.getenv("CHROMEDRIVER_PATH", "").strip()
        if chromedriver_path:
            service = Service(executable_path=chromedriver_path)
            driver = webdriver.Chrome(service=service, options=options)
            driver.set_page_load_timeout(self._page_load_timeout_seconds)
            return driver

        try:
            driver = webdriver.Chrome(options=options)
            driver.set_page_load_timeout(self._page_load_timeout_seconds)
            return driver
        except Exception:
            if not self._use_webdriver_manager:
                raise

        manager_path = ChromeDriverManager().install()
        if manager_path.lower().endswith("third_party_notices.chromedriver"):
            manager_path = os.path.join(os.path.dirname(manager_path), "chromedriver.exe")

        service = Service(manager_path)
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(self._page_load_timeout_seconds)
        return driver

    @staticmethod
    def _source_name_topdev() -> str:
        return "TopDev"

    @staticmethod
    def is_topdev_detail_url(url: str) -> bool:
        parsed = urlparse(url)
        hostname = parsed.netloc.lower()
        path = parsed.path.lower()
        if "topdev.vn" not in hostname:
            return False
        return "/detail-jobs/" in path or "/viec-lam/" in path

    @staticmethod
    def _first_non_empty(values: list[str]) -> str:
        for value in values:
            if value and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _clean_lines(text: str) -> str:
        if not text:
            return ""

        text = unescape(str(text)).replace("\xa0", " ")
        if re.search(r"</?[a-z][\s\S]*>", text, re.I):
            soup = BeautifulSoup(text, "html.parser")
            for noisy_node in soup(["script", "style", "noscript", "svg"]):
                noisy_node.decompose()
            text = soup.get_text("\n", strip=True)

        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[\u200b-\u200f\ufeff]", "", text)
        text = re.sub(r"[ \t]+", " ", text)

        lines: list[str] = []
        previous = ""
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                if lines and lines[-1] != "":
                    lines.append("")
                continue
            if line == previous:
                continue
            lines.append(line)
            previous = line

        cleaned = "\n".join(lines).strip()
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned

    @staticmethod
    def _normalize_section_heading(text: str) -> str:
        value = JobCrawler._strip_accents(text or "").lower()
        value = re.sub(r"^\s*\d+\s*", "", value)
        value = value.replace("&", " and ")
        value = re.sub(r"[^a-z0-9]+", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _clean_section_text(text: str) -> str:
        return JobCrawler._clean_lines(text)

    @staticmethod
    def _normalize_inline_text(text: str) -> str:
        if not text:
            return ""
        text = unescape(text).replace("\xa0", " ")
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @classmethod
    def _node_text(cls, node: Tag | NavigableString | None) -> str:
        if node is None:
            return ""
        if isinstance(node, NavigableString):
            return cls._normalize_inline_text(str(node))
        return cls._normalize_inline_text(node.get_text(" ", strip=True))

    @classmethod
    def _direct_text_from_tag(cls, tag: Tag) -> str:
        fragments: list[str] = []

        for child in tag.contents:
            if isinstance(child, NavigableString):
                text = cls._normalize_inline_text(str(child))
                if text:
                    fragments.append(text)
                continue

            if not isinstance(child, Tag):
                continue

            if child.name in {"ul", "ol"}:
                continue

            text = cls._direct_text_from_tag(child)
            if text:
                fragments.append(text)

        return cls._normalize_inline_text(" ".join(fragments))

    @classmethod
    def _format_html_list(cls, list_tag: Tag, depth: int = 0) -> list[str]:
        lines: list[str] = []
        top_level_bullet = "-"
        child_bullet = "•" if list_tag.name == "ul" else "◦"

        for item in list_tag.find_all("li", recursive=False):
            fragments: list[str] = []

            for child in item.contents:
                if isinstance(child, NavigableString):
                    text = cls._normalize_inline_text(str(child))
                    if text:
                        fragments.append(text)
                    continue

                if not isinstance(child, Tag):
                    continue

                if child.name in {"ul", "ol"}:
                    continue

                text = cls._node_text(child)
                if text:
                    fragments.append(text)

            item_text = cls._normalize_inline_text(" ".join(fragments))
            if item_text:
                if depth == 0:
                    lines.append(f"{top_level_bullet} {item_text}")
                else:
                    indent = "\xa0" * (4 * depth)
                    lines.append(f"{indent}{child_bullet} {item_text}")

            for nested_list in item.find_all(["ul", "ol"], recursive=False):
                lines.extend(cls._format_html_list(nested_list, depth + 1))

        return lines

    @classmethod
    def _format_section_content(cls, html: str) -> str:
        soup = BeautifulSoup(html or "", "html.parser")
        root = soup.body or soup
        lines: list[str] = []

        def append_line(text: str, *, force_blank_before: bool = False) -> None:
            normalized = cls._normalize_inline_text(text)
            if not normalized:
                return
            if force_blank_before and lines and lines[-1] != "":
                lines.append("")
            lines.append(normalized)

        def append_nested_lists(tag: Tag) -> None:
            for nested_list in tag.find_all(["ul", "ol"], recursive=False):
                if lines and lines[-1] != "":
                    previous = lines[-1].rstrip()
                    if not previous.endswith(":"):
                        lines.append("")
                lines.extend(cls._format_html_list(nested_list))

        for child in root.children:
            if isinstance(child, NavigableString):
                append_line(str(child))
                continue

            if not isinstance(child, Tag):
                continue

            if child.name in {"ul", "ol"}:
                if lines and lines[-1] != "":
                    previous = lines[-1].rstrip()
                    if not previous.endswith(":"):
                        lines.append("")
                lines.extend(cls._format_html_list(child))
                continue

            if child.name == "br":
                if lines and lines[-1] != "":
                    lines.append("")
                continue

            if child.name in {"p", "div"}:
                text = cls._direct_text_from_tag(child)
                if text:
                    append_line(text, force_blank_before=bool(lines))
                append_nested_lists(child)
                continue

            text = cls._direct_text_from_tag(child)
            if text:
                append_line(text, force_blank_before=bool(lines))
            append_nested_lists(child)

        formatted = "\n".join(line.rstrip() for line in lines).strip()
        formatted = re.sub(r"\n{3,}", "\n\n", formatted)
        return formatted

    def _classify_section_heading(self, text: str) -> str | None:
        normalized = self._normalize_section_heading(text)
        if not normalized:
            return None

        for section_name, candidates in self._SECTION_PATTERNS.items():
            for candidate in candidates:
                candidate_normalized = self._normalize_section_heading(candidate)
                if normalized == candidate_normalized:
                    return section_name
                if candidate_normalized in normalized or normalized in candidate_normalized:
                    return section_name
        return None

    def _looks_like_heading_only_content(self, content_text: str, section_key: str | None = None) -> bool:
        normalized = self._normalize_section_heading(content_text)
        if not normalized:
            return True

        if section_key:
            for candidate in self._SECTION_PATTERNS.get(section_key, ()):
                candidate_normalized = self._normalize_section_heading(candidate)
                if normalized == candidate_normalized:
                    return True

        candidate_headings = [
            "required domain knowledge",
            "technical awareness",
            "business analysis competencies",
            "job requirements",
            "required qualifications",
            "preferred qualifications",
            "benefits",
            "job description",
        ]
        for candidate in candidate_headings:
            candidate_normalized = self._normalize_section_heading(candidate)
            if normalized == candidate_normalized:
                return True

        if "\n" not in content_text and len(content_text.split()) <= 6:
            return True

        return False

    @classmethod
    def _unwrap_section_content_node(cls, node: Tag | None) -> Tag | None:
        current = node
        while isinstance(current, Tag):
            has_direct_list = any(
                isinstance(child, Tag) and child.name in {"ul", "ol"}
                for child in current.contents
            )
            child_tags = [child for child in current.contents if isinstance(child, Tag)]
            has_direct_non_wrapper_text = False

            for child in current.contents:
                if isinstance(child, NavigableString):
                    if cls._normalize_inline_text(str(child)):
                        has_direct_non_wrapper_text = True
                        break
                    continue

                if not isinstance(child, Tag):
                    continue

                if child.name in {"ul", "ol", "div", "section", "article"}:
                    continue

                if cls._node_text(child):
                    has_direct_non_wrapper_text = True
                    break

            if has_direct_non_wrapper_text or has_direct_list or len(child_tags) != 1:
                return current

            next_child = child_tags[0]
            if next_child.name not in {"div", "section", "article"}:
                return current

            current = next_child

        return node

    def _extract_structured_sections(self, soup: BeautifulSoup) -> dict[str, str]:
        sections: dict[str, str] = {}

        containers = soup.select("div.border-text-200")
        if not containers:
            containers = soup.select("article, main, div[class*='job-description']")

        for container in containers:
            heading_candidates = container.select(
                "span.flex.items-center.gap-1.font-semibold, "
                "span.mt-4.flex.items-center.gap-1.font-semibold, "
                "span.font-semibold.text-\\[\\#3659B3\\], "
                "span.font-semibold, "
                "h2, h3, h4, "
                "p > strong, div > strong"
            )

            for heading in heading_candidates:
                heading_text = heading.get_text(" ", strip=True)
                section_key = self._classify_section_heading(heading_text)
                if not section_key or section_key in sections:
                    continue

                content_node = heading.find_next_sibling("div")
                content_text = ""

                while content_node:
                    if not content_node.get_text(" ", strip=True):
                        content_node = content_node.find_next_sibling("div")
                        continue

                    content_target = self._unwrap_section_content_node(content_node)
                    candidate_text = self._format_section_content(str(content_target))
                    if not candidate_text or self._looks_like_heading_only_content(candidate_text, section_key):
                        next_content = content_node.find_next_sibling("div")
                        if next_content is None:
                            break
                        content_node = next_content
                        continue

                    content_text = candidate_text
                    break

                if not content_node or not content_text:
                    continue

                next_heading = content_node.find_next(
                    lambda tag: getattr(tag, "name", None) == "span"
                    and self._classify_section_heading(tag.get_text(" ", strip=True)) is not None
                )
                if next_heading and next_heading is not heading:
                    next_heading_text = next_heading.get_text(" ", strip=True)
                    if content_text == self._clean_section_text(next_heading_text):
                        continue

                sections[section_key] = content_text

            if len(sections) >= 2:
                return sections

        return sections

    @staticmethod
    def _dedupe_preserve_order(values: list[str]) -> list[str]:
        results: list[str] = []
        seen: set[str] = set()
        for value in values:
            key = value.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            results.append(value.strip())
        return results

    @staticmethod
    def _normalize_skill_name(skill: str) -> str:
        raw = re.sub(r"\s+", " ", skill or "").strip()
        if not raw:
            return ""

        normalized_map = {
            "postgresql": "PostgreSQL",
            "postgressql": "PostgreSQL",
            "postgres": "PostgreSQL",
            "javascript": "JavaScript",
            "typescript": "TypeScript",
            "nodejs": "Node.js",
            "node.js": "Node.js",
            "reactjs": "React",
            "react.js": "React",
            "vuejs": "Vue.js",
            "vue.js": "Vue.js",
            "springboot": "Spring Boot",
            "spring boot": "Spring Boot",
            "dotnet": ".NET",
            ".net": ".NET",
            ".net core": ".NET",
            "net core": ".NET",
            "asp.net": ".NET",
            "c sharp": "C#",
            "c#": "C#",
            "golang": "Go",
            "ci/cd": "CI/CD",
            "rest api": "REST API",
            "graphql": "GraphQL",
            "aws": "AWS",
            "gcp": "GCP",
            "k8s": "Kubernetes",
            "elasticsearch": "Elasticsearch",
            "opensearch": "OpenSearch",
        }

        key = raw.lower().replace(" ", "")
        if key in normalized_map:
            return normalized_map[key]

        key_with_space = raw.lower()
        if key_with_space in normalized_map:
            return normalized_map[key_with_space]

        return raw

    @staticmethod
    def _strip_accents(text: str) -> str:
        if not text:
            return ""
        normalized = unicodedata.normalize("NFD", text)
        return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")

    def _extract_title(self, soup: BeautifulSoup) -> str:
        candidates = [
            soup.select_one("h1") and soup.select_one("h1").get_text(" ", strip=True),
            soup.find("meta", property="og:title") and soup.find("meta", property="og:title").get("content", ""),
            soup.title and soup.title.get_text(" ", strip=True),
        ]
        title = self._first_non_empty([value for value in candidates if value])
        return title or "Không tìm thấy tiêu đề"

    @staticmethod
    def _extract_job_posting_jsonld(soup: BeautifulSoup) -> dict:
        scripts = soup.select("script[type='application/ld+json']")
        for script in scripts:
            raw = (script.string or script.get_text() or "").strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            candidates = []
            if isinstance(data, dict):
                candidates.append(data)
                graph = data.get("@graph")
                if isinstance(graph, list):
                    candidates.extend([item for item in graph if isinstance(item, dict)])
            elif isinstance(data, list):
                candidates.extend([item for item in data if isinstance(item, dict)])

            for item in candidates:
                item_type = item.get("@type")
                if isinstance(item_type, list):
                    is_job_posting = any(str(t).lower() == "jobposting" for t in item_type)
                else:
                    is_job_posting = str(item_type).lower() == "jobposting"
                if is_job_posting:
                    return item

        return {}

    def _extract_company(self, soup: BeautifulSoup, job_posting: dict | None = None) -> str:
        if job_posting:
            org = job_posting.get("hiringOrganization")
            if isinstance(org, dict):
                name = org.get("name")
                if isinstance(name, str) and name.strip():
                    return name.strip()

        selectors = [
            "[data-testid='company-name']",
            ".company-name",
            "a[href*='/company/']",
            "a[href*='/nha-tuyen-dung/']",
        ]
        values: list[str] = []
        for selector in selectors:
            element = soup.select_one(selector)
            if element:
                values.append(element.get_text(" ", strip=True))

        marker = soup.find(string=re.compile(r"(Company|Công ty|Employer)", re.I))
        if marker:
            parent = marker.find_parent(["div", "section", "article"])
            if parent:
                values.append(parent.get_text(" ", strip=True))

        company_name = self._first_non_empty(values)
        return company_name or "N/A"

    def _extract_location(self, soup: BeautifulSoup, job_posting: dict | None = None) -> str:
        def compact_location(text: str) -> str:
            value = re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip(" ,")
            if not value:
                return ""

            normalized = self._strip_accents(value).lower()
            if "," in value:
                parts = [part.strip() for part in value.split(",") if part.strip()]
                district_markers = ("quan ", "q.", "district ", "huyen ", "h.", "phuong ", "p.", "ward ")
                city_markers = (
                    "ho chi minh",
                    "tp ho chi minh",
                    "thanh pho ho chi minh",
                    "ha noi",
                    "thanh pho ha noi",
                    "da nang",
                    "thanh pho da nang",
                    "can tho",
                    "hai phong",
                )

                if len(parts) >= 2:
                    head_normalized = self._strip_accents(parts[0]).lower()
                    tail_normalized = self._strip_accents(parts[-1]).lower()
                    if any(marker in head_normalized for marker in district_markers) and any(
                        marker in tail_normalized for marker in city_markers
                    ):
                        return f"{parts[0]}, {parts[-1]}"

            city_aliases: list[tuple[str, tuple[str, ...]]] = [
                ("Hồ Chí Minh", ("ho chi minh", "tp ho chi minh", "thanh pho ho chi minh", "sai gon")),
                ("Hà Nội", ("ha noi", "thanh pho ha noi")),
                ("Đà Nẵng", ("da nang", "thanh pho da nang")),
                ("Cần Thơ", ("can tho", "thanh pho can tho")),
                ("Hải Phòng", ("hai phong", "thanh pho hai phong")),
            ]

            for canonical, aliases in city_aliases:
                for alias in aliases:
                    if alias in normalized:
                        return canonical

            if "," in value:
                tail = [part.strip() for part in value.split(",") if part.strip()]
                if tail:
                    return tail[-1]

            return value

        header_selectors = [
            "div.sticky span.line-clamp-1",
            "div.sticky span.flex.items-center.gap-1.text-xs\\[12px\\].font-medium.text-text-500",
            "div.sticky span.flex.items-center.gap-1.text-sm",
        ]

        header_location_candidates: list[str] = []
        for selector in header_selectors:
            for element in soup.select(selector):
                text = element.get_text(" ", strip=True)
                if not text:
                    continue
                normalized = self._strip_accents(text).lower()
                if any(keyword in normalized for keyword in ["fulltime", "parttime", "remote", "hybrid", "intern", "junior", "middle", "mid", "senior", "lead", "nam", "year"]):
                    continue
                if len(text) <= 80 and (
                    "hồ chí minh" in normalized
                    or "ha noi" in normalized
                    or "đà nẵng" in text.lower()
                    or "da nang" in normalized
                    or "can tho" in normalized
                    or "viet nam" in normalized
                ):
                    header_location_candidates.append(text)

        header_location = self._first_non_empty(header_location_candidates)
        if header_location:
            return compact_location(header_location)

        if job_posting:
            job_location = job_posting.get("jobLocation")
            if isinstance(job_location, list):
                job_location = job_location[0] if job_location else None

            if isinstance(job_location, dict):
                address = job_location.get("address")
                if isinstance(address, dict):
                    parts = [
                        str(address.get("streetAddress", "")).strip(),
                        str(address.get("addressLocality", "")).strip(),
                        str(address.get("addressRegion", "")).strip(),
                    ]
                    location = ", ".join([part for part in parts if part])
                    if location:
                        return compact_location(location)

        selectors = [
            "[data-testid='job-location']",
            ".job-location",
            "a[href*='location']",
            "span[class*='location']",
        ]
        values: list[str] = []
        for selector in selectors:
            element = soup.select_one(selector)
            if element:
                values.append(element.get_text(" ", strip=True))

        location = self._first_non_empty(values)
        return compact_location(location) or "Việt Nam"

    def _extract_salary(self, soup: BeautifulSoup, job_posting: dict | None = None) -> str:
        if job_posting:
            base_salary = job_posting.get("baseSalary")
            if isinstance(base_salary, dict):
                currency = base_salary.get("currency")
                value = base_salary.get("value")
                if isinstance(value, dict):
                    raw_value = value.get("value")
                    if raw_value is not None:
                        salary_text = str(raw_value).strip()
                        if salary_text:
                            if currency:
                                return f"{salary_text} {currency}"
                            return salary_text

        selectors = [
            "[data-testid='salary']",
            ".salary",
            "span[class*='salary']",
            "div[class*='salary']",
        ]
        values: list[str] = []
        for selector in selectors:
            element = soup.select_one(selector)
            if element:
                text = element.get_text(" ", strip=True)
                if text:
                    values.append(text)

        if not values:
            body_text = soup.get_text(" ", strip=True)
            salary_match = re.search(r"(\$\s?\d[\d,\.\s-]*|\d+[\d,\.\s-]*(VND|USD|triệu|million))", body_text, re.I)
            if salary_match:
                values.append(salary_match.group(0))

        return self._first_non_empty(values)

    @staticmethod
    def _map_job_level(raw_text: str | None) -> JobLevel | None:
        normalized = JobCrawler._strip_accents(raw_text or "").lower()
        normalized = re.sub(r"[^a-z0-9\s/+()-]", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if not normalized:
            return None

        level_patterns: list[tuple[JobLevel, tuple[str, ...]]] = [
            (JobLevel.lead, ("team lead", "tech lead", "leader", "lead", "principal", "manager")),
            (JobLevel.senior, ("senior", "sr", "expert", "specialist")),
            (JobLevel.mid, ("middle", "mid level", "mid-level", "mid", "intermediate")),
            (JobLevel.junior, ("junior", "jr", "fresher", "entry level", "entry-level")),
            (JobLevel.intern, ("intern", "internship", "trainee")),
        ]

        for level, keywords in level_patterns:
            for keyword in keywords:
                keyword_normalized = JobCrawler._strip_accents(keyword).lower()
                if re.search(rf"(?<!\w){re.escape(keyword_normalized)}(?!\w)", normalized):
                    return level

        return None

    def _extract_level(self, soup: BeautifulSoup, title: str = "", job_posting: dict | None = None) -> JobLevel | None:
        if job_posting:
            experience_requirements = job_posting.get("experienceRequirements")
            if isinstance(experience_requirements, str):
                mapped = self._map_job_level(experience_requirements)
                if mapped:
                    return mapped

            seniority = job_posting.get("seniority")
            if isinstance(seniority, str):
                mapped = self._map_job_level(seniority)
                if mapped:
                    return mapped

        selectors = [
            "div.sticky span.flex.items-center.gap-1.text-xs\\[12px\\].font-medium.text-text-500",
            "div.sticky span.flex.items-center.gap-1.text-sm",
            "div.sticky div.my-2 span",
        ]

        candidate_texts: list[str] = []
        for selector in selectors:
            for element in soup.select(selector):
                text = element.get_text(" ", strip=True)
                if text:
                    candidate_texts.append(text)

        if title:
            candidate_texts.append(title)

        for text in candidate_texts:
            mapped = self._map_job_level(text)
            if mapped:
                return mapped

        return None

    @staticmethod
    def _map_employment_type(raw_text: str | None) -> str | None:
        normalized = JobCrawler._strip_accents(raw_text or "").lower()
        normalized = re.sub(r"[^a-z0-9\s/+()-]", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if not normalized:
            return None

        employment_patterns: list[tuple[str, tuple[str, ...]]] = [
            ("Fulltime", ("fulltime", "full time", "toan thoi gian")),
            ("Part-time", ("part time", "part-time", "ban thoi gian")),
            ("Contract", ("contract", "hop dong")),
            ("Internship", ("internship", "intern", "thuc tap")),
            ("Remote", ("remote", "lam viec tu xa")),
            ("Hybrid", ("hybrid",)),
            ("Freelance", ("freelance",)),
        ]

        for canonical, keywords in employment_patterns:
            for keyword in keywords:
                keyword_normalized = JobCrawler._strip_accents(keyword).lower()
                if re.search(rf"(?<!\w){re.escape(keyword_normalized)}(?!\w)", normalized):
                    return canonical

        return None

    def _extract_employment_type(self, soup: BeautifulSoup, job_posting: dict | None = None) -> str | None:
        if job_posting:
            employment_type = job_posting.get("employmentType")
            if isinstance(employment_type, list):
                for item in employment_type:
                    if isinstance(item, str):
                        mapped = self._map_employment_type(item)
                        if mapped:
                            return mapped
            elif isinstance(employment_type, str):
                mapped = self._map_employment_type(employment_type)
                if mapped:
                    return mapped

        selectors = [
            "div.sticky span.flex.items-center.gap-1.text-xs\\[12px\\].font-medium.text-text-500",
            "div.sticky span.flex.items-center.gap-1.text-sm",
            "div.sticky div.my-2 span",
        ]

        for selector in selectors:
            for element in soup.select(selector):
                text = element.get_text(" ", strip=True)
                mapped = self._map_employment_type(text)
                if mapped:
                    return mapped

        return None

    @staticmethod
    def _extract_experience(soup: BeautifulSoup, job_posting: dict | None = None) -> str | None:
        if job_posting:
            experience_requirements = job_posting.get("experienceRequirements")
            if isinstance(experience_requirements, str):
                cleaned = re.sub(r"\s+", " ", experience_requirements).strip()
                if cleaned:
                    year_match = re.search(r"(\d+\+?\s*(?:năm|years?|yrs?))", cleaned, re.IGNORECASE)
                    if year_match:
                        return year_match.group(1).strip()

        selectors = [
            "div.sticky span.flex.items-center.gap-1.text-xs\\[12px\\].font-medium.text-text-500",
            "div.sticky span.flex.items-center.gap-1.text-sm",
            "div.sticky div.my-2 span",
        ]

        for selector in selectors:
            for element in soup.select(selector):
                text = element.get_text(" ", strip=True)
                if not text:
                    continue
                match = re.search(r"(\d+\+?\s*(?:năm|years?|yrs?))", text, re.IGNORECASE)
                if match:
                    return match.group(1).strip()

        return None

    @staticmethod
    def _extract_application_deadline(soup: BeautifulSoup, job_posting: dict | None = None) -> date | None:
        if job_posting:
            valid_through = job_posting.get("validThrough")
            if isinstance(valid_through, str) and valid_through.strip():
                raw_value = valid_through.strip()
                for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        if "%z" in fmt or "T" in fmt:
                            return datetime.strptime(raw_value, fmt).date()
                        return datetime.strptime(raw_value, fmt).date()
                    except ValueError:
                        continue

        deadline_text_candidates: list[str] = []
        selectors = [
            "div.sticky span.break-none",
            "div.sticky span.whitespace-nowrap",
            "div.sticky span",
        ]
        for selector in selectors:
            for element in soup.select(selector):
                text = element.get_text(" ", strip=True)
                if text and "deadline" in text.lower():
                    deadline_text_candidates.append(text)

        for text in deadline_text_candidates:
            match = re.search(r"(\d{2}-\d{2}-\d{4})", text)
            if match:
                try:
                    return datetime.strptime(match.group(1), "%d-%m-%Y").date()
                except ValueError:
                    continue

        return None

    def _extract_skill_tags_from_page(self, soup: BeautifulSoup) -> list[str]:
        # Find and remove "More jobs for you" section to avoid capturing unrelated skills
        for div in soup.find_all("div"):
            span = div.find("span")
            if span and "More jobs for you" in span.get_text():
                div.decompose()
                break

        skill_selectors = [
            "div.flex.flex-wrap.items-center.gap-1 a",
            "a[class*='skill']",
            "span[class*='skill']",
            "li[class*='skill']",
            "[data-testid='job-skill']",
            "a[href*='keyword=']",
        ]

        skills: list[str] = []
        for selector in skill_selectors:
            for element in soup.select(selector):
                text = element.get_text(" ", strip=True)
                if text and 1 < len(text) <= 50:
                    skills.append(text)

        normalized: list[str] = []
        ignored = {"4+", "xem thêm", "apply", "ứng tuyển", "save job", "hot"}
        for skill in skills:
            cleaned = self._normalize_skill_name(skill)
            key = cleaned.lower().strip()
            if key in ignored:
                continue
            if is_non_skill_role_label(cleaned):
                continue
            if len(cleaned) <= 1:
                continue
            normalized.append(cleaned)

        return self._dedupe_preserve_order(normalized)

    def _extract_skills_from_text(self, text: str) -> list[str]:
        if not text:
            return []

        ascii_text = self._strip_accents(text).lower()

        keyword_patterns: list[tuple[str, str]] = [
            ("JavaScript", r"\bjavascript\b"),
            ("TypeScript", r"\btypescript\b"),
            ("Java", r"\bjava\b"),
            ("Python", r"\bpython\b"),
            ("C#", r"\bc#\b|\bc\s*sharp\b"),
            (".NET", r"\b\.net\b|\bdotnet\b"),
            ("Spring Boot", r"\bspring\s*boot\b"),
            ("React", r"\breact(?:\.js|js)?\b"),
            ("Vue.js", r"\bvue(?:\.js|js)?\b"),
            ("Angular", r"\bangular\b"),
            ("Node.js", r"\bnode(?:\.js|js)?\b"),
            ("PostgreSQL", r"\bpostgres(?:ql)?\b"),
            ("MySQL", r"\bmysql\b"),
            ("MongoDB", r"\bmongodb\b"),
            ("Redis", r"\bredis\b"),
            ("Elasticsearch", r"\belasticsearch\b"),
            ("OpenSearch", r"\bopensearch\b"),
            ("AWS", r"\baws\b|\bamazon web services\b"),
            ("GCP", r"\bgcp\b|\bgoogle cloud\b"),
            ("Azure", r"\bazure\b"),
            ("Docker", r"\bdocker\b"),
            ("Kubernetes", r"\bkubernetes\b|\bk8s\b"),
            ("Linux", r"\blinux\b"),
            ("CI/CD", r"\bci/cd\b|\bci\s*cd\b"),
            ("REST API", r"\brest\s*api\b|\brestful\b"),
            ("GraphQL", r"\bgraphql\b"),
            ("Microservices", r"\bmicroservices?\b"),
            ("Kafka", r"\bkafka\b"),
            ("RabbitMQ", r"\brabbitmq\b"),
            ("Selenium", r"\bselenium\b"),
            ("JQuery", r"\bjquery\b"),
            ("Information Security", r"an\s*ninh\s*thong\s*tin|\binformation\s+security\b"),
            ("Security Architecture", r"kien\s*truc\s*an\s*ninh\s*thong\s*tin|\bsecurity\s+architecture\b"),
            ("Security Policy", r"chinh\s*sach\s*an\s*ninh\s*thong\s*tin|\bsecurity\s+policy\b"),
            ("Risk Management", r"\brisk\s+management\b|quan\s*tri\s*rui\s*ro"),
            ("SOC", r"\bsoc\b|security\s*operations\s*center"),
            ("SIEM", r"\bsiem\b"),
            ("IAM", r"\biam\b|identity\s*(and|&)\s*access\s*management"),
            ("ISO 27001", r"\biso\s*27001\b"),
            ("NIST", r"\bnist\b"),
            ("CISSP", r"\bcissp\b"),
            ("CISM", r"\bcism\b"),
            ("PCI DSS", r"\bpci\s*dss\b"),
            ("Threat Modeling", r"\bthreat\s+model(?:ing|ling)\b"),
            ("Zero Trust", r"\bzero\s+trust\b"),
        ]

        found: list[str] = []
        for skill_name, pattern in keyword_patterns:
            if re.search(pattern, text, flags=re.IGNORECASE) or re.search(pattern, ascii_text, flags=re.IGNORECASE):
                found.append(skill_name)

        return self._dedupe_preserve_order(found)

    def _extract_skills(
        self,
        soup: BeautifulSoup,
        description: str,
        title: str,
        job_posting: dict | None = None,
    ) -> list[str]:
        merged_skills: list[str] = []

        if job_posting:
            skills_value = job_posting.get("skills")
            if isinstance(skills_value, str) and skills_value.strip():
                jsonld_skills = [item.strip() for item in skills_value.split(",") if item.strip()]
                merged_skills.extend([self._normalize_skill_name(skill) for skill in jsonld_skills])
            elif isinstance(skills_value, list):
                merged_skills.extend(
                    [self._normalize_skill_name(str(item).strip()) for item in skills_value if str(item).strip()]
                )

        merged_skills.extend(self._extract_skill_tags_from_page(soup))
        inference_text = f"{title}\n{description}" if title else description
        merged_skills.extend(self._extract_skills_from_text(inference_text))

        clean_skills: list[str] = []
        for skill in merged_skills:
            for expanded_skill in expand_skill_labels(skill):
                normalized = self._normalize_skill_name(expanded_skill)
                if not normalized:
                    continue
                if is_non_skill_role_label(normalized):
                    continue
                if len(normalized) > 50:
                    continue
                clean_skills.append(normalized)

        return self._dedupe_preserve_order(clean_skills)

    @staticmethod
    def _build_skill_details(skills: list[str], description: str, title: str) -> list[dict]:
        if not skills:
            return []

        title_text = (title or "").lower()
        normalized_description = description or ""
        description_ascii = JobCrawler._strip_accents(normalized_description).lower()

        lines = [line.strip() for line in normalized_description.splitlines() if line.strip()]
        if not lines:
            lines = [normalized_description]

        sentences = [item.strip() for item in re.split(r"[\n\.\!\?;•\-]+", normalized_description) if item.strip()]
        sentence_pool = sentences if sentences else lines

        must_keywords = [
            "must",
            "required",
            "mandatory",
            "strong",
            "expert",
            "proficient",
            "need",
            "yeu cau",
            "bat buoc",
            "kinh nghiem",
            "thanh thao",
            "bắt buộc",
            "yêu cầu",
            "kinh nghiệm",
            "thành thạo",
            "cần",
        ]
        preferred_keywords = [
            "preferred",
            "nice to have",
            "plus",
            "bonus",
            "advantage",
            "uu tien",
            "loi the",
            "ưu tiên",
            "lợi thế",
        ]
        optional_keywords = [
            "familiar",
            "exposure",
            "basic",
            "co ban",
            "cơ bản",
            "biet",
            "biết",
        ]
        requirement_headers = [
            "requirement",
            "qualification",
            "must-have",
            "job requirement",
            "yeu cau",
            "bắt buộc",
            "yêu cầu",
        ]
        preferred_headers = [
            "preferred",
            "nice to have",
            "benefit",
            "plus",
            "ưu tiên",
            "lợi thế",
        ]

        def _contains_any(value: str, tokens: list[str]) -> bool:
            lowered = value.lower()
            lowered_ascii = JobCrawler._strip_accents(lowered)
            return any(token in lowered or token in lowered_ascii for token in tokens)

        def classify_text(value: str) -> float:
            if _contains_any(value, must_keywords):
                return 0.25
            if _contains_any(value, preferred_keywords):
                return -0.05
            if _contains_any(value, optional_keywords):
                return -0.10
            return 0.10

        def section_bonus(evidence_text: str) -> float:
            target = evidence_text.lower()
            target_ascii = JobCrawler._strip_accents(target)

            for idx, line in enumerate(lines):
                candidate = line.lower()
                candidate_ascii = JobCrawler._strip_accents(candidate)
                if target in candidate or target_ascii in candidate_ascii:
                    window = " ".join(lines[max(0, idx - 3) : min(len(lines), idx + 1)])
                    if _contains_any(window, requirement_headers):
                        return 0.10
                    if _contains_any(window, preferred_headers):
                        return -0.05
                    break
            return 0.0

        def _skill_patterns(skill_name: str) -> tuple[re.Pattern[str], re.Pattern[str] | None]:
            plain = re.compile(rf"(?<!\w){re.escape(skill_name)}(?!\w)", re.IGNORECASE)
            normalized_skill = JobCrawler._strip_accents(skill_name)
            if len(normalized_skill) <= 3:
                return plain, None
            ascii_variant = re.compile(rf"(?<!\w){re.escape(normalized_skill)}(?!\w)", re.IGNORECASE)
            return plain, ascii_variant

        details: list[dict] = []
        seen: set[str] = set()

        for skill in skills:
            key = skill.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)

            importance = 0.60
            evidence = ""

            if key in title_text:
                importance += 0.20

            pattern, ascii_pattern = _skill_patterns(skill)
            matched_lines = [line for line in sentence_pool if pattern.search(line)]

            if not matched_lines and ascii_pattern is not None:
                for line in sentence_pool:
                    if ascii_pattern.search(JobCrawler._strip_accents(line)):
                        matched_lines.append(line)

            if matched_lines:
                evidence = matched_lines[0]
                importance += classify_text(evidence)
                importance += section_bonus(evidence)
            else:
                if pattern.search(normalized_description):
                    importance += 0.05
                elif ascii_pattern is not None and ascii_pattern.search(description_ascii):
                    importance += 0.05

            importance = max(0.35, min(1.0, importance))
            details.append(
                {
                    "skill": skill,
                    "importance": round(importance, 2),
                    "evidence_snippet": evidence[:220] if evidence else None,
                }
            )

        if len(details) >= 2:
            unique_importance = {item["importance"] for item in details}
            if len(unique_importance) == 1:
                ranked: list[tuple[float, int, dict]] = []
                total = len(details)
                for idx, item in enumerate(details):
                    signal = 0.0
                    skill_key = item["skill"].strip().lower()
                    evidence_text = (item.get("evidence_snippet") or "").lower()

                    if skill_key in title_text:
                        signal += 0.9

                    if evidence_text:
                        signal += 0.25
                        if _contains_any(evidence_text, must_keywords):
                            signal += 0.6
                        elif _contains_any(evidence_text, preferred_keywords):
                            signal -= 0.2
                        elif _contains_any(evidence_text, optional_keywords):
                            signal -= 0.35

                    # Ưu tiên nhẹ skill xuất hiện sớm trong danh sách đã trích xuất
                    signal += (total - idx) / (total * 10.0)
                    ranked.append((signal, idx, item))

                ranked.sort(key=lambda value: (value[0], -value[1]), reverse=True)

                top_cut = max(1, round(total * 0.3))
                mid_cut = max(top_cut + 1, round(total * 0.7))

                for rank_idx, (_, _, item) in enumerate(ranked):
                    if rank_idx < top_cut:
                        item["importance"] = 0.85
                    elif rank_idx < mid_cut:
                        item["importance"] = 0.65
                    else:
                        item["importance"] = 0.45

        return details

    def _extract_description(self, soup: BeautifulSoup, job_posting: dict | None = None) -> str:
        if job_posting:
            description_html = job_posting.get("description")
            if isinstance(description_html, str) and description_html.strip():
                unescaped = unescape(description_html)
                text = BeautifulSoup(unescaped, "html.parser").get_text("\n", strip=True)
                cleaned = self._clean_lines(text)
                if len(cleaned) > 80:
                    return cleaned

        description_selectors = [
            "div[class*='job-description']",
            "section[class*='job-description']",
            "div.prose",
            "article",
            "main",
        ]

        for selector in description_selectors:
            element = soup.select_one(selector)
            if element:
                text = element.get_text("\n", strip=True)
                cleaned = self._clean_lines(text)
                if len(cleaned) > 120:
                    return cleaned

        role_header = soup.find(string=re.compile(r"(Responsibilities|Mô tả công việc|Job Description|Yêu cầu)", re.I))
        if role_header:
            block = role_header.find_parent(["div", "section", "article"])
            if block:
                text = block.get_text("\n", strip=True)
                cleaned = self._clean_lines(text)
                if len(cleaned) > 80:
                    return cleaned

        fallback = self._clean_lines(soup.get_text("\n", strip=True))
        return fallback[:3000] if fallback else "Không tìm thấy mô tả công việc"

    def get_job_info(self, url: str):
        driver = None
        try:
            if not self.is_topdev_detail_url(url):
                raise ValueError("Chỉ hỗ trợ crawl link TopDev dạng /detail-jobs/ hoặc /viec-lam/")

            print(f"DEBUG: Bắt đầu crawl Selenium: {url}")
            driver = self._create_driver()
            page_load_timeout = False
            try:
                driver.get(url)
            except TimeoutException as exc:
                print(f"WARNING: Timeout khi tải trang {url}: {exc}")
                page_load_timeout = True
                try:
                    driver.execute_script("window.stop();")
                except Exception:
                    pass

            if not page_load_timeout:
                try:
                    WebDriverWait(driver, self._body_wait_seconds).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                    WebDriverWait(driver, self._body_wait_seconds).until(
                        lambda current_driver: current_driver.execute_script("return document.readyState") in ["interactive", "complete"]
                    )
                except TimeoutException as exc:
                    print(f"WARNING: Trang chưa sẵn sàng hoàn toàn {url}: {exc}")
            else:
                try:
                    WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                except TimeoutException:
                    print(f"WARNING: Page không load được dữ liệu {url}")

            time.sleep(self._post_load_sleep_seconds)

            html = driver.page_source
            soup = BeautifulSoup(html, "html.parser")
            job_posting = self._extract_job_posting_jsonld(soup)

            title = self._extract_title(soup)
            if job_posting and isinstance(job_posting.get("title"), str) and job_posting.get("title", "").strip():
                title = job_posting.get("title", "").strip()

            company_name = self._extract_company(soup, job_posting)
            location = self._extract_location(soup, job_posting)
            level = self._extract_level(soup, title, job_posting)
            employment_type = self._extract_employment_type(soup, job_posting)
            experience = self._extract_experience(soup, job_posting)
            application_deadline = self._extract_application_deadline(soup, job_posting)
            salary_range = self._extract_salary(soup, job_posting)
            description = self._extract_description(soup, job_posting)
            structured_sections = self._extract_structured_sections(soup)
            skills_list = self._extract_skills(soup, description, title, job_posting)
            skill_details = self._build_skill_details(skills_list, description, title)

            print(f"DEBUG: Title: {title}")
            print(f"DEBUG: Company: {company_name}")
            print(
                f"DEBUG: Skills ({len(skills_list)}): "
                f"{', '.join(skills_list[:15]) if skills_list else 'Không tìm thấy'}"
            )

            return {
                "title": title,
                "company_name": company_name,
                "location": location,
                "level": level.value if level else None,
                "employment_type": employment_type,
                "experience": experience,
                "application_deadline": application_deadline.isoformat() if application_deadline else None,
                "salary_range": salary_range,
                "description": description,
                "role_responsibilities": structured_sections.get("role_responsibilities"),
                "skills_qualifications": structured_sections.get("skills_qualifications"),
                "benefits": structured_sections.get("benefits"),
                "job_requirements": ", ".join(skills_list) if skills_list else "Không tìm thấy kỹ năng",
                "job_skill_details": skill_details,
                "source_url": driver.current_url if driver is not None else url,
                "source_website": self._source_name_topdev(),
            }
        except (WebDriverException, TimeoutException, ValueError) as exc:
            print(f"Lỗi Selenium {url}: {exc}")
            return None
        finally:
            if driver is not None:
                driver.quit()

    def save_job_to_db(self, db: Session, job_data: dict):
        if not job_data:
            return None

        try:
            def parse_salary_fields(salary_text: str | None) -> tuple[int | None, int | None, Currency | None]:
                if not salary_text:
                    return None, None, None

                normalized = salary_text.lower()
                currency: Currency | None = None
                if "$" in normalized or "usd" in normalized:
                    currency = Currency.USD
                elif "vnd" in normalized or "triệu" in normalized or "đ" in normalized:
                    currency = Currency.VND

                numbers = re.findall(r"\d+[\d\.,]*", salary_text)
                parsed: list[int] = []
                for raw in numbers:
                    cleaned = re.sub(r"[^\d]", "", raw)
                    if not cleaned:
                        continue
                    try:
                        parsed.append(int(cleaned))
                    except ValueError:
                        continue

                if not parsed:
                    return None, None, currency
                if len(parsed) == 1:
                    return parsed[0], parsed[0], currency
                return min(parsed), max(parsed), currency

            def parse_deadline(raw_deadline: str | None) -> date | None:
                if not raw_deadline:
                    return None
                raw_deadline = raw_deadline.strip()
                for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
                    try:
                        return datetime.strptime(raw_deadline, fmt).date()
                    except ValueError:
                        continue
                return None

            def upsert_company(company_name: str, location: str | None) -> Company:
                cleaned_name = (company_name or "N/A").strip() or "N/A"
                company = db.query(Company).filter(func.lower(Company.name) == cleaned_name.lower()).first()
                if company:
                    if location and not company.location:
                        company.location = location
                    return company

                company = Company(name=cleaned_name, location=location)
                db.add(company)
                db.flush()
                return company

            def replace_job_skills(job: Job, skills_text: str | None, skill_details: list[dict] | None = None) -> None:
                db.query(JobSkill).filter(JobSkill.job_job_id == job.job_id).delete()

                def to_importance_tier(raw_value: float) -> float:
                    if raw_value >= 2.5:
                        return 3.0
                    if raw_value >= 1.5:
                        return 2.0
                    if raw_value > 1.0:
                        return 1.0
                    if raw_value >= 0.8:
                        return 3.0
                    if raw_value >= 0.55:
                        return 2.0
                    return 1.0

                normalized_details: list[dict] = []
                if skill_details:
                    seen_keys: set[str] = set()
                    for detail in skill_details:
                        skill_name = str(detail.get("skill", "")).strip()
                        if not skill_name:
                            continue
                        if is_non_skill_role_label(skill_name):
                            continue
                        for expanded_skill in expand_skill_labels(skill_name):
                            if is_non_skill_role_label(expanded_skill):
                                continue
                            key = expanded_skill.lower()
                            if key in seen_keys:
                                continue
                            seen_keys.add(key)

                            raw_importance = detail.get("importance", 0.6)
                            try:
                                importance_value = float(raw_importance)
                            except (TypeError, ValueError):
                                importance_value = 0.6

                            normalized_details.append(
                                {
                                    "skill": expanded_skill,
                                    "importance": to_importance_tier(importance_value),
                                    "evidence_snippet": detail.get("evidence_snippet"),
                                }
                            )

                if normalized_details:
                    unique_tiers = {float(item.get("importance", 2.0)) for item in normalized_details}
                    if len(unique_tiers) == 1 and len(normalized_details) >= 2:
                        ranked_details = sorted(
                            normalized_details,
                            key=lambda item: (
                                len(str(item.get("evidence_snippet") or "")),
                                str(item.get("skill") or "").lower(),
                            ),
                            reverse=True,
                        )

                        total = len(ranked_details)
                        top_cut = max(1, round(total * 0.3))
                        mid_cut = max(top_cut + 1, round(total * 0.7))

                        for idx, item in enumerate(ranked_details):
                            if idx < top_cut:
                                item["importance"] = 3.0
                            elif idx < mid_cut:
                                item["importance"] = 2.0
                            else:
                                item["importance"] = 1.0

                if not normalized_details and not skills_text:
                    return

                if not normalized_details:
                    skill_names = [item.strip() for item in skills_text.split(",") if item.strip()]
                    deduped: list[str] = []
                    seen: set[str] = set()
                    for skill_name in skill_names:
                        if is_non_skill_role_label(skill_name):
                            continue
                        for expanded_skill in expand_skill_labels(skill_name):
                            if is_non_skill_role_label(expanded_skill):
                                continue
                            key = expanded_skill.lower()
                            if key in seen:
                                continue
                            seen.add(key)
                            deduped.append(expanded_skill)
                    normalized_details = [
                        {
                            "skill": skill_name,
                            "importance": 2.0,
                            "evidence_snippet": None,
                        }
                        for skill_name in deduped
                    ]

                for detail in normalized_details:
                    skill_name = detail["skill"]
                    skill = db.query(Skill).filter(func.lower(Skill.name) == skill_name.lower()).first()
                    if not skill:
                        skill = Skill(name=skill_name, category="technical", aliases=[], is_active=True)
                        db.add(skill)
                        db.flush()

                    db.add(
                        JobSkill(
                            job_job_id=job.job_id,
                            skill_skill_id=skill.skill_id,
                            importance=detail["importance"],
                            evidence_snippet=detail.get("evidence_snippet"),
                        )
                    )

            salary_min, salary_max, currency = parse_salary_fields(job_data.get("salary_range"))
            level = self._map_job_level(job_data.get("level"))
            employment_type = (job_data.get("employment_type") or "").strip() or None
            experience = (job_data.get("experience") or "").strip() or None
            application_deadline = parse_deadline(job_data.get("application_deadline"))
            raw_description = job_data.get("description") or ""
            cleaned_description = self._clean_lines(raw_description)
            role_responsibilities = (job_data.get("role_responsibilities") or "").strip() or None
            skills_qualifications = (job_data.get("skills_qualifications") or "").strip() or None
            benefits = (job_data.get("benefits") or "").strip() or None
            company = upsert_company(job_data.get("company_name") or "N/A", job_data.get("location"))
            existing_job = db.query(Job).filter(Job.source_url == job_data["source_url"]).first()

            if existing_job:
                existing_job.title = job_data["title"]
                existing_job.company_company_id = company.company_id
                existing_job.level = level
                existing_job.employment_type = employment_type
                existing_job.experience = experience
                existing_job.application_deadline = application_deadline
                existing_job.location = job_data["location"]
                existing_job.salary_min = salary_min
                existing_job.salary_max = salary_max
                existing_job.currency = currency
                existing_job.description_raw = raw_description
                existing_job.description_clean = cleaned_description
                existing_job.role_responsibilities = role_responsibilities
                existing_job.skills_qualifications = skills_qualifications
                existing_job.benefits = benefits
                existing_job.source_site = job_data["source_website"]
                existing_job.scraped_at = datetime.now(timezone.utc)
                existing_job.status = JobStatus.active

                replace_job_skills(existing_job, job_data.get("job_requirements"), job_data.get("job_skill_details"))
                db.commit()
                db.refresh(existing_job)
                print(f"--- UPDATED --- {existing_job.title}")
                return existing_job

            new_job = Job(
                company_company_id=company.company_id,
                title=job_data["title"],
                level=level,
                employment_type=employment_type,
                experience=experience,
                application_deadline=application_deadline,
                location=job_data["location"],
                salary_min=salary_min,
                salary_max=salary_max,
                currency=currency,
                description_raw=raw_description,
                description_clean=cleaned_description,
                role_responsibilities=role_responsibilities,
                skills_qualifications=skills_qualifications,
                benefits=benefits,
                source_url=job_data["source_url"],
                source_site=job_data["source_website"],
                scraped_at=datetime.now(timezone.utc),
                status=JobStatus.active,
            )
            db.add(new_job)
            db.flush()

            replace_job_skills(new_job, job_data.get("job_requirements"), job_data.get("job_skill_details"))
            db.commit()
            db.refresh(new_job)
            print(f"--- CREATED --- {new_job.title}")
            return new_job
        except SQLAlchemyError as exc:
            db.rollback()
            print(f"Lỗi Database: {exc}")
            return None


class CrawlerService:
    @staticmethod
    def crawl_job(db: Session, url: str):
        crawler = JobCrawler()
        job_data = crawler.get_job_info(url)
        if not job_data:
            raise ValueError("Không crawl được dữ liệu job từ URL đã cung cấp")

        crawler.save_job_to_db(db, job_data)
        return job_data

    @staticmethod
    def crawl_jobs(db: Session, urls: Iterable[str]):
        crawler = JobCrawler()
        results = []
        for url in urls:
            if not crawler.is_topdev_detail_url(url):
                results.append(
                    {
                        "url": url,
                        "status": "skipped",
                        "reason": "topdev_detail_only",
                    }
                )
                continue

            job_data = crawler.get_job_info(url)
            if not job_data:
                results.append({"url": url, "status": "failed"})
                continue

            saved = crawler.save_job_to_db(db, job_data)
            results.append(
                {
                    "url": url,
                    "status": "success" if saved else "failed",
                    "title": job_data.get("title"),
                    "source_website": job_data.get("source_website"),
                }
            )
        return results
