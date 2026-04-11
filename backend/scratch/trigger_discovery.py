import asyncio
import uuid
from contextedge.deps import get_db
from contextedge.api.v1.patterns import discover_pattern, PatternDiscoverRequest
from contextedge.models.tenant import User

async def trigger_discovery():
    async for db in get_db():
        # Using the episode ID from previous verification
        episode_id = uuid.UUID("32be3fbe-7056-4338-b16a-c0fab6c4f4ca")
        
        # Create a mock user for the auth context
        # In a real scenario, we'd fetch a real user, but here we can mock if needed
        # Or just fetch the first user
        from sqlalchemy import select
        res = await db.execute(select(User).limit(1))
        user = res.scalar_one_or_none()
        
        if not user:
            print("No user found.")
            return

        print(f"Triggering pattern discovery for episode: {episode_id}")
        
        request_body = PatternDiscoverRequest(episode_ids=[episode_id])
        
        # Mocking AuthUser for the API call
        class MockAuthUser:
            def __init__(self, u):
                self.id = u.id
                self.tenant_id = u.tenant_id
                
        auth_user = MockAuthUser(user)
        
        try:
            pattern_resp = await discover_pattern(request_body, db, auth_user)
            print(f"Discovery Successful! New Pattern: {pattern_resp.title}")
            print(f"ID: {pattern_resp.id}")
            print(f"Root Causes: {pattern_resp.root_causes}")
            print(f"Resolution Steps: {pattern_resp.resolution_steps}")
        except Exception as e:
            print(f"Discovery Failed: {str(e)}")
            import traceback
            traceback.print_exc()
            
        break

if __name__ == "__main__":
    asyncio.run(trigger_discovery())
