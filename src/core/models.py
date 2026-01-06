"""Core datamodels used across the CI-LLM4APR pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


# ---------------------------------------------------------------------------
# Configuration dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RelevanceDimension:
	"""Single scoring dimension for relevance evaluation."""

	name: str
	weight: float
	description: Optional[str] = None


@dataclass
class TopicQuery:
	"""Query definition for fetching arXiv papers."""

	categories: List[str] = field(default_factory=list)
	include: List[str] = field(default_factory=list)
	exclude: List[str] = field(default_factory=list)


@dataclass
class TopicConfig:
	"""Topic level configuration."""

	name: str
	label: str
	query: TopicQuery
	interest_prompt: str


@dataclass
class OpenAIConfig:
	api_key: Optional[str]
	base_url: Optional[str]
	relevance_model: str
	summarization_model: str
	temperature: float = 0.2
	language: str = "zh-CN"  # Output language: 'zh-CN' or 'en'


@dataclass
class FetchConfig:
	max_papers_per_topic: int
	days_back: int
	request_delay: float = 1.0


@dataclass
class RelevanceConfig:
	dimensions: List[RelevanceDimension]
	pass_threshold: float


@dataclass
class SummarizationConfig:
	task_list_size: int
	max_sections: int


@dataclass
class SiteConfig:
	output_dir: str
	base_url: str


@dataclass
class EmailConfig:
	enabled: bool = False
	sender: Optional[str] = None
	recipients: List[str] = field(default_factory=list)
	subject_template: str = "Weekly Paper Radar - {run_date}"
	smtp_host: Optional[str] = None
	smtp_port: int = 587
	username: Optional[str] = None
	password: Optional[str] = None
	use_tls: bool = True
	use_ssl: bool = False
	timeout: int = 30


@dataclass
class GitHubConfig:
	"""Configuration for GitHub repository integration."""
	enabled: bool = False
	token: Optional[str] = None
	repo_name: Optional[str] = None
	branch: str = "updates"
	file_path: str = "update.md"


@dataclass
class RuntimeConfig:
	mode: str = "offline"
	paper_limit: Optional[int] = None


@dataclass
class PipelineConfig:
	openai: OpenAIConfig
	fetch: FetchConfig
	topics: List[TopicConfig]
	relevance: RelevanceConfig
	summarization: SummarizationConfig
	site: SiteConfig
	email: EmailConfig
	github: GitHubConfig
	runtime: RuntimeConfig

	@staticmethod
	def from_dict(payload: Dict[str, Any]) -> "PipelineConfig":
		topics = [
			TopicConfig(
				name=item["name"],
				label=item.get("label", item["name"].title()),
				query=TopicQuery(
					categories=list(item.get("query", {}).get("categories", [])),
					include=list(item.get("query", {}).get("include", [])),
					exclude=list(item.get("query", {}).get("exclude", [])),
				),
				interest_prompt=item.get("interest_prompt", ""),
			)
			for item in payload.get("topics", [])
		]

		dimensions = [
			RelevanceDimension(
				name=d["name"],
				weight=float(d.get("weight", 0.0)),
				description=d.get("description"),
			)
			for d in payload.get("relevance", {}).get("scoring_dimensions", [])
		]

		openai_section = payload.get("openai", {})
		fetch_section = payload.get("fetch", {})
		relevance_section = payload.get("relevance", {})
		summarization_section = payload.get("summarization", {})
		site_section = payload.get("site", {})
		email_section = payload.get("email", {})
		github_section = payload.get("github", {})
		runtime_section = payload.get("runtime", {})

		return PipelineConfig(
			openai=OpenAIConfig(
				api_key=openai_section.get("api_key"),
				base_url=openai_section.get("base_url") or None,
				relevance_model=openai_section.get("relevance_model", "gpt-4o-mini"),
				summarization_model=openai_section.get("summarization_model", "gpt-4o-mini"),
				temperature=float(openai_section.get("temperature", 0.2)),
				language=openai_section.get("language", "zh-CN"),
			),
			fetch=FetchConfig(
				max_papers_per_topic=int(fetch_section.get("max_papers_per_topic", 25)),
				days_back=int(fetch_section.get("days_back", 7)),
				request_delay=float(fetch_section.get("request_delay", 1.0)),
			),
			topics=topics,
			relevance=RelevanceConfig(
				dimensions=dimensions,
				pass_threshold=float(relevance_section.get("pass_threshold", 60.0)),
			),
			summarization=SummarizationConfig(
				task_list_size=int(summarization_section.get("task_list_size", 5)),
				max_sections=int(summarization_section.get("max_sections", 4)),
			),
			site=SiteConfig(
				output_dir=site_section.get("output_dir", "site"),
				base_url=site_section.get("base_url", ""),
			),
			email=EmailConfig(
				enabled=bool(email_section.get("enabled", False)),
				sender=email_section.get("sender"),
				recipients=list(email_section.get("recipients", [])),
				subject_template=email_section.get("subject_template", "Weekly Paper Radar - {run_date}"),
				smtp_host=email_section.get("smtp_host"),
				smtp_port=int(email_section.get("smtp_port", 587)),
				username=email_section.get("username"),
				password=email_section.get("password"),
				use_tls=bool(email_section.get("use_tls", True)),
				use_ssl=bool(email_section.get("use_ssl", False)),
				timeout=int(email_section.get("timeout", 30)),
			),
			github=GitHubConfig(
				enabled=bool(github_section.get("enabled", False)),
				token=github_section.get("token"),
				repo_name=github_section.get("repo_name"),
				branch=github_section.get("branch", "updates"),
				file_path=github_section.get("file_path", "update.md"),
			),
			runtime=RuntimeConfig(
				mode=runtime_section.get("mode", "offline"),
				paper_limit=runtime_section.get("paper_limit"),
			),
		)


# ---------------------------------------------------------------------------
# Runtime data models
# ---------------------------------------------------------------------------


@dataclass
class PaperCandidate:
	topic: TopicConfig
	arxiv_id: str
	title: str
	abstract: str
	authors: List[str]
	categories: List[str]
	published: datetime
	updated: datetime
	arxiv_url: str
	pdf_url: Optional[str] = None
	affiliations: List[str] = field(default_factory=list)
	comment: Optional[str] = None

	def to_dict(self) -> Dict[str, Any]:
		return {
			"topic_name": self.topic.name,
			"arxiv_id": self.arxiv_id,
			"title": self.title,
			"abstract": self.abstract,
			"authors": self.authors,
			"categories": self.categories,
			"published": self.published.isoformat() if self.published else None,
			"updated": self.updated.isoformat() if self.updated else None,
			"arxiv_url": self.arxiv_url,
			"pdf_url": self.pdf_url,
			"affiliations": self.affiliations,
			"comment": self.comment,
		}

	@staticmethod
	def from_dict(data: Dict[str, Any], topic: TopicConfig) -> "PaperCandidate":
		return PaperCandidate(
			topic=topic,
			arxiv_id=data["arxiv_id"],
			title=data["title"],
			abstract=data.get("abstract", ""),
			authors=data.get("authors", []),
			categories=data.get("categories", []),
			published=datetime.fromisoformat(data["published"]) if data.get("published") else datetime.utcnow(),
			updated=datetime.fromisoformat(data["updated"]) if data.get("updated") else datetime.utcnow(),
			arxiv_url=data.get("arxiv_url", ""),
			pdf_url=data.get("pdf_url"),
			affiliations=data.get("affiliations", []),
			comment=data.get("comment"),
		)


@dataclass
class DimensionScore:
	name: str
	weight: float
	value: float

	def to_dict(self) -> Dict[str, Any]:
		return {"name": self.name, "weight": self.weight, "value": self.value}

	@staticmethod
	def from_dict(data: Dict[str, Any]) -> "DimensionScore":
		return DimensionScore(
			name=data["name"],
			weight=float(data["weight"]),
			value=float(data["value"]),
		)


@dataclass
class ScoredPaper:
	paper: PaperCandidate
	scores: List[DimensionScore]
	total_score: float

	def to_dict(self) -> Dict[str, Any]:
		return {
			"arxiv_id": self.paper.arxiv_id,
			"title": self.paper.title,
			"total_score": self.total_score,
			"scores": [score.to_dict() for score in self.scores],
		}

	@staticmethod
	def from_dict(data: Dict[str, Any], paper: PaperCandidate) -> "ScoredPaper":
		scores = [DimensionScore.from_dict(s) for s in data.get("scores", [])]
		return ScoredPaper(
			paper=paper,
			scores=scores,
			total_score=float(data.get("total_score", 0.0)),
		)


@dataclass
class TaskItem:
	question: str
	reason: str

	def to_dict(self) -> Dict[str, Any]:
		return {"question": self.question, "reason": self.reason}

	@staticmethod
	def from_dict(data: Dict[str, Any]) -> "TaskItem":
		return TaskItem(question=data["question"], reason=data["reason"])


@dataclass
class TaskFinding:
	task: TaskItem
	answer: str
	confidence: float

	def to_dict(self) -> Dict[str, Any]:
		return {
			"task": self.task.to_dict(),
			"answer": self.answer,
			"confidence": self.confidence,
		}

	@staticmethod
	def from_dict(data: Dict[str, Any]) -> "TaskFinding":
		return TaskFinding(
			task=TaskItem.from_dict(data["task"]),
			answer=data["answer"],
			confidence=float(data["confidence"]),
		)


@dataclass
class CoreSummary:
	"""Five-aspect core summary of a paper."""
	problem: str  # What problem does it solve
	solution: str  # What solution is proposed
	methodology: str  # Core methodology/steps/strategy
	experiments: str  # Experimental design, Metrics, baseline, dataset
	conclusion: str  # Conclusion

	def to_dict(self) -> Dict[str, Any]:
		return {
			"problem": self.problem,
			"solution": self.solution,
			"methodology": self.methodology,
			"experiments": self.experiments,
			"conclusion": self.conclusion,
		}

	@staticmethod
	def from_dict(data: Dict[str, Any]) -> "CoreSummary":
		return CoreSummary(
			problem=data.get("problem", ""),
			solution=data.get("solution", ""),
			methodology=data.get("methodology", ""),
			experiments=data.get("experiments", ""),
			conclusion=data.get("conclusion", ""),
		)


@dataclass
class PaperSummary:
	paper: PaperCandidate
	topic: TopicConfig
	core_summary: CoreSummary  # Five-aspect summary
	task_list: List[TaskItem]
	findings: List[TaskFinding]
	overview: str
	score_details: ScoredPaper
	markdown: str
	brief_summary: str = ""  # 1-2 paragraph narrative summary (Why? What? How?)

	def to_dict(self) -> Dict[str, Any]:
		"""Serialize PaperSummary to dictionary for JSON storage."""
		return {
			"paper": self.paper.to_dict(),
			"topic": {"name": self.topic.name, "label": self.topic.label},
			"core_summary": self.core_summary.to_dict() if self.core_summary else None,
			"task_list": [task.to_dict() for task in self.task_list],
			"findings": [finding.to_dict() for finding in self.findings],
			"overview": self.overview,
			"brief_summary": self.brief_summary,
			"score_details": self.score_details.to_dict(),
		}

	@staticmethod
	def from_dict(data: Dict[str, Any], topic_config: TopicConfig) -> "PaperSummary":
		"""Deserialize PaperSummary from dictionary."""
		paper = PaperCandidate.from_dict(data["paper"], topic_config)

		core_summary = None
		if data.get("core_summary"):
			core_summary = CoreSummary.from_dict(data["core_summary"])

		task_list = [TaskItem.from_dict(t) for t in data.get("task_list", [])]
		findings = [TaskFinding.from_dict(f) for f in data.get("findings", [])]
		score_details = ScoredPaper.from_dict(data.get("score_details", {}), paper)

		return PaperSummary(
			paper=paper,
			topic=topic_config,
			core_summary=core_summary,
			task_list=task_list,
			findings=findings,
			overview=data.get("overview", ""),
			score_details=score_details,
			markdown="",  # Regenerate when needed
			brief_summary=data.get("brief_summary", ""),
		)


@dataclass
class PipelineStats:
	start_time: datetime
	end_time: datetime
	topics_processed: int
	papers_fetched: int
	papers_selected: int


@dataclass
class PipelineResult:
	summaries: List[PaperSummary]
	stats: PipelineStats


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def limit_items(items: Iterable[Any], limit: Optional[int]) -> List[Any]:
	"""Return at most `limit` items from the iterable."""

	materialised = list(items)
	if limit is None:
		return materialised
	return materialised[:limit]
