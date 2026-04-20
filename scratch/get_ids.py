import asyncio
import os
import json
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from dotenv import load_dotenv

load_dotenv()

async def get_ids():
    url = os.getenv("DATABASE_URL")
    if not url:
        print("DATABASE_URL not found")
        return
    
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        # Get first tenant
        tenant_res = await conn.execute(text("SELECT id, name FROM tenants LIMIT 1"))
        tenant = tenant_res.fetchone()
        if not tenant:
            print("No tenants found")
            return
        
        tenant_id = str(tenant[0])
        print(f"TENANT_ID: {tenant_id} ({tenant[1]})")
        
        # Get first user for that tenant
        user_res = await conn.execute(text(f"SELECT id, email FROM users WHERE tenant_id = '{tenant_id}' LIMIT 1"))
        user = user_res.fetchone()
        if not user:
            print(f"No users found for tenant {tenant_id}")
            # Try any user
            user_res = await conn.execute(text("SELECT id, email, tenant_id FROM users LIMIT 1"))
            user = user_res.fetchone()
            if not user:
                print("No users found at all")
                return
            user_id = str(user[0])
            tenant_id = str(user[2])
            print(f"Fallback USER_ID: {user_id} ({user[1]}) for TENANT_ID: {tenant_id}")
        else:
            user_id = str(user[0])
            print(f"USER_ID: {user_id} ({user[1]})")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(get_ids())
