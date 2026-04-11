import asyncio
import os
from dotenv import load_dotenv
import litellm

# Load env
load_dotenv(".env")

async def test_embedding():
    model = "vertex_ai/text-embedding-004"
    text = "Hello world"
    
    print(f"Testing model: {model}")
    
    # Test 1: No explicit dimension
    try:
        res1 = await litellm.aembedding(model=model, input=[text])
        emb1 = res1.data[0]["embedding"]
        print(f"Default dimension: {len(emb1)}")
    except Exception as e:
        print(f"Error 1: {e}")

    # Test 2: Using output_dimensionality (what's currently in code)
    try:
        res2 = await litellm.aembedding(model=model, input=[text], output_dimensionality=3072)
        emb2 = res2.data[0]["embedding"]
        print(f"output_dimensionality=3072 dimension: {len(emb2)}")
    except Exception as e:
        print(f"Error 2: {e}")

    # Test 3: Using dimensions (unified param)
    try:
        res3 = await litellm.aembedding(model=model, input=[text], dimensions=3072)
        emb3 = res3.data[0]["embedding"]
        print(f"dimensions=3072 dimension: {len(emb3)}")
    except Exception as e:
        print(f"Error 3: {e}")

if __name__ == "__main__":
    # Ensure credentials are set
    if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        print(f"Using credentials from: {os.getenv('GOOGLE_APPLICATION_CREDENTIALS')}")
    
    asyncio.run(test_embedding())
