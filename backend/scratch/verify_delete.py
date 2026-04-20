import asyncio
import sys
import os
import uuid

# Ensure we can import from src
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

from sqlalchemy import select, delete
from contextedge.database import create_db_engine, async_sessionmaker
from contextedge.models.pattern import Pattern, PatternEvidenceLink

async def main():
    engine = create_db_engine(use_null_pool=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    tid = uuid.UUID("00000000-0000-0000-0000-000000000001")
    
    async with factory() as db:
        # 1. Create a dummy pattern
        p = Pattern(
            tenant_id=tid,
            title="TEST DELETE PATTERN",
            pattern_type="recurring_issue",
            confidence=0.5
        )
        db.add(p)
        await db.flush()
        pid = p.id
        print(f"Created pattern {pid}")
        
        # 2. Create a link
        link = PatternEvidenceLink(
            pattern_id=pid,
            link_type="member",
            weight=1.0
        )
        db.add(link)
        await db.flush()
        lid = link.id
        print(f"Created link {lid}")
        await db.commit()
        
        # 3. Verify they exist
        res_p = await db.execute(select(Pattern).where(Pattern.id == pid))
        if res_p.scalar():
            print("Verified: Pattern exists.")
        
        # 4. Delete via code logic (mimicking API)
        print(f"Deleting pattern {pid} and its links...")
        await db.execute(delete(PatternEvidenceLink).where(PatternEvidenceLink.pattern_id == pid))
        await db.execute(delete(Pattern).where(Pattern.id == pid))
        await db.commit()
        
        # 5. Check if gone
        res_p2 = await db.execute(select(Pattern).where(Pattern.id == pid))
        if not res_p2.scalar():
            print("Verified: Pattern deleted successfully.")
            
        res_l = await db.execute(select(PatternEvidenceLink).where(PatternEvidenceLink.pattern_id == pid))
        if not res_l.scalar():
            print("Verified: Links deleted successfully.")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
