"""
Email class for structured email representation
"""

from typing import List, Dict
from datetime import datetime
import re


class Email:
    """Structured representation of an email"""
    
    def __init__(self, data: Dict):
        self.id = data.get('id')
        self.subject = data.get('subject', 'No Subject')
        self.from_address = data.get('from', {}).get('emailAddress', {})
        self.from_name = self.from_address.get('name', 'Unknown')
        self.from_email = self.from_address.get('address', '')
        self.to_recipients = [r.get('emailAddress', {}) for r in data.get('toRecipients', [])]
        self.cc_recipients = [r.get('emailAddress', {}) for r in data.get('ccRecipients', [])]
        self.bcc_recipients = [r.get('emailAddress', {}) for r in data.get('bccRecipients', [])]
        self.received_datetime = data.get('receivedDateTime')
        self.sent_datetime = data.get('sentDateTime')
        self.body_preview = data.get('bodyPreview', '')
        self.body_content = data.get('body', {}).get('content', '')
        self.body_type = data.get('body', {}).get('contentType', 'text')
        self.is_read = data.get('isRead', False)
        self.has_attachments = data.get('hasAttachments', False)
        self.attachments = data.get('attachments', [])
        
        # Parse datetime strings
        if self.received_datetime:
            try:
                self.received_datetime = datetime.fromisoformat(self.received_datetime.replace('Z', '+00:00'))
            except:
                pass
        if self.sent_datetime:
            try:
                self.sent_datetime = datetime.fromisoformat(self.sent_datetime.replace('Z', '+00:00'))
            except:
                pass
    
    def __str__(self):
        return f"Email(id='{self.id}', subject='{self.subject}', from='{self.from_name} <{self.from_email}>')"
    
    def __repr__(self):
        return self.__str__()
    
    @property
    def from_display(self) -> str:
        """Get formatted from address"""
        if self.from_name and self.from_email:
            return f"{self.from_name} <{self.from_email}>"
        elif self.from_email:
            return self.from_email
        else:
            return self.from_name or "Unknown"
    
    @property
    def to_display(self) -> str:
        """Get formatted to recipients"""
        if not self.to_recipients:
            return "No recipients"
        return ", ".join([f"{r.get('name', '')} <{r.get('address', '')}>" for r in self.to_recipients])
    
    @property
    def received_date(self) -> str:
        """Get formatted received date"""
        if isinstance(self.received_datetime, datetime):
            return self.received_datetime.strftime("%Y-%m-%d %H:%M:%S")
        return str(self.received_datetime) if self.received_datetime else "Unknown"
    
    @property
    def sent_date(self) -> str:
        """Get formatted sent date"""
        if isinstance(self.sent_datetime, datetime):
            return self.sent_datetime.strftime("%Y-%m-%d %H:%M:%S")
        return str(self.sent_datetime) if self.sent_datetime else "Unknown"
    
    def get_body_text(self) -> str:
        """Get email body as plain text"""
        if self.body_type == 'html':
            # Simple HTML tag removal (you might want to use a proper HTML parser)
            return re.sub(r'<[^>]+>', '', self.body_content)
        return self.body_content
    
    def get_attachment_names(self) -> List[str]:
        """Get list of attachment names"""
        return [att.get('name', 'Unknown') for att in self.attachments]
