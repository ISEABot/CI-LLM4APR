"""Generate a polished static site for published summaries."""

from __future__ import annotations

import html
import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from core.models import PaperSummary, SiteConfig


class StaticSiteBuilder:
    """Build a bilingual static site with incremental update support."""

    def __init__(self, site_config: SiteConfig, language: str = "zh-CN"):
        self.site_config = site_config
        self.language = self._normalise_language(language)
        self._existing_papers: Dict[str, Dict[str, Any]] = {}  # arxiv_id -> paper data
        self._existing_manifest: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # helpers

    @staticmethod
    def _normalise_language(language: str | None) -> str:
        if language and language.lower().startswith("en"):
            return "en"
        return "zh-CN"

    @staticmethod
    def _html_lang(lang: str) -> str:
        return "en" if lang == "en" else "zh-cn"

    @staticmethod
    def _escape(text: str | None) -> str:
        return html.escape(text or "", quote=True)

    def _i18n(self, zh_text: str, en_text: str) -> str:
        default_text = zh_text if self.language != "en" else en_text
        return (
            f"<span class=\"i18n\" data-lang-zh=\"{self._escape(zh_text)}\" "
            f"data-lang-en=\"{self._escape(en_text)}\">{self._escape(default_text)}</span>"
        )

    def _lang_toggle_button(self) -> str:
        return (
            "<button id='lang-toggle' type='button' class='lang-toggle' "
            "aria-label='toggle language'>English</button>"
        )

    def _language_script(self) -> str:
        default_lang = self.language
        return f"""
<script>
(function() {{
  const defaultLang = '{default_lang}';
  function normalise(lang) {{
    if (!lang) return defaultLang;
    const lower = lang.toLowerCase();
    if (lower.startsWith('en')) return 'en';
    return 'zh-CN';
  }}

  function apply(lang) {{
    const norm = normalise(lang);
    document.documentElement.setAttribute('data-lang', norm);
    document.documentElement.lang = norm === 'en' ? 'en' : 'zh-cn';
    document.querySelectorAll('.i18n').forEach((el) => {{
      const text = norm === 'en' ? el.dataset.langEn : el.dataset.langZh;
      if (text !== undefined) {{
        el.textContent = text;
      }}
    }});
    const toggle = document.getElementById('lang-toggle');
    if (toggle) {{
      toggle.textContent = norm === 'en' ? '中文' : 'English';
    }}
    localStorage.setItem('ci-llm4apr-lang', norm);
  }}

  const initial = localStorage.getItem('ci-llm4apr-lang') || defaultLang;
  apply(initial);
  const toggle = document.getElementById('lang-toggle');
  if (toggle) {{
    toggle.addEventListener('click', () => {{
      const current = document.documentElement.getAttribute('data-lang') || defaultLang;
      apply(current === 'en' ? 'zh-CN' : 'en');
    }});
  }}
}})();
</script>
"""

    # ------------------------------------------------------------------
    # public API

    def build(self, summaries: Iterable[PaperSummary]) -> Dict[str, str]:
        """Incremental build: merge new summaries with existing data."""
        output_dir = Path(self.site_config.output_dir)

        # Step 1: Load existing data (DO NOT delete the directory!)
        self._load_existing_data()

        # Step 2: Ensure directory structure exists
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "data").mkdir(exist_ok=True)
        (output_dir / "archives").mkdir(exist_ok=True)
        (output_dir / "topics").mkdir(exist_ok=True)

        # Step 3: Process new summaries
        batch_id = self._get_batch_id()
        new_papers: List[PaperSummary] = []
        topic_groups: Dict[str, List[PaperSummary]] = defaultdict(list)

        for summary in summaries:
            arxiv_id = summary.paper.arxiv_id

            # Skip if already exists (deduplication)
            if arxiv_id in self._existing_papers:
                print(f"[INFO] Paper {arxiv_id} already exists, skipping...")
                continue

            # Add to new papers
            new_papers.append(summary)
            topic_groups[summary.topic.name].append(summary)

            # Store paper data
            paper_data = summary.to_dict()
            paper_data["batch_id"] = batch_id
            paper_data["indexed_at"] = datetime.utcnow().isoformat()
            self._existing_papers[arxiv_id] = paper_data

            # Update manifest paper index
            self._existing_manifest.setdefault("papers", {})[arxiv_id] = {
                "topic": summary.topic.name,
                "batch": batch_id,
                "title": summary.paper.title,
                "published": summary.paper.published.strftime("%Y-%m-%d") if summary.paper.published else "",
                "score": float(self._format_score(summary.score_details)),
            }

        # Step 4: Generate paper detail pages (only for new papers)
        for topic_name, topic_summaries in topic_groups.items():
            topic_dir = output_dir / "topics" / topic_name
            topic_dir.mkdir(parents=True, exist_ok=True)

            for summary in topic_summaries:
                file_name = f"{summary.paper.arxiv_id}.html"
                file_path = topic_dir / file_name
                file_path.write_text(self._render_paper(summary), encoding="utf-8")

        # Step 5: Update batch information
        if new_papers:
            self._update_batch_info(batch_id, new_papers)
            self._render_batch_archive(output_dir, batch_id, new_papers)
            print(f"[INFO] Added {len(new_papers)} new papers to batch {batch_id}")
        else:
            print("[INFO] No new papers to add")

        # Step 6: Regenerate index pages (with latest batch papers)
        self._render_main_index(output_dir, batch_id)
        self._render_archive_index(output_dir)

        # Step 7: Save updated data
        self._save_manifest(output_dir)
        self._save_paper_data(output_dir)

        return {"index": str(output_dir / "index.html")}

    # ------------------------------------------------------------------
    # data persistence layer

    def _load_existing_data(self) -> None:
        """Load existing manifest and paper data from site directory."""
        output_dir = Path(self.site_config.output_dir)

        # Load manifest
        manifest_path = output_dir / "manifest.json"
        if manifest_path.exists():
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    self._existing_manifest = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._existing_manifest = self._create_empty_manifest()
        else:
            self._existing_manifest = self._create_empty_manifest()

        # Load paper data store
        papers_path = output_dir / "data" / "papers.json"
        if papers_path.exists():
            try:
                with open(papers_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._existing_papers = data.get("papers", {})
            except (json.JSONDecodeError, IOError):
                self._existing_papers = {}
        else:
            self._existing_papers = {}

    def _create_empty_manifest(self) -> Dict[str, Any]:
        """Create empty manifest structure."""
        return {
            "version": "2.0",
            "base_url": self.site_config.base_url,
            "last_updated": None,
            "statistics": {
                "total_papers": 0,
                "total_batches": 0,
            },
            "batches": [],
            "papers": {},
        }

    def _get_batch_id(self) -> str:
        """Generate batch ID based on current ISO week."""
        now = datetime.utcnow()
        year, week, _ = now.isocalendar()
        return f"{year}-W{week:02d}"

    def _update_batch_info(self, batch_id: str, papers: List[PaperSummary]) -> None:
        """Update manifest with new batch information."""
        topics = list(set(p.topic.name for p in papers))

        # Check if batch already exists
        existing_batch = next(
            (b for b in self._existing_manifest.get("batches", []) if b["id"] == batch_id),
            None
        )

        if existing_batch:
            existing_batch["paper_count"] += len(papers)
            existing_batch["topics"] = list(set(existing_batch.get("topics", [])) | set(topics))
        else:
            self._existing_manifest.setdefault("batches", []).insert(0, {
                "id": batch_id,
                "generated": datetime.utcnow().isoformat(),
                "paper_count": len(papers),
                "topics": topics,
            })

        # Update statistics
        stats = self._existing_manifest.setdefault("statistics", {})
        stats["total_papers"] = len(self._existing_papers)
        stats["total_batches"] = len(self._existing_manifest.get("batches", []))

        self._existing_manifest["last_updated"] = datetime.utcnow().isoformat()

    def _save_manifest(self, output_dir: Path) -> None:
        """Save manifest.json."""
        manifest_path = output_dir / "manifest.json"

        # Also include legacy fields for backward compatibility
        self._existing_manifest["base_url"] = self.site_config.base_url
        self._existing_manifest["generated"] = os.environ.get("PIPELINE_RUN_AT") or datetime.utcnow().isoformat()

        # Build topics mapping for legacy compatibility
        topics_mapping: Dict[str, List[str]] = defaultdict(list)
        for arxiv_id, info in self._existing_manifest.get("papers", {}).items():
            topics_mapping[info.get("topic", "unknown")].append(arxiv_id)
        self._existing_manifest["topics"] = dict(topics_mapping)

        manifest_path.write_text(
            json.dumps(self._existing_manifest, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

    def _save_paper_data(self, output_dir: Path) -> None:
        """Save paper data store."""
        data_dir = output_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        papers_path = data_dir / "papers.json"
        papers_path.write_text(
            json.dumps({"version": "1.0", "papers": self._existing_papers}, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

    def _render_batch_archive(self, output_dir: Path, batch_id: str, papers: List[PaperSummary]) -> None:
        """Render archive page for a specific batch."""
        batch_dir = output_dir / "archives" / batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)

        # Save batch metadata
        batch_meta = {
            "id": batch_id,
            "generated": datetime.utcnow().isoformat(),
            "papers": [p.paper.arxiv_id for p in papers],
        }
        (batch_dir / "batch.json").write_text(
            json.dumps(batch_meta, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        # Render batch index HTML
        batch_html = self._render_batch_index_html(batch_id, papers)
        (batch_dir / "index.html").write_text(batch_html, encoding="utf-8")

    def _render_main_index(self, output_dir: Path, current_batch_id: str) -> None:
        """Render main index page showing latest batch papers and archive link."""
        # Get papers from the current batch
        current_batch_papers: List[Dict[str, Any]] = []
        for arxiv_id, paper_data in self._existing_papers.items():
            if paper_data.get("batch_id") == current_batch_id:
                current_batch_papers.append(paper_data)

        # Group by topic
        topic_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for paper_data in current_batch_papers:
            topic_name = paper_data.get("topic", {}).get("name", "Unknown")
            topic_groups[topic_name].append(paper_data)

        index_entries = [(name, papers) for name, papers in topic_groups.items()]

        # Calculate total stats
        total_papers = self._existing_manifest.get("statistics", {}).get("total_papers", len(self._existing_papers))
        total_batches = len(self._existing_manifest.get("batches", []))

        index_html = self._render_index_with_archives(index_entries, total_papers, total_batches, current_batch_id)
        (output_dir / "index.html").write_text(index_html, encoding="utf-8")

    def _render_archive_index(self, output_dir: Path) -> None:
        """Render archive listing page."""
        archives_dir = output_dir / "archives"
        archives_dir.mkdir(exist_ok=True)

        batches = self._existing_manifest.get("batches", [])
        archive_html = self._render_archive_listing_html(batches)
        (archives_dir / "index.html").write_text(archive_html, encoding="utf-8")

    # ------------------------------------------------------------------
    # rendering helpers

    def _render_index(self, index_entries: Sequence[Tuple[str, Sequence[PaperSummary]]]) -> str:
        base_url = self.site_config.base_url.rstrip("/")
        total_papers = sum(len(items) for _, items in index_entries)
        total_topics = len(index_entries)
        html_lang = self._html_lang(self.language)
        
        # Calculate timestamp for display
        import datetime
        update_time = datetime.datetime.now().strftime("%Y-%m-%d")
        
        style_block = """
:root {
  --page-max-width: 1200px;
  --sidebar-width: 240px;
  --bg-color: #f8f9fa;
  --card-bg: #ffffff;
  --border-color: #e5e7eb;
  --accent: #2563eb;
  --accent-light: #eff6ff;
  --text-primary: #111827;
  --text-secondary: #6b7280;
  --text-muted: #9ca3af;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Helvetica', 'Arial', sans-serif;
}

* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  margin: 0;
  min-height: 100vh;
  background: var(--bg-color);
  color: var(--text-primary);
  line-height: 1.6;
}

.page {
  min-height: 100vh;
  display: flex;
}

/* Left Sidebar Navigation */
.sidebar {
  width: var(--sidebar-width);
  background: var(--card-bg);
  border-right: 1px solid var(--border-color);
  position: fixed;
  left: 0;
  top: 0;
  bottom: 0;
  overflow-y: auto;
  z-index: 100;
}

.sidebar-header {
  padding: 1.5rem 1.25rem;
  border-bottom: 1px solid var(--border-color);
}

.sidebar-header h1 {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 0.5rem;
}

/* Stats in Sidebar */
.sidebar-stats {
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--border-color);
}

.sidebar-stats h3 {
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  margin-bottom: 0.75rem;
}

.sidebar-stat-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0;
  font-size: 0.875rem;
}

.sidebar-stat-icon {
  font-size: 1rem;
}

.sidebar-stat-label {
  color: var(--text-secondary);
  flex: 1;
}

.sidebar-stat-value {
  font-weight: 600;
  color: var(--accent);
}

.sidebar-nav {
  padding: 1rem 0;
}

.sidebar-nav h3 {
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  padding: 0 1.25rem 0.5rem;
}

.sidebar-nav ul {
  list-style: none;
}

.sidebar-nav li {
  margin: 0.25rem 0;
}

.sidebar-nav a {
  display: block;
  padding: 0.5rem 1.25rem;
  color: var(--text-secondary);
  text-decoration: none;
  font-size: 0.875rem;
  transition: all 0.2s;
}

.sidebar-nav a:hover {
  background: var(--accent-light);
  color: var(--accent);
}

/* Main Content Area */
.main-content {
  margin-left: var(--sidebar-width);
  flex: 1;
  display: flex;
  flex-direction: column;
}

.top-bar {
  background: var(--card-bg);
  border-bottom: 1px solid var(--border-color);
  padding: 1.25rem 2rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
  position: sticky;
  top: 0;
  z-index: 50;
}

.top-bar-title {
  flex: 1;
}

.top-bar-title h1 {
  font-size: 1.75rem;
  font-weight: 700;
  margin-bottom: 0.25rem;
  color: var(--text-primary);
}

.top-bar-title p {
  font-size: 0.95rem;
  color: var(--text-secondary);
  margin: 0;
}

.lang-toggle {
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  color: var(--text-primary);
  border-radius: 6px;
  padding: 0.5rem 1rem;
  font-size: 0.875rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
  flex-shrink: 0;
}

.lang-toggle:hover {
  background: var(--accent-light);
  border-color: var(--accent);
  color: var(--accent);
}

/* Content Container */
.container {
  flex: 1;
  padding: 1rem 1.5rem;
  max-width: var(--page-max-width);
  margin: 0 auto;
  width: 100%;
}

/* Topic Sections - Flat List Design */
.topic-section {
  margin-bottom: 2rem;
  scroll-margin-top: 4rem;
}

.topic-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
  padding-bottom: 0.75rem;
  border-bottom: 2px solid var(--border-color);
}

.topic-title {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--text-primary);
}

.topic-count {
  font-size: 0.875rem;
  color: var(--text-muted);
  background: var(--bg-color);
  padding: 0.25rem 0.75rem;
  border-radius: 12px;
}

/* Paper List - Flat Design */
.paper-list {
  list-style: none;
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  overflow: hidden;
}

.paper-item {
  padding: 0.875rem 1.25rem;
  border-bottom: 1px solid var(--border-color);
  transition: background 0.2s;
}

.paper-item:last-child {
  border-bottom: none;
}

.paper-item:hover {
  background: var(--accent-light);
}

.paper-item a {
  text-decoration: none;
  color: var(--text-primary);
  font-weight: 600;
  font-size: 1.05rem;
  line-height: 1.5;
  transition: color 0.2s;
}

.paper-item a:hover {
  color: var(--accent);
}

.paper-meta {
  display: flex;
  gap: 1.5rem;
  margin-top: 0.5rem;
  font-size: 0.875rem;
  color: var(--text-secondary);
}

.paper-meta-item {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  transition: transform 0.25s ease, box-shadow 0.25s ease;
}

.topic-card:hover {
  transform: translateY(-6px);
  box-shadow: 0 28px 48px rgba(15, 23, 42, 0.12);
}

.topic-card h2 {
  margin: 0;
  font-size: 1.25rem;
  font-weight: 700;
}

.topic-count {
  font-size: 0.9rem;
  color: var(--text-muted);
  border-bottom: 1px solid var(--card-border);
  padding-bottom: 0.8rem;
}

.paper-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
}

.paper-item a {
  text-decoration: none;
  color: var(--text-primary);
  font-weight: 600;
  font-size: 1.02rem;
  line-height: 1.45;
  transition: color 0.2s ease;
}

.paper-item a:hover {
  color: var(--accent);
}

.paper-meta {
  font-size: 0.85rem;
}

.site-footer {
  text-align: center;
  padding: 2rem;
  color: var(--text-muted);
  font-size: 0.875rem;
  background: var(--card-bg);
  border-top: 1px solid var(--border-color);
}

/* Responsive Design */
@media (max-width: 1024px) {
  .sidebar {
    transform: translateX(-100%);
    transition: transform 0.3s;
  }
  
  .main-content {
    margin-left: 0;
  }
  
  .stats-panel {
    flex-wrap: wrap;
    gap: 0.75rem;
  }
  
  .stat-item {
    font-size: 0.875rem;
  }
}

@media (max-width: 768px) {
  .top-bar {
    padding: 1rem;
    flex-direction: column;
    align-items: flex-start;
    gap: 1rem;
  }
  
  .stats-panel {
    width: 100%;
  }
  
  .container {
    padding: 1rem;
  }
  
  .paper-meta {
    flex-direction: column;
    gap: 0.25rem;
  }
}
""".strip()

        # Build sidebar navigation
        sidebar_nav = []
        for topic_name, topic_summaries in index_entries:
            topic_label = topic_summaries[0].topic.label
            sidebar_nav.append(f"<li><a href='#{topic_name}'>{self._escape(topic_label)}</a></li>")
        
        html_parts = [
            "<!DOCTYPE html>",
            f"<html lang='{html_lang}'>",
            "<head>",
            "  <meta charset='utf-8'>",
            "  <meta name='viewport' content='width=device-width, initial-scale=1'>",
            f"  <title>{self._escape('CI-LLM4APR')}</title>",
            f"  <style>{style_block}</style>",
            "</head>",
            f"<body data-lang='{self.language}'>",
            "  <div class='page'>",
            # Sidebar with stats
            "    <aside class='sidebar'>",
            "      <div class='sidebar-header'>",
            f"        <h1>{self._i18n('CI-LLM4APR', 'CI-LLM4APR')}</h1>",
            "      </div>",
            # Stats in sidebar
            "      <div class='sidebar-stats'>",
            f"        <h3>{self._i18n('统计信息', 'Statistics')}</h3>",
            "        <div class='sidebar-stat-item'>",
            f"          <span class='sidebar-stat-icon'>📄</span>",
            f"          <span class='sidebar-stat-label'>{self._i18n('论文', 'Papers')}</span>",
            f"          <span class='sidebar-stat-value'>{total_papers}</span>",
            "        </div>",
            "        <div class='sidebar-stat-item'>",
            f"          <span class='sidebar-stat-icon'>🏷️</span>",
            f"          <span class='sidebar-stat-label'>{self._i18n('专题', 'Topics')}</span>",
            f"          <span class='sidebar-stat-value'>{total_topics}</span>",
            "        </div>",
            "        <div class='sidebar-stat-item'>",
            f"          <span class='sidebar-stat-icon'>🕒</span>",
            f"          <span class='sidebar-stat-label'>{self._i18n('更新', 'Updated')}</span>",
            f"          <span class='sidebar-stat-value'>{update_time}</span>",
            "        </div>",
            "      </div>",
            # Topics navigation
            "      <nav class='sidebar-nav'>",
            f"        <h3>{self._i18n('专题导航', 'Topics')}</h3>",
            "        <ul>",
            "\n".join(f"          {item}" for item in sidebar_nav),
            "        </ul>",
            "      </nav>",
            "    </aside>",
            # Main content with title in sticky header
            "    <div class='main-content'>",
            "      <header class='top-bar'>",
            "        <div class='top-bar-title'>",
            f"          <h1>{self._i18n('每周精选科技论文', 'Weekly Research Highlights')}</h1>",
            f"          <p>{self._i18n('聚合最新 arXiv 热门论文，结合智能阅读助手生成深入摘要与阅读指南', 'Curated arXiv picks with AI-generated summaries and reading guides')}</p>",
            "        </div>",
            f"        {self._lang_toggle_button()}",
            "      </header>",
            "      <main class='container'>",
        ]

        if not index_entries:
            html_parts.append(
                f"        <p>{self._i18n('暂未生成任何论文摘要。', 'No paper summaries generated yet.')}</p>"
            )
        else:
            for topic_name, topic_summaries in index_entries:
                topic_label = topic_summaries[0].topic.label
                count = len(topic_summaries)
                html_parts.append(f"        <section class='topic-section' id='{topic_name}'>")
                html_parts.append("          <div class='topic-header'>")
                html_parts.append(f"            <h2 class='topic-title'>{self._escape(topic_label)}</h2>")
                html_parts.append(f"            <span class='topic-count'>{count} {self._i18n('篇', 'papers')}</span>")
                html_parts.append("          </div>")
                html_parts.append("          <ul class='paper-list'>")
                
                for summary in topic_summaries:
                    # Always use relative path for cross-page links to work both locally and on GitHub Pages
                    relative_path = f"topics/{topic_name}/{summary.paper.arxiv_id}.html"
                    url = relative_path
                    title = self._escape(summary.paper.title)
                    score = self._format_score(summary.score_details)
                    
                    # Build meta info
                    author_text = ""
                    if summary.paper.authors:
                        author_preview = ", ".join(summary.paper.authors[:3])
                        if len(summary.paper.authors) > 3:
                            author_preview += ", ..."
                        author_text = self._escape(author_preview)
                    
                    html_parts.append("            <li class='paper-item'>")
                    html_parts.append(f"              <a href='{self._escape(url)}'>{title}</a>")
                    html_parts.append("              <div class='paper-meta'>")
                    html_parts.append(f"                <span class='paper-meta-item'>📊 {self._i18n('相关度', 'Score')}: {score}</span>")
                    if author_text:
                        html_parts.append(f"                <span class='paper-meta-item'>👤 {author_text}</span>")
                    if summary.paper.published:
                        pub_date = summary.paper.published.strftime("%Y-%m-%d")
                        html_parts.append(f"                <span class='paper-meta-item'>📅 {pub_date}</span>")
                    html_parts.append("              </div>")
                    html_parts.append("            </li>")
                
                html_parts.append("          </ul>")
                html_parts.append("        </section>")

        html_parts.append("      </main>")
        html_parts.append(
            f"      <footer class='site-footer'>{self._i18n('由 CI-LLM4APR 自动生成', 'Generated by the CI-LLM4APR pipeline')}</footer>"
        )
        html_parts.append("    </div>")
        html_parts.append("  </div>")
        html_parts.append(self._language_script())
        html_parts.append("</body>")
        html_parts.append("</html>")
        return "\n".join(html_parts)

    def _render_index_with_archives(
        self,
        index_entries: List[Tuple[str, List[Dict[str, Any]]]],
        total_papers: int,
        total_batches: int,
        current_batch_id: str,
    ) -> str:
        """Render main index page with archive link (uses paper data dicts)."""
        html_lang = self._html_lang(self.language)
        update_time = datetime.utcnow().strftime("%Y-%m-%d")
        batch_paper_count = sum(len(items) for _, items in index_entries)
        total_topics = len(index_entries)

        style_block = self._get_index_style()

        # Build sidebar navigation
        sidebar_nav = []
        for topic_name, topic_papers in index_entries:
            topic_label = topic_papers[0].get("topic", {}).get("label", topic_name) if topic_papers else topic_name
            sidebar_nav.append(f"<li><a href='#{topic_name}'>{self._escape(topic_label)}</a></li>")

        html_parts = [
            "<!DOCTYPE html>",
            f"<html lang='{html_lang}'>",
            "<head>",
            "  <meta charset='utf-8'>",
            "  <meta name='viewport' content='width=device-width, initial-scale=1'>",
            f"  <title>{self._escape('CI-LLM4APR')}</title>",
            f"  <style>{style_block}</style>",
            "</head>",
            f"<body data-lang='{self.language}'>",
            "  <div class='page'>",
            # Sidebar with stats
            "    <aside class='sidebar'>",
            "      <div class='sidebar-header'>",
            f"        <h1>{self._i18n('CI-LLM4APR', 'CI-LLM4APR')}</h1>",
            "      </div>",
            # Stats in sidebar
            "      <div class='sidebar-stats'>",
            f"        <h3>{self._i18n('统计信息', 'Statistics')}</h3>",
            "        <div class='sidebar-stat-item'>",
            "          <span class='sidebar-stat-icon'>📄</span>",
            f"          <span class='sidebar-stat-label'>{self._i18n('总论文数', 'Total Papers')}</span>",
            f"          <span class='sidebar-stat-value'>{total_papers}</span>",
            "        </div>",
            "        <div class='sidebar-stat-item'>",
            "          <span class='sidebar-stat-icon'>📦</span>",
            f"          <span class='sidebar-stat-label'>{self._i18n('历史批次', 'Batches')}</span>",
            f"          <span class='sidebar-stat-value'>{total_batches}</span>",
            "        </div>",
            "        <div class='sidebar-stat-item'>",
            "          <span class='sidebar-stat-icon'>🕒</span>",
            f"          <span class='sidebar-stat-label'>{self._i18n('更新', 'Updated')}</span>",
            f"          <span class='sidebar-stat-value'>{update_time}</span>",
            "        </div>",
            "      </div>",
            # Topics navigation
            "      <nav class='sidebar-nav'>",
            f"        <h3>{self._i18n('专题导航', 'Topics')}</h3>",
            "        <ul>",
            "\n".join(f"          {item}" for item in sidebar_nav),
            "        </ul>",
            "      </nav>",
            # Archive link
            "      <div class='sidebar-archive-link'>",
            f"        <a href='archives/'>{self._i18n('📚 查看历史归档', '📚 View Archives')}</a>",
            "      </div>",
            "    </aside>",
            # Main content
            "    <div class='main-content'>",
            "      <header class='top-bar'>",
            "        <div class='top-bar-title'>",
            f"          <h1>{self._i18n('每周精选科技论文', 'Weekly Research Highlights')}</h1>",
            f"          <p>{self._i18n(f'本周批次: {current_batch_id} ({batch_paper_count} 篇)', f'Current Batch: {current_batch_id} ({batch_paper_count} papers)')}</p>",
            "        </div>",
            f"        {self._lang_toggle_button()}",
            "      </header>",
            "      <main class='container'>",
        ]

        if not index_entries or batch_paper_count == 0:
            html_parts.append(
                f"        <p>{self._i18n('本周暂无新论文。', 'No new papers this week.')}</p>"
            )
            html_parts.append(
                f"        <p><a href='archives/'>{self._i18n('查看历史归档', 'View archives')}</a></p>"
            )
        else:
            for topic_name, topic_papers in index_entries:
                topic_label = topic_papers[0].get("topic", {}).get("label", topic_name) if topic_papers else topic_name
                count = len(topic_papers)
                html_parts.append(f"        <section class='topic-section' id='{topic_name}'>")
                html_parts.append("          <div class='topic-header'>")
                html_parts.append(f"            <h2 class='topic-title'>{self._escape(topic_label)}</h2>")
                html_parts.append(f"            <span class='topic-count'>{count} {self._i18n('篇', 'papers')}</span>")
                html_parts.append("          </div>")
                html_parts.append("          <ul class='paper-list'>")

                for paper_data in topic_papers:
                    paper_info = paper_data.get("paper", {})
                    arxiv_id = paper_info.get("arxiv_id", "unknown")
                    title = self._escape(paper_info.get("title", "Untitled"))
                    score_details = paper_data.get("score_details", {})
                    score = score_details.get("total_score", 0)

                    relative_path = f"topics/{topic_name}/{arxiv_id}.html"
                    url = relative_path

                    # Build meta info
                    authors = paper_info.get("authors", [])
                    author_text = ""
                    if authors:
                        author_preview = ", ".join(authors[:3])
                        if len(authors) > 3:
                            author_preview += ", ..."
                        author_text = self._escape(author_preview)

                    html_parts.append("            <li class='paper-item'>")
                    html_parts.append(f"              <a href='{self._escape(url)}'>{title}</a>")
                    html_parts.append("              <div class='paper-meta'>")
                    html_parts.append(f"                <span class='paper-meta-item'>📊 {self._i18n('相关度', 'Score')}: {score:.1f}</span>")
                    if author_text:
                        html_parts.append(f"                <span class='paper-meta-item'>👤 {author_text}</span>")
                    pub_date = paper_info.get("published", "")
                    if pub_date:
                        html_parts.append(f"                <span class='paper-meta-item'>📅 {pub_date[:10]}</span>")
                    html_parts.append("              </div>")
                    html_parts.append("            </li>")

                html_parts.append("          </ul>")
                html_parts.append("        </section>")

        html_parts.append("      </main>")
        html_parts.append(
            f"      <footer class='site-footer'>{self._i18n('由 CI-LLM4APR 自动生成', 'Generated by the CI-LLM4APR pipeline')}</footer>"
        )
        html_parts.append("    </div>")
        html_parts.append("  </div>")
        html_parts.append(self._language_script())
        html_parts.append("</body>")
        html_parts.append("</html>")
        return "\n".join(html_parts)

    def _render_batch_index_html(self, batch_id: str, papers: List[PaperSummary]) -> str:
        """Render archive page for a specific batch."""
        html_lang = self._html_lang(self.language)
        style_block = self._get_index_style()

        # Group by topic
        topic_groups: Dict[str, List[PaperSummary]] = defaultdict(list)
        for paper in papers:
            topic_groups[paper.topic.name].append(paper)

        html_parts = [
            "<!DOCTYPE html>",
            f"<html lang='{html_lang}'>",
            "<head>",
            "  <meta charset='utf-8'>",
            "  <meta name='viewport' content='width=device-width, initial-scale=1'>",
            f"  <title>{self._escape(f'Archive - {batch_id}')}</title>",
            f"  <style>{style_block}</style>",
            "</head>",
            f"<body data-lang='{self.language}'>",
            "  <div class='page'>",
            "    <aside class='sidebar'>",
            "      <div class='sidebar-header'>",
            f"        <a href='../../' style='text-decoration:none;color:inherit;'><h1>{self._i18n('🏠 首页', '🏠 Home')}</h1></a>",
            "      </div>",
            "      <div class='sidebar-stats'>",
            f"        <h3>{self._i18n('批次信息', 'Batch Info')}</h3>",
            "        <div class='sidebar-stat-item'>",
            f"          <span class='sidebar-stat-label'>{self._i18n('批次', 'Batch')}</span>",
            f"          <span class='sidebar-stat-value'>{batch_id}</span>",
            "        </div>",
            "        <div class='sidebar-stat-item'>",
            f"          <span class='sidebar-stat-label'>{self._i18n('论文数', 'Papers')}</span>",
            f"          <span class='sidebar-stat-value'>{len(papers)}</span>",
            "        </div>",
            "      </div>",
            "      <div class='sidebar-archive-link'>",
            f"        <a href='../'>{self._i18n('📚 返回归档列表', '📚 Back to Archives')}</a>",
            "      </div>",
            "    </aside>",
            "    <div class='main-content'>",
            "      <header class='top-bar'>",
            "        <div class='top-bar-title'>",
            f"          <h1>{self._i18n(f'归档: {batch_id}', f'Archive: {batch_id}')}</h1>",
            f"          <p>{self._i18n(f'共 {len(papers)} 篇论文', f'{len(papers)} papers in this batch')}</p>",
            "        </div>",
            f"        {self._lang_toggle_button()}",
            "      </header>",
            "      <main class='container'>",
        ]

        for topic_name, topic_papers in topic_groups.items():
            topic_label = topic_papers[0].topic.label if topic_papers else topic_name
            count = len(topic_papers)
            html_parts.append(f"        <section class='topic-section' id='{topic_name}'>")
            html_parts.append("          <div class='topic-header'>")
            html_parts.append(f"            <h2 class='topic-title'>{self._escape(topic_label)}</h2>")
            html_parts.append(f"            <span class='topic-count'>{count} {self._i18n('篇', 'papers')}</span>")
            html_parts.append("          </div>")
            html_parts.append("          <ul class='paper-list'>")

            for summary in topic_papers:
                url = f"../../topics/{topic_name}/{summary.paper.arxiv_id}.html"
                title = self._escape(summary.paper.title)
                score = self._format_score(summary.score_details)

                author_text = ""
                if summary.paper.authors:
                    author_preview = ", ".join(summary.paper.authors[:3])
                    if len(summary.paper.authors) > 3:
                        author_preview += ", ..."
                    author_text = self._escape(author_preview)

                html_parts.append("            <li class='paper-item'>")
                html_parts.append(f"              <a href='{self._escape(url)}'>{title}</a>")
                html_parts.append("              <div class='paper-meta'>")
                html_parts.append(f"                <span class='paper-meta-item'>📊 {self._i18n('相关度', 'Score')}: {score}</span>")
                if author_text:
                    html_parts.append(f"                <span class='paper-meta-item'>👤 {author_text}</span>")
                if summary.paper.published:
                    pub_date = summary.paper.published.strftime("%Y-%m-%d")
                    html_parts.append(f"                <span class='paper-meta-item'>📅 {pub_date}</span>")
                html_parts.append("              </div>")
                html_parts.append("            </li>")

            html_parts.append("          </ul>")
            html_parts.append("        </section>")

        html_parts.append("      </main>")
        html_parts.append(
            f"      <footer class='site-footer'>{self._i18n('由 CI-LLM4APR 自动生成', 'Generated by the CI-LLM4APR pipeline')}</footer>"
        )
        html_parts.append("    </div>")
        html_parts.append("  </div>")
        html_parts.append(self._language_script())
        html_parts.append("</body>")
        html_parts.append("</html>")
        return "\n".join(html_parts)

    def _render_archive_listing_html(self, batches: List[Dict[str, Any]]) -> str:
        """Render archive listing page showing all historical batches."""
        html_lang = self._html_lang(self.language)
        style_block = self._get_index_style()

        html_parts = [
            "<!DOCTYPE html>",
            f"<html lang='{html_lang}'>",
            "<head>",
            "  <meta charset='utf-8'>",
            "  <meta name='viewport' content='width=device-width, initial-scale=1'>",
            f"  <title>{self._escape('Archives - CI-LLM4APR')}</title>",
            f"  <style>{style_block}</style>",
            "</head>",
            f"<body data-lang='{self.language}'>",
            "  <div class='page'>",
            "    <aside class='sidebar'>",
            "      <div class='sidebar-header'>",
            f"        <a href='../' style='text-decoration:none;color:inherit;'><h1>{self._i18n('🏠 首页', '🏠 Home')}</h1></a>",
            "      </div>",
            "      <div class='sidebar-stats'>",
            f"        <h3>{self._i18n('归档统计', 'Archive Stats')}</h3>",
            "        <div class='sidebar-stat-item'>",
            f"          <span class='sidebar-stat-label'>{self._i18n('总批次', 'Total Batches')}</span>",
            f"          <span class='sidebar-stat-value'>{len(batches)}</span>",
            "        </div>",
            "      </div>",
            "    </aside>",
            "    <div class='main-content'>",
            "      <header class='top-bar'>",
            "        <div class='top-bar-title'>",
            f"          <h1>{self._i18n('📚 历史归档', '📚 Archives')}</h1>",
            f"          <p>{self._i18n('按周浏览历史论文', 'Browse papers by week')}</p>",
            "        </div>",
            f"        {self._lang_toggle_button()}",
            "      </header>",
            "      <main class='container'>",
        ]

        if not batches:
            html_parts.append(
                f"        <p>{self._i18n('暂无历史归档。', 'No archives yet.')}</p>"
            )
        else:
            html_parts.append("        <ul class='paper-list'>")
            for batch in batches:
                batch_id = batch.get("id", "unknown")
                paper_count = batch.get("paper_count", 0)
                generated = batch.get("generated", "")[:10]
                topics = batch.get("topics", [])
                topics_str = ", ".join(topics) if topics else "N/A"

                html_parts.append("          <li class='paper-item'>")
                html_parts.append(f"            <a href='{batch_id}/'>{self._i18n(f'批次 {batch_id}', f'Batch {batch_id}')}</a>")
                html_parts.append("            <div class='paper-meta'>")
                html_parts.append(f"              <span class='paper-meta-item'>📄 {paper_count} {self._i18n('篇论文', 'papers')}</span>")
                html_parts.append(f"              <span class='paper-meta-item'>📅 {generated}</span>")
                html_parts.append(f"              <span class='paper-meta-item'>🏷️ {self._escape(topics_str)}</span>")
                html_parts.append("            </div>")
                html_parts.append("          </li>")
            html_parts.append("        </ul>")

        html_parts.append("      </main>")
        html_parts.append(
            f"      <footer class='site-footer'>{self._i18n('由 CI-LLM4APR 自动生成', 'Generated by the CI-LLM4APR pipeline')}</footer>"
        )
        html_parts.append("    </div>")
        html_parts.append("  </div>")
        html_parts.append(self._language_script())
        html_parts.append("</body>")
        html_parts.append("</html>")
        return "\n".join(html_parts)

    def _get_index_style(self) -> str:
        """Return shared CSS styles for index pages."""
        return """
:root {
  --page-max-width: 1200px;
  --sidebar-width: 240px;
  --bg-color: #f8f9fa;
  --card-bg: #ffffff;
  --border-color: #e5e7eb;
  --accent: #2563eb;
  --accent-light: #eff6ff;
  --text-primary: #111827;
  --text-secondary: #6b7280;
  --text-muted: #9ca3af;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Helvetica', 'Arial', sans-serif;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { margin: 0; min-height: 100vh; background: var(--bg-color); color: var(--text-primary); line-height: 1.6; }
.page { min-height: 100vh; display: flex; }
.sidebar { width: var(--sidebar-width); background: var(--card-bg); border-right: 1px solid var(--border-color); position: fixed; left: 0; top: 0; bottom: 0; overflow-y: auto; z-index: 100; }
.sidebar-header { padding: 1.5rem 1.25rem; border-bottom: 1px solid var(--border-color); }
.sidebar-header h1 { font-size: 1.25rem; font-weight: 700; color: var(--text-primary); }
.sidebar-stats { padding: 1rem 1.25rem; border-bottom: 1px solid var(--border-color); }
.sidebar-stats h3 { font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); margin-bottom: 0.75rem; }
.sidebar-stat-item { display: flex; align-items: center; gap: 0.5rem; padding: 0.5rem 0; font-size: 0.875rem; }
.sidebar-stat-icon { font-size: 1rem; }
.sidebar-stat-label { color: var(--text-secondary); flex: 1; }
.sidebar-stat-value { font-weight: 600; color: var(--accent); }
.sidebar-nav { padding: 1rem 0; }
.sidebar-nav h3 { font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); padding: 0 1.25rem 0.5rem; }
.sidebar-nav ul { list-style: none; }
.sidebar-nav li { margin: 0.25rem 0; }
.sidebar-nav a { display: block; padding: 0.5rem 1.25rem; color: var(--text-secondary); text-decoration: none; font-size: 0.875rem; transition: all 0.2s; }
.sidebar-nav a:hover { background: var(--accent-light); color: var(--accent); }
.sidebar-archive-link { padding: 1rem 1.25rem; border-top: 1px solid var(--border-color); }
.sidebar-archive-link a { display: block; padding: 0.75rem 1rem; background: var(--accent-light); color: var(--accent); text-decoration: none; font-size: 0.875rem; font-weight: 600; border-radius: 6px; text-align: center; transition: all 0.2s; }
.sidebar-archive-link a:hover { background: var(--accent); color: white; }
.main-content { margin-left: var(--sidebar-width); flex: 1; display: flex; flex-direction: column; }
.top-bar { background: var(--card-bg); border-bottom: 1px solid var(--border-color); padding: 1.25rem 2rem; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 50; }
.top-bar-title { flex: 1; }
.top-bar-title h1 { font-size: 1.75rem; font-weight: 700; margin-bottom: 0.25rem; color: var(--text-primary); }
.top-bar-title p { font-size: 0.95rem; color: var(--text-secondary); margin: 0; }
.lang-toggle { border: 1px solid var(--border-color); background: var(--card-bg); color: var(--text-primary); border-radius: 6px; padding: 0.5rem 1rem; font-size: 0.875rem; font-weight: 500; cursor: pointer; transition: all 0.2s; flex-shrink: 0; }
.lang-toggle:hover { background: var(--accent-light); border-color: var(--accent); color: var(--accent); }
.container { flex: 1; padding: 1rem 1.5rem; max-width: var(--page-max-width); margin: 0 auto; width: 100%; }
.topic-section { margin-bottom: 2rem; scroll-margin-top: 4rem; }
.topic-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 1rem; padding-bottom: 0.75rem; border-bottom: 2px solid var(--border-color); }
.topic-title { font-size: 1.5rem; font-weight: 700; color: var(--text-primary); }
.topic-count { font-size: 0.875rem; color: var(--text-muted); background: var(--bg-color); padding: 0.25rem 0.75rem; border-radius: 12px; }
.paper-list { list-style: none; background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 8px; overflow: hidden; }
.paper-item { padding: 0.875rem 1.25rem; border-bottom: 1px solid var(--border-color); transition: background 0.2s; }
.paper-item:last-child { border-bottom: none; }
.paper-item:hover { background: var(--accent-light); }
.paper-item a { text-decoration: none; color: var(--text-primary); font-weight: 600; font-size: 1.05rem; line-height: 1.5; transition: color 0.2s; }
.paper-item a:hover { color: var(--accent); }
.paper-meta { display: flex; gap: 1.5rem; margin-top: 0.5rem; font-size: 0.875rem; color: var(--text-secondary); flex-wrap: wrap; }
.paper-meta-item { display: flex; align-items: center; gap: 0.375rem; }
.site-footer { text-align: center; padding: 2rem; color: var(--text-muted); font-size: 0.875rem; background: var(--card-bg); border-top: 1px solid var(--border-color); }
@media (max-width: 1024px) { .sidebar { transform: translateX(-100%); transition: transform 0.3s; } .main-content { margin-left: 0; } }
@media (max-width: 768px) { .top-bar { padding: 1rem; flex-direction: column; align-items: flex-start; gap: 1rem; } .container { padding: 1rem; } .paper-meta { flex-direction: column; gap: 0.25rem; } }
""".strip()

    def _render_paper(self, summary: PaperSummary) -> str:
        html_lang = self._html_lang(self.language)
        style_block = """
:root {
  --page-max-width: 1200px;
  --sidebar-width: 220px;
  --bg-color: #ffffff;
  --bg-secondary: #f8f9fa;
  --card-bg: #ffffff;
  --border-color: #e5e7eb;
  --accent: #2563eb;
  --accent-light: #eff6ff;
  --text-primary: #111827;
  --text-secondary: #6b7280;
  --text-muted: #9ca3af;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Helvetica', 'Arial', sans-serif;
}

* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  margin: 0;
  min-height: 100vh;
  background: var(--bg-color);
  color: var(--text-primary);
  line-height: 1.6;
}

.page {
  min-height: 100vh;
  display: flex;
}

/* Left Sidebar TOC */
.sidebar-toc {
  width: var(--sidebar-width);
  background: var(--bg-secondary);
  border-right: 1px solid var(--border-color);
  position: fixed;
  left: 0;
  top: 0;
  bottom: 0;
  overflow-y: auto;
  z-index: 100;
}

.toc-header {
  padding: 1.5rem 1rem;
  border-bottom: 1px solid var(--border-color);
}

.toc-header a {
  display: block;
  font-size: 1rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-primary);
  text-decoration: none;
  transition: color 0.2s;
}

.toc-header a:hover {
  color: var(--accent);
}

/* Paper Info in Sidebar */
.sidebar-paper-info {
  padding: 1rem;
  border-bottom: 1px solid var(--border-color);
}

.sidebar-paper-info h3 {
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  margin-bottom: 0.75rem;
}

/* Navigation Section Header */
.sidebar-nav-header {
  padding: 1rem 1rem 0.5rem;
}

.sidebar-nav-header h3 {
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  margin-bottom: 0;
}

.sidebar-info-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding: 0.5rem 0;
  font-size: 0.875rem;
  border-bottom: 1px solid var(--bg-secondary);
}

.sidebar-info-item:last-child {
  border-bottom: none;
}

.sidebar-info-label {
  color: var(--text-muted);
  font-weight: 500;
  font-size: 0.75rem;
}

.sidebar-info-value {
  color: var(--text-primary);
  font-weight: 600;
  word-break: break-word;
}

.sidebar-info-value a {
  color: var(--accent);
  text-decoration: none;
}

.sidebar-info-value a:hover {
  text-decoration: underline;
}

.toc-nav {
  padding: 1rem 0;
}

.toc-nav ul {
  list-style: none;
}

.toc-nav li {
  margin: 0.25rem 0;
}

.toc-nav a {
  display: block;
  padding: 0.5rem 1rem;
  color: var(--text-secondary);
  text-decoration: none;
  font-size: 0.875rem;
  transition: all 0.2s;
}

.toc-nav a:hover {
  background: var(--accent-light);
  color: var(--accent);
  border-left: 3px solid var(--accent);
}

/* Main Content */
.main-content {
  margin-left: var(--sidebar-width);
  flex: 1;
  display: flex;
  flex-direction: column;
}

.top-bar {
  background: var(--card-bg);
  border-bottom: 1px solid var(--border-color);
  padding: 1.25rem 2rem;
  position: sticky;
  top: 0;
  z-index: 50;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1.5rem;
}

.top-bar-title {
  flex: 1;
  min-width: 0;
}

.paper-title {
  font-size: 1.5rem;
  font-weight: 700;
  line-height: 1.4;
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.top-bar-actions {
  display: flex;
  gap: 0.75rem;
  align-items: center;
  flex-shrink: 0;
}

.back-link {
  color: var(--text-secondary);
  font-weight: 500;
  text-decoration: none;
  font-size: 0.875rem;
  transition: color 0.2s;
  white-space: nowrap;
  padding: 0.5rem 1rem;
  border: 1px solid var(--border-color);
  border-radius: 6px;
}

.back-link:hover {
  color: var(--accent);
  background: var(--accent-light);
  border-color: var(--accent);
}

.lang-toggle {
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  color: var(--text-primary);
  border-radius: 6px;
  padding: 0.5rem 1rem;
  font-size: 0.875rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
}

.lang-toggle:hover {
  background: var(--accent-light);
  border-color: var(--accent);
  color: var(--accent);
}

.container {
  flex: 1;
  max-width: var(--page-max-width);
  margin: 0 auto;
  padding: 1.5rem 2rem;
  width: 100%;
}

/* Paper Header - Flat Design */
.paper-header {
  margin-bottom: 1.5rem;
}

/* Flat Meta Bar - Simplified */
.meta-bar {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  padding: 1rem 0;
  border-bottom: 1px solid var(--border-color);
  margin-bottom: 1.5rem;
}

.meta-item {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  font-size: 0.875rem;
  padding: 0.5rem 0;
}

.meta-icon {
  font-size: 1.125rem;
  flex-shrink: 0;
  margin-top: 0.125rem;
}

.meta-label {
  color: var(--text-muted);
  font-weight: 500;
  flex-shrink: 0;
  min-width: 4rem;
}

.meta-value {
  color: var(--text-primary);
  font-weight: 600;
  word-break: break-word;
  line-height: 1.6;
  flex: 1;
}

.meta-value a {
  color: var(--accent);
  text-decoration: none;
}

.meta-value a:hover {
  text-decoration: underline;
}

/* Section Cards - Flat Design */
.section-card {
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 1.5rem;
  margin-bottom: 1.5rem;
  scroll-margin-top: 4rem;
}

.section-card h2 {
  margin: 0 0 1rem;
  font-size: 1.375rem;
  font-weight: 700;
  color: var(--text-primary);
}

.section-card h3 {
  font-size: 1.125rem;
  font-weight: 600;
  color: var(--text-secondary);
  margin: 1.5rem 0 0.75rem;
}

.section-card p {
  margin: 0 0 1rem;
  line-height: 1.7;
  color: var(--text-primary);
}

/* Brief Summary - Accent Background */
.brief-summary {
  background: var(--accent-light);
  border-left: 3px solid var(--accent);
}

/* Question Cards - Flat Design */
.todo-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.todo-list li {
  padding: 1.25rem;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  transition: all 0.2s;
}

.todo-list li:hover {
  border-color: var(--accent);
  background: var(--accent-light);
}

.todo-question {
  display: block;
  font-weight: 600;
  font-size: 1.05rem;
  margin-bottom: 0.5rem;
  color: var(--text-primary);
}

.todo-reason {
  color: var(--text-secondary);
  font-size: 0.875rem;
  line-height: 1.6;
}

/* Findings */
.finding {
  padding: 1.25rem 0;
  border-top: 1px solid var(--border-color);
}

.finding:first-of-type {
  padding-top: 0;
  border-top: none;
}

.finding h3 {
  margin: 0 0 0.75rem;
  font-size: 1.125rem;
  font-weight: 600;
  color: var(--text-primary);
}

.finding p {
  margin: 0 0 0.5rem;
  line-height: 1.75;
  color: var(--text-primary);
}

.finding .confidence {
  font-size: 0.875rem;
  color: var(--text-muted);
  font-weight: 500;
}

.site-footer {
  text-align: center;
  padding: 2rem;
  color: var(--text-muted);
  font-size: 0.875rem;
  background: var(--bg-secondary);
  border-top: 1px solid var(--border-color);
}

/* Responsive Design */
@media (max-width: 1024px) {
  .sidebar-toc {
    transform: translateX(-100%);
    transition: transform 0.3s;
  }
  
  .main-content {
    margin-left: 0;
  }
}

@media (max-width: 768px) {
  .top-bar {
    padding: 1rem;
  }

  .container {
    padding: 1rem;
  }

  .paper-title {
    font-size: 1.5rem;
  }
  
  .meta-bar {
    flex-direction: column;
    gap: 0.75rem;
  }
  
  .section-card {
    padding: 1.25rem;
  }
}

  .paper-card h1 {
    font-size: 1.6rem;
  }
}
""".strip()

        paper = summary.paper
        title = self._escape(paper.title)
        arxiv_link = self._escape(paper.arxiv_url)
        arxiv_id = self._escape(paper.arxiv_id)
        score = self._format_score(summary.score_details)
        brief_summary = (summary.brief_summary or "").strip()
        published_display = paper.published.strftime("%Y-%m-%d") if paper.published else ""

        if paper.authors:
            authors_display = self._escape(", ".join(paper.authors))
        else:
            authors_display = self._i18n("未知作者", "Unknown authors")

        # Sidebar info items (Topic, Score, Published, arXiv)
        sidebar_info_items: List[Tuple[str, str]] = [
            (
                self._i18n("所属专题", "Topic"),
                self._escape(summary.topic.label),
            ),
            (
                self._i18n("相关度得分", "Relevance Score"),
                score,
            ),
            (
                self._i18n("发表日期", "Published"),
                self._escape(published_display) if published_display else self._i18n("未知", "Unknown"),
            ),
            (
                "arXiv",
                f"<a href='{arxiv_link}' target='_blank' rel='noopener'>{arxiv_id}</a>",
            ),
        ]
        
        # Top bar meta items (only Authors and Comment)
        top_meta_items: List[Tuple[str, str, str]] = [
            (
                "👤",
                self._i18n("作者", "Authors"),
                authors_display,
            ),
        ]
        
        # Add comment if available
        if paper.comment:
            top_meta_items.append((
                "💬",
                self._i18n("备注", "Comment"),
                self._escape(paper.comment),
            ))

        # Build TOC navigation links
        toc_links = [
            (self._i18n('论文速览', 'Brief Summary'), '#brief-summary'),
            (self._i18n('核心内容', 'Core Content'), '#core-content'),
            (self._i18n('关心的问题', 'Questions'), '#questions'),
            (self._i18n('逐项解答', 'Findings'), '#findings'),
            (self._i18n('综合总结', 'Overview'), '#overview'),
        ]
        
        lines = [
            "<!DOCTYPE html>",
            f"<html lang='{html_lang}'>",
            "<head>",
            "  <meta charset='utf-8'>",
            "  <meta name='viewport' content='width=device-width, initial-scale=1'>",
            f"  <title>{title}</title>",
            f"  <style>{style_block}</style>",
            "</head>",
            f"<body data-lang='{self.language}'>",
            "  <div class='page'>",
            # Sidebar with Paper Info and TOC
            "    <aside class='sidebar-toc'>",
            "      <div class='toc-header'>",
            f"        <a href='../..'>{self._i18n('🏠 首页', '🏠 HOME')}</a>",
            "      </div>",
            # Paper info in sidebar (at top)
            "      <div class='sidebar-paper-info'>",
            f"        <h3>{self._i18n('论文信息', 'Paper Info')}</h3>",
        ]
        
        # Add sidebar info items
        for label_html, value_html in sidebar_info_items:
            lines.append("        <div class='sidebar-info-item'>")
            lines.append(f"          <span class='sidebar-info-label'>{label_html}</span>")
            lines.append(f"          <span class='sidebar-info-value'>{value_html}</span>")
            lines.append("        </div>")
        
        # Add TOC navigation section
        lines.append("      </div>")
        lines.append("      <div class='sidebar-nav-header'>")
        lines.append(f"        <h3>{self._i18n('目录导航', 'Navigation')}</h3>")
        lines.append("      </div>")
        lines.append("      <nav class='toc-nav'>")
        lines.append("        <ul>")
        
        for label, href in toc_links:
            lines.append(f"          <li><a href='{href}'>{label}</a></li>")
        
        lines.extend([
            "        </ul>",
            "      </nav>",
            "    </aside>",
            # Main content
            "    <div class='main-content'>",
            "      <header class='top-bar'>",
            "        <div class='top-bar-title'>",
            f"          <h1 class='paper-title'>{title}</h1>",
            "        </div>",
            "        <div class='top-bar-actions'>",
            f"          {self._lang_toggle_button()}",
            "        </div>",
            "      </header>",
            "      <main class='container'>",
            "        <div class='paper-header'>",
            # Simplified meta bar - only Authors and Comment
            "          <div class='meta-bar'>",
        ])
        
        # Add top meta items (Authors and Comment only)
        for icon, label_html, value_html in top_meta_items:
            lines.append("            <div class='meta-item'>")
            lines.append(f"              <span class='meta-icon'>{icon}</span>")
            lines.append(f"              <span class='meta-label'>{label_html}:</span>")
            lines.append(f"              <span class='meta-value'>{value_html}</span>")
            lines.append("            </div>")

        lines.append("          </div>")
        lines.append("        </div>")

        if brief_summary:
            lines.append("        <section class='section-card brief-summary' id='brief-summary'>")
            lines.append(f"          <h2>{self._i18n('论文速览', 'Brief Summary')}</h2>")
            for paragraph in [p.strip() for p in brief_summary.split("\n\n") if p.strip()]:
                escaped_paragraph = self._escape(paragraph).replace("\\n", "<br/>")
                lines.append(f"          <p>{escaped_paragraph}</p>")
            lines.append("        </section>")

        # Add core summary (5 aspects) if available
        if summary.core_summary:
            core = summary.core_summary
            lines.append("        <section class='section-card' id='core-content'>")
            lines.append(f"          <h2>{self._i18n('📖 论文核心内容', '📖 Core Content')}</h2>")
            
            aspects = [
                (self._i18n('1. 主要解决了什么问题？', '1. What problem does it solve?'), core.problem),
                (self._i18n('2. 提出了什么解决方案？', '2. What solution is proposed?'), core.solution),
                (self._i18n('3. 核心方法/步骤/策略', '3. Core methodology'), core.methodology),
                (self._i18n('4. 实验设计', '4. Experiment design'), core.experiments),
                (self._i18n('5. 结论', '5. Conclusion'), core.conclusion),
            ]
            
            for title_html, content in aspects:
                if content and content.strip():
                    escaped_content = self._escape(content).replace("\\n", "<br/>")
                    lines.append(f"          <h3 style='margin-top:1.2rem;color:var(--accent);font-size:1.05rem;'>{title_html}</h3>")
                    lines.append(f"          <p>{escaped_content}</p>")
            
            lines.append("        </section>")

        lines.extend(
            [
                "        <section class='section-card' id='questions'>",
                f"          <h2>{self._i18n('🤔 用户关心的问题', '🤔 Questions of Interest')}</h2>",
                "          <ul class='todo-list'>",
            ]
        )

        for task in summary.task_list:
            question = self._escape(task.question)
            reason = self._escape(task.reason)
            lines.append("            <li>")
            lines.append(f"              <span class='todo-question'>{question}</span>")
            lines.append(f"              <span class='todo-reason'>{reason}</span>")
            lines.append("            </li>")

        lines.extend(
            [
                "          </ul>",
                "        </section>",
                "        <section class='section-card' id='findings'>",
                f"          <h2>{self._i18n('💡 逐项解答', '💡 Detailed Findings')}</h2>",
            ]
        )

        for finding in summary.findings:
            q = self._escape(finding.task.question)
            answer = self._escape(finding.answer).replace("\n", "<br/>")
            confidence = f"{finding.confidence:.2f}"
            lines.append("          <div class='finding'>")
            lines.append(f"            <h3>{q}</h3>")
            lines.append(f"            <p>{answer}</p>")
            lines.append(
                f"            <p class='confidence'>{self._i18n('信心指数', 'Confidence')}: {confidence}</p>"
            )
            lines.append("          </div>")

        overview_html = self._escape(summary.overview).replace("\n", "<br/>")
        lines.extend(
            [
                "        </section>",
                "        <section class='section-card' id='overview'>",
                f"          <h2>{self._i18n('📝 综合总结', '📝 Overall Summary')}</h2>",
                f"          <p>{overview_html}</p>",
                "        </section>",
                "      </main>",
                f"      <footer class='site-footer'>{self._i18n('由 CI-LLM4APR 自动生成', 'Generated by the CI-LLM4APR pipeline')}</footer>",
                "    </div>",
                "  </div>",
                self._language_script(),
                "</body>",
                "</html>",
            ]
        )

        return "\n".join(lines)

    @staticmethod
    def _format_score(scored_paper) -> str:
        total_weight = sum(score.weight for score in scored_paper.scores) or 1.0
        value = sum(score.weight * score.value for score in scored_paper.scores)
        return f"{(value / total_weight) * 100:.1f}"
