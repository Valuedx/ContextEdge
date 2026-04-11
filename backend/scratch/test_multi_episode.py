import asyncio
import json
from contextedge.ai.extractors.episode_extractor import reconstruct_episode
from contextedge.ai.provider import llm_complete_json

async def test_splitting():
    # Mock mixed evidence
    evidence = [
        {
            "source_type": "slack",
            "timestamp": "2026-04-10T10:00:00Z",
            "title": "VPN issue",
            "body": "User A: I cannot connect to the VPN. Getting 'Auth Timeout'."
        },
        {
            "source_type": "logs",
            "timestamp": "2026-04-10T10:05:00Z",
            "title": "VPN Auth Log",
            "body": "Error: LDAP timeout for user A."
        },
        {
            "source_type": "monitoring",
            "timestamp": "2026-04-10T10:10:00Z",
            "title": "Billing Service Alert",
            "body": "CRITICAL: Billing service is restarting frequently. OutOfMemoryError detected."
        },
        {
            "source_type": "git",
            "timestamp": "2026-04-10T09:50:00Z",
            "title": "Recent Deploy",
            "body": "Deployed Billing service v2.4.1. Changes in memory allocation settings."
        }
    ]

    print("--- Running Reconstruction on Mixed Evidence ---")
    episodes = await reconstruct_episode(evidence)
    
    print(f"\nFound {len(episodes)} episodes.")
    for i, ep in enumerate(episodes):
        print(f"\nEpisode {i+1}: {ep['title']}")
        print(f"Root Cause: {ep.get('root_cause_summary')}")
        print(f"Steps: {len(ep.get('steps', []))}")
        for s in ep.get('steps', []):
            print(f"  - [{s['step_type']}] {s['text']}")

if __name__ == "__main__":
    asyncio.run(test_splitting())
