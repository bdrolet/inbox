"""
Email Processor - Main orchestrator for email analysis workflow
Combines email retrieval, analysis, and action recommendations
"""

import os
import json
import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict

from clients.azure import GraphEmailClient
from models import Email
from services.email_analyzer import EmailAnalyzer, ActionRecommendation, ActionType

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# --- Context Manager (user profile, email patterns, analysis context) ---

@dataclass
class UserProfile:
    """User profile information for context"""
    name: str
    role: str
    department: str
    manager_email: Optional[str] = None
    team_members: List[str] = None
    current_projects: List[str] = None
    working_hours: Dict[str, str] = None  # {"start": "09:00", "end": "17:00"}
    timezone: str = "UTC"

    def __post_init__(self):
        if self.team_members is None:
            self.team_members = []
        if self.current_projects is None:
            self.current_projects = []
        if self.working_hours is None:
            self.working_hours = {"start": "09:00", "end": "17:00"}


@dataclass
class EmailPatterns:
    """Patterns for identifying different types of emails"""
    urgent_keywords: List[str] = None
    meeting_keywords: List[str] = None
    approval_keywords: List[str] = None
    follow_up_keywords: List[str] = None
    spam_indicators: List[str] = None
    important_senders: List[str] = None

    def __post_init__(self):
        if self.urgent_keywords is None:
            self.urgent_keywords = ["urgent", "asap", "immediately", "deadline", "critical", "emergency"]
        if self.meeting_keywords is None:
            self.meeting_keywords = ["meeting", "call", "conference", "schedule", "calendar", "appointment"]
        if self.approval_keywords is None:
            self.approval_keywords = ["approve", "approval", "sign", "authorize", "confirm", "ok"]
        if self.follow_up_keywords is None:
            self.follow_up_keywords = ["follow up", "follow-up", "reminder", "checking in", "status update"]
        if self.spam_indicators is None:
            self.spam_indicators = ["unsubscribe", "click here", "limited time", "act now", "free money"]
        if self.important_senders is None:
            self.important_senders = []


