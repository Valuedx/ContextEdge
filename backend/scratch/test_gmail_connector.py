import asyncio
import sys
import os

# Ensure we can import from src
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

from contextedge.connectors.gmail.connector import GmailConnector

async def test_validation():
    # This is a dummy test config. 
    # Real test requires a real service account JSON.
    # But we can at least check if imports and instantiation work.
    config = {"mailbox_email": "test@gmail.com"}
    creds = {"service_account_json": {}}
    
    try:
        connector = GmailConnector(config, creds)
        print("Connector instantiated successfully.")
        
        # This will likely fail with a real error if libraries are missing
        # or it will return valid=False because creds are empty.
        result = await connector.validate_credentials()
        print(f"Validation Result: {result.valid}, Message: {result.message}")
    except Exception as e:
        print(f"CRASH: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(test_validation())
