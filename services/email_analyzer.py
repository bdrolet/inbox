"""
Email Analyzer with AI Integration
Analyzes emails to determine if action is needed and categorizes the type of action
"""

import os
import json
import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass
from enum import Enum
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from models import Email

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ActionType(Enum):
    """Types of actions that might be needed for an email.

    After analysis, messages can be moved to a top-level Outlook folder whose
    display name equals the enum value (e.g. reply_required). See EmailProcessor
    and GraphEmailClient.move_message_to_action_folder.
    """
    REPLY_REQUIRED = "reply_required"
    URGENT_ATTENTION = "urgent_attention"
    SCHEDULE_MEETING = "schedule_meeting"
    REVIEW_DOCUMENT = "review_document"
    APPROVE_REQUEST = "approve_request"
    FOLLOW_UP = "follow_up"
    ARCHIVE = "archive"
    NO_ACTION = "no_action"

@dataclass
class ActionRecommendation:
    """Recommendation for action on an email"""
    action_type: ActionType
    priority: int  # 1-5 scale, 5 being highest priority
    confidence: float  # 0.0-1.0 confidence score
    reasoning: str
    suggested_response: Optional[str] = None
    deadline: Optional[datetime] = None
    tags: List[str] = None

class EmailAnalyzer:
    """Analyzes emails using AI to determine required actions"""
    
    def __init__(self, openai_api_key: Optional[str] = None):
        """
        Initialize the email analyzer
        
        Args:
            openai_api_key: OpenAI API key. If not provided, will try to get from environment
        """
        raw_key = openai_api_key or os.getenv("OPENAI_API_KEY") or ""
        self.api_key = raw_key.strip()
        if not self.api_key:
            raise ValueError("OpenAI API key is required. Set OPENAI_API_KEY environment variable.")

        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.3,
            max_tokens=500,
            api_key=self.api_key,
            timeout=120.0,
            max_retries=3,
        )

        # Default context for AI analysis
        self.default_context = {
            "user_role": "Professional",
            "work_context": "General business communications",
            "priority_keywords": ["urgent", "asap", "deadline", "meeting", "approval", "review"],
            "action_threshold": 0.7  # Minimum confidence for action recommendation
        }
    
    def analyze_email(self, email: Email, context: Optional[Dict] = None) -> ActionRecommendation:
        """
        Analyze a single email to determine if action is needed
        
        Args:
            email: Email object to analyze
            context: Additional context for analysis
            
        Returns:
            ActionRecommendation object
        """
        try:
            # Merge provided context with default context
            analysis_context = {**self.default_context, **(context or {})}
            
            # Prepare email data for AI analysis
            email_data = self._prepare_email_data(email)
            
            # Get AI analysis
            ai_response = self._get_ai_analysis(email_data, analysis_context)
            
            # Parse AI response into ActionRecommendation
            recommendation = self._parse_ai_response(ai_response)
            
            logger.info(f"Analyzed email '{email.subject}' - Action: {recommendation.action_type.value}, Priority: {recommendation.priority}")
            
            return recommendation
            
        except Exception as e:
            logger.error(f"Error analyzing email: {str(e)}")
            # Return safe default recommendation
            return ActionRecommendation(
                action_type=ActionType.NO_ACTION,
                priority=1,
                confidence=0.0,
                reasoning=f"Analysis failed: {str(e)}"
            )
    
    def analyze_emails_batch(self, emails: List[Email], context: Optional[Dict] = None) -> List[ActionRecommendation]:
        """
        Analyze multiple emails in batch for efficiency
        
        Args:
            emails: List of Email objects to analyze
            context: Additional context for analysis
            
        Returns:
            List of ActionRecommendation objects
        """
        recommendations = []
        
        for email in emails:
            try:
                recommendation = self.analyze_email(email, context)
                recommendations.append(recommendation)
            except Exception as e:
                logger.error(f"Error analyzing email {email.id}: {str(e)}")
                # Add error recommendation
                recommendations.append(ActionRecommendation(
                    action_type=ActionType.NO_ACTION,
                    priority=1,
                    confidence=0.0,
                    reasoning=f"Analysis failed: {str(e)}"
                ))
        
        return recommendations
    
    def _prepare_email_data(self, email: Email) -> Dict:
        """Prepare email data for AI analysis"""
        return {
            "subject": email.subject,
            "from_name": email.from_name,
            "from_email": email.from_email,
            "to_recipients": email.to_display,
            "received_datetime": email.received_date,
            "body_preview": email.body_preview,
            "body_text": email.get_body_text(),
            "is_read": email.is_read,
            "has_attachments": email.has_attachments,
            "attachment_names": email.get_attachment_names()
        }
    
    def _get_ai_analysis(self, email_data: Dict, context: Dict) -> Dict:
        """Get AI analysis of the email"""
        
        # Create the prompt for AI analysis
        prompt = self._create_analysis_prompt(email_data, context)
        
        try:
            messages = [
                SystemMessage(content="You are an expert email assistant that analyzes emails to determine if action is needed. Respond with valid JSON only, no other text or markdown."),
                HumanMessage(content=prompt),
            ]
            response = self.llm.invoke(messages)

            content = self._stringify_ai_content(response.content).strip()
            if not content:
                meta = getattr(response, "response_metadata", {}) or {}
                logger.error(
                    "OpenAI API returned empty response (metadata=%s). "
                    "Check API key, model access, quota, and network.",
                    meta,
                )
                raise ValueError("Empty response from OpenAI API")

            # Strip markdown code block if present (e.g. ```json ... ```)
            if content.startswith("```"):
                lines = content.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                content = "\n".join(lines)

            return json.loads(content)

        except json.JSONDecodeError as e:
            logger.error(f"OpenAI API returned invalid JSON: {e}. Raw content: {content[:200]!r}")
            raise
        except Exception as e:
            logger.error(f"OpenAI API error: {str(e)}")
            raise
    
    def _create_analysis_prompt(self, email_data: Dict, context: Dict) -> str:
        """Create the prompt for AI analysis"""
        
        prompt = f"""
Analyze the following email and determine if any action is needed. Consider the context provided.

EMAIL DATA:
Subject: {email_data['subject']}
From: {email_data['from_name']} <{email_data['from_email']}>
To: {email_data['to_recipients']}
Received: {email_data['received_datetime']}
Read Status: {'Read' if email_data['is_read'] else 'Unread'}
Has Attachments: {'Yes' if email_data['has_attachments'] else 'No'}
Attachment Names: {', '.join(email_data['attachment_names']) if email_data['attachment_names'] else 'None'}

BODY PREVIEW:
{email_data['body_preview'][:500]}

FULL BODY TEXT:
{email_data['body_text'][:1000]}

CONTEXT:
User Role: {context.get('user_role', 'Professional')}
Work Context: {context.get('work_context', 'General business communications')}
Priority Keywords: {', '.join(context.get('priority_keywords', []))}

Please analyze this email and provide a JSON response with the following structure:
{{
    "action_type": "one of: reply_required, urgent_attention, schedule_meeting, review_document, approve_request, follow_up, archive, no_action",
    "priority": "integer from 1-5 (5 being highest priority)",
    "confidence": "float from 0.0-1.0 (confidence in the recommendation)",
    "reasoning": "brief explanation of why this action is recommended",
    "suggested_response": "if action_type is reply_required, provide a brief suggested response",
    "deadline": "if there's an implied deadline, provide it in ISO format, otherwise null",
    "tags": ["list", "of", "relevant", "tags"]
}}

Consider:
1. Is this email asking for something specific?
2. Does it require a response?
3. Is there urgency indicated?
4. Are there deadlines mentioned?
5. Does it require scheduling or approval?
6. Is it informational only?

Respond with valid JSON only.
"""
        
        return prompt

    @staticmethod
    def _stringify_ai_content(content) -> str:
        """Normalize AIMessage.content (str or multimodal list) to a single string."""
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    if block.get("type") == "text" and "text" in block:
                        parts.append(str(block["text"]))
                    elif "text" in block:
                        parts.append(str(block["text"]))
                else:
                    parts.append(str(block))
            return "".join(parts)
        return str(content)

    def _parse_ai_response(self, ai_response: Dict) -> ActionRecommendation:
        """Parse AI response into ActionRecommendation object"""
        
        try:
            # Map string action type to enum
            action_type_str = ai_response.get('action_type', 'no_action')
            action_type = ActionType(action_type_str)
            
            # Parse deadline if provided
            deadline = None
            if ai_response.get('deadline'):
                try:
                    deadline = datetime.fromisoformat(ai_response['deadline'].replace('Z', '+00:00'))
                except:
                    pass
            
            return ActionRecommendation(
                action_type=action_type,
                priority=int(ai_response.get('priority', 1)),
                confidence=float(ai_response.get('confidence', 0.0)),
                reasoning=ai_response.get('reasoning', 'No reasoning provided'),
                suggested_response=ai_response.get('suggested_response'),
                deadline=deadline,
                tags=ai_response.get('tags', [])
            )
            
        except Exception as e:
            logger.error(f"Error parsing AI response: {str(e)}")
            # Return safe default
            return ActionRecommendation(
                action_type=ActionType.NO_ACTION,
                priority=1,
                confidence=0.0,
                reasoning=f"Failed to parse AI response: {str(e)}"
            )
    
    def get_action_summary(self, recommendations: List[ActionRecommendation]) -> Dict:
        """
        Get a summary of all action recommendations
        
        Args:
            recommendations: List of ActionRecommendation objects
            
        Returns:
            Dictionary with summary statistics
        """
        if not recommendations:
            return {"total": 0, "by_action_type": {}, "by_priority": {}}
        
        summary = {
            "total": len(recommendations),
            "by_action_type": {},
            "by_priority": {},
            "high_priority_count": 0,
            "urgent_count": 0
        }
        
        for rec in recommendations:
            # Count by action type
            action_type = rec.action_type.value
            summary["by_action_type"][action_type] = summary["by_action_type"].get(action_type, 0) + 1
            
            # Count by priority
            priority = rec.priority
            summary["by_priority"][priority] = summary["by_priority"].get(priority, 0) + 1
            
            # Count high priority (4-5) and urgent
            if priority >= 4:
                summary["high_priority_count"] += 1
            if rec.action_type == ActionType.URGENT_ATTENTION:
                summary["urgent_count"] += 1
        
        return summary
