"""Extract publication venue metadata from arXiv papers using LLM."""

from __future__ import annotations

import json
import re
from typing import Optional, Tuple

from core.models import OpenAIConfig, PaperCandidate

try:
	from openai import OpenAI
except ImportError:
	OpenAI = None


class MetadataExtractor:
	"""Extract publication venue from arXiv paper comments using LLM."""
	
	def __init__(self, openai_config: OpenAIConfig, mode: str = "offline"):
		self.openai_config = openai_config
		self.mode = mode
		
		if mode == "online" and openai_config.api_key and OpenAI is not None:
			self._client = OpenAI(
				api_key=openai_config.api_key,
				base_url=openai_config.base_url or None,
			)
		else:
			self._client = None
	
	def extract_venue(self, paper: PaperCandidate) -> Tuple[str, str]:
		"""
		Extract publication venue from paper metadata.
		
		Args:
			paper: The paper candidate with metadata
			
		Returns:
			Tuple of (published_date, venue)
			- published_date: formatted publication date (YYYY-MM-DD)
			- venue: publication venue (conference/journal name or "arXiv")
		"""
		# Format published date
		published_date = paper.published.strftime("%Y-%m-%d")
		
		# Try to extract venue from comment field if available
		venue = "arXiv"
		
		# Check if paper has a comment field (usually in the raw metadata)
		comment = getattr(paper, 'comment', None)
		
		if comment and self._client is not None:
			# Use LLM to extract venue information
			try:
				extracted_venue = self._extract_venue_with_llm(comment)
				if extracted_venue:
					venue = extracted_venue
			except Exception as e:
				print(f"[WARN] Failed to extract venue with LLM for {paper.arxiv_id}: {e}")
				# Fallback to simple extraction
				venue = self._extract_venue_simple(comment)
		elif comment:
			# No LLM available, use simple extraction
			venue = self._extract_venue_simple(comment)
		
		return published_date, venue
	
	def _extract_venue_with_llm(self, comment: str) -> Optional[str]:
		"""Use LLM to extract venue from comment."""
		if not self._client:
			return None
		
		# Prepare prompt
		prompt = f"""You are an AI assistant that extracts publication venue information from arXiv paper comments.

Given the following comment from an arXiv paper, extract the publication venue (conference or journal name) if mentioned.
If no venue is mentioned, return "arXiv".

Comment: {comment}

Please respond with ONLY the venue name (e.g., "ICSE 2024", "NeurIPS 2023", "IEEE Transactions on Software Engineering", etc.).
If no venue is found, respond with exactly "arXiv".

Venue:"""
		
		try:
			response = self._client.chat.completions.create(
				model=self.openai_config.relevance_model,  # Use lightweight model
				messages=[
					{"role": "system", "content": "You are a helpful assistant that extracts publication venue information."},
					{"role": "user", "content": prompt}
				],
				temperature=0.0,  # Use deterministic output
				max_tokens=100,
			)
			
			venue = response.choices[0].message.content.strip()
			
			# Clean up the response
			venue = venue.replace('"', '').replace("'", '').strip()
			
			# If LLM returns empty or just says arXiv, return arXiv
			if not venue or venue.lower() == "arxiv":
				return "arXiv"
			
			return venue
			
		except Exception as e:
			print(f"[WARN] LLM venue extraction failed: {e}")
			return None
	
	def _extract_venue_simple(self, comment: str) -> str:
		"""Simple rule-based venue extraction from comment."""
		if not comment:
			return "arXiv"
		
		comment_lower = comment.lower()
		
		# Common conference patterns
		conferences = [
			r'icse\s*\d{4}',
			r'fse\s*\d{4}',
			r'ase\s*\d{4}',
			r'issta\s*\d{4}',
			r'neurips\s*\d{4}',
			r'iclr\s*\d{4}',
			r'icml\s*\d{4}',
			r'cvpr\s*\d{4}',
			r'iccv\s*\d{4}',
			r'eccv\s*\d{4}',
			r'acl\s*\d{4}',
			r'emnlp\s*\d{4}',
			r'naacl\s*\d{4}',
			r'sigmod\s*\d{4}',
			r'vldb\s*\d{4}',
			r'kdd\s*\d{4}',
			r'www\s*\d{4}',
			r'chi\s*\d{4}',
			r'uist\s*\d{4}',
		]
		
		# Check for conference patterns
		for pattern in conferences:
			match = re.search(pattern, comment_lower)
			if match:
				return match.group(0).upper()
		
		# Common journal patterns
		journal_keywords = [
			'ieee transactions on software engineering',
			'acm transactions on software engineering',
			'ieee transactions on pattern analysis and machine intelligence',
			'ieee software',
			'journal of systems and software',
			'empirical software engineering',
			'software testing',
			'nature',
			'science',
		]
		
		for keyword in journal_keywords:
			if keyword in comment_lower:
				# Return properly formatted journal name
				return keyword.title()
		
		# Check for generic "accepted" or "published" keywords
		if any(word in comment_lower for word in ['accepted', 'published', 'appear', 'conference', 'journal']):
			# Try to extract the venue name after these keywords
			patterns = [
				r'accepted (?:to|at|by|for)\s+([A-Z][A-Za-z0-9\s]+?)(?:\d{4}|,|\.|$)',
				r'published (?:in|at)\s+([A-Z][A-Za-z0-9\s]+?)(?:\d{4}|,|\.|$)',
				r'appear (?:in|at)\s+([A-Z][A-Za-z0-9\s]+?)(?:\d{4}|,|\.|$)',
			]
			
			for pattern in patterns:
				match = re.search(pattern, comment)
				if match:
					venue = match.group(1).strip()
					if len(venue) > 3:  # Avoid single letters
						return venue
		
		# Default to arXiv if nothing found
		return "arXiv"
