#!/usr/bin/env python3
"""
Email Analysis Script
Retrieves emails and determines actions needed using AI.
Works both locally (interactive device code auth) and in Cloud Run (headless via Secret Manager).
"""

import os
import sys
from datetime import datetime, timedelta
from services.email_processor import EmailProcessor


def is_headless():
    return bool(os.getenv("GCP_PROJECT_ID"))


def main():
    headless = is_headless()
    print("AI-Powered Email Analysis System")
    print("=" * 50)
    print(f"Mode: {'Cloud Run (headless)' if headless else 'Local (interactive)'}")

    if not os.getenv("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY environment variable not set")
        sys.exit(1) if headless else sys.exit(0)

    try:
        print("\n1. Initializing Email Processor...")
        processor = EmailProcessor(headless=headless)

        print("\n2. Setting up user context...")
        processor.setup_user_context(
            name="John Doe",
            role="Software Engineer",
            department="Engineering",
            manager_email="manager@company.com",
            team_members=["colleague1@company.com", "colleague2@company.com"],
            current_projects=["Project Alpha", "Project Beta"],
            working_hours={"start": "09:00", "end": "17:00"},
            timezone="America/New_York",
        )
        processor.add_important_contact("ceo@company.com", "CEO")
        processor.add_important_contact("manager@company.com", "Manager")
        print("User context configured")

        if headless:
            result = _run_headless(processor)
        else:
            result = _run_interactive(processor)

        if result is None or result.emails_processed == 0:
            print("No emails found to process")
            return

        processor.print_summary(result)

        print("\nAll Recommendations:")
        processor.print_recommendations(result.recommendations)

        high_priority = processor.get_high_priority_actions(result.recommendations)
        if high_priority:
            print(f"\nHigh Priority Actions ({len(high_priority)}):")
            processor.print_recommendations(high_priority)

        urgent_actions = processor.get_urgent_actions(result.recommendations)
        if urgent_actions:
            print(f"\nUrgent Actions ({len(urgent_actions)}):")
            processor.print_recommendations(urgent_actions)

        response_actions = processor.get_actions_needing_response(result.recommendations)
        if response_actions:
            print(f"\nActions Needing Response ({len(response_actions)}):")
            processor.print_recommendations(response_actions)

        print("\nAction Statistics:")
        print_action_statistics(result.recommendations)

        print("\nEmail analysis complete!")

    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
        if headless:
            sys.exit(1)


def _run_headless(processor):
    """Cloud Run path: process all emails in inbox."""
    print("\n3. Processing all emails (headless)...")
    return processor.process_all_emails()


def _run_interactive(processor):
    """Local path: process latest N, unread, and recent emails."""
    print("\n3. Processing latest emails...")
    result = processor.process_latest_emails(count=10)

    if result.emails_processed == 0:
        return result

    processor.print_summary(result)
    processor.print_recommendations(result.recommendations)

    print("\n4. Processing unread emails...")
    unread_result = processor.process_unread_emails()
    if unread_result.emails_processed > 0:
        print(f"Found {unread_result.emails_processed} unread emails")
        processor.print_summary(unread_result)

        unread_high = processor.get_high_priority_actions(unread_result.recommendations)
        if unread_high:
            print("High Priority Unread Actions:")
            processor.print_recommendations(unread_high)
    else:
        print("No unread emails found")

    print("\n5. Processing emails from last 24 hours...")
    yesterday = datetime.now() - timedelta(days=1)
    recent_result = processor.process_emails_since(yesterday)
    if recent_result.emails_processed > 0:
        print(f"Found {recent_result.emails_processed} emails from last 24 hours")
        processor.print_summary(recent_result)
    else:
        print("No emails found from last 24 hours")

    return result


def print_action_statistics(recommendations):
    if not recommendations:
        print("No recommendations to analyze")
        return

    action_counts = {}
    priority_counts = {}
    confidence_sum = 0

    for rec in recommendations:
        action_type = rec.action_type.value
        action_counts[action_type] = action_counts.get(action_type, 0) + 1
        priority_counts[rec.priority] = priority_counts.get(rec.priority, 0) + 1
        confidence_sum += rec.confidence

    avg_confidence = confidence_sum / len(recommendations)

    print(f"Total Recommendations: {len(recommendations)}")
    print(f"Average Confidence: {avg_confidence:.2f}")

    print("\nBy Action Type:")
    for action_type, count in sorted(action_counts.items()):
        percentage = (count / len(recommendations)) * 100
        print(f"  {action_type}: {count} ({percentage:.1f}%)")

    print("\nBy Priority:")
    for priority in sorted(priority_counts.keys()):
        count = priority_counts[priority]
        percentage = (count / len(recommendations)) * 100
        print(f"  Priority {priority}: {count} ({percentage:.1f}%)")

    high_conf = len([r for r in recommendations if r.confidence >= 0.8])
    med_conf = len([r for r in recommendations if 0.5 <= r.confidence < 0.8])
    low_conf = len([r for r in recommendations if r.confidence < 0.5])

    print("\nBy Confidence:")
    print(f"  High (>=0.8): {high_conf}")
    print(f"  Medium (0.5-0.8): {med_conf}")
    print(f"  Low (<0.5): {low_conf}")


if __name__ == "__main__":
    main()
