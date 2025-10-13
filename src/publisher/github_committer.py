"""GitHub committer for pushing paper metadata to a repository."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import List

try:
	from github import Github, GithubException
except ImportError:
	Github = None
	GithubException = Exception


class GitHubConfig:
	"""Configuration for GitHub repository."""
	
	def __init__(
		self,
		token: str,
		repo_name: str,
		branch: str = "updates",
		file_path: str = "update.md",
	):
		self.token = token
		self.repo_name = repo_name
		self.branch = branch
		self.file_path = file_path


class PaperMetadata:
	"""Metadata for a single paper."""
	
	def __init__(
		self,
		title: str,
		published_date: str,
		venue: str,
		arxiv_id: str = "",
		arxiv_url: str = "",
	):
		self.title = title
		self.published_date = published_date
		self.venue = venue
		self.arxiv_id = arxiv_id
		self.arxiv_url = arxiv_url
	
	def to_markdown_row(self) -> str:
		"""Convert to markdown table row."""
		# Format: | Title | Published Date | Venue/Conference |
		# Remove newlines and extra whitespace from all fields to ensure single-line display
		cleaned_title = ' '.join(self.title.split())
		cleaned_venue = ' '.join(self.venue.split())
		title_with_link = f"[{cleaned_title}]({self.arxiv_url})" if self.arxiv_url else cleaned_title
		return f"| {title_with_link} | {self.published_date} | {cleaned_venue} |"


class GitHubCommitter:
	"""Push paper metadata to GitHub repository."""
	
	def __init__(self, config: GitHubConfig):
		self.config = config
		
		if Github is None:
			raise ImportError(
				"PyGithub is required for GitHub integration. "
				"Install it with: pip install PyGithub"
			)
		
		self.github = Github(config.token)
		self.repo = None
	
	def _get_or_create_branch(self):
		"""Get or create the target branch."""
		try:
			self.repo = self.github.get_repo(self.config.repo_name)
		except GithubException as e:
			raise ValueError(f"Failed to access repository {self.config.repo_name}: {e}")
		
		# Check if branch exists
		try:
			self.repo.get_branch(self.config.branch)
			print(f"[INFO] Branch '{self.config.branch}' already exists.")
		except GithubException:
			# Branch doesn't exist, create it from default branch
			default_branch = self.repo.default_branch
			source_branch = self.repo.get_branch(default_branch)
			print(f"[INFO] Creating branch '{self.config.branch}' from '{default_branch}'")
			try:
				self.repo.create_git_ref(
					ref=f"refs/heads/{self.config.branch}",
					sha=source_branch.commit.sha
				)
				print(f"[INFO] Branch '{self.config.branch}' created successfully.")
			except GithubException as e:
				raise ValueError(f"Failed to create branch {self.config.branch}: {e}")
	
	def _get_existing_content(self) -> str:
		"""Get existing content from the file, or return empty string."""
		try:
			file_content = self.repo.get_contents(
				self.config.file_path,
				ref=self.config.branch
			)
			return file_content.decoded_content.decode('utf-8')
		except GithubException:
			# File doesn't exist yet
			print(f"[INFO] File '{self.config.file_path}' does not exist, will create new one.")
			return ""
	
	def _build_markdown_table(self, papers: List[PaperMetadata], existing_content: str) -> str:
		"""Build or update the markdown table."""
		table_header = "| Title | Published Date | Venue/Conference |\n| --- | --- | --- |"
		
		# Parse existing content to avoid duplicates
		existing_titles = set()
		if existing_content:
			lines = existing_content.split('\n')
			for line in lines:
				if line.startswith('| [') or (line.startswith('| ') and not line.startswith('| Title') and not line.startswith('| ---')):
					# Extract title from markdown link or plain text
					parts = line.split('|')
					if len(parts) >= 2:
						title_part = parts[1].strip()
						# Extract title from [title](url) format
						if title_part.startswith('['):
							title = title_part.split(']')[0][1:]
							existing_titles.add(title)
						else:
							existing_titles.add(title_part)
		
		# Build new rows for papers not already in the table
		new_rows = []
		for paper in papers:
			if paper.title not in existing_titles:
				new_rows.append(paper.to_markdown_row())
		
		if not new_rows:
			print("[INFO] No new papers to add (all already exist in the table).")
			return existing_content
		
		# If existing content has table, insert new papers at the beginning (after header)
		if table_header in existing_content or '| Title |' in existing_content:
			# Find the table header separator line (| --- | --- | --- |) and insert new rows after it
			lines = existing_content.split('\n')
			result_lines = []
			inserted = False
			
			for i, line in enumerate(lines):
				result_lines.append(line)
				# Find the separator line (| --- | --- | --- |) and insert new rows after it
				if not inserted and line.startswith('| ---') and '---' in line:
					# Insert new papers right after the separator (newest first)
					result_lines.extend(new_rows)
					inserted = True
			
			if not inserted:
				# Fallback: if we couldn't find separator, append at the end
				result_lines.extend(new_rows)
			
			return '\n'.join(result_lines)
		else:
			# No table yet, create new one
			timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
			content_lines = [
				f"# Paper Updates",
				f"",
				f"*Last updated: {timestamp}*",
				f"",
				table_header
			]
			content_lines.extend(new_rows)
			
			if existing_content:
				content_lines.append("")
				content_lines.append("---")
				content_lines.append("")
				content_lines.append(existing_content)
			
			return '\n'.join(content_lines)
	
	def commit_papers(self, papers: List[PaperMetadata], commit_message: str = None) -> bool:
		"""
		Commit paper metadata to the repository.
		
		Args:
			papers: List of paper metadata to commit
			commit_message: Optional custom commit message
			
		Returns:
			True if successful, False otherwise
		"""
		if not papers:
			print("[INFO] No papers to commit.")
			return True
		
		# Get or create branch
		self._get_or_create_branch()
		
		# Get existing content
		existing_content = self._get_existing_content()
		
		# Build updated content
		new_content = self._build_markdown_table(papers, existing_content)
		
		if new_content == existing_content:
			print("[INFO] No changes to commit.")
			return True
		
		# Prepare commit message
		if commit_message is None:
			timestamp = datetime.utcnow().strftime("%Y-%m-%d")
			commit_message = f"Update papers - {timestamp} (added {len(papers)} papers)"
		
		try:
			# Check if file exists
			try:
				file_content = self.repo.get_contents(
					self.config.file_path,
					ref=self.config.branch
				)
				# Update existing file
				self.repo.update_file(
					path=self.config.file_path,
					message=commit_message,
					content=new_content,
					sha=file_content.sha,
					branch=self.config.branch
				)
				print(f"[INFO] Successfully updated {self.config.file_path} in branch {self.config.branch}")
			except GithubException:
				# Create new file
				self.repo.create_file(
					path=self.config.file_path,
					message=commit_message,
					content=new_content,
					branch=self.config.branch
				)
				print(f"[INFO] Successfully created {self.config.file_path} in branch {self.config.branch}")
			
			return True
			
		except GithubException as e:
			print(f"[ERROR] Failed to commit to GitHub: {e}")
			return False
