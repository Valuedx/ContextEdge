import asyncio
from contextedge.config import settings
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def push():
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as conn:
        # Update unclassified logs to relevant
        await conn.execute(
            text("UPDATE evidence_items SET relevance_state = 'relevant' WHERE relevance_state = 'unclassified'")
        )
        await conn.commit()
        print("Successfully updated unclassified items to 'relevant'.")

if __name__ == "__main__":
    asyncio.run(push())