class ContextManager:
    """Manages context for email analysis"""

    def __init__(self, config_file: Optional[str] = None):
        """
        Initialize the context manager

        Args:
            config_file: Path to JSON configuration file
        """
        self.config_file = config_file or "email_context.json"
        self.user_profile = None
        self.email_patterns = EmailPatterns()
        self.custom_rules = {}
        self.load_context()

    def load_context(self):
        """Load context from configuration file"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)

                # Load user profile
                if 'user_profile' in config:
                    self.user_profile = UserProfile(**config['user_profile'])

                # Load email patterns
                if 'email_patterns' in config:
                    self.email_patterns = EmailPatterns(**config['email_patterns'])

                # Load custom rules
                if 'custom_rules' in config:
                    self.custom_rules = config['custom_rules']

                logger.info(f"Loaded context from {self.config_file}")

            except Exception as e:
                logger.error(f"Error loading context file: {str(e)}")
                self._create_default_context()
        else:
            logger.info("No context file found, creating default context")
            self._create_default_context()

    def save_context(self):
        """Save current context to configuration file"""
        try:
            config = {
                'user_profile': asdict(self.user_profile) if self.user_profile else None,
                'email_patterns': asdict(self.email_patterns),
                'custom_rules': self.custom_rules,
                'last_updated': datetime.now().isoformat()
            }

            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)

            logger.info(f"Saved context to {self.config_file}")

        except Exception as e:
            logger.error(f"Error saving context file: {str(e)}")

    def _create_default_context(self):
        """Create default context when no file exists"""
        self.user_profile = UserProfile(
            name="User",
            role="Professional",
            department="General"
        )
        self.save_context()

    def update_user_profile(self, **kwargs):
        """Update user profile information"""
        if self.user_profile is None:
            self.user_profile = UserProfile(**kwargs)
        else:
            for key, value in kwargs.items():
                if hasattr(self.user_profile, key):
                    setattr(self.user_profile, key, value)

        self.save_context()
        logger.info("Updated user profile")

    def add_important_sender(self, email: str, name: Optional[str] = None):
        """Add an important sender to the patterns"""
        sender_info = f"{name} <{email}>" if name else email
        if sender_info not in self.email_patterns.important_senders:
            self.email_patterns.important_senders.append(sender_info)
            self.save_context()
            logger.info(f"Added important sender: {sender_info}")

    def add_custom_rule(self, rule_name: str, rule_definition: Dict):
        """Add a custom rule for email analysis"""
        self.custom_rules[rule_name] = rule_definition
        self.save_context()
        logger.info(f"Added custom rule: {rule_name}")

    def get_analysis_context(self, email_data: Dict) -> Dict:
        """
        Get comprehensive context for email analysis

        Args:
            email_data: Email data dictionary

        Returns:
            Context dictionary for AI analysis
        """
        context = {
            "user_profile": asdict(self.user_profile) if self.user_profile else {},
            "email_patterns": asdict(self.email_patterns),
            "custom_rules": self.custom_rules,
            "current_time": datetime.now().isoformat(),
            "analysis_timestamp": datetime.now().isoformat()
        }

        # Add email-specific context
        context["email_context"] = self._analyze_email_context(email_data)

        return context

    def _analyze_email_context(self, email_data: Dict) -> Dict:
        """Analyze email-specific context"""
        email_context = {
            "is_from_important_sender": False,
            "contains_urgent_keywords": False,
            "contains_meeting_keywords": False,
            "contains_approval_keywords": False,
            "contains_follow_up_keywords": False,
            "potential_spam": False,
            "sender_domain": "",
            "email_length": 0,
            "has_attachments": False
        }

        # Check sender importance
        sender_email = email_data.get('from_email', '').lower()
        sender_name = email_data.get('from_name', '').lower()

        for important_sender in self.email_patterns.important_senders:
            if sender_email in important_sender.lower() or sender_name in important_sender.lower():
                email_context["is_from_important_sender"] = True
                break

        # Extract domain
        if '@' in sender_email:
            email_context["sender_domain"] = sender_email.split('@')[1]

        # Check for keywords in subject and body
        subject = email_data.get('subject', '').lower()
        body = email_data.get('body_text', '').lower()
        combined_text = f"{subject} {body}"

        # Check urgent keywords
        for keyword in self.email_patterns.urgent_keywords:
            if keyword.lower() in combined_text:
                email_context["contains_urgent_keywords"] = True
                break

        # Check meeting keywords
        for keyword in self.email_patterns.meeting_keywords:
            if keyword.lower() in combined_text:
                email_context["contains_meeting_keywords"] = True
                break

        # Check approval keywords
        for keyword in self.email_patterns.approval_keywords:
            if keyword.lower() in combined_text:
                email_context["contains_approval_keywords"] = True
                break

        # Check follow-up keywords
        for keyword in self.email_patterns.follow_up_keywords:
            if keyword.lower() in combined_text:
                email_context["contains_follow_up_keywords"] = True
                break

        # Check spam indicators
        for indicator in self.email_patterns.spam_indicators:
            if indicator.lower() in combined_text:
                email_context["potential_spam"] = True
                break

        # Email characteristics
        email_context["email_length"] = len(combined_text)
        email_context["has_attachments"] = email_data.get('has_attachments', False)

        return email_context

    def get_priority_context(self) -> Dict:
        """Get context for priority determination"""
        return {
            "working_hours": self.user_profile.working_hours if self.user_profile else {},
            "timezone": self.user_profile.timezone if self.user_profile else "UTC",
            "current_projects": self.user_profile.current_projects if self.user_profile else [],
            "team_members": self.user_profile.team_members if self.user_profile else []
        }

    def is_working_hours(self, timestamp: datetime) -> bool:
        """Check if timestamp falls within working hours"""
        if not self.user_profile or not self.user_profile.working_hours:
            return True  # Assume always working if no profile

        try:
            current_time = timestamp.strftime("%H:%M")
            start_time = self.user_profile.working_hours["start"]
            end_time = self.user_profile.working_hours["end"]

            return start_time <= current_time <= end_time
        except Exception:
            return True

    def get_sender_relationship(self, sender_email: str) -> str:
        """Determine relationship with sender"""
        if not self.user_profile:
            return "unknown"

        # Check if it's the manager
        if self.user_profile.manager_email and sender_email.lower() == self.user_profile.manager_email.lower():
            return "manager"

        # Check if it's a team member
        for team_member in self.user_profile.team_members:
            if sender_email.lower() in team_member.lower():
                return "team_member"

        # Check if it's from the same domain (internal)
        user_domain = self.user_profile.name.split('@')[1] if '@' in self.user_profile.name else ""
        sender_domain = sender_email.split('@')[1] if '@' in sender_email else ""

        if user_domain and sender_domain and user_domain == sender_domain:
            return "internal"

        return "external"

    def export_context(self, file_path: str):
        """Export context to a file"""
        try:
            config = {
                'user_profile': asdict(self.user_profile) if self.user_profile else None,
                'email_patterns': asdict(self.email_patterns),
                'custom_rules': self.custom_rules,
                'exported_at': datetime.now().isoformat()
            }

            with open(file_path, 'w') as f:
                json.dump(config, f, indent=2)

            logger.info(f"Exported context to {file_path}")

        except Exception as e:
            logger.error(f"Error exporting context: {str(e)}")

    def import_context(self, file_path: str):
        """Import context from a file"""
        try:
            with open(file_path, 'r') as f:
                config = json.load(f)

            # Update current context
            if 'user_profile' in config and config['user_profile']:
                self.user_profile = UserProfile(**config['user_profile'])

            if 'email_patterns' in config:
                self.email_patterns = EmailPatterns(**config['email_patterns'])

            if 'custom_rules' in config:
                self.custom_rules = config['custom_rules']

            self.save_context()
            logger.info(f"Imported context from {file_path}")

        except Exception as e:
            logger.error(f"Error importing context: {str(e)}")


# --- Email processor ---

@dataclass
class ProcessingResult:
    """Result of email processing"""
    emails_processed: int
    recommendations: List[ActionRecommendation]
    summary: Dict
    processing_time: float
    errors: List[str]

class EmailProcessor:
    """Main processor that orchestrates email analysis workflow"""
    
    def __init__(self, openai_api_key: Optional[str] = None, context_file: Optional[str] = None, headless: bool = False):
        """
        Initialize the email processor
        
        Args:
            openai_api_key: OpenAI API key
            context_file: Path to context configuration file
            headless: If True, use headless auth (Secret Manager) instead of interactive device code flow
        """
        self.client = GraphEmailClient()
        self.analyzer = EmailAnalyzer(openai_api_key)
        self.context_manager = ContextManager(context_file)
        self._authenticate = self.client.authenticate_headless if headless else self.client.authenticate_interactive
        
        logger.info(f"EmailProcessor initialized (headless={headless})")

    @staticmethod
    def _move_emails_to_action_folders() -> bool:
        """When true, each analyzed message is moved to a folder named for its ActionType (e.g. reply_required)."""
        return os.getenv("EMAIL_ANALYSIS_MOVE_TO_ACTION_FOLDERS", "true").lower() in ("1", "true", "yes")
    
    def process_latest_emails(self, count: int = 10, folder: str = 'inbox') -> ProcessingResult:
        """
        Process the latest emails and get action recommendations
        
        Args:
            count: Number of emails to process
            folder: Email folder to process
            
        Returns:
            ProcessingResult object
        """
        start_time = datetime.now()
        errors = []
        
        try:
            # Authenticate
            if not self._authenticate():
                errors.append("Authentication failed")
                return ProcessingResult(0, [], {}, 0, errors)
            
            # Get emails
            emails = self.client.get_latest_emails(count, folder)
            if not emails:
                logger.info("No emails found to process")
                return ProcessingResult(0, [], {}, 0, errors)
            
            logger.info(f"Retrieved {len(emails)} emails for processing")
            
            # Process emails
            recommendations = self._process_emails(emails)
            
            # Generate summary
            summary = self.analyzer.get_action_summary(recommendations)
            
            processing_time = (datetime.now() - start_time).total_seconds()
            
            logger.info(f"Processed {len(emails)} emails in {processing_time:.2f} seconds")
            
            return ProcessingResult(
                emails_processed=len(emails),
                recommendations=recommendations,
                summary=summary,
                processing_time=processing_time,
                errors=errors
            )
            
        except Exception as e:
            error_msg = f"Error processing emails: {str(e)}"
            logger.error(error_msg)
            errors.append(error_msg)
            
            processing_time = (datetime.now() - start_time).total_seconds()
            return ProcessingResult(0, [], {}, processing_time, errors)
    
    def process_all_emails(self, folder: str = 'inbox') -> ProcessingResult:
        """
        Process all emails in a folder using paginated retrieval.
        
        Args:
            folder: Email folder to process
            
        Returns:
            ProcessingResult object
        """
        start_time = datetime.now()
        errors = []
        
        try:
            if not self._authenticate():
                errors.append("Authentication failed")
                return ProcessingResult(0, [], {}, 0, errors)
            
            emails = self.client.get_all_emails(folder)
            if not emails:
                logger.info(f"No emails found in {folder}")
                return ProcessingResult(0, [], {}, 0, errors)
            
            logger.info(f"Retrieved {len(emails)} emails from {folder}")
            
            recommendations = self._process_emails(emails)
            summary = self.analyzer.get_action_summary(recommendations)
            processing_time = (datetime.now() - start_time).total_seconds()
            
            logger.info(f"Processed {len(emails)} emails in {processing_time:.2f} seconds")
            
            return ProcessingResult(
                emails_processed=len(emails),
                recommendations=recommendations,
                summary=summary,
                processing_time=processing_time,
                errors=errors
            )
            
        except Exception as e:
            error_msg = f"Error processing all emails: {str(e)}"
            logger.error(error_msg)
            errors.append(error_msg)
            
            processing_time = (datetime.now() - start_time).total_seconds()
            return ProcessingResult(0, [], {}, processing_time, errors)

    def process_emails_since(self, since_datetime: datetime, folder: str = 'inbox') -> ProcessingResult:
        """
        Process emails received since a specific datetime
        
        Args:
            since_datetime: Process emails received after this time
            folder: Email folder to process
            
        Returns:
            ProcessingResult object
        """
        start_time = datetime.now()
        errors = []
        
        try:
            if not self._authenticate():
                errors.append("Authentication failed")
                return ProcessingResult(0, [], {}, 0, errors)
            
            emails = self.client.get_emails_since(since_datetime, folder)
            if not emails:
                logger.info(f"No emails found since {since_datetime}")
                return ProcessingResult(0, [], {}, 0, errors)
            
            logger.info(f"Retrieved {len(emails)} emails since {since_datetime}")
            
            # Process emails
            recommendations = self._process_emails(emails)
            
            # Generate summary
            summary = self.analyzer.get_action_summary(recommendations)
            
            processing_time = (datetime.now() - start_time).total_seconds()
            
            logger.info(f"Processed {len(emails)} emails in {processing_time:.2f} seconds")
            
            return ProcessingResult(
                emails_processed=len(emails),
                recommendations=recommendations,
                summary=summary,
                processing_time=processing_time,
                errors=errors
            )
            
        except Exception as e:
            error_msg = f"Error processing emails: {str(e)}"
            logger.error(error_msg)
            errors.append(error_msg)
            
            processing_time = (datetime.now() - start_time).total_seconds()
            return ProcessingResult(0, [], {}, processing_time, errors)
    
    def process_unread_emails(self, folder: str = 'inbox') -> ProcessingResult:
        """
        Process all unread emails
        
        Args:
            folder: Email folder to process
            
        Returns:
            ProcessingResult object
        """
        start_time = datetime.now()
        errors = []
        
        try:
            if not self._authenticate():
                errors.append("Authentication failed")
                return ProcessingResult(0, [], {}, 0, errors)
            
            emails = self.client.get_all_emails(folder)
            unread_emails = [email for email in emails if not email.is_read]
            
            if not unread_emails:
                logger.info("No unread emails found")
                return ProcessingResult(0, [], {}, 0, errors)
            
            logger.info(f"Found {len(unread_emails)} unread emails")
            
            # Process emails
            recommendations = self._process_emails(unread_emails)
            
            # Generate summary
            summary = self.analyzer.get_action_summary(recommendations)
            
            processing_time = (datetime.now() - start_time).total_seconds()
            
            logger.info(f"Processed {len(unread_emails)} unread emails in {processing_time:.2f} seconds")
            
            return ProcessingResult(
                emails_processed=len(unread_emails),
                recommendations=recommendations,
                summary=summary,
                processing_time=processing_time,
                errors=errors
            )
            
        except Exception as e:
            error_msg = f"Error processing unread emails: {str(e)}"
            logger.error(error_msg)
            errors.append(error_msg)
            
            processing_time = (datetime.now() - start_time).total_seconds()
            return ProcessingResult(0, [], {}, processing_time, errors)
    
    def _process_emails(self, emails: List[Email]) -> List[ActionRecommendation]:
        """Process a list of emails and return recommendations"""
        recommendations = []
        
        for email in emails:
            try:
                # Prepare email data
                email_data = self._prepare_email_data(email)
                
                # Get context for this email
                context = self.context_manager.get_analysis_context(email_data)
                
                # Analyze email
                recommendation = self.analyzer.analyze_email(email, context)
                
                # Add email reference to recommendation
                recommendation.email_id = email.id
                recommendation.email_subject = email.subject
                
                recommendations.append(recommendation)

                if self._move_emails_to_action_folders():
                    folder_name = recommendation.action_type.value
                    if not self.client.move_message_to_action_folder(email.id, folder_name):
                        logger.warning(
                            "Could not move message id=%s to folder %s",
                            email.id,
                            folder_name,
                        )

            except Exception as e:
                logger.error(f"Error processing email {email.id}: {str(e)}")
                # Add error recommendation
                error_rec = ActionRecommendation(
                    action_type=ActionType.NO_ACTION,
                    priority=1,
                    confidence=0.0,
                    reasoning=f"Processing error: {str(e)}"
                )
                error_rec.email_id = email.id
                error_rec.email_subject = email.subject
                recommendations.append(error_rec)
                if self._move_emails_to_action_folders():
                    if not self.client.move_message_to_action_folder(
                        email.id, ActionType.NO_ACTION.value
                    ):
                        logger.warning(
                            "Could not move message id=%s to folder %s after processing error",
                            email.id,
                            ActionType.NO_ACTION.value,
                        )
        
        return recommendations
    
    def _prepare_email_data(self, email: Email) -> Dict:
        """Prepare email data for analysis"""
        return {
            "id": email.id,
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
    
    def get_high_priority_actions(self, recommendations: List[ActionRecommendation]) -> List[ActionRecommendation]:
        """Filter recommendations to only high priority actions"""
        return [rec for rec in recommendations if rec.priority >= 4]
    
    def get_urgent_actions(self, recommendations: List[ActionRecommendation]) -> List[ActionRecommendation]:
        """Filter recommendations to only urgent actions"""
        return [rec for rec in recommendations if rec.action_type == ActionType.URGENT_ATTENTION]
    
    def get_actions_needing_response(self, recommendations: List[ActionRecommendation]) -> List[ActionRecommendation]:
        """Filter recommendations that require a response"""
        response_actions = [
            ActionType.REPLY_REQUIRED,
            ActionType.APPROVE_REQUEST,
            ActionType.SCHEDULE_MEETING
        ]
        return [rec for rec in recommendations if rec.action_type in response_actions]
    
    def print_summary(self, result: ProcessingResult):
        """Print a formatted summary of processing results"""
        print("\n" + "="*60)
        print("EMAIL PROCESSING SUMMARY")
        print("="*60)
        
        print(f"Emails Processed: {result.emails_processed}")
        print(f"Processing Time: {result.processing_time:.2f} seconds")
        
        if result.errors:
            print(f"Errors: {len(result.errors)}")
            for error in result.errors:
                print(f"  - {error}")
        
        print(f"\nAction Summary:")
        summary = result.summary
        if summary:
            print(f"  Total Recommendations: {summary.get('total', 0)}")
            print(f"  High Priority (4-5): {summary.get('high_priority_count', 0)}")
            print(f"  Urgent Actions: {summary.get('urgent_count', 0)}")
            
            print(f"\nBy Action Type:")
            for action_type, count in summary.get('by_action_type', {}).items():
                print(f"  {action_type}: {count}")
            
            print(f"\nBy Priority:")
            for priority, count in summary.get('by_priority', {}).items():
                print(f"  Priority {priority}: {count}")
        
        print("\n" + "="*60)
    
    def print_recommendations(self, recommendations: List[ActionRecommendation], limit: Optional[int] = None):
        """Print formatted recommendations"""
        if not recommendations:
            print("No recommendations to display.")
            return
        
        print(f"\n{'='*80}")
        print("ACTION RECOMMENDATIONS")
        print(f"{'='*80}")
        
        # Sort by priority (highest first)
        sorted_recs = sorted(recommendations, key=lambda x: x.priority, reverse=True)
        
        if limit:
            sorted_recs = sorted_recs[:limit]
        
        for i, rec in enumerate(sorted_recs, 1):
            print(f"\n{i}. {rec.email_subject}")
            print(f"   Action: {rec.action_type.value.upper()}")
            print(f"   Priority: {rec.priority}/5")
            print(f"   Confidence: {rec.confidence:.2f}")
            print(f"   Reasoning: {rec.reasoning}")
            
            if rec.suggested_response:
                print(f"   Suggested Response: {rec.suggested_response}")
            
            if rec.deadline:
                print(f"   Deadline: {rec.deadline}")
            
            if rec.tags:
                print(f"   Tags: {', '.join(rec.tags)}")
            
            print(f"   {'-'*60}")
    
    def setup_user_context(self, name: str, role: str, department: str, **kwargs):
        """Setup user context for better analysis"""
        self.context_manager.update_user_profile(
            name=name,
            role=role,
            department=department,
            **kwargs
        )
        logger.info(f"Updated user context: {name} - {role} in {department}")
    
    def add_important_contact(self, email: str, name: Optional[str] = None):
        """Add an important contact for priority analysis"""
        self.context_manager.add_important_sender(email, name)
        logger.info(f"Added important contact: {name or email}")
    
    def export_results(self, result: ProcessingResult, file_path: str):
        """Export processing results to a file"""
        try:
            import json
            
            export_data = {
                "processing_timestamp": datetime.now().isoformat(),
                "emails_processed": result.emails_processed,
                "processing_time": result.processing_time,
                "errors": result.errors,
                "summary": result.summary,
                "recommendations": []
            }
            
            # Convert recommendations to serializable format
            for rec in result.recommendations:
                rec_data = {
                    "email_id": getattr(rec, 'email_id', None),
                    "email_subject": getattr(rec, 'email_subject', None),
                    "action_type": rec.action_type.value,
                    "priority": rec.priority,
                    "confidence": rec.confidence,
                    "reasoning": rec.reasoning,
                    "suggested_response": rec.suggested_response,
                    "deadline": rec.deadline.isoformat() if rec.deadline else None,
                    "tags": rec.tags
                }
                export_data["recommendations"].append(rec_data)
            
            with open(file_path, 'w') as f:
                json.dump(export_data, f, indent=2)
            
            logger.info(f"Exported results to {file_path}")
            
        except Exception as e:
            logger.error(f"Error exporting results: {str(e)}")
